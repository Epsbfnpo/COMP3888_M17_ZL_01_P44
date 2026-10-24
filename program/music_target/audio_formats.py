"""Content-based audio container detection and bounded ID3v2 tag reading.

The file content decides the format, never the extension. WAV, FLAC and MP3
are supported; AIFF, Ogg, RF64/BW64 and MPEG Layer I/II are recognised so
they are reported as unsupported instead of misread.

ID3v2 tags can precede FLAC as well as MP3: the C2PA SDK stores a FLAC
manifest in an ID3v2 ``GEOB`` frame placed before the ``fLaC`` marker. A
leading ``ID3`` therefore never decides the format on its own; the bytes after
the tags do.

Only the standard library is used, so the desktop builder shares this code.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MAX_LEADING_ID3_TAGS = 4
MAX_ID3_FRAMES = 1024
# Text frames are read up to this size; binary frames (GEOB, APIC, PRIV) only
# their leading descriptor fields.
MAX_ID3_TEXT_FRAME_BYTES = 64 * 1024
ID3_BINARY_PREFIX_BYTES = 512
# A tag with whole-tag unsynchronisation must be read into memory to be
# decoded; larger ones are skipped with a warning.
MAX_UNSYNCHRONISED_TAG_BYTES = 1 << 20
# How far past the tags the first MPEG frame may start (junk is reported).
MAX_MPEG_SYNC_SEARCH_BYTES = 64 * 1024

C2PA_MIME_TYPE = "application/c2pa"

_MPEG_BITRATES_KBPS = {
    # (version is MPEG-1, layer) -> table indexed by bitrate index
    (True, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (False, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MPEG_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
MPEG_VERSION_NAMES = {3: "1", 2: "2", 0: "2.5"}
MPEG_CHANNEL_MODES = ("stereo", "joint_stereo", "dual_channel", "mono")


@dataclass(frozen=True)
class MpegHeader:
    """One MPEG audio frame header (Layer III only has a frame length here)."""

    version: int          # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5
    layer: int            # 1, 2 or 3
    protected: bool       # a 16-bit CRC follows the header
    bitrate_kbps: int     # 0 for free format
    sample_rate: int
    padding: int
    channel_mode: int     # index into MPEG_CHANNEL_MODES
    frame_bytes: int      # 0 when not computable (free format, Layer I/II)
    samples_per_frame: int

    @property
    def channels(self) -> int:
        return 1 if self.channel_mode == 3 else 2

    @property
    def side_info_bytes(self) -> int:
        if self.version == 3:
            return 17 if self.channel_mode == 3 else 32
        return 9 if self.channel_mode == 3 else 17


def mpeg_header(data: bytes, offset: int = 0) -> MpegHeader | None:
    """Decode a frame header at ``offset``, or None when the bytes are not one."""
    if len(data) < offset + 4:
        return None
    b0, b1, b2, b3 = data[offset:offset + 4]
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    version, layer_bits = (b1 >> 3) & 3, (b1 >> 1) & 3
    bitrate_index, rate_index = b2 >> 4, (b2 >> 2) & 3
    if version == 1 or layer_bits == 0 or bitrate_index == 15 or rate_index == 3:
        return None
    layer = 4 - layer_bits
    sample_rate = _MPEG_SAMPLE_RATES[version][rate_index]
    padding = (b2 >> 1) & 1
    bitrate, frame_bytes = 0, 0
    if layer == 3:
        samples = 1152 if version == 3 else 576
        bitrate = _MPEG_BITRATES_KBPS[(version == 3, 3)][bitrate_index]
        if bitrate:
            frame_bytes = (144 if version == 3 else 72) * bitrate * 1000 // sample_rate + padding
    else:
        samples = 384 if layer == 1 else 1152
    return MpegHeader(version=version, layer=layer, protected=not (b1 & 1),
                      bitrate_kbps=bitrate, sample_rate=sample_rate, padding=padding,
                      channel_mode=b3 >> 6, frame_bytes=frame_bytes, samples_per_frame=samples)


def _synchsafe(raw: bytes) -> int | None:
    if any(byte & 0x80 for byte in raw):
        return None
    value = 0
    for byte in raw:
        value = (value << 7) | byte
    return value


@dataclass(frozen=True)
class Id3Header:
    offset: int
    major: int
    revision: int
    flags: int
    body_size: int        # bytes after the 10-byte header, excluding a footer

    @property
    def total_size(self) -> int:
        return 10 + self.body_size + (10 if self.major == 4 and self.flags & 0x10 else 0)


def id3_header(data: bytes, offset: int = 0) -> Id3Header | None:
    """Decode an ID3v2 header ("ID3", version 2–4, synchsafe size)."""
    if len(data) < offset + 10 or data[offset:offset + 3] != b"ID3":
        return None
    major, revision, flags = data[offset + 3], data[offset + 4], data[offset + 5]
    size = _synchsafe(data[offset + 6:offset + 10])
    if major not in (2, 3, 4) or revision == 0xFF or size is None:
        return None
    return Id3Header(offset, major, revision, flags, size)


@dataclass(frozen=True)
class Detected:
    """What a file contains, judged from its bytes."""

    container: str | None     # WAV, FLAC, MP3, AIFF, Ogg, RF64, MPEG Layer I/II, or None
    audio_offset: int         # where the container starts after leading ID3v2 tags
    leading_id3: tuple[Id3Header, ...]
    warnings: tuple[str, ...] = ()


def _find_mpeg_frame(handle, start: int, limit: int, file_size: int):
    """First offset in [start, start + limit) where two consecutive frames agree."""
    handle.seek(start)
    window = handle.read(min(limit, max(0, file_size - start)) + 8)
    for index in range(min(limit, max(0, len(window) - 3))):
        if window[index] != 0xFF:
            continue
        first = mpeg_header(window, index)
        if first is None:
            continue
        if first.layer != 3 or not first.frame_bytes:
            # Layer I/II or free format: recognised as MPEG audio but not parsed further.
            return start + index, first
        following = start + index + first.frame_bytes
        if following >= file_size:
            return start + index, first  # a single frame filling the file
        handle.seek(following)
        second = mpeg_header(handle.read(4))
        if (second is not None and second.version == first.version and second.layer == first.layer
                and second.sample_rate == first.sample_rate):
            return start + index, first
    return None, None


def detect(path) -> Detected:
    """Identify the container from the file's bytes; raises OSError if unreadable."""
    path = Path(path)
    file_size = path.stat().st_size
    with path.open("rb") as handle:
        position, tags, warnings = 0, [], []
        while len(tags) < MAX_LEADING_ID3_TAGS:
            handle.seek(position)
            header = id3_header(handle.read(10))
            if header is None:
                break
            header = Id3Header(position, header.major, header.revision, header.flags, header.body_size)
            if position + header.total_size > file_size:
                warnings.append("id3v2_tag_truncated")
                return Detected(None, position, tuple(tags + [header]), tuple(warnings))
            tags.append(header)
            position += header.total_size
        handle.seek(position)
        head = handle.read(12)
        if head[:4] == b"fLaC":
            return Detected("FLAC", position, tuple(tags), tuple(warnings))
        if position == 0:
            if head[:4] in (b"RF64", b"BW64"):
                return Detected("RF64", 0, (), ())
            if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                return Detected("WAV", 0, (), ())
            if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
                return Detected("AIFF", 0, (), ())
            if head[:4] == b"OggS":
                return Detected("Ogg", 0, (), ())
        # MPEG audio: a frame right here, or after junk that follows ID3 tags.
        limit = MAX_MPEG_SYNC_SEARCH_BYTES if tags else 4
        offset, frame = _find_mpeg_frame(handle, position, limit, file_size)
        if frame is not None:
            if offset != position:
                warnings.append(f"junk_before_first_mpeg_frame:{offset - position}")
            container = "MP3" if frame.layer == 3 else "MPEG Layer I/II"
            return Detected(container, offset, tuple(tags), tuple(warnings))
        return Detected(None, position, tuple(tags), tuple(warnings))


