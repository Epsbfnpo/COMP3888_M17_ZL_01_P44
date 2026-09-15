#!/usr/bin/env python3
"""Build a validated Ben-compatible music evidence bundle.

The builder is the single ingestion point for artefact files.  It combines:

* objective file identity (SHA-256);
* WAV/RIFF metadata parsed by ``wav_parser.py``;
* DDEX RIN/ERN metadata parsed by ``rin_parser.py`` and ``ern_parser.py``;
* optional embedded C2PA metadata parsed by ``c2pa_parser.py``;
* submitter-declared artefact roles and production attributes; and
* explicit submitter or traceable machine-readable relationship declarations.

It deliberately does not infer an artefact role, the final artefact, a creation
method, or a relationship from filenames, ordering, metadata similarity, or
audio similarity.

Run::

    python3 bundle_builder.py bundle_manifest.json -o evidence_bundle.json
    python3 bundle_builder.py --example
    python3 bundle_builder.py --self-test
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import wave

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:  # pragma: no cover - exercised only without dependency
    raise RuntimeError(
        "bundle_builder.py requires jsonschema; install requirements.txt"
    ) from exc

import music_evidence_types as MUSIC_TYPES
import relationship_extractor as RELATIONSHIPS
from c2pa_parser import (
    C2paAssertion,
    C2paIngredient,
    C2paParseResult,
    C2paSignatureInfo,
    parse_c2pa_file,
)
from ern_parser import ErnParseResult, parse_ern_file
from rin_parser import RinParseResult, parse_rin_file
from wav_parser import WavParseResult, parse_wav_file


BUNDLE_SCHEMA_VERSION = "0.0.3"
HASH_METHOD = "sha256"
MAX_ARTEFACTS = 256

MANIFEST_FIELDS = {"schema_version", "hash_method", "files", "relationships"}
FILE_FIELDS = {
    "id", "path", "artefact_type", "final", "attributes", "parse_c2pa",
}
REQUIRED_FILE_FIELDS = {"id", "path", "artefact_type", "final"}


class BundleBuildError(ValueError):
    """The supplied files or declarations cannot form a safe evidence bundle."""


def _read_json(path: Path) -> Any:
    def reject_duplicate_keys(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BundleBuildError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except OSError as exc:
        raise BundleBuildError(f"could not read {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise BundleBuildError(
            f"invalid JSON in {path} at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from None


def _write_json(path: Optional[Path], value: Any) -> None:
    encoded = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if path is None:
        print(encoded, end="")
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded, encoding="utf-8")
    except OSError as exc:
        raise BundleBuildError(f"could not write {path}: {exc}") from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BundleBuildError(f"could not hash {path}: {exc}") from None
    return digest.hexdigest()


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BundleBuildError(f"{field} must be a non-empty string")
    return value


def _validate_manifest(manifest: Any) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    if not isinstance(manifest, dict):
        raise BundleBuildError("manifest must be a JSON object")
    unknown = set(manifest) - MANIFEST_FIELDS
    if unknown:
        raise BundleBuildError(f"manifest has unknown fields: {sorted(unknown)}")
    if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise BundleBuildError(
            f"manifest schema_version must be {BUNDLE_SCHEMA_VERSION}"
        )
    if manifest.get("hash_method", HASH_METHOD) != HASH_METHOD:
        raise BundleBuildError("manifest hash_method must be sha256")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise BundleBuildError("manifest files must be a non-empty array")
    if len(files) > MAX_ARTEFACTS:
        raise BundleBuildError(f"manifest may contain at most {MAX_ARTEFACTS} files")

    relationships = manifest.get("relationships", [])
    if not isinstance(relationships, list):
        raise BundleBuildError("manifest relationships must be an array")
    if not all(isinstance(item, dict) for item in relationships):
        raise BundleBuildError("every relationship declaration must be an object")
    return files, relationships


def _compact(values: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop absent parser values while retaining valid zero/false values."""
    return {key: value for key, value in values.items() if value is not None}


