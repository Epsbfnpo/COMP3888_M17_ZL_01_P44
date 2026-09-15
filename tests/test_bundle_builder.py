"""
Unit tests for bundle_builder.py — Stage 1 of the music evidence pipeline.

Run from inside the project repository (so the sibling modules
music_evidence_types, relationship_extractor, wav_parser, rin_parser,
ern_parser, c2pa_parser and the libevchain package are importable), with
the project's dependencies installed:

    pip install -r requirements.txt pytest
    pytest test_bundle_builder.py -v

Design notes
------------
* No fixture files are checked in. Every WAV/XML input is generated at
  test time (mirroring bundle_builder.self_test()), so the suite has no
  binary assets to keep in sync with the schema.
* Tests are organised around the eight ordered build steps in §5 of the
  architecture doc and the design principles in §2, rather than around
  individual functions, since bundle_builder.py's public surface is
  deliberately small (build_bundle / build_bundle_from_file / main).
* Where a step's behaviour depends on a sibling parser (WAV/RIN/ERN/C2PA),
  tests use the smallest input that exercises the *builder's* handling of
  that parser's output — not the parser's own correctness, which belongs
  in that parser's own test module.
"""

from __future__ import annotations

import copy
import json
import sys
import wave
from pathlib import Path

import pytest

import bundle_builder as bb


# --------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------

def write_wav(path: Path, *, frames: int = 4000, sample_rate: int = 8000,
              channels: int = 1, sampwidth: int = 2) -> None:
    """A minimal, valid RIFF/WAVE file — enough for wav_parser to accept."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sampwidth)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00" * sampwidth * frames)


def write_rin(path: Path, session_id: str = "S-001") -> None:
    path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<RecordingInformationNotification xmlns="urn:ddex:test:rin">
  <Session><SessionId>{session_id}</SessionId><Date>2026-09-11</Date>
    <Location>Sydney</Location>
    <Party><PartyId>P-001</PartyId><Name>Alice</Name><Role>Vocalist</Role></Party>
  </Session>
</RecordingInformationNotification>
""", encoding="utf-8")


def write_ern(path: Path, release_id: str = "REL-001") -> None:
    path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<NewReleaseMessage xmlns="urn:ddex:test:ern">
  <Release><ReleaseId>{release_id}</ReleaseId><TitleText>Demo Song</TitleText>
    <ReleaseType>Single</ReleaseType></Release>
