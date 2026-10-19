"""structure_benchmark on tiny synthetic SALAMI- and GuitarSet-shaped data."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

BASE = Path(__file__).resolve().parents[1]
for path in (BASE, BASE / "vendor", BASE / "benchmarks", Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))

REQUIRED = ("librosa", "mir_eval")


def missing():
    return [name for name in REQUIRED if importlib.util.find_spec(name) is None]


@unittest.skipIf(missing() and os.environ.get("REQUIRE_AUDIO_BACKENDS") != "1",
                 "librosa and mir_eval are needed for the structure benchmark")
class StructureBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import structure_benchmark as bench
        from test_audio_diagnostics import POP, synthetic_song
        cls.bench = bench
        cls.temp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.temp.name) / "data"
        # SALAMI layout: one 100 BPM song AABAB, 16-beat (9.6 s) sections.
        song = synthetic_song(POP, "AABAB")
        (cls.data / "salami-audio").mkdir(parents=True)
        bench._write_wav(cls.data / "salami-audio" / "1.wav", song)
        parsed = cls.data / "salami-data-public-master" / "annotations" / "1" / "parsed"
        parsed.mkdir(parents=True)
        events = [(0.0, "A"), (19.2, "B"), (28.8, "A"), (38.4, "B"), (len(song) / 22050, "End")]
        (parsed / "textfile1_uppercase.txt").write_text("".join(f"{t:.3f}\t{label}\n" for t, label in events))
        # GuitarSet layout: same song, chords per bar, beats every 0.6 s, key C major.
        guitar = cls.data / "guitarset"
        guitar.mkdir()
        bench._write_wav(guitar / "00_SYN1-100-C_comp_mic.wav", song)
        chords = [chord for name in "AABAB" for chord in POP[name]]
        to_harte = lambda name: f"{name[:-1]}:min" if name.endswith("m") else f"{name}:maj"
        jams = {"annotations": [
            {"namespace": "beat_position", "data": [{"time": i * 0.6, "duration": 0, "value": {}, "confidence": 1}
                                                    for i in range(80)]},
            {"namespace": "chord", "annotation_metadata": {"data_source": "performed"},
             "data": [{"time": i * 2.4, "duration": 2.4, "value": to_harte(c), "confidence": 1} for i, c in enumerate(chords)]},
            {"namespace": "key_mode", "data": [{"time": 0, "duration": 48, "value": "C:major", "confidence": 1}]}]}
        (guitar / "00_SYN1-100-C_comp.jams").write_text(json.dumps(jams))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_salami_reference_parsing(self):
        intervals, labels = self.bench.salami_reference(self.data / "salami-data-public-master", 1)
        self.assertEqual(labels, ["A", "B", "A", "B"])
        self.assertAlmostEqual(intervals[1][0], 19.2)

    def test_salami_mode_scores_every_method_and_compares_to_fixed_phrases(self):
        summary = self.bench.run_salami(self.data, Path(self.temp.name) / "out")
        methods = summary["methods"]
        self.assertEqual(set(methods), {"fixed_phrases", "librosa_laplacian"})
        fixed = methods["fixed_phrases"]
        self.assertEqual(fixed["evaluated"], 1)
        # Phrases land on the true 16-beat section starts of this song.
        self.assertGreater(fixed["boundary_f_3s"], 0.6)
        self.assertIn("vs_fixed_phrases_boundary_f_3s", methods["librosa_laplacian"])
        self.assertTrue(summary["recommendation"])

    def test_guitarset_mode_scores_beats_chords_key_and_tempo(self):
        summary = self.bench.run_guitarset(self.data, Path(self.temp.name) / "out")
        self.assertEqual(summary["failed"], 0)
        self.assertGreater(summary["beat_f"], 0.8)
        self.assertGreater(summary["chord_majmin"], 0.6)
        self.assertEqual(summary["key_score"], 1.0)
        self.assertEqual(summary["tempo_within_4pct_octave"], 1.0)

    def test_bootstrap_interval(self):
        result = self.bench.bootstrap_difference([0.8, 0.9, 0.7], [0.5, 0.6, 0.4])
        self.assertAlmostEqual(result["mean"], 0.3)
        self.assertGreater(result["ci95"][0], 0)


if __name__ == "__main__":
    unittest.main()
