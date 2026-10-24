"""Cross-evidence audit and conservative aggregation by assessed fact."""

from collections import defaultdict
import hashlib
import json
import re


#: Rules that compare the same claim about the same recording, made in
#: different documents. With one vote per claim they are one fact per recording.
SAME_CLAIM_RULES = {
    "sound_report_scene_take_matches_recorder": "scene_take",
    "aaf_scene_take_matches_recorder": "scene_take",
    "sound_report_track_names_match_recorder": "track_names",
    "aaf_track_names_match_recorder": "track_names",
}

#: Every file in a submission is uploaded by the submitter.
DELIVERED_BY = "submitter"


def aggregate_cross_evidence(checks, one_vote_per_claim=False):
    """One vote per rule/subject; any failed observation keeps the fact failed.

    Documentary hashes and declared values are not fact identities: changing a
    report's formatting, or submitting another answer, must not add support.
    Unknown subjects stay separate. Schema-conformance and workflow-level
    contributor checks are explicitly scoped to the submitted collection.
    With ``one_vote_per_claim``, the same claim about one recording repeated in
    several documents is one fact: their independence is unknown, so the
    repetition earns no extra support.
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
        claim = SAME_CLAIM_RULES.get(row.get("rule")) if one_vote_per_claim else None
        if claim and identity.get("audio_hash"):
            identity["claim"] = claim
        else:
            identity["rule"] = row.get("rule")
        groups[json.dumps(identity, sort_keys=True, separators=(",", ":"))].append((index, row))
    facts = []
    for key, observations in groups.items():
        assessed = [row for _, row in observations if isinstance(row.get("passed"), bool)]
        failed = [row for row in assessed if row["passed"] is False]
        passed = False if failed else True if assessed else None
        representative = failed[0] if failed else assessed[0] if assessed else observations[0][1]
        document_hashes = sorted({row["evidence_hash"] for _, row in observations if row.get("evidence_hash")})
        facts.append({"fact_id": hashlib.sha256(key.encode()).hexdigest(),
                      "identity": json.loads(key), "rule": representative.get("rule"),
                      "passed": passed, "status": ("contradicted" if failed else
                                                       "corroborated" if assessed else "unavailable"),
                      "observation_count": len(observations),
                      "observation_indices": [index for index, _ in observations],
                      "conflicting_outcomes": bool(failed) and any(row["passed"] is True for row in assessed),
                      "assessed_evidence_tiers": sorted({row.get("evidence_tier", "submitter_document") for row in assessed}),
                      "document_hashes": document_hashes,
                      "document_count": len(document_hashes),
                      "delivered_by": DELIVERED_BY,
                      "representative_check": dict(representative)})
    return facts


_RELATIONSHIP_PROPERTY_RULE = re.compile(r"(input|output)_(sample_rate_hz|bit_depth)_matches_file")
_FILE_PROPERTY_RULE = re.compile(r"technical\.(\w+)_matches_observed")


def _declaration_identity(index, row):
    """The measured file property a declaration check compares, if any.

    A file's own technical claim and a relationship's input/output claim about
    the same file compare against one measured value, so they are one fact.
    Other checks (time ranges) stay separate, one fact per check.
    """
    rule = str(row.get("rule", ""))
    match = _FILE_PROPERTY_RULE.fullmatch(rule)
    if match and row.get("evidence_hash"):
        return {"file_hash": row["evidence_hash"], "property": match.group(1)}
    match = _RELATIONSHIP_PROPERTY_RULE.fullmatch(rule)
    endpoint = row.get("source_hash" if match and match.group(1) == "input" else "target_hash")
    if match and endpoint:
        return {"file_hash": endpoint, "property": match.group(2)}
    return {"rule": rule, "observation_index": index}


def aggregate_declarations(checks):
    """One vote per declared file property; any failed claim keeps the fact failed."""
    groups = defaultdict(list)
    for index, row in enumerate(checks):
        key = json.dumps(_declaration_identity(index, row), sort_keys=True, separators=(",", ":"))
        groups[key].append((index, row))
    facts = []
    for key, observations in groups.items():
        assessed = [row for _, row in observations if isinstance(row.get("passed"), bool)]
        failed = [row for row in assessed if row["passed"] is False]
        representative = failed[0] if failed else assessed[0] if assessed else observations[0][1]
        facts.append({"fact_id": hashlib.sha256(key.encode()).hexdigest(),
                      "identity": json.loads(key), "rule": representative.get("rule"),
                      "passed": False if failed else True if assessed else None,
                      "status": ("contradicted" if failed else
                                 "corroborated" if assessed else "unavailable"),
                      "observation_count": len(observations),
                      "observation_indices": [index for index, _ in observations],
                      "conflicting_outcomes": bool(failed) and any(row["passed"] is True for row in assessed),
                      "representative_check": dict(representative)})
    return facts


_SUBJECT_KEYS = ("audio_hash", "midi_hash", "asset_hash", "source_hash", "target_hash", "ingredient_hash")


def _origin_fields(digest, parser_observations, wav_observations):
    """Creator/software fields the parsers already read; listed, never matched."""
    fields = {}
    parsed = parser_observations.get(digest) or {}
    if parsed.get("parser") == "daw-session":
        fields["creator"] = ((parsed.get("data") or {}).get("metadata") or {}).get("creator")
    embedded = (wav_observations.get(digest) or {}).get("embedded_metadata") or {}
    info = embedded.get("info") or {}
    fields.update({key: info.get(key) for key in ("software", "engineer", "technician")})
    fields["originator"] = (embedded.get("broadcast_extension") or {}).get("originator")
    return {key: value for key, value in fields.items() if value}


def summarize_evidence_sources(facts, parser_observations, wav_observations, signers):
    """Documents, facts and what is known about where the cross-evidence came from.

    Report only: it never changes a score. Every file was uploaded by the
    submitter; whether documents were created independently is not known, so
    independence stays unknown rather than guessed.
    """
    assessed = [fact for fact in facts if isinstance(fact.get("passed"), bool)]
    documents = sorted({digest for fact in assessed for digest in fact["document_hashes"]})
    subjects = defaultdict(lambda: {"documents": set(), "facts": 0})
    for fact in assessed:
        check = fact["representative_check"]
        subject = next((check[key] for key in _SUBJECT_KEYS if check.get(key)), None)
        if subject and fact["document_hashes"]:
            subjects[subject]["documents"].update(fact["document_hashes"])
            subjects[subject]["facts"] += 1
    origin = {digest: fields for digest in sorted(set(documents) | set(subjects))
              if (fields := _origin_fields(digest, parser_observations, wav_observations))}
    return [{
        "document_count": len(documents),
        "fact_count": len(assessed),
        "known_common_sources": ([{
            "source": DELIVERED_BY, "document_hashes": documents,
            "reason": "All files and documents in the submission were provided and uploaded by the same user.",
        }] if documents else []),
        "identified_signers": sorted(set(signers)),
        "independent_source_count": None,
        "independence": "unknown",
        "subjects_with_multiple_documents": [
            {"subject_hash": subject, "document_count": len(item["documents"]), "fact_count": item["facts"]}
            for subject, item in sorted(subjects.items()) if len(item["documents"]) > 1],
        "origin_fields": [{"hash": digest, **fields} for digest, fields in origin.items()],
        "scoring_effect": "none_report_only",
    }]


def _fact_subjects(fact):
    identity = fact.get("identity") or {}
    subjects = {identity[key] for key in _SUBJECT_KEYS if isinstance(identity.get(key), str)}
    for key in ("audio_hashes", "target_hashes"):
        if isinstance(identity.get(key), list):
            subjects.update(identity[key])
    return subjects


def summarize_support_breadth(native, facts, support_tiers, relevant_types, aaf_coverage=(),
                              bound_hashes=None, wav_observations=None):
    """How many of the workflow's required recordings were submitted and have qualifying evidence.

    Required recordings are the submitted artefacts of the workflow's recording
    types plus recorder takes an AAF edit uses that no submitted recording
    matches. An unmatched AAF take is ``not_submitted`` only when every
    submitted recording carries a recorder identity; otherwise it may be one of
    the unidentified files, so it is an ``unmatched_reference`` and the coverage
    is provisional. A take used several times counts once. Contradicted
    evidence was checked, so it is never counted as missing; a recording that
    was not submitted is missing material, not a contradiction.
    """
    qualifying = [fact for fact in facts if isinstance(fact.get("passed"), bool)
                  and set(fact.get("assessed_evidence_tiers") or ()) & set(support_tiers)]
    wav_observations = wav_observations or {}
    assets = []
    for node in native["artefacts"]:
        if node["artefact_type"] not in relevant_types:
            continue
        digest = node["artefact_hash"]
        outcomes = [fact["passed"] for fact in qualifying if digest in _fact_subjects(fact)]
        status = ("contradicted" if False in outcomes else
                  "supported" if outcomes else "no_qualifying_evidence")
        recorder = (wav_observations.get(digest) or {}).get("recorder_metadata") or {}
        assets.append({"hash": digest, "type": node["artefact_type"], "status": status,
                       "bound": bound_hashes is None or digest in bound_hashes,
                       "recorder_identity": bool(recorder.get("present") and recorder.get("file_name"))})
    unidentified = sum(not item["recorder_identity"] for item in assets)
    references, ambiguous = {}, set()
    if "audio/raw-take" in relevant_types:
        for record in aaf_coverage or ():
            for item in record.get("missing_recordings", []):
                key = (str(item.get("tape") or "").casefold(), str(item.get("file_name") or "").casefold())
                references.setdefault(key, {"tape": item.get("tape"), "file_name": item.get("file_name"),
                                            "type": "audio/raw-take", "source": "aaf_reference",
                                            "aaf_hashes": set()})["aaf_hashes"].add(record["evidence_hash"])
            ambiguous.update((item.get("tape"), item.get("file_name"))
                             for item in record.get("ambiguous_recordings", []))
    for item in references.values():
        item["status"] = "unmatched_reference" if unidentified else "not_submitted"
        item["aaf_hashes"] = sorted(item["aaf_hashes"])
    referenced = sorted(references.values(), key=lambda item: (str(item["tape"]), str(item["file_name"])))
    statuses = ("supported", "contradicted", "no_qualifying_evidence", "not_submitted", "unmatched_reference")
    counts = {status: sum(item["status"] == status for item in assets + referenced) for status in statuses}
    required = len(assets) + counts["not_submitted"]
    checked = counts["supported"] + counts["contradicted"]
    return {"applicable": bool(required), "status": "assessed" if required else "not_applicable",
            "relevant_asset_types": list(relevant_types),
            "relevant_asset_count": len(assets), "required_recording_count": required,
            "supported": counts["supported"], "contradicted": counts["contradicted"],
            "no_qualifying_evidence": counts["no_qualifying_evidence"],
            "not_submitted": counts["not_submitted"], "unmatched_references": counts["unmatched_reference"],
            "ambiguous_references": len(ambiguous),
            "coverage_scope": ("not_applicable" if not required else
                               "provisional" if counts["unmatched_reference"] else "confirmed"),
            # Two different questions over one denominator: was the material
            # submitted, and does it carry the qualifying evidence the policy names.
            "material_coverage": (sum(item["bound"] for item in assets) / required if required else None),
            "support_coverage": checked / required if required else None,
            "checked_share": checked / required if required else None,
            "assets": assets + referenced}


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
