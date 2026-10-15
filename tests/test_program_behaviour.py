"""
~50 test

Tests for:
- WAV parsing
- DDEX RIN
- DDEX ERN
- bundle building
- relationship extraction
- Cue Sheet parsing
- Comp Sheet parsing

Run from the repository root:
    python -m pytest tests/test_program_behaviour.py -v
"""

import copy
import hashlib
import wave
import xml.etree.ElementTree as ET

import pytest

from bundle_builder import (
    BUNDLE_SCHEMA_VERSION,
    HASH_METHOD,
    BundleBuildError,
    _compact,
    _nonempty_string,
    _read_json,
    _sha256,
    _validate_manifest,
    build_bundle,
)
from ern_parser import AI_DECLARATION_LIMITATION, parse_ern_file
from rin_parser import parse_rin_file
from wav_parser import parse_wav_file
from xml_utils import first_child_text, iter_by_local_name, local_name
import relationship_extractor as relationships
from trial import Take_comps as takes_comps

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_wav(path, sample_rate=44100, channels=1, sample_width=2, frames=441):
    frame = b"\x00" * (channels * sample_width)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(frame * frames)

    return path


def minimal_manifest(path, final=True, attributes=None):
    if attributes is None:
        attributes = {"creation_method": "recorded"}

    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "hash_method": HASH_METHOD,
        "files": [
            {
                "id": "take-1",
                "path": path,
                "artefact_type": "audio/raw-take",
                "final": final,
                "attributes": attributes,
            }
        ],
        "relationships": [],
    }


class FakePage:
    def __init__(self, text="", tables=None):
        self.text = text
        self.tables = tables or []

    def extract_text(self):
        return self.text

    def extract_tables(self):
        return self.tables


class FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


# ===========================================================================
# WAV PARSER TESTS: 1-8
# ===========================================================================

def test_01_valid_wav_is_parsed(tmp_path):
    result = parse_wav_file(str(make_wav(tmp_path / "valid.wav")))
    assert result.warnings == []
    assert result.core_format is not None


def test_02_wav_sample_rate_is_correct(tmp_path):
    result = parse_wav_file(
        str(make_wav(tmp_path / "rate.wav", sample_rate=48000))
    )
    assert result.core_format.sample_rate_hz == 48000


def test_03_wav_channel_count_is_correct(tmp_path):
    result = parse_wav_file(
        str(make_wav(tmp_path / "stereo.wav", channels=2))
    )
    assert result.core_format.num_channels == 2


def test_04_wav_bit_depth_is_correct(tmp_path):
    result = parse_wav_file(
        str(make_wav(tmp_path / "bits.wav", sample_width=2))
    )
    assert result.core_format.bits_per_sample == 16


def test_05_wav_duration_is_calculated(tmp_path):
    result = parse_wav_file(
        str(make_wav(
            tmp_path / "duration.wav",
            sample_rate=1000,
            frames=500,
        ))
    )
    assert result.core_format.duration_seconds == pytest.approx(0.5)


def test_06_invalid_wav_returns_warning(tmp_path):
    path = tmp_path / "invalid.wav"
    path.write_bytes(b"not a real wav")

    result = parse_wav_file(str(path))

    assert result.core_format is None
    assert "not a valid WAV/RIFF file" in result.warnings


def test_07_missing_wav_returns_warning(tmp_path):
    result = parse_wav_file(str(tmp_path / "missing.wav"))

    assert result.core_format is None
    assert any(
        "could not read file:" in warning
        for warning in result.warnings
    )


def test_08_wav_pcm_format_is_reported(tmp_path):
    result = parse_wav_file(str(make_wav(tmp_path / "pcm.wav")))
    assert result.core_format.audio_format == "PCM"


# ===========================================================================
# XML / RIN / ERN TESTS: 9-18
# ===========================================================================

def test_09_local_name_removes_namespace():
    assert local_name("{urn:test}Session") == "Session"


def test_10_iter_by_local_name_is_case_insensitive():
    root = ET.fromstring(
        "<Root><SESSION/><session/><Session/></Root>"
    )
    assert len(list(iter_by_local_name(root, "session"))) == 3


def test_11_first_child_text_returns_text():
    root = ET.fromstring(
        "<Root><Title>  Hello  </Title></Root>"
    )
    assert first_child_text(root, "Title") == "Hello"


