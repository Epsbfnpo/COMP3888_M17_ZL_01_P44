"""Read-only presentation for GUI and human reports. Never part of the CSEC response.

No audio imports, thresholds, score computation, or evidence-file access belongs here.
"""
from __future__ import annotations

import copy
import hashlib
import json

from builder_results import (attention_items, audio_diagnostic_lines, axis_value_label,
                             music_semantics_lines, _affected)

TEMPLATE_VERSION = "0.8.3"
AXIS_LABELS = {"completeness": "Completeness", "integrity": "Core Evidence Consistency",
               "attestation_strength": "Attestation Strength", "ai_disclosure": "AI Disclosure"}
CORE_CHECK_SCOPE = ("File identity and readability, declared technical properties and relationships, "
                    "and cross-evidence consistency. Optional audio analysis is reported separately "
                    "and does not affect this score.")
SCOPE_NOTE = ("Scores describe assessed evidence, not proof of authorship or production history. "
              "Optional audio observations do not change any score, confidence or status.")
AUDIO_NOTE = ("Report-only audio observation: no change to any score or confidence. "
              "A content match does not establish production history.")
RANGE_STATES = {
    "content_found_elsewhere": ("audio_review", "Declared interval needs review",
        "The declared source interval was not supported within the current analysis profile. "
        "Corresponding content was located elsewhere in the source."),
    "content_not_located": ("audio_unresolved", "Content comparison is inconclusive",
        "No supported correspondence was located in the analysed search. Processing or insufficient "
        "signal may prevent a match; this does not establish that the source is unrelated."),
    "search_unavailable": ("audio_gap", "Declared window unsupported; wider search incomplete",
        "The declared window was not supported. The wider search did not complete; "
        "no conclusion is available for the whole source."),
    "insufficient_support": ("audio_unresolved", "Insufficient signal to decide",
        "The analysis had insufficient signal. This is not a contradiction."),
    "no_positive_control": ("audio_unresolved", "No positive control; unable to decide",
        "The comparison lacks a positive control. This is not a contradiction."),
    "declared_range_supported": ("support", "Declared interval supported within profile",
        "Correspondence was found within the analysed scope and profile. This does not prove provenance."),
}
GAP_OUTCOMES = {"not_assessed", "not_assessed_durations_differ", "not_run", "unavailable"}
GROUP_LABELS = {
    "core": "Execution blockers and core check issues",
    "audio_review": "Audio content findings requiring review (non-scoring)",
    "audio_unresolved": "Inconclusive audio comparisons (non-scoring)",
    "audio_gap": "Incomplete audio analyses (non-scoring)",
    "gap": "Missing evidence and unassessed checks",
    "unknown": "Uninterpreted results",
    "support": "Other observations and supporting details",
}
ORDER = {name: index for index, name in enumerate(GROUP_LABELS)}


