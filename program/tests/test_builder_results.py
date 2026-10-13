import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from builder_results import attention_items, attention_workflow_results


class AttentionDisplayTests(unittest.TestCase):
    def test_hides_explicit_successes_and_keeps_failures_and_unassessed_checks(self):
        response = {
            "workflow_assessment": {"rule_results": [
                {"rule_id": "ok", "level": "required", "kind": "artifact_presence",
                 "status": "satisfied", "reason": "Present."},
                {"rule_id": "missing", "level": "required", "kind": "artifact_presence",
                 "status": "missing", "reason": "No usable file."},
                {"rule_id": "unassessed", "level": "conditional", "kind": "artifact_presence",
                 "status": "not_assessed", "reason": "Parser did not produce usable data."},
                {"rule_id": "optional", "level": "optional", "kind": "artifact_presence",
                 "status": "missing", "reason": "Optional file absent."},
                {"rule_id": "optional-unassessed", "level": "optional",
                 "kind": "artifact_presence", "status": "not_assessed",
                 "reason": "An optional supplied file could not be parsed."},
                {"rule_id": "n-a", "level": "required", "kind": "artifact_presence",
                 "status": "not_applicable", "reason": "Not applicable."},
            ]},
            "assessment_checks": {
                "relationship_integrity": [
                    {"rule": "source_time_order_valid", "passed": True},
                    {"rule": "target_time_within_duration", "passed": False,
                     "source_hash": "a" * 64, "target_hash": "b" * 64},
                ],
                "edit_derivation": [
                    {"status": "matched", "reason": "Reconstructed."},
                    {"status": "contradicted", "reason": "Declared model conflicts."},
                    {"status": "not_applicable", "reason": "Unsupported process."},
                    {"status": "unavailable", "reason": "Parameters missing."},
                ],
                "integrity_layers": [
                    {"status": "corroborated", "contributes_to_integrity": False,
                     "check_count": 1, "reason": "Supportive only."},
                    {"status": "unavailable", "contributes_to_integrity": False,
                     "check_count": 0, "reason": "No optional attestation."},
                ],
            },
        }
        issues = attention_items(response, {
            "a" * 64: "source audio.wav",
            "b" * 64: "final audio.wav",
        })
        explanations = [item["explanation"] for item in issues]
        self.assertEqual(len(issues), 6)
        self.assertNotIn("Present.", explanations)
        self.assertNotIn("Optional file absent.", explanations)
        self.assertNotIn("Not applicable.", explanations)
        self.assertIn("No usable file.", explanations)
        self.assertIn("Parser did not produce usable data.", explanations)
        self.assertIn("An optional supplied file could not be parsed.", explanations)
        self.assertIn("Declared model conflicts.", explanations)
        self.assertIn("Parameters missing.", explanations)
        self.assertTrue(any("source audio.wav → final audio.wav" in item["affected"]
                            for item in issues))
        self.assertTrue(all(item["suggestion"] for item in issues))

    def test_workflow_unknown_status_is_not_assumed_to_fail_or_pass(self):
        rows = attention_workflow_results([
            {"rule_id": "new", "level": "required", "kind": "declaration_presence",
             "status": "pending_review", "reason": "New evaluator state."},
        ])
        self.assertEqual(len(rows), 1)
        self.assertIn("Needs review", rows[0]["display_status"])
        self.assertIn("not assumed to pass or fail", rows[0]["suggestion"])

    def test_parser_statuses_are_explicit(self):
        response = {"findings": [
            {"code": "PARSER_OBSERVATION", "parser_status": "parsed",
             "message": "Parser success."},
            {"code": "PARSER_OBSERVATION", "parser_status": "partial",
             "message": "Parser produced warnings."},
            {"code": "PARSER_OBSERVATION", "parser_status": "not_assessed",
             "message": "No parser applies."},
            {"code": "PARSER_OBSERVATION", "parser_status": "failed",
             "evidence_hash": "c" * 64, "message": "Malformed evidence."},
            {"code": "PARSER_FAILED", "evidence_hash": "c" * 64,
             "message": "Duplicate parser failure."},
        ]}
        issues = attention_items(response, {"c" * 64: "metadata.xml"})
        self.assertEqual(len(issues), 2)
        self.assertEqual({item["explanation"] for item in issues},
                         {"Parser produced warnings.", "Malformed evidence."})

    def test_empty_attention_list_has_no_implicit_passes(self):
        response = {"workflow_assessment": {"rule_results": [
            {"rule_id": "ok", "level": "required", "status": "satisfied"},
        ]}, "assessment_checks": {"relationship_integrity": [
            {"rule": "valid", "passed": True},
        ]}}
        self.assertEqual(attention_items(response), [])