# --- ID3v2 frames --------------------------------------------------------

_TEXT_ENCODINGS = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}


def _text(raw: bytes, encoding: int) -> str:
    name = _TEXT_ENCODINGS.get(encoding, "latin-1")
    try:
        value = raw.decode(name, errors="replace")
    except (LookupError, UnicodeDecodeError):  # odd-length or BOM-less UTF-16
        value = raw.decode("latin-1", errors="replace")
    return value.replace("\x00", "/").strip("/ ").strip()


def _split_terminated(raw: bytes, encoding: int) -> tuple[bytes, bytes]:
    """Split at the first string terminator (two zero bytes for UTF-16)."""
    if encoding in (1, 2):
        for index in range(0, len(raw) - 1, 2):
            if raw[index] == 0 and raw[index + 1] == 0:
                return raw[:index], raw[index + 2:]
        return raw, b""
    head, _, tail = raw.partition(b"\x00")
    return head, tail


def _undo_unsynchronisation(raw: bytes) -> bytes:
    return raw.replace(b"\xff\x00", b"\xff")


def _frame_summary(frame_id: str, body: bytes, full_size: int) -> dict:
    """Decoded text or descriptor fields of one frame; binary payloads are not kept."""
    item = {"id": frame_id, "size": full_size}
    if not body:
        return item
    encoding = body[0]
    if frame_id == "TXXX" or frame_id == "TXX":
        description, value = _split_terminated(body[1:], encoding)
        item.update(description=_text(description, encoding), value=_text(value, encoding))
    elif frame_id.startswith("T"):
        item["value"] = _text(body[1:], encoding)
    elif frame_id in ("GEOB", "GEO"):
        mime, rest = body[1:].partition(b"\x00")[::2]
        filename, rest = _split_terminated(rest, encoding)
        description, _ = _split_terminated(rest, encoding)
        item.update(mime_type=mime.decode("latin-1", errors="replace"),
                    filename=_text(filename, encoding), description=_text(description, encoding))
    elif frame_id in ("PRIV",):
        item["owner"] = body.partition(b"\x00")[0].decode("latin-1", errors="replace")
    elif frame_id in ("COMM", "COM"):
        language = body[1:4].decode("latin-1", errors="replace")
        description, value = _split_terminated(body[4:], encoding)
        item.update(language=language, description=_text(description, encoding),
                    value=_text(value, encoding))
    return item


