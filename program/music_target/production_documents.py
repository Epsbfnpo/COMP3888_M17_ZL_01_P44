"""Sound reports and AAF edits, checked against recorder metadata.

Both documents come from the submitter, so on their own they are
declarations. Their value is that their timecodes, scene/take numbers and
track names can be compared with metadata a field recorder wrote into the
hash-bound WAV files. Agreement there raises the cost of fabrication; it is
the ``recorder_metadata`` evidence tier. Comparisons with measured file
properties alone (for example a declared length) stay ``submitter_document``.
"""

import csv
import datetime
import io
import math
from pathlib import Path, PurePosixPath
import urllib.parse

from music_target.evidence_tiers import RECORDER_METADATA, SUBMITTER_DOCUMENT


class ProductionDocumentError(ValueError):
    pass


# --- shared helpers -------------------------------------------------------

def _normalise_take(value):
    text = str(value or "").strip().casefold()
    return text.lstrip("0") or ("0" if text else "")


def _normalise(value):
    return str(value or "").strip().casefold()


def _timecode_seconds(value, fps):
    parts = str(value or "").strip().split(":")
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        raise ProductionDocumentError(f"Timecode {value!r} is not HH:MM:SS:FF.")
    hours, minutes, seconds, frames = (int(part) for part in parts)
    if minutes > 59 or seconds > 59 or frames >= fps:
        raise ProductionDocumentError(f"Timecode {value!r} is out of range at {fps} fps.")
    return hours * 3600 + minutes * 60 + seconds + frames / fps


def _duration_seconds(value):
    parts = str(value or "").strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise ProductionDocumentError(f"Length {value!r} is not HH:MM:SS.")
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def recorder_index(wav_observations):
    """Map (tape, file name) and file name to graph WAVs with recorder metadata."""
    by_key, by_name = {}, {}
    for digest, observation in wav_observations.items():
        recorder = (observation or {}).get("recorder_metadata") or {}
        name = recorder.get("file_name")
        if not recorder.get("present") or not name:
            continue
        by_key.setdefault((_normalise(recorder.get("tape")), _normalise(name)), []).append(digest)
        by_name.setdefault(_normalise(name), []).append(digest)
    return by_key, by_name


def _check(rule, passed, tier, **extra):
    return {"rule": rule, "passed": passed, "evidence_tier": tier, **extra}


def _unavailable(rule, reason, tier, **extra):
    return {"rule": rule, "status": "unavailable", "passed": None, "reason": reason,
            "evidence_tier": tier, **extra}


def _identity_checks(prefix, document, recorder, common):
    """Scene/take and track names declared in a document vs the recorder."""
    checks = []
    if document.get("scene") and document.get("take") and recorder.get("scene") and recorder.get("take"):
        checks.append(_check(f"{prefix}_scene_take_matches_recorder",
                             (_normalise(document["scene"]), _normalise_take(document["take"])) ==
                             (_normalise(recorder["scene"]), _normalise_take(recorder["take"])),
                             RECORDER_METADATA, declared=[document["scene"], document["take"]],
                             recorded=[recorder["scene"], recorder["take"]], **common))
    declared_tracks = {str(key): value for key, value in (document.get("tracks") or {}).items() if value}
    if declared_tracks and recorder.get("tracks"):
        differing = sorted(key for key, value in declared_tracks.items()
                           if _normalise(recorder["tracks"].get(key)) != _normalise(value))
        checks.append(_check(f"{prefix}_track_names_match_recorder", not differing, RECORDER_METADATA,
                             differing_tracks=differing, **common))
    return checks


# --- sound reports ---------------------------------------------------------

def parse_sound_report_file(path):
    """Parse a field-recorder sound report CSV (metadata lines, then a take table)."""
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    metadata, header, takes, warnings = {}, None, [], []
    for row in rows:
        cells = [cell.strip() for cell in row]
        if not any(cells):
            continue
        if header is None:
            if cells[0].casefold() == "file name":
                header = [cell.casefold() for cell in cells]
            elif cells[0].endswith(":") and len(cells) > 1:
                metadata[cells[0][:-1].strip().casefold()] = cells[1]
            continue
        record = dict(zip(header, cells))
        name = record.get("file name")
        if not name:
            continue
        tracks = {key.split()[-1]: value for key, value in record.items()
                  if key.startswith("trk") and key.split()[-1].isdigit() and value}
        takes.append({"file_name": name, "scene": record.get("scene") or None,
                      "take": record.get("take") or None, "length": record.get("length") or None,
                      "start_timecode": record.get("start tc") or None, "tracks": tracks,
                      "notes": record.get("notes") or None})
    if header is None:
        raise ProductionDocumentError("Sound report has no 'File Name' take table.")
    frame_rate = metadata.get("frame rate", "")
    try:
        fps = float(frame_rate.split()[0]) if frame_rate else None
    except ValueError:
        fps = None
    drop_frame = " df" in f" {frame_rate.casefold()}" or "drop" in frame_rate.casefold() and "non" not in frame_rate.casefold()
    if fps is None:
        warnings.append("Frame rate is missing or unreadable; timecodes cannot be compared.")
    return {"metadata": {"project": metadata.get("project"), "date": metadata.get("date"),
                         "frame_rate": frame_rate or None, "fps": fps, "drop_frame": drop_frame,
                         "sample_rate": metadata.get("sample rate"), "bit_depth": metadata.get("bit depth")},
            "takes": takes, "warnings": warnings}


