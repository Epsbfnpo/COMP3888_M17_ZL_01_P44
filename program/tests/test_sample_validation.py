"""Full-length sample check: NaN/Infinity fails, an unfinished scan is never a pass."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "vendor"))
import run  # noqa: E402
from builder_results import attention_items, axis_value_label  # noqa: E402
from music_target import sample_validation  # noqa: E402
from music_target.sample_validation import from_flac_md5, sample_check, scan_decoded, scan_wav  # noqa: E402
from music_target.wav_io import inspect_wav  # noqa: E402
# Modules, not TestCase classes: a class imported here would run again in this module.
import test_production_documents  # noqa: E402
import test_target  # noqa: E402
from test_audio_formats import write_flac, write_mp3  # noqa: E402
from test_production_documents import sound_report, store  # noqa: E402


RATE = 8000


def float_wav(folder, values, bits=32, channels=1, name="float.wav"):
    path = Path(folder) / name
    path.write_bytes(test_target.TargetTests._float_wav(values, bits=bits, channels=channels, rate=RATE))
    return path


class ScanWavTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def scan(self, path, deadline=None):
        return scan_wav(path, inspect_wav(path), deadline)

    def test_nan_is_located_by_time_and_channel(self):
        stereo = np.full((RATE, 2), 0.1)
        stereo[400, 1] = np.nan
        stereo[401, 0] = np.inf
        result = self.scan(float_wav(self.folder, stereo.reshape(-1), channels=2))
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason_code"], "non_finite_samples")
        self.assertEqual(result["non_finite_samples"], 2)
        self.assertEqual(result["first_non_finite"], {"kind": "NaN", "frame": 400, "seconds": 0.05, "channel": 2})
        self.assertEqual(result["frames_checked"], result["frame_count"])
        self.assertEqual(result["check_version"], "audio-sample-validation-v1")
        self.assertIn("NaN at 0.050 s, channel 2", result["reason"])

    def test_infinities_are_named_by_sign(self):
        for value, kind in ((np.inf, "+Infinity"), (-np.inf, "-Infinity")):
            with self.subTest(kind=kind):
                result = self.scan(float_wav(self.folder, [0.0] * 799 + [value]))
                self.assertEqual(result["first_non_finite"]["kind"], kind)
                self.assertEqual(result["first_non_finite"]["frame"], 799)

    def test_float64_is_checked_as_stored_not_after_float32_conversion(self):
        # 1e300 is a finite float64 that only overflows float32: not damage.
        result = self.scan(float_wav(self.folder, [0.0, 1e300, 0.5], bits=64))
        self.assertEqual((result["status"], result["values_checked"]), ("valid", "stored_float64"))
        result = self.scan(float_wav(self.folder, [0.0, np.nan], bits=64, name="nan64.wav"))
        self.assertEqual(result["status"], "invalid")

    def test_integer_pcm_is_read_in_full(self):
        path = self.folder / "pcm.wav"
        path.write_bytes(test_target.TargetTests._pcm16([100] * 800))
        result = self.scan(path)
        self.assertEqual((result["status"], result["values_checked"]), ("valid", "integer_pcm"))
        self.assertEqual(result["frames_checked"], 800)

    def test_time_limit_is_not_completed_never_a_pass(self):
        result = self.scan(float_wav(self.folder, [0.1] * 800), deadline=time.time() - 1)
        self.assertEqual((result["status"], result["reason_code"]), ("not_completed", "caller_deadline"))
        self.assertEqual(result["frames_checked"], 0)
        check = sample_check("d" * 64, "WAV", result)
        self.assertEqual((check["passed"], check["status"]), (None, "not_completed"))

    def test_nan_found_before_the_time_limit_still_fails(self):
        values = [np.nan] + [0.1] * 799
        path = float_wav(self.folder, values)
        # One frame per block; the clock passes the deadline after the first block.
        with mock.patch.object(sample_validation, "MAX_DECODE_BLOCK_BYTES", 4), \
                mock.patch.object(sample_validation.time, "time", side_effect=[0.0] + [10.0] * 10):
            result = self.scan(path, deadline=5.0)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["frames_checked"], 1)

    def test_file_changed_after_inspection_is_not_completed(self):
        path = float_wav(self.folder, [0.1] * 800)
        descriptor = inspect_wav(path)
        path.write_bytes(path.read_bytes() + b"\0\0")
        self.assertEqual(scan_wav(path, descriptor)["reason_code"], "file_changed_after_inspection")


class ScanDecodedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_flac_and_mp3_decode_in_full(self):
        for path in (write_flac(self.folder / "a.flac"), write_mp3(self.folder / "a.mp3")):
            with self.subTest(path=path.suffix):
                result = scan_decoded(path)
                self.assertEqual(result["status"], "valid")
                self.assertGreater(result["frames_checked"], 0)
                self.assertTrue(result["decoder"].startswith("libsndfile"))

    def test_missing_decoder_is_not_completed(self):
        with mock.patch.dict(sys.modules, {"soundfile": None}):
            result = scan_decoded(write_mp3(self.folder / "b.mp3"))
        self.assertEqual((result["status"], result["reason_code"]), ("not_completed", "decoder_unavailable"))

    def test_flac_md5_pass_outcomes(self):
        self.assertEqual(from_flac_md5({"status": "matched", "decoded_frames": 10})["status"], "valid")
        self.assertEqual(from_flac_md5({"status": "mismatched", "decoded_frames": 10})["status"], "valid")
        # A decode failure is already a failed MD5 check; no second failure.
        self.assertIsNone(from_flac_md5({"status": "failed", "reason_code": "audio_not_decodable"}))
        timeout = from_flac_md5({"status": "unavailable", "reason_code": "caller_deadline"})
        self.assertEqual((timeout["status"], timeout["reason_code"]), ("not_completed", "caller_deadline"))


class EvaluationTests(unittest.TestCase):
    """The sample check through Integrity, the builder view and the saved result."""

    _workflow_request = test_target.TargetTests._workflow_request
    _float_wav = test_target.TargetTests._float_wav
    _pcm16 = staticmethod(test_target.TargetTests._pcm16)

    def evaluate(self, stem, deadline=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._workflow_request(root, "multitrack_recording_mix_master", [
                {"type": "audio/stem", "data": stem},
                {"type": "audio/mix", "data": self._pcm16([0] * 800), "parents": [(0, "mixed_from")]},
            ])
            response = run.evaluate(request, root, "native", deadline=deadline)
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        run.validate(response, "scoring-response.schema.json")
        return response

    @staticmethod
    def layer(response):
        return next(item for item in response["assessment_checks"]["integrity_layers"]
                    if item["layer"] == "file_integrity")

    def test_nan_stem_fails_the_file_check(self):
        response = self.evaluate(self._float_wav([0.1] * 400 + [float("nan")] + [0.1] * 399))
        checks = response["assessment_checks"]
        failed = [item for item in checks["bound_file_integrity"]
                  if item["rule"] == "audio_samples_valid" and item["passed"] is False]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["first_non_finite"]["frame"], 400)
        self.assertEqual(self.layer(response)["status"], "contradicted")
        self.assertEqual(response["axes"]["integrity"]["status"], "contradicted")
        self.assertIn("failed the full sample check", response["axes"]["integrity"]["reasoning"])
        rows = attention_items(response)
        self.assertTrue(any(row["status"] == "Check failed" and "NaN at 0.050 s" in row["explanation"]
                            for row in rows), rows)

    def test_time_limit_shows_incomplete_everywhere(self):
        response = self.evaluate(self._float_wav([0.1] * 800), deadline=time.time() - 1)
        checks = response["assessment_checks"]
        rows = [item for item in checks["bound_file_integrity"] if item["rule"] == "audio_samples_valid"]
        self.assertEqual({(item["passed"], item["status"]) for item in rows}, {(None, "not_completed")})
        layer = self.layer(response)
        self.assertEqual((layer["status"], layer["value"]), ("not_completed", None))
        decision = checks["integrity_decision"][0]
        self.assertEqual((decision["status"], decision["not_completed_count"]), ("incomplete", 2))
        self.assertEqual(decision["release_reason"], "file_checks_not_completed")
        integrity = response["axes"]["integrity"]
        self.assertEqual(integrity["status"], "incomplete")
        self.assertIsNone(integrity["value"])
        self.assertTrue(integrity["reasoning"].startswith("2 file checks did not complete"))
        self.assertEqual(len([row for row in response["findings"] if row["code"] == "FILE_CHECK_NOT_COMPLETED"]), 2)
        # Builder: the axis says Incomplete, each file gets one row, nothing reads as a pass.
        self.assertEqual(axis_value_label("integrity", integrity, response),
                         "Incomplete (2 file checks not completed)")
        rows = attention_items(response)
        self.assertEqual([row["status"] for row in rows].count("Check incomplete"), 2)
        self.assertFalse(any("FILE_CHECK_NOT_COMPLETED" in row["explanation"] for row in rows))
        # Export: the saved result JSON carries the same states.
        saved = json.loads(json.dumps(response, allow_nan=False))
        self.assertEqual(saved["axes"]["integrity"]["status"], "incomplete")


class IncompleteWithholdsReleaseTests(unittest.TestCase):
    """A submission that would release Integrity does not while a file check is unfinished."""

    setUp = test_production_documents.ProductionDocumentTests.setUp
    request = test_production_documents.ProductionDocumentTests.request

    def test_value_released_only_when_every_file_was_checked(self):
        report = store(self.folder, sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:43:00", '"MixL"', '"MixR"', '"Boom"', '""']]), "csv")
        request = self.request([(report, "text/sound-report", "csv")])
        finished = run.evaluate(json.loads(json.dumps(request)), self.folder, "native")
        self.assertIsNotNone(finished["axes"]["integrity"]["value"])
        self.assertEqual(finished["axes"]["integrity"]["status"], "consistent")
        unfinished = run.evaluate(json.loads(json.dumps(request)), self.folder, "native",
                                  deadline=time.time() - 1)
        self.assertIsNone(unfinished["axes"]["integrity"]["value"])
        self.assertEqual(unfinished["axes"]["integrity"]["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
