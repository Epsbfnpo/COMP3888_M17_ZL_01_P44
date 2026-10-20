"""S02/S03/S05/S06 policy regressions, including real BWF/report parsing."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import run
from music_target.contract import validate
from music_target.assessment_passes import AIDisclosurePass, assess_axes
from music_target.evidence_audit import aggregate_cross_evidence
from music_target.scoring_profile import load_default_scoring_profile, load_scoring_profile, ScoringProfileError
import test_production_documents as pd
from test_audio_diagnostics import fixture


class FactAggregationTests(unittest.TestCase):
    def test_any_failure_wins_regardless_of_copy_count_order_or_local_document_labels(self):
        bad = {"rule": "sound_report_start_timecode_matches_recorder", "audio_hash": "take",
               "evidence_tier": "recorder_metadata", "evidence_hash": "bad", "passed": False,
               "declared_seconds": 2, "recorded_seconds": 0}
        good = dict(bad, evidence_hash="good", passed=True, declared_seconds=0)
        for copies in (1, 3, 10, 30):
            for duplicate in (good, bad):
                rows = [bad, good] + [dict(duplicate, evidence_hash=f"copy-{index}") for index in range(copies)]
                original = copy.deepcopy(rows)
                facts = aggregate_cross_evidence(rows)
                reverse = aggregate_cross_evidence(rows[::-1])
                self.assertEqual(len(facts), 1)
                self.assertIs(facts[0]["passed"], False)
                self.assertTrue(facts[0]["conflicting_outcomes"])
                self.assertEqual(facts[0]["fact_id"], reverse[0]["fact_id"])
                self.assertEqual(facts[0]["passed"], reverse[0]["passed"])
                self.assertEqual(rows, original)

    def test_renaming_cue_or_selection_does_not_add_votes(self):
        for field in ("cue_id", "selection_id", "master_mob"):
            rows = [{"rule": "range_within_audio", "target_hash": "take", field: label, "passed": passed}
                    for label, passed in (("original", False), ("renamed_copy", True))]
            facts = aggregate_cross_evidence(rows)
            self.assertEqual(len(facts), 1)
            self.assertIs(facts[0]["passed"], False)

    def test_different_recordings_remain_different_facts(self):
        facts = aggregate_cross_evidence([{"rule": "scene", "audio_hash": digest, "passed": True}
                                          for digest in ("take1", "take2")])
        self.assertEqual(len(facts), 2)
        same_group = aggregate_cross_evidence([
            {"rule": "source_group", "audio_hashes": ["take1", "take2"], "passed": True},
            {"rule": "source_group", "audio_hashes": ["take2", "take1"], "passed": False}])
        self.assertEqual(len(same_group), 1)
        self.assertIs(same_group[0]["passed"], False)

    def test_unavailable_verified_tier_cannot_promote_an_assessed_document_fact(self):
        facts = aggregate_cross_evidence([
            {"rule": "same_fact", "audio_hash": "take", "passed": True, "evidence_tier": "submitter_document"},
            {"rule": "same_fact", "audio_hash": "take", "passed": None, "evidence_tier": "signed_attestation"}])
        self.assertEqual(facts[0]["assessed_evidence_tiers"], ["submitter_document"])
        self.assertEqual(facts[0]["passed"], True)


class ProductionScoringRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.harness = types.SimpleNamespace(folder=self.folder, take=pd.recorder_wav(self.folder, *pd.TAKE))

    def report(self, correct, note):
        row = ["2T01.WAV", "2", "01", "00:00:02", "08:49:43:00" if correct else "08:49:45:00",
               '"MixL"', '"MixR"', '"Boom"', note]
        digest = pd.store(self.folder, pd.sound_report("18/12/24", [row]), "csv")
        return digest, "text/sound-report", "csv"

    def score(self, documents):
        request = pd.ProductionDocumentTests.request(self.harness, documents)
        response = run.evaluate(request, self.folder, "native")
        validate(response, "scoring-response.schema.json")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        return response

    def test_good_and_bad_cosmetic_report_copies_cannot_move_integrity(self):
        original = [self.report(True, "correct"), self.report(False, "incorrect")]
        baseline = self.score(original)
        baseline_layer = next(row for row in baseline["assessment_checks"]["integrity_layers"]
                              if row["layer"] == "cross_evidence_support")
        self.assertIsNotNone(baseline["axes"]["integrity"]["value"])
        for correct in (True, False):
            for count in (1, 3, 10, 30):
                copies = [self.report(correct, f"cosmetic-{correct}-{index}") for index in range(count)]
                self.assertEqual(len({digest for digest, _, _ in original + copies}), count + 2)
                response = self.score(original + copies)
                self.assertEqual(response["axes"]["integrity"]["value"], baseline["axes"]["integrity"]["value"])
                self.assertEqual(response["axes"]["integrity"]["confidence"], baseline["axes"]["integrity"]["confidence"])
                layer = next(row for row in response["assessment_checks"]["integrity_layers"]
                             if row["layer"] == "cross_evidence_support")
                self.assertEqual(layer["value"], baseline_layer["value"])
                self.assertEqual(layer["check_count"], baseline_layer["check_count"])
                facts = response["assessment_checks"]["cross_evidence_scored_facts"]
                timecode = next(row for row in facts if row["rule"] == "sound_report_start_timecode_matches_recorder")
                self.assertTrue(timecode["conflicting_outcomes"])
                self.assertIs(timecode["passed"], False)
                explanation = next(row for row in response["assessment_checks"]["score_explanations"]
                                   if row["axis"] == "integrity")
                self.assertEqual(len([row for row in explanation["contradictions"]
                                      if row["rule"] == timecode["rule"]]), 1)

    def test_embedded_metadata_can_release_integrity_with_a_non_scoring_origin_finding(self):
        response = self.score([self.report(True, "consistent synthetic BWF")])
        axis = response["axes"]["integrity"]
        self.assertAlmostEqual(axis["value"], 1.0)
        self.assertEqual(axis["confidence"], 0.533)
        warnings = [row for row in response["findings"] if row["code"] == "RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED"]
        self.assertTrue(warnings)
        self.assertTrue(any(row["evidence_hash"] == self.harness.take for row in warnings))
        for warning in warnings:
            self.assertEqual(warning["severity"], "info")
            self.assertIs(warning["scoring_eligible"], False)
            self.assertIsNone(warning["integrity_contribution"])
            self.assertIsNone(warning["recorder_origin_authenticated"])
        layers = [row for row in response["assessment_checks"]["integrity_layers"]
                  if row["contributes_to_integrity"] and row["value"] is not None]
        self.assertAlmostEqual(axis["value"], sum(row["value"] * row["weight"] for row in layers) /
                               sum(row["weight"] for row in layers))
        decision = response["assessment_checks"]["integrity_decision"][0]
        self.assertEqual(decision["release_reason"], "qualifying_cross_evidence")
        self.assertEqual(decision["status"], "consistent")

    def test_metadata_contradiction_remains_scored_and_clearly_marked(self):
        response = self.score([self.report(False, "incorrect timecode")])
        self.assertIsNotNone(response["axes"]["integrity"]["value"])
        self.assertLess(response["axes"]["integrity"]["value"], 1)
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["status"], "contradicted")

    def test_default_severe_ceiling_still_applies_when_other_qualifying_evidence_exists(self):
        request = pd.ProductionDocumentTests.request(self.harness, [self.report(True, "support")])
        extra = pd.recorder_wav(self.folder, *pd.TAKE, seed=99)
        request["objects"][extra] = f"objects/{extra}.wav"
        request["chain"]["artefacts"].insert(0, {"artefact_hash": extra, "artefact_type": "audio/reference",
                                                "attributes": {}, "evidence": []})
        request["chain"]["artefacts"][-1]["evidence"].append({"hash": extra, "relationship_type": "input_to", "attributes": {}})
        path = self.folder / request["objects"][extra]
        path.write_bytes(path.read_bytes() + b"tampered")
        response = run.evaluate(request, self.folder, "native")
        validate(response, "scoring-response.schema.json")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        self.assertEqual(response["axes"]["integrity"]["value"], 0.25)
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["status"], "contradicted")
        self.assertTrue(response["assessment_checks"]["severe_contradiction_cap"][0]["value_lowered"])


class ReleaseAndDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.request = fixture(self.folder)
        self.request["chain"]["artefacts"][-1]["evidence"][0]["attributes"] = {
            "input_sample_rate_hz": 22050, "output_sample_rate_hz": 22050}

    def score(self, request):
        response = run.evaluate(copy.deepcopy(request), self.folder, "native")
        validate(response, "scoring-response.schema.json")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        return response

    def test_ordinary_contradiction_does_not_release_previously_withheld_integrity(self):
        baseline = self.score(self.request)
        self.request["chain"]["artefacts"][-1]["attributes"] = {"technical": {"sample_rate_hz": 44100}}
        contradicted = self.score(self.request)
        self.assertIsNone(baseline["axes"]["integrity"]["value"])
        self.assertIsNone(contradicted["axes"]["integrity"]["value"])
        self.assertEqual(contradicted["axes"]["integrity"]["confidence"], baseline["axes"]["integrity"]["confidence"])
        decision = contradicted["assessment_checks"]["integrity_decision"][0]
        self.assertEqual(decision["status"], "contradicted")
        self.assertEqual(decision["release_reason"], "insufficient_qualifying_cross_evidence")
        self.assertFalse(decision["ordinary_contradiction_release_allowed"])
        self.assertEqual(baseline["axes"]["integrity"]["status"], "insufficient_evidence")
        self.assertEqual(contradicted["axes"]["integrity"]["status"], "contradicted")

    def tamper_source(self):
        source = self.request["chain"]["artefacts"][0]["artefact_hash"]
        path = self.folder / self.request["objects"][source]
        path.write_bytes(path.read_bytes() + b"tampered")

    def test_severe_contradiction_releases_the_cap_without_qualifying_evidence(self):
        self.tamper_source()
        response = self.score(self.request)
        integrity = response["axes"]["integrity"]
        decision = response["assessment_checks"]["integrity_decision"][0]
        cap = response["assessment_checks"]["severe_contradiction_cap"][0]
        self.assertEqual(integrity["value"], min(cap["weighted_mean"], 0.25))
        self.assertEqual(integrity["status"], "contradicted")
        self.assertEqual(decision["release_reason"], "severe_contradiction_cap")
        self.assertTrue(decision["severe_contradiction_present"])
        self.assertFalse(decision["qualifying_cross_evidence_assessed"])
        self.assertIn("object_hash_bound", cap["triggered_rules"])

    def test_profile_without_severe_release_reproduces_withheld_value(self):
        profile = copy.deepcopy(load_default_scoring_profile())
        del profile["integrity"]["withholding_rule"]["release_capped_value_on_severe_contradiction"]
        path = self.folder / "v3-profile.json"
        path.write_text(json.dumps(profile))
        self.tamper_source()
        response = self.score_with_profile(load_scoring_profile(path))
        self.assertIsNone(response["axes"]["integrity"]["value"])
        self.assertEqual(response["axes"]["integrity"]["status"], "contradicted")
        decision = response["assessment_checks"]["integrity_decision"][0]
        self.assertEqual(decision["severe_contradiction_release"], "disabled")
        self.assertEqual(decision["release_reason"], "insufficient_qualifying_cross_evidence")

    def score_with_profile(self, profile):
        import music_target.assessment_passes as passes
        original = passes.load_default_scoring_profile
        passes.load_default_scoring_profile = lambda: profile
        try:
            return self.score(self.request)
        finally:
            passes.load_default_scoring_profile = original

    def test_complete_recorded_and_complete_ai_declarations_both_mean_complete_coverage(self):
        for method in ("recorded", "ai-generated"):
            request = copy.deepcopy(self.request)
            for node in request["chain"]["artefacts"]:
                node["attributes"]["creation_method"] = method
            response = self.score(request)
            self.assertEqual(response["axes"]["ai_disclosure"]["value"], 1)
            coverage = response["assessment_checks"]["ai_disclosure_coverage"][0]
            self.assertEqual((coverage["declared_audio_count"], coverage["assessed_audio_count"]), (2, 2))
            self.assertFalse(coverage["content_ai_detection_performed"])
            self.assertFalse(coverage["declaration_truth_verified"])
            self.assertIsNone(coverage["ai_generated_probability"])
            self.assertTrue(any(row["code"] == "AI_DISCLOSURE_IS_DECLARATION_COVERAGE" for row in response["findings"]))
            explanation = next(row for row in response["assessment_checks"]["score_explanations"]
                               if row["axis"] == "ai_disclosure")
            self.assertEqual(explanation["display_name"], "Creation-method disclosure coverage")

    def test_partial_and_missing_declarations_preserve_numeric_definition_and_confidence(self):
        nodes = self.request["chain"]["artefacts"]
        nodes[0]["attributes"]["creation_method"] = "recorded"
        partial = self.score(self.request)
        self.assertEqual(partial["axes"]["ai_disclosure"]["value"], 0.5)
        self.assertEqual(partial["axes"]["ai_disclosure"]["confidence"], 0.8)
        del nodes[0]["attributes"]["creation_method"]
        missing = self.score(self.request)
        self.assertIsNone(missing["axes"]["ai_disclosure"]["value"])
        self.assertEqual(missing["assessment_checks"]["ai_disclosure_coverage"][0]["declared_audio_count"], 0)

    def test_legacy_support_key_is_an_alias_without_a_contradiction_bypass(self):
        profile = copy.deepcopy(load_default_scoring_profile())
        rule = profile["integrity"]["withholding_rule"]
        rule["require_substantive_support_or_contradiction"] = rule.pop("require_substantive_support")
        path = self.folder / "profile.json"
        path.write_text(json.dumps(profile))
        loaded = load_scoring_profile(path)
        digest = "a" * 64
        native = {"artefacts": [{"artefact_hash": digest, "artefact_type": "audio/master", "attributes": {}, "evidence": []}],
                  "final_artefact_hash": digest}
        chain = types.SimpleNamespace(artefacts={digest: types.SimpleNamespace()})
        integrity, _, _, _, checks, _ = assess_axes(native, chain, {digest}, set(),
            {digest: {"checks": [{"rule": "technical.sample_rate_hz_matches_observed", "passed": False,
                                   "evidence_hash": digest}]}},
            [{"checks": [{"rule": "output_sample_rate_hz_matches_file", "passed": True}]}], {}, {}, scoring_profile=loaded)
        self.assertIsNone(integrity["value"])
        self.assertFalse(checks["integrity_decision"][0]["ordinary_contradiction_release_allowed"])
        rule["require_substantive_support"] = True
        path.write_text(json.dumps(profile))
        with self.assertRaises(ScoringProfileError):
            load_scoring_profile(path)


if __name__ == "__main__":
    unittest.main()
