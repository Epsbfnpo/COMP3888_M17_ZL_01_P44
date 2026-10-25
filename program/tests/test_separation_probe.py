"""The Phase 1 separation probe on generated signals, with a stand-in separator.

No test imports torch or demucs, downloads weights or reads real audio.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "benchmarks"))
TORCH_PRELOADED = "torch" in sys.modules
import separation_probe as probe  # noqa: E402

RATE = 44100


def synthetic_sources(seconds=4.0, seed=7):
    """Three uncorrelated parts: noise-burst drums, a low bass tone and a bright harmonic 'vocal'."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    drums = np.zeros_like(t)
    for start in np.arange(0, seconds, 0.5):
        index = int(start * RATE)
        burst = rng.standard_normal(4000) * np.exp(-np.arange(4000) / 600)
        drums[index:index + 4000] += 0.3 * burst[:len(drums) - index]
    bass = 0.3 * np.sin(2 * np.pi * 55 * t) + 0.1 * np.sin(2 * np.pi * 110 * t)
    # A gliding pitch with irregular vibrato, so no stretch of the part repeats exactly.
    pitch = 200 + 25 * t + 4 * np.sin(2 * np.pi * 5.3 * t + 0.4 * t ** 2)
    phase = 2 * np.pi * np.cumsum(pitch) / RATE
    vocals = sum(0.15 / k * np.sin(k * phase) for k in range(1, 9))
    vocals = vocals + 0.02 * rng.standard_normal(len(t))
    return {name: np.stack([signal, 0.9 * signal], axis=1) for name, signal in
            (("drums", drums), ("bass", bass), ("vocals", vocals))} | {"other": np.zeros((len(t), 2))}


def lowpass(audio, taps=9):
    kernel = np.ones(taps) / taps
    return np.stack([np.convolve(audio[:, ch], kernel, mode="same") for ch in range(audio.shape[1])], axis=1)


def fake_separator(truth, target="vocals", leak=0.1):
    """Stand-in for Demucs: the target loses high frequencies and gains leakage from the other parts."""
    def separate(mix, rate):
        others = sum(audio for name, audio in truth.items() if name != target)
        sources = {name: audio + leak * truth[target] for name, audio in truth.items() if name != target}
        sources[target] = lowpass(truth[target]) + leak * others
        return {"sources": {name: probe.fit_length(audio, len(mix)) for name, audio in sources.items()},
                "warnings": [], "model": {"name": "fake-separator", "model_sample_rate_hz": RATE}}
    return separate


