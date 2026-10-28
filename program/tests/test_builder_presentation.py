"""PDF report requirements: interpretation never changes the scoring response."""

import copy
import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from builder_presentation import build_report, report_text


class PresentationTests(unittest.TestCase):
    def response(self, outcome="content_found_elsewhere"):
        return {
            "execution_status": "succeeded",
            "axes": {"integrity": {"value": 1.0, "confidence": 0.8,
                                   "availability": "partial", "status": "consistent", "reasoning": None}},
            "assessment_checks": {
                "integrity_decision": [{"contradiction_count": 0, "assessed_fact_count": 20,
                                         "not_completed_count": 0}],
                "support_breadth": [{"applicable": True, "required_recording_count": 13,
                                    "supported": 0, "contradicted": 1, "no_qualifying_evidence": 12,
                                    "coverage_scope": "confirmed"}],
                "audio_declared_range": [{
                    "source_hash": "source", "target_hash": "target", "relationship_type": "edited_from",
                    "selection_id": None, "outcome": outcome,
                    "declared_source_range_seconds": [7, 11], "declared_target_range_seconds": [0, 4],
                    "best_source_range_seconds": [2, 6], "search_supported_offsets_seconds": [2, 10],
                    "diagnostic_ids": ["pair:audfprint", "pair:spectromap"],
                    "scoring_eligible": False,
                }],
            }, "findings": [],
        }

    def test_high_score_does_not_hide_report_only_audio_review(self):
        response = self.response()
        original = copy.deepcopy(response)
        model = build_report(response, {"source": "take.wav", "target": "mix.wav"})
        self.assertIn("1 grouped item", model["summary"][-1])
        self.assertEqual(model["axes"][0]["label"], "1.000")
        text = report_text(model)
        for expected in ("Claimed segment requires review", "take.wav -> mix.wav", "7-11 s",
                         "2-6 s", "[2, 10]", "does not change Integrity", "excludes optional audio"):
            self.assertIn(expected, text)
        self.assertEqual(response, original)
        self.assertEqual(json.loads(model["raw_json"]), original)

    def test_outcome_not_finding_code_controls_no_match_wording(self):
        response = self.response("content_not_located")
        response["assessment_checks"]["audio_declared_range"][0].pop("best_source_range_seconds")
        response["assessment_checks"]["audio_declared_range"][0].pop("search_supported_offsets_seconds")
        model = build_report(response)
        self.assertEqual(model["audio"][0]["title"], "No conclusive match found")
        self.assertIn("short dialogue", model["attention"])
        self.assertNotIn("Claimed segment requires review", model["attention"])

    def test_timeout_and_insufficient_signal_are_not_mismatches(self):
        for outcome, title in (("search_unavailable", "Analysis incomplete"),
                               ("insufficient_support", "Insufficient signal")):
            with self.subTest(outcome=outcome):
                model = build_report(self.response(outcome))
                self.assertEqual(model["audio"][0]["title"], title)
                self.assertNotIn("Check failed", model["attention"])

    def test_high_score_core_contradiction_count_is_prominent(self):
        response = self.response()
        response["axes"]["integrity"]["value"] = 0.994
        response["assessment_checks"]["integrity_decision"][0]["contradiction_count"] = 2
        model = build_report(response)
        self.assertIn("2 contradiction", "\n".join(model["summary"]))
        self.assertIn("contradicted: 2", model["axes"][0]["label"])

    def test_null_score_shows_withholding_reason_not_zero(self):
        response = self.response()
        response["axes"]["integrity"]["value"] = None
        response["assessment_checks"]["integrity_decision"][0]["release_reason"] = "file_checks_not_completed"
        model = build_report(response)
        self.assertEqual(model["axes"][0]["label"], "Not assessed")
        self.assertIn("file_checks_not_completed", model["axes"][0]["reason"])

    def test_coverage_counts_contradicted_recording_as_checked_not_supported(self):
        model = build_report(self.response(), files=[
            {"type": "audio/raw-take"} for _ in range(13)] + [{"type": "project/aaf"}])
        text = "\n".join(model["coverage"])
        self.assertIn("14 artefacts; 13 audio", text)
        self.assertIn("1 of 13 required recordings", text)
        self.assertIn("Consistent: 0; contradicted: 1", text)
        self.assertIn("does not mean correct or passed", text)

    def test_multiple_providers_and_finding_are_one_issue_with_all_citations(self):
        response = self.response()
        item = response["assessment_checks"]["audio_declared_range"][0]
        response["findings"] = [
            {**copy.deepcopy(item), "code": "AUDIO_DECLARED_RANGE_NOT_SUPPORTED", "provider": provider}
            for provider in ("audfprint", "spectromap")]
        model = build_report(response)
        self.assertEqual(len(model["audio"]), 1)
        self.assertEqual(len(model["audio"][0]["references"]), 3)
        self.assertEqual(model["audio"][0]["providers"], ["audfprint", "spectromap"])
        self.assertEqual(len(json.loads(model["raw_json"])["findings"]), 2)

    def test_empty_response_does_not_assert_full_verification(self):
        model = build_report({"execution_status": "succeeded", "axes": {}, "findings": []})
        self.assertIn("no overall pass inferred", "\n".join(model["summary"]))
        self.assertIn("Unchecked evidence", model["attention"])


if __name__ == "__main__":
    unittest.main()
