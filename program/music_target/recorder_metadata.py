"""Recorder-written BWF/iXML fields from a hash-bound WAV.

Field recorders write scene, take, tape, track names and start timecode into
the file at record time (iXML is the cross-vendor standard; Sound Devices
also mirrors fields as ``sKEY=value`` lines in the bext description). These
are observations of the bound file, classified as ``recorder_metadata``
for production-document consistency checks. A FLAC encoded with
``flac --keep-foreign-metadata`` keeps the same chunks, so FLAC takes are read
by the same code (``recorder_fields_from_chunks``). The file does not authenticate
who wrote those fields; the evaluator reports that limitation without
penalising the score.
"""

import xml.etree.ElementTree as ElementTree

from music_target.wav_io import WavError, iter_chunks, read_chunk


def _bext_fields(description):
    fields = {}
    for line in (description or "").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.startswith("s") and len(key) > 1:
            fields[key[1:].upper()] = value.strip()
    return fields


def _ixml(path, descriptor):
    """Read the iXML chunk of a WAV and parse it."""
    try:
        chunk = next((item for item in iter_chunks(path, descriptor) if item.chunk_id == b"iXML"), None)
        if chunk is None:
            return {}, None
        raw = read_chunk(path, chunk)
    except (OSError, WavError) as exc:
        return {}, "iXML chunk could not be read: " + str(exc)
    return _parse_ixml(raw)


def _parse_ixml(raw):
    """Parse iXML bytes, refusing DTDs so no entity can be expanded."""
    if raw is None:
        return {}, None
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return {}, "iXML chunk declares a DTD or entity and was not parsed."
    try:
        root = ElementTree.fromstring(raw.rstrip(b"\0"))
    except ElementTree.ParseError as exc:
        return {}, "iXML chunk is not well-formed XML: " + str(exc)
    def text(tag):
        node = root.find(".//" + tag)
        return node.text.strip() if node is not None and node.text and node.text.strip() else None
    def number(tag):
        value = text(tag)
        return int(value) if value is not None and value.isdigit() else None
    tracks = {}
    for track in root.iter("TRACK"):
        index, name = track.findtext("CHANNEL_INDEX"), track.findtext("NAME")
        if index and index.strip().isdigit() and name and name.strip():
            tracks[int(index)] = name.strip()
    # The recorder's start time, written alongside bext TimeReference (iXML
    # SPEED block, 64 bits split into HI and LO).
    high, low = number("TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI"), number("TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO")
    return {"scene": text("SCENE"), "take": text("TAKE"), "tape": text("TAPE"),
            "timecode_rate": text("TIMECODE_RATE"), "timecode_flag": text("TIMECODE_FLAG"),
            "timestamp_samples": (high << 32) | low if high is not None and low is not None else None,
            "timestamp_sample_rate": number("TIMESTAMP_SAMPLE_RATE"),
            "tracks": tracks}, None


def recorder_fields(path, descriptor, embedded_metadata):
    """Return recorder metadata for a parsed WAV.

    BWF is also written by DAWs, editors and sound libraries. Only a file that
    names its recording (file name, tape, scene or take) counts as recorder
    metadata; other BWF is reported as ``daw_or_library`` without a recorder
    start timecode, because its time reference is a session timeline position.
    """
    bext = (embedded_metadata or {}).get("broadcast_extension") or {}
    ixml, warning = _ixml(path, descriptor)
    return _recorder_fields(bext, ixml, warning, descriptor.sample_rate, descriptor.duration_seconds)


def recorder_fields_from_chunks(bext, ixml_raw, sample_rate, duration_seconds):
    """Recorder metadata from a parsed ``bext`` dict and raw iXML bytes (FLAC kept chunks)."""
    ixml, warning = _parse_ixml(ixml_raw)
    return _recorder_fields(bext or {}, ixml, warning, sample_rate, duration_seconds)


def _recorder_fields(bext, ixml, warning, rate, duration_seconds):
    warnings = [warning] if warning else []
    if not bext and not ixml:
        return {"present": False, "bwf_kind": None, "warnings": warnings}
    described = _bext_fields(bext.get("description"))
    bext_tracks = {int(key[3:]): value for key, value in described.items()
                   if key.startswith("TRK") and key[3:].isdigit() and value}
    time_reference = bext.get("time_reference")
    reference_seconds = (time_reference / rate) if isinstance(time_reference, int) and rate else None
    sources = sorted(name for name, present in (("bext", bool(bext)), ("iXML", bool(ixml))) if present)
    identity = (described.get("FILENAME"), ixml.get("tape") or described.get("TAPE"),
                ixml.get("scene") or described.get("SCENE"), ixml.get("take") or described.get("TAKE"))
    if not any(identity):
        return {
            "present": False,
            "bwf_kind": "daw_or_library",
            "originator": bext.get("originator") or None,
            "origination_date": bext.get("origination_date") or None,
            "timeline_reference_seconds": reference_seconds,
            "sources": sources,
            "warnings": warnings,
        }
    return {
        "present": True,
        "bwf_kind": "recorder",
        # iXML is the cross-vendor standard; the bext description mirrors it
        # on some recorders and is the only place the recorder file name lives.
        "file_name": described.get("FILENAME"),
        "tape": ixml.get("tape") or described.get("TAPE"),
        "scene": ixml.get("scene") or described.get("SCENE"),
        "take": ixml.get("take") or described.get("TAKE"),
        "tracks": {str(key): value for key, value in sorted((ixml.get("tracks") or bext_tracks).items())},
        "origination_date": bext.get("origination_date") or None,
        "start_timecode_seconds": reference_seconds,
        "time_reference_samples": time_reference if isinstance(time_reference, int) else None,
        "ixml_timestamp_samples": ixml.get("timestamp_samples"),
        "ixml_timestamp_sample_rate": ixml.get("timestamp_sample_rate"),
        "duration_seconds": duration_seconds,
        "timecode_rate": ixml.get("timecode_rate"),
        "timecode_flag": ixml.get("timecode_flag"),
        "sources": sources,
        "warnings": warnings,
    }


def timestamp_checks(digest, recorder, sample_rate):
    """bext TimeReference against the iXML timestamp of the same recorder file.

    The recorder writes both at record time and they agree to the sample (all
    233 Project Rock takes), so a start timecode edited in only one of them is
    a contradiction. Nothing is checked unless both are present.
    """
    recorder = recorder or {}
    bext, ixml = recorder.get("time_reference_samples"), recorder.get("ixml_timestamp_samples")
    if recorder.get("bwf_kind") != "recorder" or bext is None or ixml is None or not sample_rate:
        return []
    ixml_rate = recorder.get("ixml_timestamp_sample_rate") or sample_rate
    bext_seconds, ixml_seconds = bext / sample_rate, ixml / ixml_rate
    # Same rate: exact samples. Otherwise allow one sample at the lower rate.
    passed = (bext == ixml if ixml_rate == sample_rate
              else abs(bext_seconds - ixml_seconds) <= 1 / min(ixml_rate, sample_rate) + 1e-9)
    return [{"rule": "recorder_bext_time_reference_matches_ixml_timestamp", "passed": passed,
             "evidence_hash": digest, "source_pass": "WavMetadataPass",
             "bext_time_reference_samples": bext, "ixml_timestamp_samples": ixml,
             "sample_rate_hz": sample_rate, "ixml_timestamp_sample_rate": ixml_rate,
             "bext_seconds": bext_seconds, "ixml_seconds": ixml_seconds,
             "difference_seconds": bext_seconds - ixml_seconds}]
