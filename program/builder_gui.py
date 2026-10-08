#!/usr/bin/env python3
"""GUI for building a music-evidence native-request (manifest) JSON.

Needs manifest_logic.py next to it. Looks for
policies/workflow_evidence_policy.json (next to this file, or in the current
directory) and asks for it if it cannot be found.

Nothing is inferred: types, roles, the final artefact and every relationship
are exactly what you declare. Hashes and WAV technical data come from the files.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import manifest_logic as L


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
        tech = entry["technical"]
        info = (f"sha256 {entry['hash'][:16]}…   "
                + (f"{tech['duration_seconds']} s, {tech['sample_rate_hz']} Hz, "
                   f"{tech['channels']} ch, {tech['bit_depth']}-bit" if tech else "no WAV data"))
        ttk.Label(self.body, text=info).grid(row=self._row, column=0, columnspan=2,
                                             sticky="w", pady=(0, 6))
        self._row += 1

        self.id_var = tk.StringVar(value=entry["id"])
        self.add_row("ID (alias)", ttk.Entry(self.body, textvariable=self.id_var))
        self.type_var = tk.StringVar(value=entry["type"])
        self.add_row("Artefact type", ttk.Combobox(
            self.body, textvariable=self.type_var, values=L.ALL_TYPES))
        self.method_var = tk.StringVar(value=entry["creation_method"])
        self.add_row("Creation method", ttk.Combobox(
            self.body, textvariable=self.method_var, values=L.CREATION_METHODS))
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
                       "creation_method": self.method_var.get().strip(),
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

        ids = list(self.files)
        v = self.vars
        self.type_box = ttk.Combobox(self.body, textvariable=v["type"],
                                     values=L.RELATIONSHIP_TYPES, state="readonly")
        self.scope_box = ttk.Combobox(self.body, textvariable=v["scope"])
        self.extra_text = tk.Text(self.body, width=44, height=3)
        self.extra_text.insert("1.0", json.dumps(attrs, indent=2) if attrs else "")
        self.suggest = ttk.Button(self.body, text="Suggest ranges from file durations",
                                  command=self.suggest_ranges)
        self.order = [  # (name, label, widget)
            ("target", "Target (derived)", ttk.Combobox(
                self.body, textvariable=v["target"], values=ids, state="readonly")),
            ("type", "Relationship", self.type_box),
            ("source", "Source (origin)", ttk.Combobox(
                self.body, textvariable=v["source"], values=ids, state="readonly")),
            ("scope", "derivation_scope", self.scope_box),
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
            ("extra", "other attributes (JSON)", self.extra_text),
        ]
        self.labels = {name: ttk.Label(self.body, text=label) for name, label, _ in self.order}
        for name in ("target", "source"):
            self.widget(name).bind("<<ComboboxSelected>>", lambda _e: self.refresh_types())
        self.type_box.bind("<<ComboboxSelected>>", lambda _e: self.type_changed())
        self.scope_box.configure(values=L.SCOPES.get(v["type"].get(), []))
        self.refresh_types()
        self.layout()
        self.finish(row=99)

    def widget(self, name):
        return next(w for n, _l, w in self.order if n == name)

    def visible(self, name):
        rtype = self.vars["type"].get()
        if name in ("scope", "ss", "se", "ts", "te", "suggest"):
            return rtype in L.SCOPES
        if name == "gain":
            return rtype in L.LINEAR_TYPES
        if name in ("fi", "fo"):
            return rtype == "edited_from"
        return True

    def layout(self):
        for name, _label, widget in self.order:
            self.labels[name].grid_remove()
            widget.grid_remove()
        row = 0
        for name, _label, widget in self.order:
            if self.visible(name):
                if name != "suggest":
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
        options = L.SCOPES.get(self.vars["type"].get(), [])
        self.scope_box.configure(values=options)
        if options and self.vars["scope"].get() not in options:
            self.vars["scope"].set(options[0])
        self.layout()

    def suggest_ranges(self):
        t = self.files.get(self.vars["target"].get())
        s = self.files.get(self.vars["source"].get())
        td = ((t or {}).get("technical") or {}).get("duration_seconds")
        sd = ((s or {}).get("technical") or {}).get("duration_seconds")
        if td is None or sd is None:
            messagebox.showinfo("Ranges", "Choose target and source WAV files first.", parent=self)
            return
        end = round(min(td, sd, L.MAX_ANALYSIS_SECONDS), 3)
        for name in ("ss", "ts"):
            self.vars[name].set("0")
        for name in ("se", "te"):
            self.vars[name].set(str(end))

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
            if self.visible("scope") and v["scope"]:
                attrs["derivation_scope"] = v["scope"]
            for key, name in self.NUMERIC.items():
                if self.visible(name) and v[name]:
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
            errors, warnings = L.relationship_issues(
                rel, self.files[v["target"]], self.files[v["source"]])
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
        self.title("Evidence manifest builder")
        self.geometry("1000x700")
        self.files, self.rels, self._counter = [], [], 0
        self.policy, self._forced = None, set()
        self.mod_vars, self.mod_buttons, self.flag_vars = {}, {}, {}

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self._case_tab(notebook)
        self._files_tab(notebook)
        self._rels_tab(notebook)
        self._check_tab(notebook)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        self.copy_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Copy files into objects/ next to the manifest",
                        variable=self.copy_var).pack(side="left")
        ttk.Button(bar, text="Save manifest…", command=self.save).pack(side="right")
        ttk.Button(bar, text="Preview JSON", command=self.preview).pack(side="right", padx=6)
        ttk.Button(bar, text="Load manifest…", command=self.load).pack(side="right")

        found = L.find_policy(Path(__file__).resolve().parent, Path.cwd())
        if found:
            self.set_policy(found)
        else:
            self.after(300, self.ask_policy)

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
        if wf:
            self.hint.configure(text=f"{wf['when_applicable']}\nAllowed final types: "
                                     + ", ".join(wf["allowed_final_types"]))
        else:
            self.hint.configure(text="")

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
        for text, cmd in (("Add files…", self.add_files), ("Edit…", self.edit_file),
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
        notebook.add(tab, text="Workflow check")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Run check", command=self.run_check).pack(side="left")
        self.check_label = ttk.Label(
            bar, text="  Predicts completeness; the real run is authoritative.")
        self.check_label.pack(side="left")
        self.check_tree = self._tree(tab, ("rule", "level", "status", "weight", "reason"),
                                     (210, 90, 100, 60, 480))
        self.check_tree.tag_configure("satisfied", foreground="#1a7f37")
        self.check_tree.tag_configure("missing", foreground="#cf222e")
        self.check_tree.tag_configure("not_applicable", foreground="gray")

    # ---- helpers ---------------------------------------------------------
    def selected(self, tree):
        sel = tree.selection()
        return int(sel[0]) if sel else None

    def refresh(self):
        self.file_tree.delete(*self.file_tree.get_children())
        for i, f in enumerate(self.files):
            self.file_tree.insert("", "end", iid=str(i), values=(
                f["id"], Path(f["path"]).name if f["path"] else "(missing)", f["type"],
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
        for raw in filedialog.askopenfilenames(title="Select artefact files"):
            path = Path(raw)
            try:
                digest = L.sha256_file(path)
            except OSError as exc:
                messagebox.showerror("Error", f"Could not read {path}: {exc}")
                continue
            if any(f["hash"] == digest for f in self.files):
                messagebox.showwarning("Duplicate",
                                       f"{path.name} duplicates a file already added.")
                continue
            self._counter += 1
            is_wav = path.suffix.lower() in (".wav", ".wave")
            entry = {"id": f"file-{self._counter}", "path": str(path), "hash": digest,
                     "type": "", "creation_method": "", "role": "", "final": False,
                     "production": {}, "extra": {},
                     "technical": L.wav_technical(path) if is_wav else {}}
            dialog = FileDialog(self, entry, {f["id"] for f in self.files}, self.roles())
            if dialog.result is None:
                continue
            self.files.append({**entry, **dialog.result})
            if dialog.result["final"]:
                self._only_final(len(self.files) - 1)
            if dialog.result["type"].startswith("audio/") and not entry["technical"]:
                messagebox.showwarning(
                    "Not integer-PCM WAV",
                    f"{path.name} is typed as audio but could not be read as integer-PCM "
                    "WAV; the evaluator will reject it.")
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

    def build_manifest(self) -> dict:
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
        by_hash = {f["hash"]: f for f in self.files}
        return {
            "run_id": self.v["run_id"].get().strip(),
            "case_id": self.v["case_id"].get().strip(),
            "profile": self.v["profile"].get().strip(),
            "options": {"include_reasoning": self.v["include_reasoning"].get(),
                        "offline_only": self.v["offline_only"].get(),
                        "timeout_seconds": timeout},
            "workflow": winput,
            "chain": chain,
            "objects": {a["artefact_hash"]: L.object_rel_path(by_hash[a["artefact_hash"]])
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
        for r in result["results"]:
            self.check_tree.insert("", "end", values=(
                r["rule_id"], r["level"], r["status"],
                "" if r["weight"] is None else r["weight"], r["reason"]), tags=(r["status"],))
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
        notes = L.all_warnings(self.files, self.rels)
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
        target, skipped = Path(target), []
        try:
            target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
            if self.copy_var.get():
                for f in self.files:
                    if not f["path"]:
                        skipped.append(f["id"])
                        continue
                    dest = target.parent / L.object_rel_path(f)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.resolve() != Path(f["path"]).resolve():
                        shutil.copy2(f["path"], dest)
        except OSError as exc:
            messagebox.showerror("Error", f"Could not save: {exc}")
            return
        extra = f"\nNot copied (file missing): {', '.join(skipped)}" if skipped else ""
        messagebox.showinfo("Saved", f"Wrote {target}{extra}")

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
                path = source.parent / rel if rel else None
                attrs = dict(art.get("attributes", {}))
                production = dict(attrs.pop("production", {}))
                fid = f"{art['artefact_type'].split('/')[-1]}-{n}"
                alias[digest] = fid
                files.append({
                    "id": fid, "hash": digest, "type": art["artefact_type"],
                    "path": str(path) if path and path.is_file() else "",
                    "creation_method": attrs.pop("creation_method", ""),
                    "role": production.pop("role", ""), "production": production,
                    "technical": attrs.pop("technical", {}), "extra": attrs,
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
        missing = [f["id"] for f in files if not f["path"]]
        if missing:
            messagebox.showwarning("Files not found",
                                   "These object files were not found next to the manifest: "
                                   + ", ".join(missing))


if __name__ == "__main__":
    App().mainloop()
