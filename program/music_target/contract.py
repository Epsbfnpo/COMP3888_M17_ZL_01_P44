"""Convert the CSEC exchange format into the library's native graph format."""

import hashlib
import json
import re
import uuid
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


def source_revision():
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


def response_shell(request, mode):
    run_id, case_id, digest = identity(request, mode)
    return {"schema_version": "0.2-candidate", "run_id": run_id, "case_id": case_id,
            "asset_sha256": digest,
            "system": {"name": "COMP3888-music-baseline", "version": VERSION,
                       "source_revision": source_revision()},
            "execution_status": "error", "axes": empty_axes(),
            "validation": {"validation_state": None, "signature_present": None,
                           "signature_valid": None, "cryptographically_valid": None,
                           "credential_trusted": None, "codes": [], "reasons": []},
            "findings": [], "processing_ms": None,
            "resource_usage": {"cpu_ms": None, "peak_rss_kib": None}, "error": None}


def to_native(request, mode):
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
        if any(item["type"] != "audio/wav" for item in bundle["artefacts"]):
            raise Unsupported("This target evaluates WAV audio only; PNG/MP4 are unsupported.")
        chain = {"hash_method": "sha256", "final_artefact_hash": bundle["final-artefact"],
                 "artefacts": []}
        for item in bundle["artefacts"]:
            if any(edge["relationship"] != "draft-of.audio" for edge in item["evidence"]):
                raise Unsupported("Public audio relationship has no agreed mapping in this baseline.")
            chain["artefacts"].append({
                "artefact_hash": item["hash"], "artefact_type": "audio", "attributes": {},
                "evidence": [{"hash": edge["hash"], "relationship_type": "draft-of.audio", "attributes": {}}
                             for edge in item["evidence"]]})
    nodes = chain["artefacts"]
    if len(nodes) > MAX_NODES:
        raise Unsupported("At most 128 artefacts are supported per request.")
    hashes = [node["artefact_hash"] for node in nodes]
    if len(set(hashes)) != len(hashes):
        raise InputError("Duplicate artefact hashes are not permitted.")
    if chain["final_artefact_hash"] not in hashes:
        raise InputError("The final artefact hash does not refer to a declared node.")
    for node in nodes:
        if node["artefact_type"] not in domain.SUPPORTED_TYPES:
            raise Unsupported("Unregistered music artefact type.")
        edges = node.get("evidence", [])
        parents = [edge["hash"] for edge in edges]
        if len(set(parents)) != len(parents):
            raise InputError("Multiple relationships to the same evidence hash would be overwritten by this library.")
        for edge in edges:
            if edge["hash"] not in hashes:
                raise InputError("An evidence relationship references an undeclared node.")
            if edge["relationship_type"] not in domain.SUPPORTED_RELATIONSHIPS:
                raise Unsupported("Unregistered music relationship type.")
    for digest, relative in request["objects"].items():
        if digest not in hashes:
            raise InputError("Object mappings may only name declared artefacts.")
        if Path(relative).stem != digest:
            raise InputError("Content-addressed object filename does not match its map key.")
    return chain
