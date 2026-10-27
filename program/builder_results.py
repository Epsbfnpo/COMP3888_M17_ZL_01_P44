"""Artist-facing attention items derived without modifying evaluator results."""

from __future__ import annotations


WORKFLOW_STATUSES = {
    "satisfied": "success",
    "missing": "issue",
    "not_assessed": "unassessed",
    "not_applicable": "hide",
}

BOOLEAN_CHECK_STATUSES = {
    True: "success",
    False: "issue",
}

DERIVATION_STATUSES = {
    "matched": "success",
    "corroborated": "supportive",
    "contradicted": "issue",
    "inconclusive": "unassessed",
    "unavailable": "unassessed",
    "not_applicable": "hide",
}

LAYER_STATUSES = {
    "matched": "success",
    "corroborated": "supportive",
    "contradicted": "issue",
    # The per-file "Check incomplete" row says which file and why; the axis shows "Incomplete".
    "not_completed": "hide",
    "inconclusive": "unassessed",
    "unavailable": "unassessed",
    "not_applicable": "hide",
}

AUDIO_DIAGNOSTIC_STATUSES = {
    "matched": "success",
    "corroborated": "supportive",
    "clear": "success",
    "contradicted": "issue",
    "not_supported": "issue",
    "suspected_decoy": "issue",
    "inconclusive": "unassessed",
    "unavailable": "unassessed",
    "not_applicable": "hide",
}

PARSER_STATUSES = {
    "parsed": "success",
    "partial": "unassessed",
    "failed": "issue",
    "not_assessed": "hide",
}

CHECK_COLLECTIONS = {
    "bound_file_integrity": BOOLEAN_CHECK_STATUSES,
    "structural_validity": BOOLEAN_CHECK_STATUSES,
    "declared_file_properties": BOOLEAN_CHECK_STATUSES,
    "relationship_integrity": BOOLEAN_CHECK_STATUSES,
    "cross_evidence": BOOLEAN_CHECK_STATUSES,
    "edit_derivation": DERIVATION_STATUSES,
    "comp_derivation": DERIVATION_STATUSES,
    "stem_derivation": DERIVATION_STATUSES,
    "audio_derivation": DERIVATION_STATUSES,
    "master_derivation": DERIVATION_STATUSES,
    "integrity_layers": LAYER_STATUSES,
    "audio_alignment": AUDIO_DIAGNOSTIC_STATUSES,
    "source_contribution": AUDIO_DIAGNOSTIC_STATUSES,
    "comp_verification": AUDIO_DIAGNOSTIC_STATUSES,
    "processed_audio_match": AUDIO_DIAGNOSTIC_STATUSES,
    "stem_mix_residual": AUDIO_DIAGNOSTIC_STATUSES,
    "excerpted_from": AUDIO_DIAGNOSTIC_STATUSES,
    "decoy_source": AUDIO_DIAGNOSTIC_STATUSES,
}
# v0.6.0 comparison results carry a comparison_mode; any collection of such
# items that is not listed above is still handled with the diagnostic mapping.
MODE_LABELS = {
    "pairwise_continuity": "Continuity comparison",
    "excerpt_search": "Excerpt search",
    "selection_timeline": "Comp selection timeline",
    "multi_source_contribution": "Multi-source contribution",
    "master_continuity": "Master continuity",
    "unsupported_relationship": "Relationship comparison",
}
COMPARISON_NOTE = " A signal match supports or contradicts a claim; it is not proof of provenance."

FINAL_C2PA_DETAIL_FINDINGS = frozenset({
    "C2PA_CONTENT_BINDING_FAILED", "C2PA_SIGNATURE_INVALID", "C2PA_VALIDATION_FAILED",
    "C2PA_READ_FAILED", "C2PA_SIGNER_UNTRUSTED", "C2PA_PARSER_UNAVAILABLE"})