def read_id3_tag(path, header: Id3Header) -> dict:
    """Bounded read of one ID3v2 tag: its frames' text and descriptors.

    Compressed and encrypted frames are not decoded (compression could expand
    without bound); they are listed with a warning.
    """
    result = {"offset": header.offset, "version": f"2.{header.major}.{header.revision}",
              "size": header.total_size, "frames": [], "c2pa_manifest_present": False,
              "warnings": []}
    warnings = result["warnings"]
    major, tag_unsync = header.major, bool(header.flags & 0x80)
    body_start, body_end = header.offset + 10, header.offset + 10 + header.body_size
    with Path(path).open("rb") as handle:
        buffer = None
        if tag_unsync and major < 4:
            if header.body_size > MAX_UNSYNCHRONISED_TAG_BYTES:
                warnings.append("unsynchronised_tag_too_large_frames_not_read")
                return result
            handle.seek(body_start)
            buffer = _undo_unsynchronisation(handle.read(header.body_size))
            body_start, body_end = 0, len(buffer)

        def read(offset, size):
            if buffer is not None:
                return buffer[offset:offset + size]
            handle.seek(offset)
            return handle.read(size)

        position = body_start
        if header.flags & 0x40:  # extended header
            raw = read(position, 4)
            if major == 3 and len(raw) == 4:
                position += 4 + struct.unpack(">I", raw)[0]
            elif major == 4 and (size := _synchsafe(raw)) is not None:
                position += size
            else:
                warnings.append("invalid_extended_header")
                return result
        header_bytes = 6 if major == 2 else 10
        while position + header_bytes <= body_end:
            if len(result["frames"]) >= MAX_ID3_FRAMES:
                warnings.append("too_many_frames")
                break
            raw = read(position, header_bytes)
            if len(raw) < header_bytes or raw[0] == 0:
                break  # padding
            if major == 2:
                frame_id = raw[:3].decode("latin-1", errors="replace")
                size, flags = int.from_bytes(raw[3:6], "big"), 0
            else:
                frame_id = raw[:4].decode("latin-1", errors="replace")
                size = (_synchsafe(raw[4:8]) if major == 4 else struct.unpack(">I", raw[4:8])[0])
                flags = struct.unpack(">H", raw[8:10])[0]
            if size is None or not frame_id.isalnum() or position + header_bytes + size > body_end:
                warnings.append(f"invalid_frame_at:{position}")
                break
            data_start = position + header_bytes
            position = data_start + size
            if major == 3:
                compressed, encrypted, grouped = flags & 0x0080, flags & 0x0040, flags & 0x0020
                frame_unsync, length_indicator = False, False
            elif major == 4:
                compressed, encrypted, grouped = flags & 0x0008, flags & 0x0004, flags & 0x0040
                frame_unsync, length_indicator = bool(flags & 0x0002) or tag_unsync, bool(flags & 0x0001)
            else:
                compressed = encrypted = grouped = frame_unsync = length_indicator = False
            if compressed or encrypted:
                result["frames"].append({"id": frame_id, "size": size, "not_decoded":
                                         "compressed" if compressed else "encrypted"})
                warnings.append(f"{frame_id}: compressed or encrypted frame not decoded")
                continue
            skip = (1 if grouped else 0) + (4 if length_indicator else 0)
            binary = frame_id in ("GEOB", "GEO", "APIC", "PIC", "PRIV")
            limit = ID3_BINARY_PREFIX_BYTES if binary else MAX_ID3_TEXT_FRAME_BYTES
            body = read(data_start + skip, max(0, min(size - skip, limit)))
            if frame_unsync:
                body = _undo_unsynchronisation(body)
            if not binary and size - skip > limit:
                warnings.append(f"{frame_id}: text truncated to {limit} bytes")
            summary = _frame_summary(frame_id, body, size)
            if summary.get("mime_type", "").lower() == C2PA_MIME_TYPE:
                result["c2pa_manifest_present"] = True
            result["frames"].append(summary)
    return result


