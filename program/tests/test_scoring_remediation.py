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
from unittest import mock
from music_target import assessment_passes
from music_target.evidence_audit import aggregate_cross_evidence, summarize_evidence_sources, summarize_support_breadth
from music_target.workflow_policy import DEFAULT_POLICY_PATH, load_workflow_policy, required_recording_types
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


class OneVotePerClaimTests(unittest.TestCase):
    def rows(self, report_passed=True, aaf_passed=True, aaf_take="take"):
        return [{"rule": "sound_report_scene_take_matches_recorder", "audio_hash": "take",
                 "evidence_hash": "report", "evidence_tier": "recorder_metadata", "passed": report_passed},
                {"rule": "aaf_scene_take_matches_recorder", "audio_hash": aaf_take,
                 "evidence_hash": "aaf", "evidence_tier": "recorder_metadata", "passed": aaf_passed}]

    def test_same_claim_in_two_documents_is_one_fact(self):
        self.assertEqual(len(aggregate_cross_evidence(self.rows())), 2)
        facts = aggregate_cross_evidence(self.rows(), one_vote_per_claim=True)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["identity"], {"audio_hash": "take", "claim": "scene_take"})
        self.assertEqual(facts[0]["document_count"], 2)
        self.assertEqual(facts[0]["delivered_by"], "submitter")

    def test_either_document_disagreeing_fails_the_merged_fact(self):
        for report_passed, aaf_passed in ((False, True), (True, False)):
            facts = aggregate_cross_evidence(self.rows(report_passed, aaf_passed), one_vote_per_claim=True)
            self.assertIs(facts[0]["passed"], False)
            self.assertTrue(facts[0]["conflicting_outcomes"])

    def test_different_recordings_and_other_rules_are_never_merged(self):
        self.assertEqual(len(aggregate_cross_evidence(self.rows(aaf_take="other"), one_vote_per_claim=True)), 2)
        rows = self.rows() + [
            {"rule": "sound_report_start_timecode_matches_recorder", "audio_hash": "take",
             "evidence_hash": "report", "passed": True},
            {"rule": "aaf_source_range_within_recorded_timecode", "audio_hash": "take",
             "evidence_hash": "aaf", "passed": True}]
        self.assertEqual(len(aggregate_cross_evidence(rows, one_vote_per_claim=True)), 3)

    def test_checks_without_a_repeated_claim_aggregate_exactly_as_before(self):
        rows = [{"rule": "sound_report_scene_take_matches_recorder", "audio_hash": "take",
                 "evidence_hash": "report", "passed": True},
                {"rule": "rin_contributors_overlap_submitter_declaration", "passed": False},
                {"rule": "midi_tempo_matches_declaration", "evidence_hash": "midi", "passed": True}]
        self.assertEqual([fact["passed"] for fact in aggregate_cross_evidence(rows)],
                         [fact["passed"] for fact in aggregate_cross_evidence(rows, one_vote_per_claim=True)])
        self.assertEqual(len(aggregate_cross_evidence(rows)), len(aggregate_cross_evidence(rows, True)))

    def test_profile_flag_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = copy.deepcopy(load_default_scoring_profile())
            self.assertIs(profile["integrity"]["one_vote_per_claim"], True)
            profile["integrity"]["one_vote_per_claim"] = "yes"
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(profile))
            with self.assertRaises(ScoringProfileError):
                load_scoring_profile(path)

    def test_summary_separates_documents_facts_and_sources_and_never_guesses_independence(self):
        facts = aggregate_cross_evidence(self.rows(), one_vote_per_claim=True) + aggregate_cross_evidence(
            [{"rule": "sound_report_start_timecode_matches_recorder", "audio_hash": "take",
              "evidence_hash": "report", "passed": True}])
        parsers = {"aaf": {"parser": "aaf"}, "daw": {"parser": "daw-session",
                                                     "data": {"metadata": {"creator": "Ableton Live 11.3"}}}}
        wavs = {"take": {"embedded_metadata": {"info": {"software": "Recorder OS 9"},
                                               "broadcast_extension": {"originator": "Recorder"}}}}
        summary = summarize_evidence_sources(facts, parsers, wavs, [])[0]
        self.assertEqual((summary["document_count"], summary["fact_count"]), (2, 2))
        self.assertIsNone(summary["independent_source_count"])
        self.assertEqual(summary["independence"], "unknown")
        self.assertEqual(summary["known_common_sources"][0]["source"], "submitter")
        self.assertEqual(summary["known_common_sources"][0]["document_hashes"], ["aaf", "report"])
        self.assertEqual(summary["subjects_with_multiple_documents"],
                         [{"subject_hash": "take", "document_count": 2, "fact_count": 2}])
        self.assertEqual(summary["origin_fields"],
                         [{"hash": "take", "software": "Recorder OS 9", "originator": "Recorder"}])
        self.assertEqual(summary["scoring_effect"], "none_report_only")
        empty = summarize_evidence_sources([], {}, {}, [])[0]
        self.assertEqual((empty["document_count"], empty["known_common_sources"]), (0, []))


