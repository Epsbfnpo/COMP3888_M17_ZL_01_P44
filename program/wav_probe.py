"""Header-only audio probe matching what the evaluator accepts (no Tkinter, no NumPy).

The standard-library ``wave`` module rejects IEEE-float WAV files, so the builder
used to treat valid float32/float64 files as unreadable. For WAV this reads only the
RIFF header chunks: integer PCM (format 1), IEEE float (format 3) and
WAVE_FORMAT_EXTENSIBLE whose sub-format is PCM or float. FLAC and MP3 are read with
the evaluator's own parsers, so the fields filled in are exactly what it measures.
It never decodes samples and never changes a file.
"""

from __future__ import annotations

import struct
from pathlib import Path

from music_target.audio_formats import detect
from music_target.flac_io import FlacError, inspect_flac
from music_target.mp3_io import Mp3Error, inspect_mp3

PCM, FLOAT, EXTENSIBLE = 1, 3, 0xFFFE


def audio_technical(path) -> dict:
    """Objective fields of a supported WAV, FLAC or MP3; {} if unsupported or unreadable.

    A field the format cannot carry is left out rather than guessed: MP3 has no
    bit depth, and its duration is included only when the file states its exact
    length (Xing/Info header with encoder delay and padding).
    """
    try:
        detected = detect(path)
        if detected.container == "FLAC":
            flac = inspect_flac(path, detected)
            fields = {"sample_rate_hz": flac.sample_rate, "channels": flac.channels,
                      "bit_depth": flac.bits_per_sample}
            if flac.duration_seconds is not None:
                fields["duration_seconds"] = round(flac.duration_seconds, 6)
            return fields
        if detected.container == "MP3":
            mp3 = inspect_mp3(path, detected)
            fields = {"sample_rate_hz": mp3.sample_rate, "channels": mp3.channels}
            if mp3.duration_seconds is not None:
                fields["duration_seconds"] = round(mp3.duration_seconds, 6)
            return fields
    except (OSError, FlacError, Mp3Error):
        return {}
    return wav_technical(path)


_RECORDINGS = ("audio/raw-take", "audio/raw-track")


def _wav_chunk_ids(path) -> set:
    """RIFF chunk ids of a WAV, read from the headers only."""
    ids = set()
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as f:
        if f.read(12)[8:12] != b"WAVE":
            return ids
        position = 12
        while position + 8 <= size and len(ids) < 1024:
            f.seek(position)
            header = f.read(8)
            if len(header) < 8:
                break
            ids.add(header[:4])
            position += 8 + struct.unpack_from("<I", header, 4)[0]
            position += position & 1
    return ids


def audio_capabilities(path) -> dict:
    """What evidence a supported audio file can carry; {} if unsupported or unreadable.

    The same categories the evaluator reports in ``format_capabilities``.
    """
    try:
        detected = detect(path)
        if detected.container == "FLAC":
            flac = inspect_flac(path, detected)
            kept = {chunk_id for chunk_id, _ in flac.foreign_chunks}
            return {"format": "FLAC", "lossless": True, "exact_duration": flac.duration_seconds is not None,
                    "recorder_metadata": "present" if kept & {b"bext", b"iXML"} else "not_kept"}
        if detected.container == "MP3":
            mp3 = inspect_mp3(path, detected)
            return {"format": "MP3", "lossless": False, "exact_duration": mp3.duration_seconds is not None,
                    "recorder_metadata": "not_supported_by_format"}
        if not wav_technical(path):
            return {}
        present = _wav_chunk_ids(path) & {b"bext", b"iXML"}
        return {"format": "WAV", "lossless": True, "exact_duration": True,
                "recorder_metadata": "present" if present else "absent"}
    except (OSError, FlacError, Mp3Error):
        return {}


def format_advice(capabilities: dict, artefact_type: str, converted: bool = False) -> list:
    """Short advice for the builder, matching the evaluator's ``format_capabilities`` advice.

    ``converted`` is true when the file is declared derived_from an original with
    transformation=format_conversion, which already supplies the original.
    """
    advice = []
    recorder = capabilities.get("recorder_metadata")
    if artefact_type in _RECORDINGS and not converted:
        if recorder == "not_kept":
            advice.append("This FLAC recording carries no recorder metadata. Re-encode it from the original "
                          "WAV with flac --keep-foreign-metadata, or add the original WAV and declare this file "
                          "derived_from it as a format conversion.")
        elif recorder == "not_supported_by_format":
            advice.append("MP3 cannot carry recorder metadata. Add the original WAV or FLAC and declare this "
                          "file derived_from it as a format conversion.")
    if capabilities.get("format") == "MP3" and not capabilities.get("exact_duration"):
        advice.append("This MP3 has no Xing/LAME header, so its exact length cannot be checked.")
    if capabilities.get("format") in ("FLAC", "MP3"):
        advice.append(f"{capabilities['format']} is accepted with limited checks: the evaluator is designed for "
                      "WAV, and checks it implements for WAV only are reported as not assessed. Submit WAV for "
                      "a full assessment.")
    return advice


def wav_technical(path) -> dict:
    """Objective fields {duration_seconds, sample_rate_hz, channels, bit_depth}; {} if unsupported."""
    try:
        path = Path(path)
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return {}
            fmt = None
            position = 12
            while position + 8 <= size:
                f.seek(position)
                chunk = f.read(8)
                if len(chunk) < 8:
                    break
                chunk_id, chunk_size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                body = position + 8
                if chunk_id == b"fmt ":
                    raw = f.read(min(chunk_size, 40))
                    if len(raw) < 16:
                        return {}
                    tag, channels, rate, _byte_rate, block_align, bits = struct.unpack(
                        "<HHIIHH", raw[:16])
                    if tag == EXTENSIBLE:
                        if len(raw) < 26:
                            return {}
                        tag = struct.unpack("<H", raw[24:26])[0]  # sub-format GUID's first field
                    fmt = (tag, channels, rate, block_align, bits)
                elif chunk_id == b"data":
                    if fmt is None:
                        return {}
                    tag, channels, rate, block_align, bits = fmt
                    if (tag not in (PCM, FLOAT) or not rate or not channels or not block_align
                            or (tag == FLOAT and bits not in (32, 64))
                            or (tag == PCM and bits not in (8, 16, 24, 32))):
                        return {}
                    available = max(0, size - body)
                    # Unfinalised files (size 0 or 0xFFFFFFFF) run to the end of the file.
                    data = available if chunk_size in (0, 0xFFFFFFFF) else min(chunk_size, available)
                    frames = data // block_align
                    return {"duration_seconds": round(frames / rate, 6),
                            "sample_rate_hz": rate, "channels": channels, "bit_depth": bits}
                position = body + chunk_size + (chunk_size & 1)
    except (OSError, struct.error, ZeroDivisionError):
        return {}
    return {}
