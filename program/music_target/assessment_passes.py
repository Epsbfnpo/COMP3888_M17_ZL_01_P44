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
    MasterDerivationPass,
    StemDerivationPass,
)
from music_target.midi_parser import parse_midi_file
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
                parsed = {"parser": "wav", "status": "parsed",
                          "data": wav_observations.get(digest)}
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
    """Check physical file binding and parser readability, not declarations."""

    @staticmethod
    def evaluate(native, bound_hashes, wav_observations, parser_observations):
        checks, findings = [], []
        for node in native["artefacts"]:
            digest = node["artefact_hash"]
            checks.append({"rule": "object_hash_bound", "passed": digest in bound_hashes,
                           "evidence_hash": digest})
            parser = parser_observations.get(digest)
            if parser and parser["status"] == "failed":
                checks.append({"rule": "declared_type_parser_succeeds", "passed": False,
                               "evidence_hash": digest})
        passed = sum(item["passed"] for item in checks)
        for item in checks:
            if not item["passed"]:
                findings.append(_finding("FILE_INTEGRITY_CONTRADICTION", item["rule"],
                                         item["evidence_hash"], "medium"))
        value = passed / len(checks) if checks else None
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


class RelationshipIntegrityPass:
    """Check submitted technical and relationship declarations against observations."""

    @staticmethod
    def evaluate(native, wav_observations):
        nodes = {item["artefact_hash"]: item for item in native["artefacts"]}
        checks, findings = [], []
        for node in native["artefacts"]:
            observed = wav_observations.get(node["artefact_hash"])
            if not observed:
                continue
            for key, declared in (node.get("attributes") or {}).get("technical", {}).items():
                if declared is None or key not in observed["observed"]:
                    continue
                actual = observed["observed"][key]
                passed = (math.isclose(declared, actual, abs_tol=0.002, rel_tol=0)
                          if key == "duration_seconds" else declared == actual)
                checks.append({"rule": f"technical.{key}_matches_observed",
                               "passed": passed, "evidence_hash": node["artefact_hash"],
                               "declared": declared, "observed": actual})
                if not passed:
                    findings.append(_finding(
                        "DECLARATION_CONSISTENCY_CONTRADICTION",
                        f"technical.{key} does not match the bound file.",
                        node["artefact_hash"], "medium"))
        for target in native["artefacts"]:
            for edge in target.get("evidence", []):
                source = nodes[edge["hash"]]
                attrs = edge.get("attributes") or {}
                current = []
                source_duration = (wav_observations.get(source["artefact_hash"]) or {}).get("observed", {}).get("duration_seconds")
                target_duration = (wav_observations.get(target["artefact_hash"]) or {}).get("observed", {}).get("duration_seconds")
                if attrs.get("source_start_seconds") is not None and attrs.get("source_end_seconds") is not None:
                    current.append({"rule": "source_time_order_valid",
                                    "passed": attrs["source_end_seconds"] >= attrs["source_start_seconds"]})
                if attrs.get("source_end_seconds") is not None and source_duration is not None:
                    current.append({"rule": "source_time_within_duration",
                                    "passed": attrs["source_end_seconds"] <= source_duration})
                if attrs.get("target_end_seconds") is not None and target_duration is not None:
                    current.append({"rule": "target_time_within_duration",
                                    "passed": attrs["target_end_seconds"] <= target_duration})
                for attr, side, key in (("input_sample_rate_hz", source, "sample_rate_hz"),
                                        ("output_sample_rate_hz", target, "sample_rate_hz"),
                                        ("input_bit_depth", source, "bit_depth"),
                                        ("output_bit_depth", target, "bit_depth")):
                    observed = (wav_observations.get(side["artefact_hash"]) or {}).get("observed", {}).get(key)
                    if attrs.get(attr) is not None and observed is not None:
                        current.append({"rule": f"{attr}_matches_file", "passed": attrs[attr] == observed})
                for item in current:
                    item.update({"source_hash": source["artefact_hash"],
                                 "target_hash": target["artefact_hash"],
                                 "relationship_type": edge["relationship_type"]})
                    if not item["passed"]:
                        findings.append(_finding("RELATIONSHIP_INTEGRITY_CONTRADICTION",
                                                 f"{edge['relationship_type']}: {item['rule']}",
                                                 target["artefact_hash"], "medium"))
                checks.extend(current)
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
                            checks.append({"rule": "ern_isrc_matches_audio_declaration",
                                           "passed": metadata["isrc"].replace("-", "").casefold() ==
                                                     binding["isrc"].replace("-", "").casefold(),
                                           "audio_hash": audio_hash})
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
                    checks.append({"rule": "midi_tempo_matches_declaration",
                                   "passed": math.isclose(float(production["tempo_bpm"]),
                                                          float(tempos[0]["bpm"]), abs_tol=0.01),
                                   "evidence_hash": owner_digest})
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

            c2pa = parsed.get("c2pa") if isinstance(parsed.get("c2pa"), dict) else None
            if c2pa and c2pa.get("present"):
                compatible = {
                    "parentof": {"derived_from", "edited_from", "mastered_from",
                                 "rendered_from", "stemmed_from", "mixed_from", "comped_from"},
                    "componentof": {"mixed_from", "comped_from", "stemmed_from", "input_to"},
                    "inputto": {"input_to", "rendered_from"},
                }
                for ingredient in c2pa.get("ingredients", []):
                    relationship = str(ingredient.get("relationship") or "").replace(
                        "_", "").casefold()
                    for source_hash in ingredient.get("referenced_hashes", []):
                        exists = source_hash in graph_hashes
                        checks.append({"rule": "c2pa_ingredient_hash_exists_in_graph",
                                       "passed": exists, "evidence_hash": owner_digest,
                                       "ingredient_hash": source_hash})
                        if exists and relationship in compatible:
                            relations = {relation for source, target, relation in edges
                                         if source == source_hash and target == owner_digest}
                            checks.append({"rule": "c2pa_ingredient_relationship_matches_graph",
                                           "passed": bool(relations & compatible[relationship]),
                                           "evidence_hash": owner_digest,
                                           "ingredient_hash": source_hash,
                                           "c2pa_relationship": ingredient.get("relationship"),
                                           "graph_relationships": sorted(relations)})
                source_types = [str(item) for item in c2pa.get("digital_source_types", [])]
                ai_claimed = any("trainedalgorithmic" in item.casefold() for item in source_types)
                creation = (nodes[owner_digest].get("attributes") or {}).get("creation_method")
                methods = {creation} if isinstance(creation, str) else set(creation or [])
                if source_types and methods:
                    checks.append({"rule": "c2pa_ai_source_type_matches_creation_method",
                                   "passed": ("ai-generated" in methods) if ai_claimed else True,
                                   "evidence_hash": owner_digest,
                                   "digital_source_types": source_types,
                                   "creation_method": sorted(methods)})

        if "includes_ai_generated_audio" in set((workflow_input or {}).get("modifiers") or []):
            declared_ai = []
            for digest, node in nodes.items():
                creation = (node.get("attributes") or {}).get("creation_method")
                methods = {creation} if isinstance(creation, str) else set(creation or [])
                if "ai-generated" in methods:
                    declared_ai.append(digest)
            checks.append({"rule": "workflow_ai_modifier_matches_audio_declarations",
                           "passed": bool(declared_ai), "audio_hashes": declared_ai})
        value = sum(item["passed"] for item in checks) / len(checks) if checks else None
        findings = [] if value in (None, 1.0) else [
            _finding("CROSS_EVIDENCE_CONTRADICTION",
                     "One or more parsed evidence claims conflict with the submitted graph, declarations, or media.",
                     severity="medium")]
        return value, checks, findings


