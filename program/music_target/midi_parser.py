"""Bounded Standard MIDI File parser with event-level structural checks."""

from __future__ import annotations

import struct

MAX_MIDI_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 1_000_000


def _vlq(data, position):
    value = 0
    for _ in range(4):
        if position >= len(data):
            raise ValueError("Truncated MIDI variable-length quantity.")
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise ValueError("MIDI variable-length quantity exceeds four bytes.")


def _text(payload):
    return payload.decode("utf-8", errors="replace").strip() or None


def _parse_track(data, track_index):
    position = tick = event_count = 0
    running_status = None
    end_seen = False
    summary = {
        "track_index": track_index, "name": None, "event_count": 0,
        "note_on_count": 0, "note_off_count": 0,
        "control_change_count": 0, "program_change_count": 0,
        "pitch_bend_count": 0, "sysex_count": 0, "meta_event_count": 0,
        "duration_ticks": 0, "channels": [], "tempo_events": [],
        "time_signature_events": [], "warnings": [],
    }
    channels = set()
    while position < len(data):
        delta, position = _vlq(data, position)
        tick += delta
        if position >= len(data):
            raise ValueError(f"Track {track_index} ends before an event status byte.")
        first = data[position]
        if first & 0x80:
            status = first
            position += 1
            running_status = status if 0x80 <= status <= 0xEF else None
        else:
            if running_status is None:
                raise ValueError(f"Track {track_index} uses running status without a prior channel event.")
            status = running_status

        if status == 0xFF:
            if position >= len(data):
                raise ValueError(f"Track {track_index} has a truncated meta event.")
            meta_type = data[position]
            length, position = _vlq(data, position + 1)
            end = position + length
            if end > len(data):
                raise ValueError(f"Track {track_index} meta event exceeds its chunk.")
            payload = data[position:end]
            position = end
            summary["meta_event_count"] += 1
            if meta_type == 0x2F:
                if length != 0:
                    raise ValueError(f"Track {track_index} has an invalid end-of-track event.")
                end_seen = True
                if position != len(data):
                    summary["warnings"].append("Bytes occur after the end-of-track event.")
                break
            if meta_type == 0x03 and summary["name"] is None:
                summary["name"] = _text(payload)
            elif meta_type == 0x51:
                if length != 3:
                    raise ValueError(f"Track {track_index} has an invalid tempo event.")
                microseconds = int.from_bytes(payload, "big")
                if microseconds == 0:
                    raise ValueError(f"Track {track_index} tempo cannot be zero.")
                summary["tempo_events"].append({
                    "tick": tick, "microseconds_per_quarter_note": microseconds,
                    "bpm": round(60_000_000 / microseconds, 6),
                })
            elif meta_type == 0x58:
                if length != 4:
                    raise ValueError(f"Track {track_index} has an invalid time-signature event.")
                summary["time_signature_events"].append({
                    "tick": tick, "numerator": payload[0],
                    "denominator": 2 ** payload[1],
                    "clocks_per_metronome": payload[2],
                    "thirty_seconds_per_quarter": payload[3],
                })
        elif status in (0xF0, 0xF7):
            length, position = _vlq(data, position)
            position += length
            if position > len(data):
                raise ValueError(f"Track {track_index} SysEx event exceeds its chunk.")
            summary["sysex_count"] += 1
        elif 0x80 <= status <= 0xEF:
            family, channel = status & 0xF0, status & 0x0F
            channels.add(channel + 1)
            length = 1 if family in (0xC0, 0xD0) else 2
            end = position + length
            if end > len(data):
                raise ValueError(f"Track {track_index} has a truncated channel event.")
            payload = data[position:end]
            if any(item & 0x80 for item in payload):
                raise ValueError(f"Track {track_index} channel data contains a status byte.")
            position = end
            if family == 0x90 and payload[1] > 0:
                summary["note_on_count"] += 1
            elif family == 0x80 or (family == 0x90 and payload[1] == 0):
                summary["note_off_count"] += 1
            elif family == 0xB0:
                summary["control_change_count"] += 1
            elif family == 0xC0:
                summary["program_change_count"] += 1
            elif family == 0xE0:
                summary["pitch_bend_count"] += 1
        else:
            raise ValueError(f"Track {track_index} contains unsupported system status 0x{status:02x}.")

        event_count += 1
        if event_count > MAX_EVENTS:
            raise ValueError("MIDI event count exceeds the parser limit.")
    if not end_seen:
        summary["warnings"].append("No end-of-track meta event was found.")
    summary["event_count"] = event_count + (1 if end_seen else 0)
    summary["duration_ticks"] = tick
    summary["channels"] = sorted(channels)
    return summary


