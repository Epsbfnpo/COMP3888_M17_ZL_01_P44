"""Pure logic for the manifest GUI (no tkinter), mirroring music_target.

Mirrors, without importing: domain.py (types, endpoints), contract.py/engine.py
(limits), audio_derivation.py (derivation fields/scopes), and
workflow_policy.py (completeness rules). The real run is always authoritative;
this module only lets the GUI catch problems before you save.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import math
import re
import wave
from pathlib import Path

SCHEMA_VERSION = "0.0.3"
HASH_METHOD = "sha256"
PROFILE = "music-native-v0.1"

MAX_NODES = 128
MAX_FILE_BYTES = 64 * 1024 * 1024      # engine.py
MAX_TOTAL_BYTES = 256 * 1024 * 1024    # engine.py
MAX_ANALYSIS_SECONDS = 60              # audio_derivation.py

AUDIO_TYPES = (
    "audio/raw-take", "audio/raw-track", "audio/edited-take", "audio/stem",
    "audio/mix", "audio/master", "audio/sample", "audio/reference",
    "audio/ai-source", "audio/unclassified",
)
DOCUMENT_TYPES = (
    "project/midi", "project/daw-session", "project/automation",
    "metadata/ddex-rin", "metadata/ddex-ern", "provenance/c2pa",
    "text/cue-sheet", "text/comp-sheet", "text/session-log",
    "text/license-clearance", "text/generation-record",
)
ALL_TYPES = AUDIO_TYPES + DOCUMENT_TYPES
SUPPORTED = {"audio", *ALL_TYPES}

_RAW = {"audio/raw-take", "audio/raw-track"}
ENDPOINTS = {  # relationship -> (allowed target types, allowed source types)
    "derived_from": (SUPPORTED, SUPPORTED),
    "edited_from": ({"audio/edited-take"}, _RAW | {"audio/edited-take"}),
    "excerpted_from": ({"audio/sample"}, SUPPORTED),
    "comped_from": ({"audio/edited-take"}, {"audio/raw-take"}),
    "stemmed_from": ({"audio/stem"},
                     {"audio/raw-track", "audio/edited-take", "audio/sample"}),
    "mixed_from": ({"audio/mix"}, {"audio/stem", "audio/raw-track",
                                   "audio/edited-take", "audio/sample"}),
    "mastered_from": ({"audio/master"}, {"audio/mix"}),
    "rendered_from": (set(AUDIO_TYPES),
                      {"project/midi", "project/daw-session", "project/automation"}),
    "input_to": (SUPPORTED, SUPPORTED),
}
RELATIONSHIP_TYPES = list(ENDPOINTS)

# derivation_scope values accepted by each content-checked relationship
SCOPES = {
    "mixed_from": ["linear_mix"],
    "stemmed_from": ["linear_stem", "linear_mix"],
    "edited_from": ["linear_edit"],
    "mastered_from": ["master_similarity"],
}
TIME_FIELDS = ("source_start_seconds", "source_end_seconds",
               "target_start_seconds", "target_end_seconds")
LINEAR_FIELDS = TIME_FIELDS + ("gain",)
LINEAR_TYPES = ("mixed_from", "stemmed_from", "edited_from")
COMPLEX_WORDS = ("compress", "limiter", "master", "time stretch", "pitch shift",
                 "distortion", "saturat", "reverb", "nonlinear")
CREATION_METHODS = ["", "recorded", "synthesised", "generated", "sampled",
                    "ai-generated"]


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wav_technical(path) -> dict:
    """Objective fields from an integer-PCM WAV; {} if unreadable."""
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as handle:
            rate = handle.getframerate()
            return {
                "duration_seconds": round(handle.getnframes() / rate, 6),
                "sample_rate_hz": rate,
                "channels": handle.getnchannels(),
                "bit_depth": handle.getsampwidth() * 8,
            }
    except (wave.Error, EOFError, OSError, ZeroDivisionError):
        return {}


def object_rel_path(entry: dict) -> str:
    suffix = Path(entry["path"]).suffix.lower() if entry.get("path") else ".wav"
    return f"objects/{entry['hash']}{suffix}"


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------
def find_policy(*start_dirs):
    for base in start_dirs:
        base = Path(base)
        for rel in ("policies/workflow_evidence_policy.json",
                    "../policies/workflow_evidence_policy.json",
                    "workflow_evidence_policy.json"):
            candidate = (base / rel).resolve()
            if candidate.is_file():
                return candidate
    return None


def load_policy(path) -> dict:
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(policy.get("workflows"), list) or "workflow_selection" not in policy:
        raise ValueError("not a workflow evidence policy file")
    return policy


def policy_roles(policy) -> list:
    roles = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("role", "source_role") and isinstance(item, str):
                    roles.add(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(policy["workflows"])
    walk(policy.get("common_rules", {}))
    roles.discard("final")  # assigned automatically to the final artefact
    return sorted(roles)


def workflow_flags(workflow) -> list:
    """workflow.<name> flags that appear in this workflow's triggers."""
    found = set()
    for rule in workflow.get("conditional", []):
        found.update(re.findall(r"workflow\.([A-Za-z_]+)", rule.get("trigger", "")))
    found.discard("description")
    return sorted(found)


