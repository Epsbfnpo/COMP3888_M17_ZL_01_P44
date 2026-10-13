import copy
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import builder_operations
import manifest_logic as L
import run
from music_target.contract import validate


class LocalFileReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_dir = self.root / "Original audio – 音乐"
        self.source_dir.mkdir()
        self.bundle = self.root / "Submission folder – 提交"
        self.bundle.mkdir()
        existing = BASE / "examples" / "valid-generated" / "objects"
        sample = next(existing.glob("*.wav")).read_bytes()
        self.source = self.source_dir / "Track one – 夜.wav"
        self.source.write_bytes(sample)
        self.digest = hashlib.sha256(sample).hexdigest()
        template_path = BASE / "examples" / "valid-generated" / "request.json"
        self.manifest = json.loads(template_path.read_text(encoding="utf-8"))
        self.manifest["case_id"] = "LOCAL-FILE-REFERENCE-TEST"
        self.manifest["chain"]["artefacts"] = [{
            "artefact_hash": self.digest,
            "artefact_type": "audio/master",
            "attributes": {"creation_method": "recorded"},
            "evidence": [],
        }]
        self.manifest["chain"]["final_artefact_hash"] = self.digest
        self.manifest["objects"] = {self.digest: str(self.source)}
        self.manifest["options"]["local_file_references"] = True
        self.files = [{
            "id": "final-audio",
            "hash": self.digest,
            "path": str(self.source),
            "type": "audio/master",
        }]
        self.cancel = threading.Event()

    def test_reference_path_prefers_relative_unicode_path(self):
        mapping = L.referenced_object_paths(
            self.files, self.bundle / "submission.json")
        self.assertEqual(
            (self.bundle / mapping[self.digest]).resolve(), self.source.resolve())
        self.assertIn("Original audio", mapping[self.digest])
        self.assertIn("音乐", mapping[self.digest])
        self.assertTrue(Path(mapping[self.digest]).is_relative_to(".."))

    def test_reference_export_writes_no_object_and_verifies_identity(self):
        mapping = L.referenced_object_paths(
            self.files, self.bundle / "submission.json")
        self.manifest["objects"] = mapping
        before = self.source.read_bytes()
        target = self.bundle / "submission.json"
        builder_operations.export_bundle(
            self.manifest, self.files, target, False, lambda _text: None, self.cancel)
        saved = json.loads(target.read_text(encoding="utf-8"))
        validate(saved, "native-request.schema.json")
        self.assertEqual(saved["options"]["local_file_references"], True)
        self.assertFalse((self.bundle / "objects").exists())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(saved["objects"][self.digest], mapping[self.digest])

    def test_saved_reference_round_trips_and_evaluates(self):
        target = self.bundle / "submission.json"
        self.manifest["objects"] = L.referenced_object_paths(self.files, target)
        builder_operations.export_bundle(
            self.manifest, self.files, target, False, lambda _text: None, self.cancel)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        response = run.run_isolated(
            loaded, target.parent, "native", allow_local_files=True)
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))

    def test_full_assessment_stages_referenced_originals_inside_worker_root(self):
        response = builder_operations.assess_manifest(
            self.manifest, self.files, lambda _text: None, self.cancel)
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))

    def test_evaluator_requires_explicit_authorization_for_external_references(self):
        mapping = L.referenced_object_paths(
            self.files, self.bundle / "submission.json")
        self.manifest["objects"] = mapping
        denied = run.run_isolated(self.manifest, self.bundle, "native")
        self.assertEqual(denied["execution_status"], "error")
        self.assertIn("--allow-local-files", denied["error"])
        allowed = run.run_isolated(
            self.manifest, self.bundle, "native", allow_local_files=True)
        self.assertEqual(allowed["execution_status"], "succeeded", allowed.get("error"))

    def test_local_flag_alone_does_not_authorize_external_paths(self):
        mapping = L.referenced_object_paths(
            self.files, self.bundle / "submission.json")
        self.manifest["objects"] = mapping
        self.manifest["options"]["local_file_references"] = False
        response = run.run_isolated(
            self.manifest, self.bundle, "native", allow_local_files=True)
        self.assertEqual(response["execution_status"], "error")
        self.assertIn("--allow-local-files", response["error"])

    def test_csec_cannot_authorize_external_paths(self):
        with self.assertRaisesRegex(ValueError, "trusted native"):
            run.run_isolated({}, self.bundle, "csec", allow_local_files=True)

    def test_missing_or_changed_original_is_explicit_and_never_rehashed(self):
        mapping = L.referenced_object_paths(
            self.files, self.bundle / "submission.json")
        self.manifest["objects"] = mapping
        self.source.write_bytes(b"changed content")
        with self.assertRaisesRegex(ValueError, "declared SHA-256"):
            builder_operations.export_bundle(
                self.manifest, self.files, self.bundle / "submission.json",
                False, lambda _text: None, self.cancel)
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "missing or moved"):
            builder_operations.export_bundle(
                self.manifest, self.files, self.bundle / "submission.json",
                False, lambda _text: None, self.cancel)

    def test_local_reference_missing_file_is_rejected_by_evaluator(self):
        mapping = L.referenced_object_paths(
            self.files, self.bundle / "submission.json")
        self.manifest["objects"] = mapping
        self.source.unlink()
        response = run.run_isolated(
            self.manifest, self.bundle, "native", allow_local_files=True)
        self.assertEqual(response["execution_status"], "error")
        self.assertIn("final artefact file is missing", response["error"])

    def test_copy_mode_preserves_names_handles_collisions_and_reuses_content(self):
        other_dir = self.root / "Other"
        other_dir.mkdir()
        second = other_dir / self.source.name
        second.write_bytes(self.source.read_bytes() + b"different bytes")
        files = [
            {**self.files[0]},
            {"id": "other-audio", "hash": hashlib.sha256(second.read_bytes()).hexdigest(),
             "path": str(second), "type": "audio/master"},
        ]
        paths = L.copied_object_paths(files)
        self.assertEqual(len(set(paths.values())), 2)
        self.assertTrue(all(Path(value).parent.as_posix() == "objects" for value in paths.values()))
        self.assertTrue(any("Track one – 夜 (" in value for value in paths.values()))
        manifest = copy.deepcopy(self.manifest)
        manifest["options"]["local_file_references"] = False
        manifest["chain"]["artefacts"].append({
            "artefact_hash": files[1]["hash"], "artefact_type": "audio/master",
            "attributes": {"creation_method": "recorded"}, "evidence": [],
        })
        manifest["objects"] = paths
        target = self.bundle / "submission.json"
        builder_operations.export_bundle(
            manifest, files, target, True, lambda _text: None, self.cancel)
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["options"]["local_file_references"], False)
        copied = [self.bundle / saved["objects"][entry["hash"]] for entry in files]
        self.assertEqual([path.name for path in copied],
                         [Path(paths[entry["hash"]]).name for entry in files])
        for entry, path in zip(files, copied):
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["hash"])
        before = [path.stat().st_mtime_ns for path in copied]
        builder_operations.export_bundle(
            manifest, files, target, True, lambda _text: None, self.cancel)
        self.assertEqual([path.stat().st_mtime_ns for path in copied], before)

    def test_unrelated_existing_name_is_never_overwritten(self):
        paths = L.copied_object_paths(self.files)
        original = self.bundle / paths[self.digest]
        original.parent.mkdir()
        original.write_bytes(b"unrelated file")
        self.manifest["objects"] = paths
        self.manifest["options"]["local_file_references"] = False
        target = self.bundle / "submission.json"
        builder_operations.export_bundle(
            self.manifest, self.files, target, True, lambda _text: None, self.cancel)
        saved = json.loads(target.read_text(encoding="utf-8"))
        resolved = self.bundle / saved["objects"][self.digest]
        self.assertNotEqual(resolved, original)
        self.assertEqual(original.read_bytes(), b"unrelated file")
        self.assertEqual(hashlib.sha256(resolved.read_bytes()).hexdigest(), self.digest)
