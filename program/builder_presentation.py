"""Read-only report model shared by the desktop views and report exports."""

import copy
import json

from builder_results import attention_items

INSUFFICIENT_SUPPORT_REASONS = frozenset({
    "silent_signal", "insufficient_signal", "insufficient_landmarks", "insufficient_peaks",
    "insufficient_spectral_frames", "channel_cancellation",
})

INTEGRITY_NOTE = (
    "Core Evidence Consistency reflects the consistency of core checks performed; "
    "it excludes optional audio findings. Review audio findings separately. "
    "It does not prove authenticity or authorship.")
REPORT_ONLY = "Report-only: does not change Integrity, confidence, or declared segments."


def _first(checks, key):
    return next(iter(checks.get(key) or []), {})


def _span(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}-{value[1]} s"
    return "Not supplied"


def _axis_value_label(name, axis, decision):
    value = axis.get("value")
    if value is None:
        return "Not assessed"
    text = f"{value:.3f}"
    if name == "integrity" and axis.get("status") == "incomplete":
        count = decision.get("not_completed_count")
        return f"Incomplete ({count} file check{'s' if count != 1 else ''} not completed)" if count else "Incomplete"
    if name == "integrity" and axis.get("status") == "contradicted":
        count = decision.get("contradiction_count")
        return f"{text} (contradicted: {count})" if count else f"{text} (contradicted)"
    return text


def audio_review_items(response, names=None):
    """Group structured outcomes by claim, preserving all evidence and ambiguity."""
    names = names or {}
    checks = response.get("assessment_checks") or {}
    rows = []
    for collection, items in checks.items():
        if not collection.startswith("audio_") or not isinstance(items, list):
            continue
        rows.extend((f"assessment_checks.{collection}[{i}]", item)
                    for i, item in enumerate(items) if isinstance(item, dict))
    rows.extend((f"findings[{i}]", item) for i, item in enumerate(response.get("findings", []))
                if str(item.get("code", "")).startswith("AUDIO_"))
    diagnostic_index = {item["diagnostic_id"]: (reference, item)
                        for reference, item in rows if item.get("diagnostic_id")}
    groups = {}
    for reference, item in rows:
        outcome = item.get("outcome")
        reason = item.get("reason_code")
        if outcome == "content_found_elsewhere":
            category, title = "location", "Claimed segment requires review"
        elif outcome == "content_not_located":
            category, title = "no_match", "No conclusive match found"
        elif outcome == "insufficient_support" or reason in INSUFFICIENT_SUPPORT_REASONS:
            category, title = "signal", "Insufficient signal"
        elif outcome in {"not_assessed", "not_assessed_durations_differ", "search_unavailable", "not_run"} \
                or item.get("analysis_complete") is False:
            category, title = "incomplete", "Analysis incomplete"
        elif outcome in {"content_beyond_final", "content_beyond_target", "unused_content_found_in_final"}:
            category, title = "coverage", "Source content requires review"
        else:
            continue
        key = (item.get("source_hash"), item.get("target_hash"), item.get("relationship_type"),
               item.get("selection_id"), category)
        group = groups.setdefault(key, {
            "category": category, "title": title, "source_hash": key[0], "target_hash": key[1],
            "relationship": key[2], "selection_id": key[3], "evidence": [],
            "references": [], "providers": [], "diagnostic_ids": [],
        })
        group["evidence"].append(copy.deepcopy(item))
        group["references"].append(reference)
        if item.get("provider") and item["provider"] not in group["providers"]:
            group["providers"].append(item["provider"])
        group["diagnostic_ids"] = sorted(set(group["diagnostic_ids"]) |
                                         set(item.get("diagnostic_ids") or []) |
                                         ({item["diagnostic_id"]} if item.get("diagnostic_id") else set()))
    for group in groups.values():
        for diagnostic_id in group["diagnostic_ids"]:
            linked = diagnostic_index.get(diagnostic_id)
            if linked and linked[0] not in group["references"]:
                reference, item = linked
                group["references"].append(reference)
                group["evidence"].append(copy.deepcopy(item))
                if item.get("provider") and item["provider"] not in group["providers"]:
                    group["providers"].append(item["provider"])
        evidence = group["evidence"]
        source = names.get(group["source_hash"], group["source_hash"] or "Source not supplied")
        target = names.get(group["target_hash"], group["target_hash"] or "Target not supplied")
        lines = [group["title"], f"{source} -> {target} | {group['relationship'] or 'Relationship not supplied'}"]
        for item, reference in zip(evidence, group["references"]):
            ranges = item.get("analysed_ranges") or {}
            lines.extend([
                f"Evidence: {reference}",
                f"Declared source: {_span(item.get('declared_source_range_seconds'))}; "
                f"target: {_span(item.get('declared_target_range_seconds'))}",
            ])
            if ranges:
                lines.append(f"Analysed source: {_span(ranges.get('source'))}; "
                             f"target: {_span(ranges.get('target'))} "
                             f"(range origin: {item.get('range_source', 'not supplied')}).")
            offsets = item.get("search_supported_offsets_seconds") or []
            if offsets:
                lines.append(f"Observed candidate source-minus-target offsets: {offsets} s. "
                             "All candidates retained; no declaration is replaced.")
            if item.get("best_source_range_seconds") is not None:
                lines.append(f"Best observed source window: {_span(item['best_source_range_seconds'])}")
            if not offsets and item.get("best_source_range_seconds") is None:
                lines.append("Observed location: none conclusive or analysis incomplete; see outcome.")
            lines.append(f"Outcome: {item.get('outcome') or item.get('reason_code') or 'not supplied'}")
            lines.append(f"Method: {item.get('provider') or ', '.join(group['providers']) or 'see diagnostic evidence IDs'}")
            lines.append(f"Explanation: {item.get('message') or item.get('reason') or 'See structured evidence.'}")
            for limitation in item.get("limitations") or []:
                lines.append(f"Limitation: {limitation}")
        if group["category"] == "no_match":
            lines.append("Processing, short dialogue, or low-signal content can prevent detection; "
                         "absence of a match does not establish fabrication.")
        lines.extend([
            "Diagnostic evidence IDs: " + (", ".join(group["diagnostic_ids"]) or "not supplied"),
            "Next: Review the original files, claimed source/target segments and processing. "
            "Retry incomplete checks; consider changes to declarations only after review.",
            REPORT_ONLY,
        ])
        group["text"] = "\n".join(lines)
    return list(groups.values())


