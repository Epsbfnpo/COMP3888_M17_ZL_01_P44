#!/usr/bin/env python3
"""
wav_parser.py

Baseline reader for WAV/RIFF file metadata.

Scope (small baseline -- see parser/README.md):
- Parses the mandatory 'fmt ' chunk (core format: channel count, sample
  rate, bit depth, etc.) plus whichever optional chunks are present:
  LIST/INFO tags, Broadcast Wave Format 'bext', 'cue ' points, 'smpl'
  sampler/loop data, 'fact' sample count, and a best-effort ID3v2 header
  (non-standard in WAV but seen in the wild).
- Chunk parsing here follows the documented RIFF/WAVE, BWF ('bext'), and
  ID3v2 layouts -- these are established public formats, not guessed.
  Field names/offsets are implemented directly from those specs.
- Like the DDEX parsers in this package, this never crashes on a
  missing/malformed/truncated file or an absent optional chunk -- it
  degrades to None/[]/{} for that piece and adds a `warnings` entry, rather
  than raising or fabricating a value.
- Any chunk type not explicitly handled above is recorded by id/size only
  in `other_chunks`, never guessed at.
- Does NOT implement scoring, validation-rule evaluation, or normalization
  into the Evidence Bundle format -- that is separate, future work, same as
  the other parsers in this package.

Usage
-----
    python parser/wav_parser.py path/to/file.wav
    (or, from within parser/:  python wav_parser.py path/to/file.wav)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Standard LIST/INFO sub-chunk IDs mapped to friendly names
INFO_TAGS = {
    "INAM": "title",
    "IART": "artist",
    "ICMT": "comment",
    "ICRD": "creation_date",
    "IGNR": "genre",
    "IPRD": "product",
    "ISFT": "software",
    "ICOP": "copyright",
    "IENG": "engineer",
    "ISBJ": "subject",
    "ISRC": "source",
    "ITCH": "technician",
    "IKEY": "keywords",
    "IMED": "medium",
    "IARL": "archival_location",
}

WAV_FORMAT_NAMES = {
    0x0001: "PCM",
    0x0003: "IEEE Float",
    0x0006: "A-law",
    0x0007: "mu-law",
    0xFFFE: "Extensible",
}


@dataclass
class WavCoreFormat:
    audio_format_code: Optional[int] = None
    audio_format: Optional[str] = None
    num_channels: Optional[int] = None
    sample_rate_hz: Optional[int] = None
    byte_rate: Optional[int] = None
    block_align: Optional[int] = None
    bits_per_sample: Optional[int] = None
    data_size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None


@dataclass
class WavBroadcastExtension:
    description: Optional[str] = None
    originator: Optional[str] = None
    originator_reference: Optional[str] = None
    origination_date: Optional[str] = None
    origination_time: Optional[str] = None
    time_reference: Optional[int] = None
    version: Optional[int] = None
    umid: Optional[str] = None
    coding_history: Optional[str] = None


@dataclass
class WavCuePoint:
    cue_id: Optional[int] = None
    position: Optional[int] = None
    data_chunk_id: Optional[str] = None
    sample_offset: Optional[int] = None


@dataclass
class WavSamplerLoop:
    cue_id: Optional[int] = None
    loop_type: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None
    play_count: Optional[int] = None


@dataclass
class WavSampler:
    manufacturer: Optional[int] = None
    product: Optional[int] = None
    sample_period_ns: Optional[int] = None
    midi_unity_note: Optional[int] = None
    midi_pitch_fraction: Optional[int] = None
    smpte_format: Optional[int] = None
    smpte_offset: Optional[int] = None
    num_sample_loops: Optional[int] = None
    loops: List[WavSamplerLoop] = field(default_factory=list)


@dataclass
class WavFact:
    sample_length: Optional[int] = None


@dataclass
class WavId3Header:
    id3_version: Optional[str] = None
    flags: Optional[int] = None
    declared_size: Optional[int] = None
    note: str = "Raw ID3 frames not decoded; only header parsed."


@dataclass
class WavOtherChunk:
    chunk_id: Optional[str] = None
    size: Optional[int] = None
    note: Optional[str] = None


@dataclass
class WavParseResult:
    source_path: str
    core_format: Optional[WavCoreFormat] = None
    info: Dict[str, str] = field(default_factory=dict)
    broadcast_extension: Optional[WavBroadcastExtension] = None
    cue_points: List[WavCuePoint] = field(default_factory=list)
    sampler: Optional[WavSampler] = None
    fact: Optional[WavFact] = None
    id3: Optional[WavId3Header] = None
    other_chunks: List[WavOtherChunk] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _iter_chunks(data: bytes):
    """Yield (chunk_id, chunk_data) for each top-level RIFF chunk. Assumes caller already validated the RIFF/WAVE header."""
    pos = 12  # after 'RIFF' size 'WAVE'
    size = len(data)
    while pos + 8 <= size:
        chunk_id = data[pos:pos + 4].decode("ascii", errors="replace")
        chunk_size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        chunk_start = pos + 8
        chunk_end = chunk_start + chunk_size
        chunk_data = data[chunk_start:min(chunk_end, size)]
        yield chunk_id, chunk_data
        # Chunks are word-aligned; skip a pad byte if size is odd
        pos = chunk_end + (chunk_size % 2)


def _parse_fmt(chunk_data: bytes) -> Optional[WavCoreFormat]:
    if len(chunk_data) < 16:
        return None
    (audio_format, num_channels, sample_rate, byte_rate,
     block_align, bits_per_sample) = struct.unpack("<HHIIHH", chunk_data[:16])
    return WavCoreFormat(
        audio_format_code=audio_format,
        audio_format=WAV_FORMAT_NAMES.get(audio_format, f"Unknown (0x{audio_format:04X})"),
        num_channels=num_channels,
        sample_rate_hz=sample_rate,
        byte_rate=byte_rate,
        block_align=block_align,
        bits_per_sample=bits_per_sample,
    )


def _parse_list_info(chunk_data: bytes) -> Optional[Dict[str, str]]:
    if chunk_data[:4] != b"INFO":
        return None  # Not an INFO-type LIST chunk (could be 'adtl', etc.)

    info: Dict[str, str] = {}
    pos = 4
    size = len(chunk_data)
    while pos + 8 <= size:
        sub_id = chunk_data[pos:pos + 4].decode("ascii", errors="replace")
        sub_size = struct.unpack("<I", chunk_data[pos + 4:pos + 8])[0]
        sub_start = pos + 8
        sub_end = sub_start + sub_size
        raw = chunk_data[sub_start:min(sub_end, size)]
        text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        key = INFO_TAGS.get(sub_id, sub_id)
        info[key] = text
        pos = sub_end + (sub_size % 2)
    return info


def _parse_bext(chunk_data: bytes) -> WavBroadcastExtension:
    if len(chunk_data) < 602:
        # Still try to pull what we can from a short/truncated chunk.
        chunk_data = chunk_data.ljust(602, b"\x00")

    def txt(b: bytes) -> str:
        return b.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()

    description = txt(chunk_data[0:256])
    originator = txt(chunk_data[256:288])
    originator_ref = txt(chunk_data[288:320])
    origination_date = txt(chunk_data[320:330])
    origination_time = txt(chunk_data[330:338])
    time_ref_low, time_ref_high = struct.unpack("<II", chunk_data[338:346])
    version = struct.unpack("<H", chunk_data[346:348])[0]
    umid = chunk_data[348:412].hex()
    coding_history = chunk_data[602:].split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()

    return WavBroadcastExtension(
        description=description or None,
        originator=originator or None,
        originator_reference=originator_ref or None,
        origination_date=origination_date or None,
        origination_time=origination_time or None,
        time_reference=(time_ref_high << 32) | time_ref_low,
        version=version,
        umid=umid if any(c != "0" for c in umid) else None,
        coding_history=coding_history or None,
    )


def _parse_cue(chunk_data: bytes) -> List[WavCuePoint]:
    if len(chunk_data) < 4:
        return []
    num_points = struct.unpack("<I", chunk_data[0:4])[0]
    points: List[WavCuePoint] = []
    pos = 4
    for _ in range(num_points):
        if pos + 24 > len(chunk_data):
            break
        (cue_id, position, data_chunk_id, _chunk_start,
         _block_start, sample_offset) = struct.unpack("<II4sIII", chunk_data[pos:pos + 24])
        points.append(WavCuePoint(
            cue_id=cue_id,
            position=position,
            data_chunk_id=data_chunk_id.decode("ascii", errors="replace"),
            sample_offset=sample_offset,
        ))
        pos += 24
    return points


def _parse_smpl(chunk_data: bytes) -> Optional[WavSampler]:
    if len(chunk_data) < 36:
        return None
    (manufacturer, product, sample_period, midi_unity_note, midi_pitch_fraction,
     smpte_format, smpte_offset, num_sample_loops, _sampler_data) = struct.unpack(
        "<IIIIIIIII", chunk_data[0:36]
    )
    loops: List[WavSamplerLoop] = []
    pos = 36
    for _ in range(num_sample_loops):
        if pos + 24 > len(chunk_data):
            break
        (cue_id, loop_type, start, end, _fraction, play_count) = struct.unpack(
            "<IIIIII", chunk_data[pos:pos + 24]
        )
        loops.append(WavSamplerLoop(
            cue_id=cue_id, loop_type=loop_type, start=start, end=end, play_count=play_count,
        ))
        pos += 24

    return WavSampler(
        manufacturer=manufacturer,
        product=product,
        sample_period_ns=sample_period,
        midi_unity_note=midi_unity_note,
        midi_pitch_fraction=midi_pitch_fraction,
        smpte_format=smpte_format,
        smpte_offset=smpte_offset,
        num_sample_loops=num_sample_loops,
        loops=loops,
    )


def _parse_fact(chunk_data: bytes) -> Optional[WavFact]:
    if len(chunk_data) < 4:
        return None
    sample_length = struct.unpack("<I", chunk_data[0:4])[0]
    return WavFact(sample_length=sample_length)


def _parse_id3(chunk_data: bytes) -> Optional[WavId3Header]:
    if len(chunk_data) < 10 or chunk_data[0:3] != b"ID3":
        return None
    version = f"2.{chunk_data[3]}.{chunk_data[4]}"
    flags = chunk_data[5]
    # ID3 size is a 4-byte synchsafe integer (7 bits per byte).
    size = 0
    for b in chunk_data[6:10]:
        size = (size << 7) | (b & 0x7F)
    return WavId3Header(id3_version=version, flags=flags, declared_size=size)


def parse_wav_file(path: str) -> WavParseResult:
    """Read whatever WAV core/optional metadata is present in `path`.

    Mirrors the other parsers in this package: missing/optional chunks are
    left None/[]/{} rather than invented, and a malformed/unreadable file is
    reported via `warnings` rather than raised to the caller.
    """
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return WavParseResult(source_path=path, warnings=[f"could not read file: {exc}"])

    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        return WavParseResult(source_path=path, warnings=["not a valid WAV/RIFF file"])

    result = WavParseResult(source_path=path)
    warnings: List[str] = []
    seen_fmt = False

    for chunk_id, chunk_data in _iter_chunks(data):
        cid = chunk_id.strip()

        if cid == "fmt":
            result.core_format = _parse_fmt(chunk_data)
            seen_fmt = True

        elif cid == "LIST":
            info = _parse_list_info(chunk_data)
            if info:
                result.info.update(info)
            else:
                result.other_chunks.append(WavOtherChunk(chunk_id="LIST", size=len(chunk_data), note="non-INFO LIST"))

        elif cid == "bext":
            result.broadcast_extension = _parse_bext(chunk_data)

        elif cid == "cue":
            cues = _parse_cue(chunk_data)
            if cues:
                result.cue_points = cues

        elif cid == "smpl":
            result.sampler = _parse_smpl(chunk_data)

        elif cid == "fact":
            result.fact = _parse_fact(chunk_data)

        elif cid.lower() == "id3":
            id3 = _parse_id3(chunk_data)
            if id3:
                result.id3 = id3

        elif cid == "data":
            if result.core_format is not None:
                result.core_format.data_size_bytes = len(chunk_data)
            else:
                warnings.append("'data' chunk found before a valid 'fmt ' chunk; data_size_bytes not recorded")

        else:
            result.other_chunks.append(WavOtherChunk(chunk_id=cid, size=len(chunk_data)))

    if not seen_fmt:
        warnings.append("no 'fmt ' chunk found (required by the WAV/RIFF spec)")
    elif result.core_format is None:
        warnings.append("'fmt ' chunk present but too short to parse")

    if result.core_format and result.core_format.byte_rate and result.core_format.data_size_bytes is not None:
        result.core_format.duration_seconds = round(
            result.core_format.data_size_bytes / result.core_format.byte_rate, 3
        )

    result.warnings = warnings
    return result


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Parse a WAV file into baseline core-format + optional-metadata data."
    )
    arg_parser.add_argument("path", help="Path to a .wav file")
    args = arg_parser.parse_args()

    result = parse_wav_file(args.path)
    print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
