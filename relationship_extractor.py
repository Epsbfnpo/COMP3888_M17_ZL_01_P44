#!/usr/bin/env python3
"""
This module deliberately does not infer authorship or production relationships
from filenames, ordering, metadata similarity, or audio similarity. An edge is
eligible only when it comes from a separate explicit declaration or from a
traceable machine-readable declaration such as a C2PA ingredient. System
suggestions are never inserted as evidence edges.

Eligible declarations are checked against ``music_evidence_types.py``, inserted
into each target artefact's ``evidence`` array, and passed to Ben's
``EvidenceChain`` to load the completed graph.


Input bundle (the team's schema / Ben's graph shape):

    {
      "schema_version": "0.0.3",
      "hash_method": "sha256",
      "final_artefact_hash": "<sha256>",
      "artefacts": [
        {
          "artefact_hash": "<sha256>",
          "artefact_type": "audio/raw-take",
          "attributes": {},
          "evidence": []
        }
      ]
    }

Separate declarations file:

    {
      "relationships": [
        {
          "target": "<target sha256>",
          "source": "<source sha256>",
          "relationship_type": "excerpted_from",
          "attributes": {
            "assertion_origin": "submitter",
            "confirmed_by_submitter": true,
            "source_start_seconds": 42.5,
            "source_end_seconds": 50.5,
            "claim_rationale": "Declared by the submitter."
          }
        }
      ]
    }

"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


BUNDLE_SCHEMA_VERSION = "0.0.3"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RELATIONSHIP_FIELDS = {
    "target", "source", "target_hash", "source_hash",
    "relationship_type", "attributes",
}
DECLARATION_ORIGINS = {
    "submitter", "daw-session", "c2pa-ingredient", "ddex-rin", "ddex-ern",
}
MACHINE_READABLE_ORIGINS = {
    "daw-session", "c2pa-ingredient", "ddex-rin", "ddex-ern",
}


class RelationshipExtractionError(ValueError):
    """A relationship declaration cannot be represented safely in the graph."""


def _load_music_types():
    """Load the sibling type registry without requiring a package install."""
    configured = os.environ.get("MUSIC_EVIDENCE_TYPES")
    candidates = ([Path(configured).expanduser()] if configured else []) + [
        Path(__file__).resolve().with_name("music_evidence_types.py")]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise RelationshipExtractionError(
            "music_evidence_types.py must be beside this file, or its path must "
            "be set in MUSIC_EVIDENCE_TYPES")
    spec = importlib.util.spec_from_file_location("music_evidence_types", path)
    if spec is None or spec.loader is None:
        raise RelationshipExtractionError(f"could not load type registry: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.register_all()
    return module


MUSIC_TYPES = _load_music_types()

from libevchain.evidence_chain import EvidenceChain  # noqa: E402


@dataclass(frozen=True)
class RelationshipClaim:
    """A normalized, validated relationship claim."""

    target_hash: str
    source_hash: str
    relationship_type: str
    attributes: Dict[str, Any]

    def evidence_entry(self) -> dict:
        """Return the exact edge shape consumed by Ben's Evidence.from_dict."""
        return {
            "hash": self.source_hash,
            "relationship_type": self.relationship_type,
            "attributes": copy.deepcopy(self.attributes),
        }

    def report_entry(self) -> dict:
        """Return a form that keeps the target visible outside nested bundles."""
        return {
            "target_hash": self.target_hash,
            "source_hash": self.source_hash,
            "relationship_type": self.relationship_type,
            "attributes": copy.deepcopy(self.attributes),
        }