FINAL_C2PA_ATTENTION = {
    "invalid": ("Final file's C2PA is invalid", "Submit the file exactly as signed, or declare and document the edits made after signing."),
    "unreadable": ("Final file's C2PA cannot be read", "Re-export the file from the tool that signed it, or remove the damaged C2PA chunk and explain why."),
    "untrusted": ("Final file's C2PA signer is not trusted", "Provide a manifest from a signer on the C2PA Trust List if the signature is meant to support the claims."),
    "not_checked": ("Final file's C2PA not checked", "Install the required C2PA dependency (pip install -r requirements.txt), then rerun the assessment."),
    "unclear": ("Final file's C2PA state unclear", "Check the manifest with the issuing tool, then resubmit."),
}
ATTENTION_FINDINGS = {
    "MISSING_OBJECT": ("Missing file", "Locate the original file; confirm its SHA-256 matches the manifest."),
    "PARSER_FAILED": ("Invalid evidence", "Replace or repair the file so its declared format can be parsed."),
    "WAV_PARSER_FAILED": ("Audio not assessed", "Replace or repair this audio with a supported WAV file (integer PCM or IEEE float)."),
    "AUDIO_PARSER_FAILED": ("Audio not assessed", "Replace or repair this audio with a supported WAV, FLAC or MP3 (MPEG Layer III) file."),
    "CROSS_EVIDENCE_CHECK_UNAVAILABLE": ("Cross-evidence check not assessed", "Provide the supported source fields or bindings needed to compare this evidence."),
    "UNKNOWN_ARTEFACT_TYPE_SKIPPED": ("Evidence not assessed", "Choose a supported artefact type; the evaluator did not infer a type for this file."),
    "UNKNOWN_RELATIONSHIP_TYPE_SKIPPED": ("Relationship not assessed", "Choose a supported relationship type; the evaluator did not infer the submitted relationship."),
    "RELATIONSHIP_SKIPPED_QUARANTINED_ENDPOINT": ("Relationship not assessed", "Correct the unsupported endpoint type, then rerun evaluation."),
    "RELATIONSHIP_ENDPOINTS_SKIPPED": ("Relationship not assessed", "Choose a registered relationship whose source and target roles match these files."),
    "PARTIAL_INPUT_COVERAGE": ("Assessment incomplete", "Correct or replace skipped input types and relationships, then rerun the assessment."),
    "C2PA_READ_FAILED": ("Attestation not assessed", "Check the C2PA file and provide a readable supported manifest if attestation is required."),
    "C2PA_CONTENT_BINDING_FAILED": ("C2PA does not match the file", "Submit the file exactly as signed, or declare and document the edits made after signing."),
    "C2PA_SIGNATURE_INVALID": ("C2PA signature invalid", "Obtain a correctly signed file from the original tool; the manifest may have been altered."),
    "C2PA_VALIDATION_FAILED": ("C2PA validation failed", "Check the C2PA manifest with the issuing tool, then resubmit."),
    "C2PA_KNOWN_AI_SIGNER": ("Signed AI-generation label", "Declare this artefact as ai-generated, or explain why the signed AI label does not apply."),
    "C2PA_SIGNER_UNTRUSTED": ("Signer not trusted", "Provide a manifest from a trusted signer if this signature is meant to support the claims."),
    "C2PA_PARSER_UNAVAILABLE": ("Attestation not assessed", "Install the required C2PA dependency (pip install -r requirements.txt), then rerun the assessment."),
    "AI_DISCLOSURE_MISSING": ("Missing declaration", "Declare the creation method for each relevant audio artefact."),
    "MANUAL_REVIEW_RECOMMENDED": ("Manual review", "Review the related content-check details and provide any missing supported evidence."),
    "WORKFLOW_POLICY_NOT_APPLIED": ("Completeness not assessed", "Select a workflow so applicable evidence expectations can be checked."),
    "UNSUPPORTED_INPUT": ("Unsupported input", "Use a supported submission profile and file format."),
    "INVALID_OR_FAILED_INPUT": ("Evaluation error", "Correct the reported input or validation error, then run the assessment again."),
    "TIMEOUT": ("Evaluation timed out", "Reduce the submission size or complexity and run the assessment again."),
    "WORKER_ERROR": ("Evaluation error", "Check the evaluator setup and the reported worker error, then retry."),
}


