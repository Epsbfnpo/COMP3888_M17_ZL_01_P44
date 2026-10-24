"""Bounded FLAC inspection: metadata blocks, kept WAV chunks and the audio MD5.

Metadata is parsed directly from the FLAC block layout (RFC 9639):
``STREAMINFO`` (rate, channels, bit depth, total samples, MD5 of the decoded
audio), ``VORBIS_COMMENT`` (encoder and tags) and ``APPLICATION`` blocks with
ID ``riff``, where ``flac --keep-foreign-metadata`` keeps the WAV chunks
(``bext``, ``iXML``, ``LIST``) of the source file. soundfile does not expose
the MD5 or ``APPLICATION`` blocks, so it is used only to decode audio for the
MD5 check (``verify_md5``); inspection needs the standard library only.
"""

from __future__ import annotations

import dataclasses
import hashlib
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from music_target.audio_formats import detect, read_id3_tag

MAX_METADATA_BLOCKS = 1024
MAX_METADATA_BLOCK_READ_BYTES = 1 << 20
MAX_VORBIS_COMMENTS = 1024
MAX_SAMPLE_RATE = 384000
MAX_CHANNELS = 8

BLOCK_NAMES = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE",
               4: "VORBIS_COMMENT", 5: "CUESHEET", 6: "PICTURE"}


class FlacError(ValueError):
    """A malformed FLAC with a stable machine-facing reason code."""

    reason_code = "invalid_flac"

    def __init__(self, message: str, reason_code: str | None = None):
        super().__init__(message)
        if reason_code is not None:
            self.reason_code = reason_code


class UnsupportedFlac(FlacError):
    """A readable FLAC outside the supported limits."""

    reason_code = "unsupported_flac"


@dataclass(frozen=True)
class FlacDescriptor:
    sample_rate: int
    channels: int
    bits_per_sample: int
    total_samples: int | None       # None when STREAMINFO records 0 (unknown)
    duration_seconds: float | None
    md5: str | None                 # None when the encoder did not store one
    min_block_size: int
    max_block_size: int
    audio_offset: int
    file_size: int
    metadata: dict                  # JSON-safe summary of every block
    foreign_chunks: tuple           # (chunk_id, payload bytes) kept from a source WAV
    id3_tags: tuple                 # summaries of ID3v2 tags before the fLaC marker
    c2pa_container_present: bool
    warnings: tuple
    mtime_ns: int = 0
    inode: int = 0
    format: str = "FLAC"


def _streaminfo(raw: bytes | None) -> dict:
    if raw is None or len(raw) != 34:
        raise FlacError("STREAMINFO must be 34 bytes.", "invalid_streaminfo")
    min_block, max_block = struct.unpack(">HH", raw[:4])
    min_frame, max_frame = int.from_bytes(raw[4:7], "big"), int.from_bytes(raw[7:10], "big")
    packed = int.from_bytes(raw[10:18], "big")
    sample_rate = packed >> 44
    channels = ((packed >> 41) & 0x7) + 1
    bits = ((packed >> 36) & 0x1F) + 1
    total = packed & 0xFFFFFFFFF
    md5 = raw[18:34]
    return {"min_block_size": min_block, "max_block_size": max_block, "min_frame_size": min_frame,
            "max_frame_size": max_frame, "sample_rate_hz": sample_rate, "channels": channels,
            "bits_per_sample": bits, "total_samples": total,
            "md5": md5.hex() if any(md5) else None}


def _vorbis_comment(raw: bytes, warnings: list) -> dict:
    """Vendor string and tags (keys upper-cased, values kept in order)."""
    def take(position, size):
        if position + size > len(raw):
            raise FlacError("VORBIS_COMMENT is truncated.", "invalid_vorbis_comment")
        return raw[position:position + size], position + size
    length_raw, position = take(0, 4)
    vendor, position = take(position, struct.unpack("<I", length_raw)[0])
    count_raw, position = take(position, 4)
    count = struct.unpack("<I", count_raw)[0]
    if count > MAX_VORBIS_COMMENTS:
        warnings.append(f"vorbis_comments_truncated:{count}")
        count = MAX_VORBIS_COMMENTS
    comments = {}
    for _ in range(count):
        length_raw, position = take(position, 4)
        entry, position = take(position, struct.unpack("<I", length_raw)[0])
        key, separator, value = entry.decode("utf-8", errors="replace").partition("=")
        if separator and key:
            comments.setdefault(key.upper(), []).append(value)
    return {"vendor": vendor.decode("utf-8", errors="replace"), "comments": comments}


def _riff_chunk(raw: bytes) -> tuple[bytes, bytes] | None:
    """One WAV chunk from an APPLICATION 'riff' block; None for the RIFF header itself."""
    if raw[:4] in (b"RIFF", b"RF64", b"BW64") and len(raw) == 12:
        return None
    if len(raw) < 8:
        raise FlacError("Foreign RIFF metadata block is truncated.", "invalid_foreign_metadata")
    size = struct.unpack("<I", raw[4:8])[0]
    if raw[:4] == b"data":
        return raw[:4], b""  # flac stores only the data chunk header
    if 8 + size > len(raw):
        raise FlacError("Foreign RIFF chunk exceeds its metadata block.", "invalid_foreign_metadata")
    return raw[:4], raw[8:8 + size]


