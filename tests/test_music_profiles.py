"""
Unit tests for music_profiles.py — Stage 2 of the music evidence pipeline
(the four-axis scoring profile that plugs into Ben's libevchain Pipeline).

Run from inside the project repository, so that music_profiles.py can find
music_profile.json, music_evidence_types.py, relationship_extractor.py and
wav_parser.py beside it, and so libevchain is importable:

    pytest test_music_profiles.py -v

Design notes
------------
* music_profiles.py loads its support modules and music_profile.json via
  file-path lookup relative to its own location at *import time*
  (MUSIC_TYPES/RELATIONSHIPS/WAV_PARSER, and the module-level PROFILE
  constant). These tests therefore import music_profiles the ordinary way
  and rely on that lookup succeeding in the real project layout, exactly as
  bundle_builder's own tests rely on the sibling parser modules.
* Where a test needs a *different* profile (e.g. to check _load_profile's
  required-field validation), it calls _load_profile() directly with
  MUSIC_PROFILE_PATH monkeypatched, rather than touching the already-loaded
  module-level PROFILE singleton — mutating that singleton would leak
  between tests since it's read by every pass at call time.
* Bundles here are built directly in the already-built evidence-bundle shape
  (artefact_hash / attributes / evidence-with-resolved-hashes), the same
  shape self_test() constructs, rather than routed through bundle_builder's
  manifest format — evaluate_bundle() explicitly documents accepting this
  shape (apply_relationships(bundle, []) normalizes/validates it without
  adding new declarations).
"""

from __future__ import annotations

import copy
import hashlib
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import music_profiles as mp


# --------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------

def write_wav(path: Path, *, frames: int, sample_rate: int = 48000,
              channels: int = 1, sampwidth: int = 2) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sampwidth)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00" * sampwidth * frames)


def fake_hash(label: str) -> str:
    """A valid-looking sha256 hex digest that need not back a real file —
    fine for artefacts this suite never asks Ben to bind to a path."""
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def single_artefact_bundle(hash_value: str, artefact_type: str, attributes: dict,
                           evidence: list | None = None) -> dict:
    return {
        "schema_version": "0.0.3",
        "hash_method": "sha256",
        "final_artefact_hash": hash_value,
        "artefacts": [{
            "artefact_hash": hash_value,
            "artefact_type": artefact_type,
            "attributes": attributes,
            "evidence": evidence or [],
        }],
    }


@pytest.fixture
def excerpt_setup(tmp_path):
    """The self_test() scenario: a 1.0s raw take, a 0.5s sample excerpted
    from its middle, joined by one declared, submitter-confirmed edge."""
    source_path = tmp_path / "original-vocal.wav"
    target_path = tmp_path / "vocal-phrase.wav"
    write_wav(source_path, frames=48000, sample_rate=48000)
    write_wav(target_path, frames=24000, sample_rate=48000)
    source_hash = mp._hash_file(source_path)
    target_hash = mp._hash_file(target_path)

    bundle = {
        "schema_version": "0.0.3",
        "hash_method": "sha256",
        "final_artefact_hash": target_hash,
        "artefacts": [
            {
                "artefact_hash": source_hash,
                "artefact_type": "audio/raw-take",
                "attributes": {
                    "technical": {"duration_seconds": 1.0, "sample_rate_hz": 48000},
                    "production": {"track_name": "Original vocal"},
                    "creation_method": "recorded",
                },
                "evidence": [],
            },
            {
                "artefact_hash": target_hash,
                "artefact_type": "audio/sample",
                "attributes": {
                    "technical": {"duration_seconds": 0.5, "sample_rate_hz": 48000},
                    "production": {"sample_name": "Vocal phrase"},
                    "creation_method": "recorded",
                },
                "evidence": [],
            },
        ],
    }
    declarations = {"relationships": [{
        "target": target_hash, "source": source_hash,
        "relationship_type": "excerpted_from",
        "attributes": {
            "assertion_origin": "submitter", "confirmed_by_submitter": True,
            "source_start_seconds": 0.25, "source_end_seconds": 0.75,
            "target_start_seconds": 0.0,
            "claim_rationale": "Declared by the submitter.",
        },
    }]}
    complete = mp.RELATIONSHIPS.apply_relationships(bundle, declarations)
    return SimpleNamespace(
        root=tmp_path, source_path=source_path, target_path=target_path,
        source_hash=source_hash, target_hash=target_hash, bundle=complete,
    )


