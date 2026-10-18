"""Report isolation plus real backend checks on a known repeated excerpt."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import wave

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import run
from music_target.contract import to_native, validate
from music_target.audio_diagnostics.contracts import result, validate_result
from music_target.audio_diagnostics.policy import DEFAULTS, DEFAULT_PATH, load_policy
from music_target.audio_diagnostics.runner import augment_response, _run_task
from music_target.audio_diagnostics.tasks import build_tasks
from music_target.audio_diagnostics.worker import execute


def fixture(folder, silent=False):
    rate = 22050
    rng = np.random.default_rng(42)
    t = np.arange(rate * 8) / rate
    motif = np.zeros(len(t))
    for index in range(16):
        first, last = index * rate // 2, (index + 1) * rate // 2
        frequency = rng.uniform(180, 1800)
        section = t[first:last]
        motif[first:last] = (0.2 * np.sin(2 * np.pi * frequency * section)
                             + 0.12 * np.sin(2 * np.pi * frequency * 1.49 * section)
                             + 0.03 * rng.normal(size=last - first))
    motif *= 0.6 + 0.4 * np.sin(2 * np.pi * 2 * t) ** 2
    if silent:
        motif[:] = 0
    objects, hashes = {}, []
    for name, audio in (("source", np.r_[motif, motif]), ("sample", motif[rate * 2:rate * 6])):
        path = Path(folder) / (name + ".wav")
        with wave.open(str(path), "wb") as stream:
            stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            stream.writeframes((audio * 32767).astype("<i2").tobytes())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = "objects/" + digest + ".wav"
        destination = Path(folder) / relative
        destination.parent.mkdir(exist_ok=True)
        path.replace(destination)
        objects[digest] = relative
        hashes.append(digest)
    request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
    request["case_id"] = "AUDIO-OBSERVATION-FIXTURE"
    request["workflow"]["workflow_id"] = "sample_and_loop_based_production"
    request["objects"] = objects
    request["chain"]["final_artefact_hash"] = hashes[1]
    request["chain"]["artefacts"] = [
        {"artefact_hash": hashes[0], "artefact_type": "audio/raw-track", "attributes": {}, "evidence": []},
        {"artefact_hash": hashes[1], "artefact_type": "audio/sample", "attributes": {},
         "evidence": [{"hash": hashes[0], "relationship_type": "excerpted_from", "attributes": {}}]},
    ]
    return request


def tone_sequence(seconds, rate=22050, seed=7):
    """Deterministic quarter-second tone sequence, distinct throughout its length."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(rate * seconds)) / rate
    audio = np.zeros(len(t))
    step = rate // 4
    for first in range(0, len(t), step):
        section = t[first:first + step]
        frequency = rng.uniform(150, 2000)
        audio[first:first + step] = (0.2 * np.sin(2 * np.pi * frequency * section)
                                     + 0.1 * np.sin(2 * np.pi * frequency * 2.01 * section)
                                     + 0.02 * rng.normal(size=len(section)))
    return audio


