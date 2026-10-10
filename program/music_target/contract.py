"""Convert the CSEC exchange format into the library's native graph format."""

import copy
import hashlib
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from music_target import VERSION, domain
from music_target.engine import InputError, Unsupported, empty_axes

BASE = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_NODES = 128


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

    return json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=invalid_number)


def validate(document, schema_name):
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
    files = [BASE / name for name in ["run.py", "make_examples.py", "check_public.py"]]
    files.extend(BASE.glob("requirements*.txt"))
    for folder, pattern in [("music_target", "*.py"), ("vendor", "*.py"),
                            ("schemas", "*.json"), ("legacy_parsers", "*.py"),
                            ("policies", "*.json")]:
        files.extend((BASE / folder).rglob(pattern))
    files.extend((BASE / "schemas").rglob("*.xsd"))
    for path in sorted(files):
        digest.update(str(path.relative_to(BASE)).encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


def public_bundle_request(bundle):
    validate(bundle, "public-bundle.schema.json")
    suffixes = {"audio/wav": "wav", "image/png": "png", "video/mp4": "mp4"}
    objects = {}
    for artefact in bundle["artefacts"]:
        suffix = suffixes.get(artefact["type"])
        if suffix:
            objects[artefact["hash"]] = f"objects/{artefact['hash']}.{suffix}"
    return {"schema_version": "0.2-candidate", "run_id": str(uuid.uuid4()),
            "case_id": bundle["case_id"], "profile": "csec-public-bundle-v0.2-candidate",
            "bundle": bundle, "objects": objects,
            "options": {"include_reasoning": True, "offline_only": True, "timeout_seconds": 60}}


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
        raise InputError("A valid run_id, case_id and final SHA-256 are needed to form a scoring response.") from None


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
        if any(edge["hash"] not in hash_set for edge in edges):
            raise InputError("An evidence relationship references an undeclared node.")

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


def to_native(request, mode, include_report=False):
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
                "artefact_type": "audio" if item["type"] == "audio/wav" else item["type"],
                "attributes": {},
                "evidence": [{"hash": edge["hash"],
                              "relationship_type": ("draft-of.audio"
                                                    if edge["relationship"] == "draft-of.audio"
                                                    else edge["relationship"]),
                              "attributes": {}}
                             for edge in item["evidence"]]})
    nodes = chain["artefacts"]
    hashes = [node["artefact_hash"] for node in nodes]
    for digest, relative in request["objects"].items():
        if digest not in hashes:
            raise InputError("Object mappings may only name declared artefacts.")
        if Path(relative).stem != digest:
            raise InputError("Content-addressed object filename does not match its map key.")
    chain, report = _normalise_native_chain(chain)
    return (chain, report) if include_report else chain
