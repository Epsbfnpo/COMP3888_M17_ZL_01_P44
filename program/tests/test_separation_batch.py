"""The Phase 2A batch runner on generated songs, with a stand-in separator (no torch, weights or real audio)."""

import contextlib
import csv
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import separation_batch as batch  # noqa: E402
from test_separation_probe import RATE, fake_separator, synthetic_sources  # noqa: E402


def write_song(root, name, seed, silent_other=False):
    truth = synthetic_sources(seconds=3.0, seed=seed)
    if not silent_other:
        rng = np.random.default_rng(seed + 100)
        truth["other"] = 0.05 * rng.standard_normal(truth["drums"].shape)
    folder = root / "test" / name
    folder.mkdir(parents=True)
    sf.write(str(folder / "mixture.wav"), sum(truth.values()) * 0.8, RATE, subtype="FLOAT")
    for source, audio in truth.items():
        sf.write(str(folder / f"{source}.wav"), audio, RATE, subtype="FLOAT")
    return truth


class CountingSeparator:
    """Separates each song by matching its mix to the generated parts, and counts calls."""
    def __init__(self, truths):
        self.truths, self.calls = truths, 0

    def __call__(self, mix, rate):
        self.calls += 1
        for truth in self.truths:
            if np.allclose(sum(truth.values()) * 0.8, mix, atol=1e-6):
                return fake_separator(truth)(mix, rate)
        raise AssertionError("unknown mix")


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.truths = [write_song(self.root, "Song A", 1), write_song(self.root, "Song B", 2),
                       write_song(self.root, "Song C", 3, silent_other=True)]

    def tearDown(self):
        self.temp.cleanup()

    def test_song_selection_is_sorted_and_honours_exclusions(self):
        songs = batch.select_songs(self.root, "test", 2, ["Song A"])
        self.assertEqual([song.name for song in songs], ["Song B", "Song C"])
        with self.assertRaises(batch.probe.ProbeError):
            batch.select_songs(self.root, "test", 4, [])

    def test_batch_separates_each_song_once_and_writes_labelled_outputs(self):
        separator = CountingSeparator(self.truths)
        songs = batch.select_songs(self.root, "test", 3, [])
        meta, rows, analysis = batch.run_batch(songs, separator, self.root / "out")
        self.assertEqual(separator.calls, 3)
        self.assertEqual(len(rows), 3 * 4 * 2)
        with open(self.root / "out" / "measurements.csv") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 24)
        for name in ("measurements.json", "analysis.json"):
            data = json.loads((self.root / "out" / name).read_text())
            self.assertEqual(data["status"], "exploratory_uncalibrated")
            self.assertIs(data["scoring_eligible"], False)
        self.assertTrue((self.root / "out" / "stems" / "Song A" / "demucs_vocals.wav").is_file())
        # Song C's 'other' stem is silent, so that pair is excluded by the declared rule.
        self.assertIn("Song C / other", analysis["pairs_excluded"])
        self.assertNotIn("loso_youden", analysis["metrics"]["vs_demucs_si_sdr_db"])
        self.assertEqual(analysis["metrics"]["vs_demucs_pearson"]["role"], "circular_same_model")
        with self.assertRaises(batch.probe.ProbeError):
            batch.run_batch(songs, separator, self.root / "out")

    def test_reused_stems_reproduce_the_measurements_without_separating(self):
        songs = batch.select_songs(self.root, "test", 3, [])
        _, rows, _ = batch.run_batch(songs, CountingSeparator(self.truths), self.root / "first")
        _, again, _ = batch.run_batch(songs, batch.saved_separator(self.root / "first"), self.root / "second")
        # Saved stems are float32; the stand-in separator works in float64 (Demucs itself outputs float32).
        self.assertEqual(len(rows), len(again))
        for first, second in zip(rows, again):
            self.assertEqual(first.keys(), second.keys())
            for key, value in first.items():
                if isinstance(value, float) and second[key] is not None:
                    self.assertAlmostEqual(value, second[key], delta=1e-4 * max(1.0, abs(value)), msg=key)
                else:
                    self.assertEqual(value, second[key], key)
        self.assertFalse((self.root / "second" / "stems").exists())
        self.assertIn("hf_excess_mix_referenced_db", rows[0])
        self.assertEqual([song.name for song in batch.select_songs(self.root, "test", 1, [], skip=2)], ["Song C"])

    def test_frozen_rules_apply_unchanged_and_report_development_overlap(self):
        import separation_rules as rules_module
        rows = []
        for song, offset in (("dev1", 0.0), ("dev2", 0.5), ("held", 3.0)):
            for source in batch.probe.SOURCES:
                for kind, value in (("genuine", 0.0 + offset), ("derived", 10.0 + offset)):
                    rows.append({"song": song, "source": source, "kind": kind, "excluded": False,
                                 "hf_excess_over_mix_db": value, "hf_excess_mix_referenced_db": value})
        frozen = rules_module.freeze([row for row in rows if row["song"] != "held"])
        self.assertEqual(frozen["development_songs"], ["dev1", "dev2"])
        result = rules_module.apply_rules(frozen, [row for row in rows if row["song"] == "held"])
        self.assertEqual(result["development_songs_in_evaluation"], [])
        primary = result["rules"]["P"]
        self.assertTrue(primary["primary"])
        # Thresholds sit between 0.5 and 10 from development; held-out genuine 3.0 is not flagged.
        self.assertEqual(primary["rates"]["false_positive"]["count"], 0)
        self.assertEqual(primary["rates"]["detection"]["count"], 4)
        self.assertEqual(result["rules"]["S0"]["thresholds"]["all"]["threshold"], 10.0)
        mixed = rules_module.apply_rules(frozen, rows)
        self.assertEqual(mixed["development_songs_in_evaluation"], ["dev1", "dev2"])
        out = self.root / "frozen.json"
        out.write_text("{}")
        measurements = self.root / "m.json"
        measurements.write_text(json.dumps({"rows": rows}))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(rules_module.main(["freeze", str(measurements), "--out", str(out)]), 2)
        self.assertEqual(out.read_text(), "{}")

    def test_auc_and_threshold_choice(self):
        self.assertEqual(batch.auc([0, 1], [2, 3]), 1.0)
        self.assertEqual(batch.auc([2, 3], [0, 1]), 0.0)
        self.assertEqual(batch.auc([1], [1]), 0.5)
        rule = batch.choose_threshold([0, 1, 2], [5, 6])
        self.assertEqual(rule["direction"], "greater_or_equal")
        self.assertTrue(2 < rule["threshold"] < 5)
        self.assertEqual(batch.choose_threshold([5, 6], [0, 1])["direction"], "less_or_equal")
        strict = batch.choose_threshold([0, 4], [3, 5], max_false_positive=0.0)
        self.assertGreater(strict["threshold"], 4)

    def test_leave_one_song_out_never_sees_the_held_out_song(self):
        rows = []
        for song, genuine, derived in (("s1", 0.0, 1.0), ("s2", 0.1, 1.1), ("s3", 5.0, 6.0)):
            rows += [{"song": song, "kind": "genuine", "m": genuine}, {"song": song, "kind": "derived", "m": derived}]
        result = batch.leave_one_song_out(rows, "m")
        # Holding out s3, s1 and s2 give 0.55 (between 0.1 and 1.0), so s3's genuine 5.0
        # is a false positive and its derived 6.0 a detection. Holding out s1 (or s2), the
        # training set ties a low threshold and 5.5 on Youden J and the lower
        # false-positive rate wins (5.5), so neither of its stems is flagged.
        self.assertEqual(result["rates_held_out"]["false_positive"]["count"], 1)
        self.assertEqual(result["rates_held_out"]["detection"]["count"], 1)
        self.assertEqual(result["threshold_range"], [0.55, 5.5])


if __name__ == "__main__":
    unittest.main()
