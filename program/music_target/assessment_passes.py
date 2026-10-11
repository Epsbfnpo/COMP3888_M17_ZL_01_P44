"""Target-owned assessment passes kept separate from workflow completeness."""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

from legacy_parsers.c2pa_parser import parse_c2pa_file
from legacy_parsers.ern_parser import parse_ern_file
from legacy_parsers.rin_parser import parse_rin_file
from music_target.audio_derivation import (
    AudioDerivationPass,
    CompDerivationPass,
    EditDerivationPass,
    StemDerivationPass,
)
from music_target.midi_parser import parse_midi_file
from music_target.physical_audio_passes import (
    AudioAlignmentPass,
    CompVerificationPass,
    DecoySourcePass,
    ExcerptedFromPass,
    MasteringDerivationPass,
    ProcessedAudioMatchPass,
    SourceContributionPass,
    StemMixResidualPass,
)
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
                parsed = {"parser": "wav",
                          "status": "failed" if wav_failed else "parsed",
                          "data": wav_result}
                # C2PA may be embedded in the media itself. Attempt once per
                # audio file; absence never makes completeness fail.
                c2pa = dataclasses.asdict(parse_c2pa_file(str(path)))
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
            findings.append(_finding("C2PA_PARSER_UNAVAILABLE",
                                     "c2pa-python is not installed; C2PA attestation was not assessed.", severity="low"))
    return observations, findings