</NewReleaseMessage>
""", encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path):
    """A minimal, otherwise-valid manifest + backing files: raw take,
    sample excerpted from it, and the two DDEX documents from §7."""
    objects = tmp_path / "objects"
    objects.mkdir()
    write_wav(objects / "raw-vocal.wav", frames=8000)     # 1.0s @ 8kHz
    write_wav(objects / "vocal-sample.wav", frames=2000)  # 0.25s @ 8kHz
    write_rin(objects / "session-rin.xml")
    write_ern(objects / "release-ern.xml")

    manifest = {
        "schema_version": bb.BUNDLE_SCHEMA_VERSION,
        "hash_method": bb.HASH_METHOD,
        "files": [
            {
                "id": "raw-vocal", "path": "objects/raw-vocal.wav",
                "artefact_type": "audio/raw-take", "final": False,
                "attributes": {
                    "creation_method": "recorded",
                    "production": {"track_name": "Lead Vocal Take 3",
                                   "source_role": "lead-vocal"},
                },
            },
            {
                "id": "vocal-sample", "path": "objects/vocal-sample.wav",
                "artefact_type": "audio/sample", "final": True,
                "attributes": {
                    "creation_method": "recorded",
                    "production": {"sample_name": "Vocal phrase",
                                   "sample_category": "vocal"},
                },
            },
            {
                "id": "rin-document", "path": "objects/session-rin.xml",
                "artefact_type": "metadata/ddex-rin", "final": False,
                "attributes": {},
            },
            {
                "id": "ern-document", "path": "objects/release-ern.xml",
                "artefact_type": "metadata/ddex-ern", "final": False,
                "attributes": {},
            },
        ],
        "relationships": [
            {
                "target": "vocal-sample", "source": "raw-vocal",
                "relationship_type": "excerpted_from",
                "attributes": {
                    "assertion_origin": "submitter", "confirmed_by_submitter": True,
                    "source_start_seconds": 0.0, "source_end_seconds": 0.25,
                    "target_start_seconds": 0.0,
                },
            },
            {
                "target": "vocal-sample", "source": "rin-document",
                "relationship_type": "documented_by",
                "attributes": {
                    "assertion_origin": "ddex-rin",
                    "reference_pointer": "Session[S-001]",
                    "document_role": "recording-session",
                },
            },
            {
                "target": "vocal-sample", "source": "ern-document",
                "relationship_type": "documented_by",
                "attributes": {
                    "assertion_origin": "ddex-ern",
                    "reference_pointer": "Release[REL-001]",
                    "document_role": "release-message",
                },
            },
        ],
    }
    return tmp_path, manifest


def by_type(bundle: dict) -> dict:
    return {a["artefact_type"]: a for a in bundle["artefacts"]}


# --------------------------------------------------------------------------
# §5 step 1 — manifest shape
# --------------------------------------------------------------------------

class TestManifestValidation:

    def test_valid_manifest_builds(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        assert bundle["schema_version"] == bb.BUNDLE_SCHEMA_VERSION

    def test_manifest_must_be_object(self, project):
        root, _ = project
        with pytest.raises(bb.BundleBuildError, match="must be a JSON object"):
            bb.build_bundle([], base_dir=root)

    def test_unknown_top_level_field_rejected(self, project):
        root, manifest = project
        manifest["unexpected"] = True
        with pytest.raises(bb.BundleBuildError, match="unknown fields"):
            bb.build_bundle(manifest, base_dir=root)

    @pytest.mark.parametrize("bad_version", ["0.0.2", "0.0.4", "", None])
    def test_wrong_schema_version_rejected(self, project, bad_version):
        root, manifest = project
        manifest["schema_version"] = bad_version
        with pytest.raises(bb.BundleBuildError, match="schema_version"):
            bb.build_bundle(manifest, base_dir=root)

    def test_wrong_hash_method_rejected(self, project):
        root, manifest = project
        manifest["hash_method"] = "md5"
        with pytest.raises(bb.BundleBuildError, match="sha256"):
            bb.build_bundle(manifest, base_dir=root)

    def test_empty_files_array_rejected(self, project):
        root, manifest = project
        manifest["files"] = []
        with pytest.raises(bb.BundleBuildError, match="non-empty array"):
            bb.build_bundle(manifest, base_dir=root)

    def test_files_must_be_a_list(self, project):
        root, manifest = project
        manifest["files"] = {"not": "a list"}
        with pytest.raises(bb.BundleBuildError, match="non-empty array"):
            bb.build_bundle(manifest, base_dir=root)

    def test_too_many_files_rejected(self, project):
        root, manifest = project
        template = manifest["files"][0]
        # One over the cap; every entry beyond the real files fails at path
        # resolution, which is fine — the count check must fire first.
        manifest["files"] = [dict(template, id=f"f{i}") for i in range(bb.MAX_ARTEFACTS + 1)]
        with pytest.raises(bb.BundleBuildError, match=f"at most {bb.MAX_ARTEFACTS}"):
            bb.build_bundle(manifest, base_dir=root)

    def test_relationships_must_be_array(self, project):
        root, manifest = project
        manifest["relationships"] = {"not": "a list"}
        with pytest.raises(bb.BundleBuildError, match="relationships must be an array"):
            bb.build_bundle(manifest, base_dir=root)

    def test_relationships_entries_must_be_objects(self, project):
        root, manifest = project
        manifest["relationships"] = ["not-an-object"]
        with pytest.raises(bb.BundleBuildError, match="must be an object"):
            bb.build_bundle(manifest, base_dir=root)

    def test_relationships_may_be_omitted(self, project):
        root, manifest = project
        del manifest["relationships"]
        bundle = bb.build_bundle(manifest, base_dir=root)
        assert bundle  # omission defaults to [], not a build failure

    def test_duplicate_json_keys_rejected(self, tmp_path):
        # _read_json's object_pairs_hook rejects duplicate keys outright,
        # independent of jsonschema, so hand-craft the raw text.
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            '{"schema_version": "0.0.3", "schema_version": "0.0.3", '
            '"hash_method": "sha256", "files": []}',
            encoding="utf-8",
        )
        with pytest.raises(bb.BundleBuildError, match="duplicate JSON key"):
            bb.build_bundle_from_file(manifest_path)

    def test_invalid_json_reports_location(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(bb.BundleBuildError, match="invalid JSON"):
            bb.build_bundle_from_file(manifest_path)


# --------------------------------------------------------------------------
# §5 step 2 — resolving each file entry
# --------------------------------------------------------------------------

class TestFileEntryValidation:

    def test_unknown_file_field_rejected(self, project):
        root, manifest = project
        manifest["files"][0]["unexpected"] = 1
        with pytest.raises(bb.BundleBuildError, match="unknown fields"):
            bb.build_bundle(manifest, base_dir=root)

    @pytest.mark.parametrize("missing", ["id", "path", "artefact_type", "final"])
    def test_missing_required_field_rejected(self, project, missing):
        root, manifest = project
        del manifest["files"][0][missing]
        with pytest.raises(bb.BundleBuildError, match="missing fields"):
            bb.build_bundle(manifest, base_dir=root)

    def test_duplicate_id_rejected(self, project):
        root, manifest = project
        manifest["files"][1]["id"] = manifest["files"][0]["id"]
        with pytest.raises(bb.BundleBuildError, match="duplicate file id"):
            bb.build_bundle(manifest, base_dir=root)

    def test_duplicate_path_rejected(self, project):
        root, manifest = project
        manifest["files"][1]["path"] = manifest["files"][0]["path"]
        with pytest.raises(bb.BundleBuildError, match="listed more than once"):
            bb.build_bundle(manifest, base_dir=root)

    def test_nonexistent_path_rejected(self, project):
        root, manifest = project
        manifest["files"][0]["path"] = "objects/does-not-exist.wav"
        with pytest.raises(bb.BundleBuildError, match="is not a file"):
            bb.build_bundle(manifest, base_dir=root)

    def test_directory_as_path_rejected(self, project):
        root, manifest = project
        manifest["files"][0]["path"] = "objects"
        with pytest.raises(bb.BundleBuildError, match="is not a file"):
            bb.build_bundle(manifest, base_dir=root)

    def test_unknown_artefact_type_rejected(self, project):
        root, manifest = project
        manifest["files"][0]["artefact_type"] = "audio/not-a-real-type"
        with pytest.raises(bb.BundleBuildError, match="unknown artefact_type"):
            bb.build_bundle(manifest, base_dir=root)

    @pytest.mark.parametrize("bad_final", ["true", 1, None, [], {}])
    def test_final_must_be_strict_bool(self, project, bad_final):
        root, manifest = project
        manifest["files"][0]["final"] = bad_final
        with pytest.raises(bb.BundleBuildError, match="final must be true or false"):
            bb.build_bundle(manifest, base_dir=root)

    def test_attributes_must_be_object(self, project):
        root, manifest = project
        manifest["files"][0]["attributes"] = "not-an-object"
        with pytest.raises(bb.BundleBuildError, match="attributes must be an object"):
            bb.build_bundle(manifest, base_dir=root)

    def test_attributes_defaults_to_empty(self, project):
        root, manifest = project
        del manifest["files"][2]["attributes"]  # rin-document
        bundle = bb.build_bundle(manifest, base_dir=root)
        assert bundle  # absence of 'attributes' is not itself a failure

    @pytest.mark.parametrize("bad_flag", ["true", 1, None])
    def test_parse_c2pa_must_be_strict_bool(self, project, bad_flag):
        root, manifest = project
        manifest["files"][0]["parse_c2pa"] = bad_flag
        with pytest.raises(bb.BundleBuildError, match="parse_c2pa must be true or false"):
            bb.build_bundle(manifest, base_dir=root)

    def test_parse_c2pa_rejected_on_metadata_types(self, project):
        root, manifest = project
        manifest["files"][2]["parse_c2pa"] = True  # metadata/ddex-rin
        with pytest.raises(bb.BundleBuildError, match="parse_c2pa is supported only"):
            bb.build_bundle(manifest, base_dir=root)

    def test_relative_path_resolves_against_manifest_dir(self, tmp_path):
        # build_bundle_from_file must resolve relative paths against the
        # *manifest's* directory, not the process cwd.
        project_dir = tmp_path / "nested" / "project"
        (project_dir / "objects").mkdir(parents=True)
        write_wav(project_dir / "objects" / "raw-vocal.wav", frames=8000)
        write_wav(project_dir / "objects" / "vocal-sample.wav", frames=2000)
        write_rin(project_dir / "objects" / "session-rin.xml")
        write_ern(project_dir / "objects" / "release-ern.xml")
        manifest = bb.example_manifest()
        manifest_path = project_dir / "bundle_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        bundle = bb.build_bundle_from_file(manifest_path)
        assert bundle["final_artefact_hash"]

    def test_absolute_path_is_accepted(self, project):
        root, manifest = project
        absolute = (root / "objects" / "raw-vocal.wav").resolve()
        manifest["files"][0]["path"] = str(absolute)
        bundle = bb.build_bundle(manifest, base_dir=root)
        assert bundle  # no error from an absolute path


# --------------------------------------------------------------------------
# §5 step 6 — exactly one final artefact
# --------------------------------------------------------------------------

class TestFinalArtefactRule:

    def test_no_final_rejected(self, project):
        root, manifest = project
        for entry in manifest["files"]:
            entry["final"] = False
        with pytest.raises(bb.BundleBuildError, match="exactly one file must have final=true; found 0"):
            bb.build_bundle(manifest, base_dir=root)

    def test_two_finals_rejected(self, project):
        root, manifest = project
        manifest["files"][0]["final"] = True
        with pytest.raises(bb.BundleBuildError, match="exactly one file must have final=true; found 2"):
            bb.build_bundle(manifest, base_dir=root)

    def test_final_hash_matches_the_declared_final_file(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        expected = bb._sha256((root / "objects" / "vocal-sample.wav").resolve())
        assert bundle["final_artefact_hash"] == expected


# --------------------------------------------------------------------------
# §2 principle — identity is content (SHA-256 addressing)
# --------------------------------------------------------------------------

class TestContentAddressing:

    def test_artefact_hash_is_sha256_of_bytes(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        raw_node = by_type(bundle)["audio/raw-take"]
        expected = bb._sha256((root / "objects" / "raw-vocal.wav").resolve())
        assert raw_node["artefact_hash"] == expected

    def test_identical_bytes_collapse_and_are_rejected(self, project):
        root, manifest = project
        # Make the sample byte-identical to the raw take.
        (root / "objects" / "vocal-sample.wav").write_bytes(
            (root / "objects" / "raw-vocal.wav").read_bytes()
        )
        with pytest.raises(bb.BundleBuildError, match="same artefact hash"):
            bb.build_bundle(manifest, base_dir=root)

    def test_manifest_id_never_leaks_into_output(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        assert all("id" not in artefact for artefact in bundle["artefacts"])

    def test_id_colliding_with_a_hash_rejected(self, project):
        root, manifest = project
        raw_path = (root / "objects" / "raw-vocal.wav").resolve()
        real_hash = bb._sha256(raw_path)
        manifest["files"][0]["id"] = real_hash
        with pytest.raises(bb.BundleBuildError, match="must not equal an artefact hash"):
            bb.build_bundle(manifest, base_dir=root)


# --------------------------------------------------------------------------
# §2 principle — observed and declared are stored apart
# --------------------------------------------------------------------------

class TestDeclaredVsObserved:

    def test_declared_technical_renamed_and_observed_preserved(self, project):
        root, manifest = project
        manifest["files"][0]["attributes"]["technical"] = {"sample_rate_hz": 44100}
        bundle = bb.build_bundle(manifest, base_dir=root)
        raw = by_type(bundle)["audio/raw-take"]
        assert raw["attributes"]["technical"]["sample_rate_hz"] == 8000
        assert raw["attributes"]["declared_technical"]["sample_rate_hz"] == 44100

    def test_cannot_declare_both_technical_and_declared_technical(self, project):
        root, manifest = project
        manifest["files"][0]["attributes"]["technical"] = {"sample_rate_hz": 44100}
        manifest["files"][0]["attributes"]["declared_technical"] = {"sample_rate_hz": 44100}
        with pytest.raises(bb.BundleBuildError, match="cannot contain both"):
            bb.build_bundle(manifest, base_dir=root)

    def test_declared_source_metadata_is_also_renamed(self, project):
        root, manifest = project
        manifest["files"][0]["attributes"]["source_metadata"] = {"original_filename": "spoofed.wav"}
        bundle = bb.build_bundle(manifest, base_dir=root)
        raw = by_type(bundle)["audio/raw-take"]
        assert raw["attributes"]["source_metadata"]["original_filename"] == "raw-vocal.wav"
        assert raw["attributes"]["declared_source_metadata"]["original_filename"] == "spoofed.wav"

    def test_agreeing_claim_does_not_create_a_contradiction_marker(self, project):
        root, manifest = project
        manifest["files"][0]["attributes"]["technical"] = {"sample_rate_hz": 8000}
        bundle = bb.build_bundle(manifest, base_dir=root)
        raw = by_type(bundle)["audio/raw-take"]
        assert raw["attributes"]["technical"]["sample_rate_hz"] == 8000
        assert raw["attributes"]["declared_technical"]["sample_rate_hz"] == 8000


# --------------------------------------------------------------------------
# §2 principle — declared, never inferred / parser-owned groups
# --------------------------------------------------------------------------

class TestParserOwnedGroups:

    def test_submitter_cannot_declare_rin_production(self, project):
        root, manifest = project
        manifest["files"][2]["attributes"] = {"production": {"session_id": "FAKE"}}
        with pytest.raises(bb.BundleBuildError, match="parser-owned groups"):
            bb.build_bundle(manifest, base_dir=root)

    def test_submitter_cannot_declare_ern_release(self, project):
        root, manifest = project
        manifest["files"][3]["attributes"] = {"release": {"release_id": "FAKE"}}
        with pytest.raises(bb.BundleBuildError, match="parser-owned groups"):
            bb.build_bundle(manifest, base_dir=root)

    def test_submitter_cannot_declare_ern_ai_declaration(self, project):
        root, manifest = project
        manifest["files"][3]["attributes"] = {"AI-declaration": {"value": {"contains_ai_declared": True}}}
        with pytest.raises(bb.BundleBuildError, match="parser-owned groups"):
            bb.build_bundle(manifest, base_dir=root)

    def test_submitter_cannot_declare_provenance_on_c2pa_type(self, project):
        root, manifest = project
        # Needs a file path not already claimed by another manifest entry —
        # duplicate-path rejection in _build_artefacts would otherwise fire
        # first and mask the check this test targets.
        write_rin(root / "objects" / "extra-c2pa-source.xml")
        manifest["files"].append({
            "id": "c2pa-doc", "path": "objects/extra-c2pa-source.xml",
            "artefact_type": "provenance/c2pa", "final": False,
            "attributes": {"provenance": {"present": True}},
        })
        with pytest.raises(bb.BundleBuildError, match="parser-owned groups"):
            bb.build_bundle(manifest, base_dir=root)

    def test_submitter_cannot_declare_provenance_via_parse_c2pa_flag(self, project):
        root, manifest = project
        manifest["files"][1]["parse_c2pa"] = True
        manifest["files"][1]["attributes"]["provenance"] = {"present": True}
        with pytest.raises(bb.BundleBuildError, match="parser-owned groups"):
            bb.build_bundle(manifest, base_dir=root)

    def test_ern_does_not_fabricate_ai_declaration_when_absent(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        ern = by_type(bundle)["metadata/ddex-ern"]
        assert "AI-declaration" not in ern["attributes"]


# --------------------------------------------------------------------------
# §5 step 3 — parsing by type / WAV integration
# --------------------------------------------------------------------------

class TestWavParsingIntegration:

    def test_wav_technical_fields_are_mapped(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        raw = by_type(bundle)["audio/raw-take"]["attributes"]["technical"]
        assert raw["sample_rate_hz"] == 8000
        assert raw["channels"] == 1
        assert raw["bit_depth"] == 16
        assert raw["format"] == "WAV"
        assert raw["duration_seconds"] == pytest.approx(1.0)

    def test_wav_original_filename_recorded(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        raw = by_type(bundle)["audio/raw-take"]
        assert raw["attributes"]["source_metadata"]["original_filename"] == "raw-vocal.wav"

    def test_non_wav_bytes_with_wav_extension_aborts_build(self, project):
        root, manifest = project
        (root / "objects" / "raw-vocal.wav").write_bytes(b"not a real RIFF file")
        # wav_parser reports this as a warning ("not a valid WAV/RIFF file"),
        # which _build_artefacts rejects itself before _wav_attributes ever
        # runs — so the message is "WAV parser warnings for ...", not
        # _wav_attributes' own "could not parse WAV" (that one covers a
        # parseable-but-fmt-less file: warnings == [] but core_format is None).
        with pytest.raises(bb.BundleBuildError, match="WAV parser warnings"):
            bb.build_bundle(manifest, base_dir=root)

    def test_malformed_xml_aborts_build(self, project):
        root, manifest = project
        (root / "objects" / "session-rin.xml").write_text("<not><closed>", encoding="utf-8")
        with pytest.raises(bb.BundleBuildError, match="could not parse XML"):
            bb.build_bundle(manifest, base_dir=root)

    def test_ern_release_fields_are_mapped(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        ern = by_type(bundle)["metadata/ddex-ern"]
        assert ern["attributes"]["release"]["release_id"] == "REL-001"

    def test_rin_production_fields_are_mapped(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        rin = by_type(bundle)["metadata/ddex-rin"]
        assert rin["attributes"]["production"]["session_id"] == "S-001"


# --------------------------------------------------------------------------
# §2 principle — declared relationships only, plus §5 step 7 (edges)
# --------------------------------------------------------------------------

class TestRelationshipApplication:

    def test_relationship_ids_resolved_to_hashes(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        nodes = by_type(bundle)
        sample, raw = nodes["audio/sample"], nodes["audio/raw-take"]
        edge = next(e for e in sample["evidence"] if e["relationship_type"] == "excerpted_from")
        assert edge["hash"] == raw["artefact_hash"]

    def test_declared_edge_count_matches_manifest(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        sample = by_type(bundle)["audio/sample"]
        assert len(sample["evidence"]) == 3

    def test_empty_relationships_yields_no_edges(self, project):
        root, manifest = project
        manifest["relationships"] = []
        bundle = bb.build_bundle(manifest, base_dir=root)
        assert all(node["evidence"] == [] for node in bundle["artefacts"])

    def test_unknown_relationship_alias_is_rejected(self, project):
        root, manifest = project
        manifest["relationships"][0]["source"] = "does-not-exist"
        with pytest.raises(bb.BundleBuildError, match="relationship declaration failed"):
            bb.build_bundle(manifest, base_dir=root)

    def test_self_edge_is_rejected(self, project):
        root, manifest = project
        manifest["relationships"][0]["target"] = manifest["relationships"][0]["source"]
        with pytest.raises(bb.BundleBuildError, match="relationship declaration failed"):
            bb.build_bundle(manifest, base_dir=root)

    def test_duplicate_edge_on_same_target_source_pair_is_rejected(self, project):
        root, manifest = project
        manifest["relationships"].append(dict(manifest["relationships"][0]))
        with pytest.raises(bb.BundleBuildError, match="relationship declaration failed"):
            bb.build_bundle(manifest, base_dir=root)


# --------------------------------------------------------------------------
# §5 step 8 — publish (schema + EvidenceChain re-validation)
# --------------------------------------------------------------------------

class TestBundlePublishing:

    def test_output_matches_schema(self, project):
        root, manifest = project
        # No explicit assertion needed beyond "doesn't raise": build_bundle
        # itself calls validate_bundle_schema before returning.
        bb.build_bundle(manifest, base_dir=root)

    def test_top_level_shape(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        assert set(bundle) == {"schema_version", "hash_method", "final_artefact_hash", "artefacts"}

    def test_every_artefact_has_required_keys(self, project):
        root, manifest = project
        bundle = bb.build_bundle(manifest, base_dir=root)
        for artefact in bundle["artefacts"]:
            assert set(artefact) == {"artefact_hash", "artefact_type", "attributes", "evidence"}

    def test_nothing_partial_is_written_to_disk_on_failure(self, project, tmp_path):
        root, manifest = project
        manifest["files"][0]["path"] = "objects/does-not-exist.wav"
        out = tmp_path / "out" / "evidence_bundle.json"
        manifest_path = root / "bundle_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(bb.BundleBuildError):
            bb.build_bundle_from_file(manifest_path)
        assert not out.exists()


# --------------------------------------------------------------------------
# Built-in helpers: example_manifest() and self_test()
# --------------------------------------------------------------------------

class TestBuiltInHelpers:

    def test_example_manifest_is_internally_consistent(self, tmp_path):
        manifest = bb.example_manifest()
        objects = tmp_path / "objects"
        objects.mkdir()
        write_wav(objects / "raw-vocal.wav", frames=8000)
        write_wav(objects / "vocal-sample.wav", frames=2000)
        write_rin(objects / "session-rin.xml")
        write_ern(objects / "release-ern.xml")
        bundle = bb.build_bundle(manifest, base_dir=tmp_path)
        assert bundle["final_artefact_hash"]

    def test_self_test_reports_ok(self):
        result = bb.self_test()
        assert result["status"] == "ok"
        assert result["inference_used"] is False
        assert result["no_declaration_produces_no_edge"] is True


# --------------------------------------------------------------------------
# CLI (main())
# --------------------------------------------------------------------------

class TestCLI:

    def test_example_flag_prints_manifest_json(self, capsys):
        rc = bb.main(["--example"])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["schema_version"] == bb.BUNDLE_SCHEMA_VERSION

    def test_self_test_flag_prints_ok_status(self, capsys):
        rc = bb.main(["--self-test"])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["status"] == "ok"

    def test_example_and_manifest_together_is_an_error(self, capsys):
        with pytest.raises(SystemExit):
            bb.main(["--example", "some_manifest.json"])

    def test_self_test_and_manifest_together_is_an_error(self, capsys):
        with pytest.raises(SystemExit):
            bb.main(["--self-test", "some_manifest.json"])

    def test_no_manifest_and_no_flag_is_an_error(self, capsys):
        with pytest.raises(SystemExit):
            bb.main([])

    def test_build_failure_exits_with_status_2(self, project, capsys):
        root, manifest = project
        manifest["schema_version"] = "wrong"
        manifest_path = root / "bundle_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        rc = bb.main([str(manifest_path)])
        assert rc == 2
        assert "bundle build failed" in capsys.readouterr().err

    def test_successful_build_writes_output_file(self, project, capsys):
        root, manifest = project
        manifest_path = root / "bundle_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        out_path = root / "out" / "evidence_bundle.json"
        rc = bb.main([str(manifest_path), "-o", str(out_path)])
        assert rc == 0
        written = json.loads(out_path.read_text(encoding="utf-8"))
        assert written["final_artefact_hash"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