def axis_value_label(name, axis, response):
    """Value text for the axis table; a contradicted Integrity shows its count beside the number."""
    text = "Not assessed" if axis["value"] is None else f"{axis['value']:.3f}"
    # integrity_decision is present even when include_reasoning=false omits the explanations.
    decision = ((response.get("assessment_checks") or {}).get("integrity_decision") or [{}])[0] or {}
    if name == "integrity" and axis.get("status") == "incomplete":
        # A file check that did not finish is never shown as a value or a pass.
        count = decision.get("not_completed_count")
        return f"Incomplete ({count} file check{'' if count == 1 else 's'} not completed)" if count else "Incomplete"
    if name != "integrity" or axis.get("status") != "contradicted":
        return text
    count = decision.get("contradiction_count")
    return f"{text} (contradicted: {count})" if count else f"{text} (contradicted)"


def _hash_label(digest, names):
    return names.get(digest, f"file {digest[:12]}") if digest else ""


def _affected(item, names):
    source = item.get("source_hash")
    target = item.get("target_hash")
    sources = item.get("source_hashes") or []
    if source and target:
        return f"{_hash_label(source, names)} → {_hash_label(target, names)}"
    if target and sources:
        return f"{', '.join(_hash_label(digest, names) for digest in sources)} → {_hash_label(target, names)}"
    if target:
        return _hash_label(target, names)
    if source:
        return _hash_label(source, names)
    digest = next((item.get(key) for key in (
        "evidence_hash", "asset_hash", "audio_hash", "owner_hash", "target_hash",
        "source_hash") if item.get(key)), None)
    if digest:
        return _hash_label(digest, names)
    if item.get("relationship_type"):
        return f"{item['relationship_type']} relationship"
    return "Submission evidence"


def _suggestion_for_check(collection, item, status):
    rule = item.get("rule", "")
    if status == "incomplete":
        return ("Run the assessment again with a longer time limit (or install the audio decoder if one "
                "is missing). Until the check finishes, this file is not counted as checked.")
    if collection == "bound_file_integrity":
        return ("Relocate or replace the original file and confirm its SHA-256 matches "
                "the declared hash." if item.get("rule") == "object_hash_bound"
                else "Repair or replace the file with a readable file of its declared type.")
    if collection == "structural_validity":
        return "Correct the graph references and relationship endpoints, then rerun evaluation."
    if collection == "declared_file_properties":
        return "Check the declared technical value against the file."
    if collection == "relationship_integrity":
        return "Check the declared technical value or relationship range against the files."
    if collection == "cross_evidence":
        return "Check that the metadata, sheet references, and submitted graph agree."
    if collection in {"edit_derivation", "comp_derivation", "stem_derivation",
                      "audio_derivation", "master_derivation"}:
        missing = item.get("missing_parameters")
        if missing:
            return f"Provide the missing reconstruction parameters: {missing}."
        return ("Review the declared source/target ranges and processing, or provide readable "
                "evidence supported by this content check.")
    if collection == "integrity_layers":
        return "Provide the applicable files or independent evidence needed for this layer to be assessed."
    if collection in {
            "audio_alignment", "source_contribution", "comp_verification",
            "processed_audio_match", "stem_mix_residual", "mastering_derivation",
            "excerpted_from", "decoy_source"}:
        if item.get("status") == "inconclusive":
            return ("Declare source/target ranges (or add a comp sheet for comps) so "
                    "the comparison has something to anchor on; heavy processing can "
                    "also make a result inconclusive.")
        if status == "unassessed":
            return ("Provide readable source and target audio and complete declared ranges; "
                    "if the resource budget was reached, reduce the audio workload and retry.")
        return ("Review the declared source, target, and transformation details against the "
                "reported signal mismatch; signal similarity alone does not prove provenance.")
    return ("Check the related evidence and correct or add the information required "
            "for this check.")