def write_task(folder, source, target, rate=22050):
    task = {"task_id": "long:0", "relation_id": "long", "relationship_type": "excerpted_from",
            "selection_id": None, "unavailable_reason": None,
            "attributes": {key: None for key in ("source_start_seconds", "source_end_seconds",
                                                 "target_start_seconds", "target_end_seconds")}}
    for role, audio in (("source", source), ("target", target)):
        path = Path(folder) / (role + ".wav")
        with wave.open(str(path), "wb") as stream:
            stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            stream.writeframes((audio * 32767).astype("<i2").tobytes())
        task[role + "_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
        task[role + "_path"] = str(path)
    return task


class AudioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.request = fixture(self.folder)
        self.core = run.evaluate(copy.deepcopy(self.request), self.folder, "native")
        self.assertEqual(self.core["execution_status"], "succeeded")

    def augment(self, deadline=None, policy_path=DEFAULT_PATH):
        return augment_response(self.core, self.request, self.folder, "native", policy_path,
                                deadline if deadline is not None else time.monotonic() + 60)

    def assert_core_unchanged(self, value):
        validate(value, "scoring-response.schema.json")
        for key in ("axes", "workflow_assessment", "validation", "coverage", "unknown_inputs", "execution_status"):
            self.assertEqual(value.get(key), self.core.get(key), key)
        self.assertEqual([row for row in value["findings"] if not row["code"].startswith("AUDIO_")], self.core["findings"])
        for key, data in self.core["assessment_checks"].items():
            self.assertEqual(value["assessment_checks"][key], data, key)

    def test_all_three_observations_append_without_changing_any_core_result(self):
        original = copy.deepcopy(self.core)
        with mock.patch("music_target.audio_diagnostics.runner._run_task", side_effect=lambda task, name, params, deadline: result(task, name)):
            response = self.augment()
        self.assert_core_unchanged(response)
        self.assertEqual(self.core, original)
        self.assertEqual(len(response["assessment_checks"]["audio_fingerprint"]), 2)
        self.assertEqual(len(response["assessment_checks"]["audio_structure"]), 1)

    def test_expired_budget_is_only_diagnostic_unavailability(self):
        response = self.augment(deadline=time.monotonic() - 1)
        self.assert_core_unchanged(response)
        self.assertTrue(all(row["reason_code"] == "caller_timeout" for row in response["assessment_checks"]["audio_fingerprint"]))

    def test_invalid_policy_does_not_turn_the_core_response_into_an_error(self):
        path = self.folder / "bad-policy.json"
        path.write_text('{"scoring_eligible":true}')
        response = self.augment(policy_path=path)
        self.assert_core_unchanged(response)
        self.assertIn("configuration", response["assessment_checks"]["audio_diagnostic_summary"][0]["reason_code"])

    def test_snapshot_rejects_content_changed_after_core_assessment(self):
        source = next(iter(self.request["objects"].values()))
        with (self.folder / source).open("ab") as stream:
            stream.write(b"changed")
        response = self.augment()
        self.assert_core_unchanged(response)
        self.assertEqual(response["assessment_checks"]["audio_structure"][0]["reason_code"], "input_changed_since_core")

    def test_snapshot_rejects_path_escape(self):
        digest = next(iter(self.request["objects"]))
        with tempfile.TemporaryDirectory() as outside:
            path = self.folder / self.request["objects"][digest]
            destination = Path(outside) / "outside.wav"
            destination.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(destination)
            response = self.augment()
        self.assert_core_unchanged(response)
        self.assertFalse(response["assessment_checks"]["audio_structure"][0]["analysis_complete"])

    def test_audio_cannot_release_a_severe_cap(self):
        self.core["axes"]["integrity"]["value"] = 0.25
        with mock.patch("music_target.audio_diagnostics.runner._run_task", side_effect=lambda task, name, params, deadline: result(task, name)):
            response = self.augment()
        self.assert_core_unchanged(response)
        self.assertEqual(response["axes"]["integrity"]["value"], 0.25)

    def test_core_isolated_nodes_are_not_reintroduced_by_task_builder(self):
        native = to_native(copy.deepcopy(self.request), "native")
        source = next(iter(self.request["objects"]))
        for row in self.core["assessment_checks"]["bound_file_integrity"]:
            if row["rule"] == "object_hash_bound" and row["evidence_hash"] == source:
                row["passed"] = False
        tasks = build_tasks(native, self.core)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["unavailable_reason"], "core_input_unavailable")

    def test_worker_invalid_json_and_timeout_are_diagnostic_only(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        process = mock.Mock()
        process.returncode = 0
        process.communicate.return_value = ("not JSON", "")
        with mock.patch("subprocess.Popen", return_value=process):
            value = _run_task(task, "audfprint", DEFAULTS["audfprint"], time.monotonic() + 10)
        self.assertEqual(value["reason_code"], "invalid_provider_output")
        process = mock.Mock(pid=123)
        process.communicate.side_effect = [subprocess.TimeoutExpired("provider", 1), ("", "")]
        with mock.patch("subprocess.Popen", return_value=process), mock.patch("os.killpg") as kill:
            value = _run_task(task, "audfprint", DEFAULTS["audfprint"], time.monotonic() + 1)
        self.assertEqual(value["reason_code"], "caller_timeout")
        kill.assert_called_once()

    def test_comp_requires_matching_selection_instead_of_guessing(self):
        native = to_native(copy.deepcopy(self.request), "native")
        native["artefacts"][1]["evidence"][0]["relationship_type"] = "comped_from"
        tasks = build_tasks(native, self.core)
        self.assertEqual(tasks[0]["unavailable_reason"], "comp_selection_unavailable")
        self.core["findings"].append({"code": "PARSER_OBSERVATION", "parser": "comp-sheet", "parser_status": "parsed",
            "parser_result": {"metadata": {"target_hash": tasks[0]["target_hash"]}, "selections": [
                {"selection_id": "S1", "source_hash": tasks[0]["source_hash"], "source_start_seconds": 2,
                 "source_end_seconds": 4, "target_start_seconds": 0, "target_end_seconds": 2}]}})
        selected = build_tasks(native, self.core)[0]
        self.assertIsNone(selected["unavailable_reason"])
        self.assertEqual(selected["selection_id"], "S1")
        self.assertEqual(selected["attributes"]["source_start_seconds"], 2)

    def test_no_declared_edges_produces_no_comparisons(self):
        native = to_native(copy.deepcopy(self.request), "native")
        native["artefacts"][1]["evidence"] = []
        self.assertEqual(build_tasks(native, self.core), [])

    def test_provider_crash_keeps_its_stderr_in_the_diagnostic(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        process = mock.Mock(pid=123, returncode=1)
        process.communicate.return_value = ("", "Traceback (most recent call last):\nRuntimeError: boom")
        process.poll.return_value = 1
        with mock.patch("subprocess.Popen", return_value=process):
            value = _run_task(task, "librosa", DEFAULTS["librosa"], time.monotonic() + 10)
        self.assertEqual(value["reason_code"], "invalid_provider_output")
        self.assertIn("exited with code 1", value["message"])
        self.assertIn("RuntimeError: boom", value["message"])

    def test_provider_start_failure_is_one_unavailable_row(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        with mock.patch("subprocess.Popen", side_effect=OSError("no interpreter")):
            value = _run_task(task, "audfprint", DEFAULTS["audfprint"], time.monotonic() + 10)
        self.assertEqual(value["reason_code"], "provider_start_failed")
        self.assertEqual(value["status"], "unavailable")

    def test_protocol_violation_inside_worker_is_structured_not_a_crash(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        def analyse(task, parameters):
            value = result(task, "spectromap")
            value["metrics"] = {"broken": float("nan")}
            return value
        with mock.patch("importlib.import_module", return_value=mock.Mock(analyse=analyse)):
            value = execute(task, "spectromap", DEFAULTS["spectromap"])
        self.assertEqual(value["status"], "unavailable")
        self.assertEqual(value["reason_code"], "invalid_provider_output")
        self.assertEqual(value["metrics"], {})

    def test_missing_backend_is_unavailable_not_a_failed_scored_parser(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        with mock.patch("importlib.import_module", side_effect=ModuleNotFoundError("optional backend missing")):
            value = execute(task, "audfprint", DEFAULTS["audfprint"])
        self.assertEqual(value["reason_code"], "dependency_unavailable")
        self.assertFalse(value["scoring_eligible"])


class AudioPolicyTests(unittest.TestCase):
    def test_default_has_all_providers_and_no_development_caps(self):
        policy = load_policy()
        self.assertEqual(policy["providers"], ["audfprint", "spectromap", "librosa"])
        self.assertFalse(any("max_" in key or "timeout" in key for params in policy["parameters"].values() for key in params))

    def test_protocol_rejects_score_eligibility(self):
        task = {"task_id": "a", "relation_id": "a", "source_hash": "1" * 64,
                "target_hash": "2" * 64, "relationship_type": "excerpted_from"}
        value = result(task, "audfprint")
        value["scoring_eligible"] = True
        with self.assertRaises(ValueError):
            validate_result(value, task, "audfprint")


class RealAudioBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [name for name in ("scipy", "librosa", "spectromap") if importlib.util.find_spec(name) is None]
        if missing:
            if os.environ.get("REQUIRE_AUDIO_BACKENDS") == "1":
                raise RuntimeError("Required real audio dependencies missing: " + ", ".join(missing))
            raise unittest.SkipTest("Optional audio dependencies are not installed: " + ", ".join(missing))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.request = fixture(self.folder)
        self.core = run.evaluate(copy.deepcopy(self.request), self.folder, "native")
        self.task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        for role in ("source", "target"):
            self.task[role + "_path"] = str(self.folder / self.request["objects"][self.task[role + "_hash"]])

    def test_audfprint_locates_excerpt_and_retains_repeated_source_candidates(self):
        value = execute(self.task, "audfprint", DEFAULTS["audfprint"])
        self.assertTrue(value["analysis_complete"], value)
        offsets = [row["source_minus_target_offset_seconds"] for row in value["matches"]]
        self.assertTrue(any(abs(offset - 2) < 0.15 for offset in offsets), offsets)
        self.assertTrue(any(abs(offset - 10) < 0.15 for offset in offsets), offsets)
        self.assertEqual(value["status"], "inconclusive")

    def test_spectromap_extracts_and_compares_real_peak_pairs(self):
        value = execute(self.task, "spectromap", DEFAULTS["spectromap"])
        self.assertTrue(value["analysis_complete"], value)
        self.assertGreater(value["metrics"]["source_peaks"], 0)
        self.assertGreater(value["metrics"]["target_peaks"], 0)
        offsets = [row["source_minus_target_offset_seconds"] for row in value["matches"]]
        self.assertTrue(any(abs(offset - 2) < 0.15 for offset in offsets), offsets)
        self.assertTrue(any(abs(offset - 10) < 0.15 for offset in offsets), offsets)
        self.assertFalse(value["scoring_eligible"])

    def test_librosa_returns_structure_and_cross_file_correspondences(self):
        value = execute(self.task, "librosa", DEFAULTS["librosa"])
        self.assertTrue(value["analysis_complete"], value)
        self.assertTrue(value["metrics"]["source_sections_seconds"])
        self.assertTrue(value["matches"])
        self.assertTrue(value["metrics"]["source_repetition_candidates"])

    def test_silence_is_unavailable_without_contradiction(self):
        silent_request = fixture(self.folder, silent=True)
        for role, digest in zip(("source", "target"), silent_request["objects"]):
            self.task[role + "_path"] = str(self.folder / silent_request["objects"][digest])
        value = execute(self.task, "audfprint", DEFAULTS["audfprint"])
        self.assertEqual(value["reason_code"], "silent_signal")
        self.assertEqual(value["status"], "unavailable")

    def test_declared_crop_times_are_restored_to_original_file_timeline(self):
        self.task["attributes"] = {"source_start_seconds": 1, "source_end_seconds": 7,
                                   "target_start_seconds": 0, "target_end_seconds": 4}
        value = execute(self.task, "audfprint", DEFAULTS["audfprint"])
        self.assertTrue(value["analysis_complete"], value)
        self.assertTrue(any(abs(row["source_minus_target_offset_seconds"] - 2) < 0.15 for row in value["matches"]))
        self.assertTrue(all(1 <= a <= b <= 7 for match in value["matches"] for a, b in match["source_supported_windows_seconds"]))

    def test_partial_ranges_are_not_silently_inferred(self):
        self.task["attributes"] = {"source_start_seconds": 2}
        value = execute(self.task, "audfprint", DEFAULTS["audfprint"])
        self.assertEqual(value["reason_code"], "incomplete_declared_ranges")

    def test_spectromap_peak_density_does_not_depend_on_file_length(self):
        from music_target.audio_diagnostics.providers.spectromap import _tokens
        density = []
        for seconds in (4, 180):
            samples = tone_sequence(seconds).astype(np.float32)
            _, peaks = _tokens({"samples": samples}, DEFAULTS["spectromap"])
            density.append(peaks / seconds)
        # Upstream's whole-array peak distance made 180 s about 15x sparser.
        self.assertLess(max(density) / min(density), 1.25, density)

    def test_long_source_excerpt_is_found_by_both_fingerprint_providers(self):
        rate, offset = 22050, 97.3
        source = tone_sequence(180)
        first = round(offset * rate)
        task = write_task(self.folder, source, source[first:first + 20 * rate])
        for provider in ("audfprint", "spectromap"):
            value = execute(task, provider, DEFAULTS[provider])
            self.assertTrue(value["analysis_complete"], value)
            self.assertTrue(value["matches"], provider)
            best = value["matches"][0]
            self.assertLess(abs(best["source_minus_target_offset_seconds"] - offset), 0.15, (provider, best))
            if provider == "spectromap":
                # Whole-file peak search aligned ~3% of the excerpt's pairs
                # and supported under half of it; windowed search aligns most.
                share = best["matched_token_pairs"] / value["metrics"]["target_tokens"]
                self.assertGreater(share, 0.3, share)
                self.assertGreater(best["target_supported_window_duration_seconds"], 18)

    def test_actual_three_worker_processes_preserve_core_axes(self):
        response = augment_response(self.core, self.request, self.folder, "native", DEFAULT_PATH,
                                    time.monotonic() + 180)
        self.assertEqual(response["axes"], self.core["axes"])
        rows = response["assessment_checks"]["audio_fingerprint"] + response["assessment_checks"]["audio_structure"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["analysis_complete"] for row in rows), rows)
        validate(response, "scoring-response.schema.json")


if __name__ == "__main__":
    unittest.main()
