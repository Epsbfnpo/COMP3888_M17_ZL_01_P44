"""Target-owned assessment passes kept separate from workflow completeness."""

from __future__ import annotations

import dataclasses
import math
import re
from pathlib import Path

from music_target.evidence_audit import (audit_cross_evidence, aggregate_cross_evidence, aggregate_declarations,
                                         summarize_evidence_sources, summarize_support_breadth)
from music_target.workflow_policy import DEFAULT_POLICY_PATH, load_workflow_policy, required_recording_types

from legacy_parsers.c2pa_parser import parse_c2pa_file, trust_configuration_record
from legacy_parsers.ern_parser import parse_ern_file
from legacy_parsers.rin_parser import parse_rin_file
from music_target.daw_session import DawSessionError, daw_session_checks, parse_ableton_session
from music_target.midi_parser import parse_midi_file
from music_target.production_documents import (aaf_checks, aaf_non_recorder_media, aaf_reference_coverage,
                                               parse_aaf_file, parse_sound_report_file, sound_report_checks)
from music_target.evidence_tiers import TIERS, assign_default_tiers, c2pa_signer_trusted, c2pa_tier, tier_counts
from music_target.score_explanations import contradiction_summary, describe_check, fact_counts
from music_target.forge_cost import describe as describe_strength, evidence_strength
from music_target.scoring_profile import load_default_scoring_profile
from music_target.sheet_parsers import parse_comp_sheet_file, parse_cue_sheet_file


def _axis(availability="unavailable", value=None, confidence=None, reasoning=None):
    if availability == "unavailable":
        value = confidence = None
    return {"availability": availability, "value": value,
            "confidence": confidence, "reasoning": reasoning}


def _finding(code, message, digest=None, severity="info", **extra):
    result = {"code": code, "severity": severity, "message": message, **extra}
    if digest:
        result["evidence_hash"] = digest
    return result


def parse_bound_artefacts(native, chain, wav_observations):
    """Run type-specific parsers and preserve their warnings as observations."""
    observations, findings = {}, []
    c2pa_attempted = False
    for node in native["artefacts"]:
        digest, kind = node["artefact_hash"], node["artefact_type"]
        artefact = chain.artefacts[digest]
        if not artefact.has_file():
            continue
        path = Path(artefact.get_file())
        try:
            if kind.startswith("audio/") or kind == "audio":
                wav_result = wav_observations.get(digest)
                wav_failed = ((wav_result or {}).get("status") == "parser_failed")
                parsed = {"parser": ((wav_result or {}).get("audio_format") or "wav").lower(),
                          "status": "failed" if wav_failed else "parsed",
                          "data": wav_result}
                # C2PA may be embedded in the media itself. Attempt once per
                # audio file; absence never makes completeness fail.
                c2pa = _reconcile_with_c2pa_chunk(dataclasses.asdict(parse_c2pa_file(str(path))), wav_result)
                parsed["c2pa"] = c2pa
                c2pa_attempted = True
            elif kind == "project/midi":
                result = parse_midi_file(path)
                parsed = {"parser": "midi", "status": "partial" if result["warnings"] else "parsed",
                          "data": result}
            elif kind == "text/comp-sheet":
                result = parse_comp_sheet_file(path)
                parsed = {"parser": "comp-sheet",
                          "status": "partial" if result["warnings"] else "parsed", "data": result}
            elif kind == "project/daw-session":
                try:
                    result = parse_ableton_session(path)
                    parsed = {"parser": "daw-session",
                              "status": "partial" if result["warnings"] else "parsed", "data": result}
                except DawSessionError as exc:
                    if not str(exc).startswith("Not an Ableton"):
                        raise
                    # Other DAW formats are not parsed yet; unchanged behaviour.
                    parsed = {"parser": None, "status": "not_assessed", "data": None}
            elif kind == "text/sound-report":
                result = parse_sound_report_file(path)
                parsed = {"parser": "sound-report",
                          "status": "partial" if result["warnings"] else "parsed", "data": result}
            elif kind == "project/aaf":
                result = parse_aaf_file(path)
                parsed = {"parser": "aaf",
                          "status": "partial" if result["warnings"] else "parsed", "data": result}
            elif kind == "text/cue-sheet":
                result = parse_cue_sheet_file(path)
                parsed = {"parser": "cue-sheet",
                          "status": "partial" if result["warnings"] else "parsed", "data": result}
            elif kind == "metadata/ddex-rin":
                result = dataclasses.asdict(parse_rin_file(str(path)))
                fatal = any(item.startswith(("malformed XML", "could not read file"))
                            for item in result["warnings"])
                parsed = {"parser": "rin", "status": "failed" if fatal else
                          ("parsed" if not result["warnings"] else "partial"),
                          "data": result}
            elif kind == "metadata/ddex-ern":
                result = dataclasses.asdict(parse_ern_file(str(path)))
                fatal = any(item.startswith(("malformed XML", "could not read file"))
                            for item in result["warnings"])
                parsed = {"parser": "ern", "status": "failed" if fatal else
                          ("parsed" if not result["warnings"] else "partial"),
                          "data": result}
            elif kind == "provenance/c2pa":
                result = dataclasses.asdict(parse_c2pa_file(str(path)))
                parsed = {"parser": "c2pa", "status": "parsed" if result["present"] else "not_assessed",
                          "data": result}
                c2pa_attempted = True
            else:
                parsed = {"parser": None, "status": "not_assessed", "data": None}
            observations[digest] = parsed
            findings.append(_finding("PARSER_OBSERVATION", f"{kind}: {parsed['status']}", digest,
                                     parser=parsed["parser"], parser_status=parsed["status"],
                                     parser_result=parsed["data"]))
        except (OSError, ValueError) as exc:
            observations[digest] = {"parser": kind, "status": "failed", "data": None}
            findings.append(_finding("PARSER_FAILED", f"{kind} parser failed: {exc}", digest, "medium"))
    if c2pa_attempted:
        unavailable = [item for item in observations.values()
                       if isinstance(item.get("c2pa"), dict)
                       and any("not installed" in warning for warning in item["c2pa"].get("warnings", []))]
        if unavailable:
            # A missing required dependency silently disables the check that a
            # signed AI-generation label agrees with the declared creation method.
            findings.append(_finding("C2PA_PARSER_UNAVAILABLE",
                                     "c2pa-python, a required dependency, is not installed; C2PA attestation "
                                     "and C2PA cross-evidence checks were not assessed. Install it with "
                                     "pip install -r requirements.txt.", severity="medium"))
    return observations, findings


def _is_declared_property_check(item):
    return item["rule"].startswith("technical.")


def _declared_file_property_results(native, wav_observations):
    """Submitted technical claims (sample rate, channels, ...) against measured audio properties.

    These compare a submitter's claim with the file, so they belong to
    declaration consistency alongside relationship claims, not to file integrity.
    WAV, FLAC and MP3 observations share the ``technical.*`` rule names.
    """
    checks = [dict(item) for node in native["artefacts"]
              for item in (wav_observations.get(node["artefact_hash"]) or {}).get("checks", [])
              if _is_declared_property_check(item)]
    findings = [_finding("TECHNICAL_CLAIM_MISMATCH", item["rule"], item["evidence_hash"], "medium")
                for item in checks if item.get("passed") is False]
    return checks, findings


def _observed_duration(wav_observations, digest):
    """Measured duration of a bound audio file, or None (not bound, unparsed, or unknown for the format)."""
    if not digest:
        return None
    return ((wav_observations.get(digest) or {}).get("observed") or {}).get("duration_seconds")


class BoundFileIntegrityPass:
    """Aggregate file binding, non-claim audio checks (such as the FLAC MD5), and parser readability."""

    @staticmethod
    def evaluate(native, bound_hashes, missing_hashes, wav_observations,
                 parser_observations):
        checks, findings = [], []
        for node in native["artefacts"]:
            digest = node["artefact_hash"]
            if digest in missing_hashes:
                checks.append({
                    "rule": "object_hash_bound",
                    "passed": None,
                    "status": "not_assessed",
                    "reason_code": "MISSING_OBJECT",
                    "evidence_hash": digest,
                })
            else:
                checks.append({"rule": "object_hash_bound",
                               "passed": digest in bound_hashes,
                               "evidence_hash": digest})
            wav_result = wav_observations.get(digest)
            if wav_result:
                checks.extend(dict(item) for item in wav_result.get("checks", [])
                              if not _is_declared_property_check(item))
            parser = parser_observations.get(digest)
            if (parser and parser["status"] == "failed" and
                    not (wav_result and wav_result.get("status") == "parser_failed")):
                checks.append({"rule": "declared_type_parser_succeeds", "passed": False,
                               "evidence_hash": digest})
        eligible = [item for item in checks if isinstance(item.get("passed"), bool)]
        passed = sum(item["passed"] for item in eligible)
        for item in checks:
            if item.get("passed") is False:
                findings.append(_finding("FILE_INTEGRITY_CONTRADICTION", item["rule"],
                                         item["evidence_hash"], "medium"))
        value = passed / len(eligible) if eligible else None
        return value, checks, findings


class StructuralValidityPass:
    """Expose graph checks that already succeeded before assessment; never score them as truth."""

    @staticmethod
    def evaluate(native):
        checks = [
            {"rule": "graph_loaded_without_cycle", "passed": True},
            {"rule": "all_nodes_connected_to_final", "passed": True},
            {"rule": "declared_hash_references_resolve", "passed": True},
        ]
        for target in native["artefacts"]:
            for edge in target.get("evidence", []):
                checks.append({
                    "rule": "relationship_endpoint_roles_registered",
                    "passed": True,
                    "source_hash": edge["hash"],
                    "target_hash": target["artefact_hash"],
                    "relationship_type": edge["relationship_type"],
                })
        return checks


def _relationship_integrity_results(relationship_observations):
    """Aggregate checks already produced by RelationshipIntegrityPass."""
    checks = [dict(check) for result in relationship_observations
              for check in result.get("checks", [])]
    findings = [
        _finding("RELATIONSHIP_INTEGRITY_CONTRADICTION",
                 f"{item['relationship_type']}: {item['rule']}",
                 item["target_hash"], "medium")
        for item in checks if not item["passed"]
    ]
    value = sum(item["passed"] for item in checks) / len(checks) if checks else None
    return value, checks, findings


