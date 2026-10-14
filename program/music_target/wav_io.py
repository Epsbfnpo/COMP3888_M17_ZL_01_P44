"""Streaming RIFF/WAVE inspection and PCM decoding.

The evaluator uses this module as the single source of truth for supported
WAV formats.  Inspection reads only RIFF and chunk headers.  Sample data is
decoded on demand into C-contiguous ``float32`` NumPy blocks.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
PCM_SUBFORMAT_GUID = bytes.fromhex("0100000000001000800000aa00389b71")
FLOAT_SUBFORMAT_GUID = bytes.fromhex("0300000000001000800000aa00389b71")
UNFINALISED_SIZE = 0xFFFFFFFF
MAX_CHUNKS = 1024
MAX_METADATA_CHUNK_BYTES = 1 << 20
MAX_FMT_CHUNK_BYTES = 4096
# Upper bound on raw bytes decoded at once, whatever block length a caller asks
# for, so high channel counts and sample rates cannot allocate huge blocks.
MAX_DECODE_BLOCK_BYTES = 16 * 1024 * 1024

# Leading bytes of common audio containers that are not RIFF/WAVE.  The file
# content, not its extension, decides the format.
_OTHER_CONTAINERS = (
    (b"fLaC", "FLAC"),
    (b"OggS", "Ogg"),
    (b"ID3", "MP3"),
    (b"\xff\xfb", "MP3"),
    (b"\xff\xf3", "MP3"),
    (b"\xff\xf2", "MP3"),
)


class WavError(ValueError):
    """A malformed WAV with a stable machine-facing reason code."""

    reason_code = "invalid_wav"

    def __init__(self, message: str, reason_code: str | None = None):
        super().__init__(message)
        if reason_code is not None:
            self.reason_code = reason_code


class UnsupportedWav(WavError):
    """A structurally readable WAV whose encoding is outside target scope."""

    reason_code = "unsupported_wav_format"


class NonFiniteSamples(WavError):
    """IEEE-float PCM containing NaN or infinity."""

    reason_code = "non_finite_samples"


@dataclass(frozen=True)
class WavDescriptor:
    format_kind: str
    format_tag: str
    sample_rate: int
    channels: int
    container_bits: int
    valid_bits: int
    block_align: int
    frame_count: int
    duration_seconds: float
    data_offset: int
    data_size: int
    file_size: int
    channel_mask: int | None = None
    trailing_bytes: int = 0
    warnings: tuple[str, ...] = ()
    container_end: int = 0
    mtime_ns: int = 0
    inode: int = 0


@dataclass(frozen=True)
class WavChunk:
    chunk_id: bytes
    data_offset: int
    size: int


def _read_exact(handle, size: int, reason_code: str, message: str) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise WavError(message, reason_code)
    return data


def _detected_container(header: bytes) -> str | None:
    if header[:4] == b"FORM" and header[8:12] in {b"AIFF", b"AIFC"}:
        return "AIFF"
    for prefix, name in _OTHER_CONTAINERS:
        if header.startswith(prefix):
            return name
    return None


def _container_bounds(handle, file_size: int,
                      suffix: str = "") -> tuple[int, list[str], int]:
    header = handle.read(12)
    detected = _detected_container(header)
    if detected:
        extension = f"extension '{suffix}'" if suffix else "no extension"
        raise UnsupportedWav(
            f"File content is {detected} audio ({extension}); {detected} is "
            "not supported by this target.", "unsupported_container")
    if len(header) != 12:
        raise WavError("Truncated RIFF/WAVE header.", "truncated_riff_header")
    container_id = header[:4]
    if container_id in {b"RF64", b"BW64"}:
        raise UnsupportedWav(
            "RF64/BW64 WAV is not supported.", "unsupported_rf64")
    if container_id != b"RIFF" or header[8:12] != b"WAVE":
        raise WavError("Expected a RIFF/WAVE file.", "not_riff_wave")
    declared_size = struct.unpack_from("<I", header, 4)[0]
    warnings: list[str] = []
    if declared_size in {0, UNFINALISED_SIZE}:
        container_end = file_size
        warnings.append("riff_size_unfinalised")
    else:
        container_end = declared_size + 8
        if container_end > file_size:
            raise WavError(
                "RIFF container is truncated relative to its declared size.",
                "riff_truncated")
    trailing = max(0, file_size - container_end)
    if trailing:
        warnings.append(f"trailing_bytes:{trailing}")
    return container_end, warnings, trailing


def _parse_format(data: bytes) -> dict:
    if len(data) < 16:
        raise WavError("Truncated WAV fmt chunk.", "truncated_fmt")
    code, channels, rate, byte_rate, align, bits = struct.unpack_from(
        "<HHIIHH", data, 0)
    format_kind = None
    format_tag = None
    valid_bits = bits
    channel_mask = None
    if code == WAVE_FORMAT_PCM:
        format_kind, format_tag = "int", "WAVE_FORMAT_PCM"
    elif code == WAVE_FORMAT_IEEE_FLOAT:
        format_kind, format_tag = "float", "WAVE_FORMAT_IEEE_FLOAT"
    elif code == WAVE_FORMAT_EXTENSIBLE:
        if len(data) < 40:
            raise WavError(
                "Truncated WAVE_FORMAT_EXTENSIBLE fmt chunk.",
                "truncated_extensible_fmt")
        extension_size = struct.unpack_from("<H", data, 16)[0]
        if extension_size < 22 or len(data) < 18 + extension_size:
            raise WavError(
                "Invalid WAVE_FORMAT_EXTENSIBLE extension size.",
                "invalid_extensible_fmt")
        valid_bits, channel_mask = struct.unpack_from("<HI", data, 18)
        subformat = data[24:40]
        if subformat == PCM_SUBFORMAT_GUID:
            format_kind = "int"
        elif subformat == FLOAT_SUBFORMAT_GUID:
            format_kind = "float"
        else:
            raise UnsupportedWav(
                "WAVE_FORMAT_EXTENSIBLE SubFormat is not PCM or IEEE float.",
                "unsupported_wav_subformat")
        format_tag = "WAVE_FORMAT_EXTENSIBLE"
    else:
        raise UnsupportedWav(
            f"WAV format code 0x{code:04X} is not supported.",
            "unsupported_wav_format")

    if not (1 <= channels <= 32 and 1 <= rate <= 384000):
        raise UnsupportedWav(
            "Channel count or sample rate is outside the supported limits.",
            "unsupported_channel_or_rate")
    supported_bits = ({8, 16, 24, 32} if format_kind == "int" else {32, 64})
    if bits not in supported_bits:
        label = "integer PCM" if format_kind == "int" else "IEEE float"
        raise UnsupportedWav(
            f"Unsupported {label} container width: {bits} bits.",
            "unsupported_sample_width")
    if not (1 <= valid_bits <= bits):
        raise WavError(
            "Valid bits per sample must fit the sample container.",
            "invalid_valid_bits")
    if format_kind == "float" and valid_bits != bits:
        raise UnsupportedWav(
            "IEEE-float WAV must use all bits in its sample container.",
            "unsupported_float_valid_bits")
    if bits == 8 and format_kind == "int" and valid_bits != 8:
        raise UnsupportedWav(
            "8-bit PCM with fewer than eight valid bits is not supported.",
            "unsupported_sample_width")
    expected_align = channels * (bits // 8)
    if align != expected_align:
        raise WavError(
            "WAV block alignment does not match channels and sample width.",
            "bad_block_align")
    return {
        "code": code,
        "format_kind": format_kind,
        "format_tag": format_tag,
        "channels": channels,
        "rate": rate,
        "byte_rate": byte_rate,
        "align": align,
        "bits": bits,
        "valid_bits": valid_bits,
        "channel_mask": channel_mask,
    }


def inspect_wav(path: str | os.PathLike[str]) -> WavDescriptor:
    """Inspect RIFF structure and return a descriptor without reading PCM data."""
    path = Path(path)
    stat = path.stat()
    file_size = stat.st_size
    with path.open("rb") as handle:
        container_end, warnings, trailing = _container_bounds(
            handle, file_size, path.suffix)
        riff_unfinalised = "riff_size_unfinalised" in warnings
        position = 12
        fmt = None
        data_offset = None
        data_size = None
        seen: set[bytes] = set()
        chunk_count = 0
        while position < container_end:
            if container_end - position < 8:
                raise WavError("Truncated WAV chunk header.",
                               "truncated_chunk_header")
            handle.seek(position)
            header = _read_exact(handle, 8, "truncated_chunk_header",
                                 "Truncated WAV chunk header.")
            chunk_id = header[:4]
            declared_size = struct.unpack_from("<I", header, 4)[0]
            start = position + 8
            chunk_count += 1
            if chunk_count > MAX_CHUNKS:
                raise WavError("WAV contains too many chunks.", "too_many_chunks")
            if chunk_id in {b"fmt ", b"data"}:
                if chunk_id in seen:
                    raise WavError("Duplicate mandatory WAV chunk.",
                                   "duplicate_chunk")
                seen.add(chunk_id)
            # A zero data size means "not finalised" only when the RIFF size
            # is unfinalised too; otherwise the data chunk is genuinely empty
            # and must not swallow the chunks that follow it.
            if chunk_id == b"data" and (
                    declared_size == UNFINALISED_SIZE or
                    (declared_size == 0 and riff_unfinalised)):
                size = max(0, container_end - start)
                warnings.append("data_size_unfinalised")
                unfinalised_data = True
            else:
                size = declared_size
                unfinalised_data = False
            end = start + size
            padded_end = end + (size & 1)
            if end > container_end or padded_end > container_end + 1:
                reason = "data_truncated" if chunk_id == b"data" else "chunk_overflow"
                raise WavError("WAV chunk exceeds the RIFF container.", reason)
            if chunk_id == b"fmt ":
                if size > MAX_FMT_CHUNK_BYTES:
                    raise WavError(
                        "WAV fmt chunk exceeds the supported parsing limit.",
                        "fmt_chunk_too_large")
                handle.seek(start)
                fmt = _parse_format(_read_exact(
                    handle, size, "truncated_fmt", "Truncated WAV fmt chunk."))
            elif chunk_id == b"data":
                if fmt is None:
                    raise WavError("The data chunk must follow fmt.",
                                   "data_before_fmt")
                data_offset, data_size = start, size
            position = container_end if unfinalised_data else padded_end

    if fmt is None or data_offset is None or data_size is None:
        raise WavError("A fmt chunk and a data chunk are required.",
                       "missing_mandatory_chunk")
    remainder = data_size % fmt["align"]
    if remainder:
        data_size -= remainder
        warnings.append(f"partial_final_frame_ignored:{remainder}")
    if data_size <= 0:
        raise WavError("WAV contains no complete PCM frames.", "empty_audio_data")
    if fmt["byte_rate"] != fmt["rate"] * fmt["align"]:
        warnings.append("byte_rate_mismatch")
    frame_count = data_size // fmt["align"]
    return WavDescriptor(
        format_kind=fmt["format_kind"],
        format_tag=fmt["format_tag"],
        sample_rate=fmt["rate"],
        channels=fmt["channels"],
        container_bits=fmt["bits"],
        valid_bits=fmt["valid_bits"],
        block_align=fmt["align"],
        frame_count=frame_count,
        duration_seconds=frame_count / fmt["rate"],
        data_offset=data_offset,
        data_size=data_size,
        file_size=file_size,
        channel_mask=fmt["channel_mask"],
        trailing_bytes=trailing,
        warnings=tuple(warnings),
        container_end=container_end,
        mtime_ns=stat.st_mtime_ns,
        inode=stat.st_ino,
    )


def iter_chunks(path: str | os.PathLike[str],
                descriptor: WavDescriptor | None = None) -> Iterator[WavChunk]:
    """Yield chunk locations without loading payloads into memory."""
    path = Path(path)
    descriptor = descriptor or inspect_wav(path)
    with path.open("rb") as handle:
        position = 12
        count = 0
        while position < descriptor.container_end:
            if descriptor.container_end - position < 8:
                break
            handle.seek(position)
            header = _read_exact(handle, 8, "truncated_chunk_header",
                                 "Truncated WAV chunk header.")
            chunk_id = header[:4]
            size = struct.unpack_from("<I", header, 4)[0]
            start = position + 8
            count += 1
            if count > MAX_CHUNKS:
                raise WavError("WAV contains too many chunks.", "too_many_chunks")
            if chunk_id == b"data" and size in {0, UNFINALISED_SIZE}:
                size = descriptor.data_size
                yield WavChunk(chunk_id, start, size)
                break
            yield WavChunk(chunk_id, start, size)
            position = start + size + (size & 1)


def read_chunk(path: str | os.PathLike[str], chunk: WavChunk,
               limit: int = MAX_METADATA_CHUNK_BYTES) -> bytes:
    """Read one bounded metadata chunk; PCM data should use ``read_frames``."""
    if chunk.size > limit:
        raise WavError(
            f"Metadata chunk exceeds the {limit}-byte parsing limit.",
            "metadata_chunk_too_large")
    with Path(path).open("rb") as handle:
        handle.seek(chunk.data_offset)
        return _read_exact(handle, chunk.size, "truncated_metadata_chunk",
                           "Metadata chunk is truncated.")


def _decode_samples(raw: bytes, descriptor: WavDescriptor) -> np.ndarray:
    width = descriptor.container_bits // 8
    if len(raw) % descriptor.block_align:
        raise WavError("Truncated PCM frame data.", "truncated_pcm_frames")
    if descriptor.format_kind == "float":
        dtype = "<f4" if descriptor.container_bits == 32 else "<f8"
        source = np.frombuffer(raw, dtype=dtype)
        if not np.isfinite(source).all():
            raise NonFiniteSamples("PCM contains NaN or Infinity.")
        with np.errstate(over="ignore", invalid="ignore"):
            values = source.astype(np.float32)
        if not np.isfinite(values).all():
            raise NonFiniteSamples(
                "PCM cannot be represented as finite float32 samples.")
    elif width == 1:
        values = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        values = (values - np.float32(128.0)) / np.float32(128.0)
    else:
        if width == 2:
            integers = np.frombuffer(raw, dtype="<i2").astype(np.int32)
        elif width == 3:
            octets = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
            integers = (octets[:, 0].astype(np.int32) |
                        (octets[:, 1].astype(np.int32) << 8) |
                        (octets[:, 2].astype(np.int32) << 16))
            integers = (integers ^ 0x800000) - 0x800000
        elif width == 4:
            integers = np.frombuffer(raw, dtype="<i4").astype(np.int64)
        else:  # Protected by inspect_wav; retained as a defensive boundary.
            raise UnsupportedWav("Unsupported integer PCM width.",
                                 "unsupported_sample_width")
        shift = descriptor.container_bits - descriptor.valid_bits
        if shift:
            integers = integers >> shift
        values = integers.astype(np.float32)
        values /= np.float32(2 ** (descriptor.valid_bits - 1))
    return np.ascontiguousarray(
        values.reshape(-1, descriptor.channels), dtype=np.float32)


def read_frames(path: str | os.PathLike[str], descriptor: WavDescriptor,
                start_frame: int = 0, end_frame: int | None = None,
                block_frames: int | None = None) -> Iterator[tuple[int, np.ndarray]]:
    """Yield decoded ``(first_frame, block)`` pairs for an exact frame range."""
    path = Path(path)
    stat = path.stat()
    if (stat.st_size != descriptor.file_size or
            (descriptor.mtime_ns and stat.st_mtime_ns != descriptor.mtime_ns) or
            (descriptor.inode and stat.st_ino != descriptor.inode)):
        raise WavError("WAV file changed after inspection.",
                       "file_changed_after_inspection")
    start_frame = int(start_frame)
    end_frame = descriptor.frame_count if end_frame is None else int(end_frame)
    if start_frame < 0 or end_frame < start_frame or end_frame > descriptor.frame_count:
        raise WavError("PCM frame range is outside the submitted audio.",
                       "frame_range_out_of_bounds")
    if block_frames is None:
        block_frames = max(1, descriptor.sample_rate * 10)
    block_frames = int(block_frames)
    if block_frames < 1:
        raise ValueError("block_frames must be positive")
    block_frames = min(
        block_frames, max(1, MAX_DECODE_BLOCK_BYTES // descriptor.block_align))
    with path.open("rb") as handle:
        first = start_frame
        handle.seek(descriptor.data_offset + first * descriptor.block_align)
        while first < end_frame:
            count = min(block_frames, end_frame - first)
            byte_count = count * descriptor.block_align
            raw = _read_exact(handle, byte_count, "truncated_pcm_frames",
                              "PCM frame data is truncated.")
            yield first, _decode_samples(raw, descriptor)
            first += count
