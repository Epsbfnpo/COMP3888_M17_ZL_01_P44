"""Bounded MP3 (MPEG Layer III) inspection without decoding.

Every frame header is walked, so the frame count, stream parameters and any
loss of sync are measured rather than assumed. The exact number of audio
samples is known only when a Xing/Info header gives the frame count and a
LAME-style extension gives the encoder delay and padding; it is then
``frames * samples_per_frame - delay - padding``, the length a gapless decoder
(mpg123, used by libsndfile) produces. Without those headers the duration is
only an estimate, and it is reported as one: MP3 encoders add up to about two
frames of delay and padding that cannot be measured from the stream.

MP3 is lossy: it has no bit depth, and no standard place for recorder
metadata. Those checks are reported as not applicable, never as failed.
Only the standard library is used, so the desktop builder shares this code.
"""

from __future__ import annotations

import mmap
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from music_target.audio_formats import (MAX_MPEG_SYNC_SEARCH_BYTES, MPEG_CHANNEL_MODES, MPEG_VERSION_NAMES,
                                        detect, mpeg_header, read_id3_tag, trailing_tags)

MAX_SYNC_LOSSES = 64
DEADLINE_CHECK_FRAMES = 100_000


class Mp3Error(ValueError):
    """A malformed MP3 with a stable machine-facing reason code."""

    reason_code = "invalid_mp3"

    def __init__(self, message: str, reason_code: str | None = None):
        super().__init__(message)
        if reason_code is not None:
            self.reason_code = reason_code


class UnsupportedMp3(Mp3Error):
    reason_code = "unsupported_mp3"


@dataclass(frozen=True)
class Mp3Descriptor:
    mpeg_version: str
    layer: int
    sample_rate: int
    channels: int
    channel_mode: str
    samples_per_frame: int
    audio_frames: int               # MPEG frames carrying audio (an Info frame excluded)
    info_tag: str | None            # "Xing", "Info", "VBRI" or None
    info_tag_frames: int | None
    encoder: str | None
    encoder_delay: int | None
    encoder_padding: int | None
    lame_tag_crc_valid: bool | None
    bitrate_mode: str               # "CBR" or "VBR"
    nominal_bitrate_kbps: int
    average_bitrate_kbps: float | None
    total_samples: int | None       # exact gapless length, or None
    duration_seconds: float | None  # exact, or None
    estimated_duration_seconds: float
    audio_offset: int
    audio_end: int
    file_size: int
    id3_tags: tuple
    trailing_tags: dict
    c2pa_container_present: bool
    warnings: tuple
    mtime_ns: int = 0
    inode: int = 0
    format: str = "MP3"


