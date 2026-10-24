import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from builder_results import (INSUFFICIENT_SUPPORT_LABEL, attention_items, attention_workflow_results,
                             audio_diagnostic_lines, axis_value_label, music_semantics_lines)


class AttentionDisplayTests(unittest.TestCase):
    def test_contradicted_integrity_shows_its_count_beside_the_value(self):
        # The count comes from integrity_decision, which is kept when explanations are omitted.
        response = {"assessment_checks": {"integrity_decision": [
            {"status": "contradicted", "contradiction_count": 2, "assessed_fact_count": 122}]}}
        contradicted = {"value": 0.990, "status": "contradicted"}
        self.assertEqual(axis_value_label("integrity", contradicted, response), "0.990 (contradicted: 2)")
        self.assertEqual(axis_value_label("integrity", {"value": None, "status": "contradicted"}, response),
                         "Not assessed (contradicted: 2)")
        self.assertEqual(axis_value_label("integrity", {"value": 1.0, "status": "consistent"}, response), "1.000")
        self.assertEqual(axis_value_label("completeness", {"value": 0.5}, response), "0.500")
        self.assertEqual(axis_value_label("integrity", contradicted, {}), "0.990 (contradicted)")

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


SOURCE, TARGET = "1" * 64, "2" * 64


def _row(provider, **extra):
    row = {"provider": provider, "relation_id": "r1", "selection_id": None, "source_hash": SOURCE,
           "target_hash": TARGET, "relationship_type": "excerpted_from", "status": "inconclusive",
           "reason_code": "bounded_observation", "message": "Observation.", "matches": [], "metrics": {}}
    row.update(extra)
    return row


class AudioSummaryTests(unittest.TestCase):
    """The GUI summaries read the arrays the evaluator actually emits."""

    names = {SOURCE: "source.wav", TARGET: "target.wav"}

    def response(self, **checks):
        base = {"audio_diagnostic_summary": [{"completed_provider_tasks": 4, "unavailable_provider_tasks": 0}]}
        return {"assessment_checks": {**base, **checks}}

    def test_music_semantics_are_read_from_audio_semantics(self):
        row = _row("music_semantics", metrics={
            "correspondence_assessment": {"status": "supported_within_profile", "rejection_reasons": []},
            "transposition_semitones": 2, "tempo_ratio": 1.0983, "harmonic_similarity_z": 30.38,
            "source": {"key": {"tonic": "C#", "mode": "major"}, "tempo_bpm": 136.0},
            "target": {"key": {"tonic": "C", "mode": "minor"}, "tempo_bpm": 143.6}})
        text = "\n".join(music_semantics_lines(self.response(audio_semantics=[row]), self.names))
        self.assertIn("source.wav → target.wav [excerpted_from]", text)
        self.assertIn("Correspondence: supported within profile", text)
        self.assertIn("+2 semitone(s)", text)
        self.assertIn("tempo ratio 1.098", text)
        self.assertIn("Source C# major, 136 BPM → target C minor, 144 BPM", text)

    def test_abstained_semantics_show_their_reasons(self):
        row = _row("music_semantics", metrics={"correspondence_assessment": {
            "status": "abstained", "rejection_reasons": ["low_harmonic_similarity", "a", "b", "c"]}})
        text = "\n".join(music_semantics_lines(self.response(audio_semantics=[row]), self.names))
        self.assertIn("Correspondence: abstained (low harmonic similarity, a, b, +1 more)", text)

    def test_fingerprints_show_the_strongest_offset_and_agreement(self):
        audfprint = _row("audfprint", matches=[
            {"source_minus_target_offset_seconds": 25.008, "matched_token_pairs": 33,
             "support_assessment": {"status": "supported_within_profile"}}])
        spectromap = _row("spectromap", matches=[
            {"source_minus_target_offset_seconds": 32.1, "matched_token_pairs": 13},
            {"source_minus_target_offset_seconds": 24.99, "matched_token_pairs": 34}])
        agreement = _row("fingerprint_agreement", matches=[
            {"source_minus_target_offset_seconds": 24.995, "audfprint_votes": 33, "spectromap_votes": 34}],
            metrics={"expected_chance_agreements": 0.27})
        text = "\n".join(audio_diagnostic_lines(self.response(
            audio_fingerprint=[audfprint, spectromap], audio_fingerprint_agreement=[agreement]), self.names))
        self.assertIn("audfprint: strongest offset source − target = 25.01 s (33 matching token pairs; "
                      "supported within profile)", text)
        self.assertIn("spectromap: strongest offset source − target = 24.99 s", text)
        self.assertIn("1 weaker candidate offset(s)", text)
        self.assertIn("both algorithms agree at source − target = 25.00 s", text)
        self.assertIn("expected by chance ≈ 0.27", text)

    def test_insufficient_signal_is_labelled_as_a_normal_outcome(self):
        silent = dict(status="unavailable", reason_code="silent_signal", message="Selected audio is silent.")
        rows = [_row("audfprint", **silent), _row("spectromap", **silent)]
        agreement = _row("fingerprint_agreement", status="unavailable", reason_code="provider_inputs_unavailable",
                         message="Agreement needs both analyses.")
        failed = _row("librosa", status="unavailable", reason_code="provider_failed", message="IndexError")
        lines = audio_diagnostic_lines(self.response(audio_fingerprint=rows, audio_fingerprint_agreement=[agreement],
                                                     audio_structure=[failed]), self.names)
        muted = [line for line in lines if INSUFFICIENT_SUPPORT_LABEL in line]
        self.assertEqual(len(muted), 3)  # both fingerprints and their agreement
        self.assertIn("    - librosa: not run (provider_failed): IndexError", lines)


