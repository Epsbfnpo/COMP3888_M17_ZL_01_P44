"""Ableton Live sessions (.als) as production evidence.

An .als file is gzip-compressed XML listing the tempo, time signature, tracks
and every audio clip's sample (file name, original file size, length in
samples and sample rate). It is written by the submitter's own software, so
its checks are ``submitter_document`` tier: they reveal contradictions with
the submitted graph and MIDI, but never release an Integrity value alone.

A sample reference is matched to a submitted WAV by exact file size, frame
count and sample rate, not by name: names are free text, those three are
properties of the bytes.
"""

import gzip
import math
from pathlib import PurePosixPath, PureWindowsPath
import xml.etree.ElementTree as ElementTree

from music_target.evidence_tiers import SUBMITTER_DOCUMENT

SOURCE_RELATIONSHIPS = {"mixed_from", "stemmed_from", "edited_from", "comped_from", "derived_from",
                        "excerpted_from"}


class DawSessionError(ValueError):
    pass


def _value(element, path):
    node = element.find(path)
    return None if node is None else node.get("Value")


def _number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _sample_reference(clip):
    ref = clip.find("SampleRef")
    file_ref = None if ref is None else ref.find("FileRef")
    if file_ref is None:
        return None
    path = _value(file_ref, "Path") or ""
    relative = _value(file_ref, "RelativePath") or ""
    if not relative:  # Live 10 and earlier: RelativePathElement Dir="..." + Name
        relative = "/".join(item.get("Dir", "") for item in file_ref.iter("RelativePathElement"))
    name = _value(file_ref, "Name") or (PureWindowsPath(path).name if "\\" in path else PurePosixPath(path).name)
    frames, rate, size = (_number(_value(ref, "DefaultDuration")), _number(_value(ref, "DefaultSampleRate")),
                          _number(_value(file_ref, "OriginalFileSize")))
    return {"clip_name": _value(clip, "Name"), "file_name": name or None,
            "relative_path": relative or None,
            "frames": int(frames) if frames is not None else None,
            "sample_rate_hz": int(rate) if rate is not None else None,
            "file_size_bytes": int(size) if size is not None else None,
            "arrangement_start_beats": _number(clip.get("Time")),
            "current_start_beats": _number(_value(clip, "CurrentStart")),
            "current_end_beats": _number(_value(clip, "CurrentEnd"))}


def parse_ableton_session(path):
    with open(path, "rb") as handle:
        head = handle.read(2)
    if head != b"\x1f\x8b":
        raise DawSessionError("Not an Ableton Live set (expected gzip-compressed XML).")
    with gzip.open(path, "rb") as stream:
        prefix = stream.read(4096)
    if b"<!DOCTYPE" in prefix.upper() or b"<!ENTITY" in prefix.upper():
        raise DawSessionError("Session XML declares a DTD or entity and was not parsed.")
    try:
        with gzip.open(path, "rb") as stream:
            root = ElementTree.parse(stream).getroot()
    except (OSError, EOFError, ElementTree.ParseError) as exc:
        raise DawSessionError(f"Ableton Live set could not be read: {exc}") from exc
    if root.tag != "Ableton" or root.find("LiveSet") is None:
        raise DawSessionError("XML is not an Ableton Live set.")
    live_set = root.find("LiveSet")
    tempo = _number(_value(live_set, ".//MasterTrack//Tempo/Manual") or _value(live_set, ".//Tempo/Manual"))
    numerator = _number(_value(live_set, ".//MasterTrack//TimeSignature//Numerator"))
    denominator = _number(_value(live_set, ".//MasterTrack//TimeSignature//Denominator"))
    tracks, samples, midi_notes = [], [], 0
    track_list = live_set.find("Tracks")
    for track in (list(track_list) if track_list is not None else []):
        name = _value(track, "Name/EffectiveName") or _value(track, "Name/UserName")
        tracks.append({"kind": track.tag, "name": name})
        for clip in track.iter("AudioClip"):
            reference = _sample_reference(clip)
            if reference:
                samples.append({**reference, "track": name})
        midi_notes += sum(1 for _ in track.iter("MidiNoteEvent"))
    warnings = [] if samples or midi_notes else ["The set contains no audio clips or MIDI notes."]
    return {"metadata": {"creator": root.get("Creator"), "tempo_bpm": tempo,
                         "time_signature": (f"{int(numerator)}/{int(denominator)}"
                                            if numerator and denominator else None)},
            "tracks": tracks, "samples": samples, "midi_note_count": midi_notes, "warnings": warnings}