def _crc16(data: bytes) -> int:
    """CRC-16/ARC, as LAME uses for its tag."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _info_tag(frame: bytes, header) -> dict:
    """Xing/Info (with an optional LAME extension) or VBRI header in the first frame."""
    offset = 4 + (2 if header.protected else 0) + header.side_info_bytes
    tag = frame[offset:offset + 4]
    if tag in (b"Xing", b"Info") and len(frame) >= offset + 8:
        flags = struct.unpack(">I", frame[offset + 4:offset + 8])[0]
        position, frames = offset + 8, None
        if flags & 1:
            frames = struct.unpack(">I", frame[position:position + 4])[0]
            position += 4
        position += (4 if flags & 2 else 0) + (100 if flags & 4 else 0) + (4 if flags & 8 else 0)
        result = {"tag": tag.decode(), "frames": frames}
        encoder = frame[position:position + 9]
        if (len(frame) >= position + 36 and encoder[:1].isalpha()
                and all(32 <= byte < 127 or byte == 0 for byte in encoder)):
            packed = frame[position + 21:position + 24]
            result.update(
                encoder=encoder.rstrip(b"\x00 ").decode("ascii"),
                delay=(packed[0] << 4) | (packed[1] >> 4),
                padding=((packed[1] & 0x0F) << 8) | packed[2],
                crc_valid=struct.unpack(">H", frame[position + 34:position + 36])[0]
                == _crc16(frame[:position + 34]))
        return result
    vbri = 4 + 32
    if frame[vbri:vbri + 4] == b"VBRI" and len(frame) >= vbri + 18:
        return {"tag": "VBRI", "frames": struct.unpack(">I", frame[vbri + 14:vbri + 18])[0],
                "vbri_delay": struct.unpack(">H", frame[vbri + 6:vbri + 8])[0]}
    return {}


def inspect_mp3(path, detected=None, deadline=None) -> Mp3Descriptor:
    """Walk every frame header and return a descriptor; no audio is decoded."""
    path = Path(path)
    stat = path.stat()
    detected = detected or detect(path)
    if detected.container == "MPEG Layer I/II":
        raise UnsupportedMp3("MPEG Layer I/II audio is not supported; only Layer III (MP3) is.",
                             "unsupported_mpeg_layer")
    if detected.container != "MP3":
        raise Mp3Error("File content is not MP3.", "not_mp3")
    warnings = list(detected.warnings)
    id3_tags = [read_id3_tag(path, header) for header in detected.leading_id3]
    file_size = stat.st_size
    audio_end, trailing = trailing_tags(path, detected.audio_offset, file_size)
    appended = trailing.pop("appended_id3v2", None)
    if appended is not None:
        id3_tags.append(read_id3_tag(path, appended))
        trailing["appended_id3v2"] = {"offset": appended.offset, "size": appended.total_size}
    for tag in id3_tags:
        warnings.extend("id3v2: " + item for item in tag["warnings"])
    if file_size == 0:
        raise Mp3Error("The file is empty.", "empty_audio_data")
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        position = detected.audio_offset
        first = mpeg_header(data, position)
        if first is None or first.layer != 3:
            raise Mp3Error("No MPEG Layer III frame was found.", "no_mpeg_frames")
        if not first.frame_bytes:
            raise UnsupportedMp3("Free-format MP3 bitrates are not supported.", "unsupported_free_format")
        info = _info_tag(bytes(data[position:position + first.frame_bytes]), first)
        frames, sync_losses, bitrates, parameters_changed, truncated = 0, 0, set(), False, False
        # The Xing/Info frame carries no audio and often has its own bitrate.
        skip_first = info.get("tag") in ("Xing", "Info")
        payload_bytes, nominal = 0, None
        while position + 4 <= audio_end:
            header = mpeg_header(data, position)
            if header is None or header.layer != 3 or not header.frame_bytes:
                sync_losses += 1
                if sync_losses > MAX_SYNC_LOSSES:
                    warnings.append("too_many_sync_losses")
                    break
                found = None
                window_end = min(audio_end - 3, position + MAX_MPEG_SYNC_SEARCH_BYTES)
                for candidate in range(position + 1, max(position + 1, window_end)):
                    if data[candidate] == 0xFF:
                        probe = mpeg_header(data, candidate)
                        if (probe is not None and probe.layer == 3 and probe.frame_bytes
                                and probe.sample_rate == first.sample_rate):
                            found = candidate
                            break
                if found is None:
                    warnings.append(f"trailing_bytes:{audio_end - position}")
                    break
                warnings.append(f"sync_lost:{position}:{found - position}")
                position = found
                continue
            if (header.version, header.sample_rate, header.channels) != (
                    first.version, first.sample_rate, first.channels):
                parameters_changed = True
            if position + header.frame_bytes > audio_end:
                truncated = True
                warnings.append(f"truncated_final_frame:{audio_end - position}")
                break
            frames += 1
            if not (skip_first and frames == 1):
                payload_bytes += header.frame_bytes
                bitrates.add(header.bitrate_kbps)
                nominal = header.bitrate_kbps if nominal is None else nominal
            position += header.frame_bytes
            if deadline is not None and frames % DEADLINE_CHECK_FRAMES == 0 and time.time() >= deadline:
                raise Mp3Error("The caller's time limit was reached while reading MP3 frames.", "caller_deadline")
    tag = info.get("tag")
    audio_frames = frames - (1 if tag in ("Xing", "Info") else 0)
    if audio_frames <= 0:
        raise Mp3Error("The MP3 contains no audio frames.", "empty_audio_data")
    if parameters_changed:
        warnings.append("stream_parameters_change")
    spf = first.samples_per_frame
    total = None
    clean = not (sync_losses or parameters_changed or truncated)
    if tag in ("Xing", "Info") and info.get("frames") is not None and info["frames"] != audio_frames:
        warnings.append(f"info_tag_frame_count_mismatch:{info['frames']}:{audio_frames}")
    elif clean and info.get("delay") is not None and info.get("frames") is not None:
        candidate = info["frames"] * spf - info["delay"] - info["padding"]
        if candidate > 0:
            total = candidate
    if total is None:
        warnings.append("duration_estimated_without_gapless_information")
    if info.get("crc_valid") is False:
        warnings.append("lame_tag_crc_mismatch")
    estimated = audio_frames * spf / first.sample_rate
    duration = total / first.sample_rate if total else None
    mode = "VBR" if tag == "Xing" or tag == "VBRI" or len(bitrates) > 1 else "CBR"
    average = (payload_bytes * 8 / 1000 / estimated) if estimated else None
    return Mp3Descriptor(
        mpeg_version=MPEG_VERSION_NAMES[first.version], layer=3, sample_rate=first.sample_rate,
        channels=first.channels, channel_mode=MPEG_CHANNEL_MODES[first.channel_mode],
        samples_per_frame=spf, audio_frames=audio_frames, info_tag=tag,
        info_tag_frames=info.get("frames"), encoder=info.get("encoder"),
        encoder_delay=info.get("delay"), encoder_padding=info.get("padding"),
        lame_tag_crc_valid=info.get("crc_valid"), bitrate_mode=mode,
        nominal_bitrate_kbps=nominal,
        average_bitrate_kbps=round(average, 3) if average else None,
        total_samples=total, duration_seconds=duration, estimated_duration_seconds=estimated,
        audio_offset=detected.audio_offset, audio_end=audio_end, file_size=file_size,
        id3_tags=tuple(id3_tags), trailing_tags=trailing,
        c2pa_container_present=any(item["c2pa_manifest_present"] for item in id3_tags),
        warnings=tuple(warnings), mtime_ns=stat.st_mtime_ns, inode=stat.st_ino)