class CrossEvidencePass:
    """Cross-check independent fields only when both sides are available."""

    @staticmethod
    def evaluate(native, workflow_input, parser_observations, wav_observations):
        submitted = {item.casefold() for item in (workflow_input or {}).get("declarations", {}).get("contributors", [])}
        graph_hashes = {item["artefact_hash"] for item in native["artefacts"]}
        comp_edges = {(edge["hash"], target["artefact_hash"])
                      for target in native["artefacts"]
                      for edge in target.get("evidence", [])
                      if edge["relationship_type"] == "comped_from"}
        checks = []
        for parsed in parser_observations.values():
            if not parsed.get("data"):
                continue
            if parsed.get("parser") == "rin":
                rin_names = {item["name"].casefold() for item in parsed["data"].get("contributors", []) if item.get("name")}
                if submitted and rin_names:
                    checks.append({"rule": "rin_contributors_overlap_submitter_declaration",
                                   "passed": bool(submitted & rin_names),
                                   "submitted": sorted(submitted), "observed": sorted(rin_names)})
            elif parsed.get("parser") == "comp-sheet":
                default_target = parsed["data"].get("metadata", {}).get("target_hash")
                for selection in parsed["data"].get("selections", []):
                    source = selection.get("source_hash")
                    target = selection.get("target_hash") or default_target
                    if source:
                        checks.append({"rule": "comp_sheet_source_exists_in_graph",
                                       "passed": source in graph_hashes,
                                       "selection_id": selection["selection_id"],
                                       "source_hash": source})
                    if source and target:
                        checks.append({"rule": "comp_sheet_selection_matches_comped_from_edge",
                                       "passed": (source, target) in comp_edges,
                                       "selection_id": selection["selection_id"],
                                       "source_hash": source, "target_hash": target})
                    duration = _observed_duration(wav_observations, target)
                    if duration is not None and selection.get("target_end_seconds") is not None:
                        checks.append({"rule": "comp_sheet_target_range_within_audio",
                                       "passed": selection["target_end_seconds"] <= duration,
                                       "selection_id": selection["selection_id"],
                                       "target_hash": target})
            elif parsed.get("parser") == "cue-sheet":
                for cue in parsed["data"].get("cues", []):
                    asset = cue.get("asset_hash")
                    if asset:
                        checks.append({"rule": "cue_sheet_asset_exists_in_graph",
                                       "passed": asset in graph_hashes,
                                       "cue_id": cue["cue_id"], "asset_hash": asset})
                    duration = _observed_duration(wav_observations, asset)
                    if duration is not None:
                        checks.append({"rule": "cue_sheet_range_within_audio",
                                       "passed": cue["end_seconds"] <= duration,
                                       "cue_id": cue["cue_id"], "asset_hash": asset})
        value = sum(item["passed"] for item in checks) / len(checks) if checks else None
        findings = [] if value in (None, 1.0) else [
            _finding("CROSS_EVIDENCE_CONTRADICTION",
                     "One or more RIN, CompSheet, or CueSheet claims conflict with the submitted graph or media.",
                     severity="medium")]
        return value, checks, findings


def _unavailable_check(rule, reason, **extra):
    """Represent one failed comparison without failing unrelated checks."""
    return {"rule": rule, "status": "unavailable", "passed": None,
            "reason": reason, **extra}


def _reconcile_with_c2pa_chunk(c2pa, wav_result):
    """Use the container parser as ground truth for whether a manifest exists.

    WAV keeps a manifest in a ``C2PA`` chunk; FLAC and MP3 in an ID3v2 ``GEOB``
    frame of type application/c2pa. A reader failure means a damaged manifest
    only when the file really carries one; otherwise the SDK failed for another
    reason and there is simply no manifest. A manifest location the SDK finds no
    manifest in is unreadable, not absent.
    """
    metadata = (wav_result or {}).get("embedded_metadata")
    if not c2pa.get("sdk_available") or not isinstance(metadata, dict):
        return c2pa  # container unknown (parser failed): keep the SDK result
    if "c2pa_container_present" in metadata:
        has_chunk = bool(metadata["c2pa_container_present"])
    else:
        has_chunk = any(item.get("chunk_id") == "C2PA" for item in metadata.get("other_chunks") or [])
    c2pa["c2pa_chunk_present"] = has_chunk
    warnings = list(c2pa.get("warnings") or [])
    if not c2pa.get("reader_succeeded") and not has_chunk:
        c2pa.update(reader_succeeded=True, present=False)
        warnings.append("The C2PA SDK could not read this file, but it has no C2PA manifest location; "
                        "treated as having no manifest." if "c2pa_container_present" in metadata else
                        "The C2PA SDK could not read this file, but it has no C2PA chunk; "
                        "treated as having no manifest.")
    elif c2pa.get("reader_succeeded") and not c2pa.get("present") and has_chunk:
        c2pa["reader_succeeded"] = False
        warnings.append("The file has a C2PA manifest location, but the SDK found no readable manifest in it."
                        if "c2pa_container_present" in metadata else
                        "The file has a C2PA chunk, but the SDK found no readable manifest in it.")
    c2pa["warnings"] = warnings
    return c2pa


# Plain-language meaning of SDK failure codes, matched by prefix.
_C2PA_CODE_EXPLANATIONS = (
    ("assertion.dataHash.mismatch", "the audio no longer matches the content hash recorded when it was signed "
                                    "(the file was edited after signing, or the manifest was copied from another file)"),
    ("assertion.bmffHash.mismatch", "the media no longer matches the content hash recorded when it was signed"),
    ("assertion.boxesHash.mismatch", "the media no longer matches the content hash recorded when it was signed"),
    ("assertion.collectionHash.mismatch", "a file in the signed collection no longer matches its recorded hash"),
    ("assertion.hashedURI.mismatch", "an assertion inside the manifest was changed after signing"),
    ("assertion.", "an assertion in the manifest failed validation"),
    ("claimSignature.mismatch", "the claim signature does not verify, so the manifest was altered after signing"),
    ("claimSignature.outsideValidity", "the manifest was signed outside the certificate's validity period"),
    ("claimSignature.", "the claim signature failed validation"),
    ("signingCredential.revoked", "the signing certificate was revoked"),
    ("signingCredential.expired", "the signing certificate had expired"),
    ("signingCredential.", "the signing certificate is not valid for signing"),
    ("timeStamp.", "the signing time stamp failed validation"),
)


def _c2pa_code_explanation(code):
    for prefix, text in _C2PA_CODE_EXPLANATIONS:
        if code.startswith(prefix):
            return f"{text} ({code})"
    return code


# SDK codes that report signer trust, not tampering; trust is assessed separately.
_C2PA_TRUST_CODES = {"signingCredential.trusted", "signingCredential.untrusted"}
_C2PA_CONTENT_BINDING_ASSERTIONS = ("assertion.dataHash.", "assertion.bmffHash.",
                                    "assertion.boxesHash.", "assertion.collectionHash.")
_C2PA_FAILURE_MESSAGES = {
    "C2PA_CONTENT_BINDING_FAILED": (
        "The C2PA manifest's content hash does not match this file: the audio changed after "
        "signing, or the manifest was copied from another file. Its claims cannot be relied on."),
    "C2PA_SIGNATURE_INVALID": (
        "The C2PA claim signature or signing credential failed validation; the manifest may have "
        "been altered after signing. Its claims cannot be relied on."),
    "C2PA_VALIDATION_FAILED": "The C2PA manifest failed SDK validation; its claims cannot be relied on.",
}


def _c2pa_reduces_human_share(manifest):
    """An AI-generation source type, or any manifest signed by a known AI generation signer."""
    source_types = [str(item).casefold() for item in manifest.get("digital_source_types") or []]
    return bool(manifest.get("known_ai_signer")) or any("trainedalgorithmic" in item for item in source_types)


def _c2pa_signer_label(manifest):
    signature = manifest.get("signature_info") or {}
    parts = [signature.get("issuer"), signature.get("common_name")]
    return ", ".join(str(part) for part in parts if part) or "unknown signer"


def _c2pa_is_invalid(manifest):
    return bool(manifest.get("present")) and str(manifest.get("validation_state") or "").casefold() == "invalid"


def _c2pa_failure_codes(manifest):
    """SDK failure codes for one manifest, excluding signer-trust status."""
    codes = []

    def walk(value, in_failure):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "code" and in_failure and isinstance(item, str):
                    codes.append(item)
                else:
                    walk(item, in_failure or key == "failure")
        elif isinstance(value, list):
            for item in value:
                walk(item, in_failure)

    walk(manifest.get("validation_results"), False)
    return sorted(set(codes) - _C2PA_TRUST_CODES)


def _c2pa_failure_finding_code(failures):
    if any(code.startswith(_C2PA_CONTENT_BINDING_ASSERTIONS) for code in failures):
        return "C2PA_CONTENT_BINDING_FAILED"
    if any(code.startswith(("claimSignature.", "signingCredential.")) for code in failures):
        return "C2PA_SIGNATURE_INVALID"
    return "C2PA_VALIDATION_FAILED"


def _c2pa_records(native, parser_observations):
    """Normalise embedded and standalone C2PA results with an asset binding."""
    edges = [(edge["hash"], target["artefact_hash"], edge["relationship_type"])
             for target in native["artefacts"] for edge in target.get("evidence", [])]
    final_hash = native.get("final_artefact_hash")
    records = []
    for container_hash, parsed in parser_observations.items():
        embedded = parsed.get("c2pa")
        if isinstance(embedded, dict):
            records.append({
                "manifest_container_hash": container_hash,
                "asset_hash": container_hash,
                "source_kind": "embedded",
                "binding_relationship": None,
                "manifest": embedded,
            })

        if parsed.get("parser") != "c2pa" or not isinstance(parsed.get("data"), dict):
            continue
        manifest = parsed["data"]
        outgoing = [(target, relation) for source, target, relation in edges
                    if source == container_hash and target != container_hash]
        incoming = [(source, relation) for source, target, relation in edges
                    if target == container_hash and source != container_hash]
        bindings = outgoing or incoming
        if not bindings and container_hash == final_hash:
            bindings = [(container_hash, None)]
        if not bindings:
            bindings = [(None, None)]
        seen = set()
        for asset_hash, relationship in bindings:
            key = (asset_hash, relationship)
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "manifest_container_hash": container_hash,
                "asset_hash": asset_hash,
                "source_kind": "standalone",
                "binding_relationship": relationship,
                "manifest": manifest,
            })
    return records


