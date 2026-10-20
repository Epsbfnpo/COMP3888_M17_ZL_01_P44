"""Regression cases for the October 2026 pilot findings."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import run
from music_target.contract import read_json, validate
from music_target.engine import InputError
from music_target.evidence_audit import audit_cross_evidence
from music_target.time_ranges import content_range_checks
from music_target.workflow_policy import load_workflow_policy, WorkflowCompletenessPass, WorkflowPolicyError
from music_target.audio_diagnostics.io import read_audio, AnalysisUnavailable
from music_target.audio_diagnostics.policy import DEFAULTS
from music_target.audio_diagnostics.worker import execute
from music_target.audio_diagnostics.providers.music_semantics import _locate, _locate_candidates, _path_quality, _aligned_tempo_candidate
from benchmarks.audio_dataset import validate_manifest
from test_audio_diagnostics import fixture, tone_sequence, write_task


class RangeRegressionTests(unittest.TestCase):
    def test_both_sides_reject_negative_nonfinite_reversed_zero_and_past_eof(self):
        for role in ("source", "target"):
            for start, end in ((-1, 2), (1, 1), (10, 2), (20, 21), (21, 22),
                               ("1", 2), (float("nan"), 2), (0, float("inf")), (False, 2)):
                with self.subTest(role=role, start=start, end=end):
                    self.assertTrue(any(not row["passed"] for row in content_range_checks(start, end, 20, 48000, role)))

    def test_one_sample_end_tolerance_without_filling_in_missing_endpoints(self):
        self.assertTrue(all(row["passed"] for row in content_range_checks(0, 20 + 0.5 / 48000, 20, 48000)))
        self.assertTrue(any(not row["passed"] for row in content_range_checks(0, 20 + 2 / 48000, 20, 48000)))
        self.assertEqual(content_range_checks(None, None, 20, 48000), [])
        self.assertFalse(any(row["rule"].endswith("order_valid") for row in content_range_checks(1, None, 20, 48000)))

    def test_real_request_reports_reversed_target_instead_of_passing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            request = fixture(Path(directory))
            request["chain"]["artefacts"][-1]["evidence"][0]["attributes"] = {
                "source_start_seconds": 0, "source_end_seconds": 2,
                "target_start_seconds": 3, "target_end_seconds": 2}
            response = run.evaluate(request, Path(directory), "native")
            validate(response, "scoring-response.schema.json")
            self.assertEqual(response["execution_status"], "succeeded")
            order = [row for row in response["assessment_checks"]["relationship_integrity"]
                     if row["rule"] == "target_time_order_valid"]
            self.assertEqual([row["passed"] for row in order], [False])

    def test_audio_reader_uses_the_same_end_tolerance_and_rejects_invalid_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            request = fixture(Path(directory))
            path = Path(directory) / next(iter(request["objects"].values()))
            audio = read_audio(path, 0, 16 + 0.5 / 22050, 22050)
            self.assertEqual(audio["range"], [0.0, 16.0])
            for start, end in ((3, 2), (-1, 2), (0, 16 + 2 / 22050), (1, 1), (float("nan"), 2)):
                with self.subTest(start=start, end=end), self.assertRaises(AnalysisUnavailable) as error:
                    read_audio(path, start, end, 22050)
                self.assertEqual(error.exception.code, "invalid_analysis_range")

    def test_successful_extra_validations_do_not_dilute_existing_source_error(self):
        with tempfile.TemporaryDirectory() as directory:
            request = fixture(Path(directory))
            request["chain"]["artefacts"][-1]["evidence"][0]["attributes"] = {
                "source_start_seconds": 3, "source_end_seconds": 2,
                "target_start_seconds": 0, "target_end_seconds": 2}
            response = run.evaluate(request, Path(directory), "native")
            checks = response["assessment_checks"]
            self.assertEqual(len(checks["relationship_integrity"]), 3)
            self.assertGreater(len(checks["relationship_range_validation"]), 3)
            layer = next(row for row in checks["integrity_layers"] if row["layer"] == "declaration_consistency")
            self.assertAlmostEqual(layer["value"], 2 / 3)

    def test_nonfinite_numbers_rejected_at_file_and_python_entry_points(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for literal in ("NaN", "Infinity", "1e309", "-1e309"):
                path.write_text('{"value": ' + literal + '}')
                with self.subTest(literal=literal), self.assertRaises(InputError):
                    read_json(path)
            with self.assertRaises(InputError):
                validate({"extra": float("nan")}, "native-request.schema.json")


class EvidenceRegressionTests(unittest.TestCase):
    def test_duplicate_audit_covers_correct_and_incorrect_copies_without_mutation(self):
        rows = [{"rule": "sound_report_start_timecode_matches_recorder", "audio_hash": "audio",
                 "evidence_hash": "report0", "declared_seconds": 1, "recorded_seconds": 0, "passed": False}]
        for passed, declared, name in ((True, 0, "report1"), (True, 0, "report2"), (False, 1, "report3")):
            rows.append(dict(rows[0], evidence_hash=name, passed=passed, declared_seconds=declared))
        original = copy.deepcopy(rows)
        audit = audit_cross_evidence(rows)
        self.assertEqual(rows, original)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["distinct_observation_count"], 2)
        self.assertEqual(audit[0]["duplicate_observation_count"], 2)
        self.assertTrue(audit[0]["conflicting_outcomes"])
        self.assertEqual(audit[0]["scoring_effect"], "none_audit_only")
        self.assertIsNone(audit[0]["independent_source_count"])

    def test_unidentified_or_different_audio_observations_are_not_merged(self):
        self.assertEqual(len(audit_cross_evidence([{"rule": "x", "passed": True}] * 2)), 2)
        self.assertEqual(len(audit_cross_evidence([{"rule": "x", "audio_hash": digest, "passed": True}
                                                 for digest in ("a", "b")])), 2)

    def test_generation_record_registered_type_satisfies_conditional_rule(self):
        policy = load_workflow_policy(BASE / "policies/workflow_evidence_policy.json")
        rule = next(rule for workflow in policy["workflows"] for rule in workflow.get("conditional", [])
                    if rule["rule_id"] == "ai.generation_record")
        evaluator = WorkflowCompletenessPass(policy)
        node = {"artefact_hash": "record", "artefact_type": "text/generation-record", "attributes": {}}
        result = evaluator._evaluate_rule(rule, "conditional", [node], "final", {"record"}, {})
        self.assertEqual(result["status"], "satisfied")
        self.assertEqual(evaluator._evaluate_rule(rule, "conditional", [], "final", set(), {})["status"], "missing")

    def test_unknown_policy_type_fails_fast(self):
        policy = json.loads((BASE / "policies/workflow_evidence_policy.json").read_text())
        policy["workflows"][0]["allowed_final_types"] = ["metadata/generation-record"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy))
            with self.assertRaisesRegex(WorkflowPolicyError, "Unsupported policy artefact"):
                load_workflow_policy(path)


class AudioRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("librosa") is None:
            if os.environ.get("REQUIRE_AUDIO_BACKENDS") == "1":
                raise RuntimeError("Required real audio dependency missing: librosa")
            raise unittest.SkipTest("librosa is not installed")

    def test_structure_of_identical_source_is_exactly_equal_for_different_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = tone_sequence(12)
            first = write_task(folder, source, tone_sequence(8, seed=8))
            first_result = execute(first, "librosa", DEFAULTS["librosa"])
            second = write_task(folder, source, tone_sequence(10, seed=99) * 0.05)
            second_result = execute(second, "librosa", DEFAULTS["librosa"])
            self.assertEqual(first["source_hash"], second["source_hash"])
            self.assertNotEqual(first["target_hash"], second["target_hash"])
            for result in (first_result, second_result):
                self.assertTrue(result["analysis_complete"], result)
                self.assertFalse(result["scoring_eligible"])
            for field in ("source_sections_seconds", "source_repetition_candidates", "source_aggregated_frames"):
                self.assertEqual(first_result["metrics"][field], second_result["metrics"][field])

    def test_cross_file_similarity_keeps_a_shared_scale(self):
        # Per-file scaling would make a much quieter copy look identical.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = tone_sequence(12)
            same = execute(write_task(folder, source, source.copy()), "librosa", DEFAULTS["librosa"])
            quiet = execute(write_task(folder, source, source * 0.05), "librosa", DEFAULTS["librosa"])
            self.assertEqual(quiet["metrics"]["feature_normalization"],
                             {"sections_and_repetitions": "per_file", "cross_file_similarity": "joint_pair"})
            self.assertAlmostEqual(same["metrics"]["mean_best_feature_cosine"], 1.0, places=5)
            self.assertLess(quiet["metrics"]["mean_best_feature_cosine"],
                            same["metrics"]["mean_best_feature_cosine"] - 0.01)
            self.assertEqual(quiet["metrics"]["source_sections_seconds"], same["metrics"]["source_sections_seconds"])

    def test_repeated_phrase_locations_do_not_depend_on_previous_phrase(self):
        import librosa
        phrase = np.eye(4)
        reference = np.tile(phrase, (1, 3))
        before = _locate(phrase, reference, 0, librosa)
        after = _locate(phrase, reference, 8, librosa)
        np.testing.assert_array_equal(before[1], after[1])
        self.assertEqual(before[0], after[0])
        _, _, alternatives = _locate_candidates(phrase, reference, librosa)
        self.assertGreaterEqual(len(alternatives), 3)

    def test_unique_reordered_phrases_are_located_out_of_order(self):
        import librosa
        reference = np.eye(12)
        last = _locate(reference[:, 8:12], reference, 0, librosa)[1]
        first = _locate(reference[:, :4], reference, int(last.max()) + 1, librosa)[1]
        self.assertGreater(int(last.min()), int(first.min()))

    def test_compressed_alignment_reports_ratio_and_beat_collapse(self):
        source = {"times": np.arange(5) * 0.46, "chroma": np.ones((12, 4))}
        target = {"times": np.arange(13), "chroma": np.ones((12, 12))}
        quality = _path_quality(np.column_stack((np.zeros(12, dtype=int), np.arange(12))), source, target)
        self.assertAlmostEqual(quality["source_to_target_duration_ratio"], 0.46 / 12)
        self.assertEqual(quality["max_target_beats_per_source_beat"], 12)
        self.assertEqual(quality["acceptance_status"], "not_calibrated")

    def test_alignment_tempo_candidate_uses_known_time_mapping(self):
        target = {"times": np.arange(30) * 0.5}
        source = {"times": np.arange(30) * 0.56 + 3}
        estimate = _aligned_tempo_candidate(np.column_stack((np.arange(29), np.arange(29))), source, target)
        self.assertAlmostEqual(estimate["ratio"], 1.12)
        self.assertLess(estimate["residual_seconds"], 1e-10)
        self.assertEqual(estimate["status"], "candidate_not_calibrated")

    def test_fingerprint_support_is_bounded_and_explicitly_a_window_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            request = fixture(Path(directory))
            nodes = request["chain"]["artefacts"]
            task = {"task_id": "audit", "relation_id": "audit", "relationship_type": "excerpted_from",
                    "selection_id": None, "unavailable_reason": None, "attributes": {}}
            for role, node in zip(("source", "target"), nodes):
                task[role + "_hash"] = node["artefact_hash"]
                task[role + "_path"] = str(Path(directory) / request["objects"][node["artefact_hash"]])
            result = execute(task, "audfprint", DEFAULTS["audfprint"])
            self.assertTrue(result["analysis_complete"], result)
            self.assertTrue(result["matches"])
            for match in result["matches"]:
                for role in ("source", "target"):
                    self.assertGreater(match[role + "_supported_fraction"], 0)
                    self.assertLessEqual(match[role + "_supported_fraction"], 1)
                self.assertEqual(match["support_semantics"], "token_neighbourhood_windows_not_verified_continuous_audio")


class DatasetRegressionTests(unittest.TestCase):
    def manifest(self):
        return {"schema_version": "1.0", "cases": [
            {"case_id": "a", "partition": "development", "family_ids": ["song1"], "audio_sha256": "a" * 64,
             "requested_properties": {"tempo_bpm": 120}, "verified_labels": [],
             "generation": {"status": "succeeded", "submitted_prompt_text": "Actual submitted music prompt",
                            "refund_status": "unknown", "charged_credits": 8}}]}

    def test_prompt_is_not_a_verified_label_and_missing_labels_are_reported(self):
        manifest = self.manifest()
        self.assertEqual(validate_manifest(manifest)["successful_cases_without_verified_labels"], ["a"])
        manifest["cases"][0]["verified_labels"] = [{"property": "tempo_bpm", "value": 120,
                                                   "method": "prompt", "annotator_or_tool": "Lumina"}]
        with self.assertRaisesRegex(ValueError, "prompts are not labels"):
            validate_manifest(manifest)

    def test_shared_family_and_same_hash_cannot_cross_partitions(self):
        for keep_family in (True, False):
            manifest = self.manifest()
            child = dict(manifest["cases"][0], case_id="b", partition="test")
            if not keep_family:
                child["family_ids"] = ["mislabelled_other_song"]
            manifest["cases"].append(child)
            with self.subTest(keep_family=keep_family), self.assertRaisesRegex(ValueError, "leaks across partitions"):
                validate_manifest(manifest)

    def test_derivatives_inherit_parent_families(self):
        manifest = self.manifest()
        manifest["cases"].append(dict(manifest["cases"][0], case_id="b", audio_sha256="b" * 64,
                                      family_ids=["wrong"], parent_case_ids=["a"]))
        with self.assertRaisesRegex(ValueError, "inherit all parent"):
            validate_manifest(manifest)

    def test_generation_requires_actual_prompt_and_refund_state(self):
        for field in ("submitted_prompt_text", "refund_status"):
            manifest = self.manifest()
            del manifest["cases"][0]["generation"][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_manifest(manifest)

    def test_failed_generation_is_tracked_without_claiming_a_missing_audio_file(self):
        manifest = self.manifest()
        manifest["cases"][0]["generation"]["status"] = "failed"
        manifest["cases"][0]["audio_sha256"] = None
        summary = validate_manifest(manifest)
        self.assertEqual(summary["failed_generation_count"], 1)
        self.assertEqual(summary["successful_cases_without_verified_labels"], [])


if __name__ == "__main__":
    unittest.main()