def _wav_attributes(path: Path, parsed: WavParseResult) -> Dict[str, Any]:
    """Map the objective subset of a WAV parse result into bundle attributes."""
    if parsed.core_format is None:
        detail = "; ".join(parsed.warnings) or "missing or malformed fmt chunk"
        raise BundleBuildError(f"could not parse WAV {path}: {detail}")

    core = parsed.core_format
    technical = _compact({
        "media_type": "audio/wav",
        "duration_seconds": core.duration_seconds,
        "sample_rate_hz": core.sample_rate_hz,
        "channels": core.num_channels,
        "bit_depth": core.bits_per_sample,
        "format": "WAV",
        "codec_subtype": core.audio_format,
        "file_size_bytes": path.stat().st_size,
    })

    source_metadata: Dict[str, Any] = {"original_filename": path.name}
    bext = parsed.broadcast_extension
    if bext is not None:
        source_metadata.update(_compact({
            "description": bext.description,
            "originator": bext.originator,
            "originator_reference": bext.originator_reference,
            "origination_date": bext.origination_date,
            "origination_time": bext.origination_time,
            "time_reference_samples": bext.time_reference,
            "umid": bext.umid,
            "coding_history": bext.coding_history,
        }))

    # Only map INFO fields whose meaning has a direct schema equivalent.  Other
    # parsed tags remain parser diagnostics and are not reinterpreted.
    for key in ("software", "artist", "engineer", "comment"):
        value = parsed.info.get(key)
        if value is not None:
            source_metadata[key] = value

    return {
        "technical": technical,
        "source_metadata": source_metadata,
    }


def _parser_diagnostics(parser_name: str, warnings: Sequence[str]) -> Dict[str, Any]:
    """Keep parser limitations visible without turning them into relationships."""
    return {
        "parser_diagnostics": {
            "parser": parser_name,
            "warnings": list(warnings),
        }
    }


def _reject_unreadable_xml(path: Path, warnings: Sequence[str]) -> None:
    fatal_prefixes = ("malformed XML:", "could not read file:")
    fatal = [item for item in warnings if item.startswith(fatal_prefixes)]
    if fatal:
        raise BundleBuildError(f"could not parse XML {path}: " + "; ".join(fatal))


def _rin_attributes(path: Path, parsed: RinParseResult) -> Dict[str, Any]:
    """Map parser-observed RIN data without claiming full DDEX validation."""
    _reject_unreadable_xml(path, parsed.warnings)
    sessions = [dataclasses.asdict(item) for item in parsed.sessions]
    contributors = [dataclasses.asdict(item) for item in parsed.contributors]
    equipment = [dataclasses.asdict(item) for item in parsed.equipment]
    components = [dataclasses.asdict(item) for item in parsed.recording_components]
    first = parsed.sessions[0] if parsed.sessions else None
    production = _compact({
        "session_id": first.session_id if first else None,
        "session_date": first.date if first else None,
        "location": first.location if first else None,
        "sessions": sessions,
        "contributors": contributors,
        "contributor_roles": [item.role for item in parsed.contributors if item.role],
        "equipment": equipment,
        "recording_components": components,
    })
    return {
        "production": production,
        "source_metadata": {"original_filename": path.name},
        **_parser_diagnostics("ddex-rin", parsed.warnings),
    }


def _ern_attributes(path: Path, parsed: ErnParseResult) -> Dict[str, Any]:
    """Map baseline ERN release data; do not invent an AI declaration."""
    _reject_unreadable_xml(path, parsed.warnings)
    releases = [dataclasses.asdict(item) for item in parsed.releases]
    first = parsed.releases[0] if parsed.releases else None
    result: Dict[str, Any] = {
        "release": _compact({
            "release_id": first.release_id if first else None,
            "title": first.title if first else None,
            "release_type": first.release_type if first else None,
            "releases": releases,
        }),
        "source_metadata": {"original_filename": path.name},
        **_parser_diagnostics("ddex-ern", parsed.warnings),
    }
    if parsed.contains_ai_declared is not None or parsed.ai_contributions:
        result["AI-declaration"] = {
            "value": {
                "contains_ai_declared": parsed.contains_ai_declared,
                "contributions": list(parsed.ai_contributions),
            }
        }
    return result


def _c2pa_attributes(path: Path, parsed: C2paParseResult) -> Dict[str, Any]:
    """Map C2PA SDK output as provenance observations, without trust scoring."""
    provenance = _compact({
        "present": parsed.present,
        "active_manifest_label": parsed.active_manifest_label,
        "assertions": [dataclasses.asdict(item) for item in parsed.assertions],
        "ingredients": [dataclasses.asdict(item) for item in parsed.ingredients],
        "signature_info": (
            dataclasses.asdict(parsed.signature_info)
            if parsed.signature_info is not None else None
        ),
        "validation_status": parsed.validation_status,
    })
    return {
        "provenance": provenance,
        "source_metadata": {"original_filename": path.name},
        **_parser_diagnostics("c2pa", parsed.warnings),
    }


