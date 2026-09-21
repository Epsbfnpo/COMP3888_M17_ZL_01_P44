"""
Unit tests for music_evidence_types.py — the music-domain artefact and
relationship type registry that plugs into Ben's libevchain.

Run from inside the project repository (so libevchain is importable, either
via COMP3988_Evidence_Chains-master/src beside this file or LIBEVCHAIN_SRC):

    pytest test_music_evidence_types.py -v

Design notes
------------
* Importing this module has the side effect of locating and sys.path-inserting
  libevchain (_load_libevchain() runs at import time), so these tests assume
  the same environment bundle_builder's tests already run in.
* register_all() mutates a process-global type registry owned by libevchain.
  Tests that need "freshly unregistered" behaviour avoid depending on call
  order relative to other test modules (e.g. test_music_profiles.py, which
  also registers these types on import) by asserting invariants that hold
  regardless of how many times registration has already happened, rather than
  asserting exact "added" counts from a assumed-clean registry.
"""

from __future__ import annotations

import copy
import json

import pytest

import music_evidence_types as met


# --------------------------------------------------------------------------
# Primitive validators
# --------------------------------------------------------------------------

class TestPrimitiveValidators:

    def test_str_accepts_only_str(self):
        assert met.STR("hello") is True
        assert met.STR(1) is False
        assert met.STR(None) is False
        assert met.STR(True) is False  # bool is not accepted as str

    def test_bool_uses_strict_type_check(self):
        assert met.BOOL(True) is True
        assert met.BOOL(False) is True
        assert met.BOOL(1) is False       # int(1) must not pass as bool
        assert met.BOOL("true") is False

    def test_int_rejects_bool_and_float(self):
        assert met.INT(3) is True
        assert met.INT(True) is False     # bool is a subclass of int in Python,
        assert met.INT(3.0) is False      # but this validator uses `type(x) is int`

    def test_number_accepts_int_and_float_but_not_bool(self):
        assert met.NUMBER(3) is True
        assert met.NUMBER(3.5) is True
        assert met.NUMBER(True) is False
        assert met.NUMBER("3") is False

    def test_non_negative_number(self):
        assert met.NON_NEGATIVE_NUMBER(0) is True
        assert met.NON_NEGATIVE_NUMBER(0.25) is True
        assert met.NON_NEGATIVE_NUMBER(-0.01) is False
        assert met.NON_NEGATIVE_NUMBER("0") is False

    def test_pan_range(self):
        assert met.PAN(-1) is True
        assert met.PAN(1) is True
        assert met.PAN(0.5) is True
        assert met.PAN(-1.01) is False
        assert met.PAN(1.01) is False

    def test_list_str(self):
        assert met.LIST_STR([]) is True
        assert met.LIST_STR(["a", "b"]) is True
        assert met.LIST_STR(["a", 1]) is False
        assert met.LIST_STR("a") is False

    def test_json_value_accepts_any_json_type_including_none(self):
        for value in (None, True, 1, 1.5, "s", [1, "a"], {"k": "v"}):
            assert met.JSON_VALUE(value) is True

    def test_json_value_rejects_non_json_types(self):
        assert met.JSON_VALUE(object()) is False

    def test_sha256_or_null(self):
        assert met.SHA256_OR_NULL(None) is True
        assert met.SHA256_OR_NULL("a" * 64) is True
        assert met.SHA256_OR_NULL("A" * 64) is False   # must be lowercase hex
        assert met.SHA256_OR_NULL("a" * 63) is False
        assert met.SHA256_OR_NULL("z" * 64) is False

    def test_optional_wraps_none_as_valid(self):
        wrapped = met._optional(met.STR)
        assert wrapped(None) is True
        assert wrapped("x") is True
        assert wrapped(1) is False

    def test_literal_restricts_to_named_values(self):
        validator = met._literal("a", "b")
        assert validator("a") is True
        assert validator("b") is True
        assert validator("c") is False
        assert validator(None) is False


# --------------------------------------------------------------------------
# _record: optional-field record validator
# --------------------------------------------------------------------------