class SignalMeasureTests(unittest.TestCase):
    def setUp(self):
        self.vocals = synthetic_sources()["vocals"][:, 0]

    def test_alignment_recovers_offset_and_polarity(self):
        for lag in (1234, -777, 0):
            query = np.roll(self.vocals, -lag) if lag >= 0 else np.concatenate((np.zeros(-lag), self.vocals))
            result = probe.align(self.vocals, query, RATE, 1.0)
            self.assertEqual(result["lag_samples"], lag)
            self.assertEqual(result["polarity"], 1)
        inverted = probe.align(self.vocals, -0.5 * self.vocals, RATE, 1.0)
        self.assertEqual((inverted["lag_samples"], inverted["polarity"]), (0, -1))

    def test_exactly_repeating_material_is_reported_as_ambiguous(self):
        t = np.arange(3 * RATE) / RATE
        loop = np.sin(2 * np.pi * 220 * t) * (1 + 0.5 * np.sin(2 * np.pi * 5 * t))
        self.assertGreaterEqual(probe.align(loop, loop, RATE, 1.0)["secondary_peak_ratio"],
                                probe.AMBIGUOUS_PEAK_RATIO)

    def test_lag_beyond_search_limit_is_flagged(self):
        query = self.vocals[RATE:]
        self.assertTrue(probe.align(self.vocals, query, RATE, 0.25)["stronger_peak_outside_limit"])
        wide = probe.align(self.vocals, query, RATE, 2.0)
        self.assertEqual(wide["lag_samples"], RATE)
        self.assertFalse(wide["stronger_peak_outside_limit"])

    def test_gain_and_identity_measures(self):
        gain = probe.gain_to_reference(self.vocals, 2.0 * self.vocals)
        self.assertAlmostEqual(gain, 0.5)
        measures = probe.waveform_measures(self.vocals, 2.0 * self.vocals, gain)
        self.assertAlmostEqual(measures["pearson_correlation"], 1.0, places=6)
        self.assertAlmostEqual(measures["gain_db"], -6.0206, places=3)
        self.assertLess(measures["residual_to_reference_db"], -200)
        self.assertIsNone(probe.si_sdr(self.vocals, self.vocals))
        self.assertGreater(probe.si_sdr(self.vocals, self.vocals + 1e-3 * np.sin(np.arange(len(self.vocals)))),
                           20)
        spectral = probe.spectral_measures(self.vocals, self.vocals, 1.0, RATE)
        self.assertAlmostEqual(spectral["log_spectral_correlation"], 1.0, places=6)
        self.assertAlmostEqual(spectral["log_spectral_distance_db"], 0.0, places=6)

    def test_lowpass_lowers_high_frequency_profile(self):
        full = probe.high_frequency_profile(self.vocals, RATE)
        dull = probe.high_frequency_profile(lowpass(self.vocals[:, None])[:, 0], RATE)
        self.assertLess(dull["band_energy_fraction"]["8000-16000_hz"],
                        full["band_energy_fraction"]["8000-16000_hz"])
        self.assertLess(dull["rolloff_99_hz_median"], full["rolloff_99_hz_median"])

    def test_silence_gives_null_measures_not_errors(self):
        silent = np.zeros(RATE * 2)
        self.assertIsNone(probe.high_frequency_profile(silent, RATE)["band_energy_fraction"])
        self.assertIsNone(probe.spectral_measures(silent, silent, 1.0, RATE)["log_spectral_correlation"])
        self.assertIsNone(probe.waveform_measures(silent, silent, 0.0)["pearson_correlation"])


class ProbeRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.truth = synthetic_sources()
        self.mix_path, self.stem_path = self.root / "mix.wav", self.root / "vocals.wav"
        sf.write(str(self.mix_path), sum(self.truth.values()) * 0.8, RATE, subtype="FLOAT")
        sf.write(str(self.stem_path), self.truth["vocals"], RATE, subtype="FLOAT")

    def tearDown(self):
        self.temp.cleanup()

    def run_probe(self, name="out", **kwargs):
        return probe.run_probe(self.mix_path, self.stem_path, "vocals", self.root / name,
                               fake_separator(self.truth), **kwargs)

    def test_report_structure_hashes_and_labels(self):
        report = self.run_probe()
        text = (self.root / "out" / probe.REPORT_NAME).read_text()
        self.assertEqual(json.loads(text), report)
        json.dumps(report, allow_nan=False)
        self.assertEqual(report["status"], "exploratory_uncalibrated")
        self.assertIs(report["scoring_eligible"], False)
        self.assertIs(report["content_match_is_provenance"], False)
        self.assertEqual(report["inputs"]["mix"]["sha256"], probe.sha256(self.mix_path))
        self.assertEqual(report["inputs"]["submitted_stem"]["sha256"], probe.sha256(self.stem_path))
        derived = Path(report["outputs"]["derived_stem"]["path"])
        self.assertEqual(report["outputs"]["derived_stem"]["sha256"], probe.sha256(derived))
        self.assertEqual(report["model"]["name"], "fake-separator")
        self.assertEqual(set(report["comparisons"]), {"A_submitted_stem_vs_mix", "B_derived_stem_vs_mix",
                                                      "C_submitted_stem_vs_derived_stem"})
        self.assertEqual(set(report["runtime_seconds"]), {"load", "separation", "diagnostics", "total"})
        self.assertTrue(report["limitations"])
        lowered = text.lower()
        for word in ("verdict", "forged", "\"label\"", "classification", "is genuine"):
            self.assertNotIn(word, lowered)

    def test_measurements_follow_the_stand_in_artefacts(self):
        # Sanity of the plumbing on constructed signals, not a calibration result.
        report = self.run_probe()
        a = report["comparisons"]["A_submitted_stem_vs_mix"]
        c = report["comparisons"]["C_submitted_stem_vs_derived_stem"]
        self.assertEqual(a["alignment"]["lag_samples"], 0)
        # The fitted gain maps the stem onto the mix, which holds it at 0.8; the other
        # parts are only nearly orthogonal to it, so allow a small deviation.
        self.assertAlmostEqual(a["waveform"]["gain_db"], 20 * np.log10(0.8), delta=0.05)
        self.assertGreater(c["waveform"]["pearson_correlation"], 0.8)
        self.assertIsNotNone(c["waveform"]["si_sdr_db_query_against_reference"])
        hf = report["high_frequency"]
        self.assertLess(hf["derived_stem"]["band_energy_fraction"]["8000-16000_hz"],
                        hf["submitted_stem"]["band_energy_fraction"]["8000-16000_hz"])
        talk = report["crosstalk"]
        self.assertGreater(talk["derived_stem"]["pearson_correlation_with_other_sources_sum"],
                           talk["submitted_stem"]["pearson_correlation_with_other_sources_sum"])
        self.assertTrue(talk["derived_stem"]["same_separation_run"])

    def test_repeated_runs_are_identical_apart_from_runtime_and_paths(self):
        def stable(report):
            report = json.loads(json.dumps(report))
            report.pop("runtime_seconds")
            stem = report.pop("outputs")["derived_stem"]
            return report, stem["samples_sha256"]
        self.assertEqual(stable(self.run_probe("one")), stable(self.run_probe("two")))

    def test_sample_hash_matches_the_written_audio(self):
        report = self.run_probe()
        stem = report["outputs"]["derived_stem"]
        audio, _ = sf.read(stem["path"], dtype="float32", always_2d=True)
        self.assertEqual(stem["samples_sha256"],
                         hashlib.sha256(np.ascontiguousarray(audio, dtype="<f4").tobytes()).hexdigest())

    def test_existing_output_is_not_overwritten_without_flag(self):
        self.run_probe()
        with self.assertRaises(probe.ProbeError):
            self.run_probe()
        self.run_probe(overwrite=True)

    def test_input_mismatches_produce_warnings(self):
        sf.write(str(self.stem_path), self.truth["vocals"][:RATE, 0], RATE, subtype="FLOAT")
        warnings = " ".join(self.run_probe()["warnings"])
        self.assertIn("channel counts", warnings)
        self.assertIn("durations differ", warnings)

    def test_unreadable_audio_is_a_probe_error(self):
        self.stem_path.write_bytes(b"not audio")
        with self.assertRaises(probe.ProbeError):
            self.run_probe()


class OptionalDependencyTests(unittest.TestCase):
    def test_module_import_does_not_load_torch(self):
        if not TORCH_PRELOADED:
            self.assertNotIn("torch", sys.modules)
            self.assertNotIn("demucs", sys.modules)

    def test_cli_refuses_without_local_weights_or_download_consent(self):
        with tempfile.TemporaryDirectory() as folder:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = probe.main(["--mix", "m.wav", "--genuine-stem", "s.wav", "--source", "bass",
                                   "--output-dir", folder])
        self.assertEqual(code, 2)
        self.assertIn("--allow-download", stderr.getvalue())
        if not TORCH_PRELOADED:
            self.assertNotIn("torch", sys.modules)

    def test_cli_rejects_unknown_source(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            probe.parse_args(["--mix", "m", "--genuine-stem", "s", "--source", "piano", "--output-dir", "o"])

    def test_missing_model_repo_directory_is_rejected(self):
        with self.assertRaises(probe.ProbeError):
            probe.demucs_separator(model_repo="/nonexistent/demucs-models")

    @unittest.skipIf(importlib.util.find_spec("demucs") is not None, "demucs is installed")
    def test_missing_demucs_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as folder:
            separate = probe.demucs_separator(model_repo=folder)
            with self.assertRaises(probe.ProbeError) as caught:
                separate(np.zeros((RATE, 2)), RATE)
        self.assertIn("requirements-separation.txt", str(caught.exception))

    def test_evaluator_does_not_reference_the_probe_or_torch(self):
        for path in (BASE / "music_target").rglob("*.py"):
            text = path.read_text()
            self.assertNotIn("separation_probe", text, path)
            self.assertNotIn("import torch", text, path)


if __name__ == "__main__":
    unittest.main()