def _first(checks, key):
    rows = checks.get(key) or []
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def _stable_id(identity):
    return "review-" + hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _range(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return "–".join(f"{n:.2f}" if isinstance(n, (int, float)) else "?" for n in value) + " s"
    return "not supplied"


def _core_summary(response, decision):
    axis = (response.get("axes") or {}).get("integrity") or {}
    status = axis.get("status") or decision.get("status")
    count, pending = decision.get("contradiction_count"), decision.get("not_completed_count")
    parts = []
    if isinstance(count, int) and count > 0:
        parts.append(f"{count} core contradiction(s) recorded.")
    elif status == "contradicted":
        parts.append("Core contradictions recorded; count not supplied.")
    elif count == 0 and (status == "consistent" or decision.get("assessed_fact_count", 0) > 0):
        parts.append("No contradictions were found in assessed core checks.")
    if status == "incomplete" or pending:
        parts.append(f"{pending if pending is not None else 'Some'} file check(s) incomplete.")
    if axis.get("value") is None:
        reasons = {
            "insufficient_qualifying_cross_evidence": "qualifying cross-evidence is missing",
            "insufficient_assessed_layers": "too few contributing evidence layers were assessed",
            "insufficient_support_breadth": "too few relevant sources have qualifying cross-evidence checks",
            "file_checks_not_completed": "required file checks have not completed",
            "final_attestation_unreadable": "the final file's attestation could not be read",
            "no_assessed_layers": "no contributing evidence layers were assessed",
        }
        reason = decision.get("release_reason")
        if reason in reasons:
            parts.append("Score withheld because " + reasons[reason] + ".")
        elif decision.get("numeric_release") == "withheld":
            parts.append("Score withheld; release reason: " + str(reason or "not supplied").replace("_", " ") + ".")
        elif response.get("execution_status") != "succeeded":
            parts.append("Core consistency was not assessed because core evaluation did not complete.")
        else:
            parts.append("Score not available; a complete release explanation was not supplied.")
    else:
        parts.append("Score available.")
        if status not in {"consistent", "contradicted", "incomplete"}:
            parts.append(f"Recorded core-check state: {status or 'unknown'}.")
        if decision.get("release_reason") == "severe_contradiction_cap":
            parts.append("The severe-contradiction cap applies.")
    return " ".join(parts)


def _coverage(checks):
    breadth = _first(checks, "support_breadth")
    if not breadth:
        return ["Source cross-evidence coverage: unknown (not supplied)."]
    if breadth.get("applicable") is False:
        return ["Source cross-evidence coverage: not applicable to the reported scope."]
    total = breadth.get("required_recording_count")
    supported, contradicted = breadth.get("supported"), breadth.get("contradicted")
    if all(isinstance(n, int) for n in (total, supported, contradicted)):
        checked = supported + contradicted
        rows = [f"{checked}/{total} relevant source files have qualifying cross-evidence checks "
                f"({supported} consistent; {contradicted} contradicted); "
                f"{max(0, total - checked)} lack qualifying checks."]
    else:
        rows = ["Source cross-evidence coverage: unknown (counts not supplied)."]
    assets = breadth.get("assets")
    bound = sum(item.get("bound") is True for item in assets) if isinstance(assets, list) else None
    rows.append(f"Source material available: {bound if bound is not None else 'unknown'}/{total if total is not None else 'unknown'}.")
    if breadth.get("coverage_scope") == "provisional":
        rows.append(f"Provisional coverage: {breadth.get('unmatched_references', 'unknown')} AAF reference(s) "
                    "cannot be matched to submitted sources; do not assume all were omitted.")
    rows.append("Qualifying means accepted by the scoring policy; optional audio matching is excluded. "
                "Checked includes both consistent and contradicted sources; it is not a pass rate.")
    types = breadth.get("relevant_asset_types")
    if types:
        rows.append("Source types in this coverage denominator: " + ", ".join(types) + ".")
    return rows


def _audio_run_state(checks, audio_requested):
    audio = _first(checks, "audio_diagnostic_summary")
    if not audio:
        return ("Optional audio analysis was not enabled for this snapshot." if audio_requested is False else
                "Audio analysis was requested, but no diagnostic result returned." if audio_requested is True else
                "Audio analysis run state unknown: no diagnostic summary was supplied.")
    if audio.get("enabled") is False:
        return "Optional audio analysis was not enabled for this snapshot."
    if audio.get("reason_code"):
        return "Audio analysis not completed: " + str(audio.get("message") or audio["reason_code"])
    completed, unavailable = audio.get("completed_provider_tasks"), audio.get("unavailable_provider_tasks")
    if not isinstance(completed, int) or not isinstance(unavailable, int):
        return "Audio analysis completion state unknown: task counts are incomplete."
    if completed == unavailable == 0:
        return "No audio algorithm tasks were reported; no content comparison is claimed."
    if unavailable:
        return (f"Audio analysis partly completed: {completed} algorithm task(s) completed; "
                f"{unavailable} unavailable or unable to conclude.")
    return f"Audio analysis completed {completed} algorithm task(s) within the reported scope."


def _audio_limitations(checks):
    """Explain unavailable providers separately from interval-level review counts."""
    labels = {"music_semantics": "Musical-semantics analysis", "librosa": "Acoustic-structure analysis",
              "audfprint": "audfprint", "spectromap": "SpectroMap", "bandwidth": "Bandwidth analysis",
              "fingerprint_agreement": "Fingerprint agreement"}
    normal = {"silent_signal", "insufficient_signal", "insufficient_landmarks", "insufficient_peaks",
              "insufficient_spectral_frames", "channel_cancellation"}
    reasons = {"provider_failed": "the analysis could not run successfully; see diagnostic details",
               "dependency_unavailable": "a required dependency was unavailable",
               "provider_inputs_unavailable": "the required input observations were unavailable"}
    seen, limits = set(), []
    for name, rows in checks.items():
        if not name.startswith("audio_") or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not row.get("provider") or row.get("status") != "unavailable":
                continue
            provider, reason = row["provider"], row.get("reason_code", "unknown")
            if (provider, reason) in seen:
                continue
            seen.add((provider, reason))
            why = ("too little usable audio for musical comparison" if provider == "music_semantics" and reason == "insufficient_signal" else
                   "insufficient usable signal to conclude" if reason in normal else
                   reasons.get(reason, str(row.get("message") or reason.replace("_", " "))))
            limits.append(f"{labels.get(provider, provider)}: {why}.")
    return limits


def _other_key_results(response, checks):
    results = []
    rules = (response.get("workflow_assessment") or {}).get("rule_results")
    if rules:
        required = [row for row in rules if row.get("level") in {"required", "conditional"}
                    and row.get("status") != "not_applicable"]
        missing = sum(row.get("status") == "missing" for row in required)
        unassessed = sum(row.get("status") == "not_assessed" for row in required)
        unknown = sum(row.get("status") not in {"missing", "not_assessed", "satisfied"} for row in required)
        if missing or unassessed or unknown:
            results.append(f"Workflow requirements: {missing} missing; {unassessed} not assessed; {unknown} unrecognized.")
        elif required:
            results.append("Applicable workflow evidence requirements were met.")
        else:
            results.append("No applicable workflow requirements were reported.")
    else:
        results.append("Workflow requirements were not assessed or not supplied.")
    final = next((f for f in response.get("findings", []) if f.get("code") == "C2PA_FINAL_ATTESTATION"), {})
    state = final.get("final_c2pa_state")
    c2pa = {"absent": "no manifest found; absence is not evidence against the submission",
            "invalid": "manifest invalid; review the validation findings",
            "unreadable": "manifest could not be read", "not_checked": "not checked",
            "untrusted": "manifest intact, but signer not trusted under the configured policy",
            "trusted_c2pa": "valid manifest; signer trusted under the configured C2PA policy",
            "trusted_legacy_itl": "valid manifest; signer trusted under the configured legacy policy",
            "unclear": "state unclear; inspect the detailed findings"}
    results.append("Final-file C2PA: " + c2pa.get(state, "state not supplied" if state is None else f"uninterpreted state {state}") + ".")
    disclosure = _first(checks, "ai_disclosure_coverage")
    declared, assessed = disclosure.get("declared_audio_count"), disclosure.get("assessed_audio_count")
    if isinstance(declared, int) and isinstance(assessed, int) and assessed > 0:
        results.append(f"Creation methods declared for {declared}/{assessed} assessed audio files (declarations, not AI detection).")
        excluded = disclosure.get("excluded_input_error_count", 0)
        if excluded:
            results.append(f"{excluded} audio file(s) excluded from declaration coverage because of input errors.")
    elif assessed == 0:
        results.append("Creation-method disclosure: no assessable audio files.")
    else:
        results.append("Creation-method disclosure coverage not supplied.")
    if any(row.get("rule") == "c2pa_ai_source_type_matches_creation_method" and row.get("passed") is False
           for row in checks.get("cross_evidence", [])):
        results.append("A creation-method declaration conflicts with signed evidence.")
    return " ".join(results)


def _review_summary(items, counts, decision):
    parts = []
    if counts["core"]:
        parts.append(f"{counts['core']} execution/check/evidence item(s) need attention.")
    elif decision.get("contradiction_count"):
        parts.append("Core contradictions require review; see the recorded decision.")
    if counts["audio_review"]:
        parts.append(f"{counts['audio_review']} audio content finding(s) require review (non-scoring).")
    if counts["audio_unresolved"]:
        parts.append(f"{counts['audio_unresolved']} audio comparison(s) are inconclusive (non-scoring).")
    if counts["audio_gap"]:
        parts.append(f"{counts['audio_gap']} interval assessment(s) could not conclude (non-scoring).")
    if counts["gap"]:
        parts.append(f"{counts['gap']} evidence/check gap(s) require attention.")
    if counts["unknown"]:
        parts.append(f"{counts['unknown']} uninterpreted item(s) require inspection.")
    if not parts:
        parts.append("No review items identified in the available display details; unassessed analyses are not passes.")
    elsewhere = next((item for item in items if item.get("outcome") == "content_found_elsewhere"), None)
    if elsewhere:
        row = elsewhere["raw"][0]
        parts.append(f"Declared source {_range(row.get('declared_source_range_seconds'))}; "
                     f"reported source candidate {_range(row.get('best_source_range_seconds'))}.")
        if len(row.get("search_supported_offsets_seconds") or []) > 1:
            parts.append("Multiple source positions were reported; see the findings.")
    return " ".join(parts)


def _summary_sections(response, checks, decision, items, counts, coverage, audio_requested):
    execution = response.get("execution_status", "unknown")
    run_text = {"succeeded": "Core evaluation completed.", "error": "Core evaluation failed.",
                "timeout": "Core evaluation timed out.", "unsupported": "The submission is unsupported by the core evaluator."}
    evaluation = run_text.get(execution, f"Core evaluation state: {execution}.")
    if response.get("error"):
        evaluation += " " + str(response["error"])
    evaluation += " " + _audio_run_state(checks, audio_requested)
    # Provider task failures and insufficient signal are limitations, not interval contradictions.
    limitations = _audio_limitations(checks)
    coverage_text = " ".join(coverage[:3] if _first(checks, "support_breadth").get("coverage_scope") == "provisional" else coverage[:2])
    if limitations:
        coverage_text += " " + " ".join(limitations[:2])
        if len(limitations) > 2:
            coverage_text += f" {len(limitations) - 2} additional provider limitation(s); see detailed diagnostics."
    return [
        {"title": "Evaluation Status", "text": evaluation},
        {"title": "Core Evidence Consistency", "text": _core_summary(response, decision), "scope": CORE_CHECK_SCOPE},
        {"title": "Review Required", "text": _review_summary(items, counts, decision)},
        {"title": "Coverage & Limitations", "text": coverage_text},
        {"title": "Other Key Results", "text": _other_key_results(response, checks)},
    ]


def _refs(row):
    refs = list(row.get("diagnostic_ids") or [])
    if row.get("diagnostic_id"):
        refs.append(row["diagnostic_id"])
    return sorted(set(refs))


def _range_item(row, names, reference, diagnostic_index):
    outcome = row.get("outcome", "unknown")
    group, title, observation = RANGE_STATES.get(outcome, (
        "audio_gap" if outcome in GAP_OUTCOMES else "unknown",
        "Audio analysis not completed" if outcome in GAP_OUTCOMES else f"Uninterpreted audio outcome: {outcome}",
        "No applicable conclusion is available; see the recorded reason." if outcome in GAP_OUTCOMES else
        "This state has not been interpreted as a pass or failure."))
    refs = _refs(row)
    providers = sorted({diagnostic_index[r].get("provider", "unknown") for r in refs if r in diagnostic_index})
    details = [observation,
               f"Declared source: {_range(row.get('declared_source_range_seconds'))}; "
               f"target: {_range(row.get('declared_target_range_seconds'))}."]
    if row.get("best_source_range_seconds") is not None:
        details.append(f"Reported best source candidate: {_range(row['best_source_range_seconds'])}.")
    offsets = row.get("search_supported_offsets_seconds") or []
    if offsets:
        target = row.get("declared_target_range_seconds")
        candidates = [_range([offset + target[0], offset + target[1]]) for offset in offsets] if (
            isinstance(target, list) and len(target) == 2 and all(isinstance(v, (int, float)) for v in target)) else [str(o) + " s offset" for o in offsets]
        details.append(f"Supported candidate position(s): {len(offsets)} — " + "; ".join(candidates) + ".")
        if len(offsets) > 1:
            details.append("Repeated content may give several valid positions; no unique historical position is established.")
    agreement = row.get("spectromap_agrees")
    if "spectromap_agrees" in row:
        details.append("SpectroMap: " + ("also observed correspondence near the reported best offset." if agreement is True else
                       "did not corroborate that offset." if agreement is False else "agreement not assessed."))
    if providers:
        details.append("Methods: " + ", ".join(providers) + ". These are algorithm observations, not independent witnesses.")
    if row.get("message") or row.get("reason"):
        details.append("Recorded detail: " + str(row.get("message") or row.get("reason")))
    return {"id": _stable_id([reference, refs, row]), "group": group, "title": title,
            "affected": _affected(row, names) + f" [{row.get('relationship_type', 'relationship unknown')}]" +
                        (f" selection {row['selection_id']}" if row.get("selection_id") is not None else ""),
            "explanation": "\n".join(details), "suggestion":
            "Review the selected source, declared intervals and processing; listen to both files. "
            "Only the submitter should correct a declaration and rerun the assessment.",
            "scoring_note": AUDIO_NOTE, "affects_integrity": False,
            "outcome": outcome, "severity": row.get("severity"),
            "references": [reference], "diagnostic_ids": refs, "raw": [copy.deepcopy(row)]}


def _same_range(left, right):
    # Explicit task references take precedence. Partial overlaps can link two independent windows,
    # so require the SAME task's IDs, or an unambiguous window/selection identity.
    a, b = set(_refs(left)), set(_refs(right))
    if a and b:
        return a == b and all(left.get(k) == right.get(k) for k in (
            "declared_source_range_seconds", "declared_target_range_seconds", "selection_id")
            if k in left and k in right)
    keys = ("source_hash", "target_hash", "relationship_type", "selection_id", "relation_id",
            "declared_source_range_seconds", "declared_target_range_seconds")
    if not (left.get("declared_source_range_seconds") is not None or
            left.get("selection_id") is not None and left.get("relation_id") is not None):
        return False
    # A finding may omit the target interval but still name the exact same source interval.
    identity_keys = [k for k in keys if k != "declared_target_range_seconds" or k in left and k in right]
    return all(left.get(k) == right.get(k) for k in identity_keys)


def build_review_presentation(response, file_names=None, policy_rules=None, *, audio_requested=None,
                              snapshot_time=None, notes=()):
    """Create a detached display model. NEVER insert this model into response or its JSON export."""
    names = dict(file_names or {})
    checks = response.get("assessment_checks") or {}
    decision = _first(checks, "integrity_decision")
    items = []
    if response.get("execution_status") != "succeeded" and response.get("error"):
        items.append({"id": "execution-detail", "group": "core", "title": "Evaluation did not complete",
                      "affected": "Submission", "explanation": str(response["error"]),
                      "suggestion": "Resolve the reported execution error and rerun the assessment.",
                      "severity": None, "affects_integrity": None,
                      "scoring_note": "Execution failure is not an authenticity verdict; retain the reported axis states.",
                      "references": ["execution_status", "error"], "diagnostic_ids": [], "raw": []})
    leaves = attention_items(response, names, policy_rules)
    for index, issue in enumerate(leaves):
        if issue.get("collection") == "integrity_layers":
            # Layer aggregates are explained below the axes; leaf details own attention rows.
            continue
        group = ("gap" if issue.get("collection") == "workflow_assessment" or
                 issue.get("raw_status") in {"unavailable", "not_completed", "inconclusive"} else "core")
        if issue.get("scoring_eligible") is False:
            group = "audio_unresolved" if issue.get("raw_status") != "contradicted" else "audio_review"
        items.append({"id": _stable_id([issue, index]), "group": group, "title": issue["status"],
                      "affected": issue["affected"], "explanation": issue["explanation"],
                      "suggestion": issue["suggestion"], "severity": issue.get("severity"),
                      "affects_integrity": False if issue.get("scoring_eligible") is False else None,
                      "scoring_note": AUDIO_NOTE if issue.get("scoring_eligible") is False else
                        "Scoring attribution is not inferred from this display label; consult the recorded core decision and checks.",
                      "references": [f"{issue.get('collection')}:{issue.get('reference') or index}"],
                      "diagnostic_ids": [], "raw": []})
    diagnostic_index = {}
    for collection, rows in checks.items():
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("diagnostic_id"):
                    diagnostic_index[row["diagnostic_id"]] = row
    ranges = []
    for index, row in enumerate(checks.get("audio_declared_range") or []):
        reliable_task = bool(_refs(row) or row.get("selection_id") is not None and row.get("relation_id") is not None)
        previous = next((item for raw, item in ranges if reliable_task and _same_range(raw, row)), None)
        if previous:
            previous["references"].append(f"audio_declared_range[{index}]")
            previous["raw"].append(copy.deepcopy(row))
        else:
            item = _range_item(row, names, f"audio_declared_range[{index}]", diagnostic_index)
            ranges.append((row, item)); items.append(item)
    for index, finding in enumerate(response.get("findings") or []):
        if finding.get("code") == "AUDIO_DECLARED_RANGE_NOT_SUPPORTED":
            candidates = [item for raw, item in ranges if _same_range(raw, finding)]
            previous = candidates[0] if len(candidates) == 1 else None
            if previous:
                previous["references"].append(f"findings[{index}]")
                previous["raw"].append(copy.deepcopy(finding))
                previous["severity"] = finding.get("severity", previous["severity"])
            else:
                item = _range_item(finding, names, f"findings[{index}]", diagnostic_index)
                ranges.append((finding, item)); items.append(item)
        elif finding.get("scoring_eligible") is False:
            # Other report-only findings remain accessible without being labelled contradictions.
            if finding.get("code") in {"AUDIO_FINGERPRINT_OBSERVATION", "AUDIO_STRUCTURE_OBSERVATION",
                    "AUDIO_FINGERPRINT_AGREEMENT_OBSERVATION", "AUDIO_SEMANTICS_OBSERVATION",
                    "AUDIO_BANDWIDTH_OBSERVATION", "AUDIO_DIAGNOSTIC_UNAVAILABLE",
                    "AUDIO_DIAGNOSTIC_NOT_APPLICABLE"}:
                continue  # provider details and execution summary below
            items.append({"id": _stable_id(["finding", index, finding]),
                          "group": "audio_unresolved" if finding.get("code") == "AUDIO_UNUSED_RANGE_FOUND_IN_FINAL" else "support",
                          "title": str(finding.get("code", "Audio observation")).replace("_", " "),
                          "affected": _affected(finding, names), "explanation": finding.get("message", ""),
                          "suggestion": "Review the observation within its stated scope.",
                          "severity": finding.get("severity"), "affects_integrity": False,
                          "scoring_note": AUDIO_NOTE, "references": [f"findings[{index}]"],
                          "diagnostic_ids": _refs(finding), "raw": [copy.deepcopy(finding)]})
    items.sort(key=lambda item: ORDER[item["group"]])
    counts = {key: sum(item["group"] == key for item in items) for key in GROUP_LABELS}
    coverage = _coverage(checks)
    audio = _first(checks, "audio_diagnostic_summary")
    if audio:
        coverage.append(f"Audio provider tasks: {audio.get('completed_provider_tasks', 'unknown')} completed; "
                        f"{audio.get('unavailable_provider_tasks', 'unknown')} unavailable. "
                        "Provider tasks are not counts of verified files, relationships or seconds.")
    axes = []
    for name, axis in (response.get("axes") or {}).items():
        axes.append({"key": name, "label": AXIS_LABELS.get(name, name),
                     "availability": axis.get("availability", "unknown"),
                     "value": axis_value_label(name, axis, response),
                     "confidence": "Not assessed" if axis.get("confidence") is None else f"{axis['confidence']:.3f}",
                     "status": _core_summary(response, decision) if name == "integrity" else axis.get("status") or axis.get("availability", "unknown"),
                     "reasoning": axis.get("reasoning") or "Reasoning not supplied; structured checks remain available."})
    sections = _summary_sections(response, checks, decision, items, counts, coverage, audio_requested)
    summary = [section["title"] + ": " + section["text"] +
               ("\nScope: " + section["scope"] if section.get("scope") else "") for section in sections]
    return {"template_version": TEMPLATE_VERSION, "summary": summary, "summary_sections": sections, "scope_note": SCOPE_NOTE,
            "items": items, "counts": counts, "axes": axes, "coverage": coverage,
            "metadata": {"case_id": response.get("case_id"), "run_id": response.get("run_id"),
                         "snapshot_time": snapshot_time or "not recorded",
                         "system": copy.deepcopy(response.get("system") or {}),
                         "audio_requested": audio_requested},
            "file_names": names, "notes": list(notes),
            "details": audio_diagnostic_lines(response, names) + music_semantics_lines(response, names),
            "integrity_layers": copy.deepcopy(checks.get("integrity_layers") or []),
            "evidence_strength": copy.deepcopy(checks.get("evidence_strength") or []),
            "raw_response": copy.deepcopy(response)}


def confidence_lines(model):
    layers = [row for row in model["integrity_layers"] if row.get("contributes_to_integrity") is True]
    checked = sum(row.get("value") is not None for row in layers)
    lines = [f"Contributing core layers checked: {checked}/{len(layers)}." if layers else
             "Contributing core layers checked: unknown (layer details not supplied)."]
    for row in layers:
        lines.append(f"  {str(row.get('layer', 'unknown')).replace('_', ' ')}: {row.get('status', 'unknown')}; "
                     f"{row.get('check_count', 'unknown')} check(s). {row.get('reason', '')}")
    strength = model["evidence_strength"]
    lines.append("Evidence strength factor for confidence: " +
                 (str(strength[0].get("confidence_factor", "unknown")) if strength else "not supplied") + ".")
    return lines


def review_text(model, *, include_support=False):
    """The same ordered content is used by the GUI, clipboard and offline report."""
    lines = ["Assessment summary", *model["summary"], "", model["scope_note"], "", "Items requiring attention"]
    visible = [item for item in model["items"] if include_support or item["group"] != "support"]
    if not visible:
        lines.append("No review items identified in the available display details. "
                     "Check execution, diagnostic state and coverage above; missing analyses are not passes.")
    last = None
    for index, item in enumerate(visible, 1):
        if last != item["group"]:
            lines.extend(["", GROUP_LABELS[item["group"]]]); last = item["group"]
        lines.extend([f"{index}. {item['title']} — {item['affected']}", item["explanation"],
                      item["scoring_note"], "Suggested action: " + item["suggestion"],
                      "Evidence references: " + ", ".join(item["references"] + item["diagnostic_ids"]), ""])
    lines.extend(["Four separate dimensions"])
    for axis in model["axes"]:
        lines.extend([f"{axis['label']}: {axis['value']} | {axis['status']} | "
                      f"assessment confidence {axis['confidence']}", axis["reasoning"]])
    lines.extend(["", "Coverage and limitations", *model["coverage"],
                  "Confidence is not a probability of authenticity or the percentage of recordings checked.",
                  *confidence_lines(model),
                  "", *model["details"], *model["notes"], "", "Snapshot and display version",
                  json.dumps(model["metadata"], ensure_ascii=False, indent=2),
                  "Display template: " + model["template_version"]])
    return "\n".join(lines)