def _check_explanation(item):
    explanation = (item.get("reason") or item.get("explanation")
                   or item.get("rule", "The check needs attention."))
    if item.get("analysis_complete") is False:
        explanation += " The analysis did not complete (time or resource limit)."
    if item.get("comparison_mode"):
        explanation += COMPARISON_NOTE
    regions = item.get("local_mismatch_regions") or []
    if regions:
        intervals = ", ".join(
            f"{region['start_seconds']:.2f}–{region['end_seconds']:.2f} s"
            for region in regions)
        explanation = f"{explanation} Affected intervals: {intervals}."
    return explanation


def _workflow_suggestion(result, rule):
    kind = (rule or {}).get("kind", result.get("kind"))
    if kind == "artifact_presence":
        expected = ", ".join((rule or {}).get("artifact_types", []))
        role = (rule or {}).get("role")
        extra = f" with role {role!r}" if role else ""
        description = f" ({expected})" if expected else ""
        return f"Add the required evidence file{description}{extra}, or correct its declared type and role."
    if kind == "relationship_presence":
        expected = ", ".join((rule or {}).get("relationship_types", []))
        description = f" ({expected})" if expected else ""
        return f"Add the required source-to-target relationship{description} between the applicable files."
    if kind == "ancestry_path":
        return "Connect the required source evidence to the final artefact using an allowed relationship."
    if kind == "declaration_presence":
        declaration = (rule or {}).get("declaration", "")
        return f"Complete the required declaration: {declaration}."
    if kind == "alternative_group":
        return "Provide at least one of the listed evidence alternatives."
    return "Review this workflow expectation and provide the applicable evidence or declaration."


def _rule_index(policy_rules):
    if isinstance(policy_rules, dict):
        return policy_rules
    result = {}

    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("rule_id"), str):
                result[value["rule_id"]] = value
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(policy_rules)
    return result


def attention_workflow_results(results, policy_rules=None):
    """Keep missing/unassessed required expectations; hide successful and optional absence."""
    index = _rule_index(policy_rules)
    visible = []
    for result in results:
        status = WORKFLOW_STATUSES.get(result.get("status"), "unknown")
        if status in {"success", "hide"}:
            continue
        if result.get("level") == "optional" and status == "issue":
            continue
        rule = index.get(result.get("rule_id"), {})
        if status == "unknown":
            title = f"Needs review ({result.get('status', 'unknown status')})"
            suggestion = "Review this unrecognized workflow status; it was not assumed to pass or fail."
        elif status == "issue":
            title = "Missing required evidence"
            suggestion = _workflow_suggestion(result, rule)
        else:
            title = "Required check not assessed"
            suggestion = ("Make the evidence readable by its required parser or provide "
                          "another supported item for this workflow expectation.")
        visible.append({
            **result,
            "display_status": title,
            "suggestion": suggestion,
            "affected": _workflow_affected(rule),
        })
    return visible


def _workflow_affected(rule):
    kind = rule.get("kind")
    if kind == "artifact_presence":
        expected = ", ".join(rule.get("artifact_types", []))
        return f"Expected file: {expected}" if expected else "Expected workflow evidence file"
    if kind == "relationship_presence":
        expected = ", ".join(rule.get("relationship_types", []))
        return f"Expected relationship: {expected}" if expected else "Expected evidence relationship"
    if kind == "ancestry_path":
        return "Expected connected source evidence"
    if kind == "declaration_presence":
        return f"Declaration: {rule.get('declaration', 'required workflow information')}"
    return "Workflow evidence"