def _report_dates(value):
    """A dd/mm/yy or mm/dd/yy report date, as every ISO reading it allows."""
    parts = str(value or "").replace("-", "/").replace(".", "/").split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return set()
    first, second, year = (int(part) for part in parts)
    year += 2000 if year < 100 else 0
    readings = set()
    for day, month in ((first, second), (second, first)):
        try:
            readings.add(datetime.date(year, month, day).isoformat())
        except ValueError:
            pass
    return readings


def sound_report_checks(data, owner_digest, wav_observations):
    _, by_name = recorder_index(wav_observations)
    meta = data.get("metadata") or {}
    dates = _report_dates(meta.get("date"))
    fps, drop = meta.get("fps"), meta.get("drop_frame")
    checks = []
    for row in data.get("takes", []):
        # Recorders restart file numbering each day, so a report row names a
        # recording by (date, file name). A same-named file recorded on
        # another day is a different take, not a contradiction.
        candidates = by_name.get(_normalise(row["file_name"]), [])
        if dates:
            candidates = [digest for digest in candidates
                          if wav_observations[digest]["recorder_metadata"].get("origination_date") in dates
                          or not wav_observations[digest]["recorder_metadata"].get("origination_date")]
        if not candidates:
            continue  # a report lists every take; unsubmitted takes are not contradictions
        common = {"evidence_hash": owner_digest, "file_name": row["file_name"],
                  "matched_by": "date_and_file_name" if dates else "file_name"}
        if len(candidates) > 1:
            checks.append(_unavailable("sound_report_take_matches_one_recording",
                                       "Several submitted recordings share this file name and date.",
                                       RECORDER_METADATA, audio_hashes=sorted(candidates), **common))
            continue
        digest = candidates[0]
        recorder = wav_observations[digest]["recorder_metadata"]
        common["audio_hash"] = digest
        recorded_start = recorder.get("start_timecode_seconds")
        if row.get("start_timecode") and recorded_start is not None:
            if not fps or drop:
                checks.append(_unavailable("sound_report_start_timecode_matches_recorder",
                                           "Only non-drop-frame report timecode with a known frame rate is compared.",
                                           RECORDER_METADATA, **common))
            else:
                try:
                    declared = _timecode_seconds(row["start_timecode"], fps)
                    checks.append(_check("sound_report_start_timecode_matches_recorder",
                                         abs(declared - recorded_start) <= 1 / fps + 1e-6, RECORDER_METADATA,
                                         declared_seconds=declared, recorded_seconds=recorded_start,
                                         difference_seconds=declared - recorded_start, fps=fps, **common))
                except ProductionDocumentError as exc:
                    checks.append(_unavailable("sound_report_start_timecode_matches_recorder", str(exc),
                                               RECORDER_METADATA, **common))
        checks.extend(_identity_checks("sound_report", row, recorder, common))
        if row.get("length"):
            try:
                declared = _duration_seconds(row["length"])
                # Reports print whole seconds; allow one second of rounding.
                checks.append(_check("sound_report_length_matches_audio",
                                     abs(declared - recorder["duration_seconds"]) <= 1.0, SUBMITTER_DOCUMENT,
                                     declared_seconds=declared, observed_seconds=recorder["duration_seconds"],
                                     **common))
            except ProductionDocumentError as exc:
                checks.append(_unavailable("sound_report_length_matches_audio", str(exc),
                                           SUBMITTER_DOCUMENT, **common))
    return checks


# --- AAF edits ---------------------------------------------------------------

def _comments(mob):
    try:
        return {item.name: item.value for item in mob["UserComments"].value}
    except Exception:
        return {}


def _locator_paths(mob, depth=0, seen=None):
    seen = seen if seen is not None else set()
    if mob is None or depth > 6 or mob.mob_id in seen:
        return []
    seen.add(mob.mob_id)
    found = []
    try:
        for locator in mob.descriptor["Locator"].value:
            found.append(urllib.parse.unquote(urllib.parse.urlparse(locator["URLString"].value).path))
    except Exception:
        pass
    for slot in mob.slots:
        found.extend(_locator_paths(getattr(slot.segment, "mob", None), depth + 1, seen))
    return found