class ExpandedCrossEvidencePass:
    """Cross-check DDEX, MIDI, C2PA, AI and sheet claims against the graph."""

    @staticmethod
    def evaluate(native, workflow_input, parser_observations, wav_observations):
        submitted = {item.casefold() for item in
                     (workflow_input or {}).get("declarations", {}).get("contributors", [])}
        nodes = {item["artefact_hash"]: item for item in native["artefacts"]}
        graph_hashes = set(nodes)
        edges = [(edge["hash"], target["artefact_hash"], edge["relationship_type"])
                 for target in native["artefacts"] for edge in target.get("evidence", [])]
        comp_edges = {(source, target) for source, target, relation in edges
                      if relation == "comped_from"}
        checks = []
        for owner_digest, parsed in parser_observations.items():
            first_check = len(checks)
            data = parsed.get("data")
            parser = parsed.get("parser")
            if parser == "rin" and data:
                names = {item["name"].casefold() for item in data.get("contributors", [])
                         if item.get("name")}
                if submitted and names:
                    checks.append({"rule": "rin_contributors_overlap_submitter_declaration",
                                   "passed": bool(submitted & names),
                                   "submitted": sorted(submitted), "observed": sorted(names)})
                validation = data.get("xsd_validation") or {}
                if validation.get("status") in {"valid", "invalid"}:
                    checks.append({"rule": "rin_xsd_conforms",
                                   "passed": validation["status"] == "valid",
                                   "evidence_hash": owner_digest,
                                   "xsd_status": validation["status"]})
                for binding in data.get("audio_bindings", []):
                    audio_hash = binding.get("sha256")
                    if audio_hash:
                        exists = audio_hash in graph_hashes
                        checks.append({"rule": "rin_bound_audio_hash_exists_in_graph",
                                       "passed": exists, "evidence_hash": owner_digest,
                                       "audio_hash": audio_hash,
                                       "file_reference": binding.get("file_reference")})
                        if exists:
                            metadata = (nodes[audio_hash].get("attributes") or {}).get(
                                "source_metadata") or {}
                            if metadata.get("rin_file_reference"):
                                checks.append({"rule": "rin_file_reference_matches_audio_declaration",
                                               "passed": metadata["rin_file_reference"] ==
                                                         binding.get("file_reference"),
                                               "audio_hash": audio_hash})
            elif parser == "ern" and data:
                validation = data.get("xsd_validation") or {}
                if validation.get("status") in {"valid", "invalid"}:
                    checks.append({"rule": "ern_xsd_conforms",
                                   "passed": validation["status"] == "valid",
                                   "evidence_hash": owner_digest,
                                   "xsd_status": validation["status"]})
                names = {item["name"].casefold() for item in data.get("parties", [])
                         if item.get("name")}
                if submitted and names:
                    checks.append({"rule": "ern_parties_overlap_submitter_declaration",
                                   "passed": bool(submitted & names),
                                   "submitted": sorted(submitted), "observed": sorted(names)})
                for binding in data.get("audio_bindings", []):
                    audio_hash = binding.get("sha256")
                    if not audio_hash:
                        continue
                    exists = audio_hash in graph_hashes
                    checks.append({"rule": "ern_delivery_audio_hash_exists_in_graph",
                                   "passed": exists, "evidence_hash": owner_digest,
                                   "audio_hash": audio_hash,
                                   "resource_reference": binding.get("resource_reference")})
                    if exists:
                        metadata = (nodes[audio_hash].get("attributes") or {}).get(
                            "source_metadata") or {}
                        if metadata.get("isrc") and binding.get("isrc"):
                            declared_isrc, observed_isrc = metadata["isrc"], binding["isrc"]
                            if not isinstance(declared_isrc, str) or not isinstance(observed_isrc, str):
                                checks.append(_unavailable_check(
                                    "ern_isrc_matches_audio_declaration",
                                    "ISRC comparison requires string values.",
                                    audio_hash=audio_hash,
                                    evidence_hash=owner_digest))
                            else:
                                checks.append({
                                    "rule": "ern_isrc_matches_audio_declaration",
                                    "passed": declared_isrc.replace("-", "").casefold() ==
                                              observed_isrc.replace("-", "").casefold(),
                                    "audio_hash": audio_hash,
                                })
            elif parser == "midi" and data:
                targets = [target for source, target, relation in edges
                           if source == owner_digest and relation == "rendered_from"]
                checks.append({"rule": "midi_render_relationship_declared",
                               "passed": bool(targets), "evidence_hash": owner_digest,
                               "target_hashes": targets})
                if targets:
                    checks.append({"rule": "midi_used_for_render_contains_note_events",
                                   "passed": data.get("note_on_count", 0) > 0,
                                   "evidence_hash": owner_digest,
                                   "note_on_count": data.get("note_on_count", 0)})
                for target in targets:
                    midi_duration = data.get("duration_seconds")
                    audio_duration = (wav_observations.get(target) or {}).get(
                        "observed", {}).get("duration_seconds")
                    if midi_duration is not None and audio_duration is not None:
                        checks.append({"rule": "midi_duration_fits_rendered_audio",
                                       "passed": midi_duration <= audio_duration + 1.0,
                                       "evidence_hash": owner_digest, "target_hash": target})
                production = (nodes[owner_digest].get("attributes") or {}).get("production") or {}
                tempos = data.get("tempo_events") or []
                if production.get("tempo_bpm") is not None and tempos:
                    declared_tempo = production["tempo_bpm"]
                    observed_tempo = tempos[0].get("bpm")
                    numeric = (type(declared_tempo) in (int, float) and
                               type(observed_tempo) in (int, float) and
                               math.isfinite(declared_tempo) and math.isfinite(observed_tempo))
                    if not numeric:
                        checks.append(_unavailable_check(
                            "midi_tempo_matches_declaration",
                            "Tempo comparison requires finite numeric BPM values.",
                            evidence_hash=owner_digest))
                    else:
                        checks.append({
                            "rule": "midi_tempo_matches_declaration",
                            "passed": math.isclose(declared_tempo, observed_tempo,
                                                   abs_tol=0.01),
                            "evidence_hash": owner_digest,
                        })
                signatures = data.get("time_signature_events") or []
                if production.get("time_signature") and signatures:
                    observed = f"{signatures[0]['numerator']}/{signatures[0]['denominator']}"
                    checks.append({"rule": "midi_time_signature_matches_declaration",
                                   "passed": str(production["time_signature"]) == observed,
                                   "evidence_hash": owner_digest, "observed": observed})
            elif parser == "comp-sheet" and data:
                target_default = data.get("metadata", {}).get("target_hash")
                for selection in data.get("selections", []):
                    source = selection.get("source_hash")
                    target = selection.get("target_hash") or target_default
                    if source:
                        checks.append({"rule": "comp_sheet_source_exists_in_graph",
                                       "passed": source in graph_hashes,
                                       "selection_id": selection["selection_id"],
                                       "source_hash": source})
                    if source and target:
                        checks.append({"rule": "comp_sheet_selection_matches_comped_from_edge",
                                       "passed": (source, target) in comp_edges,
                                       "selection_id": selection["selection_id"],
                                       "source_hash": source, "target_hash": target})
                    duration = _observed_duration(wav_observations, target)
                    if duration is not None and selection.get("target_end_seconds") is not None:
                        checks.append({"rule": "comp_sheet_target_range_within_audio",
                                       "passed": selection["target_end_seconds"] <= duration,
                                       "selection_id": selection["selection_id"],
                                       "target_hash": target})
            elif parser == "cue-sheet" and data:
                for cue in data.get("cues", []):
                    asset = cue.get("asset_hash")
                    if asset:
                        checks.append({"rule": "cue_sheet_asset_exists_in_graph",
                                       "passed": asset in graph_hashes,
                                       "cue_id": cue["cue_id"], "asset_hash": asset})
                    duration = _observed_duration(wav_observations, asset)
                    if duration is not None:
                        checks.append({"rule": "cue_sheet_range_within_audio",
                                       "passed": cue["end_seconds"] <= duration,
                                       "cue_id": cue["cue_id"], "asset_hash": asset})
            elif parser == "daw-session" and data:
                checks.extend(daw_session_checks(data, owner_digest, native, wav_observations, parser_observations))
            elif parser == "sound-report" and data:
                checks.extend(sound_report_checks(data, owner_digest, wav_observations))
            elif parser == "aaf" and data:
                checks.extend(aaf_checks(data, owner_digest, wav_observations))

            for check in checks[first_check:]:
                check.setdefault("evidence_hash", owner_digest)
                if parser == "midi" and targets:
                    check.setdefault("target_hashes", sorted(targets))

        compatible = {
            "parentof": {"derived_from", "edited_from", "mastered_from",
                         "rendered_from", "stemmed_from", "mixed_from", "comped_from"},
            "componentof": {"mixed_from", "comped_from", "stemmed_from", "input_to"},
            "inputto": {"input_to", "rendered_from"},
        }
        for record in _c2pa_records(native, parser_observations):
            c2pa = record["manifest"]
            if not c2pa.get("present"):
                continue
            container_hash = record["manifest_container_hash"]
            asset_hash = record["asset_hash"]
            common = {
                "evidence_hash": container_hash,
                "manifest_container_hash": container_hash,
                "asset_hash": asset_hash,
                "c2pa_source_kind": record["source_kind"],
                # Only a manifest whose signature validated is independent of
                # the submitter; an unverified manifest is another declaration.
                "evidence_tier": c2pa_tier(c2pa),
            }
            if asset_hash == native.get("final_artefact_hash") and _c2pa_is_invalid(c2pa):
                # The submitted final file is not the content the manifest signed,
                # including a file edited after signing that kept the old manifest.
                # This is an ordinary contradiction, not a severe-cap rule.
                checks.append({
                    "rule": "final_c2pa_manifest_validates",
                    "passed": False,
                    "validation_codes": _c2pa_failure_codes(c2pa),
                    **common,
                })
            if record["source_kind"] == "standalone":
                checks.append({
                    "rule": "c2pa_standalone_manifest_bound_to_graph_asset",
                    "passed": asset_hash in graph_hashes,
                    "binding_relationship": record["binding_relationship"],
                    **common,
                })
            for ingredient in c2pa.get("ingredients", []):
                relationship = str(ingredient.get("relationship") or "").replace(
                    "_", "").casefold()
                for source_hash in ingredient.get("referenced_hashes", []):
                    exists = source_hash in graph_hashes
                    checks.append({
                        "rule": "c2pa_ingredient_hash_exists_in_graph",
                        "passed": exists,
                        "ingredient_hash": source_hash,
                        **common,
                    })
                    if exists and relationship in compatible:
                        if asset_hash not in graph_hashes:
                            checks.append(_unavailable_check(
                                "c2pa_ingredient_relationship_matches_graph",
                                "The C2PA manifest is not bound to a submitted asset.",
                                ingredient_hash=source_hash,
                                c2pa_relationship=ingredient.get("relationship"),
                                **common))
                        else:
                            relations = {relation for source, target, relation in edges
                                         if source == source_hash and target == asset_hash}
                            checks.append({
                                "rule": "c2pa_ingredient_relationship_matches_graph",
                                "passed": bool(relations & compatible[relationship]),
                                "ingredient_hash": source_hash,
                                "c2pa_relationship": ingredient.get("relationship"),
                                "graph_relationships": sorted(relations),
                                **common,
                            })
            source_types = [str(item) for item in c2pa.get("digital_source_types", [])]
            known_signer = c2pa.get("known_ai_signer")
            ai_claimed = _c2pa_reduces_human_share(c2pa)
            if asset_hash in nodes:
                creation = (nodes[asset_hash].get("attributes") or {}).get("creation_method")
                methods = {creation} if isinstance(creation, str) else set(creation or [])
                if (source_types or known_signer) and methods:
                    checks.append({
                        "rule": "c2pa_ai_source_type_matches_creation_method",
                        "passed": ("ai-generated" in methods) if ai_claimed else True,
                        "digital_source_types": source_types,
                        "creation_method": sorted(methods),
                        **common,
                        # An AI label is believed from any valid signature or a known AI
                        # signer; a label supporting human authorship needs a trusted one.
                        "evidence_tier": c2pa_tier(c2pa, reduces_human_share=ai_claimed),
                        **({"known_ai_signer": known_signer.get("name")} if known_signer else {}),
                    })

        if "includes_ai_generated_audio" in set((workflow_input or {}).get("modifiers") or []):
            declared_ai = []
            for digest, node in nodes.items():
                creation = (node.get("attributes") or {}).get("creation_method")
                methods = {creation} if isinstance(creation, str) else set(creation or [])
                if "ai-generated" in methods:
                    declared_ai.append(digest)
            checks.append({"rule": "workflow_ai_modifier_matches_audio_declarations",
                           "passed": bool(declared_ai), "audio_hashes": declared_ai})
        assign_default_tiers(checks)
        facts = aggregate_cross_evidence(checks)
        eligible = [item for item in facts if isinstance(item.get("passed"), bool)]
        value = (sum(item["passed"] for item in eligible) / len(eligible)
                 if eligible else None)
        findings = [] if value in (None, 1.0) else [
            _finding("CROSS_EVIDENCE_CONTRADICTION",
                     "One or more parsed evidence claims conflict with the submitted graph, declarations, or media.",
                     severity="medium")]
        unavailable = [item for item in checks if item.get("status") == "unavailable"]
        if unavailable:
            findings.append(_finding(
                "CROSS_EVIDENCE_CHECK_UNAVAILABLE",
                "One or more cross-evidence comparisons could not run because a field had an unsupported type or required binding was absent.",
                severity="low", unavailable_checks=unavailable))
        return value, checks, findings