class TestRecordValidator:

    def test_accepts_empty_object(self):
        record = met._record({"a": met.STR})
        assert record({}) is True

    def test_accepts_known_fields_with_valid_values(self):
        record = met._record({"a": met.STR, "b": met.INT})
        assert record({"a": "x", "b": 1}) is True

    def test_rejects_unknown_fields(self):
        record = met._record({"a": met.STR})
        assert record({"a": "x", "z": "unexpected"}) is False

    def test_rejects_wrong_type_for_known_field(self):
        record = met._record({"a": met.STR})
        assert record({"a": 1}) is False

    def test_rejects_non_dict_input(self):
        record = met._record({"a": met.STR})
        assert record(["not", "a", "dict"]) is False
        assert record(None) is False


# --------------------------------------------------------------------------
# _matches: glob-style artefact type patterns ("audio/*")
# --------------------------------------------------------------------------

class TestMatches:

    def test_exact_pattern_matches_exact_type(self):
        assert met._matches(["audio/sample"], "audio/sample") is True
        assert met._matches(["audio/sample"], "audio/mix") is False

    def test_wildcard_pattern_matches_prefix(self):
        assert met._matches(["audio/*"], "audio/sample") is True
        assert met._matches(["audio/*"], "audio/raw-take") is True
        assert met._matches(["audio/*"], "metadata/ddex-rin") is False

    def test_multiple_patterns_any_match_wins(self):
        patterns = ["text/*", "metadata/*"]
        assert met._matches(patterns, "metadata/ddex-ern") is True
        assert met._matches(patterns, "text/cue-sheet") is True
        assert met._matches(patterns, "audio/mix") is False


# --------------------------------------------------------------------------
# Type registry content: every declared type is well-formed
# --------------------------------------------------------------------------

class TestArtefactTypeDefinitions:

    @pytest.mark.parametrize("name", list(met.ARTEFACT_TYPES))
    def test_every_group_field_is_a_mapping_of_validators(self, name):
        for group_name, fields in met.ARTEFACT_TYPES[name].items():
            assert isinstance(fields, dict), f"{name}.{group_name} must be a field map"
            for field_name, validator in fields.items():
                assert callable(validator), f"{name}.{group_name}.{field_name} must be callable"

    def test_audio_types_share_the_common_audio_groups(self):
        audio_types = [name for name in met.ARTEFACT_TYPES if name.startswith("audio/")]
        assert audio_types, "expected at least one audio/* type"
        for name in audio_types:
            groups = met.ARTEFACT_TYPES[name]
            for common_group in ("technical", "declared_technical", "source_metadata",
                                 "declared_source_metadata", "provenance", "parser_diagnostics"):
                assert common_group in groups, f"{name} is missing {common_group}"

    def test_metadata_types_do_not_carry_audio_technical_group(self):
        assert "technical" not in met.ARTEFACT_TYPES["metadata/ddex-rin"]
        assert "technical" not in met.ARTEFACT_TYPES["metadata/ddex-ern"]

    def test_ern_has_ai_declaration_group_but_rin_does_not(self):
        assert "AI-declaration" in met.ARTEFACT_TYPES["metadata/ddex-ern"]
        assert "AI-declaration" not in met.ARTEFACT_TYPES["metadata/ddex-rin"]


