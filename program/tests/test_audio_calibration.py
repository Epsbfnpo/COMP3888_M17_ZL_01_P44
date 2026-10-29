"""Observable safety/abstention regressions for the bounded audio profile."""
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from music_target.audio_diagnostics.policy import DEFAULTS
from music_target.audio_diagnostics.providers.calibration import profile, path_rejections, qualify_comparison
from music_target.audio_diagnostics.providers.music_semantics import _path_quality, _aligned_tempo_candidate
from music_target.audio_diagnostics.providers.structure import detect_sections
from music_target.audio_diagnostics.worker import execute


class ReplayLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        file = Path(__file__).resolve().parents[1] / "benchmarks/audio_calibration.py"
        spec = importlib.util.spec_from_file_location("calibration_replay", file)
        cls.replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.replay)

    def dataset(self, folder, root_value=None):
        data = folder / "inputs"
        data.mkdir()
        families = []
        for name, partition in [("first", "test"), ("second", "test"), ("other", "development")]:
            file = data / (name + ".bin")
            file.write_bytes(name.encode())
            families.append({"id": name, "partition": partition, "path": file.name,
                             "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
        document = {"families": families}
        if root_value is not None:
            document["data_root"] = root_value
        manifest = folder / "manifest.json"
        manifest.write_text(json.dumps(document))
        return manifest, data, document

    def test_manifest_root_works_outside_the_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, data, _ = self.dataset(Path(directory), "inputs")
            rows = self.replay.load_families(manifest, "test")
            self.assertEqual([row["id"] for row in rows], ["first", "second"])
            self.assertTrue(all(Path(row["path"]).parent == data.resolve() for row in rows))

    def test_legacy_manifest_relative_and_absolute_paths_still_work(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, data, document = self.dataset(Path(directory))
            for row in document["families"]:
                row["path"] = str(data / row["path"]) if row["id"] == "first" else "inputs/" + row["path"]
            manifest.write_text(json.dumps(document))
            self.assertEqual(len(self.replay.load_families(manifest, "test")), 2)

    def test_root_override_still_checks_unselected_family_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, data, _ = self.dataset(Path(directory), "missing-old-location")
            self.assertEqual(len(self.replay.load_families(manifest, "test", data)), 2)
            (data / "other.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Audio hash mismatch: other"):
                self.replay.load_families(manifest, "test", data)

    def test_cli_uses_the_relocated_root_without_rewriting_the_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            manifest, data, _ = self.dataset(folder, "missing-old-location")
            before = manifest.read_bytes()
            output = folder / "replay.json"
            args = ["audio_calibration.py", str(manifest), "--data-root", str(data),
                    "--partition", "test", "--out", str(output)]
            with mock.patch.object(sys, "argv", args), \
                 mock.patch.object(self.replay, "semantic_rows", return_value=[]) as measure, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.replay.main()
            self.assertEqual([row["id"] for row in measure.call_args.args[0]], ["first", "second"])
            self.assertEqual(json.loads(output.read_text())["families"], ["first", "second"])
            self.assertEqual(manifest.read_bytes(), before)


class AlignmentGateTests(unittest.TestCase):
    def test_twelve_seconds_collapsed_to_one_timestamp_is_rejected(self):
        source = {"times": np.arange(5) * .46, "chroma": np.ones((12, 4))}
        target = {"times": np.arange(13), "chroma": np.ones((12, 12))}
        path = np.column_stack((np.zeros(12, dtype=int), np.arange(12)))
        quality = _path_quality(path, source, target)
        reasons = path_rejections(quality, _aligned_tempo_candidate(path, source, target), .5)
        self.assertIn("duration_ratio_outside_profile", reasons)
        self.assertIn("excessive_beat_collapse", reasons)

    @staticmethod
    def comparison():
        source = {"times": np.arange(41) * .5, "chroma": np.ones((12, 40))}
        target = copy.deepcopy(source)
        path = np.column_stack((np.arange(40), np.arange(40)))
        quality = _path_quality(path, source, target)
        aligned = _aligned_tempo_candidate(path, source, target)
        first = {"harmonic_similarity": .99, "harmonic_similarity_z": 8., "alignment_quality": quality,
                 "aligned_tempo_candidate": aligned, "alignment_residual_fraction": 0.}
        second = {**first, "harmonic_similarity": .7}
        return {"candidates": [first, second], "shuffled_std": .02, "temporal_information": .3,
                "time_grid_tempo": aligned.copy(), "time_grid_quality": quality.copy(), "time_grid_residual_fraction": 0.}

    def test_beat_octave_or_alias_error_cannot_release_a_speed(self):
        comparison = self.comparison()
        comparison["candidates"][0]["aligned_tempo_candidate"] = {"ratio": .94}
        comparison["time_grid_tempo"] = {"ratio": 1.12}
        decision = qualify_comparison(comparison, DEFAULTS["music_semantics"])
        self.assertEqual(decision["status"], "abstained")
        self.assertIn("tempo_estimators_disagree", decision["rejection_reasons"])

    def test_pitch_tie_and_custom_features_abstain(self):
        comparison = self.comparison()
        comparison["candidates"][1]["harmonic_similarity"] = .99
        decision = qualify_comparison(comparison, {**DEFAULTS["music_semantics"], "hop_length": 256})
        self.assertIn("ambiguous_transposition", decision["rejection_reasons"])
        self.assertIn("feature_parameters_outside_calibration", decision["rejection_reasons"])

    def test_consistent_noncollapsed_alignment_can_be_supported_without_a_score(self):
        decision = qualify_comparison(self.comparison(), DEFAULTS["music_semantics"])
        self.assertEqual(decision["status"], "supported_within_profile")
        self.assertNotIn("confidence", decision)
        self.assertTrue(profile()["freeze_sha256"])


class AcousticBoundaryTests(unittest.TestCase):
    def test_constant_material_is_one_section_not_forced_sixteen_beats(self):
        intervals, _ = detect_sections(np.tile([[1.], [0.]], (1, 80)), np.arange(80) * .5, [0, 40], DEFAULTS["music_semantics"])
        self.assertEqual(intervals, [[0., 40.]])

    def test_non_grid_changes_and_small_feature_noise(self):
        times = np.arange(100) * .5
        features = np.zeros((3, 100))
        features[0, :23] = 1; features[1, 23:61] = 1; features[2, 61:] = 1
        _, original = detect_sections(features, times, [0, 50], DEFAULTS["music_semantics"])
        noisy = features + np.random.default_rng(42).normal(0, .005, features.shape)
        _, perturbed = detect_sections(noisy, times, [0, 50], DEFAULTS["music_semantics"])
        self.assertEqual(len(original["boundary_times_seconds"]), 2)
        for expected, found, changed in zip([11.5, 30.5], original["boundary_times_seconds"], perturbed["boundary_times_seconds"]):
            self.assertLessEqual(abs(expected - found), .5)
            self.assertLessEqual(abs(found - changed), .5)


@unittest.skipIf(importlib.util.find_spec("librosa") is None and os.environ.get("REQUIRE_AUDIO_BACKENDS") != "1", "librosa required")
class CalibratedBackendTests(unittest.TestCase):
    def test_sustained_tone_does_not_force_a_transposition_or_tempo(self):
        from test_audio_diagnostics import write_task
        with tempfile.TemporaryDirectory() as directory:
            y = .1 * np.sin(2 * np.pi * 440 * np.arange(22050 * 12) / 22050)
            task = write_task(Path(directory), y, y)
            value = execute(task, "music_semantics", DEFAULTS["music_semantics"])
            self.assertTrue(value["analysis_complete"], value)
            self.assertIsNone(value["metrics"]["transposition_semitones"])
            self.assertIsNone(value["metrics"]["tempo_ratio"])
            self.assertFalse(value["scoring_eligible"])
            self.assertIsNone(value["integrity_contribution"])

    def test_music_section_detection_is_exactly_target_independent(self):
        from test_audio_diagnostics import write_task, tone_sequence
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory); y = tone_sequence(14)
            a = execute(write_task(folder, y, tone_sequence(10, seed=8)), "music_semantics", DEFAULTS["music_semantics"])
            b = execute(write_task(folder, y, tone_sequence(12, seed=9)), "music_semantics", DEFAULTS["music_semantics"])
            self.assertTrue(a["analysis_complete"], a); self.assertTrue(b["analysis_complete"], b)
            self.assertEqual(a["metrics"]["source"]["sections_seconds"], b["metrics"]["source"]["sections_seconds"])
            self.assertEqual(a["metrics"]["source"]["section_detection"], b["metrics"]["source"]["section_detection"])

    def test_low_beat_count_falls_back_to_time_grid_without_dividing_none(self):
        from test_audio_diagnostics import write_task, tone_sequence
        import librosa
        with tempfile.TemporaryDirectory() as directory:
            task = write_task(Path(directory), tone_sequence(10), tone_sequence(10, seed=9))
            with mock.patch.object(librosa.beat, "beat_track", return_value=(0., np.array([], dtype=int))):
                value = execute(task, "music_semantics", DEFAULTS["music_semantics"])
            self.assertTrue(value["analysis_complete"], value)
            self.assertIsNone(value["metrics"]["tempo_ratio"])
            self.assertEqual(value["metrics"]["source"]["beat_tracking_status"], "insufficient_beats_using_time_grid")