class BoundFileIntegrityPass:
    """Aggregate file binding, cached WAV checks, and parser readability."""

    @staticmethod
    def evaluate(native, bound_hashes, wav_observations, parser_observations):
        checks, findings = [], []
        for node in native["artefacts"]:
            digest = node["artefact_hash"]
            checks.append({"rule": "object_hash_bound", "passed": digest in bound_hashes,
                           "evidence_hash": digest})
            wav_result = wav_observations.get(digest)
            if wav_result:
                checks.extend(dict(item) for item in wav_result.get("checks", []))
            parser = parser_observations.get(digest)
            if (parser and parser["status"] == "failed" and
                    not (wav_result and wav_result.get("status") == "parser_failed")):
                checks.append({"rule": "declared_type_parser_succeeds", "passed": False,
                               "evidence_hash": digest})
        eligible = [item for item in checks if isinstance(item.get("passed"), bool)]
        passed = sum(item["passed"] for item in eligible)
        for item in checks:
            if item.get("passed") is False:
                code = ("TECHNICAL_CLAIM_MISMATCH"
                        if item["rule"].startswith("technical.")
                        else "FILE_INTEGRITY_CONTRADICTION")
                findings.append(_finding(code, item["rule"],
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
                    if (target and target in wav_observations and
                            selection.get("target_end_seconds") is not None):
                        duration = wav_observations[target]["observed"]["duration_seconds"]
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
                    if asset and asset in wav_observations:
                        duration = wav_observations[asset]["observed"]["duration_seconds"]
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
                    if (target and target in wav_observations and
                            selection.get("target_end_seconds") is not None):
                        duration = wav_observations[target]["observed"]["duration_seconds"]
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
                    if asset and asset in wav_observations:
                        duration = wav_observations[asset]["observed"]["duration_seconds"]
                        checks.append({"rule": "cue_sheet_range_within_audio",
                                       "passed": cue["end_seconds"] <= duration,
                                       "cue_id": cue["cue_id"], "asset_hash": asset})

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
            }
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
            ai_claimed = any("trainedalgorithmic" in item.casefold() for item in source_types)
            if asset_hash in nodes:
                creation = (nodes[asset_hash].get("attributes") or {}).get("creation_method")
                methods = {creation} if isinstance(creation, str) else set(creation or [])
                if source_types and methods:
                    checks.append({
                        "rule": "c2pa_ai_source_type_matches_creation_method",
                        "passed": ("ai-generated" in methods) if ai_claimed else True,
                        "digital_source_types": source_types,
                        "creation_method": sorted(methods),
                        **common,
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
        eligible = [item for item in checks if isinstance(item.get("passed"), bool)]
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
            "validation_codes": sorted(set(
                C2PAAttestationPass._collect_codes(
                    manifest.get("validation_results")))),
        }

    @staticmethod
    def evaluate(native, parser_observations):
        records = _c2pa_records(native, parser_observations)
        final_hash = native["final_artefact_hash"]
        reports = [C2PAAttestationPass._report(record, final_hash)
                   for record in records]
        final_records = [record["manifest"] for record in records
                         if record["asset_hash"] == final_hash]
        if not final_records:
            reason = "No C2PA parser record is bound to the final artefact. Supporting-node attestations do not raise the final attestation axis."
            installed = any(record["manifest"].get("sdk_available") for record in records)
            if not installed:
                return _axis(reasoning=reason), {}, [], reports
            return _axis("available", 0.0, 0.9, reason), {
                "validation_state": "manifest_absent", "signature_present": False,
                "signature_valid": None, "cryptographically_valid": None,
                "credential_trusted": None, "codes": [], "reasons": [reason]}, [], reports

        installed = any(item.get("sdk_available") for item in final_records)
        reader_succeeded = any(item.get("reader_succeeded") for item in final_records)
        manifests = [item for item in final_records if item.get("present")]
        if not installed:
            reason = "C2PA SDK is unavailable for the final artefact, so no cryptographic attestation was assessed."
            return _axis(reasoning=reason), {}, [], reports
        if not reader_succeeded:
            reason = "C2PA SDK was available, but it could not read the final artefact; absence and read failure cannot be conflated."
            return _axis("partial", None, 0.3, reason), {
                "validation_state": "manifest_not_read", "signature_present": None,
                "signature_valid": None, "cryptographically_valid": None,
                "credential_trusted": None, "codes": [], "reasons": [reason]}, [
                    _finding("C2PA_READ_FAILED", reason, final_hash, "low")], reports
        if not manifests:
            reason = "C2PA SDK ran but found no active manifest associated with the final artefact."
            return _axis("available", 0.0, 0.9, reason), {
                "validation_state": "manifest_absent", "signature_present": False,
                "signature_valid": None, "cryptographically_valid": None,
                "credential_trusted": None, "codes": [], "reasons": [reason]}, [], reports
        signature = any(item.get("signature_info") for item in manifests)
        states = [str(item.get("validation_state")) for item in manifests
                  if item.get("validation_state") is not None]
        normalized = {item.casefold() for item in states}
        codes = sorted(set(code for item in manifests
                           for code in C2PAAttestationPass._collect_codes(
                               item.get("validation_results"))))
        state = ("Invalid" if "invalid" in normalized else
                 "Trusted" if "trusted" in normalized else
                 "Valid" if "valid" in normalized else "manifest_parsed")
        if state == "Invalid":
            value, availability, confidence = 0.0, "available", 0.9
            crypto, trusted = False, False
        elif state == "Trusted":
            value, availability, confidence = 1.0, "available", 0.9
            crypto, trusted = True, True
        elif state == "Valid":
            value, availability, confidence = 0.8, "available", 0.9
            crypto, trusted = True, False
        else:
            value, availability, confidence = (0.5 if signature else 0.25), "partial", 0.6
            crypto = trusted = None
        reason = (f"Active C2PA manifest for the final artefact parsed; SDK validation state={state}. "
                  "Manifest claims and ingredient relationships remain claims even when content binding is valid.")
        validation = {"validation_state": state, "signature_present": signature,
                      "signature_valid": crypto, "cryptographically_valid": crypto,
                      "credential_trusted": trusted, "codes": codes, "reasons": [reason]}
        return _axis(availability, value, confidence, reason), validation, [], reports


class AIDisclosurePass:
    """Measure explicit disclosure coverage; never detect AI from content."""

    @staticmethod
    def evaluate(native):
        audio = [node for node in native["artefacts"] if node["artefact_type"].startswith("audio/")
                 or node["artefact_type"] == "audio"]
        if not audio:
            return _axis(reasoning="No applicable audio artefacts were submitted."), []
        declared = [node for node in audio
                    if (node.get("attributes") or {}).get("creation_method") not in (None, "", [], {})]
        value = len(declared) / len(audio)
        availability = "available" if declared else "unavailable"
        axis = _axis(availability, value if declared else None, 0.8 if declared else None,
                     f"Explicit creation_method declarations cover {len(declared)}/{len(audio)} audio artefacts; this is disclosure coverage, not AI detection.")
        findings = [] if len(declared) == len(audio) else [
            _finding("AI_DISCLOSURE_MISSING", "One or more audio artefacts lack creation_method disclosure.", severity="medium")]
        return axis, findings


ASSESSMENT_STATUS_SEMANTICS = [
    {"status": "matched",
     "meaning": "An exact, explicitly scoped check passed on independent validation data.",
     "numeric_treatment": "1.0 when the layer is eligible for integrity scoring."},
    {"status": "corroborated",
     "meaning": "Independent evidence supports the claim but does not prove exact derivation.",
     "numeric_treatment": "Reported separately; never silently scored as an exact match."},
    {"status": "contradicted",
     "meaning": "An applicable assessed claim conflicts with observed evidence.",
     "numeric_treatment": "0.0 in its eligible layer and emits a contradiction finding."},
    {"status": "not_applicable",
     "meaning": "The check exists but the submitted transformation or evidence is outside its reliable scope.",
     "numeric_treatment": "Excluded from the denominator; may request manual review."},
    {"status": "unavailable",
     "meaning": "The claim may be present, but required evidence or declared parameters were absent or unreadable, so the check did not run.",
     "numeric_treatment": "Excluded from the denominator and never treated as zero."},
]


def _layer(name, status, value, contributes, reason, check_count,
           manual_review_reasons=None):
    return {
        "layer": name,
        "status": status,
        "value": value,
        "contributes_to_integrity": contributes,
        "check_count": check_count,
        "reason": reason,
        "manual_review_reasons": sorted(set(manual_review_reasons or [])),
    }


def _boolean_status(checks, successful_status="matched"):
    eligible = [item for item in checks if isinstance(item.get("passed"), bool)]
    if not eligible:
        return "unavailable", None
    value = sum(item["passed"] for item in eligible) / len(eligible)
    return (successful_status if value == 1.0 else "contradicted"), value


def assess_axes(native, chain, bound_hashes, wav_observations,
                relationship_observations, parser_observations, workflow_input):
    file_value, file_checks, file_findings = BoundFileIntegrityPass.evaluate(
        native, bound_hashes, wav_observations, parser_observations)
    rel_value, rel_checks, rel_findings = _relationship_integrity_results(
        relationship_observations)
    cross_value, cross_checks, cross_findings = ExpandedCrossEvidencePass.evaluate(
        native, workflow_input, parser_observations, wav_observations)
    structural_checks = StructuralValidityPass.evaluate(native)
    edit_derivations = EditDerivationPass.evaluate(native, chain)
    comp_derivations = CompDerivationPass.evaluate(native, chain, parser_observations)
    stem_derivations = StemDerivationPass.evaluate(native, chain)
    mix_derivations = AudioDerivationPass.evaluate(native, chain)
    master_derivations = MasteringDerivationPass.evaluate(native, chain)
    audio_alignments = AudioAlignmentPass.evaluate(native, chain)
    source_contributions = SourceContributionPass.evaluate(native, chain)
    comp_verifications = CompVerificationPass.evaluate(
        native, chain, parser_observations)
    processed_audio_matches = ProcessedAudioMatchPass.evaluate(native, chain)
    stem_mix_residuals = StemMixResidualPass.evaluate(native, chain)
    excerpted_from_checks = ExcerptedFromPass.evaluate(native, chain)
    decoy_source_checks = DecoySourcePass.evaluate(source_contributions)
    all_derivations = (edit_derivations + comp_derivations + stem_derivations +
                       mix_derivations + master_derivations)
    # Exact scoped reconstruction and master similarity are different evidence
    # strengths. Corroboration is reported but is not silently scored as proof.
    scored_derivations = [item for item in all_derivations
                          if item["status"] in {"matched", "contradicted"}]
    corroborated_derivations = [item for item in all_derivations
                                if item["status"] == "corroborated"]
    derivation_value = (sum(item["status"] == "matched" for item in scored_derivations) /
                        len(scored_derivations) if scored_derivations else None)
    derivation_findings = [
        _finding("AUDIO_DERIVATION_CONTRADICTION",
                 "The submitted audio contradicts the scoped content-derivation model.",
                 item["target_hash"], "medium", derivation_result=item)
        for item in scored_derivations if item["status"] == "contradicted"]
    physical_checks = (comp_verifications + processed_audio_matches +
                       stem_mix_residuals + excerpted_from_checks)
    physical_findings = [
        _finding("PHYSICAL_AUDIO_CONTRADICTION",
                 "A bounded signal-content check contradicts the submitted relationship claim.",
                 item.get("target_hash"), "medium", physical_audio_result=item)
        for item in physical_checks if item.get("status") == "contradicted"
    ]
    physical_findings.extend(
        _finding("DECLARED_SOURCE_NOT_SUPPORTED",
                 "A declared source has no measurable unique contribution under the bounded leave-one-out model.",
                 item.get("target_hash"), "low", source_contribution_result=item)
        for item in source_contributions if item.get("status") == "not_supported"
    )
    physical_findings.extend(
        _finding("DECOY_SOURCE_SUSPECTED",
                 "A declared source can be removed without materially changing the bounded reconstruction.",
                 item.get("target_hash"), "medium", decoy_source_result=item)
        for item in decoy_source_checks if item.get("status") == "suspected_decoy"
    )
    (attestation, validation, attestation_findings,
     c2pa_attestation_records) = C2PAAttestationPass.evaluate(
         native, parser_observations)
    ai, ai_findings = AIDisclosurePass.evaluate(native)

    file_status, file_layer_value = _boolean_status(file_checks)
    declaration_status, declaration_layer_value = _boolean_status(rel_checks)
    cross_status, cross_layer_value = _boolean_status(
        cross_checks, successful_status="corroborated")
    if any(item["status"] == "contradicted" for item in scored_derivations):
        content_status = "contradicted"
    elif scored_derivations:
        content_status = "matched"
    elif corroborated_derivations:
        content_status = "corroborated"
    elif any(item["status"] == "not_applicable" for item in all_derivations):
        content_status = "not_applicable"
    else:
        content_status = "unavailable"
    content_layer_value = derivation_value
    crypto = validation.get("cryptographically_valid")
    if crypto is False:
        cryptographic_status = "contradicted"
    elif crypto is True:
        cryptographic_status = "corroborated"
    elif validation.get("validation_state") == "manifest_absent":
        cryptographic_status = "not_applicable"
    else:
        cryptographic_status = "unavailable"

    review_reasons = [reason for item in all_derivations
                      for reason in item.get("manual_review_reasons", [])]
    layers = [
        _layer("file_integrity", file_status, file_layer_value, True,
               "SHA-256 binding, cached WAV technical checks, and parser readability of submitted files.",
               len(file_checks)),
        _layer("structural_validity", "matched", 1.0, False,
               "Graph, references, cycles, connectivity, and endpoint roles passed before assessment; this is not content proof.",
               len(structural_checks)),
        _layer("declaration_consistency", declaration_status,
               declaration_layer_value, True,
               "Submitted relationship parameters compared with cached source and target media observations.",
               len(rel_checks)),
        _layer("content_reconstruction", content_status, content_layer_value,
               True,
               "Only submitter-declared ranges and gains are assessed. Missing parameters are unavailable, not adverse; Master continuity corroboration remains unscored.",
               len(all_derivations), review_reasons),
        _layer("cross_evidence_support", cross_status, cross_layer_value, True,
               "Independent parser outputs compared with graph, declarations, and media.",
               len(cross_checks)),
        _layer("cryptographic_attestation", cryptographic_status,
               (attestation.get("value")
                if cryptographic_status in {"corroborated", "contradicted"} else None), False,
               "C2PA remains in the separate attestation-strength axis and never inflates integrity.",
               1 if validation else 0),
    ]
    components = [item["value"] for item in layers
                  if item["contributes_to_integrity"] and item["value"] is not None]
    contradiction_present = any(
        item["contributes_to_integrity"] and item["status"] == "contradicted"
        for item in layers)
    substantive_support = (content_layer_value is not None or
                           cross_layer_value is not None)
    if not components:
        integrity_availability, integrity_value, integrity_confidence = "unavailable", None, None
    else:
        integrity_availability = "available" if len(components) == 4 else "partial"
        # File/declaration consistency alone would create a misleading perfect-
        # looking score without content or independent cross-evidence support.
        integrity_value = (sum(components) / len(components)
                           if ((len(components) >= 2 and substantive_support) or
                               contradiction_present) else None)
        integrity_confidence = round(0.8 * len(components) / 4, 3)
    integrity = _axis(
        integrity_availability, integrity_value, integrity_confidence,
        (f"Integrity has {len(components)}/4 score-eligible evidence layers; missing evidence reduces availability and confidence but is never treated as contradiction. A numeric value is withheld "
         "without content reconstruction or independent CrossEvidence unless a contradiction exists. "
         "Structural validity and cryptographic attestation are reported separately and do not raise integrity. "
         f"{len(corroborated_derivations)} Master/content corroboration result(s) were not scored as exact matches."))

    review_findings = []
    if review_reasons:
        review_findings.append(_finding(
            "MANUAL_REVIEW_RECOMMENDED",
            "One or more content checks are ambiguous, insufficiently covered, or outside the supported model.",
            severity="low", review_reasons=sorted(set(review_reasons))))
    checks = {
        "bound_file_integrity": file_checks,
        "structural_validity": structural_checks,
        "relationship_integrity": rel_checks,
        "integrity_layers": layers,
        "assessment_status_semantics": ASSESSMENT_STATUS_SEMANTICS,
        "cross_evidence": cross_checks,
        "c2pa_attestation_records": c2pa_attestation_records,
        "edit_derivation": edit_derivations,
        "comp_derivation": comp_derivations,
        "stem_derivation": stem_derivations,
        # Retained for compatibility: this key remains the mixed_from linear-mix pass.
        "audio_derivation": mix_derivations,
        "master_derivation": master_derivations,
        "audio_alignment": audio_alignments,
        "source_contribution": source_contributions,
        "comp_verification": comp_verifications,
        "processed_audio_match": processed_audio_matches,
        "stem_mix_residual": stem_mix_residuals,
        "mastering_derivation": master_derivations,
        "excerpted_from": excerpted_from_checks,
        "decoy_source": decoy_source_checks,
    }
    findings = (file_findings + rel_findings + cross_findings + derivation_findings +
                physical_findings + review_findings +
                attestation_findings + ai_findings)
    return integrity, attestation, ai, validation, checks, findings