def _ensure_parser_owned_groups_absent(
    declared: Mapping[str, Any], groups: Sequence[str], context: str
) -> None:
    present = sorted(set(declared) & set(groups))
    if present:
        raise BundleBuildError(
            f"{context}.attributes may not declare parser-owned groups: {present}"
        )


def _c2pa_dependency_missing(parsed: C2paParseResult) -> bool:
    return any("c2pa-python is not installed" in item for item in parsed.warnings)


def _separate_audio_claims(declared: Mapping[str, Any], context: str) -> Dict[str, Any]:
    """Keep external claims separate from fields observed in the WAV bytes.

    ``technical`` and ``source_metadata`` in a manifest are accepted as a
    convenient legacy spelling, but they become ``declared_*`` in the canonical
    bundle. The unprefixed groups are reserved for parser observations.
    """
    result = copy.deepcopy(dict(declared))
    for observed_name, claim_name in (
        ("technical", "declared_technical"),
        ("source_metadata", "declared_source_metadata"),
    ):
        if observed_name not in result:
            continue
        if claim_name in result:
            raise BundleBuildError(
                f"{context}.attributes cannot contain both {observed_name!r} "
                f"and {claim_name!r}"
            )
        result[claim_name] = result.pop(observed_name)
    return result


def _merge_observed_attributes(
    declared: Mapping[str, Any], observed: Mapping[str, Any], context: str
) -> Dict[str, Any]:
    """Add parser observations while retaining external claims separately."""
    result = copy.deepcopy(dict(declared))
    for group_name, observed_group in observed.items():
        existing_group = result.get(group_name)
        if existing_group is None:
            existing_group = {}
            result[group_name] = existing_group
        if not isinstance(existing_group, dict):
            raise BundleBuildError(f"{context}.{group_name} must be an object")
        for field, observed_value in observed_group.items():
            existing_group[field] = observed_value
    return result


