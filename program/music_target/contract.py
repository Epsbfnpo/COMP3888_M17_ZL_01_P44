"""Convert the CSEC exchange format into the library's native graph format."""

import copy
import difflib
import hashlib
import json
import math
import re
import uuid
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from music_target import VERSION, domain
from music_target.engine import InputError, Unsupported, empty_axes

BASE = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ANY_CASE_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_NODES = 128

# Findings that mean an input problem made some checks unavailable.
INPUT_ERROR_CODES = frozenset({
    "INVALID_HASH_FORMAT", "INVALID_OBJECT_PATH", "UNKNOWN_FIELD",
    "OBJECT_HASH_MISMATCH", "OBJECT_SKIPPED_SIZE_LIMIT", "OBJECT_PATH_INVALID",
    "OBJECT_CHANGED_DURING_EVALUATION",
})


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise InputError("Input JSON exceeds 2 MiB.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InputError("Duplicate JSON object keys are not permitted.")
            result[key] = value
        return result

    def invalid_number(value):
        raise InputError("Non-finite JSON numbers are not permitted.")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            invalid_number(value)
        return number

    return json.loads(path.read_text(), object_pairs_hook=pairs,
                      parse_constant=invalid_number, parse_float=finite_float)


def validate(document, schema_name):
    # Direct Python callers do not necessarily pass through read_json().
    def check_numbers(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise InputError("Non-finite JSON numbers are not permitted.")
        if isinstance(value, dict):
            for item in value.values():
                check_numbers(item)
        elif isinstance(value, list):
            for item in value:
                check_numbers(item)
    check_numbers(document)
    schema = json.loads((BASE / "schemas" / schema_name).read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    error = next(validator.iter_errors(document), None)
    if error is not None:
        location = "/".join(map(str, error.absolute_path)) or "<root>"
        # Do not interpolate arbitrary user objects into protocol errors.
        raise InputError(f"Schema validation failed at {location} ({error.validator}).")


@lru_cache(maxsize=1)
def source_revision():
    """Hash the installed source tree once per process."""
    digest = hashlib.sha256()
    files = [BASE / name for name in ["run.py", "make_examples.py", "check_public.py",
                                      "make_audio_diagnostic_example.py"]]
    files.extend(BASE.glob("requirements*.txt"))
    files.extend((BASE / "music_target/audio_diagnostics/vendor").glob("*.json"))
    files.extend((BASE / "music_target/audio_diagnostics/vendor").rglob("LICENSE"))
    for folder, pattern in [("music_target", "*.py"), ("vendor", "*.py"),
                            ("schemas", "*.json"), ("legacy_parsers", "*.py"),
                            ("policies", "*.json")]:
        files.extend((BASE / folder).rglob(pattern))
    files.extend((BASE / "schemas").rglob("*.xsd"))
    for path in sorted(files):
        digest.update(str(path.relative_to(BASE)).encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


# Public media types evaluated as audio. The file content decides the
# container; the declared type only selects the evaluator's audio role.
PUBLIC_AUDIO_TYPES = frozenset({"audio/wav", "audio/flac", "audio/mpeg"})

# Used only when a request carries no timeout of its own (for example
# --bundle mode). Callers that send options.timeout_seconds are not capped.
DEFAULT_TIMEOUT_SECONDS = 60


def public_bundle_request(bundle):
    validate(bundle, "public-bundle.schema.json")
    suffixes = {"audio/wav": "wav", "audio/flac": "flac", "audio/mpeg": "mp3",
                "image/png": "png", "video/mp4": "mp4"}
    objects = {}
    for artefact in bundle["artefacts"]:
        suffix = suffixes.get(artefact["type"])
        if suffix:
            objects[artefact["hash"]] = f"objects/{artefact['hash']}.{suffix}"
    return {"schema_version": "0.2-candidate", "run_id": str(uuid.uuid4()),
            "case_id": bundle["case_id"], "profile": "csec-public-bundle-v0.2-candidate",
            "bundle": bundle, "objects": objects,
            "options": {"include_reasoning": True, "offline_only": True,
                        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS}}


def identity(request, mode):
    try:
        run_id, case_id = request["run_id"], request["case_id"]
        uuid.UUID(run_id)
        digest = (request["chain"]["final_artefact_hash"] if mode == "native"
                  else request["bundle"]["final-artefact"])
        if not isinstance(case_id, str) or not case_id or not SHA256.fullmatch(digest):
            raise ValueError("invalid identity")
        return run_id, case_id, digest
    except (KeyError, ValueError, TypeError, AttributeError):
        hint = ""
        try:
            digest = (request["chain"]["final_artefact_hash"] if mode == "native"
                      else request["bundle"]["final-artefact"])
            if isinstance(digest, str) and ANY_CASE_SHA256.fullmatch(digest):
                hint = " The final SHA-256 must be written in lower-case hex."
        except (KeyError, TypeError):
            pass
        raise InputError("A valid run_id, case_id and final SHA-256 are needed to form a scoring response."
                         + hint) from None


def response_shell(request, mode, include_source_revision=True):
    run_id, case_id, digest = identity(request, mode)
    return {"schema_version": "0.2-candidate", "run_id": run_id, "case_id": case_id,
            "asset_sha256": digest,
            "system": {"name": "COMP3888-music-baseline", "version": VERSION,
                       "source_revision": (source_revision()
                                           if include_source_revision else None)},
            "execution_status": "error", "axes": empty_axes(),
            "validation": {"validation_state": None, "signature_present": None,
                           "signature_valid": None, "cryptographically_valid": None,
                           "credential_trusted": None, "codes": [], "reasons": []},
            "findings": [], "processing_ms": None,
            "resource_usage": {"cpu_ms": None, "peak_rss_kib": None}, "error": None}


def _coverage(submitted_artefacts, evaluated_artefacts,
              submitted_relationships, evaluated_relationships):
    return {
        "complete": (submitted_artefacts == evaluated_artefacts
                     and submitted_relationships == evaluated_relationships),
        "submitted_artefacts": submitted_artefacts,
        "evaluated_artefacts": evaluated_artefacts,
        "skipped_artefacts": submitted_artefacts - evaluated_artefacts,
        "submitted_relationships": submitted_relationships,
        "evaluated_relationships": evaluated_relationships,
        "skipped_relationships": submitted_relationships - evaluated_relationships,
    }


def _relationship_endpoints_valid(relationship, target_type, source_type):
    if relationship == "draft-of.audio":
        return target_type == source_type == "audio"
    target_types, source_types = domain.ENDPOINTS[relationship]
    return target_type in target_types and source_type in source_types


def _normalise_native_chain(chain):
    """Quarantine locally identifiable unknown values without guessing replacements."""
    nodes = chain["artefacts"]
    if len(nodes) > MAX_NODES:
        raise Unsupported("At most 128 artefacts are supported per request.")
    hashes = [node["artefact_hash"] for node in nodes]
    if len(set(hashes)) != len(hashes):
        raise InputError("Duplicate artefact hashes are not permitted.")
    if chain["final_artefact_hash"] not in hashes:
        raise InputError("The final artefact hash does not refer to a declared node.")

    hash_set = set(hashes)
    relationship_count = 0
    for node in nodes:
        edges = node.get("evidence", [])
        relationship_count += len(edges)
        parents = [edge["hash"] for edge in edges]
        if len(set(parents)) != len(parents):
            raise InputError("Multiple relationships to the same evidence hash would be overwritten by this library.")
        dangling = [edge["hash"] for edge in edges if edge["hash"] not in hash_set]
        if dangling:
            lowered = {digest.lower() for digest in hash_set}
            hint = (" The hash differs from a declared node only in letter case; hashes are "
                    "not corrected, so use the same lower-case value everywhere."
                    if any(str(item).lower() in lowered for item in dangling) else "")
            raise InputError("An evidence relationship references an undeclared node." + hint)

    by_hash = {node["artefact_hash"]: node for node in nodes}
    final_hash = chain["final_artefact_hash"]
    unknown_hashes = {
        node["artefact_hash"] for node in nodes
        if node["artefact_type"] not in domain.SUPPORTED_TYPES
    }
    if final_hash in unknown_hashes:
        final_type = by_hash[final_hash]["artefact_type"]
        raise Unsupported(
            f"The final artefact has unregistered type {final_type!r}; no evaluation root can be formed."
        )

    originally_reachable, pending = set(), [final_hash]
    while pending:
        digest = pending.pop()
        if digest in originally_reachable:
            continue
        originally_reachable.add(digest)
        pending.extend(edge["hash"] for edge in by_hash[digest].get("evidence", []))
    disconnected_valid = (hash_set - unknown_hashes) - originally_reachable
    if disconnected_valid:
        raise InputError("All declared supported artefacts must be connected to the final artefact.")

    unknown_inputs = []
    diagnostics = []
    for node in nodes:
        if node["artefact_hash"] not in unknown_hashes:
            continue
        item = {"kind": "artefact_type", "value": node["artefact_type"],
                "action": "skipped", "artefact_hash": node["artefact_hash"]}
        unknown_inputs.append(item)
        diagnostics.append({
            "code": "UNKNOWN_ARTEFACT_TYPE_SKIPPED", "severity": "medium",
            "message": (f"Artefact type {node['artefact_type']!r} is not registered; "
                        "the artefact was quarantined without type inference."),
            "evidence_hash": node["artefact_hash"],
            "submitted_value": node["artefact_type"], "action": "skipped",
        })

    retained = []
    evaluated_relationships = 0
    for node in nodes:
        target_hash = node["artefact_hash"]
        if target_hash in unknown_hashes:
            for edge in node.get("evidence", []):
                if edge["relationship_type"] not in domain.SUPPORTED_RELATIONSHIPS:
                    item = {"kind": "relationship_type", "value": edge["relationship_type"],
                            "action": "skipped", "source_hash": edge["hash"],
                            "target_hash": target_hash}
                    unknown_inputs.append(item)
                    diagnostics.append({
                        "code": "UNKNOWN_RELATIONSHIP_TYPE_SKIPPED", "severity": "medium",
                        "message": (f"Relationship type {edge['relationship_type']!r} is not registered; "
                                    "the relationship was skipped without inference."),
                        "evidence_hash": target_hash,
                        "source_hash": edge["hash"], "target_hash": target_hash,
                        "submitted_value": edge["relationship_type"], "action": "skipped",
                    })
                else:
                    diagnostics.append({
                        "code": "RELATIONSHIP_SKIPPED_QUARANTINED_ENDPOINT", "severity": "medium",
                        "message": "The relationship was skipped because its target artefact was quarantined.",
                        "evidence_hash": target_hash,
                        "source_hash": edge["hash"], "target_hash": target_hash,
                        "relationship_type": edge["relationship_type"], "action": "skipped",
                    })
            continue

        retained_node = copy.deepcopy(node)
        retained_edges = []
        for edge in node.get("evidence", []):
            relationship = edge["relationship_type"]
            source_hash = edge["hash"]
            if relationship not in domain.SUPPORTED_RELATIONSHIPS:
                item = {"kind": "relationship_type", "value": relationship,
                        "action": "skipped", "source_hash": source_hash,
                        "target_hash": target_hash}
                unknown_inputs.append(item)
                diagnostics.append({
                    "code": "UNKNOWN_RELATIONSHIP_TYPE_SKIPPED", "severity": "medium",
                    "message": (f"Relationship type {relationship!r} is not registered; "
                                "the relationship was skipped without inference."),
                    "evidence_hash": target_hash,
                    "source_hash": source_hash, "target_hash": target_hash,
                    "submitted_value": relationship, "action": "skipped",
                })
                continue
            if source_hash in unknown_hashes:
                diagnostics.append({
                    "code": "RELATIONSHIP_SKIPPED_QUARANTINED_ENDPOINT", "severity": "medium",
                    "message": "The relationship was skipped because its source artefact was quarantined.",
                    "evidence_hash": target_hash,
                    "source_hash": source_hash, "target_hash": target_hash,
                    "relationship_type": relationship, "action": "skipped",
                })
                continue
            source_type = by_hash[source_hash]["artefact_type"]
            target_type = node["artefact_type"]
            if not _relationship_endpoints_valid(relationship, target_type, source_type):
                diagnostics.append({
                    "code": "RELATIONSHIP_ENDPOINTS_SKIPPED", "severity": "medium",
                    "message": (f"Relationship {relationship!r} does not permit the submitted "
                                f"{source_type!r} to {target_type!r} endpoint roles; it was skipped."),
                    "evidence_hash": target_hash,
                    "source_hash": source_hash, "target_hash": target_hash,
                    "relationship_type": relationship, "action": "skipped",
                })
                continue
            retained_edges.append(copy.deepcopy(edge))
            evaluated_relationships += 1
        retained_node["evidence"] = retained_edges
        retained.append(retained_node)

    normalised = copy.deepcopy(chain)
    normalised["artefacts"] = retained
    coverage = _coverage(len(nodes), len(retained), relationship_count,
                         evaluated_relationships)
    if not coverage["complete"]:
        diagnostics.append({
            "code": "PARTIAL_INPUT_COVERAGE", "severity": "medium",
            "message": (f"Evaluation used {coverage['evaluated_artefacts']}/"
                        f"{coverage['submitted_artefacts']} artefacts and "
                        f"{coverage['evaluated_relationships']}/"
                        f"{coverage['submitted_relationships']} relationships."),
        })
    return normalised, {"coverage": coverage, "unknown_inputs": unknown_inputs,
                        "findings": diagnostics}


@lru_cache(maxsize=1)
def _known_fields():
    """Field names the native schema documents; anything else is unknown."""
    schema = json.loads((BASE / "schemas" / "native-request.schema.json").read_text())
    artefact = schema["properties"]["chain"]["properties"]["artefacts"]["items"]
    attributes = artefact["properties"]["attributes"]["properties"]
    edge = artefact["properties"]["evidence"]["items"]["properties"]["attributes"]["properties"]
    return (frozenset(attributes), frozenset(attributes["technical"]["properties"]),
            frozenset(edge))


def _input_finding(code, message, *, location, suggestion=None, digest=None,
                   submitted=None, affected=None, severity="medium"):
    item = {"code": code, "severity": severity, "message": message, "location": location}
    if digest is not None and SHA256.fullmatch(str(digest)):
        item["evidence_hash"] = digest
    elif digest is not None:
        item["submitted_hash"] = str(digest)[:80]
    if submitted is not None:
        item["submitted_value"] = str(submitted)[:200]
    if suggestion:
        item["suggestion"] = suggestion
    if affected:
        item["affected_checks"] = affected
    return item


def _suggest(name, known):
    close = difflib.get_close_matches(str(name), sorted(known), n=1, cutoff=0.6)
    return f"Did you mean '{close[0]}'?" if close else "Remove the field."


def _mapping_issues(digest, objects, location_prefix, local_file_references=False):
    """Hash-case and object-path problems for one node; nothing is corrected."""
    issues = []
    if not SHA256.fullmatch(digest):
        issues.append(_input_finding(
            "INVALID_HASH_FORMAT",
            "SHA-256 values must be 64 lower-case hex characters; the hash was not corrected.",
            location=location_prefix, digest=digest,
            suggestion="Write the hash in lower case everywhere it appears.",
            affected="checks that use this artefact's file"))
    relative = objects.get(digest)
    if relative is None:
        alternatives = [key for key in objects if key.lower() == digest.lower()]
        if alternatives:
            issues.append(_input_finding(
                "INVALID_HASH_FORMAT",
                "The objects map names this artefact with different letter case; "
                "the mapping was not corrected, so the file is not bound.",
                location=f"objects/{alternatives[0][:80]}", digest=digest,
                submitted=alternatives[0],
                suggestion="Use the same lower-case hash as the artefact declaration.",
                affected="checks that use this artefact's file"))
        return issues
    if local_file_references:
        return issues
    path = Path(relative)
    extension = path.suffix[1:]
    if extension and extension != extension.lower():
        issues.append(_input_finding(
            "INVALID_OBJECT_PATH",
            f"Object file extension '.{extension}' must be lower case; the path was not corrected.",
            location=f"objects/{digest[:80]}", digest=digest, submitted=relative,
            suggestion=f"Rename the file to use '.{extension.lower()}'.",
            affected="checks that use this artefact's file"))
    if path.stem != digest:
        case_only = path.stem.lower() == digest.lower()
        if case_only and not SHA256.fullmatch(digest):
            # Already reported as INVALID_HASH_FORMAT; the file name is the
            # lower-case form, so this is the same problem seen twice.
            return issues
        issues.append(_input_finding(
            "INVALID_OBJECT_PATH",
            "Content-addressed object filename does not match its map key"
            + (" (letter case differs)." if case_only else "."),
            location=f"objects/{digest[:80]}", digest=digest, submitted=relative,
            suggestion=("Use the lower-case hash as the file name."
                        if case_only else "Name the file after the artefact's SHA-256."),
            affected="checks that use this artefact's file"))
    return issues


def _screen_input_format(request, chain):
    """Find format errors without correcting them.

    The final artefact is screened first: any problem with its identity stops
    the request. Problems on other nodes or relationships are recorded so that
    only the checks that depend on them are skipped.
    """
    chain = copy.deepcopy(chain)
    objects = request["objects"]
    local_file_references = (
        request.get("options", {}).get("local_file_references", False))
    nodes = chain["artefacts"]
    final_hash = chain["final_artefact_hash"]
    final_index = next((index for index, node in enumerate(nodes)
                        if node["artefact_hash"] == final_hash), None)

    if not SHA256.fullmatch(final_hash):
        problem = _input_finding(
            "INVALID_HASH_FORMAT",
            "final_artefact_hash must be 64 lower-case hex characters; it was not corrected.",
            location="chain/final_artefact_hash", digest=final_hash,
            suggestion="Write the hash in lower case everywhere it appears.")
        raise InputError(problem["message"], [problem])
    if final_index is not None:
        final_problems = _mapping_issues(
            final_hash, objects, f"chain/artefacts/{final_index}/artefact_hash",
            local_file_references)
        if final_problems:
            raise InputError(final_problems[0]["message"], final_problems)

    findings, unbound, declaration_nodes, declaration_edges = [], {}, {}, {}
    declared = {node["artefact_hash"].lower() for node in nodes}
    for key in objects:
        if key.lower() not in declared:
            findings.append(_input_finding(
                "UNUSED_OBJECT_MAPPING",
                "This objects entry does not name any declared artefact and was ignored.",
                location=f"objects/{key[:80]}", digest=key, severity="low"))
    for index, node in enumerate(nodes):
        digest = node["artefact_hash"]
        if digest == final_hash:
            continue
        issues = _mapping_issues(
            digest, objects, f"chain/artefacts/{index}/artefact_hash",
            local_file_references)
        if issues:
            findings.extend(issues)
            unbound[digest] = sorted({item["code"] for item in issues})

    artefact_keys, technical_keys, edge_keys = _known_fields()
    for index, node in enumerate(nodes):
        digest = node["artefact_hash"]
        attributes = node.get("attributes")
        unknown = []
        if isinstance(attributes, dict):
            for key in [key for key in attributes if key not in artefact_keys]:
                unknown.append(key)
                findings.append(_input_finding(
                    "UNKNOWN_FIELD", f"Field '{str(key)[:80]}' is not part of the artefact declaration schema.",
                    location=f"chain/artefacts/{index}/attributes/{str(key)[:80]}", digest=digest,
                    suggestion=_suggest(key, artefact_keys),
                    affected="technical-claim and disclosure checks for this artefact"))
                del attributes[key]
            technical = attributes.get("technical")
            if isinstance(technical, dict):
                for key in [key for key in technical if key not in technical_keys]:
                    unknown.append(f"technical.{key}")
                    findings.append(_input_finding(
                        "UNKNOWN_FIELD", f"Field 'technical.{str(key)[:80]}' is not part of the schema.",
                        location=f"chain/artefacts/{index}/attributes/technical/{str(key)[:80]}",
                        digest=digest, suggestion=_suggest(key, technical_keys),
                        affected="technical-claim and disclosure checks for this artefact"))
                    del technical[key]
        if unknown:
            declaration_nodes[digest] = unknown
        for edge_index, edge in enumerate(node.get("evidence", [])):
            edge_attributes = edge.get("attributes")
            if not isinstance(edge_attributes, dict):
                continue
            unknown_edge = [key for key in edge_attributes if key not in edge_keys]
            for key in unknown_edge:
                findings.append(_input_finding(
                    "UNKNOWN_FIELD",
                    f"Field '{str(key)[:80]}' is not part of the relationship declaration schema; "
                    "the declaration was not corrected, so its content checks were not run.",
                    location=f"chain/artefacts/{index}/evidence/{edge_index}/attributes/{str(key)[:80]}",
                    digest=digest, suggestion=_suggest(key, edge_keys),
                    affected="content checks that use this relationship's declared parameters"))
                del edge_attributes[key]
            if unknown_edge:
                declaration_edges[(digest, edge["hash"])] = unknown_edge
    issues = {"unbound_nodes": unbound, "declaration_nodes": declaration_nodes,
              "declaration_edges": declaration_edges}
    return chain, issues, findings


def to_native(request, mode, include_report=False, allow_local_files=False):
    if mode == "native":
        local_file_references = (
            request.get("options", {}).get("local_file_references", False))
        if local_file_references != allow_local_files:
            raise InputError(
                "Native local file references require options.local_file_references=true "
                "and the --allow-local-files command-line option.")
    elif allow_local_files:
        raise InputError("--allow-local-files is restricted to trusted native manifests.")

    validate(request, "native-request.schema.json" if mode == "native"
             else "scoring-request.schema.json")
    if mode == "native":
        if request["profile"] != "music-native-v0.1":
            raise Unsupported("Unknown native music profile.")
        chain = request["chain"]
    else:
        if request["profile"] != "csec-public-bundle-v0.2-candidate":
            raise Unsupported("Unknown CSEC exchange profile.")
        bundle = request["bundle"]
        if bundle["case_id"] != request["case_id"]:
            raise InputError("Request and bundle case_id differ.")
        chain = {"hash_method": "sha256", "final_artefact_hash": bundle["final-artefact"],
                 "artefacts": []}
        for item in bundle["artefacts"]:
            chain["artefacts"].append({
                "artefact_hash": item["hash"],
                "artefact_type": "audio" if item["type"] in PUBLIC_AUDIO_TYPES else item["type"],
                "attributes": {},
                "evidence": [{"hash": edge["hash"],
                              "relationship_type": ("draft-of.audio"
                                                    if edge["relationship"] == "draft-of.audio"
                                                    else edge["relationship"]),
                              "attributes": {}}
                             for edge in item["evidence"]]})
    chain, input_issues, input_findings = _screen_input_format(request, chain)
    chain, report = _normalise_native_chain(chain)
    chain["_input_issues"] = input_issues
    report["findings"] = input_findings + report["findings"]
    return (chain, report) if include_report else chain