def trailing_tags(path, audio_start: int, file_size: int) -> tuple[int, dict]:
    """Where audio ends before an ID3v1, APEv2 or appended ID3v2 tag, and which tags were found."""
    found, end = {}, file_size
    with Path(path).open("rb") as handle:
        for _ in range(3):  # tags may be stacked; each pass removes one
            if end - audio_start >= 128:
                handle.seek(end - 128)
                block = handle.read(128)
                if block[:3] == b"TAG" and "id3v1" not in found:
                    found["id3v1"] = {
                        "title": block[3:33].split(b"\x00")[0].decode("latin-1", errors="replace").strip(),
                        "artist": block[33:63].split(b"\x00")[0].decode("latin-1", errors="replace").strip(),
                    }
                    end -= 128
                    continue
            if end - audio_start >= 32:
                handle.seek(end - 32)
                footer = handle.read(32)
                if footer[:8] == b"APETAGEX" and "apev2" not in found:
                    size, flags = struct.unpack("<I", footer[12:16])[0], struct.unpack("<I", footer[20:24])[0]
                    total = size + (32 if flags & 0x80000000 else 0)
                    if total <= end - audio_start:
                        found["apev2"] = {"size": total}
                        end -= total
                        continue
            if end - audio_start >= 10:
                handle.seek(end - 10)
                footer = handle.read(10)
                if footer[:3] == b"3DI" and "appended_id3v2" not in found:
                    size = _synchsafe(footer[6:10])
                    if size is not None and size + 20 <= end - audio_start:
                        found["appended_id3v2"] = Id3Header(end - size - 20, footer[3], footer[4],
                                                            footer[5], size)
                        end -= size + 20
                        continue
            break
    return end, found
