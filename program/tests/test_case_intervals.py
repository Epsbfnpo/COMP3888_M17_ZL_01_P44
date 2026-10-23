"""Wilson intervals for case counts, as stored in the calibration status."""

import importlib.util
import json
from pathlib import Path
import sys
import unittest

BASE = Path(__file__).resolve().parents[1]
for path in (BASE, BASE / "benchmarks"):
    sys.path.insert(0, str(path))

from music_target.case_intervals import case_count, interval_record, wilson_interval  # noqa: E402

STATUS_PATH = BASE / "policies" / "audio_calibration_status.json"
SPLITS = ("development", "held_out", "real_material")


class WilsonIntervalTests(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(interval_record(0, 29), {"lower": 0.0, "upper": 0.117})
        self.assertEqual(interval_record(15, 16), {"lower": 0.717, "upper": 0.989})
        self.assertEqual(interval_record(57, 72), {"lower": 0.684, "upper": 0.869})
        self.assertEqual(interval_record(4, 4), {"lower": 0.51, "upper": 1.0})

    def test_no_cases_has_no_interval(self):
        self.assertIsNone(wilson_interval(0, 0))
        self.assertIsNone(case_count("Nothing run", 0, 0)["wilson_95"])

    def test_interval_contains_the_rate_and_is_symmetric(self):
        for cases in (1, 4, 29, 72):
            for count in range(cases + 1):
                lower, upper = wilson_interval(count, cases)
                self.assertLessEqual(0.0, lower)
                self.assertLessEqual(lower, count / cases)
                self.assertLessEqual(count / cases, upper)
                self.assertLessEqual(upper, 1.0)
                mirror = wilson_interval(cases - count, cases)
                self.assertAlmostEqual(lower, 1 - mirror[1])

    def test_invalid_counts_are_rejected(self):
        for count, cases in ((-1, 4), (5, 4), (0, -1)):
            with self.assertRaises(ValueError):
                wilson_interval(count, cases)
        for count, cases in ((True, 4), (1.0, 4), (1, "4")):
            with self.assertRaises(TypeError):
                wilson_interval(count, cases)


class CalibrationStatusIntervalTests(unittest.TestCase):
    """Every stored count carries the interval recomputed from it."""

    status = json.loads(STATUS_PATH.read_text())

    def test_case_counts_are_split_and_intervals_match(self):
        self.assertEqual(tuple(self.status["intervals"]["splits"]), SPLITS)
        entries = 0
        for provider, record in self.status["providers"].items():
            counts = record.get("case_counts")
            if counts is None:
                continue
            self.assertEqual(tuple(counts), SPLITS, provider)
            for split in SPLITS:
                for entry in counts[split]:
                    entries += 1
                    self.assertEqual(entry["wilson_95"], interval_record(entry["count"], entry["cases"]),
                                     f"{provider} {split}: {entry['measure']}")
        self.assertGreater(entries, 10)

    def test_false_match_categories_are_held_out_with_intervals(self):
        for provider, record in self.status["providers"].items():
            rates = record.get("false_match_rates")
            if not rates:
                continue
            self.assertEqual(rates["split"], "held_out", provider)
            for name, item in rates["categories"].items():
                observed = next(item[key] for key in ("false_matches", "detected", "supported") if key in item)
                self.assertEqual(item["wilson_95"], interval_record(observed, item["pairs"]), f"{provider} {name}")

    def test_encoded_benchmark_runs_carry_intervals(self):
        for provider, record in self.status["providers"].items():
            for encoding, rates in (record.get("encoded_false_match_rates") or {}).items():
                self.assertEqual(rates["split"], "held_out")
                for name, item in rates["categories"].items():
                    observed = next(item[key] for key in ("false_matches", "detected", "supported") if key in item)
                    self.assertEqual(item["wilson_95"], interval_record(observed, item["pairs"]),
                                     f"{provider} {encoding} {name}")

    def test_documented_held_out_counts(self):
        held_out = {entry["measure"]: (entry["count"], entry["cases"])
                    for entry in self.status["providers"]["music_semantics"]["case_counts"]["held_out"]}
        self.assertEqual(held_out["Distinct-recording controls and a sustained tone supported"], (0, 29))
        self.assertEqual(held_out["Known DSP derivatives supported (8 SALAMI families)"], (57, 72))


class BenchmarkSummaryTests(unittest.TestCase):
    """The replay and false-match scripts report counts with intervals."""

    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("numpy") is None:
            raise unittest.SkipTest("numpy is needed for the benchmark scripts")
        import audio_calibration
        cls.calibration = audio_calibration

    def test_semantic_summary(self):
        def row(label, status, pitch=0, truth=0):
            return {"label": label, "assessment": {"status": status}, "best_pitch": pitch,
                    "truth": None if label != "known_dsp_derivative" else {"transposition": truth}}
        rows = [row("known_dsp_derivative", "supported_within_profile", 2, 2),
                row("known_dsp_derivative", "supported_within_profile", 0, -3),
                row("known_dsp_derivative", "abstained"),
                row("distinct_recording_control", "abstained"),
                row("low_information_control", "supported_within_profile")]
        summary = [(entry["count"], entry["cases"]) for entry in self.calibration.semantic_summary(rows)]
        self.assertEqual(summary, [(2, 3), (1, 2), (1, 2)])

    def test_fingerprint_summary_requires_the_expected_offset(self):
        def match(offset, status="supported_within_profile"):
            return {"source_minus_target_offset_seconds": offset, "support_assessment": {"status": status}}
        rows = [{"density": 40, "expected_offset": -2.0, "matches": [match(-2.05)]},
                {"density": 40, "expected_offset": 5.0, "matches": [match(3.0)]},
                {"density": 40, "expected_offset": 5.0, "matches": [match(5.0, "candidate_only")]},
                {"density": 40, "expected_offset": None, "matches": [match(1.0)]},
                {"density": 40, "expected_offset": None, "matches": []}]
        found, controls = self.calibration.fingerprint_summary(rows)
        self.assertEqual((found["count"], found["cases"]), (1, 3))
        self.assertEqual((controls["count"], controls["cases"]), (1, 2))
        self.assertEqual(found["wilson_95"], interval_record(1, 3))

    def test_false_match_summary_records_split_and_intervals(self):
        if importlib.util.find_spec("soundfile") is None:
            self.skipTest("soundfile is needed for the false-match benchmark")
        import false_match
        manifest = {"benchmark_id": "tiny", "date": "2026-10-23",
                    "categories": {"same_prompt_regeneration": {}}}
        observation = {name: {"match": False} for name in false_match.DEFINITIONS}
        results = [{"category": "same_prompt_regeneration", "observation": observation}] * 4
        record = false_match.summarize(manifest, results)["audfprint"]
        self.assertEqual(record["split"], "held_out")
        self.assertEqual(record["categories"]["same_prompt_regeneration"],
                         {"pairs": 4, "false_matches": 0, "wilson_95": {"lower": 0.0, "upper": 0.49}})


if __name__ == "__main__":
    unittest.main()
