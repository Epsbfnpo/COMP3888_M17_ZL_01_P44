import copy
import gc
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import builder_operations as operations
# assess_manifest imports run lazily, and importing it loads c2pa, which calls
# platform.processor() and so subprocess.Popen. The worker tests patch Popen on
# the shared subprocess module, so import run here, before any test patches it.
import run  # noqa: F401


class ManifestLimitTests(unittest.TestCase):
    """The builder's preflight must accept exactly what the evaluator accepts."""

    def test_builder_limits_match_the_evaluator(self):
        import manifest_logic
        import relationship_modes
        from music_target import engine
        for module in (manifest_logic, relationship_modes):
            self.assertEqual((module.MAX_FILE_BYTES, module.MAX_TOTAL_BYTES),
                             (engine.MAX_FILE_BYTES, engine.MAX_TOTAL_BYTES))

    def entry(self, path, technical=None):
        return {"id": "take", "type": "audio/master", "hash": "a" * 64, "path": str(path), "final": True,
                "technical": technical if technical is not None else {"sample_rate_hz": 48000},
                "creation_method": "recorded", "production": {}, "extra": {}}

    def test_preflight_accepts_large_files_and_rejects_oversized_ones(self):
        import manifest_logic
        with tempfile.TemporaryDirectory() as folder:
            # Sparse files: the size check reads st_size only, so no disk space is used.
            large, oversized = Path(folder) / "large.wav", Path(folder) / "oversized.wav"
            with open(large, "wb") as handle:
                handle.truncate(133 * 2**20)  # the 132.5 MiB Project Rock effect was rejected before
            with open(oversized, "wb") as handle:
                handle.truncate(manifest_logic.MAX_FILE_BYTES + 1)
            manifest_logic.build_chain([self.entry(large)], [])
            with self.assertRaisesRegex(ValueError, "limit is 512 MiB per file"):
                manifest_logic.build_chain([self.entry(oversized)], [])

    def test_unreadable_audio_message_names_the_supported_encodings(self):
        import manifest_logic
        with self.assertRaisesRegex(ValueError, "IEEE float 32/64-bit, including WAVE_FORMAT_EXTENSIBLE"):
            manifest_logic.build_chain([self.entry(Path("missing.wav"), technical={})], [])


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.txt"
        self.source.write_bytes(b"original evidence")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.files = [{"id": "source", "path": str(self.source), "hash": self.digest}]
        self.relative = f"objects/{self.digest}.txt"
        self.manifest = {"objects": {self.digest: self.relative}}
        self.target = self.root / "submission.json"
        self.cancel = threading.Event()

    def export(self, copy_files=True, progress=lambda text: None):
        operations.export_bundle(self.manifest, self.files, self.target,
                                 copy_files, progress, self.cancel)

    def test_export_publishes_verified_objects_and_manifest(self):
        self.export()
        self.assertEqual(json.loads(self.target.read_text()), self.manifest)
        self.assertEqual((self.root / self.relative).read_bytes(), self.source.read_bytes())
        self.assertFalse(list(self.root.glob(".manifest-stage-*")))

    def test_changed_source_preserves_old_manifest(self):
        self.target.write_text("old manifest")
        self.source.write_bytes(b"changed evidence")
        with self.assertRaisesRegex(ValueError, "declared SHA-256"):
            self.export()
        self.assertEqual(self.target.read_text(), "old manifest")
        self.assertFalse((self.root / self.relative).exists())
        self.assertFalse(list(self.root.glob(".manifest-stage-*")))

    def test_missing_source_does_not_publish(self):
        self.files[0]["path"] = ""
        with self.assertRaisesRegex(ValueError, "original file is missing"):
            self.export()
        self.assertFalse(self.target.exists())

    def test_no_copy_references_verified_original_without_copying(self):
        self.manifest["objects"][self.digest] = self.source.name
        self.manifest["options"] = {"local_file_references": True}
        self.export(False)
        self.assertTrue(self.target.exists())
        self.assertEqual(json.loads(self.target.read_text())["objects"][self.digest],
                         self.source.name)
        self.assertFalse((self.root / "objects").exists())

    def test_existing_incorrect_object_gets_collision_safe_name(self):
        dest = self.root / self.relative
        dest.parent.mkdir()
        dest.write_bytes(b"incorrect")
        self.export()
        self.assertEqual(dest.read_bytes(), b"incorrect")
        saved = json.loads(self.target.read_text())
        mapped = saved["objects"][self.digest]
        self.assertNotEqual(mapped, self.relative)
        self.assertEqual((self.root / mapped).read_bytes(), self.source.read_bytes())

    def test_existing_correct_object_is_reused(self):
        self.export()
        dest = self.root / self.relative
        modified = dest.stat().st_mtime_ns
        self.export()
        self.assertEqual(dest.stat().st_mtime_ns, modified)

    def test_cancel_before_publish_leaves_no_manifest(self):
        def progress(text):
            if text.startswith("Publishing"):
                self.cancel.set()

        with self.assertRaises(operations.Cancelled):
            self.export(progress=progress)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / self.relative).exists())

    def test_publication_failure_rolls_back_new_objects_and_keeps_old_manifest(self):
        self.target.write_text("old manifest")
        with patch.object(operations.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.export()
        self.assertEqual(self.target.read_text(), "old manifest")
        self.assertFalse((self.root / self.relative).exists())

    def test_import_reports_missing_file_and_reads_valid_file(self):
        result = operations.inspect_files(
            [str(self.root / "missing"), str(self.source)], lambda text: None, self.cancel)
        self.assertIn("error", result[0])
        self.assertEqual(result[1]["hash"], self.digest)

    def test_hash_cancellation_is_explicit(self):
        self.cancel.set()
        with self.assertRaises(operations.Cancelled):
            operations.verified_hash(self.source, self.cancel)


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.root = BASE / "examples" / "valid-generated"
        self.request = json.loads((self.root / "request.json").read_text())
        self.files = [{"id": str(n), "hash": digest, "path": str(self.root / relative)}
                      for n, (digest, relative) in enumerate(self.request["objects"].items())]

    def test_full_assessment_uses_real_worker_and_four_dimensions(self):
        original = copy.deepcopy(self.request)
        response = operations.assess_manifest(
            self.request, self.files, lambda text: None, threading.Event())
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        self.assertEqual(set(response["axes"]),
                         {"completeness", "integrity", "attestation_strength", "ai_disclosure"})
        self.assertIn("workflow_assessment", response)
        self.assertIn("assessment_checks", response)
        integrity = response["axes"]["integrity"]["value"]
        # A small fixture may have its integrity value withheld (null = not assessed).
        if integrity is not None:
            self.assertGreaterEqual(integrity, 0.0)
            self.assertLessEqual(integrity, 1.0)
        self.assertNotIn("overall_score", response)
        self.assertEqual(self.request, original)

    def test_unsupported_profile_is_returned_not_reported_as_success(self):
        self.request["profile"] = "unknown"
        response = operations.assess_manifest(
            self.request, self.files, lambda text: None, threading.Event())
        self.assertEqual(response["execution_status"], "unsupported")
        self.assertTrue(response["error"])

    def test_cancellation_kills_and_reaps_worker(self):
        cancel = threading.Event()
        process = unittest.mock.Mock()
        process.poll.return_value = None
        def communicate(*args, **kwargs):
            if "timeout" in kwargs:
                cancel.set()
                raise operations.subprocess.TimeoutExpired("worker", 0.1)
            return "", ""

        process.communicate.side_effect = communicate
        with patch.object(operations.subprocess, "Popen", return_value=process):
            with self.assertRaises(operations.Cancelled):
                operations.assess_manifest(self.request, self.files, lambda text: None, cancel)
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)

    def test_timeout_kills_and_reaps_worker(self):
        process = unittest.mock.Mock()
        process.poll.return_value = None
        with patch.object(operations.subprocess, "Popen", return_value=process), \
                patch.object(operations.time, "monotonic", side_effect=[0, 61]):
            with self.assertRaises(TimeoutError):
                operations.assess_manifest(
                    self.request, self.files, lambda text: None, threading.Event())
        process.kill.assert_called_once()
        process.communicate.assert_called_once()

    def check_real_worker_stop(self, cancel_worker):
        real_popen = operations.subprocess.Popen
        processes = []
        cancel = threading.Event()
        self.request["options"]["timeout_seconds"] = 1

        def launch(command, **kwargs):
            process = real_popen(
                [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(20)"],
                **kwargs)
            processes.append(process)
            if cancel_worker:
                timer = threading.Timer(0.3, cancel.set)
                timer.start()
                self.addCleanup(timer.cancel)
            return process

        started = time.monotonic()
        with patch.object(operations.subprocess, "Popen", side_effect=launch):
            with self.assertRaises(operations.Cancelled if cancel_worker else TimeoutError):
                operations.assess_manifest(self.request, self.files, lambda text: None, cancel)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll(), "Worker was left running")
        self.assertLess(time.monotonic() - started, 3)
        if not cancel_worker:
            self.assertGreaterEqual(time.monotonic() - started, 1)

    def test_real_cancellation_stops_worker_promptly(self):
        self.check_real_worker_stop(True)

    def test_real_timeout_enforces_one_second_limit(self):
        self.check_real_worker_stop(False)


class GuiTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        from builder_gui import App
        self.app = App()
        self.app.withdraw()
        self.addCleanup(self.app.destroy)

    def pump_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)
        self.assertTrue(predicate(), "GUI operation did not complete")

    def test_background_operation_keeps_ui_responsive_and_restores_controls(self):
        release = threading.Event()
        self.addCleanup(release.set)
        completed, ticks = [], []
        main_thread = threading.get_ident()

        def work(progress, cancel):
            self.assertNotEqual(threading.get_ident(), main_thread)
            release.wait(3)
            return 42

        self.app._start_job(work, lambda result: completed.append((result, threading.get_ident())))
        self.assertEqual(str(self.app.v["timeout"].get()), "60")
        self.app.after(10, lambda: ticks.append(True))
        self.pump_until(lambda: bool(ticks))
        self.assertFalse(completed)
        release.set()
        self.pump_until(lambda: bool(completed))
        self.assertEqual(completed, [(42, main_thread)])
        self.assertEqual(str(self.app.cancel_button.cget("state")), "disabled")

    def test_audio_diagnostics_are_on_by_default_and_can_be_unticked(self):
        requested = []
        jobs = []
        self.app.build_manifest = lambda *args: {"manifest": True}
        self.app._start_job = lambda work, done: jobs.append(work)
        with patch("builder_gui.operations.assess_manifest",
                   side_effect=lambda manifest, files, progress, cancel, audio: requested.append(audio)):
            self.assertTrue(self.app.audio_var.get())
            self.app.run_assessment()
            self.app.audio_var.set(False)
            self.app.run_assessment()
            for work in jobs:
                work(lambda *_: None, threading.Event())
        self.assertEqual(requested, [True, False])

    def test_worker_error_is_visible_and_job_resets(self):
        def work(progress, cancel):
            raise OSError("copy failure")

        with patch("builder_gui.messagebox.showerror") as error:
            self.app._start_job(work, lambda result: self.fail("Unexpected success"))
            self.pump_until(lambda: self.app._job is None)
            self.assertIn("copy failure", error.call_args.args[1])

    def test_result_shows_null_as_not_assessed_and_preserves_failure_status(self):
        from run import response_shell
        request = json.loads((BASE / "examples" / "valid-generated" / "request.json").read_text())
        response = response_shell(request, "native")
        response["error"] = "evaluation failed"
        self.app.show_response(response)
        rows = [self.app.axis_tree.item(i, "values")
                for i in self.app.axis_tree.get_children()]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row[2] == "Not assessed" for row in rows))
        self.assertIn("status: error", self.app.assessment_label.cget("text"))
        self.assertIn("evaluation failed", self.app.assessment_text.get("1.0", "end"))

    def test_load_export_assess_and_save_result_end_to_end(self):
        example = BASE / "examples" / "valid-generated" / "request.json"
        with patch("builder_gui.filedialog.askopenfilename", return_value=str(example)):
            self.app.load()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "submission.json"
            with patch("builder_gui.filedialog.asksaveasfilename", return_value=str(target)), \
                    patch("builder_gui.messagebox.askyesno", return_value=True), \
                    patch("builder_gui.messagebox.showinfo"), \
                    patch("builder_gui.messagebox.showerror") as error:
                self.app.save()
                self.pump_until(lambda: self.app._job is None)
                error.assert_not_called()
            manifest = json.loads(target.read_text())
            for digest, relative in manifest["objects"].items():
                self.assertEqual(hashlib.sha256((Path(folder) / relative).read_bytes()).hexdigest(),
                                 digest)
            with patch("builder_gui.messagebox.showerror") as error:
                self.app.run_assessment()
                self.pump_until(lambda: self.app._job is None)
                error.assert_not_called()
            self.assertEqual(self.app._response["execution_status"], "succeeded")
            self.assertEqual(len(self.app.axis_tree.get_children()), 4)
            report = Path(folder) / "assessment-result.json"
            with patch("builder_gui.filedialog.asksaveasfilename", return_value=str(report)):
                self.app.save_response()
            self.assertEqual(json.loads(report.read_text()), self.app._response)

    def test_review_summary_first_and_exports_preserve_worker_json(self):
        from test_builder_review import response, SOURCE, TARGET
        payload = response()
        payload["_builder_notes"] = ["Transport note."]
        before = copy.deepcopy(payload)
        self.app.files = [{"hash": SOURCE, "path": "source.wav"}, {"hash": TARGET, "path": "mix.wav"}]
        self.app.show_response(payload)
        self.assertEqual(payload, before)
        self.assertIn("1 content finding(s) require review", self.app.summary_label.cget("text"))
        self.assertIn("7.00–11.00 s", self.app.first_finding.cget("text"))
        self.assertIn("Core Evidence Consistency", self.app.axis_tree.item(self.app.axis_tree.get_children()[0], "values"))
        self.app.view_findings()
        self.assertEqual(self.app.detail_notebook.index("current"), 0)
        self.app.copy_review_text()
        self.assertIn("1 content finding(s) require review", self.app.clipboard_get())
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "result.json"
            with patch("builder_gui.filedialog.asksaveasfilename", return_value=str(report)):
                self.app.save_response()
            self.assertEqual(json.loads(report.read_text()), {k: v for k, v in before.items() if k != "_builder_notes"})
            self.assertNotIn("summary", json.loads(report.read_text()))
            report = Path(folder) / "review.html"
            with patch("builder_gui.filedialog.asksaveasfilename", return_value=str(report)):
                self.app.save_review_report()
            self.assertIn("1 content finding(s) require review", report.read_text())

    def test_report_snapshot_keeps_original_names_when_submission_changes(self):
        from test_builder_review import response, SOURCE
        self.app.files = [{"hash": SOURCE, "path": "original.wav"}]
        self.app.show_response(response())
        self.app.files[0]["path"] = "replacement.wav"
        self.app._check_snapshot()
        self.assertIn("Showing previous assessment", self.app.assessment_label.cget("text"))
        self.assertEqual(self.app._review["file_names"][SOURCE], "original.wav")
        self.assertIn("original.wav", self.app.first_finding.cget("text"))
        self.app.show_response(response())
        self.app.v["case_id"].set("CHANGED")
        self.assertIn("Showing previous assessment", self.app.assessment_label.cget("text"))

    def test_review_layout_keeps_summary_and_finding_above_scores_at_small_size(self):
        from test_builder_review import response
        self.app.deiconify()
        self.app.geometry("800x600")
        self.app.tk.call("tk", "scaling", 1.5)
        self.app.show_response(response())
        notebook = self.app.axis_tree.master.master
        notebook.select(self.app.axis_tree.master)
        self.app.update()
        self.assertLess(self.app.summary_label.winfo_rooty(), self.app.first_finding.winfo_rooty())
        self.assertLess(self.app.first_finding.winfo_rooty(), self.app.axis_tree.winfo_rooty())
        self.assertLess(self.app.axis_tree.winfo_rooty() + self.app.axis_tree.winfo_height(),
                        self.app.winfo_rooty() + self.app.winfo_height())
        self.assertTrue(self.app.finding_button.winfo_viewable())
        self.assertLessEqual(int(self.app.summary_label.cget("wraplength")), 800)


if __name__ == "__main__":
    unittest.main()