# --------------------------------------------------------------------------
# Relationship checks
# --------------------------------------------------------------------------
def endpoint_ok(rtype, target_type, source_type) -> bool:
    ends = ENDPOINTS.get(rtype)
    return bool(ends) and target_type in ends[0] and source_type in ends[1]


def _number(attrs, key):
    value = attrs.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    return float(value)


def relationship_issues(rel, target, source):
    """Return (errors, warnings) for one declared relationship."""
    errors, warnings = [], []
    rtype, attrs = rel["type"], rel["attrs"]
    if rtype not in ENDPOINTS:
        return [f"'{rtype}' is not a registered relationship type"], warnings
    if not endpoint_ok(rtype, target["type"], source["type"]):
        errors.append(f"{rtype} cannot link {source['type']} (source) to "
                      f"{target['type']} (target)")
    try:
        nums = {k: _number(attrs, k) for k in LINEAR_FIELDS +
                ("fade_in_seconds", "fade_out_seconds")}
    except ValueError as exc:
        return errors + [str(exc)], warnings

    if rtype in SCOPES:
        fields = TIME_FIELDS if rtype == "mastered_from" else LINEAR_FIELDS
        missing = [f for f in ("derivation_scope",) + fields if attrs.get(f) is None]
        if missing:
            warnings.append(f"{rtype}: content check will be unavailable; "
                            f"missing {', '.join(missing)}")
        scope = attrs.get("derivation_scope")
        if scope is not None and scope not in SCOPES[rtype]:
            warnings.append(f"{rtype}: derivation_scope must be "
                            f"{' or '.join(SCOPES[rtype])}")

    ss, se, ts, te = (nums[k] for k in TIME_FIELDS)
    if ss is not None and se is not None and se <= ss:
        errors.append("source_end_seconds must be after source_start_seconds")
    if ts is not None and te is not None and te <= ts:
        errors.append("target_end_seconds must be after target_start_seconds")
    if None not in (ss, se, ts, te):
        tol = 0.005 if rtype == "mastered_from" else 0.002
        if abs((se - ss) - (te - ts)) > tol:
            warnings.append(f"{rtype}: source and target ranges differ in length "
                            "(no time-stretch model; the content check will fail)")
    if rtype in LINEAR_TYPES and any(v is not None and v > MAX_ANALYSIS_SECONDS
                                     for v in (se, te)):
        warnings.append(f"{rtype}: only the first {MAX_ANALYSIS_SECONDS} s of audio "
                        "is analysed; later ranges cannot be checked")

    s_tech, t_tech = source.get("technical") or {}, target.get("technical") or {}
    if se is not None and s_tech.get("duration_seconds") is not None \
            and se > s_tech["duration_seconds"]:
        errors.append(f"source_end_seconds ({se}) exceeds the source file "
                      f"({s_tech['duration_seconds']} s)")
    if te is not None and t_tech.get("duration_seconds") is not None \
            and te > t_tech["duration_seconds"]:
        errors.append(f"target_end_seconds ({te}) exceeds the target file "
                      f"({t_tech['duration_seconds']} s)")
    for attr, side, key in (("input_sample_rate_hz", s_tech, "sample_rate_hz"),
                            ("output_sample_rate_hz", t_tech, "sample_rate_hz"),
                            ("input_bit_depth", s_tech, "bit_depth"),
                            ("output_bit_depth", t_tech, "bit_depth")):
        if attrs.get(attr) is not None and side.get(key) is not None \
                and attrs[attr] != side[key]:
            errors.append(f"{attr} ({attrs[attr]}) does not match the file ({side[key]})")

    if rtype in LINEAR_TYPES:
        for key in ("sample_rate_hz", "channels"):
            if s_tech.get(key) and t_tech.get(key) and s_tech[key] != t_tech[key]:
                warnings.append(f"{rtype}: source and target {key} differ "
                                f"({s_tech[key]} vs {t_tech[key]}); the content "
                                "check will be unavailable")
        words = str(attrs.get("transformation_description") or "").casefold()
        hits = [w for w in COMPLEX_WORDS if w in words]
        if hits:
            warnings.append(f"{rtype}: transformation_description mentions "
                            f"{', '.join(hits)}; the content check will be skipped "
                            "as outside the constant-gain model")
    return errors, warnings


