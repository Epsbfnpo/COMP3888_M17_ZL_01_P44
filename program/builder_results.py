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

ATTENTION_FINDINGS = {
    "MISSING_OBJECT": ("Missing file", "Locate the original file; confirm its SHA-256 matches the manifest."),
    "PARSER_FAILED": ("Invalid evidence", "Replace or repair the file so its declared format can be parsed."),
    "WAV_PARSER_FAILED": ("Audio not assessed", "Replace or repair this audio with a supported integer-PCM WAV file."),
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
    "C2PA_PARSER_UNAVAILABLE": ("Attestation not assessed", "Install the required C2PA dependency (pip install -r requirements.txt), then rerun the assessment."),
    "AI_DISCLOSURE_MISSING": ("Missing declaration", "Declare the creation method for each relevant audio artefact."),
    "MANUAL_REVIEW_RECOMMENDED": ("Manual review", "Review the related content-check details and provide any missing supported evidence."),
    "WORKFLOW_POLICY_NOT_APPLIED": ("Completeness not assessed", "Select a workflow so applicable evidence expectations can be checked."),
    "UNSUPPORTED_INPUT": ("Unsupported input", "Use a supported submission profile and file format."),
    "INVALID_OR_FAILED_INPUT": ("Evaluation error", "Correct the reported input or validation error, then run the assessment again."),
    "TIMEOUT": ("Evaluation timed out", "Reduce the submission size or complexity and run the assessment again."),
    "WORKER_ERROR": ("Evaluation error", "Check the evaluator setup and the reported worker error, then retry."),
}


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
    if collection == "bound_file_integrity":
        return ("Relocate or replace the original file and confirm its SHA-256 matches "
                "the declared hash." if item.get("rule") == "object_hash_bound"
                else "Repair or replace the file with a readable file of its declared type.")
    if collection == "structural_validity":
        return "Correct the graph references and relationship endpoints, then rerun evaluation."
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
            elif collection in ("bound_file_integrity", "structural_validity",
                                "relationship_integrity", "cross_evidence"):
                disposition = ("unassessed" if item.get("status") == "unavailable"
                               else mapping.get(item.get("passed"), "unknown"))
                if (collection == "bound_file_integrity"
                        and item.get("rule") == "declared_type_parser_succeeds"
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
    }
    findings = response.get("findings", [])
    failed_parser_hashes = {
        finding.get("evidence_hash") for finding in findings
        if finding.get("code") == "PARSER_OBSERVATION"
        and finding.get("parser_status") == "failed"
        and finding.get("evidence_hash")
    }
    for finding in findings:
        code = finding.get("code")
        if code in skip_findings:
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


def audio_diagnostic_lines(response, file_names=None):
    """Plain-language lines for the optional, report-only audio diagnostics.

    These rows never affect scores and are not attention items. A candidate is a
    content match found by a fingerprint/structure provider; it is not provenance.
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
    for row in (checks.get("audio_fingerprint") or []) + (checks.get("audio_structure") or []):
        pair = f"{_hash_label(row.get('source_hash'), names)} → {_hash_label(row.get('target_hash'), names)}"
        found = len(row.get("matches") or [])
        state = row.get("status", "?")
        detail = row.get("message", "")
        lines.append(f"  - {row.get('provider', '?')} | {row.get('relationship_type', '?')} | {pair}: "
                     f"{state}, {found} candidate(s). {detail}".rstrip())
    return lines