def attention_items(response, file_names=None, policy_rules=None):
    """Return only actionable/unassessed UI rows while preserving the response object."""
    names = file_names or {}
    issues = []
    workflow = response.get("workflow_assessment") or {}
    for result in attention_workflow_results(
            workflow.get("rule_results", []), policy_rules):
        issues.append({
            "status": result["display_status"],
            "affected": result["affected"],
            "explanation": result.get("reason", ""),
            "suggestion": result["suggestion"],
        })

    checks = response.get("assessment_checks") or {}
    collections = dict(CHECK_COLLECTIONS)
    for name, items in checks.items():
        if (name not in collections and isinstance(items, list) and items
                and all(isinstance(i, dict) and "comparison_mode" in i for i in items)):
            collections[name] = AUDIO_DIAGNOSTIC_STATUSES
    for collection, mapping in collections.items():
        for item in checks.get(collection, []):
            if item.get("reason_code") == "disabled_by_policy":
                continue  # policy-disabled placeholder: nothing for the artist to act on
            if collection == "integrity_layers":
                disposition = mapping.get(item.get("status"), "unknown")
                if item.get("status") == "unavailable" and not item.get(
                        "contributes_to_integrity", False):
                    disposition = "hide"
                if item.get("status") == "unavailable" and not item.get("check_count", 0):
                    disposition = "hide"
            elif collection in ("bound_file_integrity", "structural_validity", "declared_file_properties",
                                "relationship_integrity", "cross_evidence"):
                disposition = ("unassessed" if item.get("status") == "unavailable"
                               else "incomplete" if item.get("status") == "not_completed"
                               else mapping.get(item.get("passed"), "unknown"))
                if (collection == "bound_file_integrity"
                        and item.get("rule") in ("declared_type_parser_succeeds", "audio_file_readable")
                        and item.get("passed") is False):
                    continue
            else:
                disposition = mapping.get(item.get("status"), "unknown")
            if disposition in {"success", "supportive", "hide"}:
                continue
            label = MODE_LABELS.get(item.get("comparison_mode"))
            if disposition == "issue":
                title = "Contradiction" if item.get("status") == "contradicted" else "Check failed"
            elif item.get("status") == "inconclusive":
                title = f"{label or 'Comparison'} inconclusive"
            elif item.get("status") == "unavailable" and item.get(
                    "comparison_mode") == "unsupported_relationship":
                continue  # nothing to act on: no comparison exists for this type
            elif disposition == "incomplete":
                title = "Check incomplete"
            elif disposition == "unassessed":
                title = "Check not assessed"
            else:
                title = f"Needs review ({item.get('status', item.get('passed', 'unknown'))})"
            issues.append({
                "status": title,
                "affected": _affected(item, names),
                "explanation": _check_explanation(item),
                "suggestion": _suggestion_for_check(collection, item, disposition),
            })

    skip_findings = {
        "FILE_INTEGRITY_CONTRADICTION",
        "DECLARATION_CONSISTENCY_CONTRADICTION",
        "RELATIONSHIP_INTEGRITY_CONTRADICTION",
        "CROSS_EVIDENCE_CONTRADICTION",
        "AUDIO_DERIVATION_CONTRADICTION",
        "TECHNICAL_CLAIM_MISMATCH",
        "PHYSICAL_AUDIO_CONTRADICTION",
        "DECLARED_SOURCE_NOT_SUPPORTED",
        "DECOY_SOURCE_SUSPECTED",
        "CROSS_EVIDENCE_CHECK_UNAVAILABLE",
        "FILE_CHECK_NOT_COMPLETED",  # shown once, as its "Check incomplete" row
    }
    findings = response.get("findings", [])
    failed_parser_hashes = {
        finding.get("evidence_hash") for finding in findings
        if finding.get("code") == "PARSER_OBSERVATION"
        and finding.get("parser_status") == "failed"
        and finding.get("evidence_hash")
    }
    # The final artefact's C2PA state is summarised once; its detailed findings
    # would repeat the same problem.
    final_c2pa = next((item for item in findings if item.get("code") == "C2PA_FINAL_ATTESTATION"), None)
    for finding in findings:
        code = finding.get("code")
        if code in skip_findings:
            continue
        if (final_c2pa and code in FINAL_C2PA_DETAIL_FINDINGS
                and finding.get("evidence_hash", final_c2pa.get("evidence_hash")) == final_c2pa.get("evidence_hash")
                and finding.get("scope", "final") == "final"):
            continue
        if code == "PARSER_FAILED" and finding.get("evidence_hash") in failed_parser_hashes:
            continue
        if code == "PARSER_OBSERVATION":
            status = finding.get("parser_status")
            disposition = PARSER_STATUSES.get(status, "unknown")
            if disposition in {"success", "hide"}:
                continue
            title = ("Invalid evidence" if disposition == "issue" else
                     "Evidence not fully assessed" if disposition == "unassessed" else
                     f"Needs review ({status or 'unknown parser status'})")
            suggestion = (
                "Repair or replace the file with a readable file of its declared type."
                if disposition == "issue" else
                "Review parser warnings and provide a complete supported evidence file."
                if disposition == "unassessed" else
                "Review this parser status; it was not assumed to pass or fail.")
        elif code == "C2PA_FINAL_ATTESTATION":
            state = finding.get("final_c2pa_state")
            if state not in FINAL_C2PA_ATTENTION:
                continue  # absent or trusted: nothing to act on
            title, suggestion = FINAL_C2PA_ATTENTION[state]
        elif code in ATTENTION_FINDINGS:
            title, suggestion = ATTENTION_FINDINGS[code]
        else:
            continue
        issues.append({
            "status": title,
            "affected": _affected(finding, names),
            "explanation": finding.get("message", code),
            "suggestion": suggestion,
        })
    return issues