def all_warnings(files, rels) -> list:
    by_id = {f["id"]: f for f in files}
    out = []
    for rel in rels:
        if rel["target"] in by_id and rel["source"] in by_id:
            _, warnings = relationship_issues(rel, by_id[rel["target"]], by_id[rel["source"]])
            out.extend(f"{rel['target']} ← {rel['source']}: {w}" for w in warnings)
    return out


# --------------------------------------------------------------------------
# Chain
# --------------------------------------------------------------------------
def build_chain(files, rels) -> dict:
    if not files:
        raise ValueError("add at least one file")
    if len(files) > MAX_NODES:
        raise ValueError(f"at most {MAX_NODES} artefacts are supported")
    finals = [f for f in files if f["final"]]
    if len(finals) != 1:
        raise ValueError(f"exactly one file must be final; found {len(finals)}")

    seen, total = {}, 0
    for f in files:
        if f["type"] not in SUPPORTED:
            raise ValueError(f"'{f['id']}' has an unregistered artefact type: {f['type']!r}")
        if f["hash"] in seen:
            raise ValueError(f"'{f['id']}' and '{seen[f['hash']]}' have identical content")
        seen[f["hash"]] = f["id"]
        if f["type"].startswith("audio/") and not f["technical"]:
            raise ValueError(f"'{f['id']}' is typed as audio but is not readable as "
                             "integer-PCM WAV (the evaluator would reject it)")
        if f.get("path") and Path(f["path"]).is_file():
            size = Path(f["path"]).stat().st_size
            total += size
            if size > MAX_FILE_BYTES:
                raise ValueError(f"'{f['id']}' is {size / 2**20:.1f} MiB; "
                                 f"limit is {MAX_FILE_BYTES // 2**20} MiB per file")
    if total > MAX_TOTAL_BYTES:
        raise ValueError(f"total size {total / 2**20:.1f} MiB exceeds "
                         f"{MAX_TOTAL_BYTES // 2**20} MiB")

    by_id = {f["id"]: f for f in files}
    incoming, pairs = {f["id"]: [] for f in files}, set()
    for rel in rels:
        if rel["source"] not in by_id or rel["target"] not in by_id:
            raise ValueError(f"relationship references a missing file: {rel}")
        if rel["source"] == rel["target"]:
            raise ValueError(f"'{rel['source']}' cannot be derived from itself")
        if (rel["target"], rel["source"]) in pairs:
            raise ValueError(f"'{rel['target']}' has more than one relationship "
                             f"to '{rel['source']}'")
        pairs.add((rel["target"], rel["source"]))
        errors, _ = relationship_issues(rel, by_id[rel["target"]], by_id[rel["source"]])
        if errors:
            raise ValueError(f"{rel['target']} ← {rel['source']}: {errors[0]}")
        incoming[rel["target"]].append(rel)

    reachable, stack = set(), [finals[0]["id"]]
    while stack:
        cur = stack.pop()
        if cur not in reachable:
            reachable.add(cur)
            stack.extend(r["source"] for r in incoming[cur])
    if reachable != set(by_id):
        raise ValueError("not connected to the final artefact: "
                         + ", ".join(sorted(set(by_id) - reachable)))

    order, done, visiting = [], set(), set()

    def visit(fid):
        if fid in done:
            return
        if fid in visiting:
            raise ValueError(f"relationship cycle involving '{fid}'")
        visiting.add(fid)
        for rel in incoming[fid]:
            visit(rel["source"])
        visiting.discard(fid)
        done.add(fid)
        order.append(fid)

    for f in files:
        visit(f["id"])

    artefacts = []
    for fid in order:
        f = by_id[fid]
        attributes = {}
        if f["creation_method"]:
            attributes["creation_method"] = f["creation_method"]
        production = dict(f["production"])
        if f.get("role"):
            production["role"] = f["role"]
        attributes["production"] = production
        if f["technical"]:
            attributes["technical"] = f["technical"]
        attributes.update(f["extra"])
        artefacts.append({
            "artefact_hash": f["hash"],
            "artefact_type": f["type"],
            "attributes": attributes,
            "evidence": [{"hash": by_id[r["source"]]["hash"],
                          "relationship_type": r["type"],
                          "attributes": r["attrs"]} for r in incoming[fid]],
        })
    return {"schema_version": SCHEMA_VERSION, "hash_method": HASH_METHOD,
            "final_artefact_hash": finals[0]["hash"], "artefacts": artefacts}


