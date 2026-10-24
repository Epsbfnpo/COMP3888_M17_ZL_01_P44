"""Forge cost grades checked evidence and scales Integrity confidence, never the value."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run
from music_target import assessment_passes
from music_target.contract import validate
from music_target.forge_cost import evidence_strength, fact_cost
from music_target.scoring_profile import ScoringProfileError, load_default_scoring_profile, load_scoring_profile
import test_production_documents as pd

TAKE, OTHER, REPORT, AAF, CUES = "a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64
TYPES = {TAKE: "audio/raw-take", OTHER: "audio/raw-take", REPORT: "text/sound-report",
         AAF: "project/aaf", CUES: "text/cue-sheet"}


def fact(rule, tier, document, passed=True, **identity):
    return {"rule": rule, "identity": {**identity, "rule": rule}, "passed": passed,
            "assessed_evidence_tiers": [tier], "document_hashes": [document]}


class ForgeCostProfileTests(unittest.TestCase):
    def write(self, change):
        profile = copy.deepcopy(load_default_scoring_profile())
        change(profile["integrity"]["forge_cost"])
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "profile.json"
        path.write_text(json.dumps(profile))
        return path

    def test_default_profile_keeps_the_tier_order(self):
        config = load_default_scoring_profile()["integrity"]["forge_cost"]
        self.assertGreaterEqual(config["tier_costs"]["signed_attestation"], config["tier_costs"]["recorder_metadata"])
        self.assertGreater(config["tier_costs"]["recorder_metadata"],
                           max([*config["submitter_document_costs"].values(), config["submitter_document_default"]]))
        self.assertIn("benchmarks/results/forge_cost", config["derivation"])

    def test_a_document_may_not_outrank_recorder_metadata(self):
        path = self.write(lambda config: config["submitter_document_costs"].update({"project/aaf": 3}))
        with self.assertRaisesRegex(ScoringProfileError, "tier order"):
            load_scoring_profile(path)

    def test_malformed_forge_cost_is_rejected(self):
        for change, message in ((lambda config: config.pop("strength_floor"), "missing"),
                                (lambda config: config.update(strength_floor=1.0), "strength_floor"),
                                (lambda config: config["tier_costs"].update(recorder_metadata=6), "1 to 5"),
                                (lambda config: config.update(unbound_source_cost=2, submitter_document_default=1),
                                 "unbound_source_cost")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ScoringProfileError, message):
                    load_scoring_profile(self.write(change))


class EvidenceStrengthTests(unittest.TestCase):
    config = load_default_scoring_profile()["integrity"]["forge_cost"]
    bound = {TAKE, OTHER, REPORT, AAF, CUES}

    def strength(self, facts, bound=None):
        return evidence_strength(facts, TYPES, self.bound if bound is None else bound, self.config)

    def test_fact_costs_follow_tier_then_document_type(self):
        cases = [(fact("timecode", "recorder_metadata", REPORT, audio_hash=TAKE), (3, "recorder_metadata")),
                 (fact("aaf_only", "submitter_document", AAF, audio_hash=TAKE), (2, "submitter_document:project/aaf")),
                 (fact("cue_range", "submitter_document", CUES, asset_hash=TAKE), (1, "submitter_document")),
                 (fact("signed", "signed_attestation", AAF, asset_hash=TAKE), (5, "signed_attestation"))]
        for item, expected in cases:
            self.assertEqual(fact_cost(item, TYPES, self.bound, self.config), expected)

    def test_unbound_documents_or_recordings_fall_to_the_lowest_cost(self):
        item = fact("timecode", "recorder_metadata", REPORT, audio_hash=TAKE)
        self.assertEqual(fact_cost(item, TYPES, self.bound - {REPORT}, self.config), (1, "unbound_source"))
        self.assertEqual(fact_cost(item, TYPES, self.bound - {TAKE}, self.config), (1, "unbound_source"))

    def test_a_weaker_document_about_the_same_recording_never_lowers_strength(self):
        recorder = [fact("timecode", "recorder_metadata", REPORT, audio_hash=TAKE)]
        # The cue sheet names the take asset_hash; it is still the same subject.
        with_cues = recorder + [fact("cue_range", "submitter_document", CUES, asset_hash=TAKE)]
        self.assertEqual(self.strength(recorder)["confidence_factor"], self.strength(with_cues)["confidence_factor"])
        self.assertEqual(self.strength(with_cues)["subject_count"], 1)

    def test_a_recording_backed_only_by_a_plain_document_lowers_the_mean(self):
        value = self.strength([fact("timecode", "recorder_metadata", REPORT, audio_hash=TAKE),
                               fact("cue_range", "submitter_document", CUES, asset_hash=OTHER)])
        self.assertEqual((value["subject_count"], value["mean_subject_forge_cost"]), (2, 2.0))
        self.assertEqual(value["confidence_factor"], 0.7)

    def test_unassessed_facts_do_not_count_and_no_evidence_is_submitter_level(self):
        value = self.strength([fact("timecode", "recorder_metadata", REPORT, passed=None, audio_hash=TAKE)])
        self.assertEqual((value["basis"], value["subject_count"], value["confidence_factor"]),
                         ("no_cross_evidence", 0, 0.6))

    def test_contradicted_facts_still_count_as_checked_evidence(self):
        value = self.strength([fact("timecode", "recorder_metadata", REPORT, passed=False, audio_hash=TAKE)])
        self.assertEqual(value["mean_subject_forge_cost"], 3.0)


class ForgeCostScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.harness = types.SimpleNamespace(folder=self.folder, take=pd.recorder_wav(self.folder, *pd.TAKE))

    def score(self, correct, profile=None):
        row = ["2T01.WAV", "2", "01", "00:00:02", "08:49:43:00" if correct else "08:49:45:00",
               '"MixL"', '"MixR"', '"Boom"', "note"]
        report = (pd.store(self.folder, pd.sound_report("18/12/24", [row]), "csv"), "text/sound-report", "csv")
        request = pd.ProductionDocumentTests.request(self.harness, [report])
        with mock.patch.object(assessment_passes, "load_default_scoring_profile",
                               return_value=profile or load_default_scoring_profile()):
            response = run.evaluate(request, self.folder, "native")
        validate(response, "scoring-response.schema.json")
        return response

    def test_forge_cost_changes_confidence_never_the_value(self):
        without = copy.deepcopy(load_default_scoring_profile())
        without["integrity"].pop("forge_cost")
        for correct in (True, False):
            graded, plain = self.score(correct), self.score(correct, without)
            self.assertEqual(graded["axes"]["integrity"]["value"], plain["axes"]["integrity"]["value"])
            self.assertEqual(graded["axes"]["integrity"]["status"], plain["axes"]["integrity"]["status"])
            self.assertEqual(graded["assessment_checks"]["integrity_decision"][0]["numeric_release"],
                             plain["assessment_checks"]["integrity_decision"][0]["numeric_release"])
            factor = graded["assessment_checks"]["evidence_strength"][0]["confidence_factor"]
            self.assertAlmostEqual(graded["axes"]["integrity"]["confidence"],
                                   round(plain["axes"]["integrity"]["confidence"] * factor, 3), places=2)

    def test_profiles_without_forge_cost_keep_the_layer_count_formula(self):
        without = copy.deepcopy(load_default_scoring_profile())
        without["integrity"].pop("forge_cost")
        response = self.score(True, without)
        self.assertNotIn("evidence_strength", response["assessment_checks"])
        self.assertIn("Integrity confidence reflects how many of the three evidence layers were checked. ",
                      response["axes"]["integrity"]["reasoning"])

    def test_reasoning_names_the_evidence_and_says_value_is_unchanged(self):
        reasoning = self.score(True)["axes"]["integrity"]["reasoning"]
        self.assertIn("average forge cost of 3.00 of 5 (1 recorder metadata)", reasoning)
        self.assertIn("Forge cost changes confidence only, never the value.", reasoning)


if __name__ == "__main__":
    unittest.main()
