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
        with mock.patch("music_target.audio_diagnostics.runner._run_task", side_effect=lambda task, names, params, deadline: [result(task, name) for name in names]):
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
        codes = [row["code"] for row in response["findings"] if row["code"].startswith("AUDIO_")]
        self.assertEqual(codes, ["AUDIO_DIAGNOSTIC_UNAVAILABLE"])

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
        with mock.patch("music_target.audio_diagnostics.runner._run_task", side_effect=lambda task, names, params, deadline: [result(task, name) for name in names]):
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

    def fake_worker(self, body):
        # A real child process standing in for worker.py, so the streaming
        # protocol, deadlines and pipes are exercised without mocks.
        prelude = ("import json, sys, time\n"
                   f"sys.path.insert(0, {str(BASE)!r})\n"
                   "from music_target.audio_diagnostics.contracts import result\n"
                   "payload = json.load(sys.stdin)\n"
                   "def emit(name):\n"
                   "    sys.stdout.write(json.dumps(result(payload['task'], name)) + '\\n'); sys.stdout.flush()\n")
        return [sys.executable, "-c", prelude + body]

    def test_worker_invalid_json_is_diagnostic_only(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        rows = _run_task(task, ["audfprint"], DEFAULTS, time.monotonic() + 30,
                         command=self.fake_worker("print('not JSON')"))
        self.assertEqual(rows[0]["reason_code"], "invalid_provider_output")

    def test_timeout_keeps_rows_finished_before_it(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        started = time.monotonic()
        rows = _run_task(task, ["audfprint", "spectromap", "librosa"], DEFAULTS, started + 3,
                         command=self.fake_worker("emit('audfprint')\ntime.sleep(60)"))
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual([row["provider"] for row in rows],
                         ["audfprint", "spectromap", "librosa", "fingerprint_agreement"])
        self.assertEqual(rows[0]["status"], "inconclusive")
        self.assertEqual([row["reason_code"] for row in rows[1:]], ["caller_timeout"] * 3)

    def test_one_worker_decodes_each_file_once_for_all_providers(self):
        from music_target.audio_diagnostics import io as audio_io
        from music_target.audio_diagnostics.worker import run_task
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        for role in ("source", "target"):
            task[role + "_path"] = str(self.folder / self.request["objects"][task[role + "_hash"]])
        calls, original = [], audio_io._decode_mono
        def counting(*args):
            calls.append(args[0])
            return original(*args)
        rows = []
        with mock.patch.object(audio_io, "_decode_mono", side_effect=counting):
            run_task(task, ["audfprint", "spectromap", "librosa"], DEFAULTS, rows.append)
        self.assertEqual([row["provider"] for row in rows],
                         ["audfprint", "spectromap", "librosa", "fingerprint_agreement"])
        if all(row["analysis_complete"] for row in rows):  # optional backends installed
            self.assertEqual(sorted(calls), sorted({task["source_path"], task["target_path"]}))

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

    def comp_task(self, selection):
        native = to_native(copy.deepcopy(self.request), "native")
        native["artefacts"][1]["evidence"][0]["relationship_type"] = "comped_from"
        source_hash, target_hash = native["artefacts"][0]["artefact_hash"], native["artefacts"][1]["artefact_hash"]
        core = copy.deepcopy(self.core)
        core["findings"].append({"code": "PARSER_OBSERVATION", "parser": "comp-sheet", "parser_status": "parsed",
            "parser_result": {"metadata": {"target_hash": target_hash}, "selections": [
                {"selection_id": "S1", "source_hash": source_hash, **selection}]}})
        task = build_tasks(native, core)[0]
        for role in ("source", "target"):
            task[role + "_path"] = str(self.folder / self.request["objects"][task[role + "_hash"]])
        return task

    def test_comp_selection_without_target_end_copies_the_declared_source_span(self):
        task = self.comp_task({"source_start_seconds": 2.0, "source_end_seconds": 6.0,
                               "target_start_seconds": 0.0, "target_end_seconds": None})
        self.assertIsNone(task["unavailable_reason"])
        self.assertEqual(task["range_source"], "derived_from_declared_source_duration")
        self.assertEqual(task["attributes"]["target_end_seconds"], 4.0)
        explicit = self.comp_task({"source_start_seconds": 2.0, "source_end_seconds": 6.0,
                                   "target_start_seconds": 0.0, "target_end_seconds": 4.0})
        self.assertEqual(explicit["range_source"], "declared")

    def test_derived_end_beyond_the_target_is_a_specific_limitation(self):
        task = self.comp_task({"source_start_seconds": 0.0, "source_end_seconds": 12.0,
                               "target_start_seconds": 1.0, "target_end_seconds": None})
        value = execute(task, "audfprint", DEFAULTS["audfprint"])
        self.assertEqual(value["reason_code"], "derived_range_outside_target")
        self.assertEqual(value["range_source"], "derived_from_declared_source_duration")

    def test_no_declared_edges_produces_no_comparisons(self):
        native = to_native(copy.deepcopy(self.request), "native")
        native["artefacts"][1]["evidence"] = []
        self.assertEqual(build_tasks(native, self.core), [])

    def test_nothing_to_analyse_is_reported_once(self):
        request = copy.deepcopy(self.request)
        final = request["chain"]["final_artefact_hash"]
        request["chain"]["artefacts"] = [node for node in request["chain"]["artefacts"] if node["artefact_hash"] == final]
        for node in request["chain"]["artefacts"]:
            node["evidence"] = []
        request["objects"] = {final: request["objects"][final]}
        self.core = run.evaluate(copy.deepcopy(request), self.folder, "native")
        self.request = request
        response = self.augment()
        self.assert_core_unchanged(response)
        codes = [row["code"] for row in response["findings"] if row["code"].startswith("AUDIO_")]
        self.assertEqual(codes, ["AUDIO_DIAGNOSTIC_NOT_APPLICABLE"])
        self.assertEqual(response["assessment_checks"]["audio_diagnostic_summary"][0]["task_count"], 0)

    def test_provider_crash_keeps_earlier_rows_and_its_stderr(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        rows = _run_task(task, ["audfprint", "librosa"], DEFAULTS, time.monotonic() + 30,
                         command=self.fake_worker("emit('audfprint')\n"
                                                  "sys.stderr.write('Traceback (most recent call last):\\nRuntimeError: boom')\n"
                                                  "sys.exit(1)"))
        self.assertEqual(rows[0]["status"], "inconclusive")
        self.assertEqual(rows[1]["reason_code"], "invalid_provider_output")
        self.assertIn("exited with code 1", rows[1]["message"])
        self.assertIn("RuntimeError: boom", rows[1]["message"])

    def test_provider_start_failure_marks_only_that_task(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        rows = _run_task(task, ["audfprint", "spectromap"], DEFAULTS, time.monotonic() + 10,
                         command=[str(self.folder / "no-such-interpreter")])
        self.assertEqual([row["reason_code"] for row in rows], ["provider_start_failed"] * 2)
        self.assertTrue(all(row["status"] == "unavailable" for row in rows))

    def test_protocol_violation_inside_worker_is_structured_not_a_crash(self):
        task = build_tasks(to_native(copy.deepcopy(self.request), "native"), self.core)[0]
        def analyse(task, parameters, context=None):
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


NOTE_NUMBERS = {"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}
POP = {"A": ["C", "G", "Am", "F"], "B": ["F", "C", "Dm", "G"], "C": ["Am", "Em", "F", "G"]}
OTHER = {"A": ["Eb", "Ab", "Fm", "Bb"], "B": ["C#", "F#", "Bbm", "Eb"]}


def synthetic_song(sections, arrangement, bpm=100, transpose=0, bright=False, seed=0, intro_beats=0, rate=22050):
    """Triad chords per beat with bass and kick, arranged in 16-beat phrases."""
    rng = np.random.default_rng(seed)
    beat, out = 60 / bpm, []
    plan = [None] * intro_beats + [chord for name in arrangement for chord in sections[name] for _ in range(4)]
    for chord in plan:
        t = np.arange(int(beat * rate)) / rate
        x = np.zeros_like(t)
        if chord:
            minor = chord.endswith("m")
            root = NOTE_NUMBERS[chord[:-1] if minor else chord]
            for note in (root, root + (3 if minor else 4), root + 7):
                for harmonic in range(1, 5 if bright else 2):
                    x += (0.18 / harmonic) * np.sin(2 * np.pi * 261.63 * 2 ** ((note + transpose) / 12) * harmonic * t)
            x += 0.25 * np.sin(2 * np.pi * 65.41 * 2 ** (((root + transpose) % 12) / 12) * t)
        kick = 0.6 * np.exp(-t * 40) * np.sin(2 * np.pi * 60 * t) + rng.normal(scale=0.05, size=len(t)) * np.exp(-t * 60)
        out.append(x * np.exp(-t * 1.5) + kick)
    return np.concatenate(out) / 2.5


class MusicSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("librosa") is None:
            if os.environ.get("REQUIRE_AUDIO_BACKENDS") == "1":
                raise RuntimeError("Required real audio dependency missing: librosa")
            raise unittest.SkipTest("librosa is not installed")
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        def task(name, source, target):
            (folder / name).mkdir()
            return write_task(folder / name, source, target)
        demo = synthetic_song(POP, "AABAB")
        cls.final = task("final", demo, synthetic_song(POP, "AABABC", bpm=112, transpose=2, bright=True, seed=1,
                                                       intro_beats=8))
        cls.unrelated = task("unrelated", demo, synthetic_song(OTHER, "AABAB", seed=2))
        cls.reordered = task("reordered", demo, synthetic_song(POP, "BABAA", seed=3))
        # A one-phrase loop used inside a longer song (e.g. a sample or stem in a mix).
        cls.short_source = task("short_source", synthetic_song(POP, "A", seed=4), demo)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def analyse(self, task):
        value = execute(task, "music_semantics", DEFAULTS["music_semantics"])
        self.assertTrue(value["analysis_complete"], value)
        self.assertFalse(value["scoring_eligible"])
        return value["metrics"]

    def test_final_version_is_recognised_through_transposition_tempo_and_timbre(self):
        metrics = self.analyse(self.final)
        self.assertEqual(metrics["transposition_semitones"], 2)
        self.assertAlmostEqual(metrics["tempo_ratio"], 112 / 100, delta=0.05)
        self.assertEqual((metrics["source"]["key"]["tonic"], metrics["target"]["key"]["tonic"]), ("C", "D"))
        self.assertEqual(metrics["source"]["form"], "A×2 B A B")
        self.assertGreater(metrics["harmonic_similarity_z"], 5)
        # Repeated A/B phrases have multiple plausible source locations.
        # They cannot establish order, even for a known synthetic version.
        self.assertIsNone(metrics["phrase_order_preserved_fraction"])
        self.assertEqual(metrics["phrase_order_status"], "ambiguous_locations")
        self.assertLess(metrics["matched_phrases"], metrics["target_phrases"])  # added intro and new section

    def test_unrelated_song_is_not_better_than_shuffled_harmony(self):
        metrics = self.analyse(self.unrelated)
        self.assertLess(metrics["harmonic_similarity_z"], 2)
        self.assertEqual(metrics["matched_phrases"], 0)
        self.assertIsNone(metrics["phrase_order_preserved_fraction"])

    def test_source_shorter_than_target_sections_does_not_fail_the_provider(self):
        value = execute(self.short_source, "music_semantics", DEFAULTS["music_semantics"])
        self.assertEqual(value["status"], "inconclusive", value.get("message"))
        self.assertTrue(value["analysis_complete"])
        rows = value["matches"] + value["metrics"]["section_matches"]
        statuses = {row["match_status"] for row in rows}
        self.assertIn("not_assessed_query_longer_than_source", statuses)
        self.assertNotIn("not_assessed_local_match_failed", statuses)
        for row in rows:
            if row["match_status"] == "not_assessed_query_longer_than_source":
                self.assertIsNone(row["harmonic_similarity"])
                self.assertIsNone(row["source_range_seconds"])

    def test_reordered_form_is_visible(self):
        metrics = self.analyse(self.reordered)
        self.assertEqual(metrics["target"]["form"], "A B A B×2")
        self.assertLess(metrics["candidate_phrase_order_fraction"], 1.0)
        self.assertIsNone(metrics["phrase_order_preserved_fraction"])
        self.assertEqual(metrics["phrase_order_status"], "ambiguous_locations")


class NonFinalVersionTests(unittest.TestCase):
    """Earlier versions legitimately differ from the final; that is never a penalty."""

    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("librosa") is None:
            if os.environ.get("REQUIRE_AUDIO_BACKENDS") == "1":
                raise RuntimeError("Required real audio dependency missing: librosa")
            raise unittest.SkipTest("librosa is not installed")

    def test_demo_mix_that_differs_from_the_master_changes_nothing_in_the_core(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "objects").mkdir()
            hashes = []
            for audio in (synthetic_song(POP, "AABAB"),
                          synthetic_song(POP, "AABABC", bpm=112, transpose=2, bright=True, seed=1, intro_beats=8)):
                path = folder / "objects" / "pending.wav"
                with wave.open(str(path), "wb") as stream:
                    stream.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
                    stream.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                path.rename(folder / "objects" / f"{digest}.wav")
                hashes.append(digest)
            demo, master = hashes
            request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
            request["objects"] = {digest: f"objects/{digest}.wav" for digest in hashes}
            request["chain"]["final_artefact_hash"] = master
            request["chain"]["artefacts"] = [
                {"artefact_hash": demo, "artefact_type": "audio/mix", "attributes": {}, "evidence": []},
                {"artefact_hash": master, "artefact_type": "audio/master", "attributes": {},
                 "evidence": [{"hash": demo, "relationship_type": "mastered_from", "attributes": {}}]}]
            core = run.evaluate(copy.deepcopy(request), folder, "native")
            response = augment_response(core, request, folder, "native", DEFAULT_PATH, time.monotonic() + 300)
        for key in ("axes", "workflow_assessment", "coverage", "execution_status"):
            self.assertEqual(response.get(key), core.get(key), key)
        audio = [row for row in response["findings"] if row["code"].startswith("AUDIO_")]
        self.assertTrue(audio)
        self.assertTrue(all(row["severity"] == "info" and row["scoring_eligible"] is False for row in audio))
        rows = [row for key in ("audio_fingerprint", "audio_structure", "audio_semantics")
                for row in response["assessment_checks"][key]]
        self.assertTrue(all(row["status"] == "inconclusive" for row in rows), [row["reason_code"] for row in rows])
        # The fingerprints see different signals; the semantics see the same song.
        semantics = response["assessment_checks"]["audio_semantics"][0]["metrics"]
        self.assertEqual(semantics["transposition_semitones"], 2)
        self.assertGreater(semantics["harmonic_similarity_z"], 5)


class FingerprintAgreementTests(unittest.TestCase):
    task = {"task_id": "a:0", "relation_id": "a", "source_hash": "1" * 64, "target_hash": "2" * 64,
            "relationship_type": "derived_from"}

    def rows(self, status="inconclusive"):
        return {name: {**result(self.task, name), "status": status, "analysed_ranges": {}}
                for name in ("audfprint", "spectromap")}

    @staticmethod
    def profile(bin_seconds, votes, background=0.05):
        return {"votes": {round(offset / bin_seconds): count for offset, count in votes.items()},
                "bin_seconds": bin_seconds, "feasible_bins": 2000, "range_offset_seconds": 0.0,
                "background": background}

    def test_sub_threshold_votes_at_the_same_offset_agree(self):
        from music_target.audio_diagnostics.agreement import fingerprint_agreement
        # Like take 1T04: audfprint has 4 votes (below its threshold of 5) at
        # the offset where SpectroMap has 9; chance votes elsewhere differ.
        profiles = {"audfprint": self.profile(0.05, {8.45: 4, 30.0: 2}),
                    "spectromap": self.profile(256 / 11025 * 2, {8.44: 9, 50.0: 3})}
        value = validate_result(fingerprint_agreement(self.task, self.rows(), profiles),
                                self.task, "fingerprint_agreement")
        self.assertEqual(len(value["matches"]), 1)
        agreed = value["matches"][0]
        self.assertAlmostEqual(agreed["source_minus_target_offset_seconds"], 8.45, delta=0.05)
        self.assertEqual((agreed["audfprint_votes"], agreed["spectromap_votes"]), (4, 9))
        self.assertLess(value["metrics"]["expected_chance_agreements"], 0.1)
        self.assertFalse(value["scoring_eligible"])

    def test_agreement_needs_both_completed_providers(self):
        from music_target.audio_diagnostics.agreement import fingerprint_agreement
        value = fingerprint_agreement(self.task, self.rows("unavailable"), {})
        self.assertEqual((value["status"], value["reason_code"]), ("unavailable", "provider_inputs_unavailable"))

    def test_agreement_row_only_when_both_fingerprint_providers_run(self):
        from music_target.audio_diagnostics.agreement import expected_rows
        self.assertEqual(expected_rows(["audfprint", "librosa"]), ["audfprint", "librosa"])
        self.assertEqual(expected_rows(["audfprint", "spectromap"])[-1], "fingerprint_agreement")


class AudioPolicyTests(unittest.TestCase):
    def test_default_has_all_providers_and_no_development_caps(self):
        policy = load_policy()
        self.assertEqual(policy["providers"], ["audfprint", "spectromap", "librosa", "music_semantics"])
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

    def test_librosa_returns_structure_and_only_summarises_cross_file_similarity(self):
        value = execute(self.task, "librosa", DEFAULTS["librosa"])
        self.assertTrue(value["analysis_complete"], value)
        self.assertTrue(value["metrics"]["source_sections_seconds"])
        self.assertTrue(value["metrics"]["source_repetition_candidates"])
        # Real recordings showed frame "correspondences" between unrelated
        # material, so none are listed; only the distribution is reported.
        self.assertEqual(value["matches"], [])
        self.assertIn("0.5", value["metrics"]["best_feature_cosine_quantiles"])

    def test_silence_is_unavailable_without_contradiction(self):
        silent_request = fixture(self.folder, silent=True)
        for role, digest in zip(("source", "target"), silent_request["objects"]):
            self.task[role + "_path"] = str(self.folder / silent_request["objects"][digest])
        value = execute(self.task, "audfprint", DEFAULTS["audfprint"])
        self.assertEqual(value["reason_code"], "silent_signal")
        self.assertEqual(value["status"], "unavailable")

    def test_derived_comp_range_is_analysed_and_flagged(self):
        self.task["attributes"] = {"source_start_seconds": 2.0, "source_end_seconds": 6.0,
                                   "target_start_seconds": 0.0, "target_end_seconds": 4.0}
        self.task["range_source"] = "derived_from_declared_source_duration"
        value = execute(self.task, "spectromap", DEFAULTS["spectromap"])
        self.assertTrue(value["analysis_complete"], value)
        self.assertEqual(value["range_source"], "derived_from_declared_source_duration")
        self.assertTrue(any(abs(row["source_minus_target_offset_seconds"] - 2) < 0.15 for row in value["matches"]))

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
        self.assertIn("missing: source_end_seconds, target_start_seconds, target_end_seconds", value["message"])

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
            # Background is averaged over every feasible offset, not only the
            # span that received votes, so a narrow histogram cannot inflate it.
            parameters = DEFAULTS[provider]
            step = (parameters["n_fft"] - parameters["noverlap"]) / parameters["sample_rate_hz"] if provider == "spectromap" else 256 / 11025
            bin_seconds = parameters.get("offset_bin_seconds", step * 2)
            feasible = (180 + 20) / bin_seconds + 1
            self.assertAlmostEqual(value["metrics"]["mean_votes_per_offset_bin"],
                                   value["metrics"]["raw_token_pair_matches"] / feasible, places=6)
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
        self.assertEqual(len(response["assessment_checks"]["audio_semantics"]), 1)
        # The repeated motif gives agreed offsets near both repeats.
        agreement = response["assessment_checks"]["audio_fingerprint_agreement"][0]
        self.assertTrue(agreement["analysis_complete"], agreement)
        offsets = [row["source_minus_target_offset_seconds"] for row in agreement["matches"]]
        self.assertTrue(any(abs(offset - 2) < 0.15 for offset in offsets), offsets)
        self.assertTrue(any(abs(offset - 10) < 0.15 for offset in offsets), offsets)
        validate(response, "scoring-response.schema.json")


if __name__ == "__main__":
    unittest.main()