def build_report(response, file_names=None, policy=None, files=()):
    """Organize the original response without changing its contract or scores."""
    names = file_names or {}
    checks = response.get("assessment_checks") or {}
    decision = _first(checks, "integrity_decision")
    integrity = response.get("axes", {}).get("integrity", {})
    audio = audio_review_items(response, names)
    contradictions = decision.get("contradiction_count")
    if contradictions:
        core = f"Core checks: {contradictions} contradiction(s) identified."
    elif decision.get("contradiction_present") or integrity.get("status") == "contradicted":
        core = "Core checks: contradictions identified; count not supplied."
    elif decision.get("assessed_fact_count", 0) > 0:
        core = "No contradictions reported in assessed core facts; this is not full verification."
    else:
        core = "Core checks: assessed fact count not supplied; no overall pass inferred."
    categories = {key: sum(item["category"] == key for item in audio)
                  for key in ("location", "no_match", "signal", "incomplete", "coverage")}
    summary = [
        f"Execution: {response.get('execution_status', 'unknown')}.",
        f"Pending core file checks: {decision.get('not_completed_count', 'not supplied')}.",
        core,
        (f"Audio review: {len(audio)} grouped item(s) require inspection. "
         f"Segment locations: {categories['location']}; no conclusive match: {categories['no_match']}; "
         f"incomplete: {categories['incomplete']}; insufficient signal: {categories['signal']}; "
         f"source coverage: {categories['coverage']}.") if audio else
        "Audio review: no actionable structured outcomes reported; this does not establish that audio was checked.",
    ]
    if response.get("error"):
        summary.append("ERROR: " + response["error"])
    coverage = []
    if files:
        coverage.append(f"Submitted material: {len(files)} artefacts; "
                        f"{sum(str(f.get('type', '')).startswith('audio') for f in files)} audio artefacts.")
    breadth = _first(checks, "support_breadth")
    if breadth.get("applicable"):
        supported, contradicted = breadth.get("supported", 0), breadth.get("contradicted", 0)
        coverage.extend([
            f"Qualifying cross-evidence checked: {supported + contradicted} of "
            f"{breadth.get('required_recording_count', '?')} required recordings "
            f"({breadth.get('coverage_scope', 'scope not supplied')}).",
            f"Consistent: {supported}; contradicted: {contradicted}; "
            f"no qualifying evidence: {breadth.get('no_qualifying_evidence', '?')}; "
            f"not submitted: {breadth.get('not_submitted', '?')}; "
            f"unmatched references: {breadth.get('unmatched_references', '?')}.",
        ])
    else:
        coverage.append("Qualifying recording coverage: not applicable or not supplied.")
    diagnostic_rows = {}
    for key, items in checks.items():
        if key.startswith("audio_") and isinstance(items, list):
            for row in items:
                if isinstance(row, dict) and row.get("diagnostic_id") and row.get("provider"):
                    diagnostic_rows[row["diagnostic_id"]] = row
    complete = sum(row.get("analysis_complete") is True for row in diagnostic_rows.values())
    tasks = {(row.get("relation_id"), row.get("selection_id")) for row in diagnostic_rows.values()}
    coverage.extend([
        f"Audio diagnostics: {complete}/{len(diagnostic_rows)} provider tasks completed "
        f"across {len(tasks)} relationship/selection task(s).",
        "Completed/checked includes contradictions; it does not mean correct or passed. "
        "Confidence is not a coverage measure.",
    ])
    axes = []
    for name, axis in response.get("axes", {}).items():
        count = decision.get("contradiction_count")
        label = _axis_value_label(name, axis, decision)
        if name == "integrity" and count and "contradicted" not in label:
            label += f" (contradicted: {count})"
        reason = axis.get("reasoning")
        if name == "integrity" and axis.get("value") is None:
            reason = reason or decision.get("release_reason") or "Reason not supplied by evaluator."
        axes.append({"name": name, "label": label, "availability": axis.get("availability"),
                     "confidence": axis.get("confidence"), "reason": reason or "Reasoning not supplied."})
    issues, seen = [], set()
    for issue in attention_items(response, names, policy):
        key = tuple(issue.get(field, "") for field in ("status", "affected", "explanation", "suggestion"))
        if key not in seen:
            seen.add(key)
            issues.append(copy.deepcopy(issue))
    attention = ["Issues requiring attention:"]
    for item in audio:
        attention.extend(["", item["text"]])
    for issue in issues:
        attention.extend(["", f"{issue['status']} - {issue['affected']}",
                          issue["explanation"], "Next: " + issue["suggestion"]])
    if not audio and not issues:
        attention.append("No actionable issues reported by available checks. Unchecked evidence "
                         "and optional diagnostics remain unverified; review coverage and limitations.")
    details = [
        "Content consistency does not prove authorship. AI disclosure is not AI detection.",
        "Provider-level results and methodology are retained in Expandable details and Raw JSON.",
        *[f"Builder note: {note}" for note in response.get("_builder_notes", [])],
        "Evidence details are expandable below; paths cite the unchanged Raw JSON response.",
    ]
    return {"summary": summary, "coverage": coverage, "integrity_note": INTEGRITY_NOTE,
            "axes": axes, "audio": audio, "issues": issues, "attention": "\n".join(attention),
            "details": "\n".join(details),
            "raw_json": json.dumps(response, indent=2, ensure_ascii=False, allow_nan=False)}


def report_text(model):
    """Printable review text uses exactly the model displayed in the UI."""
    return "\n\n".join([
        "Review summary\n" + "\n".join(model["summary"]),
        "Verification coverage\n" + "\n".join(model["coverage"]),
        model["integrity_note"],
        "Dimensions\n" + "\n".join(
            f"{axis['name']}: {axis['label']} | {axis['availability']} | "
            f"confidence: {axis['confidence']}\n{axis['reason']}" for axis in model["axes"]),
        model["attention"], model["details"], "Original response JSON\n" + model["raw_json"],
    ])