def inspect_flac(path, detected=None) -> FlacDescriptor:
    """Read every metadata block (bounded) and return a descriptor; no audio is decoded."""
    path = Path(path)
    stat = path.stat()
    detected = detected or detect(path)
    if detected.container != "FLAC":
        raise FlacError("File content is not FLAC.", "not_flac")
    warnings = list(detected.warnings)
    id3_tags = [read_id3_tag(path, header) for header in detected.leading_id3]
    for tag in id3_tags:
        warnings.extend("id3v2: " + item for item in tag["warnings"])
    if id3_tags:
        warnings.append("id3v2_tag_before_flac_marker")
    file_size = stat.st_size
    blocks, streaminfo, vorbis, foreign, applications = [], None, None, [], []
    pictures = padding = seek_points = 0
    cuesheet = False
    with path.open("rb") as handle:
        position = detected.audio_offset + 4
        while True:
            if len(blocks) >= MAX_METADATA_BLOCKS:
                raise FlacError("FLAC contains too many metadata blocks.", "too_many_metadata_blocks")
            handle.seek(position)
            header = handle.read(4)
            if len(header) < 4:
                raise FlacError("FLAC metadata is truncated.", "truncated_metadata")
            last, kind = bool(header[0] & 0x80), header[0] & 0x7F
            length = int.from_bytes(header[1:4], "big")
            start, end = position + 4, position + 4 + length
            if end > file_size:
                raise FlacError("A FLAC metadata block exceeds the file.", "truncated_metadata")
            if kind == 127:
                raise FlacError("FLAC metadata block type 127 is invalid.", "invalid_metadata_block")
            if not blocks and kind != 0:
                raise FlacError("The first FLAC metadata block must be STREAMINFO.", "missing_streaminfo")
            if kind == 0 and blocks:
                raise FlacError("FLAC contains more than one STREAMINFO block.", "duplicate_streaminfo")
            blocks.append({"type": BLOCK_NAMES.get(kind, f"reserved_{kind}"), "length": length})

            def payload(limit=MAX_METADATA_BLOCK_READ_BYTES):
                if length > limit:
                    return None
                handle.seek(start)
                return handle.read(length)

            if kind == 0:
                streaminfo = _streaminfo(payload())
            elif kind == 1:
                padding += length
            elif kind == 2:
                handle.seek(start)
                app_id = handle.read(4)
                applications.append({"id": app_id.decode("latin-1", errors="replace"), "length": length})
                if app_id == b"riff":
                    raw = payload(MAX_METADATA_BLOCK_READ_BYTES + 4)
                    if raw is None:
                        warnings.append(f"foreign_riff_block_too_large:{length}")
                    else:
                        chunk = _riff_chunk(raw[4:])
                        if chunk is not None:
                            foreign.append(chunk)
                elif app_id == b"aiff":
                    warnings.append("foreign_aiff_metadata_not_read")
            elif kind == 3:
                seek_points += length // 18
            elif kind == 4:
                if vorbis is not None:
                    warnings.append("duplicate_vorbis_comment_ignored")
                else:
                    raw = payload()
                    if raw is None:
                        warnings.append(f"vorbis_comment_too_large:{length}")
                    else:
                        vorbis = _vorbis_comment(raw, warnings)
            elif kind == 5:
                cuesheet = True
            elif kind == 6:
                pictures += 1
            position = end
            if last:
                break
        handle.seek(position)
        sync = handle.read(2)
    info = streaminfo
    if not 1 <= info["sample_rate_hz"] <= MAX_SAMPLE_RATE or info["channels"] > MAX_CHANNELS:
        raise UnsupportedFlac("FLAC sample rate or channel count is outside the supported limits.",
                              "unsupported_channel_or_rate")
    if info["bits_per_sample"] < 4:
        raise FlacError("FLAC bits per sample must be at least 4.", "invalid_streaminfo")
    total = info["total_samples"] or None
    if total is None:
        warnings.append("total_samples_unknown")
    if len(sync) < 2 or sync[0] != 0xFF or (sync[1] & 0xFE) != 0xF8:
        if total:
            raise FlacError("No FLAC audio frame follows the metadata.", "missing_frame_sync")
        warnings.append("no_audio_frames")
    if info["md5"] is None:
        warnings.append("md5_not_stored")
    c2pa = any(tag["c2pa_manifest_present"] for tag in id3_tags)
    metadata = {
        "streaminfo": info,
        "blocks": blocks,
        "vorbis_comment": vorbis,
        "applications": applications,
        "foreign_metadata_chunks": [{"chunk_id": chunk_id.decode("latin-1", errors="replace").strip(),
                                     "size": len(payload_bytes)} for chunk_id, payload_bytes in foreign],
        "pictures": pictures,
        "padding_bytes": padding,
        "seek_points": seek_points,
        "cuesheet_present": cuesheet,
    }
    return FlacDescriptor(
        sample_rate=info["sample_rate_hz"], channels=info["channels"],
        bits_per_sample=info["bits_per_sample"], total_samples=total,
        duration_seconds=(total / info["sample_rate_hz"]) if total else None,
        md5=info["md5"], min_block_size=info["min_block_size"], max_block_size=info["max_block_size"],
        audio_offset=position, file_size=file_size, metadata=metadata, foreign_chunks=tuple(foreign),
        id3_tags=tuple(id3_tags), c2pa_container_present=c2pa, warnings=tuple(warnings),
        mtime_ns=stat.st_mtime_ns, inode=stat.st_ino)


