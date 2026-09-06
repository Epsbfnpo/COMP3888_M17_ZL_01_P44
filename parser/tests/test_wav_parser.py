"""
Synthetic parser-behaviour tests for wav_parser.py.

Fixtures under tests/fixtures/*.wav are hand-generated (via the stdlib
`wave` module plus manual chunk splicing for the LIST/INFO case) purely to
exercise parser behaviour -- they are not a substitute for testing against
real-world WAV files produced by DAWs/recorders, which may use additional
or differently-shaped optional chunks.
"""

import pathlib

from wav_parser import parse_wav_file

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_parses_minimal_core_format_no_optional_chunks():
    result = parse_wav_file(str(FIXTURES / "wav_minimal.wav"))

    assert result.core_format is not None
    assert result.core_format.audio_format == "PCM"
    assert result.core_format.num_channels == 1
    assert result.core_format.sample_rate_hz == 8000
    assert result.core_format.bits_per_sample == 16
    assert result.core_format.data_size_bytes == 1000
    assert result.core_format.duration_seconds == 0.062

    # No optional chunks present -> stay empty/None, not fabricated.
    assert result.info == {}
    assert result.broadcast_extension is None
    assert result.cue_points == []
    assert result.sampler is None
    assert result.fact is None
    assert result.id3 is None
    assert result.other_chunks == []
    assert result.warnings == []


def test_parses_list_info_chunk():
    result = parse_wav_file(str(FIXTURES / "wav_with_info.wav"))

    assert result.core_format is not None
    assert result.core_format.num_channels == 2
    assert result.core_format.sample_rate_hz == 44100

    assert result.info == {
        "title": "Test Recording",
        "artist": "Jane Doe",
        "software": "wave module",
    }
    assert result.warnings == []


def test_missing_fmt_chunk_reported_not_fabricated():
    result = parse_wav_file(str(FIXTURES / "wav_missing_fmt.wav"))

    assert result.core_format is None
    assert any("no 'fmt '" in w for w in result.warnings)

    # The unrecognized chunk in this fixture is still recorded by id/size,
    # never decoded or guessed at.
    assert len(result.other_chunks) == 1
    assert result.other_chunks[0].chunk_id == "JUNK"
    assert result.other_chunks[0].size == 4


def test_non_riff_file_reports_warning_without_crashing():
    result = parse_wav_file(str(FIXTURES / "wav_malformed.wav"))

    assert result.core_format is None
    assert result.warnings == ["not a valid WAV/RIFF file"]


def test_missing_file_reports_warning_without_crashing(tmp_path):
    missing = tmp_path / "does_not_exist.wav"

    result = parse_wav_file(str(missing))

    assert result.core_format is None
    assert result.warnings
    assert "could not read file" in result.warnings[0]