class C2PAAttestationPass:
    """Assess only the final artefact while reporting every C2PA record."""

    @staticmethod
    def _collect_codes(value):
        codes = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "code" and isinstance(item, str):
                    codes.append(item)
                else:
                    codes.extend(C2PAAttestationPass._collect_codes(item))
        elif isinstance(value, list):
            for item in value:
                codes.extend(C2PAAttestationPass._collect_codes(item))
        return codes

    @staticmethod
    def _report(record, final_hash):
        manifest = record["manifest"]
        present = bool(manifest.get("present"))
        state = manifest.get("validation_state")
        if not manifest.get("sdk_available"):
            reported_state = "sdk_unavailable"
        elif not manifest.get("reader_succeeded"):
            reported_state = "manifest_not_read"
        elif not present:
            reported_state = "manifest_absent"
        else:
            reported_state = state or "manifest_parsed"
        return {
            "scope": "final" if record["asset_hash"] == final_hash else "supporting",
            "source_kind": record["source_kind"],
            "manifest_container_hash": record["manifest_container_hash"],
            "asset_hash": record["asset_hash"],
            "binding_relationship": record["binding_relationship"],
            "sdk_available": bool(manifest.get("sdk_available")),
            "reader_succeeded": bool(manifest.get("reader_succeeded")),
            "manifest_present": present,
            "signature_present": bool(manifest.get("signature_info")) if present else False,
            "validation_state": reported_state,
            "trust_status": manifest.get("trust_status") if present else None,
            "revocation_status": manifest.get("revocation_status") if present else None,
            "signer": _c2pa_signer_label(manifest) if present else None,
            "known_ai_signer": (manifest.get("known_ai_signer") or {}).get("name"),
            "validation_codes": sorted(set(
                C2PAAttestationPass._collect_codes(
                    manifest.get("validation_results")))),
        }

    @staticmethod
    def _failure_findings(records, final_hash):
        """A failed manifest must be visible, not only a 0.0 that looks like absence."""
        findings = []
        for record in records:
            manifest = record["manifest"]
            if not _c2pa_is_invalid(manifest):
                continue
            failures = _c2pa_failure_codes(manifest)
            code = _c2pa_failure_finding_code(failures)
            final = record["asset_hash"] == final_hash
            message = _C2PA_FAILURE_MESSAGES[code]
            if failures:
                message += " Validation found that " + "; ".join(_c2pa_code_explanation(item) for item in failures) + "."
            if not final:
                message = "Supporting artefact: " + message
            findings.append(_finding(code, message, record["asset_hash"], "medium" if final else "low",
                                     scope="final" if final else "supporting",
                                     source_kind=record["source_kind"], validation_codes=failures))
        return findings

    @staticmethod
    def _signer_findings(native, records, final_hash):
        """Report who signed; an AI label is accepted, a human-authorship signature needs trust."""
        nodes = {node["artefact_hash"]: node for node in native.get("artefacts", [])}
        findings = []
        for record in records:
            manifest = record["manifest"]
            if not manifest.get("present"):
                continue
            final = record["asset_hash"] == final_hash
            scope = "final" if final else "supporting"
            signer = _c2pa_signer_label(manifest)
            known = manifest.get("known_ai_signer")
            state = str(manifest.get("validation_state") or "").casefold()
            if known:
                creation = ((nodes.get(record["asset_hash"]) or {}).get("attributes") or {}).get("creation_method")
                methods = {creation} if isinstance(creation, str) else set(creation or [])
                undeclared = bool(methods) and "ai-generated" not in methods
                message = (f"The C2PA manifest is signed by a known AI generation signer ({known['name']}). "
                           "Evidence that lowers the human share of a work is accepted even though this signer "
                           "is not on a C2PA trust list.")
                if undeclared:
                    message += " The submitter did not declare this artefact as ai-generated."
                findings.append(_finding("C2PA_KNOWN_AI_SIGNER", message, record["asset_hash"],
                                         "medium" if undeclared else "info", scope=scope,
                                         known_ai_signer=known["name"], signer=signer))
            elif (state in ("valid", "trusted") and not c2pa_signer_trusted(manifest)
                  and not _c2pa_reduces_human_share(manifest)):
                message = (f"The C2PA signature is valid but its signer ({signer}) is not on the pinned C2PA "
                           "Trust List or, for content signed before its freeze, the legacy Interim Trust List. "
                           "A signature identifies who signed, not that the claims are true; this manifest is "
                           "treated as a submitter-supplied document and cannot qualify Integrity for release.")
                if manifest.get("trust_note"):
                    message += " " + manifest["trust_note"]
                findings.append(_finding("C2PA_SIGNER_UNTRUSTED", message, record["asset_hash"],
                                         "medium" if final else "low", scope=scope, signer=signer))
        return findings

    @staticmethod
    def evaluate(native, parser_observations):
        axis, validation, findings, reports = C2PAAttestationPass._evaluate(native, parser_observations)
        findings.append(C2PAAttestationPass._final_state_finding(native, parser_observations, axis, validation))
        return axis, validation, findings, reports

    @staticmethod
    def _final_state_finding(native, parser_observations, axis, validation):
        """State, meaning and score effects of the final artefact's C2PA, for every state."""
        final_hash = native["final_artefact_hash"]
        manifests = [record["manifest"] for record in _c2pa_records(native, parser_observations)
                     if record["asset_hash"] == final_hash]
        present = [item for item in manifests if item.get("present")]
        known = next((item["known_ai_signer"]["name"] for item in present if item.get("known_ai_signer")), None)
        state = validation.get("validation_state")
        trust = validation.get("trust_status")
        signer = validation.get("signer") or "unknown signer"
        value = axis.get("value")
        boundary = ("A signature identifies who signed the manifest; it does not show that its claims are true. "
                    "Revocation was not checked (offline validation).")
        if not validation:
            key, severity = "not_checked", "medium"
            message = ("The final artefact's C2PA was not checked because the required c2pa-python SDK is not "
                       "installed. Attestation strength is unavailable, and no C2PA claim was compared with the "
                       "declarations.")
        elif state == "manifest_absent":
            key, severity = "absent", "info"
            message = ("The final artefact has no C2PA manifest. Many tools do not write C2PA yet, so this is not "
                       "evidence against the submission. Attestation strength is 0.0 because there is nothing to "
                       "verify; Integrity is not affected.")
        elif state == "manifest_not_read":
            key, severity = "unreadable", "medium"
            reasons = "; ".join(warning for item in manifests for warning in item.get("warnings") or []) or "no reason reported"
            message = ("The final artefact carries a C2PA chunk, but the SDK could not read a manifest from it "
                       f"({reasons}). Its claims are unknown, so they cannot be compared with the declarations. "
                       "Attestation strength is not assessed, and Integrity is withheld "
                       "(final_attestation_unreadable) unless a severe contradiction caps it.")
        elif trust == "invalid":
            key, severity = "invalid", "medium"
            failures = sorted({code for item in present if _c2pa_is_invalid(item) for code in _c2pa_failure_codes(item)})
            meaning = "; ".join(_c2pa_code_explanation(code) for code in failures) or "the SDK reported no failure code"
            message = (f"The final artefact's C2PA manifest, signed by {signer}, is INVALID: {meaning}. "
                       "Its claims and ingredient relationships cannot be relied on. Effects: attestation strength "
                       "is 0.0; Integrity records the ordinary contradiction final_c2pa_manifest_validates "
                       "(not the severe cap), and a value is still released only under the normal rule.")
            if known:
                message += (f" The signing certificate belongs to a known AI generation signer ({known}), so its "
                            "AI-generation label is still accepted as evidence that lowers the human share.")
        elif trust in ("trusted_c2pa", "trusted_legacy_itl"):
            key, severity = trust, "info"
            message = (f"The final artefact's C2PA manifest is valid and signed by {signer}, trusted through "
                       f"{validation.get('trust_source')}. Attestation strength is {value:g}. {boundary}")
        elif trust == "untrusted":
            key, severity = "untrusted", "low"
            message = (f"The final artefact's C2PA manifest is intact (content binding and signature verify), but "
                       f"its signer ({signer}) is not on the pinned C2PA Trust List or, for content signed before "
                       "its freeze, the legacy Interim Trust List. Anyone can create such a signature, so attestation "
                       "strength is 0.3 (partial) and a claim supporting human authorship cannot release Integrity. "
                       f"{boundary}")
            if known:
                message += (f" The signer is a known AI generation signer ({known}); its AI-generation label is "
                            "accepted as evidence that lowers the human share.")
        else:
            key, severity = "unclear", "low"
            message = (f"A C2PA manifest was read from the final artefact, but its validation state is unclear. "
                       f"Attestation strength is {value:g} (partial) and its claims are treated as submitter "
                       f"documents. {boundary}")
        return _finding("C2PA_FINAL_ATTESTATION", message, final_hash, severity, final_c2pa_state=key,
                        attestation_value=value, trust_status=trust, known_ai_signer=known)

    @staticmethod
    def _evaluate(native, parser_observations):
        records = _c2pa_records(native, parser_observations)
        final_hash = native["final_artefact_hash"]
        reports = [C2PAAttestationPass._report(record, final_hash)
                   for record in records]
        findings = (C2PAAttestationPass._failure_findings(records, final_hash) +
                    C2PAAttestationPass._signer_findings(native, records, final_hash))
        final_records = [record["manifest"] for record in records
                         if record["asset_hash"] == final_hash]
        if not final_records:
            reason = "No C2PA parser record is bound to the final artefact. Supporting-node attestations do not raise the final attestation axis."
            installed = any(record["manifest"].get("sdk_available") for record in records)
            if not installed:
                return _axis(reasoning=reason), {}, findings, reports
            return _axis("available", 0.0, 0.9, reason), {
                "validation_state": "manifest_absent", "signature_present": False,
                "signature_valid": None, "cryptographically_valid": None,
                "credential_trusted": None, "codes": [], "reasons": [reason]}, findings, reports

        installed = any(item.get("sdk_available") for item in final_records)
        reader_succeeded = any(item.get("reader_succeeded") for item in final_records)
        manifests = [item for item in final_records if item.get("present")]
        if not installed:
            reason = "C2PA SDK is unavailable for the final artefact, so no cryptographic attestation was assessed."
            return _axis(reasoning=reason), {}, findings, reports
        if not reader_succeeded:
            reason = "C2PA SDK was available, but it could not read the final artefact; absence and read failure cannot be conflated."
            return _axis("partial", None, 0.3, reason), {
                "validation_state": "manifest_not_read", "signature_present": None,
                "signature_valid": None, "cryptographically_valid": None,
                "credential_trusted": None, "codes": [], "reasons": [reason]}, [
                    _finding("C2PA_READ_FAILED", reason, final_hash, "low")] + findings, reports
        if not manifests:
            reason = "C2PA SDK ran but found no active manifest associated with the final artefact."
            return _axis("available", 0.0, 0.9, reason), {
                "validation_state": "manifest_absent", "signature_present": False,
                "signature_valid": None, "cryptographically_valid": None,
                "credential_trusted": None, "codes": [], "reasons": [reason]}, findings, reports
        signature = any(item.get("signature_info") for item in manifests)
        states = [str(item.get("validation_state")) for item in manifests
                  if item.get("validation_state") is not None]
        normalized = {item.casefold() for item in states}
        codes = sorted(set(code for item in manifests
                           for code in C2PAAttestationPass._collect_codes(
                               item.get("validation_results"))))
        statuses = {item.get("trust_status") for item in manifests}
        # Invalid takes precedence, then the strongest trust found among final manifests.
        if "invalid" in normalized or "invalid" in statuses:
            trust_status, chosen = "invalid", None
        else:
            trust_status = next((status for status in ("trusted_c2pa", "trusted_legacy_itl", "untrusted")
                                 if status in statuses), None)
            if trust_status is None and "trusted" in normalized:
                trust_status = "trusted_c2pa"
            elif trust_status is None and "valid" in normalized:
                trust_status = "untrusted"
            chosen = next((item for item in manifests if item.get("trust_status") == trust_status), manifests[0])
        state = ("Invalid" if trust_status == "invalid" else
                 "Trusted" if trust_status in ("trusted_c2pa", "trusted_legacy_itl") else
                 "Valid" if trust_status == "untrusted" else "manifest_parsed")
        signer = _c2pa_signer_label(chosen) if chosen else _c2pa_signer_label(manifests[0])
        source = (chosen or {}).get("trust_source") or {}
        listed = f"{source.get('label')} @ {str(source.get('commit') or '')[:7]}" if source else None
        # Ordered by what was verified: content binding, signature, signer identity.
        # Revocation is never checked offline, so no state reaches a verified revocation.
        if trust_status == "invalid":
            value, availability, confidence = 0.0, "available", 0.9
            crypto, trusted = False, False
        elif trust_status == "trusted_c2pa":
            value, availability, confidence = 1.0, "available", 0.9
            crypto, trusted = True, True
        elif trust_status == "trusted_legacy_itl":
            value, availability, confidence = 0.9, "available", 0.9
            crypto, trusted = True, True
        elif trust_status == "untrusted":
            # A self-made certificate also validates, so the signer is unknown:
            # binding and signature verified, identity not.
            value, availability, confidence = 0.3, "partial", 0.9
            crypto, trusted = True, False
        else:
            value, availability, confidence = (0.25 if signature else 0.1), "partial", 0.6
            crypto = trusted = None
        boundary = ("A trusted signature identifies who signed the manifest; it does not verify that its "
                    "claims are true. Revocation was not checked (offline validation).")
        if trust_status == "invalid":
            failures = sorted({code for item in manifests if _c2pa_is_invalid(item)
                               for code in _c2pa_failure_codes(item)})
            reason = ("Active C2PA manifest for the final artefact failed SDK validation "
                      f"({', '.join(failures) or 'no failure code reported'}). "
                      "Its claims and ingredient relationships cannot be relied on.")
        elif trust_status in ("trusted_c2pa", "trusted_legacy_itl"):
            reason = f"Signed by {signer} (trusted: {listed}). {boundary}"
        elif trust_status == "untrusted":
            reason = (f"Signed by {signer}, who is not on the pinned C2PA Trust List or the legacy Interim "
                      f"Trust List; the signature is intact but the signer is unknown. {boundary}")
        else:
            reason = f"A C2PA manifest was read but its validation state is unclear. {boundary}"
        validation = {"validation_state": state, "signature_present": signature,
                      "signature_valid": crypto, "cryptographically_valid": crypto,
                      "credential_trusted": trusted, "codes": codes, "reasons": [reason],
                      "trust_status": trust_status, "trust_source": listed,
                      "revocation_status": "not_checked", "signer": signer}
        return _axis(availability, value, confidence, reason), validation, findings, reports


