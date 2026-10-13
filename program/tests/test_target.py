import copy
import hashlib
import importlib.util
import json
import math
import struct
import sys
import shutil
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import run
import music_target.engine as engine_module
import music_target.wav_io as wav_io_module
from music_target.contract import source_revision, validate
from music_target.engine import (
    InputError,
    RelationshipIntegrityPass,
    strict_pcm_check,
)
from libevchain.pipeline import EvidencePass
from music_target.assessment_passes import (
    C2PAAttestationPass,
    ExpandedCrossEvidencePass,
    _summarize_physical_similarity,
)
from music_target.audio_derivation import (
    MAX_SOURCE_CORRELATION,
    ResourceBudgetExceeded,
    _analysis_proxy,
    _fir_lowpass,
    _mono_resample,
    _proxy_decimation_factor,
    _read_bound_pcm,
    _read_pcm,
    _resample_series,
)
from legacy_parsers.common.ddex_validation import REGISTRY, _schema
from legacy_parsers.ern_parser import parse_ern_file
from legacy_parsers.rin_parser import parse_rin_file
from music_target.workflow_policy import WorkflowCompletenessPass, load_workflow_policy
from music_target.midi_parser import parse_midi_file
from music_target.sheet_parsers import parse_comp_sheet_file, parse_cue_sheet_file
from music_target.wav_io import NonFiniteSamples, inspect_wav, read_frames
from make_examples import wav_bytes


class TargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = BASE / "examples/valid-generated"
        cls.template = json.loads((cls.folder / "request.json").read_text())

    def score(self, request=None, folder=None):
        response = run.evaluate(copy.deepcopy(request or self.template), folder or self.folder, "native")
        validate(response, "scoring-response.schema.json")
        return response

    def test_valid_graph_uses_library_and_exposes_four_observations(self):
        result = self.score()
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertEqual(sum(f["code"] == "WAV_OBSERVATION" for f in result["findings"]), 4)
        relationship_findings = [
            item for item in result["findings"]
            if item["code"] == "RELATIONSHIP_INTEGRITY_OBSERVATION"]
        self.assertEqual(len(relationship_findings), 3)
        self.assertTrue(issubclass(RelationshipIntegrityPass, EvidencePass))
        self.assertTrue(all(item["pass"] == "RelationshipIntegrityPass"
                            for item in relationship_findings))
        self.assertEqual(result["axes"]["completeness"]["value"], 1.0)
        self.assertEqual(result["axes"]["completeness"]["availability"], "available")
        self.assertNotIn("decision", result)
        self.assertNotIn("overall_score", result)
        self.assertFalse(any(item["code"] == "POLICY_NOT_CONFIGURED" for item in result["findings"]))
        self.assertEqual(result["axes"]["integrity"]["availability"], "partial")
        layers = {item["layer"]: item for item in
                  result["assessment_checks"]["integrity_layers"]}
        self.assertFalse(layers["structural_validity"]["contributes_to_integrity"])
        self.assertFalse(layers["cryptographic_attestation"]["contributes_to_integrity"])
        self.assertEqual(result["axes"]["integrity"]["value"], 1.0)
        self.assertEqual(result["axes"]["ai_disclosure"]["value"], 1.0)
        self.assertIn("workflow_assessment", result)
        self.assertIsNone(result["validation"]["cryptographically_valid"])
        self.assertIsNone(result["validation"]["credential_trusted"])
        self.assertTrue(all(item["status"] in {"satisfied", "missing", "not_assessed", "not_applicable"}
                            for item in result["workflow_assessment"]["rule_results"]))

    def test_patched_dictionary_attribute_type_checks_values(self):
        from libevchain.types import AttributeTypes
        checker = AttributeTypes.DictOf(AttributeTypes.Str)
        self.assertTrue(checker({"key": "value"}))
        self.assertFalse(checker({"key": 7}))

    def test_modified_bytes_are_rejected(self):
        result = self.score(folder=BASE / "examples/invalid-hash")
        self.assertEqual(result["execution_status"], "error")
        self.assertIn("SHA-256 mismatch", result["error"])

    def test_workflow_completeness_ignores_unrequired_missing_raw_object(self):
        request = copy.deepcopy(self.template)
        missing_hash = request["chain"]["artefacts"][0]["artefact_hash"]
        del request["objects"][missing_hash]
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded")
        self.assertEqual(result["axes"]["completeness"]["value"], 1.0)
        self.assertTrue(any(f["code"] == "MISSING_OBJECT" for f in result["findings"]))
        self.assertFalse(any(f["code"] == "FILE_INTEGRITY_CONTRADICTION"
                             and f.get("evidence_hash") == missing_hash
                             for f in result["findings"]))
        missing_check = next(
            item for item in result["assessment_checks"]["bound_file_integrity"]
            if item["rule"] == "object_hash_bound"
            and item["evidence_hash"] == missing_hash)
        self.assertIsNone(missing_check["passed"])
        self.assertEqual(missing_check["status"], "not_assessed")
        self.assertEqual(missing_check["reason_code"], "MISSING_OBJECT")
        self.assertNotIn("decision", result)

    def test_missing_nonfinal_file_is_not_an_integrity_contradiction(self):
        request = copy.deepcopy(self.template)
        missing_hash = request["chain"]["artefacts"][0]["artefact_hash"]
        request["objects"][missing_hash] = f"objects/{missing_hash}.bin"

        result = self.score(request)

        self.assertEqual(result["execution_status"], "succeeded")
        self.assertTrue(any(f["code"] == "MISSING_OBJECT"
                            and f.get("evidence_hash") == missing_hash
                            for f in result["findings"]))
        self.assertFalse(any(f["code"] == "FILE_INTEGRITY_CONTRADICTION"
                             and f.get("evidence_hash") == missing_hash
                             for f in result["findings"]))
        missing_check = next(
            item for item in result["assessment_checks"]["bound_file_integrity"]
            if item["rule"] == "object_hash_bound"
            and item["evidence_hash"] == missing_hash)
        self.assertIsNone(missing_check["passed"])
        self.assertEqual(missing_check["status"], "not_assessed")

    def _workflow_request(self, root, workflow_id, node_specs, final_index=-1, modifiers=None):
        (root / "objects").mkdir()
        nodes, objects = [], {}
        for index, spec in enumerate(node_specs):
            suffix, data = spec.get("suffix", "wav"), spec["data"]
            digest = hashlib.sha256(data).hexdigest()
            relative = f"objects/{digest}.{suffix}"
            (root / relative).write_bytes(data)
            attributes = copy.deepcopy(spec.get("attributes", {}))
            if spec["type"].startswith("audio/"):
                attributes.setdefault("creation_method", "synthesised")
            nodes.append({"artefact_hash": digest, "artefact_type": spec["type"],
                          "attributes": attributes, "evidence": []})
            objects[digest] = relative
        for index, spec in enumerate(node_specs):
            relationships = []
            for parent_spec in spec.get("parents", []):
                parent, relationship = parent_spec[:2]
                edge_attributes = parent_spec[2] if len(parent_spec) > 2 else {}
                relationships.append({"hash": nodes[parent]["artefact_hash"],
                                      "relationship_type": relationship,
                                      "attributes": edge_attributes})
            nodes[index]["evidence"] = relationships
        return {"run_id": "642498bc-b128-4b49-a275-6ce319b4d67e",
                "case_id": f"WORKFLOW-{workflow_id}", "profile": "music-native-v0.1",
                "workflow": {"workflow_id": workflow_id, "modifiers": modifiers or [],
                             "description": "End-to-end workflow fixture.",
                             "declarations": {"submitter_confirmation": True,
                                              "contributors": ["Fixture Contributor"]}},
                "options": {"include_reasoning": True, "offline_only": True, "timeout_seconds": 60},
                "chain": {"schema_version": "0.0.3", "hash_method": "sha256",
                          "final_artefact_hash": nodes[final_index]["artefact_hash"], "artefacts": nodes},
                "objects": objects}

    def test_mastering_service_workflow_end_to_end(self):
        result = self.score()
        self.assertEqual(result["workflow_assessment"]["workflow_id"], "mastering_service_only")
        self.assertEqual(result["axes"]["completeness"]["value"], 1.0)

    def test_all_sixteen_workflow_policies_are_loadable_and_evaluable(self):
        policy = load_workflow_policy(BASE / "policies/workflow_evidence_policy.json")
        self.assertEqual(len(policy["workflows"]), 16)
        evaluator = WorkflowCompletenessPass(policy)
        native = self.template["chain"]
        bound = set(self.template["objects"])
        for workflow in policy["workflows"]:
            axis, assessment, findings = evaluator.evaluate(
                native,
                {"workflow_id": workflow["workflow_id"], "modifiers": [],
                 "description": "Policy smoke test.",
                 "declarations": {"submitter_confirmation": True,
                                  "contributors": ["Fixture"]}},
                bound)
            self.assertIn(axis["availability"], {"available", "partial", "unavailable"})
            self.assertEqual(assessment["workflow_id"], workflow["workflow_id"])
            self.assertTrue(assessment["rule_results"])

    def test_unknown_workflow_is_rejected_instead_of_guessed(self):
        request = copy.deepcopy(self.template)
        request["workflow"]["workflow_id"] = "made_up_workflow"
        result = self.score(request)
        self.assertEqual(result["execution_status"], "error")
        self.assertIn("Unknown workflow_id", result["error"])

    def test_multitrack_workflow_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-track", "data": wav_bytes(901)},
                {"type": "audio/raw-track", "data": wav_bytes(902)},
                {"type": "audio/mix", "data": wav_bytes(700),
                 "parents": [(0, "mixed_from"), (1, "mixed_from")]},
                {"type": "audio/master", "data": wav_bytes(800),
                 "parents": [(2, "mastered_from")]},
            ]
            result = self.score(self._workflow_request(root, "multitrack_recording_mix_master", specs), root)
            self.assertEqual(result["execution_status"], "succeeded", result["error"])
            self.assertEqual(result["axes"]["completeness"]["value"], 1.0)

    def test_midi_virtual_instrument_workflow_end_to_end(self):
        midi = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96)
                + b"MTrk" + struct.pack(">I", 4) + b"\x00\xff\x2f\x00")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "project/midi", "data": midi, "suffix": "mid"},
                {"type": "audio/mix", "data": wav_bytes(610),
                 "parents": [(0, "rendered_from")]},
            ]
            result = self.score(self._workflow_request(root, "midi_virtual_instrument_production", specs), root)
            self.assertEqual(result["execution_status"], "succeeded", result["error"])
            self.assertEqual(result["axes"]["completeness"]["value"], 1.0)
            midi_finding = next(item for item in result["findings"]
                                if item["code"] == "PARSER_OBSERVATION" and item.get("parser") == "midi")
            self.assertEqual(midi_finding["parser_status"], "parsed")

    def test_midi_parser_extracts_channel_tempo_and_time_signature_events(self):
        events = (b"\x00\xff\x03\x05Piano"
                  b"\x00\xff\x51\x03\x07\xa1\x20"
                  b"\x00\xff\x58\x04\x04\x02\x18\x08"
                  b"\x00\x90\x3c\x64"
                  b"\x60\x80\x3c\x00"
                  b"\x00\xff\x2f\x00")
        midi = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + b"MTrk" + struct.pack(">I", len(events)) + events
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.mid"
            path.write_bytes(midi)
            parsed = parse_midi_file(path)
        self.assertEqual(parsed["note_on_count"], 1)
        self.assertEqual(parsed["duration_ticks"], 96)
        self.assertEqual(parsed["tempo_events"][0]["bpm"], 120.0)
        self.assertEqual(parsed["time_signature_events"][0]["denominator"], 4)
        self.assertEqual(parsed["duration_seconds"], 0.5)

    @staticmethod
    def _pcm16(samples, rate=8000):
        with tempfile.NamedTemporaryFile(suffix=".wav") as temp:
            with wave.open(temp.name, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, value)))
                                            for value in samples))
            return Path(temp.name).read_bytes()

    @staticmethod
    def _wav_container(format_code, payload, *, rate=8000, channels=1,
                       bits=32, extension=b""):
        width = bits // 8
        block_align = channels * width
        fmt = (struct.pack("<HHIIHH", format_code, channels, rate,
                           rate * block_align, block_align, bits) + extension)

        def chunk(name, data):
            return name + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")

        body = b"WAVE" + chunk(b"fmt ", fmt) + chunk(b"data", payload)
        return b"RIFF" + struct.pack("<I", len(body)) + body

    @classmethod
    def _float32_wav(cls):
        payload = b"".join(struct.pack("<f", math.sin(index / 10))
                           for index in range(800))
        return cls._wav_container(0x0003, payload)

    @classmethod
    def _float_wav(cls, values, *, bits=32, rate=8000, channels=1,
                   extensible=False):
        payload = np.asarray(values, dtype=f"<f{bits // 8}").tobytes()
        if not extensible:
            return cls._wav_container(
                0x0003, payload, rate=rate, channels=channels, bits=bits)
        float_guid = bytes.fromhex("0300000000001000800000aa00389b71")
        extension = struct.pack("<HHI", 22, bits, 0) + float_guid
        return cls._wav_container(
            0xFFFE, payload, rate=rate, channels=channels, bits=bits,
            extension=extension)

    @classmethod
    def _alaw_wav(cls):
        return cls._wav_container(0x0006, bytes(range(64)), bits=8)

    @classmethod
    def _extensible_pcm_wav(cls):
        pcm_guid = bytes.fromhex("0100000000001000800000aa00389b71")
        extension = struct.pack("<HHI", 22, 16, 0) + pcm_guid
        payload = b"".join(struct.pack("<h", int(2000 * math.sin(index / 10)))
                           for index in range(800))
        return cls._wav_container(0xFFFE, payload, bits=16, extension=extension)

    def test_nonfinal_unsupported_wav_is_isolated_to_dependent_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._workflow_request(root, "multitrack_recording_mix_master", [
                {"type": "audio/stem", "data": self._alaw_wav()},
                {"type": "audio/mix", "data": self._pcm16([0] * 800),
                 "parents": [(0, "mixed_from")]},
            ])
            result = self.score(request, root)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        failed = [item for item in result["findings"]
                  if item["code"] == "WAV_PARSER_FAILED"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["failure_type"], "unsupported")
        self.assertTrue(any(item["status"] == "unavailable"
                            for item in result["assessment_checks"]["physical_similarity"]))

    def test_unsupported_final_wav_still_returns_unsupported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._workflow_request(root, "mastering_service_only", [
                {"type": "audio/master", "data": self._alaw_wav()},
            ])
            result = self.score(request, root)
        self.assertEqual(result["execution_status"], "unsupported")
        self.assertIn("not supported", result["error"])

    def test_float32_and_float64_wav_are_stream_decoded_without_clipping(self):
        values = np.asarray([-1.3, -0.25, 0.0, 1.17], dtype=np.float64)
        for bits in (32, 64):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"float-{bits}.wav"
                path.write_bytes(self._float_wav(values, bits=bits))
                observed = strict_pcm_check(path)
                audio = _read_pcm(path)
            self.assertEqual(observed["codec_subtype"], "IEEE_FLOAT")
            self.assertEqual(observed["sample_format"], "float")
            self.assertEqual(audio["frames"].dtype, np.float32)
            np.testing.assert_allclose(
                audio["frames"][:, 0], values.astype(np.float32),
                rtol=0, atol=1e-6)

    def test_extensible_float_subformat_is_stream_decoded(self):
        values = np.asarray([-0.75, 0.25, 1.1], dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extensible-float.wav"
            path.write_bytes(self._float_wav(
                values, bits=32, extensible=True))
            observed = strict_pcm_check(path)
            audio = _read_pcm(path)
        self.assertEqual(observed["wav_format_tag"], "WAVE_FORMAT_EXTENSIBLE")
        self.assertEqual(observed["codec_subtype"], "IEEE_FLOAT")
        np.testing.assert_allclose(audio["frames"][:, 0], values)

    def test_nonfinite_float_samples_fail_when_content_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nonfinite.wav"
            path.write_bytes(self._float_wav([0.0, float("nan"), 1.0]))
            descriptor = inspect_wav(path)
            self.assertEqual(descriptor.format_kind, "float")
            with self.assertRaises(NonFiniteSamples):
                list(read_frames(path, descriptor))

    def test_nonfinite_nonfinal_wav_only_disables_dependent_content_checks(self):
        samples = [0.0] * 799 + [float("inf")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._workflow_request(
                root, "multitrack_recording_mix_master", [
                    {"type": "audio/stem", "data": self._float_wav(samples)},
                    {"type": "audio/mix", "data": self._pcm16([0] * 800),
                     "parents": [(0, "mixed_from")]},
                ])
            result = self.score(request, root)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        failures = [item for item in result["findings"]
                    if item["code"] == "WAV_SAMPLE_DATA_INVALID"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["reason_code"], "non_finite_samples")
        self.assertTrue(any(
            item["status"] == "unavailable"
            for item in result["assessment_checks"]["physical_similarity"]))

    def test_trailing_bytes_are_reported_as_nonfatal_riff_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trailing.wav"
            path.write_bytes(self._float_wav([0.0, 0.5]) + b"TRAIL")
            observed = strict_pcm_check(path)
        self.assertEqual(observed["trailing_bytes"], 5)
        self.assertIn("trailing_bytes:5", observed["wav_warnings"])

    def test_unfinalised_sizes_partial_frame_and_byte_rate_are_warned(self):
        data = bytearray(self._wav_container(0x0001, b"\x00\x00\xff", bits=16))
        struct.pack_into("<I", data, 4, 0xFFFFFFFF)
        struct.pack_into("<I", data, 28, 1)
        data_chunk = data.index(b"data")
        struct.pack_into("<I", data, data_chunk + 4, 0xFFFFFFFF)
        data.pop()  # Simulate an interrupted writer with one incomplete frame.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unfinalised.wav"
            path.write_bytes(data)
            descriptor = inspect_wav(path)
            decoded = list(read_frames(path, descriptor))
        self.assertEqual(descriptor.frame_count, 1)
        self.assertEqual(len(decoded), 1)
        self.assertIn("riff_size_unfinalised", descriptor.warnings)
        self.assertIn("data_size_unfinalised", descriptor.warnings)
        self.assertIn("partial_final_frame_ignored:1", descriptor.warnings)
        self.assertIn("byte_rate_mismatch", descriptor.warnings)

    def test_extensible_pcm_subformat_is_recognised(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extensible.wav"
            path.write_bytes(self._extensible_pcm_wav())
            observed = strict_pcm_check(path)
        self.assertEqual(observed["codec_subtype"], "PCM")
        self.assertEqual(observed["wav_format_tag"], "WAVE_FORMAT_EXTENSIBLE")
        self.assertEqual(observed["valid_bits_per_sample"], 16)

    def test_bound_files_are_hashed_once_before_direct_binding(self):
        with mock.patch("music_target.engine.hashlib.file_digest",
                        wraps=hashlib.file_digest) as digest:
            result = self.score()
        self.assertEqual(result["execution_status"], "succeeded")
        self.assertEqual(digest.call_count, len(self.template["objects"]))

    def test_source_revision_is_cached_per_process(self):
        source_revision.cache_clear()
        first = source_revision()
        first_info = source_revision.cache_info()
        second = source_revision()
        second_info = source_revision.cache_info()
        self.assertEqual(first, second)
        self.assertEqual(first_info.misses, 1)
        self.assertEqual(second_info.hits, 1)

    @staticmethod
    def _deterministic_samples(count, seed=1):
        state, result = seed, []
        for _ in range(count):
            state = (1664525 * state + 1013904223) & 0xffffffff
            result.append(((state >> 16) & 0xffff) - 32768)
        return result

    @staticmethod
    def _linear_attributes(scope, duration, gain, source_start=0.0,
                           target_start=0.0):
        return {
            "derivation_scope": scope,
            "source_start_seconds": source_start,
            "source_end_seconds": source_start + duration,
            "target_start_seconds": target_start,
            "target_end_seconds": target_start + duration,
            "gain": gain,
        }

    def test_physical_similarity_scores_clear_mix_contributions(self):
        left = [int(5000 * math.sin(index / 17)) for index in range(4000)]
        right = [int(3500 * math.cos(index / 23)) for index in range(4000)]
        target = [round(0.6 * a + 0.4 * b) for a, b in zip(left, right)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(left)},
                {"type": "audio/stem", "data": self._pcm16(right)},
                {"type": "audio/mix", "data": self._pcm16(target),
                 "parents": [(0, "mixed_from"), (1, "mixed_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        checks = result["assessment_checks"]["physical_similarity"]
        self.assertEqual(len(checks), 2)
        self.assertEqual({item["status"] for item in checks}, {"corroborated"})
        self.assertTrue(all(item["comparison_mode"] == "multi_source_contribution"
                            for item in checks))
        self.assertTrue(all(item["integrity_contribution"] == 1.0 for item in checks))
        self.assertTrue(all(item["content_match_is_provenance"] is False
                            for item in checks))

    def test_physical_similarity_treats_correlated_sources_as_inconclusive(self):
        first = self._deterministic_samples(4000, 23)
        duplicate = first.copy()
        duplicate[0] += 1
        target = [round(0.8 * value) for value in first]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(first)},
                {"type": "audio/stem", "data": self._pcm16(duplicate)},
                {"type": "audio/mix", "data": self._pcm16(target),
                 "parents": [(0, "mixed_from"), (1, "mixed_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        checks = result["assessment_checks"]["physical_similarity"]
        self.assertEqual({item["status"] for item in checks}, {"inconclusive"})
        self.assertTrue(all(item["integrity_contribution"] is None
                            for item in checks))
        self.assertTrue(any(item["code"] == "PHYSICAL_SIMILARITY_INCONCLUSIVE"
                            for item in result["findings"]))

    def test_physical_summary_averages_relationships_within_each_target(self):
        results = []
        for target, contributions in (("target-a", [1.0, 1.0, 1.0, 1.0, 0.0]),
                                      ("target-b", [1.0])):
            for contribution in contributions:
                results.append({
                    "target_hash": target,
                    "status": ("corroborated" if contribution else "contradicted"),
                    "integrity_contribution": contribution,
                })
        summary = _summarize_physical_similarity(results)
        self.assertEqual(summary["target_values"], {"target-a": 0.8,
                                                     "target-b": 1.0})
        self.assertEqual(summary["value"], 0.9)

    def test_pcm_reader_uses_ndarray_cached_windows_and_enforces_budget(self):
        rate = 1000
        samples = self._deterministic_samples(rate * 70, 821)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "long.wav"
            path.write_bytes(self._pcm16(samples, rate=rate))
            window = _read_pcm(path, 65.0, 70.0, max_seconds=None)
            self.assertIsInstance(window["frames"], np.ndarray)
            self.assertEqual(window["frames"].dtype, np.float32)
            self.assertEqual(window["frames"].shape, (5 * rate, 1))
            self.assertEqual(len(window["frames"]), 5 * rate)
            self.assertEqual(window["decoded_bytes"], 5 * rate * 4)
            self.assertAlmostEqual(window["start_seconds"], 65.0)
            with self.assertRaisesRegex(ValueError, "request budget"):
                _read_pcm(path, 65.0, 70.0, max_seconds=None, byte_budget=100)

            artefact = mock.Mock(_wav_parser_failure=None)
            artefact.has_file.return_value = True
            artefact.get_file.return_value = str(path)
            class Chain:
                pass
            chain = Chain()
            chain.artefacts = {"audio": artefact}
            first = _read_bound_pcm(chain, "audio", 65.0, 70.0, max_seconds=None)
            second = _read_bound_pcm(chain, "audio", 65.0, 70.0, max_seconds=None)
            self.assertIs(first, second)
            self.assertEqual(chain._pcm_cache_misses, 1)
            self.assertEqual(chain._pcm_cache_hits, 1)

    def test_pcm_reader_vectorizes_signed_24_bit_samples(self):
        values = np.asarray([-8388608, -1, 0, 1, 8388607], dtype=np.int32)
        encoded = bytearray()
        for value in values:
            encoded.extend((int(value) & 0xFFFFFF).to_bytes(3, "little"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pcm24.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(3)
                handle.setframerate(8000)
                handle.writeframes(bytes(encoded))
            audio = _read_pcm(path)
        self.assertEqual(audio["frames"].shape, (len(values), 1))
        self.assertEqual(audio["frames"].dtype, np.float32)
        np.testing.assert_allclose(
            audio["frames"][:, 0], values.astype(np.float32) / 8388608.0,
            rtol=0, atol=1e-7)

    def test_analysis_proxy_is_full_length_phase_aligned_and_cached(self):
        rate, seconds = 48_000, 11
        timeline = np.arange(rate * seconds, dtype=np.float64) / rate
        left = np.sin(2 * math.pi * 110 * timeline)
        right = np.cos(2 * math.pi * 170 * timeline)
        interleaved = np.column_stack((left, right))
        pcm = np.rint(interleaved * 12_000).astype("<i2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxy.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(pcm.tobytes())
            artefact = mock.Mock(_wav_parser_failure=None)
            artefact.has_file.return_value = True
            artefact.get_file.return_value = str(path)

            class Chain:
                pass

            chain = Chain()
            chain.artefacts = {"audio": artefact}
            first = _analysis_proxy(chain, "audio")
            second = _analysis_proxy(chain, "audio")
        self.assertIs(first, second)
        self.assertEqual(first["decimation_factor"], 24)
        self.assertEqual(first["sample_rate_hz"], 2000)
        self.assertEqual(first["frames"].shape, (seconds * 2000, 2))
        self.assertEqual(first["frames"].dtype, np.float32)
        self.assertEqual(first["analysis_ranges_seconds"], [[0.0, seconds]])
        self.assertTrue(first["search_complete"])
        self.assertEqual(chain._analysis_proxy_misses, 1)
        self.assertEqual(chain._analysis_proxy_hits, 1)
        taps = _fir_lowpass(rate, 2000 * 0.45)
        decoded = pcm.astype(np.float32) / 32768.0
        expected = np.column_stack([
            np.convolve(decoded[:, channel], taps, mode="same")[::24]
            for channel in range(2)
        ]).astype(np.float32)
        np.testing.assert_allclose(first["frames"], expected,
                                   rtol=1e-5, atol=2e-6)

    # Historical fixtures remain beside the deprecated reconstruction code for
    # reference.  They are intentionally not discovered: the active report
    # contract is exercised by the unified physical-similarity tests below.
    def _legacy_audio_derivation_pass_applies_declared_time_offset(self):
        source = [int(5000 * math.sin(index / 17)) for index in range(2000)]
        target = [0] * 100 + [round(0.7 * item) for item in source]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [{"type": "audio/stem", "data": self._pcm16(source)},
                     {"type": "audio/mix", "data": self._pcm16(target),
                      "parents": [(0, "mixed_from", self._linear_attributes(
                          "linear_mix", 0.25, 0.7, target_start=0.0125))]}]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        self.assertEqual(result["assessment_checks"]["audio_derivation"][0]["status"], "matched")

    def _legacy_audio_derivation_pass_does_not_assess_unscoped_mix(self):
        source, target = self._pcm16([1000] * 200), self._pcm16([900] * 200)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [{"type": "audio/stem", "data": source},
                     {"type": "audio/mix", "data": target,
                      "parents": [(0, "mixed_from")]}]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        self.assertEqual(result["assessment_checks"]["audio_derivation"][0]["status"],
                         "unavailable")
        self.assertEqual(result["assessment_checks"]["audio_derivation"][0]["reason_code"],
                         "missing_derivation_parameters")

    def _legacy_audio_derivation_does_not_infer_or_refit_gain(self):
        source = self._deterministic_samples(2000, 71)
        target = [round(0.5 * value) for value in source]
        declared = self._linear_attributes("linear_mix", 0.25, 0.5)
        missing_gain = copy.deepcopy(declared)
        del missing_gain["gain"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(source)},
                {"type": "audio/mix", "data": self._pcm16(target),
                 "parents": [(0, "mixed_from", missing_gain)]},
            ]
            missing_result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        missing_check = missing_result["assessment_checks"]["audio_derivation"][0]
        self.assertEqual(missing_check["status"], "unavailable")
        self.assertIn("gain", missing_check["missing_parameters"][0]["fields"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = self._linear_attributes("linear_mix", 0.25, 0.9)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(source)},
                {"type": "audio/mix", "data": self._pcm16(target),
                 "parents": [(0, "mixed_from", wrong)]},
            ]
            wrong_result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        wrong_check = wrong_result["assessment_checks"]["audio_derivation"][0]
        self.assertEqual(wrong_check["status"], "contradicted")
        self.assertEqual(wrong_check["declared_gains"], [0.9])
        diagnostic = wrong_result["assessment_checks"]["stem_mix_residual"][0]
        self.assertEqual(diagnostic["status"], "matched")
        self.assertTrue(diagnostic["estimated_parameters_are_diagnostic"])
        content_layer = next(
            item for item in wrong_result["assessment_checks"]["integrity_layers"]
            if item["layer"] == "content_reconstruction"
        )
        self.assertEqual(content_layer["status"], "contradicted")

    def _legacy_edit_derivation_pass_matches_declared_crop_and_gain(self):
        source = [int(7000 * math.sin(index / 19)) for index in range(2400)]
        edited = [round(0.7 * value) for value in source[400:1600]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-take", "data": self._pcm16(source)},
                {"type": "audio/edited-take", "data": self._pcm16(edited),
                 "parents": [(0, "edited_from", {
                     "derivation_scope": "linear_edit",
                     "source_start_seconds": 0.05,
                     "source_end_seconds": 0.2,
                     "target_start_seconds": 0.0,
                     "target_end_seconds": 0.15,
                     "gain": 0.7,
                 })]},
            ]
            result = self.score(self._workflow_request(
                root, "take_comping_and_editing", specs), root)
        check = result["assessment_checks"]["edit_derivation"][0]
        self.assertEqual(check["status"], "matched")
        self.assertEqual(check["verified_scope"], "linear_edit")

    def _legacy_edit_derivation_flags_repeated_source_segment_as_ambiguous(self):
        block = self._deterministic_samples(3200, 31)
        middle = self._deterministic_samples(3200, 32)
        source = block + middle + block
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-take", "data": self._pcm16(source)},
                {"type": "audio/edited-take", "data": self._pcm16(block),
                 "parents": [(0, "edited_from", {
                     "derivation_scope": "linear_edit",
                     "source_start_seconds": 0.0,
                     "source_end_seconds": 0.4,
                     "target_start_seconds": 0.0,
                     "target_end_seconds": 0.4,
                     "gain": 1.0,
                 })]},
            ]
            result = self.score(self._workflow_request(
                root, "take_comping_and_editing", specs), root)
        check = result["assessment_checks"]["edit_derivation"][0]
        self.assertEqual(check["status"], "not_applicable")
        self.assertTrue(check["alternative_source_locations"])
        self.assertIn("ambiguous_source_segment_location",
                      check["manual_review_reasons"])

    def _legacy_edit_derivation_pass_matches_splice_offsets_and_declared_fades(self):
        first = [int(7000 * math.sin(index / 19)) for index in range(1600)]
        second = [int(6000 * math.cos(index / 23)) for index in range(1600)]
        fade_frames = 80
        first_segment = first[400:1200]
        second_segment = second[400:1200]
        for index in range(fade_frames):
            first_segment[index] = round(first_segment[index] * index / fade_frames)
            first_segment[-index - 1] = round(first_segment[-index - 1] * (index + 1) / fade_frames)
            second_segment[index] = round(second_segment[index] * index / fade_frames)
            second_segment[-index - 1] = round(second_segment[-index - 1] * (index + 1) / fade_frames)
        edited = first_segment + second_segment
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            common = {"derivation_scope": "linear_edit",
                      "source_start_seconds": 0.05, "source_end_seconds": 0.15,
                      "fade_in_seconds": 0.01, "fade_out_seconds": 0.01,
                      "gain": 1.0}
            specs = [
                {"type": "audio/raw-take", "data": self._pcm16(first)},
                {"type": "audio/raw-take", "data": self._pcm16(second)},
                {"type": "audio/edited-take", "data": self._pcm16(edited),
                 "parents": [
                     (0, "edited_from", {**common, "target_start_seconds": 0.0,
                                          "target_end_seconds": 0.1}),
                     (1, "edited_from", {**common, "target_start_seconds": 0.1,
                                          "target_end_seconds": 0.2}),
                 ]},
            ]
            result = self.score(self._workflow_request(
                root, "take_comping_and_editing", specs), root)
        self.assertEqual(result["assessment_checks"]["edit_derivation"][0]["status"],
                         "matched")

    def _legacy_stem_derivation_pass_matches_declared_linear_stem(self):
        first = [int(4000 * math.sin(index / 13)) for index in range(2000)]
        second = [int(3000 * math.cos(index / 29)) for index in range(2000)]
        stem = [round(0.55 * left + 0.35 * right)
                for left, right in zip(first, second)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-track", "data": self._pcm16(first)},
                {"type": "audio/edited-take", "data": self._pcm16(second)},
                {"type": "audio/stem", "data": self._pcm16(stem),
                 "parents": [(0, "stemmed_from", self._linear_attributes(
                     "linear_stem", 0.25, 0.55)),
                             (1, "stemmed_from", self._linear_attributes(
                                 "linear_stem", 0.25, 0.35))]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        self.assertEqual(result["assessment_checks"]["stem_derivation"][0]["status"],
                         "matched")

    def _legacy_comp_derivation_pass_reconstructs_comp_sheet_timeline(self):
        take_a = [int(6000 * math.sin(index / 17)) for index in range(2400)]
        take_b = [int(5000 * math.cos(index / 31)) for index in range(2400)]
        comp = take_a[400:1200] + take_b[1200:2000]
        take_a_wav, take_b_wav, comp_wav = (
            self._pcm16(take_a), self._pcm16(take_b), self._pcm16(comp))
        take_a_hash = hashlib.sha256(take_a_wav).hexdigest()
        take_b_hash = hashlib.sha256(take_b_wav).hexdigest()
        comp_hash = hashlib.sha256(comp_wav).hexdigest()
        sheet = json.dumps({
            "schema_version": "1.0", "sheet_type": "comp",
            "song_title": "Content fixture", "target_hash": comp_hash,
            "selections": [
                {"selection_id": "S1", "take_id": "Take A", "source_hash": take_a_hash,
                 "source_start_seconds": 0.05, "source_end_seconds": 0.15,
                 "target_start_seconds": 0.0, "target_end_seconds": 0.1,
                 "gain": 1.0},
                {"selection_id": "S2", "take_id": "Take B", "source_hash": take_b_hash,
                 "source_start_seconds": 0.15, "source_end_seconds": 0.25,
                 "target_start_seconds": 0.1, "target_end_seconds": 0.2,
                 "gain": 1.0},
            ],
        }).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-take", "data": take_a_wav},
                {"type": "audio/raw-take", "data": take_b_wav},
                {"type": "text/comp-sheet", "data": sheet, "suffix": "json"},
                {"type": "audio/edited-take", "data": comp_wav,
                 "parents": [(0, "comped_from"), (1, "comped_from"), (2, "input_to")]},
            ]
            result = self.score(self._workflow_request(
                root, "take_comping_and_editing", specs), root)
        check = result["assessment_checks"]["comp_derivation"][0]
        self.assertEqual(check["status"], "matched")
        self.assertEqual(check["selection_count"], 2)
        physical = result["assessment_checks"]["comp_verification"]
        self.assertEqual(len(physical), 2)
        self.assertTrue(all(item["status"] in {"matched", "corroborated"}
                            for item in physical), physical)

    def _legacy_master_derivation_pass_corroborates_content_continuity(self):
        source = self._deterministic_samples(8000, 41)
        master = [round(0.8 * value) for value in source]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/mix", "data": self._pcm16(source)},
                {"type": "audio/master", "data": self._pcm16(master),
                 "parents": [(0, "mastered_from", {
                     "derivation_scope": "master_similarity",
                     "source_start_seconds": 0.0,
                     "source_end_seconds": 1.0,
                     "target_start_seconds": 0.0,
                     "target_end_seconds": 1.0,
                 })]},
            ]
            result = self.score(self._workflow_request(
                root, "mastering_service_only", specs), root)
        check = result["assessment_checks"]["master_derivation"][0]
        self.assertEqual(check["status"], "corroborated")
        self.assertIsNone(check["content_relationship_verified"])
        self.assertTrue(check["content_continuity_corroborated"])
        self.assertFalse(check["alignment_ambiguous"])
        self.assertEqual(check["parameter_source"], "submitter_declared")

    def _legacy_master_duration_delta_uses_full_files_beyond_sixty_seconds(self):
        rate, source_duration, master_duration = 1000, 70.0, 75.0
        source = self._deterministic_samples(round(rate * source_duration), 831)
        master = source + self._deterministic_samples(
            round(rate * (master_duration - source_duration)), 832)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/mix", "data": self._pcm16(source, rate=rate)},
                {"type": "audio/master", "data": self._pcm16(master, rate=rate),
                 "parents": [(0, "mastered_from", {
                     "derivation_scope": "master_similarity",
                     "source_start_seconds": 0.0,
                     "source_end_seconds": source_duration,
                     "target_start_seconds": 0.0,
                     "target_end_seconds": source_duration,
                 })]},
            ]
            result = self.score(self._workflow_request(
                root, "mastering_service_only", specs), root)
        check = result["assessment_checks"]["master_derivation"][0]
        self.assertEqual(check["duration_delta_seconds"], 5.0)
        self.assertGreater(check["alignment_coverage_ratio"], 0.99)

    def _legacy_master_derivation_does_not_infer_missing_alignment(self):
        source = [int(9000 * math.sin(2 * math.pi * index / 100))
                  for index in range(16000)]
        master = [round(0.8 * value) for value in source]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/mix", "data": self._pcm16(source)},
                {"type": "audio/master", "data": self._pcm16(master),
                 "parents": [(0, "mastered_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "mastering_service_only", specs), root)
        check = result["assessment_checks"]["master_derivation"][0]
        self.assertEqual(check["status"], "unavailable")
        self.assertEqual(check["reason_code"], "missing_derivation_parameters")
        self.assertEqual(check["claim_status"], "declared_unverified")

    def _legacy_master_derivation_does_not_replace_declared_alignment(self):
        source = self._deterministic_samples(8000, 72)
        shifted = source[1000:] + source[:1000]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/mix", "data": self._pcm16(source)},
                {"type": "audio/master", "data": self._pcm16(shifted),
                 "parents": [(0, "mastered_from", {
                     "derivation_scope": "master_similarity",
                     "source_start_seconds": 0.0,
                     "source_end_seconds": 1.0,
                     "target_start_seconds": 0.0,
                     "target_end_seconds": 1.0,
                 })]},
            ]
            result = self.score(self._workflow_request(
                root, "mastering_service_only", specs), root)
        check = result["assessment_checks"]["master_derivation"][0]
        self.assertNotEqual(check["status"], "corroborated")
        self.assertEqual(check["declared_offset_seconds"], 0.0)
        self.assertLess(check["declared_alignment_correlation"], 0.2)

    def _legacy_master_derivation_handles_mild_processing_and_sample_rate_conversion(self):
        source = self._deterministic_samples(16000, 51)
        processed = []
        previous = 0
        for index, value in enumerate(source):
            filtered = round(0.9 * value + 0.1 * previous)
            previous = value
            compressed = max(-14000, min(14000, filtered))
            dither = (index % 3) - 1
            processed.append(round(0.75 * compressed) + dither)
        fade = 80
        for index in range(fade):
            processed[index] = round(processed[index] * index / fade)
            processed[-index - 1] = round(processed[-index - 1] * (index + 1) / fade)
        converted = processed[::2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/mix", "data": self._pcm16(source, rate=8000)},
                {"type": "audio/master", "data": self._pcm16(converted, rate=4000),
                 "parents": [(0, "mastered_from", {
                     "derivation_scope": "master_similarity",
                     "source_start_seconds": 0.0,
                     "source_end_seconds": 2.0,
                     "target_start_seconds": 0.0,
                     "target_end_seconds": 2.0,
                     "transformation_description": "mild EQ, limiting, dither, fades, and sample-rate conversion",
                 })]},
            ]
            result = self.score(self._workflow_request(
                root, "mastering_service_only", specs), root)
        check = result["assessment_checks"]["master_derivation"][0]
        self.assertIn(check["status"], {"corroborated", "not_applicable"})
        self.assertNotEqual(check["status"], "contradicted")

    def _legacy_audio_alignment_pass_estimates_an_undeclared_shift(self):
        source = self._deterministic_samples(8000, 101)
        target = [0] * 800 + source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-take", "data": self._pcm16(source)},
                {"type": "audio/edited-take", "data": self._pcm16(target),
                 "parents": [(0, "edited_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "take_comping_and_editing", specs), root)
        check = result["assessment_checks"]["audio_alignment"][0]
        self.assertIn(check["status"], {"corroborated", "not_applicable"})
        self.assertAlmostEqual(check["estimated_offset_seconds"], 0.1, delta=0.05)
        self.assertEqual(check["parameter_source"], "signal_estimated_diagnostic")

    def _legacy_source_contribution_and_decoy_passes_find_unused_source(self):
        used = self._deterministic_samples(8000, 111)
        unused = self._deterministic_samples(8000, 112)
        mix = [round(0.8 * value) for value in used]
        attrs = {"source_start_seconds": 0.0, "source_end_seconds": 1.0,
                 "target_start_seconds": 0.0, "target_end_seconds": 1.0}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(used)},
                {"type": "audio/stem", "data": self._pcm16(unused)},
                {"type": "audio/mix", "data": self._pcm16(mix),
                 "parents": [(0, "mixed_from", attrs),
                             (1, "mixed_from", attrs)]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        contributions = result["assessment_checks"]["source_contribution"]
        by_source = {item["source_hash"]: item for item in contributions
                     if item.get("source_hash")}
        source_hashes = [hashlib.sha256(self._pcm16(item)).hexdigest()
                         for item in (used, unused)]
        self.assertEqual(by_source[source_hashes[0]]["status"], "corroborated")
        self.assertEqual(by_source[source_hashes[1]]["status"], "not_supported")
        decoys = result["assessment_checks"]["decoy_source"]
        self.assertTrue(any(item["status"] == "suspected_decoy" and
                            item["source_hash"] == source_hashes[1]
                            for item in decoys))

    def _legacy_source_contribution_guard_rejects_highly_correlated_sources(self):
        base = self._deterministic_samples(8000, 211)
        # A pure scalar rescaling is perfectly correlated with its source
        # (correlation is scale-invariant) but is still a distinct file.
        scaled = [round(0.5 * value) for value in base]
        mix = [round(0.5 * a + 0.3 * b) for a, b in zip(base, scaled)]
        attrs = {"source_start_seconds": 0.0, "source_end_seconds": 1.0,
                 "target_start_seconds": 0.0, "target_end_seconds": 1.0}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(base)},
                {"type": "audio/stem", "data": self._pcm16(scaled)},
                {"type": "audio/mix", "data": self._pcm16(mix),
                 "parents": [(0, "mixed_from", attrs),
                             (1, "mixed_from", attrs)]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        contributions = result["assessment_checks"]["source_contribution"]
        self.assertEqual(len(contributions), 2)
        for item in contributions:
            self.assertEqual(item["status"], "not_applicable")
            self.assertGreaterEqual(item["source_span_correlation"], MAX_SOURCE_CORRELATION)
            self.assertGreaterEqual(item["max_source_correlation"], MAX_SOURCE_CORRELATION)
            self.assertNotIn("estimated_gain", item)
            self.assertNotIn("gain_stability", item)
            self.assertNotIn("unique_residual_delta", item)
        # Each source lies in the span of the other, so decoy detection --
        # which only acts on "not_supported"/"corroborated" inputs -- must
        # also decline rather than misreporting either source as a decoy.
        decoys = result["assessment_checks"]["decoy_source"]
        self.assertTrue(decoys)
        for item in decoys:
            self.assertEqual(item["status"], "not_applicable")

    def _legacy_stem_mix_residual_withholds_matrix_for_highly_correlated_sources(self):
        base = self._deterministic_samples(8000, 221)
        scaled = [round(0.6 * value) for value in base]
        target = [round(0.4 * a + 0.2 * b) for a, b in zip(base, scaled)]
        result = self._score_mix([
            {"type": "audio/stem", "data": self._pcm16(base)},
            {"type": "audio/stem", "data": self._pcm16(scaled)},
            {"type": "audio/mix", "data": self._pcm16(target),
             "parents": [(0, "mixed_from"), (1, "mixed_from")]},
        ])
        check = result["assessment_checks"]["stem_mix_residual"][0]
        # The joint reconstruction is still assessed; only the individual
        # gains, which are arbitrary between the two sources, are withheld.
        self.assertEqual(check["status"], "matched")
        self.assertLessEqual(check["normalized_rmse"], 0.05)
        self._assert_matrix_withheld(check)
        self.assertIn("no individual source is attributed", check["reason"])
        self.assertTrue(check["estimated_parameters_are_diagnostic"])

    @staticmethod
    def _pcm16_stereo(left, right, rate=8000):
        with tempfile.NamedTemporaryFile(suffix=".wav") as temp:
            with wave.open(temp.name, "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(b"".join(
                    struct.pack("<hh", *(max(-32768, min(32767, value)) for value in frame))
                    for frame in zip(left, right)))
            return Path(temp.name).read_bytes()

    _FULL_SECOND = {"source_start_seconds": 0.0, "source_end_seconds": 1.0,
                    "target_start_seconds": 0.0, "target_end_seconds": 1.0}

    def _score_mix(self, specs):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            return self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)

    def _assert_matrix_withheld(self, check):
        self.assertNotIn("estimated_channel_matrix", check)
        self.assertTrue(check["channel_matrix_withheld_reason"])
        self.assertGreaterEqual(check["max_source_correlation"], MAX_SOURCE_CORRELATION)

    @staticmethod
    def _finding_codes(result):
        return [item["code"] for item in result["findings"]]

    def _legacy_stem_mix_residual_matches_dual_mono_stereo_stem(self):
        base = self._deterministic_samples(8000, 301)
        stem = [round(0.5 * value) for value in base]
        mix = [round(0.4 * value) for value in base]
        result = self._score_mix([
            {"type": "audio/stem", "data": self._pcm16_stereo(stem, stem)},
            {"type": "audio/mix", "data": self._pcm16_stereo(mix, mix),
             "parents": [(0, "mixed_from")]},
        ])
        check = result["assessment_checks"]["stem_mix_residual"][0]
        self.assertEqual(check["status"], "matched")
        self.assertLessEqual(check["normalized_rmse"], 0.05)
        self._assert_matrix_withheld(check)
        self.assertNotIn("PHYSICAL_AUDIO_CONTRADICTION", self._finding_codes(result))

    def _legacy_stem_mix_residual_matches_panned_stereo_stem(self):
        base = self._deterministic_samples(8000, 301)
        result = self._score_mix([
            {"type": "audio/stem", "data": self._pcm16_stereo(
                [round(0.8 * value) for value in base],
                [round(0.3 * value) for value in base])},
            {"type": "audio/mix", "data": self._pcm16_stereo(
                [round(0.4 * value) for value in base],
                [round(0.15 * value) for value in base]),
             "parents": [(0, "mixed_from")]},
        ])
        check = result["assessment_checks"]["stem_mix_residual"][0]
        self.assertEqual(check["status"], "matched")
        self.assertLessEqual(check["normalized_rmse"], 0.05)
        self._assert_matrix_withheld(check)

    def _legacy_stem_mix_residual_contradicts_dual_mono_stem_with_unrelated_target(self):
        stem = [round(0.5 * value) for value in self._deterministic_samples(8000, 301)]
        other = [round(0.5 * value) for value in self._deterministic_samples(8000, 304)]
        result = self._score_mix([
            {"type": "audio/stem", "data": self._pcm16_stereo(stem, stem)},
            {"type": "audio/mix", "data": self._pcm16_stereo(other, other),
             "parents": [(0, "mixed_from")]},
        ])
        check = result["assessment_checks"]["stem_mix_residual"][0]
        self.assertEqual(check["status"], "contradicted")
        self.assertGreaterEqual(check["normalized_rmse"], 0.65)
        self._assert_matrix_withheld(check)
        self.assertIn("PHYSICAL_AUDIO_CONTRADICTION", self._finding_codes(result))

    def _legacy_stem_mix_residual_contradicts_duplicate_stems_with_unrelated_target(self):
        base = self._deterministic_samples(8000, 301)
        duplicate = [round(0.5 * value) for value in base]
        other = self._deterministic_samples(8000, 304)
        result = self._score_mix([
            {"type": "audio/stem", "data": self._pcm16(base)},
            {"type": "audio/stem", "data": self._pcm16(duplicate)},
            {"type": "audio/mix", "data": self._pcm16(other),
             "parents": [(0, "mixed_from"), (1, "mixed_from")]},
        ])
        check = result["assessment_checks"]["stem_mix_residual"][0]
        self.assertEqual(check["status"], "contradicted")
        self._assert_matrix_withheld(check)
        self.assertIn("PHYSICAL_AUDIO_CONTRADICTION", self._finding_codes(result))
        # Neither duplicate is individually blamed for the mismatch.
        contributions = result["assessment_checks"]["source_contribution"]
        self.assertEqual([item["status"] for item in contributions],
                         ["not_applicable", "not_applicable"])
        self.assertNotIn("DECOY_SOURCE_SUSPECTED", self._finding_codes(result))

    def _duplicates_plus_third_source(self, mix_from_third):
        base = self._deterministic_samples(8000, 301)
        duplicate = [round(0.5 * value) for value in base]
        third = self._deterministic_samples(8000, 303)
        if mix_from_third:
            mix = [round(0.4 * a + 0.4 * c) for a, c in zip(base, third)]
        else:
            mix = [round(0.5 * a + 0.3 * b) for a, b in zip(base, duplicate)]
        result = self._score_mix([
            {"type": "audio/stem", "data": self._pcm16(base)},
            {"type": "audio/stem", "data": self._pcm16(duplicate)},
            {"type": "audio/stem", "data": self._pcm16(third)},
            {"type": "audio/mix", "data": self._pcm16(mix),
             "parents": [(index, "mixed_from", self._FULL_SECOND) for index in range(3)]},
        ])
        hashes = [hashlib.sha256(self._pcm16(item)).hexdigest()
                  for item in (base, duplicate, third)]
        contributions = {item["source_hash"]: item for item in
                         result["assessment_checks"]["source_contribution"]}
        decoys = {item["source_hash"]: item for item in
                  result["assessment_checks"]["decoy_source"]}
        for digest in hashes[:2]:
            self.assertEqual(contributions[digest]["status"], "not_applicable")
            self.assertNotIn("estimated_gain", contributions[digest])
            self.assertNotIn("unique_residual_delta", contributions[digest])
            self.assertEqual(decoys[digest]["status"], "not_applicable")
        return result, contributions[hashes[2]], decoys[hashes[2]]

    def _legacy_source_contribution_flags_unrelated_decoy_beside_duplicate_sources(self):
        result, contribution, decoy = self._duplicates_plus_third_source(False)
        self.assertEqual(contribution["status"], "not_supported")
        self.assertEqual(decoy["status"], "suspected_decoy")
        codes = self._finding_codes(result)
        self.assertEqual(codes.count("DECLARED_SOURCE_NOT_SUPPORTED"), 1)
        self.assertEqual(codes.count("DECOY_SOURCE_SUSPECTED"), 1)

    def _legacy_source_contribution_corroborates_used_source_beside_duplicate_sources(self):
        result, contribution, decoy = self._duplicates_plus_third_source(True)
        self.assertEqual(contribution["status"], "corroborated")
        self.assertAlmostEqual(contribution["estimated_gain"], 0.4, delta=0.02)
        self.assertEqual(decoy["status"], "clear")
        self.assertNotIn("DECOY_SOURCE_SUSPECTED", self._finding_codes(result))

    def _legacy_independent_sources_keep_gains_and_channel_matrix(self):
        first = self._deterministic_samples(8000, 301)
        second = self._deterministic_samples(8000, 302)
        mix = [round(0.5 * a + 0.3 * b) for a, b in zip(first, second)]
        result = self._score_mix([
            {"type": "audio/stem", "data": self._pcm16(first)},
            {"type": "audio/stem", "data": self._pcm16(second)},
            {"type": "audio/mix", "data": self._pcm16(mix),
             "parents": [(0, "mixed_from", self._FULL_SECOND),
                         (1, "mixed_from", self._FULL_SECOND)]},
        ])
        check = result["assessment_checks"]["stem_mix_residual"][0]
        self.assertEqual(check["status"], "matched")
        self.assertNotIn("channel_matrix_withheld_reason", check)
        self.assertAlmostEqual(check["estimated_channel_matrix"][0][0], 0.5, delta=0.02)
        self.assertAlmostEqual(check["estimated_channel_matrix"][0][1], 0.3, delta=0.02)
        contributions = result["assessment_checks"]["source_contribution"]
        self.assertEqual([item["status"] for item in contributions],
                         ["corroborated", "corroborated"])
        self.assertAlmostEqual(contributions[0]["estimated_gain"], 0.5, delta=0.02)
        self.assertAlmostEqual(contributions[1]["estimated_gain"], 0.3, delta=0.02)
        for item in contributions:
            self.assertNotIn("source_span_correlation", item)
        self.assertEqual([item["status"] for item in
                          result["assessment_checks"]["decoy_source"]], ["clear", "clear"])

    def _legacy_processed_audio_match_corroborates_light_processing(self):
        source = self._deterministic_samples(12000, 121)
        processed, previous = [], 0
        for value in source:
            filtered = round(0.75 * value + 0.25 * previous)
            previous = value
            processed.append(max(-15000, min(15000, round(0.65 * filtered))))
        attrs = {"source_start_seconds": 0.0, "source_end_seconds": 1.5,
                 "target_start_seconds": 0.0, "target_end_seconds": 1.5,
                 "transformation_description": "light EQ and compression"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-take", "data": self._pcm16(source)},
                {"type": "audio/edited-take", "data": self._pcm16(processed),
                 "parents": [(0, "edited_from", attrs)]},
            ]
            result = self.score(self._workflow_request(
                root, "take_comping_and_editing", specs), root)
        check = result["assessment_checks"]["processed_audio_match"][0]
        self.assertEqual(check["status"], "corroborated")
        self.assertTrue(check["declared_complex_processing"])
        self.assertGreaterEqual(check["robust_similarity"], 0.72)

    def _legacy_stem_mix_residual_pass_estimates_source_gain(self):
        source = self._deterministic_samples(8000, 131)
        target = [round(0.4 * value) for value in source]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(source)},
                {"type": "audio/mix", "data": self._pcm16(target),
                 "parents": [(0, "mixed_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        check = result["assessment_checks"]["stem_mix_residual"][0]
        self.assertEqual(check["status"], "matched")
        self.assertAlmostEqual(check["estimated_channel_matrix"][0][0], 0.4,
                               delta=0.02)
        self.assertTrue(check["estimated_parameters_are_diagnostic"])

    def _legacy_excerpted_from_pass_finds_sample_in_recording(self):
        source = self._deterministic_samples(16000, 141)
        sample = source[4800:8800]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-track", "data": self._pcm16(source)},
                {"type": "audio/sample", "data": self._pcm16(sample),
                 "parents": [(0, "excerpted_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "custom_or_unknown_workflow", specs), root)
        check = result["assessment_checks"]["excerpted_from"][0]
        self.assertIn(check["status"], {"matched", "corroborated"})
        self.assertAlmostEqual(check["estimated_source_start_seconds"], 0.6,
                               delta=0.08)

    def _legacy_excerpt_search_finds_audio_after_sixty_seconds(self):
        rate = 1000
        source = self._deterministic_samples(70 * rate, 1141)
        sample = source[65 * rate:66 * rate]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-track", "data": self._pcm16(source, rate=rate)},
                {"type": "audio/sample", "data": self._pcm16(sample, rate=rate),
                 "parents": [(0, "excerpted_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "custom_or_unknown_workflow", specs), root)
        check = result["assessment_checks"]["excerpted_from"][0]
        self.assertIn(check["status"], {"matched", "corroborated"}, check)
        self.assertAlmostEqual(check["estimated_source_start_seconds"], 65.0,
                               delta=0.08)
        self.assertTrue(check["search_complete"])

    def _legacy_incomplete_budget_never_returns_search_contradiction(self):
        source = self._deterministic_samples(8000, 1151)
        sample = source[2000:4000]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-track", "data": self._pcm16(source)},
                {"type": "audio/sample", "data": self._pcm16(sample),
                 "parents": [(0, "excerpted_from")]},
            ]
            with mock.patch(
                    "music_target.audio_derivation.MAX_REQUEST_WORK_UNITS", 0.1):
                result = self.score(self._workflow_request(
                    root, "custom_or_unknown_workflow", specs), root)
        check = result["assessment_checks"]["excerpted_from"][0]
        self.assertEqual(check["status"], "unavailable")
        self.assertEqual(check["reason_code"], "resource_budget_exhausted")
        self.assertFalse(check["search_complete"])

    def _legacy_mastering_derivation_public_pass_name_is_exposed(self):
        source = self._deterministic_samples(8000, 151)
        attrs = {"derivation_scope": "master_similarity",
                 "source_start_seconds": 0.0, "source_end_seconds": 1.0,
                 "target_start_seconds": 0.0, "target_end_seconds": 1.0}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/mix", "data": self._pcm16(source)},
                {"type": "audio/master", "data": self._pcm16(
                    [round(0.8 * value) for value in source]),
                 "parents": [(0, "mastered_from", attrs)]},
            ]
            result = self.score(self._workflow_request(
                root, "mastering_service_only", specs), root)
        check = result["assessment_checks"]["mastering_derivation"][0]
        self.assertEqual(check["pass"], "MasteringDerivationPass")
        self.assertEqual(check["legacy_pass"], "MasterDerivationPass")

    def _legacy_integrity_layers_do_not_score_endpoint_validity_as_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(
                    self._deterministic_samples(2000, 61))},
                {"type": "audio/mix", "data": self._pcm16(
                    self._deterministic_samples(2000, 62)),
                 "parents": [(0, "mixed_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        layers = {item["layer"]: item for item in
                  result["assessment_checks"]["integrity_layers"]}
        self.assertEqual(layers["structural_validity"]["status"], "matched")
        self.assertFalse(layers["structural_validity"]["contributes_to_integrity"])
        self.assertEqual(layers["declaration_consistency"]["status"], "unavailable")
        self.assertEqual(layers["content_reconstruction"]["status"], "unavailable")
        self.assertIsNone(result["axes"]["integrity"]["value"])
        self.assertEqual({item["status"] for item in
                          result["assessment_checks"]["assessment_status_semantics"]},
                         {"matched", "corroborated", "contradicted",
                          "not_applicable", "unavailable"})

    def test_unified_physical_output_replaces_legacy_report_keys(self):
        result = self.score()
        checks = result["assessment_checks"]
        legacy_keys = {
            "audio_derivation", "edit_derivation", "comp_derivation",
            "stem_derivation", "master_derivation", "audio_alignment",
            "source_contribution", "comp_verification",
            "processed_audio_match", "stem_mix_residual",
            "mastering_derivation", "excerpted_from", "decoy_source",
        }
        self.assertTrue(legacy_keys.isdisjoint(checks))
        self.assertIn("physical_similarity", checks)
        self.assertIn("physical_similarity_summary", checks)
        allowed = {"corroborated", "contradicted", "inconclusive", "unavailable"}
        self.assertTrue(all(item["status"] in allowed
                            for item in checks["physical_similarity"]))
        self.assertTrue(all(item["content_match_is_provenance"] is False
                            for item in checks["physical_similarity"]))
        semantics = {item["status"] for item in
                     checks["assessment_status_semantics"]}
        self.assertEqual(semantics, allowed)

    def test_pairwise_continuity_corroborates_light_processing(self):
        source = self._deterministic_samples(12000, 121)
        processed, previous = [], 0
        for value in source:
            filtered = round(0.75 * value + 0.25 * previous)
            previous = value
            processed.append(max(-15000, min(15000, round(0.65 * filtered))))
        attrs = {"source_start_seconds": 0.0, "source_end_seconds": 1.5,
                 "target_start_seconds": 0.0, "target_end_seconds": 1.5,
                 "transformation_description": "light EQ and compression"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-take", "data": self._pcm16(source)},
                {"type": "audio/edited-take", "data": self._pcm16(processed),
                 "parents": [(0, "edited_from", attrs)]},
            ]
            result = self.score(self._workflow_request(
                root, "take_comping_and_editing", specs), root)
        check = result["assessment_checks"]["physical_similarity"][0]
        self.assertEqual(check["comparison_mode"], "pairwise_continuity")
        self.assertEqual(check["status"], "corroborated")
        self.assertGreaterEqual(check["metrics"]["robust_similarity"], 0.72)
        self.assertEqual(check["integrity_contribution"], 1.0)

    def test_generic_derivation_difference_is_inconclusive_not_adverse(self):
        source = self._deterministic_samples(8000, 171)
        unrelated = self._deterministic_samples(8000, 172)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-track", "data": self._pcm16(source)},
                {"type": "audio/edited-take", "data": self._pcm16(unrelated),
                 "parents": [(0, "derived_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "custom_or_unknown_workflow", specs), root)
        check = result["assessment_checks"]["physical_similarity"][0]
        self.assertEqual(check["status"], "inconclusive")
        self.assertIsNone(check["integrity_contribution"])
        self.assertFalse(any(item["code"] == "PHYSICAL_SIMILARITY_CONTRADICTION"
                             for item in result["findings"]))

    def test_clear_unused_mix_source_is_contradicted_only_with_joint_support(self):
        used = self._deterministic_samples(8000, 181)
        decoy = self._deterministic_samples(8000, 182)
        target = [round(0.65 * value) for value in used]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(used)},
                {"type": "audio/stem", "data": self._pcm16(decoy)},
                {"type": "audio/mix", "data": self._pcm16(target),
                 "parents": [(0, "mixed_from"), (1, "mixed_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        checks = result["assessment_checks"]["physical_similarity"]
        statuses = sorted(item["status"] for item in checks)
        self.assertEqual(statuses, ["contradicted", "corroborated"])
        contradicted = next(item for item in checks
                            if item["status"] == "contradicted")
        self.assertEqual(contradicted["reason_code"],
                         "declared_source_not_supported")
        self.assertEqual(contradicted["joint_model_status"], "corroborated")
        self.assertEqual(contradicted["integrity_contribution"], 0.0)
        self.assertTrue(any(item["code"] == "PHYSICAL_SIMILARITY_CONTRADICTION"
                            for item in result["findings"]))

    def test_excerpt_search_corroborates_and_budget_failure_is_unavailable(self):
        source = self._deterministic_samples(16000, 141)
        sample = source[4800:8800]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._workflow_request(root, "custom_or_unknown_workflow", [
                {"type": "audio/raw-track", "data": self._pcm16(source)},
                {"type": "audio/sample", "data": self._pcm16(sample),
                 "parents": [(0, "excerpted_from")]},
            ])
            result = self.score(request, root)
            with mock.patch(
                    "music_target.audio_derivation.MAX_REQUEST_WORK_UNITS", 0.1):
                limited = self.score(request, root)
        check = result["assessment_checks"]["physical_similarity"][0]
        self.assertEqual(check["comparison_mode"], "excerpt_search")
        self.assertEqual(check["status"], "corroborated")
        self.assertAlmostEqual(
            check["metrics"]["estimated_source_start_seconds"], 0.6, delta=0.08)
        limited_check = limited["assessment_checks"]["physical_similarity"][0]
        self.assertEqual(limited_check["status"], "unavailable")
        self.assertEqual(limited_check["reason_code"], "resource_budget_exhausted")
        self.assertIsNone(limited_check["integrity_contribution"])

    def test_master_continuity_corroborates_gain_and_format_tolerant_content(self):
        source = self._deterministic_samples(8000, 151)
        master = [round(0.8 * value) for value in source]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/mix", "data": self._pcm16(source)},
                {"type": "audio/master", "data": self._pcm16(master),
                 "parents": [(0, "mastered_from")]},
            ]
            result = self.score(self._workflow_request(
                root, "mastering_service_only", specs), root)
        check = result["assessment_checks"]["physical_similarity"][0]
        self.assertEqual(check["comparison_mode"], "master_continuity")
        self.assertEqual(check["status"], "corroborated")
        self.assertGreaterEqual(check["metrics"]["waveform_correlation"], 0.95)
        self.assertFalse(check["content_match_is_provenance"])

    def test_comp_relationship_has_one_primary_result_with_selection_details(self):
        take = self._deterministic_samples(4000, 191)
        comp = take[800:2400]
        take_wav, comp_wav = self._pcm16(take), self._pcm16(comp)
        take_hash = hashlib.sha256(take_wav).hexdigest()
        comp_hash = hashlib.sha256(comp_wav).hexdigest()
        sheet = json.dumps({
            "schema_version": "1.0", "sheet_type": "comp",
            "target_hash": comp_hash,
            "selections": [{
                "selection_id": "S1", "take_id": "Take A",
                "source_hash": take_hash,
                "source_start_seconds": 0.1, "source_end_seconds": 0.3,
                "target_start_seconds": 0.0, "target_end_seconds": 0.2,
                "gain": 1.0,
            }],
        }).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-take", "data": take_wav},
                {"type": "text/comp-sheet", "data": sheet, "suffix": "json"},
                {"type": "audio/edited-take", "data": comp_wav,
                 "parents": [(0, "comped_from"), (1, "input_to")]},
            ]
            result = self.score(self._workflow_request(
                root, "take_comping_and_editing", specs), root)
        comp_checks = [item for item in
                       result["assessment_checks"]["physical_similarity"]
                       if item["relationship_type"] == "comped_from"]
        self.assertEqual(len(comp_checks), 1)
        self.assertEqual(comp_checks[0]["comparison_mode"], "selection_timeline")
        self.assertEqual(comp_checks[0]["status"], "corroborated")
        self.assertEqual([item["selection_id"] for item in
                          comp_checks[0]["selection_results"]], ["S1"])

    def test_integrity_uses_physical_layer_and_only_endpoint_results(self):
        result = self.score()
        layers = {item["layer"]: item for item in
                  result["assessment_checks"]["integrity_layers"]}
        self.assertNotIn("content_reconstruction", layers)
        self.assertIn("physical_similarity", layers)
        self.assertFalse(layers["structural_validity"]["contributes_to_integrity"])
        self.assertEqual(layers["structural_validity"]["status"], "corroborated")
        summary = result["assessment_checks"]["physical_similarity_summary"][0]
        self.assertEqual(summary["scored_relationships"],
                         summary["counts"]["corroborated"] +
                         summary["counts"]["contradicted"])
        self.assertEqual(summary["declared_relationships"],
                         len(result["assessment_checks"]["physical_similarity"]))

    def test_comp_sheet_text_parser_extracts_selections(self):
        raw_hash, target_hash = "1" * 64, "2" * 64
        text = ("Song Title: Fixture Song\n"
                "selection_id|take_id|source_hash|target_hash|source_start_seconds|source_end_seconds|target_start_seconds|target_end_seconds|gain|notes\n"
                f"S1|Take A|{raw_hash}|{target_hash}|00:00.000|00:00.250|0|0.25|0.8|opening phrase\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comp.txt"
            path.write_text(text)
            parsed = parse_comp_sheet_file(path)
        self.assertEqual(parsed["input_format"], "txt")
        self.assertEqual(parsed["selections"][0]["take_id"], "Take A")
        self.assertEqual(parsed["selections"][0]["source_end_seconds"], 0.25)
        self.assertEqual(parsed["selections"][0]["gain"], 0.8)
        self.assertFalse(parsed["warnings"])

    @staticmethod
    def _text_pdf(lines):
        def escaped(value):
            return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands = ["BT /F1 10 Tf 72 720 Td"]
        for index, line in enumerate(lines):
            if index:
                commands.append("0 -14 Td")
            commands.append(f"({escaped(line)}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        output, offsets = bytearray(b"%PDF-1.4\n"), [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(output))
            output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(output)
        output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
        output.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            output.extend(f"{offset:010d} 00000 n \n".encode())
        output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        return bytes(output)

    def test_cue_sheet_pdf_parser_extracts_cues(self):
        asset_hash = "3" * 64
        pdf = self._text_pdf([
            "Production Title: Fixture Film",
            "cue_id,title,start_seconds,duration_seconds,composer,publisher,usage,asset_hash",
            f"C1,Opening,0,0.25,Alice,Fixture Publishing,background,{asset_hash}",
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cue.pdf"
            path.write_bytes(pdf)
            parsed = parse_cue_sheet_file(path)
        self.assertEqual(parsed["input_format"], "pdf")
        self.assertEqual(parsed["cues"][0]["composer"], "Alice")
        self.assertFalse(parsed["warnings"])

    def test_comp_sheet_workflow_parser_and_cross_evidence_end_to_end(self):
        raw_a, raw_b, edited = wav_bytes(510), wav_bytes(520), wav_bytes(530)
        raw_a_hash, raw_b_hash, edited_hash = (hashlib.sha256(item).hexdigest()
                                                for item in (raw_a, raw_b, edited))
        sheet = json.dumps({
            "schema_version": "1.0", "sheet_type": "comp",
            "song_title": "Fixture Comp", "target_hash": edited_hash,
            "selections": [
                {"selection_id": "S1", "take_id": "Take A", "source_hash": raw_a_hash,
                 "source_start_seconds": 0.0, "source_end_seconds": 0.2,
                 "target_start_seconds": 0.0, "target_end_seconds": 0.2,
                 "gain": 1.0},
                {"selection_id": "S2", "take_id": "Take B", "source_hash": raw_b_hash,
                 "source_start_seconds": 0.2, "source_end_seconds": 0.4,
                 "target_start_seconds": 0.2, "target_end_seconds": 0.4,
                 "gain": 1.0},
            ]}).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/raw-take", "data": raw_a},
                {"type": "audio/raw-take", "data": raw_b},
                {"type": "text/comp-sheet", "data": sheet, "suffix": "json"},
                {"type": "audio/edited-take", "data": edited,
                 "parents": [(0, "comped_from"), (1, "comped_from"), (2, "input_to")]},
            ]
            result = self.score(self._workflow_request(root, "take_comping_and_editing", specs), root)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertEqual(result["axes"]["completeness"]["value"], 1.0)
        self.assertTrue(all(item["passed"] for item in result["assessment_checks"]["cross_evidence"]))
        self.assertTrue(any(item.get("parser") == "comp-sheet" and item["parser_status"] == "parsed"
                            for item in result["findings"]))

    def test_cue_sheet_workflow_parser_and_cross_evidence_end_to_end(self):
        audio = wav_bytes(540)
        audio_hash = hashlib.sha256(audio).hexdigest()
        sheet = json.dumps({
            "schema_version": "1.0", "sheet_type": "cue", "production_title": "Fixture Film",
            "cues": [{"cue_id": "C1", "title": "Opening", "composer": "Alice",
                      "start_seconds": 0.0, "duration_seconds": 0.25,
                      "asset_hash": audio_hash}]}).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "text/cue-sheet", "data": sheet, "suffix": "json"},
                {"type": "audio/mix", "data": audio, "parents": [(0, "input_to")]},
            ]
            result = self.score(self._workflow_request(root, "sync_or_audiovisual_delivery", specs), root)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertEqual(result["axes"]["completeness"]["value"], 1.0)
        self.assertTrue(all(item["passed"] for item in result["assessment_checks"]["cross_evidence"]))
        self.assertTrue(any(item.get("parser") == "cue-sheet" and item["parser_status"] == "parsed"
                            for item in result["findings"]))

    def test_unparseable_required_cue_sheet_is_not_assessed_not_satisfied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "text/cue-sheet", "data": b"not-json", "suffix": "json"},
                {"type": "audio/mix", "data": wav_bytes(550), "parents": [(0, "input_to")]},
            ]
            result = self.score(self._workflow_request(root, "sync_or_audiovisual_delivery", specs), root)
        cue_rule = next(item for item in result["workflow_assessment"]["rule_results"]
                        if item["rule_id"] == "sync.cue_sheet")
        self.assertEqual(cue_rule["status"], "not_assessed")
        self.assertEqual(result["axes"]["completeness"]["availability"], "partial")
        self.assertLess(result["axes"]["completeness"]["confidence"], 1.0)

    def test_rin_ern_parsers_and_cross_evidence_are_integrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "objects").mkdir()
            request = copy.deepcopy(self.template)
            for digest, relative in request["objects"].items():
                (root / relative).write_bytes((self.folder / relative).read_bytes())
            documents = [
                ("metadata/ddex-rin", "rin", b'<R xmlns="urn:test"><Session><SessionId>S1</SessionId>'
                 b'<Party><Name>Fixture Mastering Engineer</Name></Party></Session>'
                 b'<RecordingComponent><ComponentId>C1</ComponentId></RecordingComponent></R>'),
                ("metadata/ddex-ern", "ern", b'<R xmlns="urn:test"><Release>'
                 b'<ReleaseReference>REL1</ReleaseReference><TitleText>Fixture Release</TitleText>'
                 b'<ReleaseType>Album</ReleaseType></Release></R>'),
            ]
            final = request["chain"]["artefacts"][-1]
            for kind, label, data in documents:
                digest = hashlib.sha256(data).hexdigest()
                relative = f"objects/{digest}.xml"
                (root / relative).write_bytes(data)
                request["objects"][digest] = relative
                request["chain"]["artefacts"].insert(0, {
                    "artefact_hash": digest, "artefact_type": kind,
                    "attributes": {}, "evidence": []})
                final["evidence"].append({"hash": digest, "relationship_type": "input_to", "attributes": {}})
            result = self.score(request, root)
            self.assertEqual(result["execution_status"], "succeeded", result["error"])
            parsers = {item.get("parser"): item.get("parser_status") for item in result["findings"]
                       if item["code"] == "PARSER_OBSERVATION"}
            self.assertEqual(parsers["rin"], "partial")
            self.assertEqual(parsers["ern"], "partial")
            self.assertTrue(result["assessment_checks"]["cross_evidence"][0]["passed"])

    def test_missing_final_file_is_an_execution_error(self):
        request = copy.deepcopy(self.template)
        del request["objects"][request["chain"]["final_artefact_hash"]]
        self.assertEqual(self.score(request)["execution_status"], "error")

    def test_technical_claims_do_not_replace_observed_properties(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"][-1]["attributes"]["technical"]["sample_rate_hz"] = 48000
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded")
        self.assertTrue(any(f["code"] == "TECHNICAL_CLAIM_MISMATCH" for f in result["findings"]))
        observation = next(f for f in result["findings"] if f["code"] == "WAV_OBSERVATION"
                           and f["evidence_hash"] == request["chain"]["final_artefact_hash"])
        self.assertEqual(observation["observed"]["sample_rate_hz"], 8000)
        failed_wav_checks = [item for item in observation["checks"] if not item["passed"]]
        self.assertEqual([item["rule"] for item in failed_wav_checks],
                         ["technical.sample_rate_hz_matches_observed"])
        bound_checks = result["assessment_checks"]["bound_file_integrity"]
        self.assertEqual(sum(item == failed_wav_checks[0] for item in bound_checks), 1)
        self.assertFalse(any(item["rule"].startswith("technical.") for item in
                             result["assessment_checks"]["relationship_integrity"]))

    def test_cycle_is_rejected_by_library(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"][0]["evidence"] = [
            {"hash": request["chain"]["final_artefact_hash"], "relationship_type": "derived_from"}]
        result = self.score(request)
        self.assertEqual(result["execution_status"], "error")
        self.assertIn("cycle", result["error"])

    def test_missing_final_node_rejected_before_library_merger(self):
        request = copy.deepcopy(self.template)
        request["chain"]["final_artefact_hash"] = "a" * 64
        self.assertIn("does not refer", self.score(request)["error"])

    def test_duplicate_nodes_are_not_silently_combined(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"].append(request["chain"]["artefacts"][0])
        self.assertIn("Duplicate", self.score(request)["error"])

    def test_parallel_edges_are_not_silently_overwritten(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"][-1]["evidence"] *= 2
        self.assertIn("overwritten", self.score(request)["error"])

    def test_missing_reference_rejected(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"][-1]["evidence"][0]["hash"] = "b" * 64
        self.assertIn("undeclared", self.score(request)["error"])

    def test_invalid_relationship_roles_are_isolated_to_that_relationship(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"][-1]["artefact_type"] = "audio/stem"
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertEqual(result["coverage"]["skipped_relationships"], 1)
        self.assertTrue(any(item["code"] == "RELATIONSHIP_ENDPOINTS_SKIPPED"
                            for item in result["findings"]))
        self.assertTrue(all(axis["availability"] != "available"
                            for axis in result["axes"].values()))

    def test_unconnected_nodes_cannot_inflate_completeness(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"].append({"artefact_hash": "c" * 64, "artefact_type": "audio"})
        self.assertIn("connected", self.score(request)["error"])

    def test_unknown_nonfinal_artefact_is_quarantined_and_other_files_are_checked(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"][1]["artefact_type"] = "audio/typo-stemm"
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["submitted_artefacts"], 4)
        self.assertEqual(result["coverage"]["evaluated_artefacts"], 3)
        self.assertEqual(result["unknown_inputs"], [{
            "kind": "artefact_type", "value": "audio/typo-stemm", "action": "skipped",
            "artefact_hash": request["chain"]["artefacts"][1]["artefact_hash"],
        }])
        self.assertEqual(sum(item["code"] == "WAV_OBSERVATION"
                             for item in result["findings"]), 3)
        self.assertTrue(all(axis["availability"] != "available"
                            for axis in result["axes"].values()))

    def test_unknown_relationship_is_skipped_without_inference(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"][1]["evidence"][0]["relationship_type"] = "stemmed-form"
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertEqual(result["coverage"]["evaluated_artefacts"], 4)
        self.assertEqual(result["coverage"]["evaluated_relationships"], 2)
        self.assertEqual(result["unknown_inputs"][0]["value"], "stemmed-form")
        self.assertFalse(any(item.get("relationship") == "stemmed_from"
                             for item in result["findings"]))

    def test_unknown_final_artefact_type_still_cannot_form_evaluation_root(self):
        request = copy.deepcopy(self.template)
        request["chain"]["artefacts"][-1]["artefact_type"] = "audio/typo-master"
        result = self.score(request)
        self.assertEqual(result["execution_status"], "unsupported")
        self.assertIn("no evaluation root", result["error"])

    def test_path_traversal_rejected(self):
        request = copy.deepcopy(self.template)
        digest = request["chain"]["final_artefact_hash"]
        request["objects"][digest] = "../outside.wav"
        self.assertIn("Schema validation", self.score(request)["error"])

    def test_symlink_escape_rejected_even_with_valid_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "objects").mkdir()
            for relative in self.template["objects"].values():
                (root / relative).symlink_to(self.folder / relative)
            self.assertIn("escapes", self.score(folder=root)["error"])

    def test_truncated_riff_not_accepted_as_valid_audio(self):
        source = self.folder / next(iter(self.template["objects"].values()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truncated.wav"
            path.write_bytes(source.read_bytes()[:-8])
            with self.assertRaises(InputError):
                strict_pcm_check(path)

    def test_declared_chunk_size_cannot_hide_truncation(self):
        source = self.folder / next(iter(self.template["objects"].values()))
        data = bytearray(source.read_bytes())
        struct.pack_into("<I", data, 40, len(data) * 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunk-overflow.wav"
            path.write_bytes(data)
            with self.assertRaises(InputError):
                strict_pcm_check(path)

    def test_csec_png_returns_unsupported_with_null_axes(self):
        folder = BASE / "examples/unsupported-png"
        request = json.loads((folder / "request.json").read_text())
        result = run.evaluate(request, folder, "csec")
        validate(result, "scoring-response.schema.json")
        self.assertEqual(result["execution_status"], "unsupported")
        self.assertTrue(all(axis["value"] is None for axis in result["axes"].values()))

    def test_csec_wav_mapping_does_not_invent_music_roles(self):
        digest = self.template["chain"]["final_artefact_hash"]
        bundle = {"case_id": "LOCAL-SINGLE-WAV", "final-artefact": digest,
                  "artefacts": [{"hash": digest, "hash-method": "sha256", "type": "audio/wav", "evidence": []}]}
        request = run.public_bundle_request(bundle)
        result = run.evaluate(request, self.folder, "csec")
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertEqual(result["axes"]["completeness"]["availability"], "partial")
        self.assertFalse(any(f["code"] == "RELATIONSHIP_INTEGRITY_OBSERVATION"
                             for f in result["findings"]))

    def test_csec_unknown_relationship_is_reported_while_wav_files_are_checked(self):
        source = self.template["chain"]["artefacts"][0]["artefact_hash"]
        target = self.template["chain"]["final_artefact_hash"]
        bundle = {"case_id": "LOCAL-CSEC-UNKNOWN-RELATIONSHIP", "final-artefact": target,
                  "artefacts": [
                      {"hash": source, "hash-method": "sha256", "type": "audio/wav",
                       "evidence": []},
                      {"hash": target, "hash-method": "sha256", "type": "audio/wav",
                       "evidence": [{"hash": source, "hash-method": "sha256",
                                     "relationship": "draft-of.typo"}]},
                  ]}
        request = run.public_bundle_request(bundle)
        result = run.evaluate(request, self.folder, "csec")
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertEqual(result["coverage"]["evaluated_artefacts"], 2)
        self.assertEqual(result["coverage"]["evaluated_relationships"], 0)
        self.assertEqual(result["unknown_inputs"][0]["value"], "draft-of.typo")
        self.assertEqual(sum(item["code"] == "WAV_OBSERVATION"
                             for item in result["findings"]), 2)

    def test_timeout_terminates_an_actual_sleeping_worker(self):
        request = copy.deepcopy(self.template)
        request["options"]["timeout_seconds"] = 1
        result = run.run_isolated(request, self.folder, "native",
                                  [sys.executable, "-c", "import time; time.sleep(5)"])
        self.assertEqual(result["execution_status"], "timeout")
        self.assertLess(result["processing_ms"], 4000)

    def test_requested_timeout_is_not_capped_at_sixty_seconds(self):
        self.assertEqual(run.effective_timeout({"timeout_seconds": 120}), 120)
        self.assertEqual(run.effective_timeout({"timeout_seconds": 5}), 5)
        self.assertEqual(run.effective_timeout({}), run.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(run.effective_timeout({"timeout_seconds": 0}),
                         run.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(run.effective_timeout({"timeout_seconds": 10 ** 12}),
                         run.MAX_WAIT_SECONDS)
        request = copy.deepcopy(self.template)
        request["options"]["timeout_seconds"] = 90
        with mock.patch.object(run.subprocess, "run",
                               side_effect=run.subprocess.TimeoutExpired("worker", 90)) as worker:
            result = run.run_isolated(request, self.folder, "native")
        self.assertEqual(worker.call_args.kwargs["timeout"], 90)
        self.assertEqual(result["execution_status"], "timeout")
        self.assertIn("90-second", result["error"])

    def test_cli_worker_returns_contract_valid_result(self):
        result = run.run_isolated(self.template, self.folder, "native")
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        validate(result, "scoring-response.schema.json")

    def test_no_reasoning_option(self):
        request = copy.deepcopy(self.template)
        request["options"]["include_reasoning"] = False
        self.assertTrue(all(x["reasoning"] is None for x in self.score(request)["axes"].values()))


class RestoredXmlHelperTests(unittest.TestCase):
    def test_rin_and_ern_extract_synthetic_xml_without_claiming_xsd_compliance(self):
        folder = BASE / "legacy_parsers"
        sys.path.insert(0, str(folder))
        import rin_parser
        import ern_parser
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xml"
            path.write_text('<R xmlns="urn:synthetic-test"><Session><SessionId>S1</SessionId>'
                            '<Party><Name>Fixture Contributor</Name></Party></Session>'
                            '<RecordingComponent><ComponentId>C1</ComponentId></RecordingComponent>'
                            '<Release><ReleaseReference>R1</ReleaseReference><TitleText>Fixture</TitleText></Release></R>')
            self.assertEqual(rin_parser.parse_rin_file(str(path)).sessions[0].session_id, "S1")
            result = ern_parser.parse_ern_file(str(path))
            self.assertEqual(result.releases[0].title, "Fixture")
            self.assertIsNone(result.contains_ai_declared)
            self.assertTrue(result.warnings)

    def test_pinned_ddex_schemas_compile_offline(self):
        self.assertEqual(_schema(REGISTRY["rin"]["schema"]).target_namespace,
                         REGISTRY["rin"]["namespace"])
        self.assertEqual(_schema(REGISTRY["ern"]["schema"]).target_namespace,
                         REGISTRY["ern"]["namespace"])

    def test_rin_and_ern_extract_audio_hash_bindings_even_when_xsd_is_invalid(self):
        audio_hash = "a" * 64
        rin = f'''<RecordingInformationNotification xmlns="http://ddex.net/xml/rin/21" SchemaVersionId="2.1">
          <RecordingComponent><RecordingComponentReference>K1</RecordingComponentReference>
            <Title>Stem</Title><RecordingComponentFileReference>F1</RecordingComponentFileReference>
          </RecordingComponent><File><FileReference>F1</FileReference><FileType>AudioFile</FileType>
            <URI>objects/stem.wav</URI><HashSum><Algorithm><HashSumAlgorithmType>SHA256</HashSumAlgorithmType></Algorithm>
            <HashSumValue>{audio_hash}</HashSumValue></HashSum></File>
        </RecordingInformationNotification>'''
        ern = f'''<NewReleaseMessage xmlns="http://ddex.net/xml/ern/43">
          <Release><ReleaseReference>R1</ReleaseReference><TitleText>Release</TitleText></Release>
          <SoundRecording><ResourceReference>A1</ResourceReference><DisplayTitleText>Track</DisplayTitleText>
            <SoundRecordingEdition><ResourceId><ISRC>AUBBB2600001</ISRC></ResourceId>
              <TechnicalDetails><DeliveryFile><File><URI>objects/track.wav</URI>
                <HashSum><Algorithm><HashSumAlgorithmType>SHA256</HashSumAlgorithmType></Algorithm>
                <HashSumValue>{audio_hash}</HashSumValue></HashSum>
              </File></DeliveryFile></TechnicalDetails></SoundRecordingEdition>
          </SoundRecording></NewReleaseMessage>'''
        with tempfile.TemporaryDirectory() as directory:
            rin_path, ern_path = Path(directory) / "rin.xml", Path(directory) / "ern.xml"
            rin_path.write_text(rin)
            ern_path.write_text(ern)
            rin_result, ern_result = parse_rin_file(str(rin_path)), parse_ern_file(str(ern_path))
        self.assertEqual(rin_result.audio_bindings[0]["sha256"], audio_hash)
        self.assertTrue(rin_result.audio_bindings[0]["resolved"])
        self.assertEqual(ern_result.audio_bindings[0]["sha256"], audio_hash)
        self.assertEqual(ern_result.audio_bindings[0]["isrc"], "AUBBB2600001")
        self.assertEqual(rin_result.xsd_validation["status"], "invalid")
        self.assertEqual(ern_result.xsd_validation["status"], "invalid")

    def test_cross_evidence_checks_c2pa_ingredient_and_ai_claim(self):
        source, target = "1" * 64, "2" * 64
        native = {"artefacts": [
            {"artefact_hash": source, "artefact_type": "audio/stem",
             "attributes": {"creation_method": "recorded"}, "evidence": []},
            {"artefact_hash": target, "artefact_type": "audio/mix",
             "attributes": {"creation_method": ["recorded", "ai-generated"]},
             "evidence": [{"hash": source, "relationship_type": "mixed_from", "attributes": {}}]},
        ]}
        observations = {target: {"parser": "wav", "data": {}, "c2pa": {
            "present": True,
            "ingredients": [{"relationship": "componentOf", "referenced_hashes": [source]}],
            "digital_source_types": ["http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"],
        }}}
        _, checks, _ = ExpandedCrossEvidencePass.evaluate(
            native, {"modifiers": ["includes_ai_generated_audio"]}, observations, {})
        selected = [item for item in checks if item["rule"].startswith("c2pa_") or
                    item["rule"].startswith("workflow_ai_")]
        self.assertTrue(selected)
        self.assertTrue(all(item["passed"] for item in selected))

    def test_cross_evidence_bad_free_field_types_are_locally_unavailable(self):
        midi_hash, audio_hash, ern_hash = "1" * 64, "2" * 64, "3" * 64
        native = {"final_artefact_hash": audio_hash, "artefacts": [
            {"artefact_hash": midi_hash, "artefact_type": "project/midi",
             "attributes": {"production": {"tempo_bpm": "120 bpm"}}, "evidence": []},
            {"artefact_hash": audio_hash, "artefact_type": "audio/mix",
             "attributes": {"source_metadata": {"isrc": 12345}},
             "evidence": [{"hash": midi_hash, "relationship_type": "rendered_from",
                           "attributes": {}}]},
            {"artefact_hash": ern_hash, "artefact_type": "metadata/ddex-ern",
             "attributes": {}, "evidence": []},
        ]}
        observations = {
            midi_hash: {"parser": "midi", "data": {
                "note_on_count": 1, "duration_seconds": 0.5,
                "tempo_events": [{"bpm": 120.0}], "time_signature_events": []}},
            ern_hash: {"parser": "ern", "data": {
                "xsd_validation": {"status": "not_assessed"}, "parties": [],
                "audio_bindings": [{"sha256": audio_hash,
                                    "isrc": "AUBBB2600001"}]}},
        }
        value, checks, findings = ExpandedCrossEvidencePass.evaluate(
            native, {}, observations,
            {audio_hash: {"observed": {"duration_seconds": 1.0}}})
        unavailable = {item["rule"] for item in checks
                       if item.get("status") == "unavailable"}
        self.assertIn("midi_tempo_matches_declaration", unavailable)
        self.assertIn("ern_isrc_matches_audio_declaration", unavailable)
        self.assertTrue(any(item["rule"] == "midi_render_relationship_declared" and
                            item["passed"] for item in checks))
        self.assertIsNotNone(value)
        self.assertTrue(any(item["code"] == "CROSS_EVIDENCE_CHECK_UNAVAILABLE"
                            for item in findings))

    def test_standalone_c2pa_ingredients_are_checked_against_bound_asset(self):
        source_hash, c2pa_hash, final_hash = "1" * 64, "2" * 64, "3" * 64
        native = {"final_artefact_hash": final_hash, "artefacts": [
            {"artefact_hash": source_hash, "artefact_type": "audio/stem",
             "attributes": {}, "evidence": []},
            {"artefact_hash": c2pa_hash, "artefact_type": "provenance/c2pa",
             "attributes": {}, "evidence": []},
            {"artefact_hash": final_hash, "artefact_type": "audio/mix",
             "attributes": {}, "evidence": [
                 {"hash": source_hash, "relationship_type": "mixed_from",
                  "attributes": {}},
                 {"hash": c2pa_hash, "relationship_type": "input_to",
                  "attributes": {}},
             ]},
        ]}
        observations = {c2pa_hash: {"parser": "c2pa", "data": {
            "sdk_available": True, "reader_succeeded": True, "present": True,
            "ingredients": [{"relationship": "componentOf",
                             "referenced_hashes": [source_hash]}],
            "digital_source_types": [], "signature_info": {},
            "validation_state": "Valid", "validation_results": {}}}}
        _, checks, _ = ExpandedCrossEvidencePass.evaluate(
            native, {}, observations, {})
        selected = [item for item in checks
                    if item["rule"].startswith("c2pa_")]
        self.assertTrue(selected)
        self.assertTrue(all(item.get("passed") is True for item in selected))
        self.assertTrue(all(item["manifest_container_hash"] == c2pa_hash
                            for item in selected))
        self.assertTrue(all(item["asset_hash"] == final_hash for item in selected))

    def test_attestation_axis_uses_final_artefact_not_strongest_supporting_node(self):
        source_hash, final_hash = "1" * 64, "2" * 64
        native = {"final_artefact_hash": final_hash, "artefacts": [
            {"artefact_hash": source_hash, "artefact_type": "audio/raw-take",
             "attributes": {}, "evidence": []},
            {"artefact_hash": final_hash, "artefact_type": "audio/mix",
             "attributes": {}, "evidence": [
                 {"hash": source_hash, "relationship_type": "mixed_from",
                  "attributes": {}}]},
        ]}
        trusted = {"sdk_available": True, "reader_succeeded": True,
                   "present": True, "signature_info": {"issuer": "fixture"},
                   "validation_state": "Trusted", "validation_results": {}}
        absent = {"sdk_available": True, "reader_succeeded": True,
                  "present": False, "signature_info": None,
                  "validation_state": None, "validation_results": {}}
        observations = {
            source_hash: {"parser": "wav", "data": {}, "c2pa": trusted},
            final_hash: {"parser": "wav", "data": {}, "c2pa": absent},
        }
        axis, validation, _, records = C2PAAttestationPass.evaluate(
            native, observations)
        self.assertEqual(axis["value"], 0.0)
        self.assertEqual(validation["validation_state"], "manifest_absent")
        by_asset = {item["asset_hash"]: item for item in records}
        self.assertEqual(by_asset[source_hash]["scope"], "supporting")
        self.assertEqual(by_asset[source_hash]["validation_state"], "Trusted")
        self.assertEqual(by_asset[final_hash]["scope"], "final")
        self.assertEqual(by_asset[final_hash]["validation_state"], "manifest_absent")


class AntiAliasedResamplingTests(unittest.TestCase):
    """K1: _resample_series/_mono_resample must suppress aliasing from an
    above-Nyquist signal while preserving below-Nyquist content, handle
    short/edge-case inputs without error, and leave the no-downsampling
    path exactly as it was."""

    @staticmethod
    def _tone(freq_hz, count, rate, amplitude=6000):
        return [amplitude * math.sin(2 * math.pi * freq_hz * index / rate)
                for index in range(count)]

    @staticmethod
    def _nearest_neighbor_resample(samples, rate, output_rate):
        """The exact pre-K1 behaviour, reimplemented locally as the
        baseline to compare against -- not calling removed code."""
        count = int(len(samples) / rate * output_rate)
        return [samples[min(len(samples) - 1, round(index * rate / output_rate))]
                for index in range(count)]

    @staticmethod
    def _rms(samples):
        return math.sqrt(sum(value * value for value in samples) / len(samples)) if len(samples) else 0.0

    def test_above_nyquist_tone_is_substantially_suppressed(self):
        rate, output_rate = 8000, 200  # Nyquist at output_rate is 100 Hz
        above = self._tone(150, 4000, rate)  # 150 Hz: above the 100 Hz target Nyquist
        aliased = self._nearest_neighbor_resample(above, rate, output_rate)
        filtered = _resample_series(above, rate, output_rate)
        self.assertEqual(len(filtered), len(aliased))
        aliased_rms, filtered_rms = self._rms(aliased), self._rms(filtered)
        self.assertGreater(aliased_rms, 0.0)
        # The old nearest-neighbour path aliases this tone's full energy
        # straight through; the corrected path must suppress most of it.
        self.assertLess(filtered_rms / aliased_rms, 0.5)

    def test_below_nyquist_tone_is_preserved_in_amplitude_and_timing(self):
        rate, output_rate = 8000, 200  # Nyquist at output_rate is 100 Hz
        below = self._tone(20, 4000, rate)  # 20 Hz: comfortably below Nyquist
        unfiltered = self._nearest_neighbor_resample(below, rate, output_rate)
        filtered = _resample_series(below, rate, output_rate)
        self.assertEqual(len(filtered), len(unfiltered))
        unfiltered_rms, filtered_rms = self._rms(unfiltered), self._rms(filtered)
        # Amplitude should survive filtering reasonably intact.
        self.assertGreater(filtered_rms / unfiltered_rms, 0.6)
        # Timing/phase should also survive: the filtered and unfiltered
        # versions of the same slow tone must still correlate strongly.
        count = len(filtered)
        mean_f = sum(filtered) / count
        mean_u = sum(unfiltered) / count
        numerator = sum((f - mean_f) * (u - mean_u) for f, u in zip(filtered, unfiltered))
        energy_f = sum((f - mean_f) ** 2 for f in filtered)
        energy_u = sum((u - mean_u) ** 2 for u in unfiltered)
        correlation = numerator / math.sqrt(energy_f * energy_u)
        self.assertGreater(correlation, 0.9)

    def test_non_integer_2100_to_400_resample_filters_before_interpolation(self):
        rate, output_rate = 2100, 400
        above = self._tone(600, rate * 2, rate, amplitude=1.0)
        unfiltered_times = np.arange(int(len(above) / rate * output_rate)) / output_rate
        aliased = np.interp(unfiltered_times, np.arange(len(above)) / rate, above)
        filtered = _resample_series(above, rate, output_rate)
        self.assertEqual(len(filtered), len(aliased))
        self.assertLess(self._rms(filtered) / self._rms(aliased), 0.2)

    def test_common_proxy_rates_use_expected_integer_phase_factors(self):
        self.assertEqual(_proxy_decimation_factor(48_000), 24)
        self.assertEqual(48_000 / _proxy_decimation_factor(48_000), 2000)
        self.assertEqual(_proxy_decimation_factor(44_100), 21)
        self.assertEqual(44_100 / _proxy_decimation_factor(44_100), 2100)

    def test_upsampling_uses_linear_interpolation_without_filtering(self):
        samples = [float(index % 17) for index in range(50)]
        actual = _resample_series(samples, 100, 400)
        expected = np.interp(np.arange(len(actual)) / 400,
                             np.arange(len(samples)) / 100, samples)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-6)

    def test_handles_empty_and_very_short_inputs_without_error(self):
        self.assertEqual(_resample_series([], 8000, 200).size, 0)
        # Shorter than even one bounded decimation stage's minimum length:
        # must fall back gracefully, not raise or return a wrong length.
        short = [1.0, 2.0, 3.0]
        result = _resample_series(short, 8000, 200)
        self.assertEqual(len(result), int(len(short) / 8000 * 200))
        # A single-sample clip must not divide by zero or crash.
        self.assertEqual(len(_resample_series([5.0], 8000, 200)), 0)

    def test_mono_resample_wraps_resample_series_consistently(self):
        audio = {"sample_rate_hz": 8000, "channels": 2,
                 "frames": np.asarray(
                     [(float(index), float(index) + 1.0) for index in range(1600)],
                     dtype=np.float32)}
        mono_samples = np.mean(audio["frames"], axis=1)
        np.testing.assert_allclose(_mono_resample(audio, 200),
                                   _resample_series(mono_samples, 8000, 200),
                                   rtol=0, atol=1e-6)



class InputValidationTests(unittest.TestCase):
    """Isolate non-final input problems; stop when the final artefact is wrong."""

    score = TargetTests.score
    _workflow_request = TargetTests._workflow_request
    _pcm16 = staticmethod(TargetTests._pcm16)

    @classmethod
    def setUpClass(cls):
        cls.folder = BASE / "examples/valid-generated"
        cls.template = json.loads((cls.folder / "request.json").read_text())

    def _copy_example_objects(self, directory):
        root = Path(directory)
        shutil.copytree(self.folder / "objects", root / "objects")
        return root

    @staticmethod
    def _flip_last_byte(path):
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(bytes(data))

    def _codes(self, result):
        return [item["code"] for item in result["findings"]]

    def test_nonfinal_hash_mismatch_isolates_only_that_object(self):
        mix = self.template["chain"]["artefacts"][2]["artefact_hash"]
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_example_objects(directory)
            self._flip_last_byte(root / self.template["objects"][mix])
            result = self.score(folder=root)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        mismatch = [item for item in result["findings"] if item["code"] == "OBJECT_HASH_MISMATCH"]
        self.assertEqual(len(mismatch), 1)
        self.assertEqual(mismatch[0]["evidence_hash"], mix)
        self.assertEqual(mismatch[0]["severity"], "high")
        self.assertIn("SCORE_AFFECTED_BY_INPUT_ERRORS", self._codes(result))
        self.assertTrue(all(axis["availability"] != "available"
                            for axis in result["axes"].values()))
        rules = {item["rule_id"]: item["status"]
                 for item in result["workflow_assessment"]["rule_results"]}
        self.assertEqual(rules["mastering.pre_master"], "not_assessed")
        self.assertEqual(result["axes"]["completeness"]["value"], 1.0)

    def test_final_hash_mismatch_stops_and_reports_only_the_final(self):
        request = copy.deepcopy(self.template)
        stem = request["chain"]["artefacts"][1]["artefact_hash"]
        upper = stem.upper()
        request["chain"]["artefacts"][1]["artefact_hash"] = upper
        request["chain"]["artefacts"][2]["evidence"][0]["hash"] = upper
        request["objects"][upper] = request["objects"].pop(stem)
        final = request["chain"]["final_artefact_hash"]
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_example_objects(directory)
            self._flip_last_byte(root / request["objects"][final])
            result = self.score(request, root)
        self.assertEqual(result["execution_status"], "error")
        self.assertIn("SHA-256 mismatch", result["error"])
        self.assertEqual(self._codes(result), ["OBJECT_HASH_MISMATCH"])
        self.assertEqual(result["findings"][0]["evidence_hash"], final)

    def test_uppercase_nonfinal_hash_is_reported_not_corrected(self):
        request = copy.deepcopy(self.template)
        stem = request["chain"]["artefacts"][1]["artefact_hash"]
        upper = stem.upper()
        request["chain"]["artefacts"][1]["artefact_hash"] = upper
        request["chain"]["artefacts"][2]["evidence"][0]["hash"] = upper
        request["objects"][upper] = request["objects"].pop(stem)
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        issue = next(item for item in result["findings"] if item["code"] == "INVALID_HASH_FORMAT")
        self.assertEqual(issue["submitted_hash"], upper)
        self.assertNotIn("evidence_hash", issue)
        self.assertEqual(issue["location"], "chain/artefacts/1/artefact_hash")
        self.assertNotIn("INVALID_OBJECT_PATH", self._codes(result))
        self.assertFalse(any(item.get("evidence_hash") == stem for item in result["findings"]
                             if item["code"] == "WAV_OBSERVATION"))

    def test_final_object_key_case_or_extension_error_stops(self):
        final = self.template["chain"]["final_artefact_hash"]
        request = copy.deepcopy(self.template)
        request["objects"][final.upper()] = request["objects"].pop(final)
        result = self.score(request)
        self.assertEqual(result["execution_status"], "error")
        self.assertEqual(self._codes(result), ["INVALID_HASH_FORMAT"])
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_example_objects(directory)
            request = copy.deepcopy(self.template)
            (root / request["objects"][final]).rename(root / f"objects/{final}.WAV")
            request["objects"][final] = f"objects/{final}.WAV"
            result = self.score(request, root)
        self.assertEqual(result["execution_status"], "error")
        self.assertEqual(self._codes(result), ["INVALID_OBJECT_PATH"])
        self.assertIn("'.wav'", result["findings"][0]["suggestion"])

    def test_uppercase_nonfinal_extension_isolates_that_object(self):
        raw = self.template["chain"]["artefacts"][0]["artefact_hash"]
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_example_objects(directory)
            request = copy.deepcopy(self.template)
            (root / request["objects"][raw]).rename(root / f"objects/{raw}.WAV")
            request["objects"][raw] = f"objects/{raw}.WAV"
            result = self.score(request, root)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        issue = next(item for item in result["findings"] if item["code"] == "INVALID_OBJECT_PATH")
        self.assertEqual(issue["evidence_hash"], raw)
        self.assertIn("SCORE_AFFECTED_BY_INPUT_ERRORS", self._codes(result))

    def test_unknown_fields_skip_only_dependent_checks_with_suggestions(self):
        request = copy.deepcopy(self.template)
        nodes = request["chain"]["artefacts"]
        nodes[2]["evidence"][0]["attributes"]["fade_out_secs"] = 1.0
        nodes[1]["attributes"]["creation_methd"] = nodes[1]["attributes"].pop("creation_method")
        nodes[3]["attributes"]["technical"]["bitdepth"] = 16
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        unknown = {item["location"]: item["suggestion"] for item in result["findings"]
                   if item["code"] == "UNKNOWN_FIELD"}
        self.assertEqual(unknown, {
            "chain/artefacts/2/evidence/0/attributes/fade_out_secs": "Did you mean 'fade_out_seconds'?",
            "chain/artefacts/1/attributes/creation_methd": "Did you mean 'creation_method'?",
            "chain/artefacts/3/attributes/technical/bitdepth": "Did you mean 'bit_depth'?",
        })
        mix_check = next(
            item for item in result["assessment_checks"]["physical_similarity"]
            if item["target_hash"] == nodes[2]["artefact_hash"])
        self.assertEqual(mix_check["status"], "unavailable")
        self.assertEqual(mix_check["reason_code"], "input_error")
        relationship = next(item for item in result["findings"]
                            if item["code"] == "RELATIONSHIP_INTEGRITY_OBSERVATION"
                            and item["target_hash"] == nodes[2]["artefact_hash"])
        self.assertEqual(relationship["status"], "unavailable")
        self.assertEqual(result["axes"]["ai_disclosure"]["value"], 1.0)
        rules = {item["rule_id"]: item["status"]
                 for item in result["workflow_assessment"]["rule_results"]}
        self.assertEqual(rules["common.creation_method_disclosure"], "not_assessed")

    def test_misspelled_fade_is_not_turned_into_a_contradiction(self):
        rng = np.random.default_rng(5)
        rate, count = 8000, 8000
        take = (np.sin(np.arange(count) * 0.07) * 9000 +
                rng.normal(0, 2500, count)).astype(int)
        fade = np.clip((count - np.arange(count)) / 4000.0, 0.0, 1.0)
        edited = (take * np.where(np.arange(count) >= 4000, fade, 1.0)).astype(int)
        attributes = {"derivation_scope": "linear_edit", "source_start_seconds": 0.0,
                      "source_end_seconds": 1.0, "target_start_seconds": 0.0,
                      "target_end_seconds": 1.0, "gain": 1.0}
        statuses = {}
        for name, field in (("correct", "fade_out_seconds"), ("typo", "fade_out_secs")):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                request = self._workflow_request(root, "take_comping_and_editing", [
                    {"type": "audio/raw-take", "data": self._pcm16(list(take), rate)},
                    {"type": "audio/edited-take", "data": self._pcm16(list(edited), rate),
                     "parents": [(0, "edited_from", {**attributes, field: 0.5})]},
                ])
                result = self.score(request, root)
            self.assertEqual(result["execution_status"], "succeeded", result["error"])
            statuses[name] = result["assessment_checks"]["physical_similarity"][0]["status"]
        self.assertIn(statuses["correct"], {"corroborated", "inconclusive"})
        self.assertEqual(statuses["typo"], "unavailable")

    def test_unused_object_mapping_is_reported_not_fatal(self):
        request = copy.deepcopy(self.template)
        request["objects"]["e" * 64] = f"objects/{'e' * 64}.wav"
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertIn("UNUSED_OBJECT_MAPPING", self._codes(result))
        self.assertNotIn("SCORE_AFFECTED_BY_INPUT_ERRORS", self._codes(result))

    def test_nonfinal_path_escape_is_isolated_without_opening_the_file(self):
        raw = self.template["chain"]["artefacts"][0]["artefact_hash"]
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = self._copy_example_objects(directory)
            target = Path(outside) / "outside.wav"
            shutil.copy(root / self.template["objects"][raw], target)
            (root / self.template["objects"][raw]).unlink()
            (root / self.template["objects"][raw]).symlink_to(target)
            result = self.score(folder=root)
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        issue = next(item for item in result["findings"] if item["code"] == "OBJECT_PATH_INVALID")
        self.assertEqual(issue["evidence_hash"], raw)
        self.assertEqual(issue["severity"], "high")

    def test_size_limits_skip_nonfinal_objects_but_stop_on_the_final(self):
        size = (self.folder / next(iter(self.template["objects"].values()))).stat().st_size
        with mock.patch.object(engine_module, "MAX_TOTAL_BYTES", size * 2):
            result = self.score()
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        self.assertEqual(self._codes(result).count("OBJECT_SKIPPED_SIZE_LIMIT"), 2)
        with mock.patch.object(engine_module, "MAX_FILE_BYTES", size - 1):
            result = self.score()
        self.assertEqual(result["execution_status"], "unsupported")
        self.assertEqual(self._codes(result), ["OBJECT_SIZE_LIMIT"])

    def test_default_size_limits_are_512_mib_and_1_gib(self):
        self.assertEqual(engine_module.MAX_FILE_BYTES, 512 * 1024 * 1024)
        self.assertEqual(engine_module.MAX_TOTAL_BYTES, 1024 * 1024 * 1024)

    def test_final_artefact_changed_during_evaluation_stops(self):
        real = engine_module._file_identity
        calls = {"count": 0}

        def changed_on_recheck(path):
            calls["count"] += 1
            size, mtime, inode = real(path)
            return (size, mtime, inode) if calls["count"] <= 4 else (size, mtime + 1, inode)

        with mock.patch.object(engine_module, "_file_identity", side_effect=changed_on_recheck):
            result = self.score()
        self.assertEqual(result["execution_status"], "error")
        self.assertEqual(self._codes(result), ["FINAL_ARTEFACT_CHANGED"])

    def _change_on_calls(self, plan):
        """Patch file identity so ``plan[path_name]`` call numbers report a change."""
        real = engine_module._file_identity
        calls = {}

        def identity(path):
            name = Path(path).name
            calls[name] = calls.get(name, 0) + 1
            size, mtime, inode = real(path)
            if calls[name] in plan.get(name, ()):
                return size, mtime + 1, inode
            return size, mtime, inode

        return mock.patch.object(engine_module, "_file_identity", side_effect=identity)

    def test_nonfinal_change_during_evaluation_reevaluates_with_it_isolated(self):
        stem = self.template["chain"]["artefacts"][1]["artefact_hash"]
        stem_file = Path(self.template["objects"][stem]).name
        with self._change_on_calls({stem_file: {2}}):   # bound, then changed at the end
            result = self.score()
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        changed = [item for item in result["findings"]
                   if item["code"] == "OBJECT_CHANGED_DURING_EVALUATION"]
        self.assertEqual([item["evidence_hash"] for item in changed], [stem])
        self.assertIn("SCORE_AFFECTED_BY_INPUT_ERRORS", self._codes(result))
        observed = {item["evidence_hash"] for item in result["findings"]
                    if item["code"] == "WAV_OBSERVATION"}
        self.assertNotIn(stem, observed)
        self.assertEqual(len(observed), 3)

    def test_objects_changing_again_after_reevaluation_return_error(self):
        nodes = self.template["chain"]["artefacts"]
        stem_file = Path(self.template["objects"][nodes[1]["artefact_hash"]]).name
        raw_file = Path(self.template["objects"][nodes[0]["artefact_hash"]]).name
        # The stem changes in the first attempt, the raw track in the second.
        with self._change_on_calls({stem_file: {2}, raw_file: {4}}):
            result = self.score()
        self.assertEqual(result["execution_status"], "error")
        self.assertEqual(self._codes(result), ["OBJECT_CHANGED_DURING_EVALUATION"] * 2)

    def test_expired_deadline_degrades_audio_checks_instead_of_timing_out(self):
        result = run.evaluate(copy.deepcopy(self.template), self.folder, "native",
                              deadline=time.time() - 1)
        validate(result, "scoring-response.schema.json")
        self.assertEqual(result["execution_status"], "succeeded", result["error"])
        statuses = {(item["status"], item.get("reason_code"))
                    for item in result["assessment_checks"]["physical_similarity"]}
        self.assertEqual(statuses, {("unavailable", "resource_budget_exhausted")})
        self.assertFalse(any("CONTRADICTION" in code for code in self._codes(result)))

    def test_worker_receives_a_deadline_before_the_timeout(self):
        request = copy.deepcopy(self.template)
        request["options"]["timeout_seconds"] = 30
        captured = {}

        def fake_worker(command, input, **kwargs):
            captured.update(json.loads(input))
            raise run.subprocess.TimeoutExpired("worker", kwargs["timeout"])

        started = time.time()
        with mock.patch.object(run.subprocess, "run", side_effect=fake_worker):
            run.run_isolated(request, self.folder, "native")
        self.assertLess(captured["deadline"], started + 30)
        self.assertGreater(captured["deadline"], started + 20)

    def test_edge_hash_case_mismatch_explains_the_dangling_reference(self):
        request = copy.deepcopy(self.template)
        raw = request["chain"]["artefacts"][0]["artefact_hash"]
        request["chain"]["artefacts"][1]["evidence"][0]["hash"] = raw.upper()
        result = self.score(request)
        self.assertEqual(result["execution_status"], "error")
        self.assertIn("undeclared", result["error"])
        self.assertIn("letter case", result["error"])

    def test_other_audio_containers_are_unsupported_by_content(self):
        cases = {"FLAC": b"fLaC" + bytes(60), "MP3": b"ID3" + bytes(61),
                 "Ogg": b"OggS" + bytes(60),
                 "AIFF": b"FORM" + (52).to_bytes(4, "big") + b"AIFF" + bytes(52)}
        with tempfile.TemporaryDirectory() as directory:
            for name, data in cases.items():
                path = Path(directory) / f"{name}.wav"
                path.write_bytes(data)
                with self.subTest(name=name), self.assertRaises(wav_io_module.UnsupportedWav) as caught:
                    inspect_wav(path)
                self.assertEqual(caught.exception.reason_code, "unsupported_container")
                self.assertIn(name, str(caught.exception))
                self.assertIn(".wav", str(caught.exception))

    def test_final_flac_named_wav_returns_unsupported(self):
        final = self.template["chain"]["final_artefact_hash"]
        data = b"fLaC" + bytes(200)
        digest = hashlib.sha256(data).hexdigest()
        request = copy.deepcopy(self.template)
        request["chain"]["final_artefact_hash"] = digest
        request["chain"]["artefacts"][3]["artefact_hash"] = digest
        del request["chain"]["artefacts"][3]["attributes"]["technical"]
        request["objects"].pop(final)
        request["objects"][digest] = f"objects/{digest}.wav"
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_example_objects(directory)
            (root / f"objects/{digest}.wav").write_bytes(data)
            result = self.score(request, root)
        self.assertEqual(result["execution_status"], "unsupported")
        self.assertIn("FLAC", result["error"])

    def test_empty_data_chunk_does_not_swallow_following_chunks(self):
        fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
        info = b"INFO" + b"INAM" + struct.pack("<I", 8) + b"My Song\0"
        body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt +
                b"data" + struct.pack("<I", 0) +
                b"LIST" + struct.pack("<I", len(info)) + info)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.wav"
            path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
            with self.assertRaises(wav_io_module.WavError) as caught:
                inspect_wav(path)
        self.assertEqual(caught.exception.reason_code, "empty_audio_data")

    def test_decode_blocks_are_capped_by_bytes(self):
        samples = [int(1000 * math.sin(index / 7)) for index in range(4000)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capped.wav"
            path.write_bytes(self._pcm16(samples))
            descriptor = inspect_wav(path)
            whole = np.concatenate([block for _, block in read_frames(path, descriptor)])
            with mock.patch.object(wav_io_module, "MAX_DECODE_BLOCK_BYTES", 1000):
                blocks = list(read_frames(path, descriptor, block_frames=4000))
        self.assertTrue(all(len(block) <= 500 for _, block in blocks))
        np.testing.assert_array_equal(np.concatenate([block for _, block in blocks]), whole)

    def test_reading_a_file_changed_after_inspection_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changing.wav"
            path.write_bytes(self._pcm16([0] * 800))
            descriptor = inspect_wav(path)
            stat = path.stat()
            import os
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            with self.assertRaises(wav_io_module.WavError) as caught:
                list(read_frames(path, descriptor))
        self.assertEqual(caught.exception.reason_code, "file_changed_after_inspection")

if __name__ == "__main__":
    unittest.main()
