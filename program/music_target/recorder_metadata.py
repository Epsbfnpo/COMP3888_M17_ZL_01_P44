"""Recorder-written BWF/iXML fields from a hash-bound WAV.

Field recorders write scene, take, tape, track names and start timecode into
the file at record time (iXML is the cross-vendor standard; Sound Devices
also mirrors fields as ``sKEY=value`` lines in the bext description). These
are observations of the bound file, classified as ``recorder_metadata``
for production-document consistency checks. The file does not authenticate
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
    """Parse the iXML chunk, refusing DTDs so no entity can be expanded."""
    try:
        chunk = next((item for item in iter_chunks(path, descriptor) if item.chunk_id == b"iXML"), None)
        if chunk is None:
            return {}, None
        raw = read_chunk(path, chunk)
    except (OSError, WavError) as exc:
        return {}, "iXML chunk could not be read: " + str(exc)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return {}, "iXML chunk declares a DTD or entity and was not parsed."
    try:
        root = ElementTree.fromstring(raw.rstrip(b"\0"))
    except ElementTree.ParseError as exc:
        return {}, "iXML chunk is not well-formed XML: " + str(exc)
    def text(tag):
        node = root.find(".//" + tag)
        return node.text.strip() if node is not None and node.text and node.text.strip() else None
    tracks = {}
    for track in root.iter("TRACK"):
        index, name = track.findtext("CHANNEL_INDEX"), track.findtext("NAME")
        if index and index.strip().isdigit() and name and name.strip():
            tracks[int(index)] = name.strip()
    return {"scene": text("SCENE"), "take": text("TAKE"), "tape": text("TAPE"),
            "timecode_rate": text("TIMECODE_RATE"), "timecode_flag": text("TIMECODE_FLAG"),
            "tracks": tracks}, None


def recorder_fields(path, descriptor, embedded_metadata):
    """Return recorder metadata for a parsed WAV, or None when it has none."""
    bext = (embedded_metadata or {}).get("broadcast_extension") or {}
    ixml, warning = _ixml(path, descriptor)
    if not bext and not ixml:
        return {"present": False, "warnings": [warning] if warning else []}
    described = _bext_fields(bext.get("description"))
    bext_tracks = {int(key[3:]): value for key, value in described.items()
                   if key.startswith("TRK") and key[3:].isdigit() and value}
    time_reference = bext.get("time_reference")
    rate = descriptor.sample_rate
    return {
        "present": True,
        # iXML is the cross-vendor standard; the bext description mirrors it
        # on some recorders and is the only place the recorder file name lives.
        "file_name": described.get("FILENAME"),
        "tape": ixml.get("tape") or described.get("TAPE"),
        "scene": ixml.get("scene") or described.get("SCENE"),
        "take": ixml.get("take") or described.get("TAKE"),
        "tracks": {str(key): value for key, value in sorted((ixml.get("tracks") or bext_tracks).items())},
        "origination_date": bext.get("origination_date") or None,
        "start_timecode_seconds": (time_reference / rate) if isinstance(time_reference, int) and rate else None,
        "duration_seconds": descriptor.duration_seconds,
        "timecode_rate": ixml.get("timecode_rate"),
        "timecode_flag": ixml.get("timecode_flag"),
        "sources": sorted(name for name, present in (("bext", bool(bext)), ("iXML", bool(ixml))) if present),
        "warnings": [warning] if warning else [],
    }