class FinalC2paAttentionTests(unittest.TestCase):
    final = "3" * 64

    def items(self, state, severity, extra=()):
        findings = [{"code": "C2PA_FINAL_ATTESTATION", "severity": severity, "evidence_hash": self.final,
                     "final_c2pa_state": state, "message": f"State {state}."}, *extra]
        return attention_items({"findings": findings, "assessment_checks": {}}, {})

    def test_invalid_final_c2pa_is_one_attention_item(self):
        detail = {"code": "C2PA_CONTENT_BINDING_FAILED", "severity": "medium", "evidence_hash": self.final,
                  "scope": "final", "message": "Detail."}
        items = self.items("invalid", "medium", [detail])
        self.assertEqual([item["status"] for item in items], ["Final file's C2PA is invalid"])

    def test_absent_or_trusted_final_c2pa_needs_no_attention(self):
        self.assertEqual(self.items("absent", "info"), [])
        self.assertEqual(self.items("trusted_c2pa", "info"), [])

    def test_supporting_c2pa_failures_are_still_listed(self):
        detail = {"code": "C2PA_CONTENT_BINDING_FAILED", "severity": "low", "evidence_hash": "4" * 64,
                  "scope": "supporting", "message": "Supporting detail."}
        statuses = [item["status"] for item in self.items("absent", "info", [detail])]
        self.assertEqual(len(statuses), 1)


class BandwidthLineTests(unittest.TestCase):
    def test_each_file_gets_its_bandwidth_reason(self):
        reason = "The audio stops at 16.4 kHz, but a 320 kbps MP3 usually reaches about 20.0 kHz."
        rows = [_row("bandwidth", source_hash=SOURCE, target_hash=SOURCE, relationship_type="single_file",
                     metrics={"assessment": {"status": "content_band_lower_than_bitrate_suggests", "reason": reason}}),
                _row("bandwidth", source_hash=TARGET, target_hash=TARGET, relationship_type="single_file",
                     status="unavailable", reason_code="decoder_length_mismatch", message="Not decoded.")]
        lines = audio_diagnostic_lines({"assessment_checks": {
            "audio_diagnostic_summary": [{"completed_provider_tasks": 1}], "audio_bandwidth": rows}},
            {SOURCE: "song.mp3", TARGET: "other.mp3"})
        text = "\n".join(lines)
        self.assertIn("Content bandwidth per file (heuristic; uncalibrated):", text)
        self.assertIn("- song.mp3: " + reason, text)
        self.assertIn("- other.mp3: not run (decoder_length_mismatch): Not decoded.", text)


class CalibrationLineTests(unittest.TestCase):
    def test_calibration_is_listed_once_per_provider(self):
        calibration = {"status": "bounded_calibration", "transforms_tested": ["excerpt"],
                       "not_tested": ["covers"], "false_match_rates": {"categories": {
                           "same_prompt_regeneration": {"pairs": 4, "false_matches": 0,
                                                        "wilson_95": {"lower": 0.0, "upper": 0.49}}}}}
        rows = [_row("audfprint", calibration=calibration), _row("spectromap", calibration={
            "status": "exploratory_uncalibrated", "transforms_tested": [], "not_tested": ["all"]})]
        lines = audio_diagnostic_lines({"assessment_checks": {
            "audio_diagnostic_summary": [{"completed_provider_tasks": 2}], "audio_fingerprint": rows}}, {})
        text = "\n".join(lines)
        self.assertIn("- audfprint: bounded calibration; tested: excerpt; not tested: covers; "
                      "benchmark: same prompt regeneration 0/4 false (95% CI 0-49%)", text)
        self.assertIn("- spectromap: exploratory uncalibrated", text)