def _build_artefacts(
    entries: Sequence[Mapping[str, Any]], base_dir: Path
) -> Tuple[List[dict], str]:
    artefacts: List[dict] = []
    seen_ids: set[str] = set()
    seen_paths: set[Path] = set()
    seen_hashes: Dict[str, Path] = {}
    finals: List[str] = []

    for position, entry in enumerate(entries):
        context = f"files[{position}]"
        if not isinstance(entry, dict):
            raise BundleBuildError(f"{context} must be an object")
        unknown = set(entry) - FILE_FIELDS
        missing = REQUIRED_FILE_FIELDS - set(entry)
        if unknown:
            raise BundleBuildError(f"{context} has unknown fields: {sorted(unknown)}")
        if missing:
            raise BundleBuildError(f"{context} is missing fields: {sorted(missing)}")

        artefact_id = _nonempty_string(entry["id"], f"{context}.id")
        if artefact_id in seen_ids:
            raise BundleBuildError(f"duplicate file id: {artefact_id}")
        seen_ids.add(artefact_id)

        path_text = _nonempty_string(entry["path"], f"{context}.path")
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        path = path.resolve()
        if not path.is_file():
            raise BundleBuildError(f"{context}.path is not a file: {path}")
        if path in seen_paths:
            raise BundleBuildError(f"the same file path is listed more than once: {path}")
        seen_paths.add(path)

        artefact_type_name = _nonempty_string(
            entry["artefact_type"], f"{context}.artefact_type"
        )
        try:
            artefact_type = MUSIC_TYPES.get_artefact_type(artefact_type_name)
        except Exception:
            raise BundleBuildError(
                f"{context} has unknown artefact_type: {artefact_type_name}"
            ) from None

        final = entry["final"]
        if type(final) is not bool:
            raise BundleBuildError(f"{context}.final must be true or false")

        declared_attributes = entry.get("attributes", {})
        if not isinstance(declared_attributes, dict):
            raise BundleBuildError(f"{context}.attributes must be an object")

        parse_c2pa = entry.get("parse_c2pa", False)
        if type(parse_c2pa) is not bool:
            raise BundleBuildError(f"{context}.parse_c2pa must be true or false")
        c2pa_type = artefact_type_name == "provenance/c2pa"
        if parse_c2pa and not (artefact_type_name.startswith("audio/") or c2pa_type):
            raise BundleBuildError(
                f"{context}.parse_c2pa is supported only for audio/* or provenance/c2pa"
            )

        observed_attributes: Dict[str, Any] = {}
        if artefact_type_name.startswith("audio/"):
            declared_attributes = _separate_audio_claims(
                declared_attributes, context
            )
            parsed = parse_wav_file(str(path))
            if parsed.warnings:
                raise BundleBuildError(
                    f"WAV parser warnings for {path}: " + "; ".join(parsed.warnings)
                )
            observed_attributes = _wav_attributes(path, parsed)

        elif artefact_type_name == "metadata/ddex-rin":
            _ensure_parser_owned_groups_absent(
                declared_attributes, ("production", "parser_diagnostics"), context
            )
            observed_attributes = _rin_attributes(path, parse_rin_file(str(path)))

        elif artefact_type_name == "metadata/ddex-ern":
            _ensure_parser_owned_groups_absent(
                declared_attributes,
                ("release", "AI-declaration", "parser_diagnostics"),
                context,
            )
            observed_attributes = _ern_attributes(path, parse_ern_file(str(path)))

        elif c2pa_type:
            _ensure_parser_owned_groups_absent(
                declared_attributes, ("provenance", "parser_diagnostics"), context
            )
            parsed_c2pa = parse_c2pa_file(str(path))
            if _c2pa_dependency_missing(parsed_c2pa):
                raise BundleBuildError(parsed_c2pa.warnings[0])
            observed_attributes = _c2pa_attributes(path, parsed_c2pa)

        if parse_c2pa and artefact_type_name.startswith("audio/"):
            _ensure_parser_owned_groups_absent(
                declared_attributes, ("provenance", "parser_diagnostics"), context
            )
            parsed_c2pa = parse_c2pa_file(str(path))
            if _c2pa_dependency_missing(parsed_c2pa):
                raise BundleBuildError(parsed_c2pa.warnings[0])
            observed_attributes = _merge_observed_attributes(
                observed_attributes, _c2pa_attributes(path, parsed_c2pa), context
            )

        attributes = _merge_observed_attributes(
            declared_attributes, observed_attributes, context
        )
        try:
            artefact_type.validate_attributes(attributes)
        except Exception as exc:
            raise BundleBuildError(
                f"invalid attributes for {context} ({artefact_type_name}): {exc}"
            ) from None

        digest = _sha256(path)
        if digest in seen_hashes:
            raise BundleBuildError(
                "two manifest entries have identical content and therefore the same "
                f"artefact hash: {seen_hashes[digest]} and {path}"
            )
        seen_hashes[digest] = path

        artefacts.append({
            # id is a build-time alias for declarations. It is removed before
            # final schema validation and output.
            "id": artefact_id,
            "artefact_hash": digest,
            "artefact_type": artefact_type_name,
            "attributes": attributes,
            "evidence": [],
        })
        if final:
            finals.append(digest)

    if len(finals) != 1:
        raise BundleBuildError(
            f"exactly one file must have final=true; found {len(finals)}"
        )
    hash_values = set(seen_hashes)
    conflicting_ids = sorted(seen_ids & hash_values)
    if conflicting_ids:
        raise BundleBuildError(
            "file ids must not equal an artefact hash: " + ", ".join(conflicting_ids)
        )
    return artefacts, finals[0]


def _schema_path(explicit: Optional[Path] = None) -> Path:
    path = explicit or Path(__file__).resolve().with_name("evidence_bundle_schema.json")
    if not path.is_file():
        raise BundleBuildError(f"evidence bundle schema does not exist: {path}")
    return path


def validate_bundle_schema(bundle: Mapping[str, Any], schema_path: Optional[Path] = None) -> None:
    schema = _read_json(_schema_path(schema_path))
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise BundleBuildError(f"evidence bundle schema is invalid: {exc}") from None

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(bundle), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = "$"
    for component in first.absolute_path:
        location += f"[{component}]" if isinstance(component, int) else f".{component}"
    extra = f" ({len(errors)} schema errors total)" if len(errors) > 1 else ""
    raise BundleBuildError(
        f"built bundle violates evidence_bundle_schema.json at {location}: "
        f"{first.message}{extra}"
    )