def _tape_ranges(master):
    """Source ranges a master mob's clips take from a tape source mob, in seconds."""
    ranges = []
    for slot in master.slots:
        file_clip = slot.segment
        file_mob = getattr(file_clip, "mob", None)
        if file_mob is None:
            continue
        for file_slot in file_mob.slots:
            clip = file_slot.segment
            tape = getattr(clip, "mob", None)
            descriptor = type(getattr(tape, "descriptor", None)).__name__
            if tape is None or descriptor != "TapeDescriptor":
                continue
            rate = float(file_slot.edit_rate)
            tape_start = 0
            for tape_slot in tape.slots:
                if type(tape_slot.segment).__name__ == "Timecode":
                    tape_start = tape_slot.segment["Start"].value / tape_slot.segment["FPS"].value
                    break
            start = tape_start + (clip.start or 0) / rate
            ranges.append({"tape": tape.name, "start_seconds": start,
                           "end_seconds": start + (clip.length or 0) / rate, "edit_rate": rate})
    return ranges


def parse_aaf_file(path):
    """List recorder-take references in an AAF: name, tape, scene/take, source range."""
    try:
        import aaf2
    except ImportError as exc:
        raise ProductionDocumentError("pyaaf2 is not installed; AAF evidence was not assessed.") from exc
    references, warnings = [], []
    try:
        with aaf2.open(str(path), "r") as handle:
            compositions = sorted(mob.name for mob in handle.content.compositionmobs() if mob.name)
            for master in handle.content.mastermobs():
                comments = _comments(master)
                ranges = _tape_ranges(master)
                if not ranges:
                    continue  # not a recorder take (library SFX, renders, tone)
                paths = [item for item in _locator_paths(master) if item.casefold().endswith(".wav")]
                file_name = comments.get("TapeID") or (PurePosixPath(paths[0]).name if paths else None)
                tape = ranges[0]["tape"] or (PurePosixPath(paths[0]).parent.name if paths else None)
                tracks = {key[3:]: value for key, value in comments.items()
                          if key.startswith("TRK") and key[3:].isdigit() and value}
                references.append({
                    "master_mob": master.name, "file_name": file_name, "tape": tape,
                    "scene": comments.get("Scene"), "take": comments.get("Take"),
                    "shoot_date": comments.get("Shoot Date"), "tracks": tracks,
                    "source_ranges": [{key: value for key, value in item.items() if key != "tape"}
                                      for item in ranges],
                })
    except ProductionDocumentError:
        raise
    except Exception as exc:
        raise ProductionDocumentError(f"AAF could not be read: {type(exc).__name__}: {exc}") from exc
    if not references:
        warnings.append("No recorder-take references with tape timecode were found.")
    return {"metadata": {"compositions": compositions}, "references": references, "warnings": warnings}


def aaf_checks(data, owner_digest, wav_observations):
    by_key, _ = recorder_index(wav_observations)
    checks = []
    for reference in data.get("references", []):
        candidates = by_key.get((_normalise(reference.get("tape")), _normalise(reference.get("file_name"))), [])
        if not candidates:
            continue  # the edit may use takes the submission does not include
        common = {"evidence_hash": owner_digest, "master_mob": reference.get("master_mob"),
                  "file_name": reference.get("file_name"), "tape": reference.get("tape")}
        if len(candidates) > 1:
            checks.append(_unavailable("aaf_reference_matches_one_recording",
                                       "Several submitted recordings share this tape and file name.",
                                       RECORDER_METADATA, audio_hashes=sorted(candidates), **common))
            continue
        digest = candidates[0]
        recorder = wav_observations[digest]["recorder_metadata"]
        common["audio_hash"] = digest
        start, duration = recorder.get("start_timecode_seconds"), recorder.get("duration_seconds")
        if start is not None and duration is not None:
            for item in reference.get("source_ranges", []):
                frame = 1 / item["edit_rate"]
                inside = start - frame <= item["start_seconds"] and item["end_seconds"] <= start + duration + frame
                checks.append(_check("aaf_source_range_within_recorded_timecode", inside, RECORDER_METADATA,
                                     edit_range_seconds=[item["start_seconds"], item["end_seconds"]],
                                     recorded_range_seconds=[start, start + duration],
                                     fps=item["edit_rate"], **common))
        if reference.get("shoot_date") and recorder.get("origination_date"):
            checks.append(_check("aaf_shoot_date_matches_recording",
                                 reference["shoot_date"] == recorder["origination_date"], RECORDER_METADATA,
                                 declared=reference["shoot_date"], recorded=recorder["origination_date"], **common))
        checks.extend(_identity_checks("aaf", reference, recorder, common))
    return checks