# Mirrors music_target.audio_diagnostics.runner; kept here so the GUI needs no audio imports.
INSUFFICIENT_SUPPORT_REASONS = frozenset({
    "silent_signal", "insufficient_signal", "insufficient_landmarks", "insufficient_peaks",
    "insufficient_spectral_frames", "channel_cancellation"})
# The GUI greys out lines containing this marker: a normal outcome, not an error.
INSUFFICIENT_SUPPORT_LABEL = "insufficient support (normal outcome)"


def _seconds(value):
    return f"{value:.2f} s" if isinstance(value, (int, float)) else "?"


def _not_completed(row):
    """Text for a row that has no observation, or None when the row completed."""
    if row.get("status") != "unavailable":
        return None
    if row.get("reason_code") in INSUFFICIENT_SUPPORT_REASONS:
        return f"{INSUFFICIENT_SUPPORT_LABEL}: {row.get('message', '')}".rstrip()
    if row.get("reason_code") == "provider_inputs_unavailable":
        return f"not run: {row.get('message', '')}".rstrip()
    return f"not run ({row.get('reason_code')}): {row.get('message', '')}".rstrip()


def _pairs(rows, names):
    """Group diagnostic rows by declared relationship, keeping submission order."""
    groups = {}
    for row in rows:
        key = (row.get("relation_id"), row.get("selection_id"))
        if key not in groups:
            heading = (f"  {_hash_label(row.get('source_hash'), names)} → "
                       f"{_hash_label(row.get('target_hash'), names)} [{row.get('relationship_type', '?')}]")
            groups[key] = (heading, [])
        groups[key][1].append(row)
    return groups.values()


def _fingerprint_line(row):
    provider = row.get("provider", "?")
    unavailable = _not_completed(row)
    if unavailable:
        return f"    - {provider}: {unavailable}"
    matches = row.get("matches") or []
    if not matches:
        return f"    - {provider}: no shared passage found (absence is not evidence of independence)"
    top = max(matches, key=lambda match: match.get("matched_token_pairs") or 0)
    support = (top.get("support_assessment") or {}).get("status") if isinstance(top.get("support_assessment"), dict) else None
    detail = f"{top.get('matched_token_pairs', '?')} matching token pairs"
    detail += f"; {support.replace('_', ' ')}" if support else "; uncalibrated"
    others = f"; {len(matches) - 1} weaker candidate offset(s)" if len(matches) > 1 else ""
    return (f"    - {provider}: strongest offset source − target = "
            f"{_seconds(top.get('source_minus_target_offset_seconds'))} ({detail}{others})")


