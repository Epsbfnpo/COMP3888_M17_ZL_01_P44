"""Target-owned assessment passes kept separate from workflow completeness."""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

from legacy_parsers.c2pa_parser import parse_c2pa_file
from legacy_parsers.ern_parser import parse_ern_file
from legacy_parsers.rin_parser import parse_rin_file
from music_target.midi_parser import parse_midi_file
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
        flagged = (native.get("_input_issues") or {}).get("declaration_nodes") or {}
        all_audio = [node for node in native["artefacts"] if node["artefact_type"].startswith("audio/")
                     or node["artefact_type"] == "audio"]
        # Declarations with unrecognised fields are not corrected; those nodes
        # are left out of disclosure coverage instead of counting as undisclosed.
        audio = [node for node in all_audio if node["artefact_hash"] not in flagged]
        excluded = len(all_audio) - len(audio)
        if not audio:
            return _axis(reasoning="No applicable audio artefacts were submitted."
                         if not excluded else
                         "Every audio declaration has input errors; disclosure coverage was not assessed."), []
        declared = [node for node in audio
                    if (node.get("attributes") or {}).get("creation_method") not in (None, "", [], {})]
        value = len(declared) / len(audio)
        availability = "available" if declared else "unavailable"
        axis = _axis(availability, value if declared else None, 0.8 if declared else None,
                     f"Explicit creation_method declarations cover {len(declared)}/{len(audio)} audio artefacts; this is disclosure coverage, not AI detection."
                     + (f" {excluded} audio artefact(s) with declaration input errors were not assessed." if excluded else ""))
        findings = [] if len(declared) == len(audio) else [
            _finding("AI_DISCLOSURE_MISSING", "One or more audio artefacts lack creation_method disclosure.", severity="medium")]
        return axis, findings


ASSESSMENT_STATUS_SEMANTICS = [
    {"status": "corroborated",
     "meaning": "Independent evidence clearly supports content consistency, but does not prove provenance direction or authorship.",
     "numeric_treatment": "1.0 in an eligible evidence layer."},
    {"status": "contradicted",
     "meaning": "An applicable assessed claim conflicts with observed evidence.",
     "numeric_treatment": "0.0 in its eligible layer and emits a contradiction finding."},
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


def assess_axes(native, chain, bound_hashes, missing_hashes, wav_observations,
                relationship_observations, parser_observations, workflow_input,
                scoring_profile=None):
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
    declaration_status, declaration_layer_value = _boolean_status(rel_checks)
    cross_status, cross_layer_value = _boolean_status(cross_checks)
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
               "SHA-256 binding, cached WAV technical checks, and parser readability of submitted files.",
               len(file_checks), weight=layer_weights["file_integrity"]),
        _layer("structural_validity", "corroborated", 1.0, False,
               "Graph, references, cycles, connectivity, and endpoint roles passed before assessment; this is not content proof.",
               len(structural_checks)),
        _layer("declaration_consistency", declaration_status,
               declaration_layer_value, True,
               "Submitted relationship parameters compared with cached source and target media observations.",
               len(rel_checks), weight=layer_weights["declaration_consistency"]),
        _layer("physical_similarity", "unavailable", None, False,
               "Legacy signal comparisons are disabled by policy and never contribute to integrity.",
               0),
        _layer("cross_evidence_support", cross_status, cross_layer_value, True,
               "Independent parser outputs compared with graph, declarations, and media.",
               len(cross_checks), weight=layer_weights["cross_evidence_support"]),
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
    substantive_support = cross_layer_value is not None
    min_components = withholding_rule["min_components_for_substantive_mean"]
    require_support = withholding_rule["require_substantive_support_or_contradiction"]
    denominator_layers = confidence_formula["denominator_layers"]
    cap_record = None
    if not components:
        integrity_availability, integrity_value, integrity_confidence = "unavailable", None, None
    else:
        integrity_availability = ("available" if len(components) == denominator_layers
                                  else "partial")
        # File/declaration consistency alone would create a misleading perfect-
        # looking score without content or independent cross-evidence support.
        threshold_met = (len(components) >= min_components and
                         (substantive_support or not require_support))
        if threshold_met or contradiction_present:
            weight_total = sum(item["weight"] for item in scored_layers)
            integrity_value = (sum(item["value"] * item["weight"] for item in scored_layers)
                               / weight_total)
            # A severe contradiction is a ceiling, not an override: it only
            # pulls the weighted mean down toward the cap, never below where
            # it already was, and never raises a lower mean up to meet it.
            if severe_contradiction_rules and severe_contradiction_cap is not None:
                triggered = sorted({
                    item["rule"] for item in file_checks + rel_checks + cross_checks
                    if item.get("passed") is False and item["rule"] in severe_contradiction_rules})
                if triggered:
                    weighted_mean = integrity_value
                    integrity_value = min(integrity_value, severe_contradiction_cap)
                    cap_record = {"cap": severe_contradiction_cap, "triggered_rules": triggered,
                                  "weighted_mean": weighted_mean, "value": integrity_value,
                                  "value_lowered": integrity_value < weighted_mean}
        else:
            integrity_value = None
        integrity_confidence = round(
            confidence_formula["ceiling"] * len(components) / denominator_layers, 3)
    # Describe the withholding rule that actually ran, not a fixed assumption
    # about it. At the shipped default (min_components=2, require_support=
    # True) this reproduces the original fixed sentence verbatim, so a
    # response scored under the default profile is unaffected. Under any
    # other profile, state the configured rule and whether it permitted or
    # withheld a value for *this* assessment -- never why it should have,
    # since the score itself is computed above and unchanged by this text.
    if min_components == 2 and require_support:
        withholding_clause = ("A numeric value is withheld "
            "without independent CrossEvidence unless a contradiction exists. ")
    else:
        if require_support:
            rule_description = (f"requires at least {min_components} populated contributing layer(s) "
                                "including independent CrossEvidence support")
        else:
            rule_description = f"requires at least {min_components} populated contributing layer(s)"
        outcome = "permitted" if integrity_value is not None else "withheld"
        withholding_clause = (
            f"The active scoring profile's withholding rule {rule_description}, unless a contradiction "
            f"exists; it {outcome} a numeric value for this assessment. ")
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
    integrity = _axis(
        integrity_availability, integrity_value, integrity_confidence,
        (f"Integrity has {len(components)}/{denominator_layers} score-eligible evidence layers; missing evidence reduces availability and confidence but is never treated as contradiction. "
         f"{withholding_clause}"
         f"{cap_clause}"
         "Structural validity and cryptographic attestation are reported separately and do not raise integrity. "
         "Legacy signal comparisons are disabled by policy and do not produce findings or affect integrity."))

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
        "relationship_integrity": rel_checks,
        "integrity_layers": layers,
        "assessment_status_semantics": ASSESSMENT_STATUS_SEMANTICS,
        "cross_evidence": cross_checks,
        "c2pa_attestation_records": c2pa_attestation_records,
        "physical_similarity": physical_similarity,
        "physical_similarity_summary": [physical_summary],
    }
    if cap_record:
        checks["severe_contradiction_cap"] = [cap_record]
    findings = (file_findings + rel_findings + cross_findings +
                sample_data_findings +
                attestation_findings + ai_findings)
    return integrity, attestation, ai, validation, checks, findings