def verify_md5(path, descriptor: FlacDescriptor, deadline=None, block_frames=65536) -> dict:
    """Decode the audio and compare the MD5 of its samples with STREAMINFO.

    FLAC defines the MD5 over the decoded samples as signed little-endian
    integers of ceil(bits/8) bytes, channels interleaved. soundfile returns
    them left-justified in int32, so they are shifted back to their width.
    ``deadline`` is a ``time.time()`` value; reaching it makes the check
    unavailable rather than partial.
    """
    record = {"stored_md5": descriptor.md5, "decoded_md5": None, "decoded_frames": None,
              "declared_total_samples": descriptor.total_samples}
    if descriptor.md5 is None:
        return {**record, "status": "unavailable", "reason_code": "md5_not_stored",
                "reason": "The encoder did not store an MD5 of the audio in STREAMINFO."}
    try:
        import numpy as np
        import soundfile
    except ImportError:
        return {**record, "status": "unavailable", "reason_code": "decoder_unavailable",
                "reason": "soundfile is not installed, so the audio could not be decoded."}
    bits = descriptor.bits_per_sample
    width = (bits + 7) // 8
    # The same canonical bytes give the sample SHA-256 used to compare files
    # (music_target/sample_identity.py), at no extra decoding cost.
    digest, sample_sha256, frames = hashlib.md5(), hashlib.sha256(), 0
    path = Path(path)
    stat = path.stat()
    if stat.st_size != descriptor.file_size or stat.st_mtime_ns != descriptor.mtime_ns:
        return {**record, "status": "unavailable", "reason_code": "file_changed_after_inspection",
                "reason": "The file changed after it was inspected."}
    try:
        audio = soundfile.SoundFile(str(path))
    except (RuntimeError, soundfile.LibsndfileError) as exc:
        # Not evidence against the file: the decoder may not support this
        # encoding (for example a bit depth libsndfile cannot read).
        return {**record, "status": "unavailable", "reason_code": "decoder_cannot_open",
                "reason": f"The decoder could not open this FLAC: {exc}"}
    try:
        with audio:
            if audio.channels != descriptor.channels or audio.samplerate != descriptor.sample_rate:
                return {**record, "status": "failed", "reason_code": "decoder_disagrees_with_streaminfo",
                        "reason": "The decoder reports a different channel count or sample rate than STREAMINFO."}
            for block in audio.blocks(blocksize=block_frames, dtype="int32", always_2d=True):
                if deadline is not None and time.time() >= deadline:
                    return {**record, "status": "unavailable", "reason_code": "caller_deadline",
                            "reason": "The caller's time limit was reached before the audio was fully decoded."}
                values = (block >> (32 - bits)).astype("<i4").reshape(-1)
                canonical = values.view(np.uint8).reshape(-1, 4)[:, :width].tobytes()
                digest.update(canonical)
                sample_sha256.update(canonical)
                frames += len(block)
    except (RuntimeError, soundfile.LibsndfileError) as exc:
        return {**record, "status": "failed", "reason_code": "audio_not_decodable",
                "reason": f"The audio frames could not be decoded: {exc}"}
    decoded = digest.hexdigest()
    record.update(decoded_md5=decoded, decoded_frames=frames, sample_sha256=sample_sha256.hexdigest())
    if decoded == descriptor.md5:
        return {**record, "status": "matched"}
    return {**record, "status": "mismatched", "reason_code": "md5_mismatch",
            "reason": "The decoded audio does not match the MD5 stored when the file was encoded."}


def flac_sample_digest(path, descriptor: FlacDescriptor, deadline=None) -> dict:
    """SHA-256 of the canonical decoded samples, also when STREAMINFO stores no MD5."""
    if descriptor.md5 is not None:
        result = verify_md5(path, descriptor, deadline)
    else:
        # verify_md5 needs a stored MD5 to compare with; decode with a placeholder.
        result = verify_md5(path, dataclasses.replace(descriptor, md5="0" * 32), deadline)
    if result.get("sample_sha256") is None:
        return {"status": "unavailable", "reason_code": result.get("reason_code", "not_decoded"),
                "reason": result.get("reason")}
    return {"status": "computed", "sha256": result["sample_sha256"], "frames": result["decoded_frames"],
            "bits": descriptor.bits_per_sample}