# --------------------------------------------------------------------------
# Pure helper functions
# --------------------------------------------------------------------------

class TestClamp:

    def test_clamps_below_zero(self):
        assert mp._clamp(-1.5) == 0.0

    def test_clamps_above_one(self):
        assert mp._clamp(2.5) == 1.0

    def test_passes_through_mid_range(self):
        assert mp._clamp(0.5) == 0.5

    def test_rounds_to_four_decimal_places(self):
        assert mp._clamp(1 / 3) == 0.3333


class TestScoreHelper:

    def test_defaults_are_all_unassessed(self):
        score = mp._score()
        assert score.integrity is mp.Unassessed
        assert score.completeness is mp.Unassessed
        assert score.attestation_strength is mp.Unassessed
        assert score.ai_disclosure is mp.Unassessed

    def test_supplied_values_are_clamped(self):
        score = mp._score(integrity=1.5, completeness=-0.5)
        assert score.integrity == 1.0
        assert score.completeness == 0.0

    def test_unassessed_axis_stays_unassessed_even_if_others_are_set(self):
        score = mp._score(integrity=0.8)
        assert score.integrity == 0.8
        assert score.ai_disclosure is mp.Unassessed


class TestValuesMatch:

    def test_exact_int_match(self):
        assert mp._values_match("channels", 2, 2) is True
        assert mp._values_match("channels", 2, 3) is False

    def test_float_tolerance_for_ordinary_fields(self):
        assert mp._values_match("some_field", 1.0000001, 1.0000002) is True
        assert mp._values_match("some_field", 1.0, 1.1) is False

    def test_duration_uses_sample_rate_derived_tolerance(self):
        # One sample's worth of rounding error at 48kHz should still match.
        assert mp._values_match(
            "duration_seconds", 1.0, 1.0 + (1.0 / 48000) * 0.5, sample_rate_hz=48000
        ) is True

    def test_non_numeric_falls_back_to_equality(self):
        assert mp._values_match("format", "WAV", "WAV") is True
        assert mp._values_match("format", "WAV", "MP3") is False


class TestCompact:

    def test_drops_none_values(self):
        assert mp._compact({"a": 1, "b": None}) == {"a": 1}

    def test_keeps_zero_and_false(self):
        assert mp._compact({"a": 0, "b": False}) == {"a": 0, "b": False}


class TestRecursiveLookup:

    def test_finds_direct_boolean_key(self):
        assert mp._recursive_lookup({"valid": True}, ("valid",)) is True

    def test_is_case_and_dash_insensitive(self):
        assert mp._recursive_lookup({"Signature-Valid": True}, ("signature_valid",)) is True

    def test_searches_nested_dicts_and_lists(self):
        nested = {"outer": [{"inner": {"trusted": True}}]}
        assert mp._recursive_lookup(nested, ("trusted",)) is True

    def test_returns_none_when_absent(self):
        assert mp._recursive_lookup({"other": True}, ("valid",)) is None

    def test_ignores_non_boolean_values_at_matching_key(self):
        assert mp._recursive_lookup({"valid": "yes"}, ("valid",)) is None


# --------------------------------------------------------------------------
# Profile loading
# --------------------------------------------------------------------------

class TestLoadProfile:

    def test_module_level_profile_matches_the_shipped_json(self):
        assert mp.PROFILE["profile_id"] == "comp3888-music-balanced"
        assert mp.PROFILE["profile_version"] == "0.1.2"

    def test_missing_required_field_is_rejected(self, tmp_path, monkeypatch):
        broken = {"profile_id": "x"}  # missing every other required field
        path = tmp_path / "broken_profile.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        monkeypatch.setenv("MUSIC_PROFILE_PATH", str(path))
        with pytest.raises(mp.ProfileError, match="missing fields"):
            mp._load_profile()

    def test_nonexistent_configured_path_falls_back_to_shipped_profile(self, monkeypatch):
        monkeypatch.setenv("MUSIC_PROFILE_PATH", "/does/not/exist.json")
        profile = mp._load_profile()  # falls through to the beside-file candidate
        assert profile["profile_id"] == "comp3888-music-balanced"

    def test_duplicate_json_keys_rejected(self, tmp_path, monkeypatch):
        path = tmp_path / "dup_profile.json"
        path.write_text(
            '{"profile_id": "a", "profile_id": "b"}', encoding="utf-8"
        )
        monkeypatch.setenv("MUSIC_PROFILE_PATH", str(path))
        with pytest.raises(mp.ProfileError, match="duplicate JSON key"):
            mp._load_profile()


