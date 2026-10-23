"""Report-only content bandwidth: where the decoded audio stops, against what the file claims."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

import numpy as np

BASE = Path(__file__).resolve().parents[1]
for path in (BASE, BASE / "vendor", Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))

import soundfile  # noqa: E402
from music_target.audio_diagnostics.policy import DEFAULTS, DEFAULT_PATH  # noqa: E402
from music_target.audio_diagnostics.providers import bandwidth  # noqa: E402

PARAMETERS = DEFAULTS["bandwidth"]


def noise(rate, seconds=6.0, seed=0):
    return np.random.default_rng(seed).normal(scale=0.2, size=int(rate * seconds))


def brickwall(signal, rate, cutoff_hz):
    spectrum = np.fft.rfft(signal)
    spectrum[np.fft.rfftfreq(len(signal), 1 / rate) > cutoff_hz] = 0
    return np.fft.irfft(spectrum, len(signal))


def blocks_of(signal, size):
    def blocks(first, last):
        for start in range(first, last, size):
            yield signal[start:min(last, start + size)].reshape(-1, 1).astype(np.float32)
    return blocks


def edge_of(signal, rate, block=65536):
    freqs, rel, _ = bandwidth.spectrum_profile(blocks_of(signal, block), len(signal), rate, PARAMETERS)
    cutoffs = bandwidth.find_cutoffs(freqs, rel, rate, PARAMETERS)
    return min((item["frequency_hz"] for item in cutoffs), default=None), cutoffs


class AssessmentTests(unittest.TestCase):
    def test_mp3_content_band_is_compared_with_its_bitrate(self):
        self.assertEqual(bandwidth.assess(16400, 44100, "MP3", 320)["status"],
                         "content_band_lower_than_bitrate_suggests")
        self.assertEqual(bandwidth.assess(20200, 44100, "MP3", 320)["status"], "consistent_with_bitrate")
        self.assertEqual(bandwidth.assess(16500, 44100, "MP3", 128)["status"], "consistent_with_bitrate")
        self.assertEqual(bandwidth.assess(7300, 16000, "MP3", 64)["status"], "not_assessed")

    def test_lossless_band_limits_name_their_alternatives(self):
        self.assertEqual(bandwidth.assess(None, 44100, "WAV")["status"], "no_band_limit_found")
        lossy = bandwidth.assess(16000, 44100, "FLAC")
        self.assertEqual(lossy["status"], "band_limit_like_lossy_encoding")
        self.assertIn("generated or filtered", lossy["reason"])
        self.assertEqual(bandwidth.assess(20900, 96000, "FLAC")["status"], "consistent_with_upsampling")
        # A 48 kHz file whose audio stops at 22.05 kHz was resampled up from 44.1 kHz.
        upsampled = bandwidth.assess(22054, 48000, "WAV")
        self.assertEqual((upsampled["status"], upsampled["likely_source_rate_hz"]),
                         ("consistent_with_upsampling", 44100))
        self.assertEqual(bandwidth.assess(23000, 48000, "WAV")["status"], "band_limited")


class DetectorTests(unittest.TestCase):
    def test_full_band_noise_has_no_band_limit(self):
        self.assertEqual(edge_of(noise(44100), 44100)[0], None)

    def test_low_passed_audio_shows_a_sharp_cliff(self):
        edge, cutoffs = edge_of(brickwall(noise(44100), 44100, 16000), 44100)
        self.assertAlmostEqual(edge, 16000, delta=300)
        self.assertEqual(cutoffs[0]["kind"], "sharp_cliff")

    def test_low_passed_audio_under_a_noise_floor_shows_a_step(self):
        # A lossy decode re-quantised to 16 bits: the cut sits only ~8 dB above a flat floor.
        band = brickwall(noise(44100), 44100, 16000)
        floor = np.random.default_rng(9).normal(scale=0.2 * 10 ** (-8 / 20), size=len(band))
        edge, cutoffs = edge_of(band + floor, 44100)
        self.assertAlmostEqual(edge, 16000, delta=500)
        self.assertIn("step_into_flat_floor", {item["kind"] for item in cutoffs})

    def test_upsampled_audio_stops_at_the_original_nyquist_frequency(self):
        edge, _ = edge_of(brickwall(noise(96000), 96000, 21000), 96000)
        self.assertEqual(bandwidth.assess(edge, 96000, "WAV")["status"], "consistent_with_upsampling")

    def test_block_size_does_not_change_the_profile(self):
        signal = brickwall(noise(44100, seed=4), 44100, 17000)
        one = bandwidth.spectrum_profile(blocks_of(signal, len(signal)), len(signal), 44100, PARAMETERS)[1]
        many = bandwidth.spectrum_profile(blocks_of(signal, 3001), len(signal), 44100, PARAMETERS)[1]
        np.testing.assert_allclose(one, many)

    def test_too_little_audio_is_unavailable_not_a_result(self):
        from music_target.audio_diagnostics.io import AnalysisUnavailable
        with self.assertRaises(AnalysisUnavailable):
            edge_of(noise(44100, seconds=0.1), 44100)


class EvaluatorTests(unittest.TestCase):
    def test_bandwidth_rows_and_flags_do_not_change_scores(self):
        import run
        from music_target.audio_diagnostics.runner import augment_response
        from test_production_documents import store
        folder = Path(tempfile.mkdtemp())
        (folder / "objects").mkdir()
        flat = np.stack([brickwall(noise(44100, seed=s), 44100, 16000) for s in (1, 2)], axis=1)
        path = folder / "made_from_mp3.flac"
        soundfile.write(str(path), flat, 44100, format="FLAC", subtype="PCM_24")
        digest = store(folder, path.read_bytes(), "flac")
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["chain"].update(final_artefact_hash=digest, artefacts=[
            {"artefact_hash": digest, "artefact_type": "audio/master", "attributes": {}, "evidence": []}])
        request["objects"] = {digest: f"objects/{digest}.flac"}
        core = run.evaluate(copy.deepcopy(request), folder, "native")
        combined = augment_response(core, request, folder, "native", DEFAULT_PATH, time.monotonic() + 300)
        self.assertEqual(combined["axes"], core["axes"])
        rows = combined["assessment_checks"]["audio_bandwidth"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metrics"]["assessment"]["status"], "band_limit_like_lossy_encoding")
        self.assertIs(rows[0]["scoring_eligible"], False)
        flag = next(row for row in combined["findings"] if row["code"] == "AUDIO_CONTENT_BAND_LIMITED")
        self.assertEqual((flag["evidence_hash"], flag["severity"]), (digest, "low"))
        self.assertEqual(combined["assessment_checks"]["audio_diagnostic_summary"][0]["file_task_count"], 1)


if __name__ == "__main__":
    unittest.main()