# --------------------------------------------------------------------------
# Workflow completeness preview (port of workflow_policy.py)
# --------------------------------------------------------------------------
class PolicyError(ValueError):
    pass


def _resolve(node, context):
    if isinstance(node, ast.Name):
        if node.id in ("true", "false"):
            return node.id == "true"
        if node.id not in context:
            raise PolicyError(f"Unknown trigger name: {node.id}")
        return context[node.id]
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, context)
        return base.get(node.attr, False) if isinstance(base, dict) else False
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
        return node.value
    raise PolicyError("Unsupported value in workflow trigger.")


def evaluate_trigger(expression, context):
    tree = ast.parse(expression, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.BoolOp):
            values = [bool(visit(i)) for i in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not bool(visit(node.operand))
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = _resolve(node.left, context), _resolve(node.comparators[0], context)
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if isinstance(node, (ast.Name, ast.Attribute, ast.Constant)):
            return bool(_resolve(node, context))
        raise PolicyError("Unsupported operation in workflow trigger.")
    return bool(visit(tree))


def _role(node, final_hash):
    if node["artefact_hash"] == final_hash:
        return "final"
    attributes = node.get("attributes") or {}
    production = attributes.get("production") or {}
    for key in ("role", "declared_role", "source_role", "mix_stage", "stem_role"):
        if production.get(key):
            return production[key]
    return attributes.get("role")


def _result(rule, level, status, reason):
    return {"rule_id": rule.get("rule_id", "inline-option"), "level": level,
            "status": status, "weight": rule.get("weight"), "reason": reason}


def _rule(rule, level, nodes, final_hash, bound, winput):
    kind = rule["kind"]
    minimum = rule.get("min_count", 1)
    by_hash = {n["artefact_hash"]: n for n in nodes}
    if kind == "artifact_presence":
        wanted = set(rule.get("artifact_types", []))
        n = sum(1 for node in nodes
                if node["artefact_hash"] in bound and node["artefact_type"] in wanted
                and (not rule.get("role") or _role(node, final_hash) == rule["role"]))
        return _result(rule, level, "satisfied" if n >= minimum else "missing",
                       f"{n} matching bound artefact(s); minimum {minimum}")
    if kind == "relationship_presence":
        n = 0
        for target in nodes:
            for edge in target.get("evidence", []):
                source = by_hash[edge["hash"]]
                if source["artefact_hash"] not in bound or target["artefact_hash"] not in bound:
                    continue
                if edge["relationship_type"] not in set(rule.get("relationship_types", [])):
                    continue
                if rule.get("source_types") and source["artefact_type"] not in rule["source_types"]:
                    continue
                if rule.get("target_types") and target["artefact_type"] not in rule["target_types"]:
                    continue
                if rule.get("source_role") and _role(source, final_hash) != rule["source_role"]:
                    continue
                n += 1
        return _result(rule, level, "satisfied" if n >= minimum else "missing",
                       f"{n} matching relationship(s); minimum {minimum}")
    if kind == "ancestry_path":
        allowed, found, pending, seen = set(rule.get("allowed_relationship_types", [])), set(), [final_hash], set()
        while pending:
            digest = pending.pop()
            if digest in seen:
                continue
            seen.add(digest)
            node = by_hash[digest]
            if digest != final_hash and digest in bound and node["artefact_type"] in rule["source_types"]:
                if not rule.get("source_role") or _role(node, final_hash) == rule["source_role"]:
                    found.add(digest)
            pending.extend(e["hash"] for e in node.get("evidence", [])
                           if e["relationship_type"] in allowed)
        return _result(rule, level, "satisfied" if len(found) >= minimum else "missing",
                       f"{len(found)} qualifying ancestor(s); minimum {minimum}")
    if kind == "declaration_presence":
        name, decl = rule["declaration"], winput.get("declarations") or {}
        if name == "submitter_confirmation":
            present = decl.get("submitter_confirmation") is True
        elif name == "artefacts[].creation_method":
            audio = [n for n in nodes if n["artefact_type"].startswith("audio/")]
            present = bool(audio) and all(
                (n.get("attributes") or {}).get("creation_method") not in (None, "", [], {})
                for n in audio)
        elif name == "contributors[]":
            present = isinstance(decl.get("contributors"), list) and bool(decl["contributors"])
        elif name == "workflow.description":
            present = bool(winput.get("description"))
        else:
            return _result(rule, level, "not_assessed", f"{name!r} unsupported by evaluator")
        return _result(rule, level, "satisfied" if present else "missing",
                       f"declaration {name!r} is {'present' if present else 'missing'}")
    if kind == "alternative_group":
        kids = [_rule(o, level, nodes, final_hash, bound, winput) for o in rule.get("options", [])]
        ok = sum(k["status"] == "satisfied" for k in kids)
        need = rule.get("min_satisfied", 1)
        return _result(rule, level, "satisfied" if ok >= need else "missing",
                       f"{ok}/{len(kids)} alternatives satisfied; minimum {need}")
    return _result(rule, level, "not_assessed", f"unsupported rule kind {kind}")


def evaluate_workflow(policy, winput, chain, bound_hashes) -> dict:
    workflows = {w["workflow_id"]: w for w in policy["workflows"]}
    wid = winput.get("workflow_id")
    if wid not in workflows:
        raise PolicyError(f"Unknown workflow_id: {wid}")
    workflow = workflows[wid]
    allowed_mods = set(policy["workflow_selection"]["modifiers"])
    unknown = set(winput.get("modifiers", [])) - allowed_mods
    if unknown:
        raise PolicyError("Unknown modifier(s): " + ", ".join(sorted(unknown)))
    effective = set(workflow.get("default_modifiers", [])) | set(winput.get("modifiers", []))
    nodes, final_hash = chain["artefacts"], chain["final_artefact_hash"]
    final = next(n for n in nodes if n["artefact_hash"] == final_hash)
    context = {"modifier": {m: m in effective for m in allowed_mods},
               "final": {"type": final["artefact_type"]},
               "workflow": winput, "declaration": winput.get("declarations", {})}

    results, common = [], policy["common_rules"]
    for rule in common.get("required_for_every_workflow", []) + workflow.get("required", []):
        results.append(_rule(rule, "required", nodes, final_hash, bound_hashes, winput))
    for rule in workflow.get("conditional", []):
        if evaluate_trigger(rule["trigger"], context):
            results.append(_rule(rule, "conditional", nodes, final_hash, bound_hashes, winput))
        else:
            results.append(_result(rule, "conditional", "not_applicable",
                                   f"trigger false: {rule['trigger']}"))
    optional = list(common.get("optional_for_every_workflow", []))
    optional += [{"rule_id": f"{wid}.optional.{i}", "kind": "artifact_presence",
                  "artifact_types": [t], "min_count": 1}
                 for i, t in enumerate(workflow.get("optional", []), 1)]
    for rule in optional:
        results.append(_rule(rule, "optional", nodes, final_hash, bound_hashes, winput))

    applicable = [r for r in results if r["level"] != "optional" and r["status"] != "not_applicable"]
    assessed = [r for r in applicable if r["status"] != "not_assessed"]
    denominator = sum(r["weight"] for r in assessed)
    numerator = sum(r["weight"] for r in assessed if r["status"] == "satisfied")
    value = numerator / denominator if denominator else None
    allowed_final = final["artefact_type"] in workflow.get("allowed_final_types", [])
    if not allowed_final and value is not None:
        value = 0.0
    return {"workflow_title": workflow["title"], "allowed_final": allowed_final,
            "allowed_final_types": workflow.get("allowed_final_types", []),
            "numerator": numerator, "denominator": denominator, "value": value,
            "results": results}
