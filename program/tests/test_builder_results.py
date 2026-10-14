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

    def test_latest_main_audio_diagnostics_show_only_issues_and_unassessed_checks(self):
        response = {"assessment_checks": {
            "audio_alignment": [
                {"status": "corroborated", "target_hash": "b" * 64,
                 "source_hashes": ["a" * 64], "reason": "Alignment supported."},
                {"status": "unavailable", "target_hash": "c" * 64,
                 "reason": "Resource budget exhausted."},
            ],
            "comp_verification": [
                {"status": "contradicted", "target_hash": "d" * 64,
                 "source_hashes": ["e" * 64], "reason": "Declared comp does not match.",
                 "local_mismatch_regions": [
                     {"start_seconds": 4.25, "end_seconds": 4.75},
                 ]},
                {"status": "not_applicable", "target_hash": "f" * 64,
                 "reason": "Not a comp."},
            ],
            "decoy_source": [
                {"status": "clear", "target_hash": "1" * 64, "reason": "No decoy."},
                {"status": "suspected_decoy", "target_hash": "2" * 64,
                 "source_hash": "3" * 64, "reason": "Source has no measured contribution."},
            ],
            "cross_evidence": [
                {"rule": "binding", "passed": None, "status": "unavailable",
                 "evidence_hash": "4" * 64, "reason": "Binding absent."},
            ],
        }}
        names = {
            "a" * 64: "source.wav", "b" * 64: "aligned.wav",
            "c" * 64: "unknown.wav", "d" * 64: "comp.wav",
            "e" * 64: "take.wav", "2" * 64: "final.wav",
            "3" * 64: "unused.wav", "4" * 64: "metadata.xml",
        }
        issues = attention_items(response, names)
        self.assertEqual(len(issues), 4)
        explanations = [item["explanation"] for item in issues]
        self.assertTrue(any(item.startswith("Declared comp does not match.")
                            for item in explanations))
        self.assertTrue(any(item == "Resource budget exhausted." for item in explanations))
        self.assertTrue(any(item == "Source has no measured contribution."
                            for item in explanations))
        self.assertTrue(any(item == "Binding absent." for item in explanations))
        self.assertTrue(all(item["suggestion"] for item in issues))
        self.assertTrue(any("take.wav → comp.wav" == item["affected"] for item in issues))
        self.assertTrue(any("4.25–4.75 s" in item["explanation"] for item in issues))

    def test_latest_main_quarantined_inputs_are_reported_as_unassessed(self):
        response = {"findings": [
            {"code": "UNKNOWN_ARTEFACT_TYPE_SKIPPED",
             "evidence_hash": "a" * 64,
             "message": "Unknown file type was skipped."},
            {"code": "UNKNOWN_RELATIONSHIP_TYPE_SKIPPED",
             "source_hash": "a" * 64, "target_hash": "b" * 64,
             "message": "Unknown relationship was skipped."},
            {"code": "RELATIONSHIP_ENDPOINTS_SKIPPED",
             "source_hash": "a" * 64, "target_hash": "b" * 64,
             "message": "Relationship endpoints were skipped."},
            {"code": "PARTIAL_INPUT_COVERAGE",
             "message": "Some submitted inputs were not evaluated."},
        ]}
        issues = attention_items(response, {
            "a" * 64: "source.wav", "b" * 64: "master.wav"})
        self.assertEqual(len(issues), 4)
        self.assertTrue(all("not assessed" in issue["status"].lower()
                            or "incomplete" in issue["status"].lower()
                            for issue in issues))
        self.assertTrue(all(issue["suggestion"] for issue in issues))