def _agreement_line(row, group):
    unavailable = _not_completed(row)
    inputs = [item for item in group if item.get("provider") in ("audfprint", "spectromap")
              and item.get("status") == "unavailable"]
    if (row.get("reason_code") == "provider_inputs_unavailable" and inputs
            and all(item.get("reason_code") in INSUFFICIENT_SUPPORT_REASONS for item in inputs)):
        # Agreement inherits the outcome of the fingerprint rows it needs.
        unavailable = f"{INSUFFICIENT_SUPPORT_LABEL}: {row.get('message', '')}".rstrip()
    if unavailable:
        return f"    - both algorithms: {unavailable}"
    matches = row.get("matches") or []
    if not matches:
        return "    - both algorithms: no offset where both voted"
    top = max(matches, key=lambda match: min(match.get("audfprint_votes") or 0, match.get("spectromap_votes") or 0))
    chance = (row.get("metrics") or {}).get("expected_chance_agreements")
    chance_text = f"; agreements expected by chance ≈ {chance:.2g}" if isinstance(chance, (int, float)) else ""
    return (f"    - both algorithms agree at source − target = "
            f"{_seconds(top.get('source_minus_target_offset_seconds'))} "
            f"(audfprint {top.get('audfprint_votes')} / SpectroMap {top.get('spectromap_votes')} votes{chance_text})")


def audio_diagnostic_lines(response, file_names=None):
    """Plain-language fingerprint and structure lines for the report-only audio diagnostics.

    These rows never affect scores and are not attention items. A shared offset is a
    content match; it is not provenance.
    """
    names = file_names or {}
    checks = response.get("assessment_checks") or {}
    summary = (checks.get("audio_diagnostic_summary") or [None])[0]
    if not summary:
        return []
    lines = ["Audio diagnostics (report-only; no effect on any score; a content match is not "
             "proof of provenance):"]
    if summary.get("reason_code"):
        lines.append(f"  Not completed: {summary.get('message', summary['reason_code'])}")
    lines.append(f"  {summary.get('completed_provider_tasks', 0)} provider task(s) completed, "
                 f"{summary.get('unavailable_provider_tasks', 0)} unavailable.")
    rows = ((checks.get("audio_fingerprint") or []) + (checks.get("audio_fingerprint_agreement") or [])
            + (checks.get("audio_structure") or []))
    lines.extend(calibration_lines(rows + (checks.get("audio_semantics") or [])))
    for heading, group in _pairs(rows, names):
        lines.append(heading)
        for row in group:
            provider = row.get("provider")
            if provider in ("audfprint", "spectromap"):
                lines.append(_fingerprint_line(row))
            elif provider == "fingerprint_agreement":
                lines.append(_agreement_line(row, group))
            else:
                unavailable = _not_completed(row)
                metrics = row.get("metrics") or {}
                lines.append(f"    - {provider}: " + (unavailable or (
                    f"{len(metrics.get('source_sections_seconds') or [])} source / "
                    f"{len(metrics.get('target_sections_seconds') or [])} target acoustic sections "
                    "(structure observation; uncalibrated)")))
    bandwidth = checks.get("audio_bandwidth") or []
    if bandwidth:
        lines.append("  Content bandwidth per file (heuristic; uncalibrated):")
    for row in bandwidth:
        name = names.get(row.get("source_hash")) or (row.get("source_hash") or "")[:12]
        assessment = (row.get("metrics") or {}).get("assessment") or {}
        lines.append(f"    - {name}: " + (_not_completed(row) or assessment.get("reason", "")))
    return lines


def _plain_list(values):
    return ", ".join(str(value).replace("_", " ") for value in values or []) or "none"


