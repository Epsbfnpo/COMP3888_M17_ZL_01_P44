import copy
import hashlib
import importlib.util
import json
import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import run
from music_target.contract import source_revision, validate
from music_target.engine import (
    InputError,
    RelationshipIntegrityPass,
    strict_pcm_check,
)
from libevchain.pipeline import EvidencePass
from music_target.assessment_passes import C2PAAttestationPass, ExpandedCrossEvidencePass
from legacy_parsers.common.ddex_validation import REGISTRY, _schema
from legacy_parsers.ern_parser import parse_ern_file
from legacy_parsers.rin_parser import parse_rin_file
from music_target.workflow_policy import WorkflowCompletenessPass, load_workflow_policy
from music_target.midi_parser import parse_midi_file
from music_target.sheet_parsers import parse_comp_sheet_file, parse_cue_sheet_file
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
        self.assertIsNone(result["axes"]["integrity"]["value"])
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
        del request["objects"][request["chain"]["artefacts"][0]["artefact_hash"]]
        result = self.score(request)
        self.assertEqual(result["execution_status"], "succeeded")
        self.assertEqual(result["axes"]["completeness"]["value"], 1.0)
        self.assertTrue(any(f["code"] == "MISSING_OBJECT" for f in result["findings"]))
        self.assertNotIn("decision", result)

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
                {"type": "audio/stem", "data": self._float32_wav()},
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
                            for item in result["assessment_checks"]["source_contribution"]))

    def test_unsupported_final_wav_still_returns_unsupported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._workflow_request(root, "mastering_service_only", [
                {"type": "audio/master", "data": self._float32_wav()},
            ])
            result = self.score(request, root)
        self.assertEqual(result["execution_status"], "unsupported")
        self.assertIn("integer PCM", result["error"])

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

    def test_audio_derivation_pass_matches_declared_linear_mix(self):
        left = [int(5000 * math.sin(index / 17)) for index in range(2000)]
        right = [int(3500 * math.cos(index / 23)) for index in range(2000)]
        target = [round(0.6 * a + 0.4 * b) for a, b in zip(left, right)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [{"type": "audio/stem", "data": self._pcm16(left)},
                     {"type": "audio/stem", "data": self._pcm16(right)},
                     {"type": "audio/mix", "data": self._pcm16(target),
                      "parents": [(0, "mixed_from", self._linear_attributes(
                          "linear_mix", 0.25, 0.6)),
                                  (1, "mixed_from", self._linear_attributes(
                                      "linear_mix", 0.25, 0.4))]}]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        check = result["assessment_checks"]["audio_derivation"][0]
        self.assertEqual(check["status"], "matched")
        self.assertLess(check["normalized_rmse"], 0.02)
        self.assertEqual(check["coverage_ratio"], 1.0)
        self.assertGreater(check["matched_coverage_ratio"], 0.99)
        self.assertIn("validation_normalized_rmse", check)
        relationship_checks = result["assessment_checks"]["relationship_integrity"]
        self.assertEqual(len(relationship_checks), 6)
        self.assertTrue(all(item["source_pass"] == "RelationshipIntegrityPass"
                            and item["passed"] for item in relationship_checks))

    def test_audio_derivation_requires_sufficient_target_coverage(self):
        source = self._deterministic_samples(2000, 17)
        target = source + self._deterministic_samples(2000, 18)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(source)},
                {"type": "audio/mix", "data": self._pcm16(target),
                 "parents": [(0, "mixed_from", self._linear_attributes(
                     "linear_mix", 0.25, 1.0))]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        check = result["assessment_checks"]["audio_derivation"][0]
        self.assertEqual(check["status"], "not_applicable")
        self.assertLess(check["coverage_ratio"], 0.9)
        self.assertIn("insufficient_target_coverage", check["manual_review_reasons"])

    def test_audio_derivation_rejects_redundant_correlated_sources(self):
        first = self._deterministic_samples(2400, 23)
        second = first.copy()
        second[0] += 1
        target = [round(0.4 * left + 0.4 * right)
                  for left, right in zip(first, second)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [
                {"type": "audio/stem", "data": self._pcm16(first)},
                {"type": "audio/stem", "data": self._pcm16(second)},
                {"type": "audio/mix", "data": self._pcm16(target),
                 "parents": [(0, "mixed_from", self._linear_attributes(
                     "linear_mix", 0.3, 0.4)),
                             (1, "mixed_from", self._linear_attributes(
                                 "linear_mix", 0.3, 0.4))]},
            ]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        check = result["assessment_checks"]["audio_derivation"][0]
        self.assertEqual(check["status"], "not_applicable")
        self.assertGreaterEqual(check["max_source_correlation"], 0.995)
        self.assertIn("redundant_or_highly_correlated_sources",
                      check["manual_review_reasons"])

    def test_audio_derivation_pass_contradicts_unrelated_declared_linear_mix(self):
        source = [int(5000 * math.sin(index / 17)) for index in range(2000)]
        target = [int(5000 * math.sin(index / 3.7)) for index in range(2000)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            specs = [{"type": "audio/stem", "data": self._pcm16(source)},
                     {"type": "audio/mix", "data": self._pcm16(target),
                      "parents": [(0, "mixed_from", self._linear_attributes(
                          "linear_mix", 0.25, 1.0))]}]
            result = self.score(self._workflow_request(
                root, "multitrack_recording_mix_master", specs), root)
        self.assertEqual(result["assessment_checks"]["audio_derivation"][0]["status"],
                         "contradicted")
        self.assertTrue(any(item["code"] == "AUDIO_DERIVATION_CONTRADICTION"
                            for item in result["findings"]))

    def test_audio_derivation_pass_applies_declared_time_offset(self):
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

    def test_audio_derivation_pass_does_not_assess_unscoped_mix(self):
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

    def test_audio_derivation_does_not_infer_or_refit_gain(self):
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

    def test_edit_derivation_pass_matches_declared_crop_and_gain(self):
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

    def test_edit_derivation_flags_repeated_source_segment_as_ambiguous(self):
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

    def test_edit_derivation_pass_matches_splice_offsets_and_declared_fades(self):
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

    def test_stem_derivation_pass_matches_declared_linear_stem(self):
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

    def test_comp_derivation_pass_reconstructs_comp_sheet_timeline(self):
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

    def test_master_derivation_pass_corroborates_content_continuity(self):
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

    def test_master_derivation_does_not_infer_missing_alignment(self):
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

    def test_master_derivation_does_not_replace_declared_alignment(self):
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

    def test_master_derivation_handles_mild_processing_and_sample_rate_conversion(self):
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

    def test_audio_alignment_pass_estimates_an_undeclared_shift(self):
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

    def test_source_contribution_and_decoy_passes_find_unused_source(self):
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

    def test_processed_audio_match_corroborates_light_processing(self):
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

    def test_stem_mix_residual_pass_estimates_source_gain(self):
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

    def test_excerpted_from_pass_finds_sample_in_recording(self):
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

    def test_mastering_derivation_public_pass_name_is_exposed(self):
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

    def test_integrity_layers_do_not_score_endpoint_validity_as_content(self):
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


if __name__ == "__main__":
    unittest.main()