class TestRelationshipTypeDefinitions:

    @pytest.mark.parametrize("name", list(met.RELATIONSHIP_TYPES))
    def test_every_relationship_has_target_source_and_attributes(self, name):
        spec = met.RELATIONSHIP_TYPES[name]
        assert isinstance(spec["target"], list) and spec["target"]
        assert isinstance(spec["source"], list) and spec["source"]
        assert isinstance(spec["attributes"], dict) and spec["attributes"]

    @pytest.mark.parametrize("name", list(met.RELATIONSHIP_TYPES))
    def test_every_relationship_carries_the_common_attributes(self, name):
        spec = met.RELATIONSHIP_TYPES[name]
        for common in met.COMMON_RELATIONSHIP_ATTRIBUTES:
            assert common in spec["attributes"], f"{name} is missing common attribute {common}"

    def test_common_attribute_defaults(self):
        assert met.COMMON_RELATIONSHIP_ATTRIBUTES["assertion_origin"][1] == "submitter"
        assert met.COMMON_RELATIONSHIP_ATTRIBUTES["confirmed_by_submitter"][1] is False
        assert met.COMMON_RELATIONSHIP_ATTRIBUTES["reference_pointer"][1] is None
        assert met.COMMON_RELATIONSHIP_ATTRIBUTES["claim_rationale"][1] is None

    def test_assertion_origin_literal_matches_documented_five_origins(self):
        for origin in ("submitter", "daw-session", "c2pa-ingredient", "ddex-rin", "ddex-ern"):
            assert met.ASSERTION_ORIGIN(origin) is True
        assert met.ASSERTION_ORIGIN("something-else") is False

    def test_excerpted_from_targets_sample_from_any_audio(self):
        spec = met.RELATIONSHIP_TYPES["excerpted_from"]
        assert spec["target"] == ["audio/sample"]
        assert spec["source"] == ["audio/*"]

    def test_mastered_from_is_mix_to_master_only(self):
        spec = met.RELATIONSHIP_TYPES["mastered_from"]
        assert spec["target"] == ["audio/master"]
        assert spec["source"] == ["audio/mix"]

    def test_attested_by_source_is_restricted_to_c2pa(self):
        spec = met.RELATIONSHIP_TYPES["attested_by"]
        assert spec["source"] == ["provenance/c2pa"]

    def test_excerpted_from_target_start_seconds_defaults_to_zero(self):
        validator, default = met.RELATIONSHIP_TYPES["excerpted_from"]["attributes"]["target_start_seconds"]
        assert default == 0.0
        assert validator(0.0) is True
        assert validator(-1.0) is False


# --------------------------------------------------------------------------
# register_all(): plugging music types into libevchain's global registry
# --------------------------------------------------------------------------

class TestRegisterAll:

    def test_register_all_returns_added_lists(self):
        result = met.register_all()
        assert "artefact_types_added" in result
        assert "relationship_types_added" in result
        assert isinstance(result["artefact_types_added"], list)
        assert isinstance(result["relationship_types_added"], list)

    def test_register_all_is_idempotent(self):
        met.register_all()  # ensure everything is registered at least once
        second = met.register_all()
        assert second["artefact_types_added"] == []
        assert second["relationship_types_added"] == []

    def test_every_declared_artefact_type_is_resolvable_after_registration(self):
        met.register_all()
        for name in met.ARTEFACT_TYPES:
            assert met.get_artefact_type(name) is not None

    def test_every_declared_relationship_type_is_resolvable_after_registration(self):
        met.register_all()
        for name in met.RELATIONSHIP_TYPES:
            assert met.get_relationship_type(name) is not None

    def test_unregistered_type_name_is_not_resolvable(self):
        with pytest.raises(Exception):
            met.get_artefact_type("audio/definitely-not-a-real-type")


# --------------------------------------------------------------------------
# Endpoint validation on the constructed relationship classes
# --------------------------------------------------------------------------

class TestRelationshipEndpointValidation:

    def test_excerpted_from_accepts_sample_from_raw_take(self):
        met.register_all()
        relationship = met.get_relationship_type("excerpted_from")
        target = met.get_artefact_type("audio/sample")
        source = met.get_artefact_type("audio/raw-take")
        assert relationship.validate_types(target, source) is True

    def test_excerpted_from_rejects_wrong_target_type(self):
        met.register_all()
        relationship = met.get_relationship_type("excerpted_from")
        target = met.get_artefact_type("audio/mix")  # only audio/sample is a valid target
        source = met.get_artefact_type("audio/raw-take")
        assert relationship.validate_types(target, source) is False

    def test_mastered_from_rejects_stem_as_source(self):
        met.register_all()
        relationship = met.get_relationship_type("mastered_from")
        target = met.get_artefact_type("audio/master")
        source = met.get_artefact_type("audio/stem")  # only audio/mix is a valid source
        assert relationship.validate_types(target, source) is False

    def test_documented_by_accepts_metadata_source_onto_audio_target(self):
        met.register_all()
        relationship = met.get_relationship_type("documented_by")
        target = met.get_artefact_type("audio/sample")
        source = met.get_artefact_type("metadata/ddex-rin")
        assert relationship.validate_types(target, source) is True

    def test_attested_by_rejects_non_c2pa_source(self):
        met.register_all()
        relationship = met.get_relationship_type("attested_by")
        target = met.get_artefact_type("audio/sample")
        source = met.get_artefact_type("metadata/ddex-rin")  # must be provenance/c2pa
        assert relationship.validate_types(target, source) is False


