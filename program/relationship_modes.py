"""Relationship declaration guidance for the evaluator.

Pure logic (no Tkinter). Signal/physical similarity has been policy-disabled since v0.7.0
(every relationship gets an 'unavailable / disabled_by_policy' placeholder), and
exact reconstruction is no longer run. Declared ranges, sample rate and bit depth
are still checked against the files; unknown field names are flagged. The
comparison-mode table is kept so it can drive hints if signal checks return.
"""

from __future__ import annotations

import math

TIME_FIELDS = ("source_start_seconds", "source_end_seconds",
               "target_start_seconds", "target_end_seconds")
NUMERIC_FIELDS = TIME_FIELDS + ("gain", "fade_in_seconds", "fade_out_seconds")

# relationship type -> comparison mode (release notes, v0.6.0)
MODE_OF = {
    "derived_from": "pairwise_continuity",
    "edited_from": "pairwise_continuity",
    "excerpted_from": "excerpt_search",
    "comped_from": "selection_timeline",
    "stemmed_from": "multi_source_contribution",
    "mixed_from": "multi_source_contribution",
    "mastered_from": "master_continuity",
    "rendered_from": "unsupported_relationship",
    "input_to": "unsupported_relationship",
}

_DISABLED = (" Signal comparison is disabled by policy in this release, so no "
             "physical-similarity result is produced; the declaration is only checked "
             "against the files' measured properties.")
MODE_HELP = {
    "pairwise_continuity": "Declare the matching ranges if you know them (optional)." + _DISABLED,
    "excerpt_search": "Declare where the excerpt sits in the source (optional)." + _DISABLED,
    "selection_timeline": "Selections come from the comp sheet; ranges here are optional." + _DISABLED,
    "multi_source_contribution": "Ranges and gain are optional declarations." + _DISABLED,
    "master_continuity": "Ranges are optional declarations." + _DISABLED,
    "unsupported_relationship": "Recorded in the chain as declared." + _DISABLED,
}

# Attributes the evaluator's schema knows for a relationship; any other key is
# reported as UNKNOWN_FIELD and that relationship's content checks are skipped.
KNOWN_ATTRIBUTES = frozenset({
    "assertion_origin", "confirmed_by_submitter", "reference_pointer", "claim_rationale",
    "transformation_description", "derivation_scope", *TIME_FIELDS, "gain",
    "fade_in_seconds", "fade_out_seconds", "input_sample_rate_hz", "output_sample_rate_hz",
    "input_bit_depth", "output_bit_depth", "source_format", "target_format"})

# Evaluator limits (music_target/engine.py)
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024

# Which optional attribute fields the dialog shows per mode.
MODE_FIELDS = {
    "pairwise_continuity": {"ranges", "gain"},
    "excerpt_search": {"ranges"},
    "selection_timeline": set(),
    "multi_source_contribution": {"ranges", "gain"},
    "master_continuity": {"ranges"},
    "unsupported_relationship": set(),
}


def mode_of(rtype):
    return MODE_OF.get(rtype)


def mode_help(rtype):
    return MODE_HELP.get(MODE_OF.get(rtype), "")


def visible(rtype, name):
    """Whether dialog field `name` applies to relationship type `rtype`."""
    fields = MODE_FIELDS.get(MODE_OF.get(rtype), {"ranges"})
    if name in ("ss", "se", "ts", "te", "suggest"):
        return "ranges" in fields
    if name == "gain":
        return "gain" in fields
    if name in ("fi", "fo"):
        return rtype == "edited_from"
    if name == "scope":  # obsolete; kept only if an existing manifest has one
        return False
    return True


def suggest_range(target, source):
    """(0, shortest duration) for both files, or None when durations are unknown."""
    td = ((target or {}).get("technical") or {}).get("duration_seconds")
    sd = ((source or {}).get("technical") or {}).get("duration_seconds")
    if td is None or sd is None:
        return None
    return 0.0, round(min(td, sd), 3)


def _number(attrs, key):
    value = attrs.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    return float(value)


def relationship_issues(rel, target, source, endpoint_ok):
    """(errors, warnings) for one relationship. `endpoint_ok` is L.endpoint_ok."""
    errors, warnings = [], []
    rtype, attrs = rel["type"], rel["attrs"]
    if rtype not in MODE_OF:
        return [f"'{rtype}' is not a registered relationship type"], warnings
    if not endpoint_ok(rtype, target["type"], source["type"]):
        errors.append(f"{rtype} cannot link {source['type']} (source) to "
                      f"{target['type']} (target)")
    try:
        nums = {k: _number(attrs, k) for k in NUMERIC_FIELDS}
    except ValueError as exc:
        return errors + [str(exc)], warnings
    ss, se, ts, te = (nums[k] for k in TIME_FIELDS)
    if ss is not None and se is not None and se <= ss:
        errors.append("source_end_seconds must be after source_start_seconds")
    if ts is not None and te is not None and te <= ts:
        errors.append("target_end_seconds must be after target_start_seconds")
    for key in TIME_FIELDS + ("fade_in_seconds", "fade_out_seconds"):
        if nums[key] is not None and nums[key] < 0:
            errors.append(f"{key} cannot be negative")

    s_tech, t_tech = source.get("technical") or {}, target.get("technical") or {}
    for end, tech, label in ((se, s_tech, "source"), (te, t_tech, "target")):
        dur = tech.get("duration_seconds")
        if end is not None and dur is not None and end > dur + 1e-6:
            errors.append(f"{label}_end_seconds ({end}) exceeds the {label} file ({dur} s)")
    for attr, side, key in (("input_sample_rate_hz", s_tech, "sample_rate_hz"),
                            ("output_sample_rate_hz", t_tech, "sample_rate_hz"),
                            ("input_bit_depth", s_tech, "bit_depth"),
                            ("output_bit_depth", t_tech, "bit_depth")):
        if attrs.get(attr) is not None and side.get(key) is not None \
                and attrs[attr] != side[key]:
            errors.append(f"{attr} ({attrs[attr]}) does not match the file ({side[key]})")

    import difflib
    for key in attrs:
        if key not in KNOWN_ATTRIBUTES:
            close = difflib.get_close_matches(key, sorted(KNOWN_ATTRIBUTES), n=1, cutoff=0.6)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            warnings.append(f"'{key}' is not a recognised relationship field; the evaluator "
                            f"will report UNKNOWN_FIELD and skip this relationship's "
                            f"content checks.{hint}")
    given_src, given_tgt = (ss, se), (ts, te)
    for label, pair in (("source", given_src), ("target", given_tgt)):
        if (pair[0] is None) != (pair[1] is None):
            warnings.append(f"only one {label} bound is declared; the evaluator will "
                            "estimate the other.")
    return errors, warnings


def all_warnings(files, rels, endpoint_ok):
    by_id = {f["id"]: f for f in files}
    out = []
    for rel in rels:
        if rel["target"] not in by_id or rel["source"] not in by_id:
            continue
        _, warnings = relationship_issues(
            rel, by_id[rel["target"]], by_id[rel["source"]], endpoint_ok)
        out.extend(f"{rel['target']} ← {rel['source']}: {w}" for w in warnings)
    return out