class AIDisclosurePass:
    """Measure explicit disclosure coverage; never detect AI from content."""

    @staticmethod
    def summary(native):
        flagged = (native.get("_input_issues") or {}).get("declaration_nodes") or {}
        audio = [node for node in native["artefacts"] if node["artefact_type"].startswith("audio/")
                 or node["artefact_type"] == "audio"]
        assessed = [node for node in audio if node["artefact_hash"] not in flagged]
        declared = [node for node in assessed
                    if (node.get("attributes") or {}).get("creation_method") not in (None, "", [], {})]
        return {"axis_key": "ai_disclosure", "display_name": "Creation-method disclosure coverage",
                "metric_semantics": "declaration_coverage", "applicable_audio_count": len(audio),
                "assessed_audio_count": len(assessed), "declared_audio_count": len(declared),
                "excluded_input_error_count": len(audio) - len(assessed),
                "content_ai_detection_performed": False, "ai_generated_probability": None,
                "declaration_truth_verified": False}

    @staticmethod
    def evaluate(native):
        summary = AIDisclosurePass.summary(native)
        total, declared, excluded = (summary[key] for key in
                                    ("assessed_audio_count", "declared_audio_count", "excluded_input_error_count"))
        if not total:
            return _axis(reasoning="No applicable audio artefacts were submitted."
                         if not excluded else
                         "Every audio declaration has input errors; disclosure coverage was not assessed."), []
        value = declared / total
        availability = "available" if declared else "unavailable"
        axis = _axis(availability, value if declared else None, 0.8 if declared else None,
                     f"Creation-method disclosure coverage: explicit declarations cover {declared}/{total} audio artefacts. "
                     "This measures whether declarations were supplied, not whether they are true, "
                     "the amount of AI audio, or the probability of AI generation; no content AI detector ran."
                     + (f" {excluded} audio artefact(s) with declaration input errors were not assessed." if excluded else ""))
        findings = [_finding("AI_DISCLOSURE_IS_DECLARATION_COVERAGE",
                             "The AI disclosure axis measures creation-method declaration coverage only. "
                             "It does not detect AI audio or verify the truth of the declarations.",
                             scoring_eligible=False, integrity_contribution=None)]
        if declared != total:
            findings.append(_finding("AI_DISCLOSURE_MISSING", "One or more audio artefacts lack creation_method disclosure.",
                                     severity="medium"))
        return axis, findings


ASSESSMENT_STATUS_SEMANTICS = [
    {"status": "corroborated",
     "meaning": "The assessed check or fact is consistent with the compared fields; origin, direction and authorship are not authenticated.",
     "numeric_treatment": "1.0 in an eligible evidence layer."},
    {"status": "contradicted",
     "meaning": "An applicable assessed claim conflicts with observed evidence.",
     "numeric_treatment": "0.0 for that eligible check or fact; other facts may still contribute to the layer mean. Emits a contradiction finding."},
    {"status": "inconclusive",
     "meaning": "The check ran, but the evidence did not reach either reliable endpoint.",
     "numeric_treatment": "Excluded from the denominator and reported as a finding."},
    {"status": "unavailable",
     "meaning": "The check could not run, for example because a file, parameter, budget, or analysable signal was unavailable.",
     "numeric_treatment": "Excluded from the denominator and never treated as zero."},
]


def _layer(name, status, value, contributes, reason, check_count,
           manual_review_reasons=None, weight=None):
    return {
        "layer": name,
        "status": status,
        "value": value,
        "contributes_to_integrity": contributes,
        "check_count": check_count,
        "reason": reason,
        "manual_review_reasons": sorted(set(manual_review_reasons or [])),
        # The configured weight this layer carries in the active scoring
        # profile. Only set for a layer the profile can weight (see
        # scoring_profile.INTEGRITY_LAYER_NAMES) -- never for a
        # non-contributing layer, since it has no scoring weight to report.
        "weight": weight,
    }


def _relevant_recording_types(workflow_input, workflow_policy=None):
    """Recording types the selected workflow requires; none without a workflow.

    The engine passes the policy it already loaded for completeness; direct
    callers without one get the shipped default policy.
    """
    workflow_id = (workflow_input or {}).get("workflow_id")
    if not workflow_id:
        return []
    return required_recording_types(workflow_policy or load_workflow_policy(DEFAULT_POLICY_PATH), workflow_id)


def _boolean_status(checks, successful_status="corroborated"):
    eligible = [item for item in checks if isinstance(item.get("passed"), bool)]
    if not eligible:
        return "unavailable", None
    value = sum(item["passed"] for item in eligible) / len(eligible)
    return (successful_status if value == 1.0 else "contradicted"), value