def _read_json(path: Path) -> Any:
    def reject_duplicate_keys(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RelationshipExtractionError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=reject_duplicate_keys)
    except OSError as exc:
        raise RelationshipExtractionError(f"could not read {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise RelationshipExtractionError(
            f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _validate_bundle_shape(bundle: Any) -> List[Mapping[str, Any]]:
    if not isinstance(bundle, dict):
        raise RelationshipExtractionError("bundle must be a JSON object")
    required = {"schema_version", "hash_method", "final_artefact_hash", "artefacts"}
    missing = required - set(bundle)
    if missing:
        raise RelationshipExtractionError(f"bundle is missing fields: {sorted(missing)}")
    if bundle["schema_version"] != BUNDLE_SCHEMA_VERSION:
        raise RelationshipExtractionError(
            f"schema_version must be {BUNDLE_SCHEMA_VERSION}")
    if bundle["hash_method"] != "sha256":
        raise RelationshipExtractionError("hash_method must be sha256")
    if not _is_sha256(bundle["final_artefact_hash"]):
        raise RelationshipExtractionError("final_artefact_hash must be a lowercase SHA-256")
    artefacts = bundle["artefacts"]
    if not isinstance(artefacts, list) or not artefacts:
        raise RelationshipExtractionError("artefacts must be a non-empty array")
    return artefacts


def _index_artefacts(artefacts: Sequence[Mapping[str, Any]]) -> tuple[dict, dict]:
    """Index by hash and by optional build-time id."""
    by_hash: Dict[str, Mapping[str, Any]] = {}
    aliases: Dict[str, str] = {}
    for position, artefact in enumerate(artefacts):
        if not isinstance(artefact, dict):
            raise RelationshipExtractionError(f"artefacts[{position}] must be an object")
        digest = artefact.get("artefact_hash", artefact.get("hash"))
        artefact_type = artefact.get("artefact_type", artefact.get("type"))
        if not _is_sha256(digest):
            raise RelationshipExtractionError(
                f"artefacts[{position}] has an invalid lowercase SHA-256")
        if digest in by_hash:
            raise RelationshipExtractionError(f"duplicate artefact hash: {digest}")
        if not isinstance(artefact_type, str):
            raise RelationshipExtractionError(
                f"artefacts[{position}] needs artefact_type")
        try:
            MUSIC_TYPES.get_artefact_type(artefact_type)
        except Exception:
            raise RelationshipExtractionError(
                f"artefacts[{position}] has unknown artefact_type: {artefact_type}") from None
        by_hash[digest] = artefact
        aliases[digest] = digest
        artefact_id = artefact.get("id")
        if artefact_id is not None:
            if not isinstance(artefact_id, str) or not artefact_id:
                raise RelationshipExtractionError(
                    f"artefacts[{position}].id must be a non-empty string")
            if artefact_id in aliases:
                raise RelationshipExtractionError(f"duplicate artefact id: {artefact_id}")
            aliases[artefact_id] = digest
    return by_hash, aliases


def _declaration_list(value: Any) -> List[Mapping[str, Any]]:
    if isinstance(value, dict):
        unknown = set(value) - {"relationships"}
        if unknown:
            raise RelationshipExtractionError(
                f"declarations object has unknown fields: {sorted(unknown)}")
        value = value.get("relationships")
    if not isinstance(value, list):
        raise RelationshipExtractionError(
            "declarations must be an array or an object containing relationships")
    if not all(isinstance(item, dict) for item in value):
        raise RelationshipExtractionError("every relationship declaration must be an object")
    return value


def _reference(declaration: Mapping[str, Any], short: str, long: str) -> Any:
    has_short = short in declaration
    has_long = long in declaration
    if has_short == has_long:
        raise RelationshipExtractionError(
            f"relationship must contain exactly one of {short!r} or {long!r}")
    return declaration[short] if has_short else declaration[long]


def _resolve(reference: Any, aliases: Mapping[str, str], field: str) -> str:
    if not isinstance(reference, str) or not reference:
        raise RelationshipExtractionError(f"{field} must be a non-empty hash or id")
    try:
        return aliases[reference]
    except KeyError:
        raise RelationshipExtractionError(
            f"{field} references an unknown artefact: {reference}") from None


def _normalized_attributes(
        value: Any, *, separate_declaration: bool) -> Dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise RelationshipExtractionError("relationship attributes must be an object")
    result = copy.deepcopy(value)

    # A record in the separate declarations file is itself an explicit
    # submitter declaration, so it may safely receive that default. An edge
    # already embedded in a bundle must state its origin explicitly.
    if separate_declaration:
        result.setdefault("assertion_origin", "submitter")
        result.setdefault("confirmed_by_submitter", False)
    elif "assertion_origin" not in result:
        raise RelationshipExtractionError(
            "existing evidence edge must declare attributes.assertion_origin")

    origin = result.get("assertion_origin")
    if origin == "system-suggestion":
        raise RelationshipExtractionError(
            "system-suggestion is not eligible for an evidence edge; keep it "
            "outside the bundle until a creator or machine-readable source declares it")
    if origin not in DECLARATION_ORIGINS:
        raise RelationshipExtractionError(
            f"unsupported relationship assertion_origin: {origin!r}")

    # A machine-origin claim needs a stable location inside the declaring
    # artefact. For C2PA this should identify the manifest/ingredient entry.
    if origin in MACHINE_READABLE_ORIGINS:
        pointer = result.get("reference_pointer")
        if not isinstance(pointer, str) or not pointer.strip():
            raise RelationshipExtractionError(
                f"{origin} relationship declaration requires a non-empty "
                "reference_pointer")
    return result


def extract_relationships(
        artefacts: Sequence[Mapping[str, Any]],
        declarations: Iterable[Mapping[str, Any]]) -> List[RelationshipClaim]:
    """Resolve and validate explicit declarations without inferring new ones."""
    by_hash, aliases = _index_artefacts(artefacts)
    claims: List[RelationshipClaim] = []
    occupied_pairs: Dict[tuple[str, str], str] = {}

    for position, declaration in enumerate(declarations):
        if not isinstance(declaration, dict):
            raise RelationshipExtractionError(
                f"relationships[{position}] must be an object")
        unknown = set(declaration) - RELATIONSHIP_FIELDS
        if unknown:
            raise RelationshipExtractionError(
                f"relationships[{position}] has unknown fields: {sorted(unknown)}")

        target_hash = _resolve(
            _reference(declaration, "target", "target_hash"), aliases, "target")
        source_hash = _resolve(
            _reference(declaration, "source", "source_hash"), aliases, "source")
        if target_hash == source_hash:
            raise RelationshipExtractionError("an artefact cannot be evidence for itself")

        relationship_name = declaration.get("relationship_type")
        if not isinstance(relationship_name, str) or not relationship_name:
            raise RelationshipExtractionError("relationship_type must be a non-empty string")
        try:
            relationship_type = MUSIC_TYPES.get_relationship_type(relationship_name)
        except Exception:
            raise RelationshipExtractionError(
                f"unknown relationship_type: {relationship_name}") from None

        target_name = by_hash[target_hash].get(
            "artefact_type", by_hash[target_hash].get("type"))
        source_name = by_hash[source_hash].get(
            "artefact_type", by_hash[source_hash].get("type"))
        target_type = MUSIC_TYPES.get_artefact_type(target_name)
        source_type = MUSIC_TYPES.get_artefact_type(source_name)
        if not relationship_type.validate_types(target_type, source_type):
            raise RelationshipExtractionError(
                f"invalid endpoints: {target_name} {relationship_name} {source_name}")

        attributes = _normalized_attributes(
            declaration.get("attributes", {}), separate_declaration=True)
        try:
            relationship_type.validate_attributes(attributes)
        except Exception as exc:
            raise RelationshipExtractionError(
                f"invalid attributes for {relationship_name}: {exc}") from None

        pair = (target_hash, source_hash)
        if pair in occupied_pairs:
            raise RelationshipExtractionError(
                "Ben stores one edge per target/source hash pair; "
                f"both {occupied_pairs[pair]} and {relationship_name} cannot be kept")
        occupied_pairs[pair] = relationship_name
        claims.append(RelationshipClaim(
            target_hash=target_hash,
            source_hash=source_hash,
            relationship_type=relationship_name,
            attributes=attributes,
        ))

    return sorted(claims, key=lambda item: (
        item.target_hash, item.relationship_type, item.source_hash))


def apply_relationships(bundle: Mapping[str, Any], declarations: Any) -> dict:
    """Return a copy of a bundle with validated relationship claims inserted."""
    artefacts = _validate_bundle_shape(bundle)
    indexed_artefacts, _ = _index_artefacts(artefacts)
    if bundle["final_artefact_hash"] not in indexed_artefacts:
        raise RelationshipExtractionError(
            "final_artefact_hash does not identify an artefact in the bundle")
    declaration_items = _declaration_list(declarations)
    claims = extract_relationships(artefacts, declaration_items)
    result = copy.deepcopy(bundle)
    result_artefacts = result["artefacts"]
    by_hash = {item["artefact_hash"]: item for item in result_artefacts}

    # Check existing edges before merging so repeated extraction is idempotent.
    occupied: Dict[tuple[str, str], Mapping[str, Any]] = {}
    for target in result_artefacts:
        evidence = target.setdefault("evidence", [])
        if not isinstance(evidence, list):
            raise RelationshipExtractionError("artefact evidence must be an array")
        for edge in evidence:
            if not isinstance(edge, dict):
                raise RelationshipExtractionError("existing evidence entry is malformed")
            required = {"hash", "relationship_type", "attributes"}
            if set(edge) != required:
                raise RelationshipExtractionError(
                    "existing evidence entry must contain exactly hash, "
                    "relationship_type and attributes")

            source_hash = edge["hash"]
            if not _is_sha256(source_hash) or source_hash not in by_hash:
                raise RelationshipExtractionError(
                    f"existing evidence references an unknown artefact: {source_hash!r}")

            relationship_name = edge["relationship_type"]
            try:
                relationship_type = MUSIC_TYPES.get_relationship_type(relationship_name)
            except Exception:
                raise RelationshipExtractionError(
                    f"existing evidence has unknown relationship_type: "
                    f"{relationship_name!r}") from None

            attributes = _normalized_attributes(
                edge["attributes"], separate_declaration=False)
            try:
                relationship_type.validate_attributes(attributes)
            except Exception as exc:
                raise RelationshipExtractionError(
                    f"invalid existing attributes for {relationship_name}: {exc}") from None

            target_type = MUSIC_TYPES.get_artefact_type(target["artefact_type"])
            source_type = MUSIC_TYPES.get_artefact_type(
                by_hash[source_hash]["artefact_type"])
            if not relationship_type.validate_types(target_type, source_type):
                raise RelationshipExtractionError(
                    "invalid existing endpoints: "
                    f"{target['artefact_type']} {relationship_name} "
                    f"{by_hash[source_hash]['artefact_type']}")

            pair = (target["artefact_hash"], source_hash)
            if pair in occupied:
                raise RelationshipExtractionError(
                    "bundle already contains duplicate target/source evidence")
            occupied[pair] = edge

    for claim in claims:
        pair = (claim.target_hash, claim.source_hash)
        edge = claim.evidence_entry()
        existing = occupied.get(pair)
        if existing is not None:
            if existing != edge:
                raise RelationshipExtractionError(
                    "a different relationship already exists for target/source pair "
                    f"{claim.target_hash}/{claim.source_hash}")
            continue
        by_hash[claim.target_hash]["evidence"].append(edge)
        occupied[pair] = edge

    # Ben resolves source hashes, validates attributes and rejects cycles.
    try:
        EvidenceChain.from_dict(result)
    except Exception as exc:
        raise RelationshipExtractionError(
            f"Ben's EvidenceChain rejected the extracted graph: {exc}") from None
    return result


def extraction_report(
        artefacts: Sequence[Mapping[str, Any]], declarations: Any) -> dict:
    """Return normalized relationships without building a complete bundle."""
    claims = extract_relationships(artefacts, _declaration_list(declarations))
    return {
        "relationship_count": len(claims),
        "relationships": [claim.report_entry() for claim in claims],
        "inference_used": False,
    }


def example_inputs() -> dict:
    source_hash = "1" * 64
    target_hash = "2" * 64
    return {
        "bundle": {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "hash_method": "sha256",
            "final_artefact_hash": target_hash,
            "artefacts": [
                {
                    "artefact_hash": source_hash,
                    "artefact_type": "audio/raw-take",
                    "attributes": {
                        "technical": {"duration_seconds": 60.0},
                        "production": {"track_name": "Original vocal"},
                    },
                    "evidence": [],
                },
                {
                    "artefact_hash": target_hash,
                    "artefact_type": "audio/sample",
                    "attributes": {
                        "technical": {"duration_seconds": 8.0},
                        "production": {"sample_name": "Vocal phrase"},
                    },
                    "evidence": [],
                },
            ],
        },
        "declarations": {
            "relationships": [{
                "target": target_hash,
                "source": source_hash,
                "relationship_type": "excerpted_from",
                "attributes": {
                    "assertion_origin": "submitter",
                    "confirmed_by_submitter": True,
                    "source_start_seconds": 42.5,
                    "source_end_seconds": 50.5,
                    "target_start_seconds": 0.0,
                    "claim_rationale": "Declared by the submitter.",
                },
            }],
        },
    }


def self_test() -> dict:
    example = example_inputs()
    output = apply_relationships(example["bundle"], example["declarations"])
    target = output["artefacts"][1]
    edge = target["evidence"][0]
    if edge["relationship_type"] != "excerpted_from":
        raise RuntimeError("relationship was not inserted")
    if edge["attributes"].get("source_start_seconds") != 42.5:
        raise RuntimeError("relationship attributes were not preserved")

    # No declaration means no relationship is invented.
    no_declaration = apply_relationships(
        example["bundle"], {"relationships": []})
    if any(item["evidence"] for item in no_declaration["artefacts"]):
        raise RuntimeError("a relationship was inferred without a declaration")

    # A traceable C2PA ingredient declaration is eligible.
    c2pa_declaration = copy.deepcopy(example["declarations"])
    c2pa_attributes = c2pa_declaration["relationships"][0]["attributes"]
    c2pa_attributes["assertion_origin"] = "c2pa-ingredient"
    c2pa_attributes["confirmed_by_submitter"] = False
    c2pa_attributes["reference_pointer"] = "manifests[active].ingredients[0]"
    c2pa_output = apply_relationships(example["bundle"], c2pa_declaration)
    if c2pa_output["artefacts"][1]["evidence"][0]["attributes"][
            "assertion_origin"] != "c2pa-ingredient":
        raise RuntimeError("traceable C2PA declaration was not accepted")

    missing_pointer = copy.deepcopy(c2pa_declaration)
    del missing_pointer["relationships"][0]["attributes"]["reference_pointer"]
    try:
        apply_relationships(example["bundle"], missing_pointer)
    except RelationshipExtractionError:
        pass
    else:
        raise RuntimeError("untraceable C2PA declaration was accepted")

    suggestion = copy.deepcopy(example["declarations"])
    suggestion["relationships"][0]["attributes"][
        "assertion_origin"] = "system-suggestion"
    try:
        apply_relationships(example["bundle"], suggestion)
    except RelationshipExtractionError:
        pass
    else:
        raise RuntimeError("a system suggestion was inserted as evidence")

    # Reapplying the same declaration must not duplicate an edge.
    repeated = apply_relationships(output, example["declarations"])
    if len(repeated["artefacts"][1]["evidence"]) != 1:
        raise RuntimeError("idempotent extraction duplicated an edge")

    bad_endpoint = copy.deepcopy(example["declarations"])
    bad_endpoint["relationships"][0]["relationship_type"] = "mastered_from"
    try:
        apply_relationships(example["bundle"], bad_endpoint)
    except RelationshipExtractionError:
        pass
    else:
        raise RuntimeError("invalid relationship endpoints were accepted")

    bad_attribute = copy.deepcopy(example["declarations"])
    bad_attribute["relationships"][0]["attributes"][
        "source_start_seconds"] = "not-a-number"
    try:
        apply_relationships(example["bundle"], bad_attribute)
    except RelationshipExtractionError:
        pass
    else:
        raise RuntimeError("invalid relationship attributes were accepted")

    cycle_bundle = copy.deepcopy(example["bundle"])
    cycle_declarations = {"relationships": [
        example["declarations"]["relationships"][0],
        {
            "target": "1" * 64,
            "source": "2" * 64,
            "relationship_type": "derived_from",
            "attributes": {"assertion_origin": "submitter"},
        },
    ]}
    try:
        apply_relationships(cycle_bundle, cycle_declarations)
    except RelationshipExtractionError:
        pass
    else:
        raise RuntimeError("relationship cycle was accepted")

    return {
        "status": "ok",
        "relationship_count": 1,
        "ben_evidence_chain_loaded": True,
        "invalid_endpoint_rejected": True,
        "invalid_attribute_rejected": True,
        "cycle_rejected": True,
        "duplicate_application_is_idempotent": True,
        "no_declaration_produces_no_edge": True,
        "traceable_c2pa_declaration_accepted": True,
        "untraceable_c2pa_declaration_rejected": True,
        "system_suggestion_rejected": True,
        "inference_used": False,
    }


def _write_json(path: Optional[Path], value: Any) -> None:
    encoded = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if path is None:
        print(encoded, end="")
        return
    path.write_text(encoded, encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", type=Path,
                        help="input evidence bundle JSON")
    parser.add_argument("declarations", nargs="?", type=Path,
                        help="explicit relationship declarations JSON")
    parser.add_argument("-o", "--output", type=Path,
                        help="output bundle path; stdout when omitted")
    parser.add_argument("--report", action="store_true",
                        help="output normalized relationships instead of a bundle")
    parser.add_argument("--example", action="store_true",
                        help="print example bundle and declarations")
    parser.add_argument("--self-test", action="store_true",
                        help="run extraction and Ben-library validation checks")
    args = parser.parse_args(argv)

    try:
        if args.self_test:
            if args.bundle or args.declarations or args.report:
                parser.error("--self-test cannot be combined with input files or --report")
            _write_json(args.output, self_test())
            return 0
        if args.example:
            if args.bundle or args.declarations or args.report:
                parser.error("--example cannot be combined with input files or --report")
            _write_json(args.output, example_inputs())
            return 0
        if args.bundle is None or args.declarations is None:
            parser.error("bundle and declarations files are required")

        bundle = _read_json(args.bundle)
        declarations = _read_json(args.declarations)
        if args.report:
            artefacts = _validate_bundle_shape(bundle)
            result = extraction_report(artefacts, declarations)
        else:
            result = apply_relationships(bundle, declarations)
        _write_json(args.output, result)
        return 0
    except RelationshipExtractionError as exc:
        print(f"relationship extraction failed: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"relationship extraction failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