# --------------------------------------------------------------------------
# Relationship attribute validation on the constructed classes
# --------------------------------------------------------------------------

class TestRelationshipAttributeValidation:

    def test_valid_excerpted_from_attributes_accepted(self):
        met.register_all()
        relationship = met.get_relationship_type("excerpted_from")
        relationship.validate_attributes({
            "assertion_origin": "submitter",
            "confirmed_by_submitter": True,
            "source_start_seconds": 0.0,
            "source_end_seconds": 0.25,
            "target_start_seconds": 0.0,
        })  # must not raise

    def test_invalid_assertion_origin_rejected(self):
        met.register_all()
        relationship = met.get_relationship_type("excerpted_from")
        with pytest.raises(Exception):
            relationship.validate_attributes({"assertion_origin": "made-up-origin"})

    def test_negative_time_window_rejected(self):
        met.register_all()
        relationship = met.get_relationship_type("excerpted_from")
        with pytest.raises(Exception):
            relationship.validate_attributes({"source_start_seconds": -1.0})

    def test_out_of_range_pan_rejected(self):
        met.register_all()
        relationship = met.get_relationship_type("mixed_from")
        with pytest.raises(Exception):
            relationship.validate_attributes({"declared_pan": 2.0})


# --------------------------------------------------------------------------
# example_bundle() / self_test(): the module's own integration checks
# --------------------------------------------------------------------------

class TestExampleBundle:

    def test_example_bundle_is_json_serialisable(self):
        bundle = met.example_bundle()
        json.dumps(bundle)  # must not raise

    def test_example_bundle_final_hash_matches_target_artefact(self):
        bundle = met.example_bundle()
        hashes = {artefact["artefact_hash"] for artefact in bundle["artefacts"]}
        assert bundle["final_artefact_hash"] in hashes

    def test_example_bundle_has_one_declared_edge(self):
        bundle = met.example_bundle()
        target = next(a for a in bundle["artefacts"]
                      if a["artefact_hash"] == bundle["final_artefact_hash"])
        assert len(target["evidence"]) == 1
        assert target["evidence"][0]["relationship_type"] == "excerpted_from"


class TestSelfTest:

    def test_self_test_reports_ok(self):
        result = met.self_test()
        assert result["status"] == "ok"
        assert result["invalid_attribute_rejected"] is True
        assert result["invalid_endpoint_rejected"] is True

    def test_self_test_result_is_json_serialisable(self):
        result = met.self_test()
        json.dumps(result)  # relationship_attributes must not contain exotic objects


# --------------------------------------------------------------------------
# CLI (main())
# --------------------------------------------------------------------------

class TestCLI:

    def test_list_flag_prints_type_names(self, capsys):
        rc = met.main(["--list"])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert set(printed["artefact_types"]) == set(met.ARTEFACT_TYPES)
        assert set(printed["relationship_types"]) == set(met.RELATIONSHIP_TYPES)

    def test_example_flag_prints_valid_bundle(self, capsys):
        rc = met.main(["--example"])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["final_artefact_hash"]

    def test_self_test_flag_prints_ok(self, capsys):
        rc = met.main(["--self-test"])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["status"] == "ok"

    def test_no_action_flag_is_an_error(self):
        with pytest.raises(SystemExit):
            met.main([])

    def test_two_action_flags_together_is_an_error(self):
        with pytest.raises(SystemExit):
            met.main(["--example", "--list"])


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
