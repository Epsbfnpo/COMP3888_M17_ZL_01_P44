#!/usr/bin/env python3
"""GUI for building a music-evidence native-request (manifest) JSON.

Needs manifest_logic.py next to it. Looks for
policies/workflow_evidence_policy.json (next to this file, or in the current
directory) and asks for it if it cannot be found.

Nothing is inferred: types, roles, the final artefact and every relationship
are exactly what you declare. Hashes and audio technical data (WAV, FLAC, MP3)
come from the files.
"""

from __future__ import annotations

import json
import copy
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import manifest_logic as L
import relationship_modes as RM
import wav_probe
import builder_operations as operations


def technical_summary(tech):
    """One line of measured audio properties; fields a format lacks are left out."""
    if not tech:
        return "no supported audio data"
    parts = [f"{tech['duration_seconds']} s" if tech.get("duration_seconds") is not None
             else "length not stated in the file",
             f"{tech['sample_rate_hz']} Hz", f"{tech['channels']} ch"]
    if tech.get("bit_depth") is not None:
        parts.append(f"{tech['bit_depth']}-bit")
    return ", ".join(parts)
from builder_results import INSUFFICIENT_SUPPORT_LABEL, attention_workflow_results
from builder_review import build_review_presentation, review_text, SCOPE_NOTE
from builder_report import render_review_html
from music_target import VERSION