def _summarize_physical_similarity(results):
    counts = {status: sum(item.get("status") == status for item in results)
              for status in ("corroborated", "contradicted",
                             "inconclusive", "unavailable")}
    target_buckets = {}
    for item in results:
        contribution = item.get("integrity_contribution")
        if contribution is None:
            continue
        target_buckets.setdefault(item["target_hash"], []).append(contribution)
    target_values = {
        target_hash: round(sum(values) / len(values), 6)
        for target_hash, values in sorted(target_buckets.items())
    }
    value = (round(sum(target_values.values()) / len(target_values), 6)
             if target_values else None)
    if counts["contradicted"]:
        status = "contradicted"
    elif counts["corroborated"]:
        status = "corroborated"
    elif counts["inconclusive"]:
        status = "inconclusive"
    else:
        status = "unavailable"
    return {
        "status": status,
        "value": value,
        "counts": counts,
        "declared_relationships": len(results),
        "scored_relationships": counts["corroborated"] + counts["contradicted"],
        "assessed_targets": len(target_values),
        "target_values": target_values,
        "scoring_policy": (
            "Only corroborated (1) and contradicted (0) relationships are scored. "
            "Inconclusive and unavailable relationships are excluded. Relationships "
            "are averaged within each target before targets are averaged."),
        "content_match_is_provenance": False,
    }


def _disabled_physical_similarity(native):
    """Return non-scoring placeholders without reading or comparing samples."""
    results = []
    for target in native["artefacts"]:
        for edge in target.get("evidence", []):
            results.append({
                "relationship_type": edge["relationship_type"],
                "comparison_mode": "disabled",
                "source_hashes": [edge["hash"]],
                "target_hash": target["artefact_hash"],
                "status": "unavailable",
                "integrity_contribution": None,
                "scoring_eligible": False,
                "similarity_score": None,
                "parameter_source": "not_used",
                "declared_ranges_used": False,
                "analysis_complete": False,
                "metrics": {},
                "threshold_policy": None,
                "content_match_is_provenance": False,
                "reason_code": "disabled_by_policy",
                "reason": (
                    "Legacy signal comparisons are disabled because their narrow "
                    "audio assumptions are not reliable across real production "
                    "workflows."
                ),
            })
    summary = {
        "enabled": False,
        "scoring_eligible": False,
        "status": "unavailable",
        "reason_code": "disabled_by_policy",
        "reason": (
            "Legacy signal comparisons are disabled because their narrow audio "
            "assumptions are not reliable across real production workflows."
        ),
        "value": None,
        "counts": {
            "corroborated": 0,
            "contradicted": 0,
            "inconclusive": 0,
            "unavailable": len(results),
        },
        "declared_relationships": len(results),
        "scored_relationships": 0,
        "assessed_targets": 0,
        "target_values": {},
        "scoring_policy": "disabled; no signal result contributes to integrity",
        "content_match_is_provenance": False,
    }
    return results, summary


_BWF_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BWF_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def _bwf_stamp(observation):
    """(date, time or None) from a WAV's bext origination fields, or None."""
    bext = ((observation or {}).get("embedded_metadata") or {}).get("broadcast_extension") or {}
    date, time = str(bext.get("origination_date") or ""), str(bext.get("origination_time") or "")
    if not _BWF_DATE.match(date):
        return None
    return date, time if _BWF_TIME.match(time) else None


def _undated_reason(observation):
    """Why an audio file carries no comparable BWF origination date."""
    audio_format = ((observation or {}).get("observed") or {}).get("format")
    if audio_format == "MP3":
        return "format_has_no_bwf"
    if audio_format == "FLAC" and not ((observation or {}).get("embedded_metadata") or {}).get("broadcast_extension"):
        return "bwf_not_kept"
    return "no_origination_date"


def declared_chronology(native, wav_observations, bound_hashes):
    """Declared audio edges whose source's BWF origination is later than its target's.

    BWF dates can be reset or rewritten, so a reversed order is reported for
    review and never scored. Same-day files are compared by time only when
    both carry one. When either audio file has no date (an MP3, a FLAC encoded
    without its WAV chunks, a WAV without ``bext``), the edge is listed as
    ``not_comparable`` instead of being left out, so a missing comparison is
    visible; no time is guessed.
    """
    rows, findings = [], []
    for target in native["artefacts"]:
        for edge in target.get("evidence", []):
            source_hash, target_hash = edge["hash"], target["artefact_hash"]
            if source_hash not in bound_hashes or target_hash not in bound_hashes:
                continue
            if source_hash not in wav_observations or target_hash not in wav_observations:
                continue  # not an audio-to-audio edge
            source, later = _bwf_stamp(wav_observations.get(source_hash)), _bwf_stamp(wav_observations.get(target_hash))
            if source is None or later is None:
                undated = [side for side, stamp in (("source", source), ("target", later)) if stamp is None]
                rows.append({"source_hash": source_hash, "target_hash": target_hash,
                             "relationship_type": edge["relationship_type"], "status": "not_comparable",
                             "reason_code": _undated_reason(wav_observations[source_hash if undated[0] == "source"
                                                                             else target_hash]),
                             "undated": undated, "source_newer_than_target": None,
                             "scoring_eligible": False, "integrity_contribution": None})
                continue
            newer = source[0] > later[0] or (source[0] == later[0] and source[1] is not None
                                              and later[1] is not None and source[1] > later[1])
            row = {"source_hash": source_hash, "target_hash": target_hash,
                   "relationship_type": edge["relationship_type"], "status": "compared",
                   "source_origination": " ".join(part for part in source if part),
                   "target_origination": " ".join(part for part in later if part),
                   "source_newer_than_target": newer, "scoring_eligible": False, "integrity_contribution": None}
            rows.append(row)
            if newer:
                findings.append(_finding(
                    "DECLARED_SOURCE_NEWER_THAN_TARGET",
                    f"The declared {edge['relationship_type']} source was originated {row['source_origination']}, "
                    f"after its target ({row['target_origination']}), according to their BWF fields. BWF dates can "
                    "be reset or rewritten, so this is reported for review and not scored.",
                    target_hash, "low", scoring_eligible=False, integrity_contribution=None,
                    source_hash=source_hash, relationship_type=edge["relationship_type"]))
    return rows, findings


_RECORDINGS = {"audio/raw-take", "audio/raw-track"}


def format_conversions(relationship_observations):
    """Report rows and findings for edges declared as format conversions."""
    rows, findings = [], []
    for observation in relationship_observations:
        record = observation.get("format_conversion")
        if not record:
            continue
        rows.append(record)
        identity = record.get("sample_identity") or {}
        if record.get("status") == "ignored":
            findings.append(_finding(
                "FORMAT_CONVERSION_NOT_APPLIED",
                "transformation=format_conversion is only checked on derived_from edges; it was ignored here.",
                record["target_hash"], "low", scoring_eligible=False, integrity_contribution=None,
                source_hash=record["source_hash"], relationship_type=record["relationship_type"]))
        elif identity.get("status") == "different":
            findings.append(_finding(
                "FORMAT_CONVERSION_SAMPLES_DIFFER",
                f"The {record['source_format']} and {record['target_format']} files are declared as the same audio "
                "in another format, but their decoded samples differ. This is reported, not scored; the declared "
                "properties are checked in declaration consistency.",
                record["target_hash"], "low", scoring_eligible=False, integrity_contribution=None,
                source_hash=record["source_hash"]))
    return rows, findings


def _recorder_capability(observation):
    observed = observation.get("observed") or {}
    recorder = observation.get("recorder_metadata") or {}
    if observed.get("format") == "MP3":
        return "not_supported_by_format"
    if recorder.get("present"):
        return "present"
    if observed.get("format") == "FLAC" and recorder.get("foreign_metadata_kept") is False:
        return "not_kept"
    return "absent"


def format_capabilities(native, wav_observations, c2pa_records, conversion_rows):
    """Which evidence each submitted audio file can carry, given its format. Report-only.

    The format never lowers a score; it decides which checks can run. The
    evaluator is designed for WAV (``check_scope: full``); FLAC and MP3 receive
    the checks implemented today (``limited``), and ``limited_checks`` names the
    families not made, as listed in ``audio_observations.FORMAT_SCOPE``. A file the
    submitter also supplied in an original format, declared as a format
    conversion, gets no advice to supply it.
    """
    manifests = {record.get("asset_hash"): bool(record.get("manifest_present")) for record in c2pa_records}
    converted = {row["target_hash"] for row in conversion_rows if row.get("status") == "assessed"}
    rows = []
    for node in native["artefacts"]:
        digest = node["artefact_hash"]
        observation = wav_observations.get(digest) or {}
        observed = observation.get("observed") or {}
        if observation.get("status") != "parsed" or not observed.get("format"):
            continue
        audio_format = observed["format"]
        recorder = _recorder_capability(observation)
        row = {"hash": digest, "type": node["artefact_type"], "format": audio_format,
               "lossless": audio_format in ("WAV", "FLAC"), "recorder_metadata": recorder,
               "exact_duration": observed.get("duration_seconds") is not None,
               "bit_depth": observed.get("bit_depth") is not None,
               "origination_date": _bwf_stamp(observation) is not None,
               "embedded_c2pa": manifests.get(digest),
               # WAV receives every check; FLAC and MP3 the ones implemented today.
               "check_scope": "full" if audio_format == "WAV" else "limited",
               "limited_checks": [item["check_family"] for item in observation.get("limited_checks", [])],
               "advice": [], "scoring_eligible": False, "integrity_contribution": None}
        recording = node["artefact_type"] in _RECORDINGS and digest not in converted
        if recording and recorder == "not_kept":
            row["advice"].append("Re-encode from the original WAV with flac --keep-foreign-metadata, or submit "
                                 "the original WAV as well and declare this file derived_from it with "
                                 "transformation=format_conversion.")
        elif recording and recorder == "not_supported_by_format":
            row["advice"].append("MP3 cannot carry recorder metadata. Submit the original WAV or FLAC as well "
                                 "and declare this file derived_from it with transformation=format_conversion.")
        if audio_format == "MP3" and not row["exact_duration"]:
            row["advice"].append("This MP3 has no Xing/LAME header, so its exact length is unknown. Encoders "
                                 "such as LAME and FFmpeg write one by default.")
        rows.append(row)
    return rows