class SupportBreadthTests(unittest.TestCase):
    native = {"artefacts": [{"artefact_hash": name, "artefact_type": kind}
                            for name, kind in (("a", "audio/raw-take"), ("b", "audio/raw-take"),
                                               ("c", "audio/raw-track"), ("m", "audio/master"))]}

    def fact(self, subject, passed, tier="recorder_metadata"):
        return {"identity": {"audio_hash": subject, "rule": "r"}, "passed": passed,
                "assessed_evidence_tiers": [tier]}

    def test_supported_contradicted_and_missing_are_kept_apart(self):
        facts = [self.fact("a", True), self.fact("b", False), self.fact("c", True, tier="submitter_document")]
        breadth = summarize_support_breadth(self.native, facts, ("recorder_metadata",),
                                            ["audio/raw-take", "audio/raw-track"])
        self.assertEqual({item["hash"]: item["status"] for item in breadth["assets"]},
                         {"a": "supported", "b": "contradicted", "c": "no_qualifying_evidence"})
        self.assertEqual((breadth["supported"], breadth["contradicted"], breadth["no_qualifying_evidence"]), (1, 1, 1))
        # A contradiction was checked, so it counts as covered, never as missing.
        self.assertAlmostEqual(breadth["checked_share"], 2 / 3)

    def test_no_relevant_recordings_is_not_applicable(self):
        breadth = summarize_support_breadth(self.native, [], ("recorder_metadata",), [])
        self.assertFalse(breadth["applicable"])
        self.assertIsNone(breadth["checked_share"])

    def aaf(self, *missing, ambiguous=(), owner="aaf"):
        return {"evidence_hash": owner, "missing_recordings": [{"tape": tape, "file_name": name}
                                                               for tape, name in missing],
                "ambiguous_recordings": [{"tape": tape, "file_name": name} for tape, name in ambiguous]}

    @staticmethod
    def identified(*hashes):
        return {digest: {"recorder_metadata": {"present": True, "file_name": f"{digest}.WAV"}} for digest in hashes}

    def test_unsubmitted_aaf_take_joins_the_denominator_once(self):
        facts = [self.fact("a", True)]
        native = {"artefacts": [{"artefact_hash": "a", "artefact_type": "audio/raw-take"}]}
        # The same take used twice in one AAF and once in another counts once.
        breadth = summarize_support_breadth(native, facts, ("recorder_metadata",), ["audio/raw-take"],
                                            [self.aaf(("T1", "3T02.WAV")), self.aaf(("t1", "3t02.wav"), owner="b")],
                                            {"a"}, self.identified("a"))
        self.assertEqual((breadth["required_recording_count"], breadth["not_submitted"]), (2, 1))
        self.assertEqual((breadth["material_coverage"], breadth["support_coverage"]), (0.5, 0.5))
        self.assertEqual(breadth["checked_share"], breadth["support_coverage"])
        missing = next(item for item in breadth["assets"] if item["status"] == "not_submitted")
        self.assertEqual((missing["source"], missing["aaf_hashes"]), ("aaf_reference", ["aaf", "b"]))
        # Missing material is never a contradiction.
        self.assertEqual(breadth["contradicted"], 0)

    def test_unmatched_reference_is_provisional_when_a_submission_lacks_identity(self):
        # "b" has no recorder identity (for example a plain FLAC take), so the
        # unmatched AAF take may be that file: it is pending, not missing.
        native = {"artefacts": [{"artefact_hash": name, "artefact_type": "audio/raw-take"} for name in "ab"]}
        breadth = summarize_support_breadth(native, [self.fact("a", True)], ("recorder_metadata",),
                                            ["audio/raw-take"], [self.aaf(("T1", "3T02.WAV"), ambiguous=[("T1", "X")])],
                                            {"a", "b"}, self.identified("a"))
        self.assertEqual((breadth["not_submitted"], breadth["unmatched_references"], breadth["ambiguous_references"]),
                         (0, 1, 1))
        self.assertEqual((breadth["required_recording_count"], breadth["coverage_scope"]), (2, "provisional"))
        self.assertEqual(breadth["material_coverage"], 1.0)

    def test_aaf_takes_do_not_apply_when_the_workflow_requires_no_recordings(self):
        breadth = summarize_support_breadth(self.native, [], ("recorder_metadata",), [],
                                            [self.aaf(("T1", "3T02.WAV"))], set(), {})
        self.assertEqual((breadth["status"], breadth["coverage_scope"], breadth["material_coverage"]),
                         ("not_applicable", "not_applicable", None))

    def test_engine_loads_the_workflow_policy_once(self):
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        real = assessment_passes.load_workflow_policy
        with mock.patch("music_target.engine.load_workflow_policy", wraps=real) as engine_load, \
                mock.patch.object(assessment_passes, "load_workflow_policy", wraps=real) as scoring_load:
            response = run.evaluate(request, BASE / "examples/valid-generated", "native")
        self.assertEqual(response["execution_status"], "succeeded")
        self.assertEqual((engine_load.call_count, scoring_load.call_count), (1, 0))

    def test_workflow_recording_types_come_from_the_workflow_policy(self):
        policy = load_workflow_policy(DEFAULT_POLICY_PATH)
        self.assertEqual(required_recording_types(policy, "multitrack_recording_mix_master"),
                         ["audio/raw-take", "audio/raw-track"])
        self.assertEqual(required_recording_types(policy, "mastering_service_only"), [])
        self.assertEqual(required_recording_types(policy, "no_such_workflow"), [])

    def test_share_must_be_a_number_in_range(self):
        self.assertNotIn("min_supported_asset_share",
                         load_default_scoring_profile()["integrity"]["withholding_rule"])
        for share in (0, 1.5, True, "half"):
            with tempfile.TemporaryDirectory() as directory:
                profile = copy.deepcopy(load_default_scoring_profile())
                profile["integrity"]["withholding_rule"]["min_supported_asset_share"] = share
                path = Path(directory) / "profile.json"
                path.write_text(json.dumps(profile))
                with self.assertRaises(ScoringProfileError):
                    load_scoring_profile(path)


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
        # Two of three layers (0.8 x 2/3), scaled by recorder-metadata forge cost 3 of 5: 0.5 + 0.5 x 0.6.
        self.assertEqual(axis["confidence"], 0.427)
        strength = response["assessment_checks"]["evidence_strength"][0]
        self.assertEqual((strength["subjects_by_basis"], strength["confidence_factor"]), ({"recorder_metadata": 1}, 0.8))
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

    def aaf(self):
        data = pd.aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), [
            ("24Y12M18", "2T01.WAV", "2", "01", pd.TRACKS, "2024-12-18", 31783.5, 25)])
        return pd.store(self.folder, data, "aaf"), "project/aaf", "aaf"

    def test_report_and_aaf_repeating_scene_take_and_tracks_count_once(self):
        response = self.score([self.report(True, "consistent"), self.aaf()])
        checks = response["assessment_checks"]
        facts = checks["cross_evidence_scored_facts"]
        for claim in ("scene_take", "track_names"):
            merged = [fact for fact in facts if fact["identity"].get("claim") == claim]
            self.assertEqual(len(merged), 1, claim)
            self.assertEqual(merged[0]["document_count"], 2)
        # Without one vote per claim the same checks would add two more votes.
        self.assertEqual(len(aggregate_cross_evidence(checks["cross_evidence"])), len(facts) + 2)
        sources = checks["evidence_sources"][0]
        self.assertEqual(sources["document_count"], 2)
        self.assertEqual(sources["independence"], "unknown")
        self.assertEqual(sources["subjects_with_multiple_documents"][0]["subject_hash"], self.harness.take)
        self.assertIn("These 2 documents were provided and uploaded by the same user. They are consistent "
                      "with each other, but it cannot be confirmed if they are independently true.",
                      response["axes"]["integrity"]["reasoning"])

    def test_a_single_document_scores_exactly_as_before(self):
        response = self.score([self.report(True, "consistent")])
        cross = response["assessment_checks"]["cross_evidence"]
        # Only the merged facts' identity label differs; every scored outcome is the same.
        scored = [[(fact["rule"], fact["passed"], fact["assessed_evidence_tiers"], fact["observation_count"])
                   for fact in aggregate_cross_evidence(cross, one_vote_per_claim=merge)] for merge in (False, True)]
        self.assertEqual(scored[0], scored[1])
        self.assertIn("This document was provided and uploaded by the user. It is consistent with the other "
                      "submitted files, but it cannot be confirmed if it is independently true.",
                      response["axes"]["integrity"]["reasoning"])

    def test_source_sentence_does_not_call_contradicted_documents_consistent(self):
        reasoning = self.score([self.report(False, "incorrect timecode")])["axes"]["integrity"]["reasoning"]
        self.assertIn("This document was provided and uploaded by the user. It cannot be confirmed if it is "
                      "independently true.", reasoning)
        self.assertNotIn("It is consistent", reasoning)

    def score_with(self, documents, share=None, extra_take=False):
        request = pd.ProductionDocumentTests.request(self.harness, documents)
        if extra_take:
            # A second recording with no document about it.
            other = pd.recorder_wav(self.folder, "2T02.WAV", "24Y12M18", "2", "02", pd.TRACKS, 31900.0,
                                    "2024-12-18", seed=7)
            request["chain"]["artefacts"].insert(1, {"artefact_hash": other, "artefact_type": "audio/raw-take",
                                                     "attributes": {}, "evidence": []})
            request["chain"]["artefacts"][-1]["evidence"].append(
                {"hash": other, "relationship_type": "derived_from", "attributes": {}})
            request["objects"][other] = f"objects/{other}.wav"
        profile = copy.deepcopy(load_default_scoring_profile())
        if share is not None:
            profile["integrity"]["withholding_rule"]["min_supported_asset_share"] = share
        with mock.patch.object(assessment_passes, "load_default_scoring_profile", return_value=profile):
            response = run.evaluate(request, self.folder, "native")
        validate(response, "scoring-response.schema.json")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        return response

    def test_breadth_is_reported_without_changing_the_score(self):
        response = self.score_with([self.report(True, "consistent")], extra_take=True)
        breadth = response["assessment_checks"]["support_breadth"][0]
        self.assertEqual((breadth["relevant_asset_count"], breadth["supported"], breadth["no_qualifying_evidence"]),
                         (2, 1, 1))
        self.assertEqual((breadth["requirement"], breadth["scoring_effect"]), ("not_set", "none_report_only"))
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["numeric_release"], "released")
        self.assertIn("Strong evidence was checked for 1 of 2 recordings; 1 has no strong evidence.",
                      response["axes"]["integrity"]["reasoning"])

    def test_optional_share_withholds_for_missing_evidence_only(self):
        documents = [self.report(True, "consistent")]
        withheld = self.score_with(documents, share=1.0, extra_take=True)
        decision = withheld["assessment_checks"]["integrity_decision"][0]
        self.assertIsNone(withheld["axes"]["integrity"]["value"])
        self.assertEqual(decision["release_reason"], "insufficient_support_breadth")
        self.assertEqual(withheld["assessment_checks"]["support_breadth"][0]["requirement"], "not_met")
        released = self.score_with(documents, share=0.5, extra_take=True)
        self.assertIsNotNone(released["axes"]["integrity"]["value"])
        self.assertEqual(released["assessment_checks"]["support_breadth"][0]["requirement"], "met")
        # Same value as with no share set: the gate only decides release.
        self.assertEqual(released["axes"]["integrity"]["value"],
                         self.score_with(documents, extra_take=True)["axes"]["integrity"]["value"])

    def test_contradicted_recording_counts_as_checked_not_missing(self):
        plain = self.score_with([self.report(False, "incorrect timecode")])
        gated = self.score_with([self.report(False, "incorrect timecode")], share=1.0)
        breadth = gated["assessment_checks"]["support_breadth"][0]
        self.assertEqual((breadth["contradicted"], breadth["requirement"]), (1, "met"))
        self.assertEqual(gated["axes"]["integrity"]["value"], plain["axes"]["integrity"]["value"])
        self.assertEqual(gated["assessment_checks"]["integrity_decision"][0]["status"], "contradicted")

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