class TestLoadSupportModule:

    def test_missing_support_module_raises_profile_error(self, monkeypatch):
        monkeypatch.setenv("MUSIC_PROFILE_SUPPORT_DIR", "/does/not/exist")
        with pytest.raises(mp.ProfileError, match="must be beside this file"):
            mp._load_support_module("not_a_real_module_xyz")

    def test_already_loaded_module_is_returned_as_is(self):
        # music_evidence_types is already in sys.modules from mp's own import.
        loaded = mp._load_support_module("music_evidence_types")
        assert loaded is mp.MUSIC_TYPES


# --------------------------------------------------------------------------
# evaluate_bundle(): end-to-end consistent case (mirrors self_test())
# --------------------------------------------------------------------------

class TestEvaluateBundleConsistentCase:

    def test_integrity_is_perfect_when_everything_matches(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert result["axes"]["integrity"]["value"] == 1.0
        assert result["axes"]["integrity"]["availability"] == "available"

    def test_completeness_is_satisfied(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert result["axes"]["completeness"]["value"] == 1.0

    def test_attestation_is_unavailable_without_c2pa(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert result["axes"]["attestation_strength"]["availability"] == "unavailable"
        assert result["axes"]["attestation_strength"]["value"] is None

    def test_ai_disclosure_recognizes_explicit_creation_method(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert result["axes"]["ai_disclosure"]["value"] == 1.0
        assert result["axes"]["ai_disclosure"]["availability"] == "available"

    def test_no_overall_score_is_ever_emitted(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert "overall_score" not in result

    def test_unassessed_attestation_uses_ben_sentinel(self, excerpt_setup):
        score = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root)
        assert score.attestation_strength is mp.Unassessed

    def test_final_artefact_hash_must_identify_a_real_artefact(self, excerpt_setup):
        broken = copy.deepcopy(excerpt_setup.bundle)
        broken["final_artefact_hash"] = fake_hash("not-a-real-artefact")
        # apply_relationships() itself guards this before evaluate_bundle's own
        # (otherwise-unreachable) ProfileError check ever runs.
        with pytest.raises(mp.RELATIONSHIPS.RelationshipExtractionError,
                            match="final_artefact_hash does not identify an artefact"):
            mp.evaluate_bundle(broken, excerpt_setup.root)


# --------------------------------------------------------------------------
# Integrity axis: contradictions
# --------------------------------------------------------------------------

class TestIntegrityContradictions:

    def test_declared_metadata_contradiction_caps_integrity(self, excerpt_setup):
        contradicted = copy.deepcopy(excerpt_setup.bundle)
        contradicted["artefacts"][0]["attributes"]["declared_technical"] = {
            "sample_rate_hz": 44100
        }
        result = mp.evaluate_bundle(contradicted, excerpt_setup.root).to_dict()
        ceiling = float(mp.PROFILE["integrity"]["contradiction_value_ceiling"])
        assert result["axes"]["integrity"]["value"] <= ceiling

    def test_declared_metadata_contradiction_detail_is_retained(self, excerpt_setup):
        contradicted = copy.deepcopy(excerpt_setup.bundle)
        contradicted["artefacts"][0]["attributes"]["declared_technical"] = {
            "sample_rate_hz": 44100
        }
        result = mp.evaluate_bundle(contradicted, excerpt_setup.root).to_dict()
        units = result["checks"]["integrity"]["results"]
        assert any(
            unit.get("contradiction")
            and any(
                check.get("rule") == "declared_technical.sample_rate_hz_matches_observed"
                and check.get("passed") is False
                for check in unit.get("checks", [])
            )
            for unit in units
        )

    def test_relationship_parameter_contradiction_caps_integrity_and_emits_finding(self, excerpt_setup):
        contradicted = copy.deepcopy(excerpt_setup.bundle)
        contradicted["artefacts"][1]["evidence"][0]["attributes"]["source_end_seconds"] = 75.0
        result = mp.evaluate_bundle(contradicted, excerpt_setup.root).to_dict()
        ceiling = float(mp.PROFILE["integrity"]["contradiction_value_ceiling"])
        assert result["axes"]["integrity"]["value"] <= ceiling
        assert any(item["code"] == "INTEGRITY_CONTRADICTION" for item in result["findings"])

    def test_agreeing_declared_metadata_is_not_a_contradiction(self, excerpt_setup):
        agreeing = copy.deepcopy(excerpt_setup.bundle)
        agreeing["artefacts"][0]["attributes"]["declared_technical"] = {
            "sample_rate_hz": 48000
        }
        result = mp.evaluate_bundle(agreeing, excerpt_setup.root).to_dict()
        assert result["axes"]["integrity"]["value"] == 1.0


class TestIntegrityCoverage:

    def test_missing_bound_objects_make_integrity_partial(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle).to_dict()  # no objects_dir
        assert result["axes"]["integrity"]["availability"] == "partial"

    def test_no_checkable_units_at_all_is_unavailable(self):
        # A single artefact with no incoming/outgoing edges and no bound file.
        hash_value = fake_hash("lonely-artefact")
        bundle = single_artefact_bundle(hash_value, "metadata/ddex-rin", {
            "production": {"session_id": "S-999"},
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["integrity"]["availability"] == "unavailable"


# --------------------------------------------------------------------------
# Completeness axis
# --------------------------------------------------------------------------

class TestCompletenessAxis:

    def test_no_triggered_expectation_is_unavailable(self):
        hash_value = fake_hash("solo-raw-take")
        bundle = single_artefact_bundle(hash_value, "audio/raw-take", {
            "technical": {"duration_seconds": 1.0},
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["completeness"]["availability"] == "unavailable"

    def test_sample_with_no_incoming_relationship_scores_zero_with_two_findings(self):
        hash_value = fake_hash("orphan-sample")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["completeness"]["value"] == 0.0
        codes = {item["code"] for item in result["findings"]}
        assert "EXPECTED_RELATIONSHIP_ABSENT" in codes
        assert "PRIMARY_EVIDENCE_ABSENT" in codes

    def test_complete_lineage_scores_full_marks(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert result["axes"]["completeness"]["value"] == 1.0
        assert result["axes"]["completeness"]["confidence"] == 0.9

    def test_optional_evidence_absence_does_not_affect_completeness(self, excerpt_setup):
        # No cue sheet, DDEX or C2PA is present anywhere in the fixture, yet
        # completeness is still 1.0 — optional evidence never enters the
        # denominator (per music_profile.json's `optional_types`).
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert result["axes"]["completeness"]["value"] == 1.0


# --------------------------------------------------------------------------
# Attestation axis
# --------------------------------------------------------------------------

class TestAttestationAxis:

    def test_manifest_present_only(self):
        hash_value = fake_hash("c2pa-manifest-only")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
            "provenance": {"present": True},
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        axis = result["axes"]["attestation_strength"]
        assert axis["availability"] == "partial"
        assert axis["value"] == float(mp.PROFILE["attestation"]["levels"]["manifest_present"])

    def test_signature_present_outranks_manifest_only(self):
        hash_value = fake_hash("c2pa-signature")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
            "provenance": {"present": True, "signature_info": {"issuer": "Test"}},
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["attestation_strength"]["value"] == float(
            mp.PROFILE["attestation"]["levels"]["signature_present"])

    def test_cryptographically_valid_flag_scores_higher(self):
        hash_value = fake_hash("c2pa-valid")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
            "provenance": {
                "present": True, "signature_info": {"issuer": "Test"},
                "validation_status": {"valid": True},
            },
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["attestation_strength"]["value"] == float(
            mp.PROFILE["attestation"]["levels"]["cryptographically_valid"])

    def test_credential_trusted_flag_scores_maximum(self):
        hash_value = fake_hash("c2pa-trusted")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
            "provenance": {
                "present": True, "signature_info": {"issuer": "Test"},
                "validation_status": {"trusted": True},
            },
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["attestation_strength"]["value"] == 1.0

    def test_explicit_invalid_flag_overrides_to_zero(self):
        hash_value = fake_hash("c2pa-invalid")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
            "provenance": {
                "present": True, "signature_info": {"issuer": "Test"},
                "validation_status": {"valid": False},
            },
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["attestation_strength"]["value"] == 0.0

    def test_confidence_never_exceeds_metadata_only_ceiling(self):
        hash_value = fake_hash("c2pa-confidence-ceiling")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
            "provenance": {
                "present": True, "signature_info": {"issuer": "Test"},
                "validation_status": {"trusted": True},
            },
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        ceiling = float(mp.PROFILE["attestation"]["metadata_only_confidence_ceiling"])
        assert result["axes"]["attestation_strength"]["confidence"] == ceiling

    def test_absent_c2pa_data_is_unavailable_not_a_failure(self):
        hash_value = fake_hash("no-c2pa-at-all")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["attestation_strength"]["availability"] == "unavailable"


# --------------------------------------------------------------------------
# AI disclosure axis
# --------------------------------------------------------------------------

class TestAIDisclosureAxis:

    def test_explicit_creation_method_counts_as_disclosure(self):
        hash_value = fake_hash("ai-disclosed-recorded")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
            "creation_method": "recorded",
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["ai_disclosure"]["availability"] == "available"
        assert result["axes"]["ai_disclosure"]["value"] == 1.0

    def test_missing_declaration_is_unavailable_when_nothing_is_applicable_elsewhere(self):
        hash_value = fake_hash("ai-undisclosed")
        bundle = single_artefact_bundle(hash_value, "audio/sample", {
            "technical": {"duration_seconds": 0.5},
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["ai_disclosure"]["availability"] == "unavailable"

    def test_ern_ai_declaration_group_also_counts(self):
        hash_value = fake_hash("ern-ai-declared")
        bundle = single_artefact_bundle(hash_value, "metadata/ddex-ern", {
            "release": {"release_id": "REL-002"},
            "AI-declaration": {"value": {"contains_ai_declared": True, "contributions": []}},
        })
        result = mp.evaluate_bundle(bundle).to_dict()
        assert result["axes"]["ai_disclosure"]["availability"] == "available"
        assert result["axes"]["ai_disclosure"]["value"] == 1.0

    def test_partial_coverage_when_some_applicable_artefact_is_undeclared(self):
        # An ERN document reachable via documented_by, with no AI-declaration,
        # alongside a disclosed audio sample: 1 of 2 applicable artefacts.
        sample_hash = fake_hash("partial-sample")
        ern_hash = fake_hash("partial-ern")
        bundle = {
            "schema_version": "0.0.3", "hash_method": "sha256",
            "final_artefact_hash": sample_hash,
            "artefacts": [
                {
                    "artefact_hash": ern_hash, "artefact_type": "metadata/ddex-ern",
                    "attributes": {"release": {"release_id": "REL-003"}},
                    "evidence": [],
                },
                {
                    "artefact_hash": sample_hash, "artefact_type": "audio/sample",
                    "attributes": {
                        "technical": {"duration_seconds": 0.5},
                        "creation_method": "recorded",
                    },
                    "evidence": [{
                        "hash": ern_hash, "relationship_type": "documented_by",
                        "attributes": {
                            "assertion_origin": "ddex-ern",
                            "reference_pointer": "Release[REL-003]",
                            "document_role": "release-message",
                        },
                    }],
                },
            ],
        }
        result = mp.evaluate_bundle(bundle).to_dict()
        axis = result["axes"]["ai_disclosure"]
        assert axis["availability"] == "partial"
        assert axis["value"] == 1.0  # the one assessed artefact is fully disclosed
        assert result["checks"]["ai_disclosure"]["applicable"] == 2
        assert result["checks"]["ai_disclosure"]["assessed"] == 1


# --------------------------------------------------------------------------
# ProfileScore shape
# --------------------------------------------------------------------------

class TestProfileScoreShape:

    def test_to_dict_has_expected_top_level_keys(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert set(result) == {"profile_id", "profile_version", "axes", "findings", "checks"}

    def test_axes_cover_all_four(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert set(result["axes"]) == {
            "completeness", "integrity", "attestation_strength", "ai_disclosure"
        }

    def test_to_dict_is_a_deep_copy(self, excerpt_setup):
        score = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root)
        first = score.to_dict()
        first["axes"]["integrity"]["value"] = -999
        second = score.to_dict()
        assert second["axes"]["integrity"]["value"] != -999


# --------------------------------------------------------------------------
# _bind_object_files
# --------------------------------------------------------------------------

class TestBindObjectFiles:

    def test_nonexistent_objects_dir_raises(self, excerpt_setup):
        with pytest.raises(mp.ProfileError, match="does not exist"):
            mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root / "does-not-exist")

    def test_empty_objects_dir_raises(self, excerpt_setup, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(mp.ProfileError, match="contains no files"):
            mp.evaluate_bundle(excerpt_setup.bundle, empty_dir)

    def test_unmatched_file_aborts_the_run(self, excerpt_setup, tmp_path):
        stray_dir = tmp_path / "stray"
        stray_dir.mkdir()
        (stray_dir / "unrelated.wav").write_bytes(b"not related to any artefact hash")
        with pytest.raises(mp.ProfileError, match="could not bind object file"):
            mp.evaluate_bundle(excerpt_setup.bundle, stray_dir)

    def test_matching_objects_bind_and_are_used_by_integrity(self, excerpt_setup):
        result = mp.evaluate_bundle(excerpt_setup.bundle, excerpt_setup.root).to_dict()
        assert result["axes"]["integrity"]["availability"] == "available"


# --------------------------------------------------------------------------
# self_test() and CLI
# --------------------------------------------------------------------------

class TestSelfTest:

    def test_self_test_reports_ok(self):
        result = mp.self_test()
        assert result["status"] == "ok"
        assert result["overall_score_invented"] is False

    def test_self_test_result_is_json_serialisable(self):
        result = mp.self_test()
        json.dumps(result)


class TestCLI:

    def test_show_profile_prints_the_loaded_profile(self, capsys):
        rc = mp.main(["--show-profile"])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["profile_id"] == mp.PROFILE["profile_id"]

    def test_self_test_flag_prints_ok(self, capsys):
        rc = mp.main(["--self-test"])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["status"] == "ok"

    def test_no_bundle_and_no_flag_is_an_error(self):
        with pytest.raises(SystemExit):
            mp.main([])

    def test_show_profile_combined_with_bundle_is_an_error(self, tmp_path):
        dummy = tmp_path / "bundle.json"
        dummy.write_text("{}", encoding="utf-8")
        with pytest.raises(SystemExit):
            mp.main(["--show-profile", str(dummy)])

    def test_invalid_bundle_file_exits_with_status_2(self, tmp_path, capsys):
        bad = tmp_path / "bundle.json"
        bad.write_text("{not valid json", encoding="utf-8")
        rc = mp.main([str(bad)])
        assert rc == 2
        assert "profile evaluation failed" in capsys.readouterr().err

    def test_successful_run_writes_assessment_file(self, excerpt_setup, tmp_path_factory, capsys):
        # excerpt_setup and this test share one tmp_path, so a subdirectory of
        # it is still inside --objects-dir's tree: _bind_object_files uses
        # rglob("*"), which would recurse into it and try (and fail) to bind
        # bundle.json as an artefact. Use a fully separate temp directory.
        cli_dir = tmp_path_factory.mktemp("cli")
        bundle_path = cli_dir / "bundle.json"
        bundle_path.write_text(json.dumps(excerpt_setup.bundle), encoding="utf-8")
        out_path = cli_dir / "assessment.json"
        rc = mp.main([
            str(bundle_path), "--objects-dir", str(excerpt_setup.root), "-o", str(out_path),
        ])
        assert rc == 0
        written = json.loads(out_path.read_text(encoding="utf-8"))
        assert written["axes"]["integrity"]["value"] == 1.0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
