"""Cross-evidence audit and conservative aggregation by assessed fact."""

from collections import defaultdict
import hashlib
import json


def aggregate_cross_evidence(checks):
    """One vote per rule/subject; any failed observation keeps the fact failed.

    Documentary hashes and declared values are not fact identities: changing a
    report's formatting, or submitting another answer, must not add support.
    Unknown subjects stay separate. Schema-conformance and workflow-level
    contributor checks are explicitly scoped to the submitted collection.
    """
    groups = defaultdict(list)
    collection_rules = {"rin_xsd_conforms", "ern_xsd_conforms",
                        "rin_contributors_overlap_submitter_declaration",
                        "ern_parties_overlap_submitter_declaration",
                        "workflow_ai_modifier_matches_audio_declarations"}
    subject_keys = ("audio_hash", "asset_hash", "source_hash", "target_hash", "ingredient_hash",
                    "midi_hash", "audio_hashes", "target_hashes", "shape")
    for index, row in enumerate(checks):
        identity = {key: row[key] for key in subject_keys if row.get(key) is not None}
        for field in ("audio_hashes", "target_hashes"):
            if isinstance(identity.get(field), list):
                identity[field] = sorted(set(identity[field]))
        # Selection/cue labels and AAF mob names are local document labels;
        # renaming them must not create extra votes for the same subject.
        if not identity:
            if row.get("rule") in collection_rules:
                identity["scope"] = "submitted_collection"
            elif str(row.get("rule", "")).startswith(("midi_", "c2pa_")):
                identity["scope"] = "unbound_document_collection"
            else:
                identity["unresolved_observation_index"] = index
        identity["rule"] = row.get("rule")
        groups[json.dumps(identity, sort_keys=True, separators=(",", ":"))].append((index, row))
    facts = []
    for key, observations in groups.items():
        assessed = [row for _, row in observations if isinstance(row.get("passed"), bool)]
        failed = [row for row in assessed if row["passed"] is False]
        passed = False if failed else True if assessed else None
        representative = failed[0] if failed else assessed[0] if assessed else observations[0][1]
        facts.append({"fact_id": hashlib.sha256(key.encode()).hexdigest(),
                      "identity": json.loads(key), "rule": representative.get("rule"),
                      "passed": passed, "status": ("contradicted" if failed else
                                                       "corroborated" if assessed else "unavailable"),
                      "observation_count": len(observations),
                      "observation_indices": [index for index, _ in observations],
                      "conflicting_outcomes": bool(failed) and any(row["passed"] is True for row in assessed),
                      "assessed_evidence_tiers": sorted({row.get("evidence_tier", "submitter_document") for row in assessed}),
                      "document_hashes": sorted({row["evidence_hash"] for _, row in observations if row.get("evidence_hash")}),
                      "representative_check": dict(representative)})
    return facts


def audit_cross_evidence(checks):
    """Group copies of the same observation, including contradictory copies.

    A content hash identifies a document, not an independent witness. These
    groups are diagnostic only: no check is removed from the scoring input.
    Unidentified observations are kept separate rather than guessed to refer
    to the same recording.
    """
    groups = defaultdict(list)
    for index, row in enumerate(checks):
        identity = {key: row[key] for key in ("audio_hash", "source_hash", "target_hash", "selection_id")
                    if row.get(key) is not None}
        if not any(key in identity for key in ("audio_hash", "source_hash", "target_hash")):
            identity["unresolved_observation_index"] = index
        identity["rule"] = row.get("rule")
        groups[json.dumps(identity, sort_keys=True)].append(row)
    out = []
    for key, rows in groups.items():
        variants = defaultdict(list)
        for row in rows:
            claim = {name: value for name, value in row.items()
                     if name not in {"evidence_hash", "matched_by", "file_name"}}
            variants[json.dumps(claim, sort_keys=True)].append(row)
        out.append({"identity": json.loads(key), "observation_count": len(rows),
                    "distinct_observation_count": len(variants),
                    "duplicate_observation_count": len(rows) - len(variants),
                    "conflicting_outcomes": any(row.get("passed") is True for row in rows) and
                                            any(row.get("passed") is False for row in rows),
                    "document_hashes": sorted({row["evidence_hash"] for row in rows if row.get("evidence_hash")}),
                    "scoring_effect": "none_audit_only",
                    "independent_source_count": None})
    return out
