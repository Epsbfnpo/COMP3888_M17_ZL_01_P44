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
        self.assertIsNone(response["axes"]["integrity"]["value"])
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


if __name__ == "__main__":
    unittest.main()