def daw_session_checks(data, owner_digest, native, wav_observations, parser_observations):
    checks = []
    session = {}
    for sample in data.get("samples", []):
        key = (sample.get("file_size_bytes"), sample.get("frames"), sample.get("sample_rate_hz"))
        # One file used by several clips is one sample.
        session.setdefault(key, set()).add(sample.get("relative_path") or sample.get("file_name") or len(session))

    def shape(digest):
        observed = (wav_observations.get(digest) or {}).get("observed") or {}
        if not observed.get("file_size_bytes") or not observed.get("sample_rate_hz"):
            return None
        return (observed["file_size_bytes"], round(observed["duration_seconds"] * observed["sample_rate_hz"]),
                observed["sample_rate_hz"])

    # Every audio source the graph declares for an artefact this session
    # describes should be a sample the session uses. Stems exported from one
    # session usually share size, length and rate, so files are not matched
    # one by one (which would be a guess): within each group of identical
    # shape, the session must use at least as many samples as are declared.
    nodes = {node["artefact_hash"]: node for node in native["artefacts"]}
    targets = sorted(digest for digest, node in nodes.items()
                     if any(edge["hash"] == owner_digest and edge["relationship_type"] == "input_to"
                            for edge in node.get("evidence", [])))
    for target in targets:
        groups = {}
        for edge in nodes[target].get("evidence", []):
            key = shape(edge["hash"]) if edge["relationship_type"] in SOURCE_RELATIONSHIPS else None
            if key is not None:
                groups.setdefault(key, []).append(edge["hash"])
        for (size, frames, rate), sources in sorted(groups.items()):
            available = len(session.get((size, frames, rate), ()))
            checks.append({"rule": "daw_session_uses_declared_source_audio", "passed": available >= len(sources),
                           "evidence_tier": SUBMITTER_DOCUMENT, "evidence_hash": owner_digest,
                           "target_hash": target, "audio_hashes": sorted(sources),
                           "audio_hash": sources[0] if len(sources) == 1 else None,
                           "declared_sources": len(sources), "session_samples": available,
                           "shape": {"file_size_bytes": size, "frames": frames, "sample_rate_hz": rate}})
    tempo = (data.get("metadata") or {}).get("tempo_bpm")
    signature = (data.get("metadata") or {}).get("time_signature")
    for digest, parsed in parser_observations.items():
        midi = parsed.get("data") if parsed.get("parser") == "midi" else None
        if not midi:
            continue
        tempos = midi.get("tempo_events") or []
        if tempo is not None and tempos and isinstance(tempos[0].get("bpm"), (int, float)):
            checks.append({"rule": "daw_session_tempo_matches_midi",
                           "passed": math.isclose(tempo, tempos[0]["bpm"], abs_tol=0.01),
                           "evidence_tier": SUBMITTER_DOCUMENT, "evidence_hash": owner_digest,
                           "midi_hash": digest, "declared": tempo, "observed": tempos[0]["bpm"]})
        signatures = midi.get("time_signature_events") or []
        if signature and signatures:
            observed = f"{signatures[0]['numerator']}/{signatures[0]['denominator']}"
            checks.append({"rule": "daw_session_time_signature_matches_midi", "passed": observed == signature,
                           "evidence_tier": SUBMITTER_DOCUMENT, "evidence_hash": owner_digest,
                           "midi_hash": digest, "declared": signature, "observed": observed})
    return checks