def test_12_valid_rin_session_is_parsed(tmp_path):
    path = tmp_path / "rin.xml"
    path.write_text(
        "<RIN>"
        "<Session>"
        "<SessionId>S-001</SessionId>"
        "<Date>2026-10-15</Date>"
        "<Location>Sydney</Location>"
        "</Session>"
        "<RecordingComponent>"
        "<ComponentId>C-001</ComponentId>"
        "</RecordingComponent>"
        "</RIN>",
        encoding="utf-8",
    )

    result = parse_rin_file(str(path))

    assert result.sessions[0].session_id == "S-001"
    assert result.sessions[0].location == "Sydney"


def test_13_rin_contributor_is_parsed(tmp_path):
    path = tmp_path / "rin.xml"
    path.write_text(
        "<RIN>"
        "<Session>"
        "<Party>"
        "<PartyId>P-1</PartyId>"
        "<Name>Peter</Name>"
        "<Role>Engineer</Role>"
        "</Party>"
        "</Session>"
        "<RecordingComponent>"
        "<ComponentId>C-1</ComponentId>"
        "</RecordingComponent>"
        "</RIN>",
        encoding="utf-8",
    )

    result = parse_rin_file(str(path))

    assert result.contributors[0].name == "Peter"
    assert result.contributors[0].role == "Engineer"


def test_14_malformed_rin_gives_warning(tmp_path):
    path = tmp_path / "rin.xml"
    path.write_text("<RIN><Session>", encoding="utf-8")

    result = parse_rin_file(str(path))

    assert any(
        warning.startswith("malformed XML:")
        for warning in result.warnings
    )


def test_15_valid_ern_release_is_parsed(tmp_path):
    path = tmp_path / "ern.xml"
    path.write_text(
        "<ERN>"
        "<Release>"
        "<ReleaseId>REL-1</ReleaseId>"
        "<TitleText>Run Run Run</TitleText>"
        "<ReleaseType>Single</ReleaseType>"
        "</Release>"
        "</ERN>",
        encoding="utf-8",
    )

    result = parse_ern_file(str(path))

    assert result.releases[0].release_id == "REL-1"
    assert result.releases[0].title == "Run Run Run"
    assert result.releases[0].release_type == "Single"


def test_16_ern_namespace_is_ignored(tmp_path):
    path = tmp_path / "ern.xml"
    path.write_text(
        '<ERN xmlns="urn:test">'
        "<Release>"
        "<ReleaseId>REL-NS</ReleaseId>"
        "</Release>"
        "</ERN>",
        encoding="utf-8",
    )

    result = parse_ern_file(str(path))

    assert result.releases[0].release_id == "REL-NS"


def test_17_ern_ai_fields_are_not_guessed(tmp_path):
    path = tmp_path / "ern.xml"
    path.write_text(
        "<ERN>"
        "<Release>"
        "<ReleaseId>REL-1</ReleaseId>"
        "</Release>"
        "</ERN>",
        encoding="utf-8",
    )

    result = parse_ern_file(str(path))

    assert result.contains_ai_declared is None
    assert result.ai_contributions == []
    assert AI_DECLARATION_LIMITATION in result.warnings


def test_18_malformed_ern_gives_warning(tmp_path):
    path = tmp_path / "ern.xml"
    path.write_text("<ERN><Release>", encoding="utf-8")

    result = parse_ern_file(str(path))

    assert any(
        warning.startswith("malformed XML:")
        for warning in result.warnings
    )


# ===========================================================================
# CUE SHEET TESTS: 19-30
# ===========================================================================

def test_19_extract_song_title_with_normal_quotes():
    text = 'Song: "Run Run Run"'
    assert takes_comps.extract_song_title(text) == "Run Run Run"


def test_20_extract_song_title_with_curly_quotes():
    text = "Song: “Run Run Run”"
    assert takes_comps.extract_song_title(text) == "Run Run Run"


def test_21_extract_song_title_returns_none_when_missing():
    text = "Song title without quotation marks"
    assert takes_comps.extract_song_title(text) is None


def test_22_extract_bpm_bpm_before_number():
    assert takes_comps.extract_bpm("BPM 87") == 87.0


def test_23_extract_bpm_with_colon():
    assert takes_comps.extract_bpm("BPM: 87") == 87.0


def test_24_extract_bpm_number_before_bpm():
    assert takes_comps.extract_bpm("87 bpm") == 87.0


def test_25_extract_bpm_approx_format():
    assert takes_comps.extract_bpm("Tempo approx 87bpm") == 87.0


def test_26_extract_bpm_decimal():
    assert takes_comps.extract_bpm("BPM: 87.5") == 87.5