def build_bundle(
    manifest: Mapping[str, Any],
    *,
    base_dir: Path,
    schema_path: Optional[Path] = None,
) -> dict:
    """Build, validate, and return one canonical evidence bundle dictionary."""
    MUSIC_TYPES.register_all()
    entries, declarations = _validate_manifest(manifest)
    artefacts, final_hash = _build_artefacts(entries, base_dir.resolve())
    initial_bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "hash_method": HASH_METHOD,
        "final_artefact_hash": final_hash,
        "artefacts": artefacts,
    }

    try:
        completed = RELATIONSHIPS.apply_relationships(
            initial_bundle, {"relationships": declarations}
        )
    except RELATIONSHIPS.RelationshipExtractionError as exc:
        raise BundleBuildError(f"relationship declaration failed: {exc}") from None

    # Build-time aliases are convenient in the manifest but are outside the
    # published bundle schema. All final edges already contain resolved hashes.
    for artefact in completed["artefacts"]:
        artefact.pop("id", None)

    validate_bundle_schema(completed, schema_path)
    # Validate the canonical, alias-free output once more with Ben itself.
    try:
        RELATIONSHIPS.EvidenceChain.from_dict(completed)
    except Exception as exc:
        raise BundleBuildError(f"Ben's EvidenceChain rejected the bundle: {exc}") from None
    return completed


def build_bundle_from_file(
    manifest_path: Path, *, schema_path: Optional[Path] = None
) -> dict:
    manifest_path = manifest_path.expanduser().resolve()
    manifest = _read_json(manifest_path)
    return build_bundle(
        manifest,
        base_dir=manifest_path.parent,
        schema_path=schema_path,
    )


def example_manifest() -> dict:
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "hash_method": HASH_METHOD,
        "files": [
            {
                "id": "raw-vocal",
                "path": "objects/raw-vocal.wav",
                "artefact_type": "audio/raw-take",
                "final": False,
                "attributes": {
                    "creation_method": "recorded",
                    "production": {
                        "track_name": "Lead Vocal Take 3",
                        "source_role": "lead-vocal",
                    },
                },
            },
            {
                "id": "vocal-sample",
                "path": "objects/vocal-sample.wav",
                "artefact_type": "audio/sample",
                "final": True,
                "attributes": {
                    "creation_method": "recorded",
                    "production": {
                        "sample_name": "Vocal phrase",
                        "sample_category": "vocal",
                    },
                },
            },
            {
                "id": "rin-document",
                "path": "objects/session-rin.xml",
                "artefact_type": "metadata/ddex-rin",
                "final": False,
                "attributes": {},
            },
            {
                "id": "ern-document",
                "path": "objects/release-ern.xml",
                "artefact_type": "metadata/ddex-ern",
                "final": False,
                "attributes": {},
            },
        ],
        "relationships": [
            {
                "target": "vocal-sample",
                "source": "raw-vocal",
                "relationship_type": "excerpted_from",
                "attributes": {
                    "assertion_origin": "submitter",
                    "confirmed_by_submitter": True,
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 0.25,
                    "target_start_seconds": 0.0,
                    "claim_rationale": "Declared by the submitter.",
                },
            },
            {
                "target": "vocal-sample",
                "source": "rin-document",
                "relationship_type": "documented_by",
                "attributes": {
                    "assertion_origin": "ddex-rin",
                    "reference_pointer": "Session[S-001]",
                    "document_role": "recording-session",
                },
            },
            {
                "target": "vocal-sample",
                "source": "ern-document",
                "relationship_type": "documented_by",
                "attributes": {
                    "assertion_origin": "ddex-ern",
                    "reference_pointer": "Release[REL-001]",
                    "document_role": "release-message",
                },
            },
        ],
    }