def assess_axes(native, chain, bound_hashes, missing_hashes, wav_observations,
                relationship_observations, parser_observations, workflow_input,
                scoring_profile=None, workflow_policy=None):
    # A caller-supplied profile lets integrity layer weighting be configured
    # without touching this function; the default profile's values are
    # defined to reproduce today's hardcoded behaviour exactly. An
    # explicitly supplied but malformed profile must not be silently
    # replaced by the default -- only the absence of a profile (None) falls
    # back; load_scoring_profile() already raises on anything malformed.
    if scoring_profile is None:
        scoring_profile = load_default_scoring_profile()
    integrity_profile = scoring_profile["integrity"]
    layer_weights = integrity_profile["layer_weights"]
    withholding_rule = integrity_profile["withholding_rule"]
    confidence_formula = integrity_profile["confidence_formula"]
    # Both optional, and only meaningful together: a profile that defines
    # neither leaves Integrity exactly as the plain weighted mean (today's
    # behaviour); load_scoring_profile() rejects a profile that defines only
    # one of the pair, since each is meaningless without the other.
    severe_contradiction_rules = set(integrity_profile.get("severe_contradiction_rules") or [])
    severe_contradiction_cap = integrity_profile.get("severe_contradiction_cap")

    file_value, file_checks, file_findings = BoundFileIntegrityPass.evaluate(
        native, bound_hashes, missing_hashes, wav_observations,
        parser_observations)
    rel_value, rel_checks, rel_findings = _relationship_integrity_results(
        relationship_observations)
    declared_checks, declared_findings = _declared_file_property_results(native, wav_observations)
    cross_value, cross_checks, cross_findings = ExpandedCrossEvidencePass.evaluate(
        native, workflow_input, parser_observations, wav_observations)
    structural_checks = StructuralValidityPass.evaluate(native)
    # Phase 0 evidence-chain transition: the legacy signal implementation is
    # intentionally not invoked. Its assumptions produced misleading findings
    # for legitimate high-frequency and processed material. Keep the public
    # keys stable while reporting explicit, non-scoring placeholders and summary.
    physical_similarity, physical_summary = _disabled_physical_similarity(native)
    (attestation, validation, attestation_findings,
     c2pa_attestation_records) = C2PAAttestationPass.evaluate(
         native, parser_observations)
    ai, ai_findings = AIDisclosurePass.evaluate(native)

    file_status, file_layer_value = _boolean_status(file_checks)
    declaration_facts = aggregate_declarations(declared_checks + rel_checks)
    declaration_status, declaration_layer_value = _boolean_status(declaration_facts)
    cross_facts = aggregate_cross_evidence(
        cross_checks, one_vote_per_claim=integrity_profile.get("one_vote_per_claim", False))
    evidence_sources = summarize_evidence_sources(
        cross_facts, parser_observations, wav_observations,
        [record["signer"] for record in c2pa_attestation_records
         if record.get("manifest_present") and c2pa_signer_trusted(record) and record.get("signer")])
    cross_status, cross_layer_value = _boolean_status(cross_facts)
    crypto = validation.get("cryptographically_valid")
    if crypto is False:
        cryptographic_status = "contradicted"
    elif crypto is True:
        cryptographic_status = "corroborated"
    elif validation.get("validation_state") == "manifest_absent":
        cryptographic_status = "unavailable"
    else:
        cryptographic_status = "unavailable"

    layers = [
        _layer("file_integrity", file_status, file_layer_value, True,
               "SHA-256 binding and parser readability of submitted files; no submitter claims are compared here.",
               len(file_checks), weight=layer_weights["file_integrity"]),
        _layer("structural_validity", "corroborated", 1.0, False,
               "Graph, references, cycles, connectivity, and endpoint roles passed before assessment; this is not content proof.",
               len(structural_checks)),
        _layer("declaration_consistency", declaration_status,
               declaration_layer_value, True,
               "Submitted file technical claims and relationship parameters compared with measured media; "
               "one vote per declared file property, and any failed claim keeps that fact failed.",
               sum(isinstance(item.get("passed"), bool) for item in declaration_facts),
               weight=layer_weights["declaration_consistency"]),
        _layer("physical_similarity", "unavailable", None, False,
               "Legacy signal comparisons are disabled by policy and never contribute to integrity.",
               0),
        _layer("cross_evidence_support", cross_status, cross_layer_value, True,
               "One vote per cross-evidence rule and subject; any assessed failure keeps that fact failed. "
               "Recorder-metadata agreement is consistency evidence, not origin authentication.",
               sum(isinstance(item.get("passed"), bool) for item in cross_facts),
               weight=layer_weights["cross_evidence_support"]),
        _layer("cryptographic_attestation", cryptographic_status,
               (attestation.get("value")
                if cryptographic_status in {"corroborated", "contradicted"} else None), False,
               "C2PA remains in the separate attestation-strength axis and never inflates integrity.",
               1 if validation else 0),
    ]
    scored_layers = [item for item in layers
                     if item["contributes_to_integrity"] and item["value"] is not None]
    components = [item["value"] for item in scored_layers]
    contradiction_present = any(
        item["contributes_to_integrity"] and item["status"] == "contradicted"
        for item in layers)
    for item in layers:
        if item["layer"] == "cross_evidence_support":
            item["evidence_tier_counts"] = tier_counts(cross_checks)
            item["raw_check_count"] = len(cross_checks)
            item["aggregation_method"] = "one_vote_per_rule_subject_any_failure"
            item["fact_count"] = len(cross_facts)
            item["scored_fact_count"] = item["check_count"]
    # Qualifying support means an assessed fact from a tier allowed by the
    # release policy; this does not authenticate embedded-field origins. A
    # profile that names no tiers allows any assessed cross-evidence fact.
    support_tiers = tuple(withholding_rule.get("substantive_support_tiers") or TIERS)
    substantive_support = any(isinstance(item.get("passed"), bool) and
                              set(item["assessed_evidence_tiers"]) & set(support_tiers) for item in cross_facts)
    min_components = withholding_rule["min_components_for_substantive_mean"]
    require_support = withholding_rule.get("require_substantive_support",
                                           withholding_rule.get("require_substantive_support_or_contradiction"))
    denominator_layers = confidence_formula["denominator_layers"]
    # Support needs independent evidence because agreement can be fabricated;
    # a severe failure is computed by the evaluator itself and needs none, so
    # (when the profile enables it) it releases a value no higher than the cap.
    severe_release = withholding_rule.get("release_capped_value_on_severe_contradiction", False)
    triggered = (sorted({item["rule"] for item in file_checks + declared_checks + rel_checks + cross_checks
                         if item.get("passed") is False and item["rule"] in severe_contradiction_rules})
                 if severe_contradiction_rules and severe_contradiction_cap is not None else [])
    # A final manifest the SDK could not read cannot be checked against the file,
    # so evidence about other nodes must not release a number for the final.
    final_attestation_unreadable = validation.get("validation_state") == "manifest_not_read"
    aaf_coverage, aaf_media, aaf_findings = [], [], []
    for owner_digest, parsed in sorted(parser_observations.items()):
        if parsed.get("parser") != "aaf" or not parsed.get("data") or owner_digest not in bound_hashes:
            continue
        coverage = aaf_reference_coverage(parsed["data"], owner_digest, wav_observations)
        media = aaf_non_recorder_media(parsed["data"], owner_digest)
        aaf_coverage.append(coverage)
        aaf_media.append(media)
        if coverage["missing_recordings"]:
            names = ", ".join(f"{item['tape']}/{item['file_name']}" for item in coverage["missing_recordings"])
            aaf_findings.append(_finding(
                "AAF_REFERENCED_TAKES_NOT_SUBMITTED",
                f"The AAF edit uses {coverage['referenced_recordings']} recorder takes; "
                f"{coverage['bound_recordings']} are bound in this submission. Not submitted: {names}. "
                "This is reported, not scored.",
                owner_digest, "low", scoring_eligible=False, integrity_contribution=None,
                missing_recordings=coverage["missing_recordings"]))
        if media["media_count"]:
            aaf_findings.append(_finding(
                "AAF_REFERENCES_NON_RECORDER_MEDIA",
                f"The AAF edit references {media['media_count']} media items that are not recorder takes "
                "(for example library effects, music or renders). The evaluator cannot link them to submitted "
                "files and makes no licensing determination; they are listed in "
                "assessment_checks.aaf_non_recorder_media.",
                owner_digest, "info", scoring_eligible=False, integrity_contribution=None,
                media_count=media["media_count"]))
    # Breadth: how many of the workflow's recordings have qualifying evidence.
    # It only gates release when the profile sets a minimum share.
    # AAF-used takes that were not submitted join the denominator, so leaving a
    # hard-to-check take out cannot raise the share.
    support_breadth = summarize_support_breadth(native, cross_facts, support_tiers,
                                                _relevant_recording_types(workflow_input, workflow_policy),
                                                aaf_coverage, bound_hashes, wav_observations)
    min_share = withholding_rule.get("min_supported_asset_share")
    breadth_met = (min_share is None or not support_breadth["applicable"]
                   or support_breadth["checked_share"] >= min_share)
    support_breadth.update({
        "min_supported_asset_share": min_share,
        "requirement": ("not_set" if min_share is None else "not_applicable" if not support_breadth["applicable"]
                        else "met" if breadth_met else "not_met"),
        "scoring_effect": "none_report_only" if min_share is None else "numeric_release_gate"})
    threshold_met = (len(components) >= min_components and
                     (substantive_support or not require_support) and not final_attestation_unreadable
                     and breadth_met)
    qualifying_facts = [{"rule": item["rule"], "identity": item["identity"],
                         "evidence_tiers": sorted(set(item["assessed_evidence_tiers"]) & set(support_tiers))}
                        for item in cross_facts if isinstance(item.get("passed"), bool)
                        and set(item["assessed_evidence_tiers"]) & set(support_tiers)]
    cap_record = None
    # Optional: how hard the checked evidence is to forge scales confidence,
    # never the value. Profiles without forge_cost keep the layer-count formula.
    forge_config = integrity_profile.get("forge_cost")
    strength = (evidence_strength(cross_facts, {node["artefact_hash"]: node["artefact_type"]
                                                for node in native["artefacts"]}, bound_hashes, forge_config)
                if forge_config else None)
    if not components:
        integrity_availability, integrity_value, integrity_confidence = "unavailable", None, None
    else:
        integrity_availability = ("available" if len(components) == denominator_layers
                                  else "partial")
        # File/declaration consistency alone would create a misleading perfect-
        # looking score without assessed qualifying cross-evidence.
        if threshold_met or (triggered and severe_release):
            weight_total = sum(item["weight"] for item in scored_layers)
            integrity_value = (sum(item["value"] * item["weight"] for item in scored_layers)
                               / weight_total)
            # A severe contradiction is a ceiling, not an override: it only
            # pulls the weighted mean down toward the cap, never below where
            # it already was, and never raises a lower mean up to meet it.
            if triggered:
                weighted_mean = integrity_value
                integrity_value = min(integrity_value, severe_contradiction_cap)
                cap_record = {"cap": severe_contradiction_cap, "triggered_rules": triggered,
                              "weighted_mean": weighted_mean, "value": integrity_value,
                              "value_lowered": integrity_value < weighted_mean}
        else:
            integrity_value = None
        integrity_confidence = round(
            confidence_formula["ceiling"] * len(components) / denominator_layers
            * (strength["confidence_factor"] if strength else 1.0), 3)
    # An ordinary contradiction is a finding and a status, never permission to
    # release an otherwise withheld numeric score.
    tiers_restricted = set(support_tiers) != set(TIERS)
    rule_description = f"at least {min_components} populated contributing layer(s)"
    if require_support:
        rule_description += " and an assessed qualifying cross-evidence fact"
        if tiers_restricted:
            rule_description += f" ({', '.join(support_tiers)})"
    if min_share is not None:
        rule_description += f" and qualifying evidence checked for at least {min_share:.0%} of the workflow's recordings"
    if not components:
        release_reason = "no_assessed_layers"
    elif threshold_met:
        release_reason = "qualifying_cross_evidence" if require_support else "configured_layer_coverage"
    elif triggered and severe_release:
        release_reason = "severe_contradiction_cap"
    elif final_attestation_unreadable:
        release_reason = "final_attestation_unreadable"
    elif len(components) < min_components:
        release_reason = "insufficient_assessed_layers"
    elif (substantive_support or not require_support) and not breadth_met:
        release_reason = "insufficient_support_breadth"
    else:
        release_reason = "insufficient_qualifying_cross_evidence"
    decision = {"status": ("contradicted" if contradiction_present else
                           "consistent" if integrity_value is not None else "insufficient_evidence"),
                "contradiction_present": contradiction_present,
                "severe_contradiction_present": bool(triggered),
                "numeric_release": "released" if integrity_value is not None else "withheld",
                "release_reason": release_reason,
                "qualifying_cross_evidence_assessed": substantive_support,
                "qualifying_facts": qualifying_facts,
                "ordinary_contradiction_release_allowed": False,
                "severe_contradiction_release": "capped_value" if severe_release else "disabled",
                "score_semantics": "assessed_check_consistency_not_provenance_authentication",
                # Counted once here; reasoning, explanations and the desktop view read these.
                **fact_counts({"file_integrity": file_checks,
                               "declaration_consistency": [fact["representative_check"] for fact in declaration_facts],
                               "cross_evidence_support": [fact["representative_check"] for fact in cross_facts]})}
    withholding_clause = (f"Numeric release requires {rule_description}; "
                          + ("the rule permitted this value. " if threshold_met and integrity_value is not None else
                             "a severe contradiction released a value no higher than the cap. "
                             if integrity_value is not None else
                             f"the value is withheld ({release_reason}). ")
                          + ("The final artefact's C2PA manifest could not be read, so evidence about other "
                             "files does not release a value for it. " if final_attestation_unreadable else "")
                          + ("An ordinary contradiction never releases a value. " if severe_release
                             else "A contradiction alone never releases a value. ")
                          + ("Qualifying cross-evidence was assessed for this submission. " if substantive_support else
                             "No qualifying cross-evidence was assessed for this submission. "))
    # A capped value must say so; otherwise a reader sees the cap value with
    # no indication that a severe contradiction, not the mean, produced it.
    cap_clause = ""
    if cap_record:
        rules = ", ".join(cap_record["triggered_rules"])
        if cap_record["value_lowered"]:
            cap_clause = (f"A severe contradiction ({rules}) capped Integrity at {cap_record['cap']:g}; "
                          f"the weighted mean was {cap_record['weighted_mean']:.3f}. ")
        else:
            cap_clause = (f"A severe contradiction ({rules}) was found, but the weighted mean "
                          f"{cap_record['weighted_mean']:.3f} was already at or below the "
                          f"{cap_record['cap']:g} cap, which therefore did not change it. ")
    # Name every contradiction behind the value, with what was compared, so a
    # reader can see exactly which check failed and by how much.
    failed = [item for item in file_checks +
              [fact["representative_check"] for fact in declaration_facts + cross_facts]
              if item.get("passed") is False]
    contradiction_clause = ("Contradictions: " + " ".join(describe_check(item) for item in failed) + " "
                            if failed else "")
    # Agreement between documents from one uploader is not independent support.
    document_count = evidence_sources[0]["document_count"]
    cross_consistent = not any(fact.get("passed") is False for fact in cross_facts)
    if document_count == 1:
        sources_clause = ("This document was provided and uploaded by the user. "
                          + ("It is consistent with the other submitted files, but it cannot be confirmed "
                             "if it is independently true. " if cross_consistent else
                             "It cannot be confirmed if it is independently true. "))
    elif document_count > 1:
        sources_clause = (f"These {document_count} documents were provided and uploaded by the same user. "
                          + ("They are consistent with each other, but it cannot be confirmed "
                             "if they are independently true. " if cross_consistent else
                             "It cannot be confirmed if they are independently true. "))
    else:
        sources_clause = ""
    # Name how much of the workflow's recordings the strong evidence covers.
    recordings = support_breadth["required_recording_count"]
    missing = support_breadth["no_qualifying_evidence"] + support_breadth["not_submitted"]
    checked = recordings - missing
    if not recordings:
        breadth_clause = ""
    elif recordings == 1:
        breadth_clause = ("Strong evidence was checked for the recording. " if checked else
                          "No strong evidence was checked for the recording. ")
    elif not missing:
        breadth_clause = f"Strong evidence was checked for all {recordings} recordings. "
    else:
        breadth_clause = (f"Strong evidence was checked for {checked} of {recordings} recordings; "
                          f"{missing} {'has' if missing == 1 else 'have'} no strong evidence. ")
    unsubmitted, pending = support_breadth["not_submitted"], support_breadth["unmatched_references"]
    if unsubmitted:
        breadth_clause += (f"{unsubmitted} {'recording' if unsubmitted == 1 else 'recordings'} the AAF edit uses "
                           f"{'was' if unsubmitted == 1 else 'were'} not submitted, so the result covers only the "
                           "checked material. ")
    if pending:
        breadth_clause += (f"{pending} AAF {'reference' if pending == 1 else 'references'} could not be matched "
                           "to a submitted recording, so this coverage is provisional. ")
    # Many honest facts dilute one contradiction in the mean, so the count
    # leads the reasoning: a reader of the number alone would miss it.
    summary = contradiction_summary(decision)
    count_clause = f"{summary}; the numeric value alone understates this. " if summary else ""
    confidence_clause = (describe_strength(strength, len(components), denominator_layers)
                         if strength and components else
                         "Integrity confidence reflects how many of the three evidence layers were checked. ")
    integrity = _axis(
        integrity_availability, integrity_value, integrity_confidence,
        (f"{count_clause}"
         f"Integrity has {len(components)}/{denominator_layers} score-eligible evidence layers; missing evidence reduces availability and confidence but is never treated as contradiction. "
         f"{confidence_clause}"
         "To see how many checks passed, refer to the integrity value and the layer results. "
         f"{withholding_clause}"
         f"{cap_clause}"
         f"{contradiction_clause}"
         f"{sources_clause}"
         f"{breadth_clause}"
         "The files and claims have been checked against each other, but this does not guarantee who created "
         "the material, when it was recorded or that the recording session took place. "
         "Signed origin evidence, where available, is shown in attestation_strength. "
         "Structural validity and cryptographic attestation are reported separately and do not raise integrity. "
         "Legacy signal comparisons are disabled by policy and do not produce findings or affect integrity."))
    # Lets a reader of axes alone tell a contradicted submission from one
    # that merely lacks evidence when both withhold the value.
    integrity["status"] = decision["status"]

    metadata_findings = [
        _finding("RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED",
                 "Embedded BWF/iXML metadata can be rewritten with the audio file. "
                 "Field agreement does not authenticate a recorder, recording event, author or historical origin. "
                 "Metadata consistency checks may still score and qualify Integrity for release; this finding has no score effect.",
                 digest, "info", scoring_eligible=False, integrity_contribution=None,
                 recorder_origin_authenticated=None,
                 metadata_sources=(observation.get("recorder_metadata") or {}).get("sources", []))
        for digest, observation in wav_observations.items()
        if digest in bound_hashes and (observation.get("recorder_metadata") or {}).get("present") is True]
    chronology, chronology_findings = declared_chronology(native, wav_observations, bound_hashes)
    conversions, conversion_findings = format_conversions(relationship_observations)
    capabilities = format_capabilities(native, wav_observations, c2pa_attestation_records, conversions)
    unlinked_media = sum(item["media_count"] for item in aaf_media)
    if unlinked_media and ai.get("reasoning"):
        ai["reasoning"] += (f" Coverage counts submitted artefacts only; the submitted AAF also references "
                            f"{unlinked_media} media items that are not part of this submission.")
    sample_data_findings = []
    for node in native["artefacts"]:
        digest = node["artefact_hash"]
        artefact = chain.artefacts[digest]
        reason_code = getattr(
            artefact, "_wav_parser_failure_reason_code", None)
        if reason_code and (wav_observations.get(digest) or {}).get(
                "status") != "parser_failed":
            sample_data_findings.append(_finding(
                "WAV_SAMPLE_DATA_INVALID",
                "WAV sample data failed during bounded content decoding; only dependent audio checks are unavailable.",
                digest, "medium", reason_code=reason_code,
                error=getattr(artefact, "_wav_parser_failure", None)))
    checks = {
        # Identifies the scoring profile actually used for this assessment
        # -- read from the profile object itself, never a hardcoded literal
        # -- so a response can always be traced back to its configuration.
        "scoring_profile": [{"profile_id": scoring_profile["profile_id"],
                             "profile_version": scoring_profile["profile_version"]}],
        "bound_file_integrity": file_checks,
        "structural_validity": structural_checks,
        "declared_file_properties": declared_checks,
        "relationship_integrity": rel_checks,
        "declaration_scored_facts": declaration_facts,
        "relationship_range_validation": [dict(check) for observation in relationship_observations
                                           for check in observation.get("range_validation_details", [])],
        "integrity_layers": layers,
        "integrity_decision": [decision],
        # Present only when the profile grades evidence by forge cost.
        **({"evidence_strength": [strength]} if strength else {}),
        "ai_disclosure_coverage": [AIDisclosurePass.summary(native)],
        "assessment_status_semantics": ASSESSMENT_STATUS_SEMANTICS,
        "cross_evidence": cross_checks,
        "cross_evidence_scored_facts": cross_facts,
        "cross_evidence_audit": audit_cross_evidence(cross_checks),
        "evidence_sources": evidence_sources,
        "support_breadth": [support_breadth],
        "c2pa_attestation_records": c2pa_attestation_records,
        # Pinned trust list versions, so every result can be reproduced and explained.
        "c2pa_trust_configuration": [trust_configuration_record()],
        "physical_similarity": physical_similarity,
        "physical_similarity_summary": [physical_summary],
    }
    if cap_record:
        checks["severe_contradiction_cap"] = [cap_record]
    if chronology:
        checks["declared_chronology"] = chronology
    if conversions:
        checks["format_conversions"] = conversions
    if capabilities:
        checks["format_capabilities"] = capabilities
    if aaf_coverage:
        checks["aaf_reference_coverage"] = aaf_coverage
        checks["aaf_non_recorder_media"] = aaf_media
    findings = (file_findings + declared_findings + rel_findings + cross_findings +
                sample_data_findings + metadata_findings + aaf_findings + chronology_findings +
                conversion_findings + attestation_findings + ai_findings)
    return integrity, attestation, ai, validation, checks, findings
