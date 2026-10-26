"""Regression coverage for GUI declarations and production-document submissions."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE), str(BASE / "vendor")]

import manifest_logic as logic
import builder_operations as operations
import run
from music_target import domain


class ManifestContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "evidence.csv"
        self.path.write_text("File Name,Scene,Take\ntake.wav,1,1\n")
        self.entry = {
            "id": "report", "type": "text/sound-report", "path": str(self.path),
            "hash": hashlib.sha256(self.path.read_bytes()).hexdigest(), "final": True,
            "creation_method": "", "production": {}, "technical": {}, "extra": {},
        }
        schema = json.loads((BASE / "schemas/native-request.schema.json").read_text())
        self.node_schema = schema["properties"]["chain"]["properties"]["artefacts"]["items"]

    def test_builder_types_match_the_evaluator(self):
        self.assertEqual(set(logic.ALL_TYPES), set(domain.AUDIO_TYPES + domain.DOCUMENT_TYPES))
        for kind in ("project/aaf", "text/sound-report"):
            with self.subTest(kind=kind):
                entry = {**self.entry, "type": kind}
                node = logic.build_chain([entry], [])["artefacts"][0]
                self.assertEqual(node["artefact_type"], kind)

    def test_menu_values_match_the_creation_method_schema(self):
        method_schema = self.node_schema["properties"]["attributes"]["properties"]["creation_method"]
        allowed = method_schema["oneOf"][0]["enum"]
        self.assertEqual(logic.CREATION_METHODS, ["", *allowed])

    def test_build_preserves_valid_scalar_and_list_declarations(self):
        from jsonschema import Draft202012Validator
        validator = Draft202012Validator(self.node_schema)
        for method in ("", "recorded", "synthesised", "ai-generated", [],
                       ["recorded", "ai-generated"]):
            with self.subTest(method=method):
                node = logic.build_chain([{**self.entry, "creation_method": method}], [])["artefacts"][0]
                validator.validate(node)
                if method == "":
                    self.assertNotIn("creation_method", node["attributes"])
                else:
                    self.assertEqual(node["attributes"]["creation_method"], method)

    def test_invalid_methods_are_rejected_before_export(self):
        for method in ("generated", "sampled", "Recorded", None, True, {},
                       ["recorded", "sampled"], [""], [1]):
            with self.subTest(method=method):
                with self.assertRaisesRegex(ValueError, "Creation method"):
                    logic.build_chain([{**self.entry, "creation_method": method}], [])

    def test_editor_round_trip_preserves_declared_lists(self):
        for method in ("", "recorded", [], ["recorded", "ai-generated"]):
            with self.subTest(method=method):
                self.assertEqual(logic.parse_creation_method(logic.creation_method_text(method)), method)
        for text in ("generated", "sampled", '["recorded", "sampled"]', '["recorded",'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    logic.parse_creation_method(text)

    def test_other_attributes_cannot_override_the_creation_method(self):
        entry = {**self.entry, "extra": {"creation_method": "generated"}}
        with self.assertRaisesRegex(ValueError, "own field"):
            logic.build_chain([entry], [])

    def test_production_documents_export_and_evaluate(self):
        from test_production_documents import (
            TAKE, TRACKS, aaf_bytes, bwf_wav, recorder_wav, sound_report, store)

        take = recorder_wav(self.root, *TAKE)
        final = bwf_wav(self.root, "Final mix", "DAW", 0, "2024-12-18", seed=4)
        report = store(self.root, sound_report("2024-12-18", [
            [TAKE[0], TAKE[2], TAKE[3], "00:00:02:00", "08:49:43:00", "MixL", "MixR", "Boom", ""]]), "csv")
        aaf = store(self.root, aaf_bytes(self.root, [
            (TAKE[1], TAKE[0], TAKE[2], TAKE[3], TRACKS, TAKE[6], TAKE[5] + 0.5, 25)]), "aaf")
        files = []
        for alias, digest, kind, suffix in (
                ("take", take, "audio/raw-take", "wav"),
                ("mix", final, "audio/mix", "wav"),
                ("report", report, "text/sound-report", "csv"),
                ("edit", aaf, "project/aaf", "aaf")):
            files.append({
                "id": alias, "hash": digest, "path": str(self.root / "objects" / f"{digest}.{suffix}"),
                "type": kind, "creation_method": "recorded" if kind.startswith("audio/") else "",
                "technical": {"sample_rate_hz": 48000} if kind.startswith("audio/") else {},
                "production": {}, "extra": {}, "final": alias == "mix",
            })
        relationships = [
            {"source": "take", "target": "mix", "type": "mixed_from", "attrs": {}},
            {"source": "report", "target": "mix", "type": "input_to", "attrs": {}},
            {"source": "edit", "target": "mix", "type": "input_to", "attrs": {}},
        ]
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["chain"] = logic.build_chain(files, relationships)
        request["objects"] = logic.copied_object_paths(files)
        request["workflow"] = {
            "workflow_id": "custom_or_unknown_workflow", "modifiers": [],
            "description": "Recorder take and production documents supporting a final mix.",
            "declarations": {"submitter_confirmation": True},
        }
        run.validate(request, "native-request.schema.json")
        exported = self.root / "exported"
        exported.mkdir()
        path = exported / "submission.json"
        operations.export_bundle(request, files, path, True, lambda text: None, threading.Event())
        response = run.evaluate(json.loads(path.read_text()), exported, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        parsers = {item["parser"]: item["parser_status"] for item in response["findings"]
                   if item["code"] == "PARSER_OBSERVATION"}
        self.assertEqual(parsers.get("aaf"), "parsed")
        self.assertEqual(parsers.get("sound-report"), "parsed")


class CreationMethodDialogTests(unittest.TestCase):
    def test_dialog_validates_manual_input_and_preserves_imported_lists(self):
        import tkinter as tk
        from tkinter import ttk
        from builder_gui import FileDialog

        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        entry = {
            "id": "evidence", "type": "text/sound-report", "path": "",
            "hash": "a" * 64, "final": False, "role": "", "production": {},
            "technical": {}, "extra": {}, "creation_method": ["recorded", "ai-generated"],
        }
        with patch("builder_gui.Dialog.finish"):
            dialog = FileDialog(root, copy.deepcopy(entry), set(), [])
            imported = dialog.method_var.get()
            menus = [widget for widget in dialog.body.winfo_children() if isinstance(widget, ttk.Combobox)]
            self.assertTrue(any(tuple(widget.cget("values")) == tuple(logic.ALL_TYPES) for widget in menus))
            self.assertTrue(any(tuple(widget.cget("values")) == tuple(logic.CREATION_METHODS) for widget in menus))
            for method in ("generated", "sampled", '["recorded", "sampled"]'):
                dialog.method_var.set(method)
                with patch("builder_gui.messagebox.showerror") as error:
                    dialog.on_ok()
                    error.assert_called_once()
                    self.assertIsNone(dialog.result)
            dialog.method_var.set(imported)
            dialog.on_ok()
            self.assertEqual(dialog.result["creation_method"], entry["creation_method"])
            for kind in ("project/aaf", "text/sound-report"):
                current = {**entry, "type": kind, "creation_method": ""}
                dialog = FileDialog(root, current, set(), [])
                dialog.on_ok()
                self.assertEqual(dialog.result["type"], kind)


if __name__ == "__main__":
    unittest.main()