def parse_json_object(text: str, label: str) -> dict:
    text = text.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def split_csv(text: str) -> list:
    return [p.strip() for p in text.split(",") if p.strip()]


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------
class Dialog(tk.Toplevel):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.result = None
        self.body = ttk.Frame(self, padding=10)
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(1, weight=1)
        self._row = 0

    def add_row(self, label, widget):
        ttk.Label(self.body, text=label).grid(
            row=self._row, column=0, sticky="nw", padx=(0, 8), pady=3)
        widget.grid(row=self._row, column=1, sticky="ew", pady=3)
        self._row += 1

    def finish(self, row=None):
        bar = ttk.Frame(self.body)
        bar.grid(row=self._row if row is None else row, column=0, columnspan=2,
                 sticky="e", pady=(10, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="OK", command=self.on_ok).pack(side="right", padx=6)
        self.grab_set()
        self.wait_window(self)

    def on_ok(self):
        raise NotImplementedError


class FileDialog(Dialog):
    def __init__(self, parent, entry, other_ids, roles):
        super().__init__(parent, "Artefact details")
        self.other_ids = other_ids
        name = Path(entry["path"]).name if entry["path"] else "(file not found on disk)"
        ttk.Label(self.body, text=name, font=("TkDefaultFont", 10, "bold")).grid(
            row=self._row, column=0, columnspan=2, sticky="w")
        self._row += 1
        info = f"sha256 {entry['hash'][:16]}…   " + technical_summary(entry["technical"])
        ttk.Label(self.body, text=info).grid(row=self._row, column=0, columnspan=2,
                                             sticky="w", pady=(0, 6))
        self._row += 1

        self.id_var = tk.StringVar(value=entry["id"])
        self.add_row("ID (alias)", ttk.Entry(self.body, textvariable=self.id_var))
        self.type_var = tk.StringVar(value=entry["type"])
        self.add_row("Artefact type", ttk.Combobox(
            self.body, textvariable=self.type_var, values=L.ALL_TYPES))
        self.method_var = tk.StringVar(value=L.creation_method_text(entry["creation_method"]))
        self.add_row("Creation method", ttk.Combobox(
            self.body, textvariable=self.method_var, values=L.CREATION_METHODS))
        ttk.Label(self.body, foreground="gray",
                  text='Multiple methods: ["recorded", "ai-generated"]').grid(
            row=self._row, column=1, sticky="w")
        self._row += 1
        self.role_var = tk.StringVar(value=entry["role"])
        self.add_row("Role (production.role)", ttk.Combobox(
            self.body, textvariable=self.role_var, values=[""] + roles))
        ttk.Label(self.body, foreground="gray",
                  text="The final artefact gets role 'final' automatically.").grid(
            row=self._row, column=1, sticky="w")
        self._row += 1
        self.final_var = tk.BooleanVar(value=entry["final"])
        self.add_row("Final artefact", ttk.Checkbutton(self.body, variable=self.final_var))
        self.prod_text = tk.Text(self.body, width=48, height=4)
        self.prod_text.insert("1.0", json.dumps(entry["production"], indent=2)
                              if entry["production"] else "")
        self.add_row("other production (JSON)", self.prod_text)
        self.extra_text = tk.Text(self.body, width=48, height=3)
        self.extra_text.insert("1.0", json.dumps(entry["extra"], indent=2) if entry["extra"] else "")
        self.add_row("other attributes (JSON)", self.extra_text)
        self.finish()

    def on_ok(self):
        try:
            new_id = self.id_var.get().strip()
            if not new_id:
                raise ValueError("ID must not be empty")
            if new_id in self.other_ids:
                raise ValueError(f"ID '{new_id}' is already used")
            if self.type_var.get().strip() not in L.SUPPORTED:
                raise ValueError("choose a registered artefact type")
            creation_method = L.parse_creation_method(self.method_var.get())
            production = parse_json_object(self.prod_text.get("1.0", "end"), "production")
            extra = parse_json_object(self.extra_text.get("1.0", "end"), "other attributes")
            clash = {"creation_method", "production", "technical"} & set(extra)
            if clash:
                raise ValueError(f"set {sorted(clash)} via their own fields")
            if "role" in production:
                raise ValueError("use the Role field instead of production.role")
        except ValueError as exc:
            messagebox.showerror("Invalid", str(exc), parent=self)
            return
        self.result = {"id": new_id, "type": self.type_var.get().strip(),
                       "creation_method": creation_method,
                       "role": self.role_var.get().strip(),
                       "final": self.final_var.get(),
                       "production": production, "extra": extra}
        self.destroy()


class RelDialog(Dialog):
    KEYMAP = {  # attribute key -> variable name
        "derivation_scope": "scope", "source_start_seconds": "ss",
        "source_end_seconds": "se", "target_start_seconds": "ts",
        "target_end_seconds": "te", "gain": "gain", "fade_in_seconds": "fi",
        "fade_out_seconds": "fo", "transformation_description": "trans",
        "claim_rationale": "why", "reference_pointer": "ref",
        "assertion_origin": "origin",
    }
    NUMERIC = {"source_start_seconds": "ss", "source_end_seconds": "se",
               "target_start_seconds": "ts", "target_end_seconds": "te",
               "gain": "gain", "fade_in_seconds": "fi", "fade_out_seconds": "fo"}

    def __init__(self, parent, files, rel=None):
        super().__init__(parent, "Relationship")
        self.files = {f["id"]: f for f in files}
        rel = rel or {"target": "", "source": "", "type": "",
                      "attrs": {"assertion_origin": "submitter"}}
        attrs = dict(rel["attrs"])
        names = ["target", "type", "source", *self.KEYMAP.values()]
        self.vars = {n: tk.StringVar() for n in names}
        self.vars["target"].set(rel["target"])
        self.vars["source"].set(rel["source"])
        self.vars["type"].set(rel["type"])
        for key, name in self.KEYMAP.items():
            if attrs.get(key) is not None:
                self.vars[name].set(str(attrs.pop(key)))
        self.confirmed = tk.BooleanVar(value=bool(attrs.pop("confirmed_by_submitter", False)))
        self.conversion = tk.BooleanVar(value=attrs.get("transformation") == "format_conversion")
        if self.conversion.get():
            attrs.pop("transformation")

        ids = list(self.files)
        v = self.vars
        self.type_box = ttk.Combobox(self.body, textvariable=v["type"],
                                     values=L.RELATIONSHIP_TYPES, state="readonly")
        self.scope_box = ttk.Combobox(self.body, textvariable=v["scope"])
        self.extra_text = tk.Text(self.body, width=44, height=3)
        self.extra_text.insert("1.0", json.dumps(attrs, indent=2) if attrs else "")
        self.suggest = ttk.Button(self.body, text="Suggest ranges from file durations",
                                  command=self.suggest_ranges)
        self.hint = ttk.Label(self.body, wraplength=380, justify="left",
                              foreground="#555555")
        self.order = [  # (name, label, widget)
            ("target", "Target (derived)", ttk.Combobox(
                self.body, textvariable=v["target"], values=ids, state="readonly")),
            ("type", "Relationship", self.type_box),
            ("source", "Source (origin)", ttk.Combobox(
                self.body, textvariable=v["source"], values=ids, state="readonly")),
            ("hint", "", self.hint),
            ("scope", "derivation_scope (legacy)", self.scope_box),
            ("ss", "source_start_seconds", ttk.Entry(self.body, textvariable=v["ss"])),
            ("se", "source_end_seconds", ttk.Entry(self.body, textvariable=v["se"])),
            ("ts", "target_start_seconds", ttk.Entry(self.body, textvariable=v["ts"])),
            ("te", "target_end_seconds", ttk.Entry(self.body, textvariable=v["te"])),
            ("suggest", "", self.suggest),
            ("gain", "gain (linear)", ttk.Entry(self.body, textvariable=v["gain"])),
            ("fi", "fade_in_seconds", ttk.Entry(self.body, textvariable=v["fi"])),
            ("fo", "fade_out_seconds", ttk.Entry(self.body, textvariable=v["fo"])),
            ("trans", "transformation_description",
             ttk.Entry(self.body, textvariable=v["trans"])),
            ("why", "claim_rationale", ttk.Entry(self.body, textvariable=v["why"])),
            ("ref", "reference_pointer", ttk.Entry(self.body, textvariable=v["ref"])),
            ("origin", "assertion_origin", ttk.Entry(self.body, textvariable=v["origin"])),
            ("confirmed", "confirmed_by_submitter",
             ttk.Checkbutton(self.body, variable=self.confirmed)),
            ("conversion", "same audio, other file format",
             ttk.Checkbutton(self.body, variable=self.conversion)),
            ("extra", "other attributes (JSON)", self.extra_text),
        ]
        self.labels = {name: ttk.Label(self.body, text=label) for name, label, _ in self.order}
        for name in ("target", "source"):
            self.widget(name).bind("<<ComboboxSelected>>", lambda _e: self.refresh_types())
        self.type_box.bind("<<ComboboxSelected>>", lambda _e: self.type_changed())
        self.refresh_types()
        self.layout()
        self.finish(row=99)

    def widget(self, name):
        return next(w for n, _l, w in self.order if n == name)

    def visible(self, name):
        if name == "hint":
            return True
        return RM.visible(self.vars["type"].get(), name)

    def layout(self):
        for name, _label, widget in self.order:
            self.labels[name].grid_remove()
            widget.grid_remove()
        row = 0
        self.hint.configure(text=RM.mode_help(self.vars["type"].get()))
        for name, _label, widget in self.order:
            if self.visible(name):
                if name not in ("suggest", "hint"):
                    self.labels[name].grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=3)
                widget.grid(row=row, column=1, sticky="ew", pady=3)
                row += 1

    def refresh_types(self):
        t = self.files.get(self.vars["target"].get())
        s = self.files.get(self.vars["source"].get())
        types = L.RELATIONSHIP_TYPES
        if t and s:
            types = [r for r in types if L.endpoint_ok(r, t["type"], s["type"])] or types
        self.type_box.configure(values=types)

    def type_changed(self):
        self.layout()

    def suggest_ranges(self):
        t = self.files.get(self.vars["target"].get())
        s = self.files.get(self.vars["source"].get())
        span = RM.suggest_range(t, s)
        if span is None:
            messagebox.showinfo("Ranges", "Choose target and source audio files with known "
                                "durations first.", parent=self)
            return
        for name in ("ss", "ts"):
            self.vars[name].set(str(span[0]))
        for name in ("se", "te"):
            self.vars[name].set(str(span[1]))

    def on_ok(self):
        try:
            v = {k: x.get().strip() for k, x in self.vars.items()}
            if not (v["target"] and v["source"] and v["type"]):
                raise ValueError("target, relationship and source are required")
            if v["target"] == v["source"]:
                raise ValueError("target and source must differ")
            attrs = {}
            if v["origin"]:
                attrs["assertion_origin"] = v["origin"]
            if self.confirmed.get():
                attrs["confirmed_by_submitter"] = True
            if self.conversion.get() and v["type"] == "derived_from":
                attrs["transformation"] = "format_conversion"
            if v["scope"]:  # legacy value from an older manifest; kept, not offered
                attrs["derivation_scope"] = v["scope"]
            for key, name in self.NUMERIC.items():
                if v[name]:
                    try:
                        attrs[key] = float(v[name])
                    except ValueError:
                        raise ValueError(f"{key} must be a number") from None
            for key, name in (("transformation_description", "trans"),
                              ("claim_rationale", "why"), ("reference_pointer", "ref")):
                if v[name]:
                    attrs[key] = v[name]
            for key, value in parse_json_object(self.extra_text.get("1.0", "end"),
                                                "other attributes").items():
                attrs.setdefault(key, value)
            rel = {"target": v["target"], "source": v["source"], "type": v["type"], "attrs": attrs}
            errors, warnings = RM.relationship_issues(
                rel, self.files[v["target"]], self.files[v["source"]], L.endpoint_ok)
            if errors:
                raise ValueError("\n".join(errors))
            if warnings and not messagebox.askyesno(
                    "Warnings", "\n".join(warnings) + "\n\nKeep this relationship anyway?",
                    parent=self):
                return
        except ValueError as exc:
            messagebox.showerror("Invalid", str(exc), parent=self)
            return
        self.result = rel
        self.destroy()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Evidence manifest builder — {VERSION}")
        self.geometry("1000x700")
        self.files, self.rels, self._counter = [], [], 0
        self.policy, self._forced = None, set()
        self.mod_vars, self.mod_buttons, self.flag_vars = {}, {}, {}
        self._job = None
        self._closing = False
        self._response = None
        self._review = None
        self._snapshot_signature = None
        self._pending_snapshot = None
        self.protocol("WM_DELETE_WINDOW", self.close)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self._case_tab(notebook)
        self._files_tab(notebook)
        self._rels_tab(notebook)
        self._check_tab(notebook)
        self._assessment_tab(notebook)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        self.copy_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Copy files into a portable submission folder",
                        variable=self.copy_var).pack(side="left")
        self.storage_hint = ttk.Label(self, text="References originals; keep them available at their current paths.")
        self.storage_hint.pack(fill="x", padx=12, pady=(0, 4))
        self.copy_var.trace_add("write", self._update_storage_hint)
        ttk.Button(bar, text="Save manifest…", command=self.save).pack(side="right")
        ttk.Button(bar, text="Preview JSON", command=self.preview).pack(side="right", padx=6)
        ttk.Button(bar, text="Load manifest…", command=self.load).pack(side="right")

        progress_bar = ttk.Frame(self)
        progress_bar.pack(fill="x", padx=8, pady=(0, 8))
        self.progress_label = ttk.Label(progress_bar, text="Ready")
        self.progress_label.pack(side="left", fill="x", expand=True)
        self.progress_spinner = ttk.Progressbar(progress_bar, mode="indeterminate", length=110)
        self.progress_spinner.pack(side="left", padx=6)
        self.cancel_button = ttk.Button(progress_bar, text="Cancel", command=self.cancel_job,
                                        state="disabled")
        self.cancel_button.pack(side="right")

        found = L.find_policy(Path(__file__).resolve().parent, Path.cwd())
        if found:
            self.set_policy(found)
        else:
            self.after(300, self.ask_policy)

        for var in [*self.v.values(), self.audio_var]:
            var.trace_add("write", self._check_snapshot)
        self.desc.bind("<<Modified>>", self._description_modified)

    # ---- policy ----------------------------------------------------------
    def ask_policy(self):
        path = filedialog.askopenfilename(
            title="Select workflow_evidence_policy.json", filetypes=[("JSON", "*.json")])
        if path:
            self.set_policy(Path(path))

    def set_policy(self, path):
        try:
            self.policy = L.load_policy(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Policy", f"Could not load policy: {exc}")
            return
        self.policy_label.configure(text=f"Policy: {Path(path).name} "
                                         f"v{self.policy.get('policy_version', '?')}")
        self.workflow_box.configure(values=[w["workflow_id"] for w in self.policy["workflows"]])
        for child in self.mod_frame.winfo_children():
            child.destroy()
        self.mod_vars, self.mod_buttons = {}, {}
        for i, name in enumerate(self.policy["workflow_selection"]["modifiers"]):
            var = tk.BooleanVar(value=False)
            btn = ttk.Checkbutton(self.mod_frame, text=name, variable=var)
            btn.grid(row=i // 2, column=i % 2, sticky="w", padx=6)
            self.mod_vars[name], self.mod_buttons[name] = var, btn
            var.trace_add("write", self._check_snapshot)
        self.on_workflow_change()

    def workflow(self):
        if not self.policy:
            return None
        return next((w for w in self.policy["workflows"]
                     if w["workflow_id"] == self.v["workflow_id"].get()), None)

    def on_workflow_change(self, _event=None):
        wf = self.workflow()
        defaults = set(wf.get("default_modifiers", [])) if wf else set()
        for name, var in self.mod_vars.items():
            btn = self.mod_buttons[name]
            if name in defaults:
                var.set(True)
                btn.configure(state="disabled", text=f"{name} (default)")
            else:
                if name in self._forced:
                    var.set(False)
                btn.configure(state="normal", text=name)
        self._forced = defaults
        previous = {k: x.get() for k, x in self.flag_vars.items()}
        for child in self.flag_frame.winfo_children():
            child.destroy()
        self.flag_vars = {}
        for i, name in enumerate(L.workflow_flags(wf) if wf else []):
            var = tk.BooleanVar(value=previous.get(name, False))
            ttk.Checkbutton(self.flag_frame, text=name, variable=var).grid(
                row=i, column=0, sticky="w", padx=6)
            self.flag_vars[name] = var
            var.trace_add("write", self._check_snapshot)
        if wf:
            self.hint.configure(text=f"{wf['when_applicable']}\nAllowed final types: "
                                     + ", ".join(wf["allowed_final_types"]))
        else:
            self.hint.configure(text="")
        self._check_snapshot()

    # ---- tabs ------------------------------------------------------------
    def _case_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Case & workflow")
        tab.columnconfigure(1, weight=1)
        self.v = {
            "run_id": tk.StringVar(value=str(uuid.uuid4())),
            "case_id": tk.StringVar(value="LOCAL-GENERATED-VALID"),
            "profile": tk.StringVar(value=L.PROFILE),
            "include_reasoning": tk.BooleanVar(value=True),
            "offline_only": tk.BooleanVar(value=True),
            "timeout": tk.StringVar(value="60"),
            "workflow_id": tk.StringVar(),
            "confirm": tk.BooleanVar(value=True),
            "contributors": tk.StringVar(),
        }
        self.workflow_box = ttk.Combobox(tab, textvariable=self.v["workflow_id"],
                                         state="readonly")
        self.workflow_box.bind("<<ComboboxSelected>>", self.on_workflow_change)
        self.policy_label = ttk.Label(tab, text="Policy: not loaded")
        rows = [
            ("Run ID", ttk.Entry(tab, textvariable=self.v["run_id"])),
            ("Case ID", ttk.Entry(tab, textvariable=self.v["case_id"])),
            ("Profile", ttk.Entry(tab, textvariable=self.v["profile"])),
            ("Include reasoning", ttk.Checkbutton(tab, variable=self.v["include_reasoning"])),
            ("Offline only", ttk.Checkbutton(tab, variable=self.v["offline_only"])),
            ("Timeout (seconds)", ttk.Entry(tab, textvariable=self.v["timeout"])),
            ("Workflow", self.workflow_box),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(tab, text=label).grid(row=i, column=0, sticky="w", padx=(0, 10), pady=3)
            widget.grid(row=i, column=1, sticky="ew", pady=3)
        r = len(rows)
        self.hint = ttk.Label(tab, foreground="gray", wraplength=620, justify="left")
        self.hint.grid(row=r, column=1, sticky="w")
        ttk.Button(tab, text="Load policy…", command=self.ask_policy).grid(
            row=r, column=0, sticky="w")
        self.policy_label.grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.mod_frame = ttk.LabelFrame(tab, text="Extra modifiers")
        self.mod_frame.grid(row=r + 2, column=0, columnspan=2, sticky="ew", pady=4)
        self.flag_frame = ttk.LabelFrame(
            tab, text="Workflow flags (used by this workflow's triggers)")
        self.flag_frame.grid(row=r + 3, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(tab, text="Workflow description").grid(row=r + 4, column=0, sticky="nw", pady=4)
        self.desc = tk.Text(tab, height=3)
        self.desc.grid(row=r + 4, column=1, sticky="ew", pady=4)
        ttk.Label(tab, text="Submitter confirmation").grid(row=r + 5, column=0, sticky="w")
        ttk.Checkbutton(tab, variable=self.v["confirm"]).grid(row=r + 5, column=1, sticky="w")
        ttk.Label(tab, text="Contributors (comma-separated)").grid(
            row=r + 6, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.v["contributors"]).grid(
            row=r + 6, column=1, sticky="ew")

    def _tree(self, tab, columns, widths):
        tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for col, width in zip(columns, widths):
            tree.heading(col, text=col)
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True)
        return tree

    def _files_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Files")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        for text, cmd in (("Add files…", self.add_files), ("Locate/replace…", self.locate_file),
                          ("Edit…", self.edit_file),
                          ("Mark final", self.mark_final), ("Remove", self.remove_file)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 6))
        self.file_tree = self._tree(
            tab, ("id", "file", "type", "role", "creation", "final", "hash"),
            (110, 200, 120, 110, 90, 50, 110))
        self.file_tree.bind("<Double-1>", lambda _e: self.edit_file())

    def _rels_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Relationships")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        for text, cmd in (("Add…", self.add_rel), ("Edit…", self.edit_rel),
                          ("Remove", self.remove_rel)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 6))
        self.rel_tree = self._tree(
            tab, ("target (derived)", "relationship", "source (origin)", "attributes"),
            (160, 130, 160, 440))
        self.rel_tree.bind("<Double-1>", lambda _e: self.edit_rel())

    def _check_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Preflight check")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Run preflight", command=self.run_check).pack(side="left")
        self.check_label = ttk.Label(
            bar, text="  Predicts completeness; the real run is authoritative.")
        self.check_label.pack(side="left")
        self.check_tree = self._tree(tab, ("rule", "level", "status", "weight", "reason"),
                                     (210, 90, 100, 60, 480))
        self.check_tree.tag_configure("satisfied", foreground="#1a7f37")
        self.check_tree.tag_configure("missing", foreground="#cf222e")
        self.check_tree.tag_configure("not_applicable", foreground="gray")

    def _assessment_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Full assessment")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Run assessment", command=self.run_assessment).pack(side="left")
        self.report_button = ttk.Button(bar, text="Save result JSON…", command=self.save_response,
                                        state="disabled")
        self.report_button.pack(side="left", padx=6)
        self.html_button = ttk.Button(bar, text="Save review report…", command=self.save_review_report,
                                      state="disabled")
        self.html_button.pack(side="left", padx=6)
        self.copy_report_button = ttk.Button(bar, text="Copy report text", command=self.copy_review_text,
                                             state="disabled")
        self.copy_report_button.pack(side="left", padx=6)
        self.audio_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(tab, text="Include audio diagnostics (report-only, slower; untick to skip)",
                        variable=self.audio_var).pack(anchor="w")
        self.assessment_label = ttk.Label(tab, text="No assessment run. Results describe a submission snapshot.",
                                          wraplength=950)
        self.assessment_label.pack(fill="x", pady=4)
        summary_frame = ttk.LabelFrame(tab, text="Assessment summary", padding=6)
        summary_frame.pack(fill="x")
        self.summary_label = ttk.Label(summary_frame, text="Run an assessment to see execution, core and audio states.",
                                       wraplength=930, justify="left")
        self.summary_label.pack(fill="x")
        self.scope_label = ttk.Label(summary_frame, text=SCOPE_NOTE, wraplength=930, justify="left")
        self.scope_label.pack(fill="x", pady=(4, 0))
        self.first_finding = ttk.Label(tab, text="", wraplength=950, justify="left")
        self.first_finding.pack(fill="x", pady=4)
        self.finding_button = ttk.Button(tab, text="View findings", command=self.view_findings, state="disabled")
        self.finding_button.pack(anchor="w")
        self.axis_tree = ttk.Treeview(tab, columns=("dimension", "availability", "value", "confidence", "status"),
                                      show="headings", height=4)
        for name, width in (("dimension", 210), ("availability", 95), ("value", 160),
                            ("confidence", 120), ("status", 325)):
            self.axis_tree.heading(name, text="Assessment confidence" if name == "confidence" else name.title())
            self.axis_tree.column(name, width=width, minwidth=65, stretch=True)
        self.axis_tree.pack(fill="x", pady=4)
        self.detail_notebook = ttk.Notebook(tab)
        self.detail_notebook.pack(fill="both", expand=True)
        def text_page(title):
            frame = ttk.Frame(self.detail_notebook)
            self.detail_notebook.add(frame, text=title)
            widget = tk.Text(frame, wrap="word", state="disabled", height=5)
            scroll = ttk.Scrollbar(frame, command=widget.yview)
            widget.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            widget.pack(fill="both", expand=True)
            return widget
        self.assessment_text = text_page("Findings and explanations")
        self.raw_text = text_page("All checks and raw evidence")
        tab.bind("<Configure>", self._resize_report)

    def _resize_report(self, event):
        width = max(250, event.width - 35)
        for label in (self.assessment_label, self.summary_label, self.scope_label, self.first_finding):
            label.configure(wraplength=width)

    def _submission_signature(self):
        return json.dumps({"files": self.files, "relationships": self.rels,
                           "case": {key: var.get() for key, var in self.v.items()},
                           "modifiers": {key: var.get() for key, var in self.mod_vars.items()},
                           "flags": {key: var.get() for key, var in self.flag_vars.items()},
                           "description": self.desc.get("1.0", "end").strip(),
                           "audio": self.audio_var.get(), "policy": self.policy}, sort_keys=True)

    def _description_modified(self, _event=None):
        if self.desc.edit_modified():
            self.desc.edit_modified(False)
            self._check_snapshot()

    def _check_snapshot(self, *_args):
        if self._review is None:
            return
        metadata = self._review["metadata"]
        stale = self._snapshot_signature != self._submission_signature()
        self.assessment_label.configure(text=("Showing previous assessment — submission or analysis options changed. "
            "Run again. " if stale else "Assessment snapshot. ") +
            f"case {metadata['case_id']} | run {metadata['run_id']} | status: {self._response.get('execution_status', 'unknown')} | "
            f"{metadata['snapshot_time']}")

    def view_findings(self):
        self.detail_notebook.select(0)
        self.assessment_text.see("first_review_item")
        self.assessment_text.focus_set()

    def _start_job(self, work, done):
        if self._job is not None:
            return
        events, cancel = queue.Queue(), threading.Event()
        states = []

        def freeze(parent):
            for widget in parent.winfo_children():
                if widget is not self.cancel_button and isinstance(
                        widget, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Checkbutton, tk.Text)):
                    states.append((widget, widget.cget("state")))
                    widget.configure(state="disabled")
                freeze(widget)

        freeze(self)
        self.cancel_button.configure(state="normal")
        self.progress_spinner.start()
        self.progress_label.configure(text="Starting…")
        self._job = (cancel, states)

        def worker():
            try:
                result = work(lambda text: events.put(("progress", text)), cancel)
                events.put(("done", result))
            except operations.Cancelled as exc:
                events.put(("cancelled", str(exc)))
            except Exception as exc:
                # Marshal worker failures to a visible main-thread notification.
                events.put(("error", f"{type(exc).__name__}: {exc}"))

        def poll():
            try:
                while True:
                    kind, value = events.get_nowait()
                    if kind == "progress":
                        self.progress_label.configure(text=value)
                        continue
                    self._job = None
                    for widget, state in states:
                        widget.configure(state=state)
                    self.cancel_button.configure(state="disabled")
                    self.progress_spinner.stop()
                    self.progress_label.configure(
                        text="Completed" if kind == "done" else value)
                    if self._closing:
                        self.destroy()
                    elif kind == "done":
                        done(value)
                    elif kind == "error":
                        messagebox.showerror("Operation failed", value, parent=self)
                    return
            except queue.Empty:
                self.after(75, poll)

        threading.Thread(target=worker, daemon=True).start()
        self.after(75, poll)

    def cancel_job(self):
        if self._job:
            self._job[0].set()
            self.cancel_button.configure(state="disabled")
            self.progress_label.configure(text="Cancelling…")

    def close(self):
        if self._job:
            self._closing = True
            self.cancel_job()
        else:
            self.destroy()

    def run_assessment(self):
        try:
            manifest = self.build_manifest()
        except (ValueError, L.PolicyError) as exc:
            messagebox.showerror("Cannot assess", str(exc), parent=self)
            return
        files = copy.deepcopy(self.files)
        audio = self.audio_var.get()
        self._pending_snapshot = {"signature": self._submission_signature(),
                                  "names": {item["hash"]: Path(item["path"]).name for item in files if item.get("path")},
                                  "audio_requested": audio}
        self._start_job(
            lambda progress, cancel: operations.assess_manifest(
                manifest, files, progress, cancel, audio),
            self.show_response)

    def show_response(self, response):
        # Builder transport notes are private, pre-existing metadata; never mutate the worker response.
        notes = response.get("_builder_notes", [])
        self._response = copy.deepcopy({key: value for key, value in response.items() if key != "_builder_notes"})
        snapshot = self._pending_snapshot or {
            "signature": self._submission_signature(), "audio_requested": None,
            "names": {item["hash"]: Path(item["path"]).name for item in self.files if item.get("path")}}
        self._pending_snapshot = None
        self._snapshot_signature = snapshot["signature"]
        self._review = build_review_presentation(self._response, snapshot["names"], self.policy,
            audio_requested=snapshot["audio_requested"], snapshot_time=datetime.now().astimezone().isoformat(timespec="seconds"),
            notes=notes)
        for button in (self.report_button, self.html_button, self.copy_report_button, self.finding_button):
            button.configure(state="normal")
        self._check_snapshot()
        self.summary_label.configure(text="\n".join(self._review["summary"]))
        visible = [item for item in self._review["items"] if item["group"] != "support"]
        first = visible[0] if visible else None
        self.first_finding.configure(text=(first["title"] + " — " + (first["affected"] if len(first["affected"]) < 130 else first["affected"][:127] + "…") + "\n" +
                                          "\n".join(first["explanation"].splitlines()[:3]) if first else
            "Read the execution, audio state and coverage above. Unassessed checks are not passes."),
            foreground="#985800" if first and first["group"] == "audio_review" else
                       "#a02a24" if first and first["group"] == "core" else "#263442")
        self.axis_tree.delete(*self.axis_tree.get_children())
        for axis in self._review["axes"]:
            self.axis_tree.insert("", "end", values=(axis["label"], axis["availability"], axis["value"],
                                                      axis["confidence"], axis["status"]))
        text = review_text(self._review)
        text = text[text.index("Items requiring attention"):]
        self.assessment_text.configure(state="normal")
        self.assessment_text.delete("1.0", "end")
        self.assessment_text.insert("1.0", text)
        position = self.assessment_text.search("Items requiring attention", "1.0", stopindex="end")
        self.assessment_text.mark_set("first_review_item", position or "1.0")
        self.assessment_text.tag_configure("muted", foreground="gray")
        position = "1.0"
        while True:
            position = self.assessment_text.search(INSUFFICIENT_SUPPORT_LABEL, position, stopindex="end")
            if not position:
                break
            self.assessment_text.tag_add("muted", f"{position} linestart", f"{position} lineend")
            position = f"{position} lineend"
        self.assessment_text.configure(state="disabled")
        self.assessment_text.yview_moveto(0)
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", "end")
        supporting = [item for item in self._review["items"] if item["group"] == "support"]
        self.raw_text.insert("1.0", "Other observations (not provenance)\n" +
            "\n\n".join(item["title"] + "\n" + item["explanation"] for item in supporting) +
            "\n\nComplete original machine response\n" + json.dumps(self._response, indent=2, ensure_ascii=False))
        self.raw_text.configure(state="disabled")
        self.detail_notebook.select(0)

    def copy_review_text(self):
        if self._review is not None:
            self.clipboard_clear()
            self.clipboard_append(review_text(self._review, include_support=True))

    def save_review_report(self):
        if self._review is None:
            return
        target = filedialog.asksaveasfilename(title="Save offline review report", defaultextension=".html",
            initialfile="assessment-review.html", filetypes=[("Offline HTML", "*.html")])
        if target:
            try:
                Path(target).write_text(render_review_html(self._review), encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Cannot save report", str(exc), parent=self)

    def save_response(self):
        if self._response is None:
            return
        target = filedialog.asksaveasfilename(
            title="Save assessment result", defaultextension=".json",
            initialfile="assessment-result.json", filetypes=[("JSON", "*.json")])
        if target:
            try:
                Path(target).write_text(
                    json.dumps(self._response, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Cannot save result", str(exc), parent=self)

    # ---- helpers ---------------------------------------------------------
    def selected(self, tree):
        if self._job is not None:
            return None
        sel = tree.selection()
        return int(sel[0]) if sel else None

    def _update_storage_hint(self, *_args):
        if not hasattr(self, "storage_hint"):
            return
        text = ("Copies verified originals into a portable objects/ folder."
                if self.copy_var.get()
                else "References originals; keep them available at their current paths.")
        self.storage_hint.configure(text=text)

    def refresh(self):
        self._check_snapshot()
        self.file_tree.delete(*self.file_tree.get_children())
        for i, f in enumerate(self.files):
            available = bool(f["path"] and Path(f["path"]).is_file())
            filename = Path(f["path"]).name if f["path"] else "(path not set)"
            if not available:
                filename = f"(missing: {filename})"
            self.file_tree.insert("", "end", iid=str(i), values=(
                f["id"], filename, f["type"],
                f["role"], f["creation_method"], "✔" if f["final"] else "",
                f["hash"][:12] + "…"))
        self.rel_tree.delete(*self.rel_tree.get_children())
        for i, r in enumerate(self.rels):
            self.rel_tree.insert("", "end", iid=str(i), values=(
                r["target"], r["type"], r["source"],
                json.dumps(r["attrs"]) if r["attrs"] else ""))

    def roles(self):
        return L.policy_roles(self.policy) if self.policy else []

    # ---- files -----------------------------------------------------------
    def add_files(self):
        paths = list(filedialog.askopenfilenames(title="Select artefact files"))
        if paths:
            self._start_job(
                lambda progress, cancel: operations.inspect_files(paths, progress, cancel),
                self._add_inspected_files)

    def locate_file(self):
        idx = self.selected(self.file_tree)
        if idx is None:
            messagebox.showinfo("Locate file", "Select a file row first.", parent=self)
            return
        entry = self.files[idx]
        selected = filedialog.askopenfilename(
            title=f"Locate original for {Path(entry['path']).name if entry['path'] else entry['id']}")
        if not selected:
            return
        try:
            actual = L.sha256_file(selected)
            if actual != entry["hash"]:
                raise ValueError(
                    f"{Path(selected).name} does not match the declared SHA-256 for "
                    f"{entry['id']}. The manifest identity was not changed.")
            entry["path"] = str(Path(selected).resolve())
            entry["technical"] = wav_probe.audio_technical(selected)
            entry["capabilities"] = wav_probe.audio_capabilities(selected)
            self.refresh()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot locate original", str(exc), parent=self)

    def _add_inspected_files(self, results):
        for result in results:
            path = Path(result["path"])
            if "error" in result:
                messagebox.showerror("Error", f"Could not read {path}: {result['error']}")
                continue
            digest = result["hash"]
            if any(f["hash"] == digest for f in self.files):
                messagebox.showwarning("Duplicate",
                                       f"{path.name} duplicates a file already added.")
                continue
            self._counter += 1
            entry = {"id": f"file-{self._counter}", "path": str(path), "hash": digest,
                     "type": "", "creation_method": "", "role": "", "final": False,
                     "production": {}, "extra": {},
                     "technical": result["technical"],
                     "capabilities": result.get("capabilities") or {}}
            dialog = FileDialog(self, entry, {f["id"] for f in self.files}, self.roles())
            if dialog.result is None:
                continue
            self.files.append({**entry, **dialog.result})
            if dialog.result["final"]:
                self._only_final(len(self.files) - 1)
            advice = wav_probe.format_advice(entry["capabilities"], dialog.result["type"])
            if advice:
                messagebox.showinfo("Format note", f"{path.name}:\n\n" + "\n\n".join(advice), parent=self)
            if dialog.result["type"].startswith("audio/") and not entry["technical"]:
                messagebox.showwarning(
                    "Unsupported audio",
                    f"{path.name} is typed as audio but could not be read as supported audio: "
                    f"{L.SUPPORTED_AUDIO_TEXT}. The evaluator will reject it.")
        self.refresh()

    def edit_file(self):
        idx = self.selected(self.file_tree)
        if idx is None:
            return
        entry = self.files[idx]
        others = {f["id"] for i, f in enumerate(self.files) if i != idx}
        dialog = FileDialog(self, entry, others, self.roles())
        if dialog.result is None:
            return
        old_id, new_id = entry["id"], dialog.result["id"]
        entry.update(dialog.result)
        for rel in self.rels:
            for key in ("target", "source"):
                if rel[key] == old_id:
                    rel[key] = new_id
        if entry["final"]:
            self._only_final(idx)
        self.refresh()

    def _only_final(self, keep):
        for i, f in enumerate(self.files):
            f["final"] = (i == keep)

    def mark_final(self):
        idx = self.selected(self.file_tree)
        if idx is not None:
            self._only_final(idx)
            self.refresh()

    def remove_file(self):
        idx = self.selected(self.file_tree)
        if idx is None:
            return
        gone = self.files.pop(idx)["id"]
        self.rels = [r for r in self.rels if gone not in (r["source"], r["target"])]
        self.refresh()

    # ---- relationships ---------------------------------------------------
    def add_rel(self):
        if len(self.files) < 2:
            messagebox.showinfo("Relationships", "Add at least two files first.")
            return
        dialog = RelDialog(self, self.files)
        if dialog.result:
            self.rels.append(dialog.result)
            self.refresh()

    def edit_rel(self):
        idx = self.selected(self.rel_tree)
        if idx is None:
            return
        dialog = RelDialog(self, self.files, self.rels[idx])
        if dialog.result:
            self.rels[idx] = dialog.result
            self.refresh()

    def remove_rel(self):
        idx = self.selected(self.rel_tree)
        if idx is not None:
            self.rels.pop(idx)
            self.refresh()

    # ---- build / check ---------------------------------------------------
    def workflow_input(self) -> dict:
        wf = self.workflow()
        if wf is None:
            raise ValueError("load the workflow policy and choose a workflow")
        defaults = set(wf.get("default_modifiers", []))
        description = self.desc.get("1.0", "end").strip()
        if not description:
            raise ValueError("Workflow description is required (the schema rejects an empty one)")
        result = {
            "workflow_id": wf["workflow_id"],
            "modifiers": [m for m, var in self.mod_vars.items()
                          if var.get() and m not in defaults],
            "description": description,
            "declarations": {"submitter_confirmation": self.v["confirm"].get(),
                             "contributors": split_csv(self.v["contributors"].get())},
        }
        result.update({name: True for name, var in self.flag_vars.items() if var.get()})
        return result

    def build_manifest(self, manifest_path=None) -> dict:
        try:
            timeout = int(self.v["timeout"].get())
            if timeout < 1:
                raise ValueError
        except ValueError:
            raise ValueError("timeout must be a positive whole number") from None
        try:
            uuid.UUID(self.v["run_id"].get().strip())
        except ValueError:
            raise ValueError("Run ID must be a valid UUID") from None
        for key, label in (("case_id", "Case ID"), ("profile", "Profile")):
            if not self.v[key].get().strip():
                raise ValueError(f"{label} is required")
        winput = self.workflow_input()
        chain = L.build_chain(self.files, self.rels)
        objects = (L.copied_object_paths(self.files) if self.copy_var.get()
                   else L.referenced_object_paths(
                       self.files, manifest_path))
        return {
            "run_id": self.v["run_id"].get().strip(),
            "case_id": self.v["case_id"].get().strip(),
            "profile": self.v["profile"].get().strip(),
            "options": {"include_reasoning": self.v["include_reasoning"].get(),
                        "offline_only": self.v["offline_only"].get(),
                        "timeout_seconds": timeout,
                        "local_file_references": not self.copy_var.get()},
            "workflow": winput,
            "chain": chain,
            "objects": {a["artefact_hash"]: objects[a["artefact_hash"]]
                        for a in chain["artefacts"]},
        }

    def predict(self):
        chain = L.build_chain(self.files, self.rels)
        bound = {f["hash"] for f in self.files if f["path"] and Path(f["path"]).is_file()}
        return L.evaluate_workflow(self.policy, self.workflow_input(), chain, bound)

    def run_check(self):
        try:
            result = self.predict()
        except (ValueError, L.PolicyError) as exc:
            messagebox.showerror("Cannot check", str(exc))
            return
        self.check_tree.delete(*self.check_tree.get_children())
        visible = attention_workflow_results(result["results"], self.policy)
        if not visible:
            self.check_tree.insert(
                "", "end", values=(
                    "-", "-", "-", "",
                    "No missing or unassessed workflow requirements were found in this preflight. Full evaluation is separate."))
        for r in visible:
            self.check_tree.insert("", "end", values=(
                r["rule_id"], r["level"], r["display_status"],
                "" if r["weight"] is None else r["weight"],
                f"{r['reason']} Suggested action: {r['suggestion']}"),
                tags=(r["status"],))
        value = result["value"]
        shown = "n/a" if value is None else f"{value:.2f}"
        text = (f"  {result['workflow_title']}: {result['numerator']:g}/"
                f"{result['denominator']:g} → completeness {shown}")
        if not result["allowed_final"]:
            text += ("  |  final type NOT allowed (needs "
                     + ", ".join(result["allowed_final_types"]) + ")")
        self.check_label.configure(text=text)

    def preview(self):
        try:
            text = json.dumps(self.build_manifest(), indent=2, ensure_ascii=False)
        except (ValueError, L.PolicyError) as exc:
            messagebox.showerror("Cannot build manifest", str(exc))
            return
        win = tk.Toplevel(self)
        win.title("Manifest preview")
        box = tk.Text(win, width=110, height=38)
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(fill="both", expand=True)

    def save(self):
        try:
            manifest = self.build_manifest()
            result = self.predict()
        except (ValueError, L.PolicyError) as exc:
            messagebox.showerror("Cannot build manifest", str(exc))
            return
        notes = RM.all_warnings(self.files, self.rels, L.endpoint_ok)
        if not result["allowed_final"]:
            notes.append("Final artefact type is not allowed by this workflow "
                         f"(allowed: {', '.join(result['allowed_final_types'])}).")
        notes += [f"Missing {r['level']} rule {r['rule_id']}: {r['reason']}"
                  for r in result["results"]
                  if r["level"] != "optional" and r["status"] == "missing"]
        if notes:
            shown = "\n".join("• " + n for n in notes[:14])
            more = f"\n… and {len(notes) - 14} more" if len(notes) > 14 else ""
            if not messagebox.askyesno("Review before saving",
                                       f"{shown}{more}\n\nSave anyway?"):
                return
        target = filedialog.asksaveasfilename(
            title="Save manifest", defaultextension=".json",
            initialfile="submission.json", filetypes=[("JSON", "*.json")])
        if not target:
            return
        target = Path(target)
        try:
            manifest = self.build_manifest(target)
        except (ValueError, L.PolicyError) as exc:
            messagebox.showerror("Cannot build manifest", str(exc))
            return
        files, copy_files = copy.deepcopy(self.files), self.copy_var.get()
        self._start_job(
            lambda progress, cancel: operations.export_bundle(
                manifest, files, target, copy_files, progress, cancel),
            lambda _result: messagebox.showinfo("Saved", f"Wrote verified bundle: {target}"))

    # ---- load ------------------------------------------------------------
    def load(self):
        source = filedialog.askopenfilename(title="Load manifest",
                                            filetypes=[("JSON", "*.json")])
        if not source:
            return
        source = Path(source)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            chain, objects = data["chain"], data.get("objects", {})
            files, alias = [], {}
            for n, art in enumerate(chain["artefacts"], 1):
                digest = art["artefact_hash"]
                rel = objects.get(digest)
                mapped = Path(rel) if rel else None
                path = (mapped if mapped and mapped.is_absolute()
                        else source.parent / mapped if mapped else None)
                attrs = dict(art.get("attributes", {}))
                production = dict(attrs.pop("production", {}))
                fid = f"{art['artefact_type'].split('/')[-1]}-{n}"
                alias[digest] = fid
                files.append({
                    "id": fid, "hash": digest, "type": art["artefact_type"],
                    "path": str(path.resolve()) if path else "",
                    "creation_method": attrs.pop("creation_method", ""),
                    "role": production.pop("role", ""), "production": production,
                    "technical": attrs.pop("technical", {}) or
                                 (wav_probe.audio_technical(path) if path and path.is_file() else {}),
                    "extra": attrs,
                    "final": digest == chain["final_artefact_hash"]})
            rels = [{"target": alias[a["artefact_hash"]], "source": alias[e["hash"]],
                     "type": e["relationship_type"], "attrs": e.get("attributes", {})}
                    for a in chain["artefacts"] for e in a.get("evidence", [])]
            options, workflow = data.get("options", {}), data.get("workflow", {})
            decl = workflow.get("declarations", {})
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            messagebox.showerror("Error", f"Could not load manifest: {exc!r}")
            return
        self.files, self.rels, self._counter = files, rels, len(files)
        self.copy_var.set(not options.get("local_file_references", False))
        self.v["run_id"].set(data.get("run_id", str(uuid.uuid4())))
        self.v["case_id"].set(data.get("case_id", ""))
        self.v["profile"].set(data.get("profile", L.PROFILE))
        self.v["include_reasoning"].set(options.get("include_reasoning", True))
        self.v["offline_only"].set(options.get("offline_only", True))
        self.v["timeout"].set(str(options.get("timeout_seconds", 60)))
        self.v["workflow_id"].set(workflow.get("workflow_id", ""))
        self.on_workflow_change()
        for name in workflow.get("modifiers", []):
            if name in self.mod_vars:
                self.mod_vars[name].set(True)
        for name, var in self.flag_vars.items():
            var.set(bool(workflow.get(name)))
        self.v["confirm"].set(decl.get("submitter_confirmation", False))
        self.v["contributors"].set(", ".join(decl.get("contributors", [])))
        self.desc.delete("1.0", "end")
        self.desc.insert("1.0", workflow.get("description", ""))
        self.refresh()
        missing = [f["path"] or f["id"] for f in files
                   if not f["path"] or not Path(f["path"]).is_file()]
        if missing:
            messagebox.showwarning("Files not found",
                                   "Some original files are missing. Select each file row and "
                                   "use Locate/replace… to choose the original file with the "
                                   "same SHA-256. Declared hashes will not be changed:\n"
                                   + "\n".join(missing))


if __name__ == "__main__":
    App().mainloop()