def _write_test_wav(path: Path, *, frames: int, sample_rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def self_test() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        objects = root / "objects"
        objects.mkdir()
        raw = objects / "raw-vocal.wav"
        sample = objects / "vocal-sample.wav"
        rin = objects / "session-rin.xml"
        ern = objects / "release-ern.xml"
        _write_test_wav(raw, frames=8000)
        _write_test_wav(sample, frames=2000)
        rin.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<RecordingInformationNotification xmlns="urn:ddex:test:rin">
  <Session><SessionId>S-001</SessionId><Date>2026-09-11</Date>
    <Location>Sydney</Location>
    <Party><PartyId>P-001</PartyId><Name>Alice</Name><Role>Vocalist</Role></Party>
    <Equipment><EquipmentId>E-001</EquipmentId><Description>Mic</Description>
      <EquipmentType>Microphone</EquipmentType></Equipment>
  </Session>
  <RecordingComponent><ComponentId>RC-001</ComponentId>
    <Title>Lead vocal</Title><Role>Vocal</Role></RecordingComponent>
</RecordingInformationNotification>
""",
            encoding="utf-8",
        )
        ern.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<NewReleaseMessage xmlns="urn:ddex:test:ern">
  <Release><ReleaseId>REL-001</ReleaseId><TitleText>Demo Song</TitleText>
    <ReleaseType>Single</ReleaseType></Release>
</NewReleaseMessage>
""",
            encoding="utf-8",
        )

        manifest = example_manifest()
        bundle = build_bundle(manifest, base_dir=root)
        by_type = {item["artefact_type"]: item for item in bundle["artefacts"]}
        raw_node = by_type["audio/raw-take"]
        sample_node = by_type["audio/sample"]
        rin_node = by_type["metadata/ddex-rin"]
        ern_node = by_type["metadata/ddex-ern"]

        if raw_node["artefact_hash"] != _sha256(raw):
            raise RuntimeError("builder did not use the file's SHA-256")
        if raw_node["attributes"]["technical"]["sample_rate_hz"] != 8000:
            raise RuntimeError("WAV parser metadata was not mapped")
        if raw_node["attributes"]["source_metadata"][
            "original_filename"
        ] != raw.name:
            raise RuntimeError("WAV source metadata was not mapped")
        if any("id" in item for item in bundle["artefacts"]):
            raise RuntimeError("build-time ids leaked into the canonical bundle")
        edge = next(
            item for item in sample_node["evidence"]
            if item["relationship_type"] == "excerpted_from"
        )
        if edge["hash"] != raw_node["artefact_hash"]:
            raise RuntimeError("relationship ids were not resolved to hashes")
        if edge["relationship_type"] != "excerpted_from":
            raise RuntimeError("explicit relationship was not inserted")
        if rin_node["attributes"]["production"]["session_id"] != "S-001":
            raise RuntimeError("RIN parser output was not mapped")
        if ern_node["attributes"]["release"]["release_id"] != "REL-001":
            raise RuntimeError("ERN parser output was not mapped")
        if "AI-declaration" in ern_node["attributes"]:
            raise RuntimeError("ERN parser invented an unsupported AI declaration")
        if len(sample_node["evidence"]) != 3:
            raise RuntimeError("explicit metadata relationships were not inserted")

        no_edges_manifest = copy.deepcopy(manifest)
        no_edges_manifest["relationships"] = []
        no_edges = build_bundle(no_edges_manifest, base_dir=root)
        if any(node["evidence"] for node in no_edges["artefacts"]):
            raise RuntimeError("a relationship was inferred without a declaration")

        conflict_manifest = copy.deepcopy(manifest)
        conflict_manifest["files"][0]["attributes"]["technical"] = {
            "sample_rate_hz": 44100
        }
        conflict_bundle = build_bundle(conflict_manifest, base_dir=root)
        conflict_raw = next(
            node for node in conflict_bundle["artefacts"]
            if node["artefact_type"] == "audio/raw-take"
        )
        if conflict_raw["attributes"]["technical"]["sample_rate_hz"] != 8000:
            raise RuntimeError("the parser observation was overwritten")
        if conflict_raw["attributes"]["declared_technical"][
            "sample_rate_hz"
        ] != 44100:
            raise RuntimeError("the conflicting submitter claim was not preserved")

        # The optional SDK is not required for this deterministic self-test;
        # inject a representative parser result to test the integration map.
        original_c2pa_parser = globals()["parse_c2pa_file"]
        try:
            globals()["parse_c2pa_file"] = lambda path: C2paParseResult(
                source_path=path,
                present=True,
                active_manifest_label="urn:c2pa:test",
                assertions=[C2paAssertion(label="c2pa.actions", data={"action": "created"})],
                ingredients=[C2paIngredient(
                    title="raw-vocal.wav", relationship="parentOf", format="audio/wav"
                )],
                signature_info=C2paSignatureInfo(
                    issuer="test issuer", time="2026-09-11T00:00:00Z", alg="test"
                ),
                validation_status={"signature_valid": True},
            )
            c2pa_manifest = copy.deepcopy(manifest)
            c2pa_manifest["files"][1]["parse_c2pa"] = True
            c2pa_bundle = build_bundle(c2pa_manifest, base_dir=root)
        finally:
            globals()["parse_c2pa_file"] = original_c2pa_parser

        c2pa_sample = next(
            node for node in c2pa_bundle["artefacts"]
            if node["artefact_type"] == "audio/sample"
        )
        if c2pa_sample["attributes"]["provenance"]["present"] is not True:
            raise RuntimeError("C2PA parser result was not mapped onto the audio artefact")
        if len(c2pa_sample["evidence"]) != len(sample_node["evidence"]):
            raise RuntimeError("C2PA metadata unexpectedly inferred a relationship")

        # C2PA is optional at runtime, so the self-test supplies the same typed
        # result shape that the official SDK adapter returns. This tests bundle
        # mapping and never fabricates a relationship.
        original_c2pa_parser = globals()["parse_c2pa_file"]
        try:
            globals()["parse_c2pa_file"] = lambda path: C2paParseResult(
                source_path=path,
                present=True,
                active_manifest_label="urn:c2pa:test",
                assertions=[C2paAssertion(label="c2pa.actions", data={"actions": []})],
                ingredients=[C2paIngredient(
                    title="source.wav", relationship="parentOf", format="audio/wav"
                )],
                signature_info=C2paSignatureInfo(
                    issuer="Test issuer", time="2026-09-11T00:00:00Z", alg="ES256"
                ),
                validation_status={"signature_valid": True},
            )
            c2pa_manifest = copy.deepcopy(manifest)
            c2pa_manifest["files"][1]["parse_c2pa"] = True
            c2pa_bundle = build_bundle(c2pa_manifest, base_dir=root)
        finally:
            globals()["parse_c2pa_file"] = original_c2pa_parser
        c2pa_sample = next(
            node for node in c2pa_bundle["artefacts"]
            if node["artefact_type"] == "audio/sample"
        )
        if c2pa_sample["attributes"]["provenance"]["present"] is not True:
            raise RuntimeError("C2PA parser output was not mapped onto the audio artefact")
        if len(c2pa_sample["evidence"]) != 3:
            raise RuntimeError("C2PA parser output inferred an undeclared relationship")

    return {
        "status": "ok",
        "wav_files_parsed": 2,
        "rin_files_parsed": 1,
        "ern_files_parsed": 1,
        "c2pa_result_mapped": True,
        "sha256_computed": True,
        "explicit_artefact_types_used": True,
        "explicit_final_used": True,
        "explicit_relationship_inserted": True,
        "relationship_ids_resolved_to_hashes": True,
        "schema_validated": True,
        "ben_evidence_chain_loaded": True,
        "parser_conflict_preserved_for_integrity_pass": True,
        "no_declaration_produces_no_edge": True,
        "inference_used": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path,
                        help="manifest describing files, roles, final artefact and relationships")
    parser.add_argument("-o", "--output", type=Path,
                        help="output bundle path; stdout when omitted")
    parser.add_argument("--schema", type=Path,
                        help="evidence bundle schema path; defaults to the sibling schema")
    parser.add_argument("--example", action="store_true",
                        help="print an example manifest")
    parser.add_argument("--self-test", action="store_true",
                        help="run an end-to-end builder integration test")
    args = parser.parse_args(argv)

    try:
        if args.example:
            if args.manifest or args.self_test or args.schema:
                parser.error("--example cannot be combined with other inputs")
            _write_json(args.output, example_manifest())
            return 0
        if args.self_test:
            if args.manifest:
                parser.error("--self-test cannot be combined with a manifest")
            _write_json(args.output, self_test())
            return 0
        if args.manifest is None:
            parser.error("manifest is required unless --example or --self-test is used")
        bundle = build_bundle_from_file(args.manifest, schema_path=args.schema)
        _write_json(args.output, bundle)
        return 0
    except BundleBuildError as exc:
        print(f"bundle build failed: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"bundle build failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
