"""Declared format conversions, lossless sample identity and per-file format capabilities."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

BASE = Path(__file__).resolve().parents[1]
for path in (BASE, BASE / "vendor", Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))

import run  # noqa: E402
import soundfile  # noqa: E402
from music_target import sample_identity  # noqa: E402
from music_target.flac_io import flac_sample_digest, inspect_flac  # noqa: E402
from music_target.wav_io import inspect_wav  # noqa: E402
from test_audio_formats import RATE, rebuild_flac, _flac_blocks, tone, write_mp3  # noqa: E402
from test_production_documents import TAKE, recorder_wav, store  # noqa: E402

CONVERSION = {"transformation": "format_conversion"}


def integer(samples, bits):
    """Integer samples at ``bits``: libsndfile quantises float input differently for WAV and FLAC."""
    if bits <= 16:
        return (np.round(samples * 2 ** 7) * 2 ** 8).astype(np.int16) if bits == 8 else \
            np.round(samples * 2 ** 15).astype(np.int16)
    return (np.round(samples * 2 ** 23) * 2 ** 8).astype(np.int32)


class SampleDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def digests(self, wav_subtype, flac_subtype, samples=None):
        bits = int(flac_subtype.split("_")[1].lstrip("S"))
        samples = integer(tone() if samples is None else samples, bits)
        wav, flac = self.folder / f"{wav_subtype}.wav", self.folder / f"{flac_subtype}.flac"
        soundfile.write(str(wav), samples, RATE, subtype=wav_subtype)
        soundfile.write(str(flac), samples, RATE, subtype=flac_subtype)
        return (sample_identity.wav_sample_digest(wav, inspect_wav(wav)),
                flac_sample_digest(flac, inspect_flac(flac)))

    def test_wav_and_flac_of_the_same_samples_have_one_digest(self):
        # 8-bit WAV is unsigned and FLAC signed; both reduce to the same samples.
        for wav_subtype, flac_subtype in (("PCM_16", "PCM_16"), ("PCM_24", "PCM_24"), ("PCM_U8", "PCM_S8")):
            wav, flac = self.digests(wav_subtype, flac_subtype)
            self.assertEqual(sample_identity.compare(wav, flac)["status"], "identical", wav_subtype)

    def test_a_changed_sample_changes_the_digest(self):
        samples = tone()
        changed = samples.copy()
        changed[1000, 0] += 0.01
        wav = self.digests("PCM_16", "PCM_16", samples)[0]
        flac = self.digests("PCM_16", "PCM_16", changed)[1]
        self.assertEqual(sample_identity.compare(wav, flac)["status"], "different")

    def test_float_wav_is_not_digested(self):
        wav = self.folder / "float.wav"
        soundfile.write(str(wav), tone(), RATE, subtype="FLOAT")
        self.assertEqual(sample_identity.wav_sample_digest(wav, inspect_wav(wav))["reason_code"], "float_samples")


class _ConversionHarness:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / "objects").mkdir()

    def store_audio(self, samples, suffix, rate=RATE, **kwargs):
        path = self.folder / f"scratch.{suffix}"
        if suffix == "mp3":
            write_mp3(path, samples)
        else:
            if samples.dtype.kind == "f":
                samples = integer(samples, int(kwargs.get("subtype", "PCM_24").split("_")[1]))
            soundfile.write(str(path), samples, rate, **kwargs)
        return store(self.folder, path.read_bytes(), suffix)

    def evaluate(self, nodes, final, workflow="mastering_service_only"):
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["workflow"].update(workflow_id=workflow, modifiers=[])
        artefacts, objects = [], {}
        for digest, kind, suffix, evidence in nodes:
            artefacts.append({"artefact_hash": digest, "artefact_type": kind, "attributes": {},
                              "evidence": [{"hash": source, "relationship_type": relation, "attributes": attributes}
                                           for source, relation, attributes in evidence]})
            objects[digest] = f"objects/{digest}.{suffix}"
        request["chain"].update(final_artefact_hash=final, artefacts=artefacts)
        request["objects"] = objects
        response = run.evaluate(copy.deepcopy(request), self.folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        return response

    def convert(self, source_suffix, target_suffix, target=None, relation="derived_from", **target_kwargs):
        samples = tone()
        source = self.store_audio(samples, source_suffix, subtype="PCM_24")
        target = target or self.store_audio(samples, target_suffix, **({"subtype": "PCM_24"} | target_kwargs)
                                            if target_suffix != "mp3" else {})
        types = ("audio/mix", "audio/master") if relation != "derived_from" else ("audio/master", "audio/master")
        response = self.evaluate([(source, types[0], source_suffix, []),
                                  (target, types[1], target_suffix, [(source, relation, CONVERSION)])], target)
        return response, (response["assessment_checks"].get("format_conversions") or [None])[0]


class ConversionEvaluationTests(_ConversionHarness, unittest.TestCase):
    def test_lossless_conversion_checks_parameters_and_finds_identical_samples(self):
        response, row = self.convert("wav", "flac")
        self.assertEqual((row["status"], row["direction"]), ("assessed", "lossless_to_lossless"))
        self.assertTrue(all(check["passed"] for check in row["checks"]))
        self.assertEqual({check["rule"] for check in row["checks"]},
                         {"format_conversion_sample_rate_preserved", "format_conversion_channels_preserved",
                          "format_conversion_bit_depth_preserved", "format_conversion_duration_preserved"})
        self.assertEqual(row["sample_identity"]["status"], "identical")
        self.assertIs(row["scoring_eligible"], False)
        # The parameter checks are declarations, scored like other relationship claims.
        scored = {check["rule"] for check in response["assessment_checks"]["relationship_integrity"]}
        self.assertIn("format_conversion_sample_rate_preserved", scored)

    def test_different_samples_are_reported_not_scored(self):
        samples = tone()
        samples[500, 1] += 0.02
        changed = self.store_audio(samples, "flac", subtype="PCM_24")
        response, row = self.convert("wav", "flac", target=changed)
        self.assertEqual(row["sample_identity"]["status"], "different")
        note = next(item for item in response["findings"] if item["code"] == "FORMAT_CONVERSION_SAMPLES_DIFFER")
        self.assertIs(note["scoring_eligible"], False)
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["contradiction_count"], 0)

    def test_a_changed_sample_rate_contradicts_the_conversion_claim(self):
        resampled = self.store_audio(tone(), "flac", rate=44100, subtype="PCM_24")
        response, row = self.convert("wav", "flac", target=resampled)
        failed = [check["rule"] for check in row["checks"] if check["passed"] is False]
        self.assertIn("format_conversion_sample_rate_preserved", failed)
        self.assertEqual(row["sample_identity"]["reason_code"], "parameters_differ")
        self.assertGreaterEqual(response["assessment_checks"]["integrity_decision"][0]["contradiction_count"], 1)

    def test_lossy_conversion_compares_the_exact_length_but_not_samples(self):
        _, row = self.convert("wav", "mp3")
        self.assertEqual(row["direction"], "lossless_to_lossy")
        self.assertEqual(row["sample_identity"]["reason_code"], "lossy_endpoint")
        duration = next(check for check in row["checks"] if check["rule"] == "format_conversion_duration_preserved")
        self.assertIs(duration["passed"], True)
        self.assertNotIn("format_conversion_bit_depth_preserved", {check["rule"] for check in row["checks"]})

    def test_conversion_on_another_relationship_type_is_ignored(self):
        response, row = self.convert("wav", "flac", relation="mastered_from")
        self.assertEqual((row["status"], row["reason_code"]), ("ignored", "requires_derived_from"))
        self.assertIn("FORMAT_CONVERSION_NOT_APPLIED", {item["code"] for item in response["findings"]})

    def test_unknown_transformation_value_is_an_input_error(self):
        source = self.store_audio(tone(), "wav", subtype="PCM_24")
        target = self.store_audio(tone(), "flac", subtype="PCM_24")
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["workflow"].update(workflow_id="mastering_service_only", modifiers=[])
        request["chain"].update(final_artefact_hash=target, artefacts=[
            {"artefact_hash": source, "artefact_type": "audio/master", "attributes": {}, "evidence": []},
            {"artefact_hash": target, "artefact_type": "audio/master", "attributes": {},
             "evidence": [{"hash": source, "relationship_type": "derived_from",
                           "attributes": {"transformation": "resampled"}}]}])
        request["objects"] = {source: f"objects/{source}.wav", target: f"objects/{target}.flac"}
        response = run.evaluate(request, self.folder, "native")
        self.assertNotEqual(response["execution_status"], "succeeded")

    def test_capabilities_advise_on_recordings_without_recorder_metadata(self):
        take = recorder_wav(self.folder, *TAKE)
        wav = self.folder / "objects" / f"{take}.wav"
        bits = int(soundfile.info(str(wav)).subtype.split("_")[1])
        samples, rate = soundfile.read(str(wav), dtype="int16" if bits == 16 else "int32")
        plain = self.store_audio(samples, "flac", rate=rate, subtype=f"PCM_{bits}")
        mp3 = self.store_audio(tone(), "mp3")
        alone = self.evaluate([(plain, "audio/raw-take", "flac", []), (mp3, "audio/raw-take", "mp3", []),
                               (take, "audio/master", "wav", [(plain, "derived_from", {}),
                                                              (mp3, "derived_from", {})])], take)
        rows = {row["hash"]: row for row in alone["assessment_checks"]["format_capabilities"]}
        self.assertEqual((rows[plain]["recorder_metadata"], rows[mp3]["recorder_metadata"], rows[take]["recorder_metadata"]),
                         ("not_kept", "not_supported_by_format", "present"))
        self.assertIn("--keep-foreign-metadata", rows[plain]["advice"][0])
        self.assertIn("MP3 cannot carry recorder metadata", rows[mp3]["advice"][0])
        self.assertEqual(rows[take]["advice"], [])
        self.assertTrue(all(row["scoring_eligible"] is False for row in rows.values()))
        # Supplying the original WAV and declaring the conversion removes the advice
        # and the samples are shown to be the same.
        paired = self.evaluate([(take, "audio/raw-take", "wav", []),
                                (plain, "audio/raw-take", "flac", [(take, "derived_from", CONVERSION)]),
                                (mp3, "audio/master", "mp3", [(plain, "derived_from", {})])], mp3)
        rows = {row["hash"]: row for row in paired["assessment_checks"]["format_capabilities"]}
        self.assertEqual(rows[plain]["advice"], [])
        self.assertEqual(paired["assessment_checks"]["format_conversions"][0]["sample_identity"]["status"],
                         "identical")


class ConversionDiagnosticsTests(_ConversionHarness, unittest.TestCase):
    """The optional audio diagnostics add an alignment and bandwidth summary per conversion."""

    def test_diagnostics_summarise_alignment_and_target_bandwidth(self):
        import time
        from music_target.audio_diagnostics.policy import DEFAULT_PATH
        from music_target.audio_diagnostics.runner import augment_response
        rng = np.random.default_rng(3)
        music = np.cumsum(rng.normal(scale=0.02, size=(RATE * 8, 2)), axis=0)
        music = 0.5 * music / np.max(np.abs(music))
        source = self.store_audio(music, "wav", subtype="PCM_24")
        target = self.store_audio(music, "flac", subtype="PCM_24")
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["workflow"].update(workflow_id="mastering_service_only", modifiers=[])
        request["chain"].update(final_artefact_hash=target, artefacts=[
            {"artefact_hash": source, "artefact_type": "audio/master", "attributes": {}, "evidence": []},
            {"artefact_hash": target, "artefact_type": "audio/master", "attributes": {},
             "evidence": [{"hash": source, "relationship_type": "derived_from", "attributes": CONVERSION}]}])
        request["objects"] = {source: f"objects/{source}.wav", target: f"objects/{target}.flac"}
        core = run.evaluate(copy.deepcopy(request), self.folder, "native")
        combined = augment_response(core, request, self.folder, "native", DEFAULT_PATH, time.monotonic() + 300)
        self.assertEqual(combined["axes"], core["axes"])
        row = combined["assessment_checks"]["audio_format_conversion"][0]
        self.assertEqual((row["source_hash"], row["target_hash"], row["direction"]),
                         (source, target, "lossless_to_lossless"))
        self.assertEqual(row["fingerprint"], "aligned_at_zero")
        self.assertIsNotNone(row["target_bandwidth"])
        self.assertIs(row["scoring_eligible"], False)


class BuilderFormatTests(_ConversionHarness, unittest.TestCase):
    """The desktop builder reports the same capabilities and checks the conversion flag."""

    def test_probe_and_advice_match_the_evaluator(self):
        import wav_probe
        take = recorder_wav(self.folder, *TAKE)
        wav = self.folder / "objects" / f"{take}.wav"
        flac = self.folder / "plain.flac"
        soundfile.write(str(flac), integer(tone(), 24), RATE, subtype="PCM_24")
        mp3 = write_mp3(self.folder / "x.mp3")
        caps = {name: wav_probe.audio_capabilities(path) for name, path in (("wav", wav), ("flac", flac), ("mp3", mp3))}
        self.assertEqual({name: item["recorder_metadata"] for name, item in caps.items()},
                         {"wav": "present", "flac": "not_kept", "mp3": "not_supported_by_format"})
        self.assertEqual(wav_probe.format_advice(caps["wav"], "audio/raw-take"), [])
        self.assertIn("--keep-foreign-metadata", wav_probe.format_advice(caps["flac"], "audio/raw-take")[0])
        # FLAC and MP3 always get the limited-scope note; recordings also the recorder advice.
        scope_only = wav_probe.format_advice(caps["flac"], "audio/raw-take", converted=True)
        self.assertEqual(len(scope_only), 1)
        self.assertIn("limited checks", scope_only[0])
        self.assertEqual(wav_probe.format_advice(caps["flac"], "audio/master"), scope_only)
        self.assertEqual(wav_probe.audio_capabilities(self.folder / "missing.wav"), {})

    def test_relationship_check_of_the_conversion_flag(self):
        import manifest_logic as L
        import relationship_modes as RM
        source = {"id": "a", "type": "audio/master", "technical": {"sample_rate_hz": 48000, "channels": 2}}
        target = {"id": "b", "type": "audio/master", "technical": {"sample_rate_hz": 44100, "channels": 2},
                  "capabilities": {"format": "FLAC", "recorder_metadata": "not_kept"}}
        rel = {"target": "b", "source": "a", "type": "derived_from", "attrs": dict(CONVERSION)}
        errors, warnings = RM.relationship_issues(rel, target, source, L.endpoint_ok)
        self.assertEqual(errors, [])
        self.assertIn("sample_rate_hz differs", warnings[0])
        bad = dict(rel, attrs={"transformation": "resampled"})
        self.assertIn("format_conversion", RM.relationship_issues(bad, target, source, L.endpoint_ok)[0][0])
        mastered = {"target": "b", "source": "a", "type": "mastered_from", "attrs": dict(CONVERSION)}
        mix = dict(source, type="audio/mix")
        self.assertIn("derived_from only", RM.relationship_issues(mastered, target, mix, L.endpoint_ok)[0][-1])
        self.assertTrue(RM.visible("derived_from", "conversion"))
        self.assertFalse(RM.visible("mixed_from", "conversion"))
        # Advice appears for a plain FLAC take until its original is declared.
        take = dict(target, type="audio/raw-take")
        self.assertTrue(RM.all_warnings([source, take], [], L.endpoint_ok))
        self.assertFalse([note for note in RM.all_warnings([source, take], [rel], L.endpoint_ok)
                          if "keep-foreign-metadata" in note])


class FormatScopeTests(_ConversionHarness, unittest.TestCase):
    """Every check family has a stated status for FLAC and MP3, so no gap is silent."""

    def test_every_family_and_technical_field_has_a_status_for_each_format(self):
        from music_target.audio_observations import FORMAT_SCOPE, LIMITATION_OF, TECHNICAL_FAMILY, _REASONS
        from music_target.contract import _known_fields
        allowed = {"implemented", "if_in_file", "not_applicable", *LIMITATION_OF}
        for family, scope in FORMAT_SCOPE.items():
            self.assertEqual(set(scope), {"WAV", "FLAC", "MP3"}, family)
            self.assertTrue(set(scope.values()) <= allowed, family)
            self.assertIn(scope["WAV"], ("implemented", "not_applicable"), family)
            for container in ("FLAC", "MP3"):
                if scope[container] not in ("implemented", "not_applicable"):
                    self.assertIn((family, container), _REASONS)
        # A technical field added to the schema must be assigned to a family.
        self.assertEqual(set(_known_fields()[1]), set(TECHNICAL_FAMILY))
        self.assertTrue(set(TECHNICAL_FAMILY.values()) <= set(FORMAT_SCOPE))

    def test_flac_lists_only_what_this_file_lacks_and_wav_lists_nothing(self):
        take = recorder_wav(self.folder, *TAKE)
        wav = self.folder / "objects" / f"{take}.wav"
        bits = int(soundfile.info(str(wav)).subtype.split("_")[1])
        samples, rate = soundfile.read(str(wav), dtype="int16" if bits == 16 else "int32")
        plain = self.store_audio(samples, "flac", rate=rate, subtype=f"PCM_{bits}")
        response = self.evaluate([(take, "audio/raw-take", "wav", []),
                                  (plain, "audio/master", "flac", [(take, "derived_from", CONVERSION)])], plain)
        rows = {row["hash"]: row for row in response["assessment_checks"]["format_capabilities"]}
        self.assertEqual((rows[take]["check_scope"], rows[take]["limited_checks"]), ("full", []))
        self.assertEqual(rows[plain]["check_scope"], "limited")
        self.assertEqual(set(rows[plain]["limited_checks"]), {"recorder_metadata", "bwf_chronology", "tag_dates"})
        limited = next(item for item in response["findings"] if item["code"] == "AUDIO_FORMAT_LIMITED_CHECKS")
        self.assertEqual(limited["evidence_hash"], plain)
        self.assertIn("designed and calibrated for WAV", limited["message"])

    def test_diagnostics_rows_on_flac_say_their_thresholds_are_wav_only(self):
        from music_target.audio_diagnostics.runner import _format_scope
        formats = {"w": "WAV", "f": "FLAC"}
        self.assertEqual(_format_scope({"source_hash": "w", "target_hash": "w"}, formats), {"format_scope": "wav"})
        mixed = _format_scope({"source_hash": "w", "target_hash": "f"}, formats)
        self.assertEqual((mixed["format_scope"], mixed["endpoint_formats"]), ("calibrated_on_wav_only", ["WAV", "FLAC"]))


if __name__ == "__main__":
    unittest.main()