def test_27_extract_bpm_returns_none_when_missing():
    assert takes_comps.extract_bpm("Tempo is unknown") is None


def test_28_extract_key_from_parentheses():
    text = "Song information (C Major, approx 87bpm)"
    assert takes_comps.extract_key(text) == "C Major"


def test_29_read_cue_sheet_extracts_document_fields(monkeypatch, tmp_path):
    path = tmp_path / "cue.pdf"
    path.write_bytes(b"fake pdf")

    page = FakePage(
        text='"Run Run Run"\n(C Major, approx 87bpm)',
        tables=[
            [
                ["Time", "Description", "Section"],
                ["0:00", "", "INTRO"],
                ["0:15", "", "VERSE 1"],
                ["0:30", "", "CHORUS"],
            ]
        ],
    )

    monkeypatch.setattr(
        takes_comps.pdfplumber,
        "open",
        lambda file_path: FakePDF([page]),
    )

    result = takes_comps.read_cue_sheet(str(path))

    assert result["document"]["song_title"] == "Run Run Run"
    assert result["document"]["key"] == "C Major"
    assert result["document"]["tempo_bpm"] == 87.0
    assert result["document"]["musical_structure"] == [
        "INTRO",
        "VERSE 1",
        "CHORUS",
    ]


def test_30_read_cue_sheet_ignores_invalid_time_rows(monkeypatch, tmp_path):
    path = tmp_path / "cue.pdf"
    path.write_bytes(b"fake pdf")

    page = FakePage(
        text='"Run Run Run"\n(C Major, BPM 87)',
        tables=[
            [
                ["Time", "Description", "Section"],
                ["not-time", "", "VERSE"],
                ["0:15", "", "CHORUS"],
            ]
        ],
    )

    monkeypatch.setattr(
        takes_comps.pdfplumber,
        "open",
        lambda file_path: FakePDF([page]),
    )

    result = takes_comps.read_cue_sheet(str(path))

    assert result["document"]["musical_structure"] == ["CHORUS"]


# ===========================================================================
# COMP SHEET TESTS: 31-39
# ===========================================================================

def test_31_comp_section_detects_verse():
    assert takes_comps.is_comp_section("VERSE 1") is True


def test_32_comp_section_detects_pre_chorus():
    assert takes_comps.is_comp_section("PRE-CHORUS") is True


def test_33_comp_section_detects_bridge():
    assert takes_comps.is_comp_section("BRIDGE") is True


def test_34_comp_section_rejects_normal_lyric():
    assert takes_comps.is_comp_section(
        "I am running through the night"
    ) is False


def test_35_comp_section_is_case_insensitive():
    assert takes_comps.is_comp_section("chorus") is True


def test_36_read_comp_sheet_extracts_title(monkeypatch, tmp_path):
    path = tmp_path / "comp.pdf"
    path.write_bytes(b"fake pdf")

    page = FakePage(
        text='"Run Run Run"',
        tables=[
            [
                ["VERSE 1", "", ""],
                ["First lyric", "Take A", ""],
            ]
        ],
    )

    monkeypatch.setattr(
        takes_comps.pdfplumber,
        "open",
        lambda file_path: FakePDF([page]),
    )

    result = takes_comps.read_comp_sheet(str(path))

    assert result["document"]["song_title"] == "Run Run Run"


def test_37_read_comp_sheet_formats_section_as_lyrics(monkeypatch, tmp_path):
    path = tmp_path / "comp.pdf"
    path.write_bytes(b"fake pdf")

    page = FakePage(
        text='"Run Run Run"',
        tables=[
            [
                ["VERSE 1", "", ""],
                ["First lyric", "", ""],
            ]
        ],
    )

    monkeypatch.setattr(
        takes_comps.pdfplumber,
        "open",
        lambda file_path: FakePDF([page]),
    )

    result = takes_comps.read_comp_sheet(str(path))

    assert "[VERSE 1]" in result["document"]["lyrics"]
    assert "First lyric" in result["document"]["lyrics"]


def test_38_read_comp_sheet_collects_take_selections(monkeypatch, tmp_path):
    path = tmp_path / "comp.pdf"
    path.write_bytes(b"fake pdf")

    page = FakePage(
        text='"Run Run Run"',
        tables=[
            [
                ["Line one", "Good", "Best"],
            ]
        ],
    )

    monkeypatch.setattr(
        takes_comps.pdfplumber,
        "open",
        lambda file_path: FakePDF([page]),
    )

    result = takes_comps.read_comp_sheet(str(path))

    assert "Take 1: Line one -> Good" in result["document"]["take_selections"]
    assert "Take 2: Line one -> Best" in result["document"]["take_selections"]