class C2PAAttestationPass:
    """Separate manifest presence, signature, SDK validation, and trust."""

    @staticmethod
    def evaluate(parser_observations):
        records = []
        for parsed in parser_observations.values():
            if parsed.get("parser") == "c2pa" and parsed.get("data"):
                records.append(parsed["data"])
            if isinstance(parsed.get("c2pa"), dict):
                records.append(parsed["c2pa"])
        installed = any(item.get("sdk_available") for item in records)
        reader_succeeded = any(item.get("reader_succeeded") for item in records)
        manifests = [item for item in records if item.get("present")]
        if not records or not installed:
            return _axis(reasoning="C2PA SDK is unavailable, so no cryptographic attestation was assessed."), {}, []
        if not reader_succeeded:
            reason = "C2PA SDK was available, but it could not read the submitted media; absence and read failure cannot be conflated."
            return _axis("partial", None, 0.3, reason), {
                "validation_state": "manifest_not_read", "signature_present": None,
                "signature_valid": None, "cryptographically_valid": None,
                "credential_trusted": None, "codes": [], "reasons": [reason]}, [
                    _finding("C2PA_READ_FAILED", reason, severity="low")]
        if not manifests:
            return _axis("available", 0.0, 0.9, "C2PA SDK ran but found no active manifest."), {
                "validation_state": "manifest_absent", "signature_present": False,
                "signature_valid": None, "cryptographically_valid": None,
                "credential_trusted": None, "codes": [], "reasons": ["No active manifest found."]}, []
        signature = any(item.get("signature_info") for item in manifests)
        states = [str(item.get("validation_state")) for item in manifests
                  if item.get("validation_state") is not None]
        normalized = {item.casefold() for item in states}

        def collect_codes(value):
            codes = []
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "code" and isinstance(item, str):
                        codes.append(item)
                    else:
                        codes.extend(collect_codes(item))
            elif isinstance(value, list):
                for item in value:
                    codes.extend(collect_codes(item))
            return codes

        codes = sorted(set(code for item in manifests
                           for code in collect_codes(item.get("validation_results"))))
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
        reason = (f"Active C2PA manifest parsed; SDK validation state={state}. "
                  "Manifest claims and ingredient relationships remain claims even when content binding is valid.")
        validation = {"validation_state": state, "signature_present": signature,
                      "signature_valid": crypto, "cryptographically_valid": crypto,
                      "credential_trusted": trusted, "codes": codes, "reasons": [reason]}
        return _axis(availability, value, confidence, reason), validation, []


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
    if not checks:
        return "unavailable", None
    value = sum(item["passed"] for item in checks) / len(checks)
    return (successful_status if value == 1.0 else "contradicted"), value


