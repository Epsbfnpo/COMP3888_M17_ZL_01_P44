"""Plain statements of how each axis value was reached.

Every failed check is described in words, with the compared values (for
example how many seconds two timecodes differ), and every cap, withholding or
input-error adjustment is named, so a reader never has to infer why a value
is what it is.
"""

LAYER_CHECKS = {"file_integrity": "bound_file_integrity",
                "declaration_consistency": "relationship_integrity",
                "cross_evidence_support": "cross_evidence"}


def timecode(seconds, fps):
    """Seconds since midnight as HH:MM:SS:FF at a whole frame rate."""
    fps = int(round(fps))
    total = int(round(seconds * fps))
    frames, whole = total % fps, total // fps
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}:{frames:02d}"


def _short(digest):
    return (digest or "")[:12] + "…" if digest else "an unnamed artefact"


def describe_check(check):
    """One sentence naming a failed check and what it compared."""
    rule = check.get("rule", "unnamed_check")
    name = check.get("file_name") or _short(check.get("audio_hash") or check.get("evidence_hash"))
    fps = check.get("fps")
    if rule == "sound_report_start_timecode_matches_recorder" and fps:
        declared, recorded = check["declared_seconds"], check["recorded_seconds"]
        return (f"Sound report start timecode for {name} is {timecode(declared, fps)}, but the recorder wrote "
                f"{timecode(recorded, fps)} into the file ({abs(declared - recorded):.2f} s apart).")
    if rule == "aaf_source_range_within_recorded_timecode" and fps:
        (edit_start, edit_end), (rec_start, rec_end) = check["edit_range_seconds"], check["recorded_range_seconds"]
        outside = max(rec_start - edit_start, edit_end - rec_end, 0.0)
        return (f"AAF clip {check.get('master_mob')} uses {timecode(edit_start, fps)}–{timecode(edit_end, fps)} of "
                f"tape {check.get('tape')}, but {name} was recorded {timecode(rec_start, fps)}–"
                f"{timecode(rec_end, fps)} ({outside:.2f} s outside the recording).")
    if rule == "daw_session_uses_declared_source_audio":
        shape = check.get("shape") or {}
        return (f"The graph declares {check.get('declared_sources')} source(s) of {_short(check.get('target_hash'))} "
                f"with size {shape.get('file_size_bytes')} bytes, {shape.get('frames')} frames at "
                f"{shape.get('sample_rate_hz')} Hz, but the DAW session uses {check.get('session_samples')} such sample(s).")
    if rule.endswith("_scene_take_matches_recorder"):
        return (f"{rule}: {name} is declared as scene/take {'/'.join(map(str, check.get('declared', [])))} "
                f"but the recorder wrote {'/'.join(map(str, check.get('recorded', [])))}.")
    if rule.endswith("_track_names_match_recorder"):
        return f"{rule}: track(s) {', '.join(check.get('differing_tracks', []))} of {name} are named differently by the recorder."
    if "declared" in check and "recorded" in check:
        return f"{rule}: {name} declared {check['declared']!r}, recorded {check['recorded']!r}."
    if "declared_seconds" in check and "observed_seconds" in check:
        return (f"{rule}: {name} declared {check['declared_seconds']:g} s, measured "
                f"{check['observed_seconds']:.2f} s.")
    if "declared" in check and "observed" in check:
        return f"{rule}: {name} declared {check['declared']!r}, observed {check['observed']!r}."
    return f"{rule} failed for {name}."


def contradictions(assessment_checks):
    rows = []
    for layer, key in LAYER_CHECKS.items():
        for check in assessment_checks.get(key) or []:
            if check.get("passed") is False:
                rows.append({"layer": layer, "rule": check.get("rule"),
                             "evidence_tier": check.get("evidence_tier"), "statement": describe_check(check)})
    return rows


def _integrity(axis, checks):
    layers = [item for item in checks.get("integrity_layers") or [] if item.get("contributes_to_integrity")]
    populated = [item for item in layers if item.get("value") is not None]
    cap = (checks.get("severe_contradiction_cap") or [None])[0]
    cross = next((item for item in layers if item["layer"] == "cross_evidence_support"), {})
    found = contradictions(checks)
    statements = []
    if populated:
        statements.append("Weighted mean of " + ", ".join(
            f"{item['layer']} {item['value']:.3f} (weight {item['weight']:g})" for item in populated) + ".")
    if cap:
        statements.append(
            (f"Capped at {cap['cap']:g} by severe rule(s) {', '.join(cap['triggered_rules'])}; "
             f"the weighted mean was {cap['weighted_mean']:.3f}.") if cap["value_lowered"] else
            (f"Severe rule(s) {', '.join(cap['triggered_rules'])} failed, but the weighted mean "
             f"{cap['weighted_mean']:.3f} was already at or below the {cap['cap']:g} cap."))
    if axis["value"] is None and populated:
        statements.append("No numeric value: the withholding rule was not met (see reasoning).")
    statements.extend(item["statement"] for item in found)
    return {"layers": [{key: item.get(key) for key in ("layer", "status", "value", "weight", "check_count")}
                       for item in layers],
            "weighted_mean": cap["weighted_mean"] if cap else axis["value"],
            "cap_applied": bool(cap and cap["value_lowered"]), "cap": cap,
            "withheld": axis["value"] is None and bool(populated),
            "evidence_tier_counts": cross.get("evidence_tier_counts"),
            "contradictions": found, "statements": statements}


def build(response):
    """One explanation per axis, derived from the final response."""
    checks = response.get("assessment_checks") or {}
    rows = []
    for name, axis in (response.get("axes") or {}).items():
        row = {"axis": name, "availability": axis.get("availability"), "value": axis.get("value"),
               "confidence": axis.get("confidence")}
        if name == "integrity":
            row.update(_integrity(axis, checks))
        else:
            row["statements"] = [axis["reasoning"]] if axis.get("reasoning") else []
            if name == "completeness":
                row["statements"] += [item["message"] for item in response.get("findings", [])
                                      if item.get("code") in ("WORKFLOW_FINAL_TYPE_MISMATCH",
                                                              "WORKFLOW_EVIDENCE_OUTSIDE_POLICY")]
        rows.append(row)
    return rows