def test_39_read_comp_sheet_ignores_blank_first_cell(monkeypatch, tmp_path):
    path = tmp_path / "comp.pdf"
    path.write_bytes(b"fake pdf")

    page = FakePage(
        text='"Run Run Run"',
        tables=[
            [
                ["", "Take A", "Take B"],
                ["Real lyric", "", ""],
            ]
        ],
    )

    monkeypatch.setattr(
        takes_comps.pdfplumber,
        "open",
        lambda file_path: FakePDF([page]),
    )

    result = takes_comps.read_comp_sheet(str(path))

    assert result["document"]["lyrics"] == "Real lyric"


# ===========================================================================
# BUNDLE / HELPER TESTS: 40-47
# ===========================================================================

def test_40_nonempty_string_accepts_string():
    assert _nonempty_string("hello", "field") == "hello"


def test_41_nonempty_string_rejects_empty():
    with pytest.raises(BundleBuildError):
        _nonempty_string("", "field")


def test_42_compact_removes_none_but_keeps_zero_and_false():
    result = _compact({
        "a": 1,
        "b": None,
        "c": False,
        "d": 0,
    })

    assert result == {
        "a": 1,
        "c": False,
        "d": 0,
    }


def test_43_sha256_matches_hashlib(tmp_path):
    path = tmp_path / "hash.txt"
    path.write_bytes(b"COMP3888")

    expected = hashlib.sha256(b"COMP3888").hexdigest()

    assert _sha256(path) == expected


def test_44_read_json_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"name":"one","name":"two"}',
        encoding="utf-8",
    )

    with pytest.raises(
        BundleBuildError,
        match="duplicate JSON key",
    ):
        _read_json(path)


def test_45_manifest_rejects_wrong_schema_version():
    manifest = {
        "schema_version": "wrong",
        "hash_method": HASH_METHOD,
        "files": [{"example": True}],
        "relationships": [],
    }

    with pytest.raises(
        BundleBuildError,
        match="schema_version",
    ):
        _validate_manifest(manifest)


def test_46_minimal_bundle_builds(tmp_path):
    make_wav(tmp_path / "final.wav")

    bundle = build_bundle(
        minimal_manifest("final.wav"),
        base_dir=tmp_path,
    )

    assert len(bundle["artefacts"]) == 1
    assert (
        bundle["final_artefact_hash"]
        == bundle["artefacts"][0]["artefact_hash"]
    )


def test_47_declared_metadata_stays_separate(tmp_path):
    make_wav(
        tmp_path / "take.wav",
        sample_rate=44100,
    )

    manifest = minimal_manifest(
        "take.wav",
        attributes={
            "creation_method": "recorded",
            "technical": {
                "sample_rate_hz": 48000,
            },
        },
    )

    bundle = build_bundle(
        manifest,
        base_dir=tmp_path,
    )

    attrs = bundle["artefacts"][0]["attributes"]

    assert attrs["technical"]["sample_rate_hz"] == 44100
    assert attrs["declared_technical"]["sample_rate_hz"] == 48000


# ===========================================================================
# RELATIONSHIP TESTS: 48-50
# ===========================================================================

def test_48_valid_relationship_is_inserted():
    example = relationships.example_inputs()

    output = relationships.apply_relationships(
        example["bundle"],
        example["declarations"],
    )

    assert (
        output["artefacts"][1]["evidence"][0]["relationship_type"]
        == "excerpted_from"
    )


def test_49_relationship_reapplication_is_idempotent():
    example = relationships.example_inputs()

    output = relationships.apply_relationships(
        example["bundle"],
        example["declarations"],
    )

    repeated = relationships.apply_relationships(
        output,
        example["declarations"],
    )

    assert len(repeated["artefacts"][1]["evidence"]) == 1


def test_50_system_suggestion_is_rejected():
    example = relationships.example_inputs()
    declarations = copy.deepcopy(
        example["declarations"]
    )

    declarations["relationships"][0]["attributes"][
        "assertion_origin"
    ] = "system-suggestion"

    with pytest.raises(
        relationships.RelationshipExtractionError,
        match="system-suggestion",
    ):
        relationships.apply_relationships(
            example["bundle"],
            declarations,
        )