def _interval_text(bounds):
    if not isinstance(bounds, dict):
        return ""
    return f" (95% CI {bounds['lower'] * 100:.0f}-{bounds['upper'] * 100:.0f}%)"


def calibration_lines(rows):
    """One line per provider: how far its results have been calibrated, and on what."""
    seen, lines = {}, []
    for row in rows:
        if row.get("provider") not in seen and isinstance(row.get("calibration"), dict):
            seen[row["provider"]] = row["calibration"]
    if not seen:
        return lines
    lines.append("  Calibration (case counts on limited samples with 95% Wilson intervals, not probabilities):")
    for provider, record in seen.items():
        status = str(record.get("status") or "unknown").replace("_", " ")
        text = f"    - {provider}: {status}"
        if record.get("transforms_tested"):
            text += f"; tested: {_plain_list(record['transforms_tested'])}"
        if record.get("not_tested") and record.get("not_tested") != ["all"]:
            text += f"; not tested: {_plain_list(record['not_tested'])}"
        rates = record.get("false_match_rates")
        if isinstance(rates, dict) and rates.get("categories"):
            parts = []
            for name, item in rates["categories"].items():
                label = name.replace("_", " ")
                if "false_matches" in item:
                    part = f"{label} {item['false_matches']}/{item['pairs']} false"
                elif "detected" in item:
                    part = f"{label} {item['detected']}/{item['pairs']} detected"
                else:
                    part = f"{label} {item.get('supported')}/{item['pairs']} supported (unverified)"
                parts.append(part + _interval_text(item.get("wilson_95")))
            text += "; benchmark: " + ", ".join(parts)
        lines.append(text)
    return lines


def _reasons(assessment):
    reasons = [str(item).replace("_", " ") for item in assessment.get("rejection_reasons") or []]
    if len(reasons) > 3:
        return ", ".join(reasons[:3]) + f", +{len(reasons) - 3} more"
    return ", ".join(reasons)


def _describe_file(description):
    key = description.get("key") or {}
    tonic = f"{key.get('tonic')} {key.get('mode')}" if key.get("tonic") else "key ?"
    tempo = description.get("tempo_bpm")
    return f"{tonic}, {tempo:.0f} BPM" if isinstance(tempo, (int, float)) else f"{tonic}, tempo not tracked"


def music_semantics_lines(response, file_names=None):
    """Summarise assessment_checks.audio_semantics: correspondence, transposition and tempo.

    Report-only: a musical correspondence indicates relatedness, never copying,
    direction or authorship, and never changes a score.
    """
    names = file_names or {}
    rows = (response.get("assessment_checks") or {}).get("audio_semantics") or []
    if not rows:
        return []
    lines = ["Music semantics (report-only; a musical match is not proof of provenance):"]
    for heading, group in _pairs(rows, names):
        lines.append(heading)
        for row in group:
            unavailable = _not_completed(row)
            if unavailable:
                lines.append(f"    {unavailable}")
                continue
            metrics = row.get("metrics") or {}
            assessment = metrics.get("correspondence_assessment") or {}
            status = str(assessment.get("status") or "not assessed")
            reasons = _reasons(assessment)
            lines.append(f"    Correspondence: {status.replace('_', ' ')}" + (f" ({reasons})" if reasons else ""))
            shift, ratio, z = (metrics.get("transposition_semitones"), metrics.get("tempo_ratio"),
                               metrics.get("harmonic_similarity_z"))
            if shift is not None or ratio is not None:
                parts = []
                if shift is not None:
                    parts.append(f"transposition {shift:+d} semitone(s)")
                if isinstance(ratio, (int, float)):
                    parts.append(f"tempo ratio {ratio:.3f}")
                if isinstance(z, (int, float)):
                    parts.append(f"harmonic z {z:.1f}")
                lines.append("    " + "; ".join(parts).capitalize())
            if metrics.get("source") and metrics.get("target"):
                lines.append(f"    Source {_describe_file(metrics['source'])} → "
                             f"target {_describe_file(metrics['target'])}")
    return lines