def assess_axes(native, chain, bound_hashes, wav_observations, parser_observations, workflow_input):
    file_value, file_checks, file_findings = BoundFileIntegrityPass.evaluate(
        native, bound_hashes, wav_observations, parser_observations)
    rel_value, rel_checks, rel_findings = RelationshipIntegrityPass.evaluate(native, wav_observations)
    cross_value, cross_checks, cross_findings = ExpandedCrossEvidencePass.evaluate(
        native, workflow_input, parser_observations, wav_observations)
    structural_checks = StructuralValidityPass.evaluate(native)
    edit_derivations = EditDerivationPass.evaluate(native, chain)
    comp_derivations = CompDerivationPass.evaluate(native, chain, parser_observations)
    stem_derivations = StemDerivationPass.evaluate(native, chain)
    mix_derivations = AudioDerivationPass.evaluate(native, chain)
    master_derivations = MasterDerivationPass.evaluate(native, chain)
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
    attestation, validation, attestation_findings = C2PAAttestationPass.evaluate(parser_observations)
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
               "SHA-256 binding and parser readability of submitted files.", len(file_checks)),
        _layer("structural_validity", "matched", 1.0, False,
               "Graph, references, cycles, connectivity, and endpoint roles passed before assessment; this is not content proof.",
               len(structural_checks)),
        _layer("declaration_consistency", declaration_status,
               declaration_layer_value, True,
               "Submitted technical and relationship parameters compared with bound media observations.",
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
        "edit_derivation": edit_derivations,
        "comp_derivation": comp_derivations,
        "stem_derivation": stem_derivations,
        # Retained for compatibility: this key remains the mixed_from linear-mix pass.
        "audio_derivation": mix_derivations,
        "master_derivation": master_derivations,
    }
    findings = (file_findings + rel_findings + cross_findings + derivation_findings + review_findings +
                attestation_findings + ai_findings)
    return integrity, attestation, ai, validation, checks, findings
