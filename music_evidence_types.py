#!/usr/bin/env python3
"""
Relationship attributes describe a submitter/tool claim and the concrete
parameters a later EvidencePass can check.  They never contain assessor
confidence, verification status, or assessment rationale.

Functionalities:
Define which relationships are permitted (e.g., `excerpted_from`, `mixed_from`).
Define which artifact types a relationship is allowed to connect.
Define the attributes applicable to each type of relationship.
Reject malformed relationships.


"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence


def _load_libevchain() -> None:
    """Locate the supplied Ben repository without installing it globally."""
    here = Path(__file__).resolve().parent
    candidates = []
    configured = os.environ.get("LIBEVCHAIN_SRC")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        here / "COMP3988_Evidence_Chains-master" / "src",
        here / "COMP3888" / "COMP3988_Evidence_Chains-master" / "src",
        here.parent / "COMP3988_Evidence_Chains-master" / "src",
        here.parent / "COMP3888" / "COMP3988_Evidence_Chains-master" / "src",
    ])
    for candidate in candidates:
        if (candidate / "libevchain" / "evidence_chain.py").is_file():
            sys.path.insert(0, str(candidate))
            return
    try:
        __import__("libevchain")
        return
    except ImportError:
        searched = "\n".join(f"  - {path}" for path in candidates)
        raise RuntimeError(
            "Could not find Ben's libevchain. Set LIBEVCHAIN_SRC to its src directory.\n"
            f"Searched:\n{searched}") from None


_load_libevchain()

from libevchain.types import (  # noqa: E402
    ArtefactType,
    Attribute,
    AttributeTypes,
    AudioType,
    RelationshipType,
    get_artefact_type,
    get_relationship_type,
    register_artefact_type,
    register_relationship_type,
)


Validator = Callable[[Any], bool]


def _named(name: str, validator: Validator) -> Validator:
    validator._boundtype = name  # type: ignore[attr-defined]
    validator.__name__ = f"typecheck<{name}>"
    return validator


def _optional(inner: Validator) -> Validator:
    return _named(f"optional<{getattr(inner, '_boundtype', inner.__name__)}>",
                  lambda value: value is None or inner(value))


STR = _named("str", lambda value: type(value) is str)
BOOL = _named("bool", lambda value: type(value) is bool)
INT = _named("int", lambda value: type(value) is int)
NUMBER = _named("number", lambda value: type(value) in (int, float))
NON_NEGATIVE_NUMBER = _named(
    "non-negative-number", lambda value: type(value) in (int, float) and value >= 0)
PAN = _named("pan[-1,1]", lambda value: type(value) in (int, float) and -1 <= value <= 1)
LIST_STR = _named("list<str>",
                  lambda value: isinstance(value, list)
                  and all(type(item) is str for item in value))
JSON_VALUE = _named("json-value", lambda value: value is None or isinstance(
    value, (bool, int, float, str, list, dict)))
SHA256_OR_NULL = _named(
    "sha256-or-null",
    lambda value: value is None
    or (type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None))


def _literal(*values: str) -> Validator:
    allowed = set(values)
    return _named("literal<" + ",".join(values) + ">", lambda value: value in allowed)


def _record(fields: Mapping[str, Validator]) -> Validator:
    """Optional-field record: reject unknown keys, allow omitted values."""
    def validate(value: Any) -> bool:
        return (isinstance(value, dict)
                and not (set(value) - set(fields))
                and all(fields[key](item) for key, item in value.items()))
    return _named("record<" + ",".join(fields) + ">", validate)


TECHNICAL_AUDIO = {
    "media_type": _optional(STR),
    "duration_seconds": _optional(NON_NEGATIVE_NUMBER),
    "sample_rate_hz": _optional(INT),
    "channels": _optional(INT),
    "bit_depth": _optional(INT),
    "format": _optional(STR),
    "codec_subtype": _optional(STR),
    "file_size_bytes": _optional(INT),
}

SOURCE_METADATA_AUDIO = {
    "original_filename": _optional(STR),
    "author": _optional(STR),
    "license": _optional(STR),
    "description": _optional(STR),
    "submitted_at": _optional(STR),
    "updated_at": _optional(STR),
    "originator": _optional(STR),
    "originator_reference": _optional(STR),
    "origination_date": _optional(STR),
    "origination_time": _optional(STR),
    "time_reference_samples": _optional(INT),
    "umid": _optional(STR),
    "coding_history": _optional(STR),
    "software": _optional(STR),
    "artist": _optional(STR),
    "engineer": _optional(STR),
    "comment": _optional(STR),
}

PARSER_DIAGNOSTICS = {
    "parser": _optional(STR),
    "warnings": _optional(LIST_STR),
}

C2PA_PROVENANCE = {
    "present": _optional(BOOL),
    "active_manifest_label": _optional(STR),
    "assertions": _optional(JSON_VALUE),
    "ingredients": _optional(JSON_VALUE),
    "signature_info": _optional(JSON_VALUE),
    "validation_status": _optional(JSON_VALUE),
}


def _audio_groups(production: Mapping[str, Validator]) -> Dict[str, Mapping[str, Validator]]:
    return {
        # Parser-observed values and external declarations are kept separate so
        # later passes can score a disagreement as evidence instead of losing it
        # during bundle construction.
        "technical": TECHNICAL_AUDIO,
        "declared_technical": TECHNICAL_AUDIO,
        "production": production,
        "source_metadata": SOURCE_METADATA_AUDIO,
        "declared_source_metadata": SOURCE_METADATA_AUDIO,
        "provenance": C2PA_PROVENANCE,
        "parser_diagnostics": PARSER_DIAGNOSTICS,
    }


ARTEFACT_TYPES: Dict[str, Dict[str, Mapping[str, Validator]]] = {
    "audio/raw-take": _audio_groups({
        "track_name": _optional(STR), "source_role": _optional(STR),
        "instrument": _optional(STR), "capture_method": _optional(STR),
        "equipment": _optional(LIST_STR),
    }),
    "audio/raw-track": _audio_groups({
        "track_name": _optional(STR), "source_role": _optional(STR),
        "instrument": _optional(STR), "capture_method": _optional(STR),
        "equipment": _optional(LIST_STR),
    }),
    "audio/edited-take": _audio_groups({
        "track_name": _optional(STR), "source_role": _optional(STR),
        "instrument": _optional(STR),
    }),
    "audio/stem": _audio_groups({
        "stem_name": _optional(STR), "stem_role": _optional(STR),
        "source_role": _optional(STR), "instrument": _optional(STR),
    }),
    "audio/mix": _audio_groups({
        "mix_name": _optional(STR), "mix_stage": _optional(STR),
    }),
    "audio/master": _audio_groups({"master_name": _optional(STR)}),
    "audio/sample": _audio_groups({
        "sample_name": _optional(STR), "sample_category": _optional(STR),
    }),
    "audio/unclassified": _audio_groups({"declared_role": _optional(STR)}),
    "text/cue-sheet": {
        "document": {
            "song_title": _optional(STR), "style": _optional(STR),
            "key": _optional(STR), "tempo_bpm": _optional(NUMBER),
            "musical_structure": _optional(LIST_STR),
        },
        "source_metadata": SOURCE_METADATA_AUDIO,
    },
    "text/comp-sheet": {
        "document": {
            "song_title": _optional(STR), "lyrics": _optional(STR),
            "take_selections": _optional(LIST_STR),
            "comp_notes": _optional(LIST_STR),
        },
        "source_metadata": SOURCE_METADATA_AUDIO,
    },
    "project/daw-session": {
        "technical": {
            "project_format": _optional(STR), "creator": _optional(STR),
            "creator_version": _optional(STR),
        },
        "production": {
            "project_name": _optional(STR), "tempo_bpm": _optional(NUMBER),
            "timesig_numerator": _optional(INT),
            "timesig_denominator": _optional(INT),
            "master_volume": _optional(NUMBER), "master_pitch": _optional(NUMBER),
            "tracks": _optional(JSON_VALUE), "media_references": _optional(JSON_VALUE),
            "automation": _optional(JSON_VALUE), "plugins": _optional(JSON_VALUE),
        },
        "source_metadata": SOURCE_METADATA_AUDIO,
    },
    "metadata/ddex-rin": {
        "production": {
            "session_id": _optional(STR), "session_date": _optional(STR),
            "location": _optional(STR), "contributors": _optional(JSON_VALUE),
            "contributor_roles": _optional(JSON_VALUE), "equipment": _optional(JSON_VALUE),
            "recording_components": _optional(JSON_VALUE),
            "sessions": _optional(JSON_VALUE),
        },
        "source_metadata": SOURCE_METADATA_AUDIO,
        "parser_diagnostics": PARSER_DIAGNOSTICS,
    },
    "metadata/ddex-ern": {
        "release": {
            "release_id": _optional(STR), "title": _optional(STR),
            "release_type": _optional(STR), "releases": _optional(JSON_VALUE),
        },
        "AI-declaration": {"value": _optional(JSON_VALUE)},
        "source_metadata": SOURCE_METADATA_AUDIO,
        "parser_diagnostics": PARSER_DIAGNOSTICS,
    },
    "provenance/c2pa": {
        "provenance": C2PA_PROVENANCE,
        "source_metadata": SOURCE_METADATA_AUDIO,
        "parser_diagnostics": PARSER_DIAGNOSTICS,
    },
}


ASSERTION_ORIGIN = _literal(
    "submitter", "daw-session", "c2pa-ingredient", "ddex-rin", "ddex-ern")

COMMON_RELATIONSHIP_ATTRIBUTES: Dict[str, tuple[Validator, Any]] = {
    "assertion_origin": (ASSERTION_ORIGIN, "submitter"),
    "confirmed_by_submitter": (BOOL, False),
    "reference_pointer": (_optional(STR), None), #This is used to locate the evidence in the artefact file
    "claim_rationale": (_optional(STR), None),
}


def _relationship_attributes(**specific: tuple[Validator, Any]) -> Dict[str, tuple[Validator, Any]]:
    return {**COMMON_RELATIONSHIP_ATTRIBUTES, **specific}


RELATIONSHIP_TYPES: Dict[str, dict] = {
    "derived_from": {
        "target": ["audio/*"], "source": ["audio/*"],
        "attributes": _relationship_attributes(
            transformation_description=(_optional(STR), None)),
    },
    "edited_from": {
        "target": ["audio/edited-take", "audio/raw-track"],
        "source": ["audio/raw-take", "audio/raw-track"],
        "attributes": _relationship_attributes(
            source_start_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            source_end_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            target_start_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            edit_operations=(_optional(LIST_STR), None)),
    },
    "comped_from": {
        "target": ["audio/edited-take", "audio/raw-track"],
        "source": ["audio/raw-take"],
        "attributes": _relationship_attributes(
            source_start_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            source_end_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            target_start_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            target_end_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            take_label=(_optional(STR), None)),
    },
    "stemmed_from": {
        "target": ["audio/stem"],
        "source": ["audio/raw-take", "audio/raw-track", "audio/edited-take",
                   "project/daw-session"],
        "attributes": _relationship_attributes(
            timeline_offset_seconds=(_optional(NUMBER), None),
            source_role=(_optional(STR), None), track_id=(_optional(STR), None)),
    },
    "mixed_from": {
        "target": ["audio/mix"],
        "source": ["audio/stem", "audio/raw-track", "audio/edited-take", "audio/sample"],
        "attributes": _relationship_attributes(
            timeline_offset_seconds=(_optional(NUMBER), None),
            source_role=(_optional(STR), None),
            declared_gain_db=(_optional(NUMBER), None),
            declared_pan=(_optional(PAN), None)),
    },
    "mastered_from": {
        "target": ["audio/master"], "source": ["audio/mix"],
        "attributes": _relationship_attributes(
            input_sample_rate_hz=(_optional(INT), None),
            output_sample_rate_hz=(_optional(INT), None),
            input_bit_depth=(_optional(INT), None),
            output_bit_depth=(_optional(INT), None),
            target_loudness_lufs=(_optional(NUMBER), None)),
    },
    "excerpted_from": {
        "target": ["audio/sample"], "source": ["audio/*"],
        "attributes": _relationship_attributes(
            source_start_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            source_end_seconds=(_optional(NON_NEGATIVE_NUMBER), None),
            target_start_seconds=(_optional(NON_NEGATIVE_NUMBER), 0.0)),
    },
    "rendered_from": {
        "target": ["audio/*"], "source": ["project/daw-session"],
        "attributes": _relationship_attributes(
            render_profile=(_optional(STR), None), render_timestamp=(_optional(STR), None)),
    },
    "converted_from": {
        "target": ["audio/*"], "source": ["audio/*"],
        "attributes": _relationship_attributes(
            source_format=(_optional(STR), None), target_format=(_optional(STR), None)),
    },
    "documented_by": {
        "target": ["audio/*", "project/daw-session"],
        "source": ["text/*", "metadata/*"],
        "attributes": _relationship_attributes(
            document_role=(_optional(STR), None), referenced_item=(_optional(STR), None)),
    },
    "attested_by": {
        "target": ["audio/*", "project/daw-session", "text/*", "metadata/*"],
        "source": ["provenance/c2pa"],
        "attributes": _relationship_attributes(
            assertion_label=(_optional(STR), None)),
    },
}


def _matches(patterns: Iterable[str], actual: str) -> bool:
    return any(actual.startswith(pattern[:-1]) if pattern.endswith("*")
               else actual == pattern for pattern in patterns)


def _patch_supplied_audio_type_if_needed() -> None:
    """The supplied snapshot has a NameError in AudioType.attributes()."""
    try:
        AudioType().get_attributes()
        return
    except NameError:
        pass

    @staticmethod
    def attributes():
        method = AttributeTypes.Literal("recorded", "synthesised", "ai-generated")
        return {"creation_method": Attribute(
            AttributeTypes.Either(method, AttributeTypes.ListOf(method)), "unknown")}

    AudioType.attributes = attributes


def _existing_artefact(name: str):
    try:
        return get_artefact_type(name)
    except Exception:
        return None


def _existing_relationship(name: str):
    try:
        return get_relationship_type(name)
    except Exception:
        return None


def _make_artefact_class(name: str, groups: Mapping[str, Mapping[str, Validator]]) -> type:
    base = AudioType if name.startswith("audio/") else ArtefactType

    @staticmethod
    def attributes() -> Dict[str, Attribute]:
        return {group: Attribute(_record(fields), {}) for group, fields in groups.items()}

    return type("Music_" + re.sub(r"[^0-9A-Za-z]", "_", name),
                (base,), {"attributes": attributes, "domain_spec": groups})


def _make_relationship_class(name: str, spec: Mapping[str, Any]) -> type:
    @staticmethod
    def attributes() -> Dict[str, Attribute]:
        return {key: Attribute(validator, default)
                for key, (validator, default) in spec["attributes"].items()}

    def validate_types(self, target_type, source_type) -> bool:
        target = getattr(target_type, "artefact_type_name", "")
        source = getattr(source_type, "artefact_type_name", "")
        return _matches(spec["target"], target) and _matches(spec["source"], source)

    return type("MusicRel_" + name, (RelationshipType,), {
        "attributes": attributes,
        "validate_types": validate_types,
        "domain_spec": spec,
    })


def register_all() -> dict:
    """Register the music domain. Safe to call repeatedly in one process."""
    _patch_supplied_audio_type_if_needed()
    added_artefacts, added_relationships = [], []
    for name, groups in ARTEFACT_TYPES.items():
        if _existing_artefact(name) is None:
            register_artefact_type(name, _make_artefact_class(name, groups))
            added_artefacts.append(name)
    for name, spec in RELATIONSHIP_TYPES.items():
        if _existing_relationship(name) is None:
            register_relationship_type(name, _make_relationship_class(name, spec))
            added_relationships.append(name)
    return {"artefact_types_added": added_artefacts,
            "relationship_types_added": added_relationships}


def example_bundle() -> dict:
    source_hash = "1" * 64
    target_hash = "2" * 64
    return {
        "schema_version": "0.0.3",
        "hash_method": "sha256",
        "final_artefact_hash": target_hash,
        "artefacts": [
            {
                "artefact_hash": source_hash,
                "artefact_type": "audio/raw-take",
                "attributes": {
                    "technical": {"duration_seconds": 60.0, "sample_rate_hz": 48000},
                    "production": {"track_name": "Original vocal"},
                },
                "evidence": [],
            },
            {
                "artefact_hash": target_hash,
                "artefact_type": "audio/sample",
                "attributes": {
                    "technical": {"duration_seconds": 8.0, "sample_rate_hz": 48000},
                    "production": {"sample_name": "Vocal phrase"},
                },
                "evidence": [{
                    "hash": source_hash,
                    "relationship_type": "excerpted_from",
                    "attributes": {
                        "assertion_origin": "submitter",
                        "confirmed_by_submitter": True,
                        "source_start_seconds": 42.5,
                        "source_end_seconds": 50.5,
                        "target_start_seconds": 0.0,
                        "claim_rationale": "The vocal phrase was cut from the original recording."
                    },
                }],
            },
        ],
    }


def self_test() -> dict:
    from libevchain.evidence_chain import EvidenceChain

    registered = register_all()
    bundle = example_bundle()
    chain = EvidenceChain.from_dict(bundle)
    target = chain.artefacts[bundle["final_artefact_hash"]]
    edge = next(target.get_evidence())
    if not edge.relationship_type.validate_types(
            target.artefact_type, edge.get_evidence_artefact().artefact_type):
        raise RuntimeError("relationship endpoint validation failed")
    if edge.source_start_seconds != 42.5 or edge.source_end_seconds != 50.5:
        raise RuntimeError("relationship attributes were not loaded")
    if edge.claim_rationale is None or not edge.confirmed_by_submitter:
        raise RuntimeError("relationship claim context was not loaded")

    invalid_attributes = example_bundle()
    invalid_attributes["artefacts"][1]["evidence"][0]["attributes"][
        "source_start_seconds"] = "not-a-number"
    try:
        EvidenceChain.from_dict(invalid_attributes)
    except Exception:
        pass
    else:
        raise RuntimeError("invalid relationship attribute was accepted")

    mastered_from = get_relationship_type("mastered_from")
    if mastered_from.validate_types(
            get_artefact_type("audio/master"), get_artefact_type("audio/stem")):
        raise RuntimeError("invalid mastered_from endpoints were accepted")

    return {
        "status": "ok",
        "artefact_count": len(chain.artefacts),
        "relationship_type": edge.relationship_type.relationship_type_name,
        "relationship_attributes": dict(edge._attributes),
        "registered": registered,
        "invalid_attribute_rejected": True,
        "invalid_endpoint_rejected": True,
        "note": "The relationship is loaded as a claim; this test does not verify audio content.",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--self-test", action="store_true",
                        help="register types and load a sample graph with Ben's EvidenceChain")
    action.add_argument("--example", action="store_true",
                        help="print a Ben-compatible example evidence bundle")
    action.add_argument("--list", action="store_true",
                        help="register and list all music-domain types")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            result = self_test()
        elif args.example:
            register_all()
            result = example_bundle()
        else:
            register_all()
            result = {"artefact_types": list(ARTEFACT_TYPES),
                      "relationship_types": list(RELATIONSHIP_TYPES)}
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        print(f"music evidence type registration failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
