"""Music types and deliberately limited relationship checks for libevchain."""

from libevchain.types import (
    ArtefactType, AudioType, Attribute, AttributeTypes, RelationshipType,
    register_artefact_type, register_relationship_type,
)

AUDIO_TYPES = (
    "audio/raw-take", "audio/raw-track", "audio/edited-take",
    "audio/stem", "audio/mix", "audio/master", "audio/sample",
    "audio/reference", "audio/ai-source", "audio/unclassified",
)
DOCUMENT_TYPES = (
    "project/midi", "project/daw-session", "project/automation",
    "metadata/ddex-rin", "metadata/ddex-ern", "provenance/c2pa",
    "text/cue-sheet", "text/comp-sheet", "text/session-log",
    "text/license-clearance", "text/generation-record",
)
SUPPORTED_TYPES = {"audio", *AUDIO_TYPES, *DOCUMENT_TYPES}


class MusicAudioType(AudioType):
    @staticmethod
    def attributes():
        return {
            # These are submission claims. Extracted observations live in pass output.
            "technical": Attribute(AttributeTypes.Any, {}),
            "production": Attribute(AttributeTypes.Any, {}),
            "source_metadata": Attribute(AttributeTypes.Any, {}),
        }


for _name in AUDIO_TYPES:
    register_artefact_type(_name, type(_name.replace("/", "_").replace("-", "_"),
                                     (MusicAudioType,), {}))


class MusicDocumentType(ArtefactType):
    @staticmethod
    def attributes():
        return {
            "technical": Attribute(AttributeTypes.Any, {}),
            "production": Attribute(AttributeTypes.Any, {}),
            "release": Attribute(AttributeTypes.Any, {}),
            "source_metadata": Attribute(AttributeTypes.Any, {}),
            "parser_diagnostics": Attribute(AttributeTypes.Any, {}),
            "provenance": Attribute(AttributeTypes.Any, {}),
            "AI-declaration": Attribute(AttributeTypes.Any, {}),
            "document": Attribute(AttributeTypes.Any, {}),
        }


for _name in DOCUMENT_TYPES:
    register_artefact_type(_name, type(_name.replace("/", "_").replace("-", "_"),
                                      (MusicDocumentType,), {}))

# Each pair is (target roles, evidence/source roles). This is a local v0.1
# contract, not an upstream registry or proof that a transformation occurred.
ENDPOINTS = {
    "derived_from": (SUPPORTED_TYPES, SUPPORTED_TYPES),
    "edited_from": ({"audio/edited-take"},
                    {"audio/raw-take", "audio/raw-track", "audio/edited-take"}),
    "excerpted_from": ({"audio/sample"}, SUPPORTED_TYPES),
    "comped_from": ({"audio/edited-take"}, {"audio/raw-take"}),
    "stemmed_from": ({"audio/stem"},
                     {"audio/raw-track", "audio/edited-take", "audio/sample"}),
    "mixed_from": ({"audio/mix"},
                   {"audio/stem", "audio/raw-track", "audio/edited-take", "audio/sample"}),
    "mastered_from": ({"audio/master"}, {"audio/mix"}),
    "rendered_from": ({*AUDIO_TYPES}, {"project/midi", "project/daw-session", "project/automation"}),
    "input_to": (SUPPORTED_TYPES, SUPPORTED_TYPES),
}

RELATIONSHIP_ATTRIBUTES = {
    "assertion_origin": Attribute(AttributeTypes.Str, "submitter"),
    "confirmed_by_submitter": Attribute(AttributeTypes.Bool, False),
    "reference_pointer": Attribute(AttributeTypes.Any, None),
    "claim_rationale": Attribute(AttributeTypes.Any, None),
    "transformation_description": Attribute(AttributeTypes.Any, None),
    "derivation_scope": Attribute(AttributeTypes.Any, None),
    "source_start_seconds": Attribute(AttributeTypes.Any, None),
    "source_end_seconds": Attribute(AttributeTypes.Any, None),
    "target_start_seconds": Attribute(AttributeTypes.Any, None),
    "target_end_seconds": Attribute(AttributeTypes.Any, None),
    "fade_in_seconds": Attribute(AttributeTypes.Any, None),
    "fade_out_seconds": Attribute(AttributeTypes.Any, None),
    "input_sample_rate_hz": Attribute(AttributeTypes.Any, None),
    "output_sample_rate_hz": Attribute(AttributeTypes.Any, None),
    "input_bit_depth": Attribute(AttributeTypes.Any, None),
    "output_bit_depth": Attribute(AttributeTypes.Any, None),
    "source_format": Attribute(AttributeTypes.Any, None),
    "target_format": Attribute(AttributeTypes.Any, None),
}


def _relationship_class(name, endpoints):
    class MusicRelationship(RelationshipType):
        @staticmethod
        def attributes():
            return RELATIONSHIP_ATTRIBUTES

        def validate_types(self, artefact_type, evidence_type):
            return (artefact_type.artefact_type_name in endpoints[0]
                    and evidence_type.artefact_type_name in endpoints[1])
    MusicRelationship.__name__ = name
    return MusicRelationship


for _name, _endpoints in ENDPOINTS.items():
    register_relationship_type(_name, _relationship_class(_name, _endpoints))

SUPPORTED_RELATIONSHIPS = set(ENDPOINTS) | {"draft-of.audio"}