def _duration_seconds(midi_format, timing, tracks):
    if midi_format == 2:
        return None
    end_tick = max(track["duration_ticks"] for track in tracks)
    if timing["mode"] == "smpte":
        return end_tick / (timing["frames_per_second"] * timing["ticks_per_frame"])
    ticks_per_quarter = timing["ticks_per_quarter_note"]
    tempo, previous_tick, elapsed = 500_000, 0, 0.0
    events = sorted((event for track in tracks for event in track["tempo_events"]),
                    key=lambda event: event["tick"])
    for event in events:
        tick = min(event["tick"], end_tick)
        if tick >= previous_tick:
            elapsed += (tick - previous_tick) * tempo / ticks_per_quarter
            previous_tick, tempo = tick, event["microseconds_per_quarter_note"]
        if tick == end_tick:
            break
    elapsed += (end_tick - previous_tick) * tempo / ticks_per_quarter
    return elapsed / 1_000_000


def parse_midi_file(path):
    with open(path, "rb") as handle:
        data = handle.read(MAX_MIDI_BYTES + 1)
    if len(data) > MAX_MIDI_BYTES:
        raise ValueError("MIDI file exceeds 8 MiB parser limit.")
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("Expected a Standard MIDI File MThd header.")
    header_length = struct.unpack_from(">I", data, 4)[0]
    if header_length != 6:
        raise ValueError("Invalid MIDI header length.")
    midi_format, declared_tracks, division = struct.unpack_from(">HHH", data, 8)
    if midi_format not in (0, 1, 2) or declared_tracks < 1 or division == 0:
        raise ValueError("Invalid MIDI format, track count, or time division.")
    if midi_format == 0 and declared_tracks != 1:
        raise ValueError("MIDI format 0 must declare exactly one track.")

    if division & 0x8000:
        frames_per_second = -struct.unpack("b", bytes([(division >> 8) & 0xFF]))[0]
        ticks_per_frame = division & 0xFF
        if frames_per_second not in (24, 25, 29, 30) or not ticks_per_frame:
            raise ValueError("Invalid SMPTE MIDI time division.")
        timing = {"mode": "smpte", "frames_per_second": frames_per_second,
                  "ticks_per_frame": ticks_per_frame}
    else:
        timing = {"mode": "metrical", "ticks_per_quarter_note": division}

    position, tracks = 14, []
    while position < len(data):
        if position + 8 > len(data) or data[position:position + 4] != b"MTrk":
            raise ValueError("Invalid or truncated MIDI track chunk.")
        size = struct.unpack_from(">I", data, position + 4)[0]
        start, end = position + 8, position + 8 + size
        if end > len(data):
            raise ValueError("MIDI track chunk exceeds file length.")
        tracks.append(_parse_track(data[start:end], len(tracks)))
        position = end
    if len(tracks) != declared_tracks:
        raise ValueError("Declared MIDI track count differs from chunk count.")
    warnings = [f"track {track['track_index']}: {warning}"
                for track in tracks for warning in track["warnings"]]
    duration_seconds = _duration_seconds(midi_format, timing, tracks)
    if midi_format == 2:
        warnings.append("Format 2 tracks are asynchronous; one file-level duration was not calculated.")
    return {
        "format": midi_format, "track_count": len(tracks), "time_division": division,
        "timing": timing, "file_size_bytes": len(data), "tracks": tracks,
        "event_count": sum(track["event_count"] for track in tracks),
        "note_on_count": sum(track["note_on_count"] for track in tracks),
        "control_change_count": sum(track["control_change_count"] for track in tracks),
        "tempo_events": [event for track in tracks for event in track["tempo_events"]],
        "time_signature_events": [event for track in tracks
                                  for event in track["time_signature_events"]],
        "duration_ticks": max(track["duration_ticks"] for track in tracks),
        "duration_seconds": round(duration_seconds, 6) if duration_seconds is not None else None,
        "warnings": warnings,
    }
