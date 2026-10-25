"""Parent-side orchestration after the immutable core response is returned."""

import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .agreement import AGREEMENT, expected_rows
from .contracts import FILE_PROVIDERS, NON_SCORING, unavailable, validate_result
from .policy import load_policy
from .providers.alignment import merge_intervals, subtract_intervals
from .tasks import (AAF_UNUSED, DERIVED, MINIMUM_UNUSED_SECONDS, RANGE_SEARCH, SEARCH_PROVIDERS, build_tasks,
                    file_tasks, range_search_tasks, unused_range_tasks)

STDERR_TAIL_CHARACTERS = 2000
# Too little usable signal is a normal analytical outcome, not a failure to run.
INSUFFICIENT_SUPPORT_REASONS = frozenset({
    "silent_signal", "insufficient_signal", "insufficient_landmarks", "insufficient_peaks",
    "insufficient_spectral_frames", "channel_cancellation"})


# Bandwidth assessments where the content does not support what the file claims.
BAND_LIMIT_FLAGS = {"band_limit_like_lossy_encoding", "consistent_with_upsampling",
                    "content_band_lower_than_bitrate_suggests"}


def _insufficient_support(row, rows):
    """True when a row could not conclude because the audio offered too little to compare."""
    if row["status"] != "unavailable":
        return False
    if row["reason_code"] in INSUFFICIENT_SUPPORT_REASONS:
        return True
    if row["provider"] == AGREEMENT and row["reason_code"] == "provider_inputs_unavailable":
        # Agreement inherits the outcome of the fingerprint rows it needs.
        task = row["diagnostic_id"].rsplit(":", 1)[0]
        inputs = [item for item in rows if item["provider"] in ("audfprint", "spectromap")
                  and item["diagnostic_id"].rsplit(":", 1)[0] == task and item["status"] == "unavailable"]
        return bool(inputs) and all(item["reason_code"] in INSUFFICIENT_SUPPORT_REASONS for item in inputs)
    return False


def _snapshot(root, relative, digest, directory, deadline):
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Audio file no longer resolves to a regular file inside the bundle root.")
    # Keep the submitted extension; readers decide the format from content.
    target = directory / (digest + (Path(relative).suffix.lower() or ".audio"))
    hasher = hashlib.sha256()
    with path.open("rb") as source, target.open("wb") as destination:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Caller timeout exhausted while preparing audio snapshots.")
            block = source.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
            destination.write(block)
    if hasher.hexdigest() != digest:
        target.unlink()
        raise ValueError("Audio bytes changed since core hash binding; diagnostic snapshot rejected.")
    target.chmod(0o400)
    return str(target)


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run_task(task, providers, parameters, deadline, command=None):
    """Run every provider for one task in a single isolated worker process.

    Returns one validated row per provider, in ``providers`` order, plus a
    fingerprint-agreement row when both fingerprint providers run. Rows the
    worker finished before a timeout or crash are kept; only the missing
    providers become unavailable.
    """
    if deadline - time.monotonic() <= 0:
        return [unavailable(task, name, "caller_timeout", "No caller time remains for this diagnostic.")
                for name in providers]
    command = command or [sys.executable, "-B", str(Path(__file__).with_name("worker.py"))]
    environment = dict(os.environ)
    # Let cold optional-library imports work without spawning unbounded BLAS
    # threads; this is not a file, frame, duration or task-count limit.
    environment.setdefault("OPENBLAS_NUM_THREADS", "1")
    environment.setdefault("OMP_NUM_THREADS", "1")
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True,
                                   env=environment)
    except Exception as exc:
        return [unavailable(task, name, "provider_start_failed", str(exc)) for name in providers]
    lines, stderr_parts = queue.Queue(), []
    # Drain both pipes concurrently so a verbose library cannot block the
    # worker on a full stderr pipe while the parent waits for stdout.
    readers = [threading.Thread(target=lambda: ([lines.put(line) for line in process.stdout], lines.put(None)),
                                daemon=True),
               threading.Thread(target=lambda: stderr_parts.append(process.stderr.read()), daemon=True)]
    for reader in readers:
        reader.start()
    rows, failure, timed_out = {}, None, False
    expected = expected_rows(providers)
    try:
        process.stdin.write(json.dumps({"task": task, "providers": list(providers),
                                        "parameters": {name: parameters[name] for name in providers}},
                                       allow_nan=False))
        process.stdin.close()
    except OSError as exc:
        failure = exc
    while True:
        wait = deadline - time.monotonic()
        if wait <= 0:
            timed_out = True
            break
        try:
            line = lines.get(timeout=wait)
        except queue.Empty:
            timed_out = True
            break
        if line is None:  # stdout closed: the worker finished or died
            break
        try:
            value = json.loads(line)
            name = value.get("provider") if isinstance(value, dict) else None
            if name not in expected or name in rows:
                raise ValueError("Unexpected or duplicate provider result.")
            rows[name] = validate_result(value, task, name)
        except Exception as exc:
            failure = failure or exc
    if not timed_out:
        try:
            process.wait(timeout=max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired:
            timed_out = True
    if process.poll() is None:
        _kill(process)
    process.wait()
    for reader in readers:
        reader.join(timeout=5)
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            stream.close()
        except OSError:
            pass
    stderr = "".join(stderr_parts)
    if failure is None and process.returncode and not timed_out:
        failure = ValueError("Provider process exited with code " + str(process.returncode))
    result = []
    for name in expected:
        if name in rows:
            result.append(rows[name])
        elif timed_out:
            result.append(unavailable(task, name, "caller_timeout",
                                      "Provider exceeded the caller's remaining timeout; core scores were retained."))
        else:
            result.append(unavailable(task, name, "invalid_provider_output",
                                      str(failure or "Provider process ended without a result.") + _stderr_tail(stderr)))
    return result


def _stderr_tail(stderr):
    # Keep the provider's own error text so a crash is diagnosable, bounded
    # so a noisy library cannot flood the response.
    text = (stderr or "").strip()
    if not text:
        return ""
    return " Provider stderr (tail): " + text[-STDERR_TAIL_CHARACTERS:]


CALIBRATION_STATUS_PATH = Path(__file__).resolve().parents[2] / "policies" / "audio_calibration_status.json"


def _calibration_status():
    """Per-provider calibration evidence; providers without a record are exploratory."""
    try:
        raw = CALIBRATION_STATUS_PATH.read_bytes()
        value = json.loads(raw)
        return value, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError):
        return {"providers": {}}, None


def _calibration(provider, status):
    record = (status.get("providers") or {}).get(provider)
    if record is None:
        return {"status": "exploratory_uncalibrated", "summary": "No calibration record for this provider.",
                "sample": None, "case_counts": None, "transforms_tested": [], "not_tested": ["all"],
                "false_match_rates": None}
    return {"profile_id": status.get("profile_id"), **copy.deepcopy(record)}


def _formats(response):
    return {row["hash"]: row["format"]
            for row in (response.get("assessment_checks") or {}).get("format_capabilities") or []}


def _format_scope(row, formats):
    """Thresholds were calibrated on WAV; say so on every row that reads FLAC or MP3."""
    endpoints = [formats.get(row.get("source_hash")), formats.get(row.get("target_hash"))]
    if row.get("source_hash") == row.get("target_hash"):
        endpoints = endpoints[:1]
    other = sorted({name for name in endpoints if name and name != "WAV"})
    if not other:
        return {"format_scope": "wav"}
    return {"format_scope": "calibrated_on_wav_only", "endpoint_formats": endpoints,
            "format_scope_note": f"Thresholds were calibrated on WAV only; this row reads {', '.join(other)}, "
                                 "for which they are not calibrated (a limitation of this program)."}


def _decoder_versions():
    """The FLAC/MP3 decoder in use; results may depend on its version."""
    try:
        import soundfile
    except ImportError:
        return None
    return {"soundfile": soundfile.__version__, "libsndfile": soundfile.__libsndfile_version__}


# A format conversion keeps the timeline, so its audio should line up at 0 s.
CONVERSION_OFFSET_TOLERANCE_SECONDS = 0.1


def _conversion_corroboration(response, rows):
    """For each declared format conversion: whole-file fingerprint offset and target bandwidth.

    Report-only context beside the core checks of ``format_conversions``.
    """
    out = []
    for conversion in (response.get("assessment_checks") or {}).get("format_conversions") or []:
        if conversion.get("status") != "assessed":
            continue
        source, target = conversion["source_hash"], conversion["target_hash"]
        fingerprint = [row for row in rows if row["provider"] == "audfprint" and row.get("range_source") == "whole_file"
                       and (row["source_hash"], row["target_hash"]) == (source, target)]
        offsets = sorted({round(match["source_minus_target_offset_seconds"], 3) for row in fingerprint
                          for match in row["matches"]
                          if (match.get("support_assessment") or {}).get("status") == "supported_within_profile"})
        aligned = any(abs(offset) <= CONVERSION_OFFSET_TOLERANCE_SECONDS for offset in offsets)
        bandwidth = next((row for row in rows if row["provider"] == "bandwidth" and row["source_hash"] == target
                          and row["status"] != "unavailable"), None)
        out.append({
            "source_hash": source, "target_hash": target, "direction": conversion.get("direction"),
            "fingerprint": ("not_run" if not fingerprint else
                            "aligned_at_zero" if aligned else "no_supported_alignment_at_zero"),
            "supported_offsets_seconds": offsets,
            "target_bandwidth": ((bandwidth.get("metrics") or {}).get("assessment") or {}).get("status")
                                if bandwidth else None,
            "scoring_eligible": False, "integrity_contribution": None})
    return out


# Declared seconds are often rounded; an offset this close is the declared alignment.
DECLARED_OFFSET_TOLERANCE_SECONDS = 0.25
# A window whose source and target lengths differ declares a speed change,
# which fingerprints cannot align; such windows are not assessed.
DURATION_TOLERANCE_SECONDS = 0.05
NOT_SUPPORTED_OUTCOMES = {"content_found_elsewhere", "content_not_located", "search_unavailable"}


def _supported(match):
    # Only audfprint has a calibrated support profile; SpectroMap is context.
    return (match.get("support_assessment") or {}).get("status") == "supported_within_profile"


def _best_offset(row):
    if not row or not row["analysis_complete"] or not row["matches"]:
        return None
    return round(row["matches"][0]["source_minus_target_offset_seconds"], 3)


def _has_offset(row, offset):
    # Repeated material gives several valid offsets; any candidate near ours counts.
    return any(abs(match["source_minus_target_offset_seconds"] - offset) <= DECLARED_OFFSET_TOLERANCE_SECONDS
               for match in row["matches"])


def _declared_range_assessments(rows, search_rows):
    """Does the audio support the declared source window, and if not, where is the content?

    Compares the calibrated audfprint result inside the declared window with a
    whole-source search for the declared target window. Report-only.
    """
    declared, searched = {}, {}
    for row in rows:
        if row["provider"] in SEARCH_PROVIDERS and row.get("range_source") in ("declared", DERIVED):
            declared.setdefault(row["diagnostic_id"].rsplit(":", 1)[0], {})[row["provider"]] = row
    for row in search_rows:
        searched.setdefault(row["diagnostic_id"].rsplit(":", 2)[0], {})[row["provider"]] = row
    out = []
    for task_id, search in searched.items():
        window = declared.get(task_id, {})
        audfprint = window.get("audfprint")
        if audfprint is None:
            continue
        item = {"source_hash": audfprint["source_hash"], "target_hash": audfprint["target_hash"],
                "relationship_type": audfprint["relationship_type"], "selection_id": audfprint.get("selection_id"),
                "range_source": audfprint.get("range_source"),
                "diagnostic_ids": sorted(row["diagnostic_id"] for row in [*window.values(), *search.values()]),
                "offset_tolerance_seconds": DECLARED_OFFSET_TOLERANCE_SECONDS, **NON_SCORING}
        out.append(item)
        if not audfprint["analysis_complete"]:
            item["outcome"] = ("insufficient_support" if _insufficient_support(audfprint, rows) else "not_assessed")
            continue
        source, target = audfprint["analysed_ranges"]["source"], audfprint["analysed_ranges"]["target"]
        expected = source[0] - target[0]
        item.update(declared_source_range_seconds=source, declared_target_range_seconds=target,
                    declared_offset_seconds=round(expected, 3))
        if abs((source[1] - source[0]) - (target[1] - target[0])) > DURATION_TOLERANCE_SECONDS:
            item["outcome"] = "not_assessed_durations_differ"
            continue
        fractions = [match["source_supported_fraction"] for match in audfprint["matches"]
                     if abs(match["source_minus_target_offset_seconds"] - expected) <= DECLARED_OFFSET_TOLERANCE_SECONDS]
        item["declared_window_supported_fraction"] = round(max(fractions, default=0.0), 3)
        item["declared_window_support"] = ("supported_within_profile" if any(
            _supported(match) and abs(match["source_minus_target_offset_seconds"] - expected)
            <= DECLARED_OFFSET_TOLERANCE_SECONDS for match in audfprint["matches"]) else "not_supported")
        item["spectromap_declared_best_offset_seconds"] = _best_offset(window.get("spectromap"))
        if item["declared_window_support"] == "supported_within_profile":
            item["outcome"] = "declared_range_supported"
            continue
        whole = search.get("audfprint")
        if not whole or not whole["analysis_complete"]:
            item["outcome"] = "search_unavailable"
            continue
        supported = sorted((match for match in whole["matches"] if _supported(match)),
                           key=lambda match: (-match["target_supported_fraction"], -match["matched_token_pairs"]))
        offsets = [round(match["source_minus_target_offset_seconds"], 3) for match in supported]
        item["search_supported_offsets_seconds"] = offsets
        spectromap = search.get("spectromap")
        if not (spectromap and spectromap["analysis_complete"]):
            spectromap = None
        item["spectromap_search_best_offset_seconds"] = _best_offset(spectromap)
        if any(abs(offset - expected) <= DECLARED_OFFSET_TOLERANCE_SECONDS for offset in offsets):
            item["outcome"] = "declared_range_supported"
        elif offsets:
            best = supported[0]
            item.update(outcome="content_found_elsewhere", best_offset_seconds=offsets[0],
                        best_source_range_seconds=[round(offsets[0] + target[0], 3), round(offsets[0] + target[1], 3)],
                        best_target_supported_fraction=round(best["target_supported_fraction"], 3),
                        spectromap_agrees=None if spectromap is None else _has_offset(spectromap, offsets[0]))
        else:
            item["outcome"] = "content_not_located"
    return out


def _declared_range_finding(item):
    source = item["declared_source_range_seconds"]
    head =(f"The declared source range {source[0]:.2f}-{source[1]:.2f} s is not supported by the audio: audfprint "
            f"supports {item['declared_window_supported_fraction']:.0%} of it at the declared offset. ")
    if item["outcome"] == "content_found_elsewhere":
        best = item["best_source_range_seconds"]
        others = len(item["search_supported_offsets_seconds"]) - 1
        body = (f"The target's content was found at source {best[0]:.2f}-{best[1]:.2f} s instead "
                f"({item['best_target_supported_fraction']:.0%} of the target window supported" +
                (f"; {others} other supported offset(s), as with repeated material" if others else "") +
                ("; SpectroMap agrees" if item.get("spectromap_agrees") else "") + "). ")
        severity = "low"
    elif item["outcome"] == "content_not_located":
        body = ("audfprint did not locate the target's content anywhere in the source. Processing such as "
                "time-stretching or heavy effects can prevent a match, so this is not a contradiction. ")
        severity = "info"
    else:
        body = "A whole-source search for the target's content could not be completed. "
        severity = "info"
    return {"code": "AUDIO_DECLARED_RANGE_NOT_SUPPORTED", "severity": severity,
            "message": head + body + "This is a report-only content observation, not provenance; it does not "
                                     "change any score.",
            "outcome": item["outcome"], "source_hash": item["source_hash"], "target_hash": item["target_hash"],
            "relationship_type": item["relationship_type"], "selection_id": item["selection_id"],
            "declared_source_range_seconds": source, "best_offset_seconds": item.get("best_offset_seconds"),
            "best_source_range_seconds": item.get("best_source_range_seconds"),
            "diagnostic_ids": item["diagnostic_ids"], "detail_key": "audio_declared_range", **NON_SCORING}


def _seconds(intervals):
    return round(sum(end - start for start, end in intervals), 3)


def _unused_range_assessments(unused_rows, controls):
    """Per AAF-used take: is the take content the edit did not use absent from the target?

    Absence only means something when the same comparison found this take's
    used seconds in the target (the positive control); otherwise the take is
    not assessed. Every candidate counts as found, so seconds 'beyond the
    final' are counted conservatively.
    """
    pieces = {}
    for row in unused_rows:
        if row["provider"] in SEARCH_PROVIDERS:
            pieces.setdefault(row["diagnostic_id"].rsplit(":", 1)[0], []).append(row)
    takes = {}
    for task_rows in pieces.values():
        first = task_rows[0]
        span = next((row["analysed_ranges"]["source"] for row in task_rows if row["analysis_complete"]), None)
        candidates = [(row["provider"], match) for row in task_rows if row["analysis_complete"]
                      for match in row["matches"]]
        found = sorted({provider for provider, _ in candidates})
        outcome = ("found_in_target" if found else
                   "not_found" if span is not None else "not_analysable")
        strongest = max(candidates, key=lambda item: item[1]["matched_token_pairs"], default=None)
        takes.setdefault((first["source_hash"], first["target_hash"]), []).append(
            {"selection_id": first.get("selection_id"), "source_range_seconds": span, "outcome": outcome,
             "providers_found": found,
             "strongest_candidate": None if strongest is None else {
                 "provider": strongest[0], "matched_token_pairs": strongest[1]["matched_token_pairs"],
                 "supported_seconds": round(strongest[1]["source_supported_window_duration_seconds"], 3),
                 "source_minus_target_offset_seconds": round(strongest[1]["source_minus_target_offset_seconds"], 3)},
             "diagnostic_ids": sorted(row["diagnostic_id"] for row in task_rows)})
    out = []
    for (source, target), items in sorted(takes.items()):
        items.sort(key=lambda item: item["selection_id"] or "")
        seconds = {outcome: _seconds([item["source_range_seconds"] for item in items if item["outcome"] == outcome])
                   for outcome in ("found_in_target", "not_found")}
        control = (source, target) in controls
        if not control:
            outcome = "no_positive_control"
        elif seconds["not_found"] > 0:
            outcome = "content_beyond_final"
        elif seconds["found_in_target"] > 0:
            outcome = "unused_content_found_in_final"
        else:
            outcome = "not_analysable"
        out.append({"source_hash": source, "target_hash": target, "positive_control": control,
                    "outcome": outcome, "unused_seconds_not_found": seconds["not_found"],
                    "unused_seconds_found": seconds["found_in_target"], "pieces": items, **NON_SCORING})
    return out


def _beyond_final_finding(take):
    return {"code": "AUDIO_TAKE_CONTENT_BEYOND_FINAL", "severity": "info",
            "message": (f"{take['unused_seconds_not_found']:.1f} s of this take lie outside the AAF-used ranges and "
                        f"were not found in the target ({take['unused_seconds_found']:.1f} s of unused material were "
                        "found). The take's used ranges were found there, so the comparison could see this take's "
                        "content in the target. A stem separated from the final cannot hold material the final "
                        "lacks. This is a report-only content observation, not provenance; it does not change any "
                        "score."),
            "outcome": take["outcome"], "source_hash": take["source_hash"], "target_hash": take["target_hash"],
            "unused_seconds_not_found": take["unused_seconds_not_found"],
            "unused_seconds_found": take["unused_seconds_found"],
            "detail_key": "audio_take_beyond_final", **NON_SCORING}


def _unused_found_finding(take, piece):
    span, best = piece["source_range_seconds"], piece["strongest_candidate"]
    return {"code": "AUDIO_UNUSED_RANGE_FOUND_IN_FINAL", "severity": "info",
            "message": (f"Take content at {span[0]:.2f}-{span[1]:.2f} s, which the AAF does not list as used, has a "
                        f"candidate in the target ({best['provider']}: {best['matched_token_pairs']} matched pairs over "
                        f"{best['supported_seconds']:.1f} s). A weak candidate can be chance; a strong one can be "
                        "another clip, repeated material or an AAF that does not list every use. It is not a "
                        "contradiction and does not change any score."),
            "strongest_candidate": best,
            "source_hash": take["source_hash"], "target_hash": take["target_hash"],
            "selection_id": piece["selection_id"], "source_range_seconds": span,
            "providers": piece["providers_found"], "diagnostic_ids": piece["diagnostic_ids"],
            "detail_key": "audio_take_beyond_final", **NON_SCORING}


def _coverage_finding(item):
    return {"code": "AUDIO_SOURCE_CONTENT_BEYOND_TARGET", "severity": "info",
            "message": (f"{item['beyond_target_seconds']:.1f} s of this source's content "
                        f"({item['beyond_target_fraction']:.0%} of {item['source_content_seconds']:.1f} s with "
                        "fingerprint landmarks) were not found in the target, while other parts were. Material the "
                        "target lacks cannot have been separated from it. A stem rendered for this mix is expected "
                        "to be fully contained, so containment is not evidence against a stem. Report-only; no "
                        "score changes."),
            "outcome": item["outcome"], "source_hash": item["source_hash"], "target_hash": item["target_hash"],
            "relationship_type": item["relationship_type"], "beyond_target_seconds": item["beyond_target_seconds"],
            "beyond_target_windows_seconds": item["beyond_target_windows_seconds"],
            "diagnostic_ids": [item["diagnostic_id"]], "detail_key": "audio_source_coverage", **NON_SCORING}


# Relationships where the source is a part the target was built from.
COVERAGE_RELATIONSHIPS = {"mixed_from", "stemmed_from", "edited_from", "comped_from", "derived_from"}


def _bridge(windows, gap):
    """Join windows separated by less than ``gap`` seconds."""
    out = []
    for start, end in merge_intervals(windows):
        if out and start - out[-1][1] < gap:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


# Uncovered content shorter than this is too little to report.
MINIMUM_BEYOND_CONTENT_SECONDS = 2.0


def _source_coverage(rows):
    """Without an AAF: which stretches of the source have content but no match in the target?

    Uses whole-file audfprint rows. Landmarks are picked differently in a
    short file and in a long one, so even an exact copy matches only part of
    its landmarks; coverage is therefore judged by region. Matched windows of
    one candidate closer than MINIMUM_UNUSED_SECONDS are joined, and only
    uncovered stretches at least that long count. Silence carries no
    landmarks and is never counted. Every candidate counts as found, so the
    figure errs low.
    """
    out = []
    for row in rows:
        if not (row["provider"] == "audfprint" and row.get("range_source") == "whole_file"
                and row["relationship_type"] in COVERAGE_RELATIONSHIPS and row["analysis_complete"]):
            continue
        content = row["metrics"].get("source_content_windows_seconds")
        if content is None:
            continue
        found = merge_intervals(window for match in row["matches"]
                                for window in _bridge(match["source_supported_windows_seconds"],
                                                      MINIMUM_UNUSED_SECONDS))
        uncovered = [piece for piece in subtract_intervals([row["analysed_ranges"]["source"]], found)
                     if piece[1] - piece[0] >= MINIMUM_UNUSED_SECONDS]
        beyond = subtract_intervals(content, subtract_intervals(content, uncovered))
        regions = [[round(a, 3), round(b, 3)] for a, b in uncovered
                   if any(start < b and end > a for start, end in beyond)]
        control = any(_supported(match) for match in row["matches"])
        total, beyond_seconds = _seconds(content), _seconds(beyond)
        if not control:
            outcome = "no_positive_control"
        elif beyond_seconds >= MINIMUM_BEYOND_CONTENT_SECONDS:
            outcome = "content_beyond_target"
        else:
            outcome = "contained_in_target"
        out.append({"source_hash": row["source_hash"], "target_hash": row["target_hash"],
                    "relationship_type": row["relationship_type"], "positive_control": control,
                    "outcome": outcome, "source_content_seconds": total,
                    "found_in_target_seconds": _seconds(subtract_intervals(content, subtract_intervals(content, found))),
                    "beyond_target_seconds": beyond_seconds,
                    "beyond_target_fraction": round(beyond_seconds / total, 3) if total else None,
                    "beyond_target_windows_seconds": regions,
                    "minimum_region_seconds": MINIMUM_UNUSED_SECONDS,
                    "minimum_beyond_content_seconds": MINIMUM_BEYOND_CONTENT_SECONDS,
                    "diagnostic_id": row["diagnostic_id"], **NON_SCORING})
    return out


def _attach(response, rows, summary):
    # Copy-on-append: do not feed any observations back through core assessors.
    combined = copy.deepcopy(response)
    status, status_sha256 = _calibration_status()
    # Every row states how far its provider has been calibrated, beside the result.
    rows = [{**row, "calibration": {**_calibration(row["provider"], status),
                                    **_format_scope(row, _formats(response))}} for row in rows]
    # Whole-source searches only serve the declared-range assessment below.
    search_rows = [row for row in rows if row.get("range_source") == RANGE_SEARCH]
    # Unused-take comparisons likewise serve only the take assessment below.
    unused_rows = [row for row in rows if row.get("range_source") == AAF_UNUSED]
    rows = [row for row in rows if row.get("range_source") not in (RANGE_SEARCH, AAF_UNUSED)]
    summary = {**summary, "calibration_status_sha256": status_sha256, "decoders": _decoder_versions()}
    checks = combined.setdefault("assessment_checks", {})
    checks["audio_fingerprint"] = [row for row in rows if row["provider"] in ("audfprint", "spectromap")]
    checks["audio_structure"] = [row for row in rows if row["provider"] == "librosa"]
    checks["audio_fingerprint_agreement"] = [row for row in rows if row["provider"] == AGREEMENT]
    checks["audio_semantics"] = [row for row in rows if row["provider"] == "music_semantics"]
    checks["audio_bandwidth"] = [row for row in rows if row["provider"] == "bandwidth"]
    checks["audio_diagnostic_summary"] = [summary]
    corroboration = _conversion_corroboration(response, rows)
    if corroboration:
        checks["audio_format_conversion"] = corroboration
    detail_keys = {"librosa": "audio_structure", AGREEMENT: "audio_fingerprint_agreement",
                   "music_semantics": "audio_semantics", "bandwidth": "audio_bandwidth"}
    observation_codes = {"librosa": "AUDIO_STRUCTURE_OBSERVATION",
                         AGREEMENT: "AUDIO_FINGERPRINT_AGREEMENT_OBSERVATION",
                         "music_semantics": "AUDIO_SEMANTIC_OBSERVATION",
                         "bandwidth": "AUDIO_BANDWIDTH_OBSERVATION"}
    for row in rows:
        if _insufficient_support(row, rows):
            code, outcome = "AUDIO_INSUFFICIENT_SUPPORT", "insufficient_support"
            message = (row["provider"] + ": insufficient support for " + row["relationship_type"] + ". " +
                       row["message"] + " This is a normal outcome, not an error.")
        else:
            if row["status"] == "unavailable":
                code, outcome = "AUDIO_DIAGNOSTIC_UNAVAILABLE", "not_run"
            else:
                code = observation_codes.get(row["provider"], "AUDIO_FINGERPRINT_OBSERVATION")
                outcome = "completed"
            counted = {AGREEMENT: " offset(s) where both fingerprint algorithms voted, for ",
                       "music_semantics": " aligned phrase(s) for "}.get(row["provider"], " content candidate(s) for ")
            message = (row["provider"] + ": " + str(len(row["matches"])) + counted +
                       row["relationship_type"] + ". " + row["message"])
            if row["provider"] == "bandwidth" and row["status"] != "unavailable":
                edge = row["metrics"].get("content_edge_hz")
                message = ("bandwidth: " + (f"audio content stops at {edge / 1000:.1f} kHz. " if edge else
                                            "no sharp band limit found. ") + row["message"])
        combined["findings"].append({"code": code, "severity": "info", "message": message, "outcome": outcome,
                                     "diagnostic_id": row["diagnostic_id"], "provider": row["provider"],
                                     "source_hash": row["source_hash"], "target_hash": row["target_hash"],
                                     "status": row["status"], "reason_code": row["reason_code"],
                                     "candidate_count": len(row["matches"]),
                                     "analysis_complete": row["analysis_complete"],
                                     "relationship_type": row["relationship_type"],
                                     "detail_key": detail_keys.get(row["provider"], "audio_fingerprint"),
                                     **NON_SCORING})
        if row["provider"] == "bandwidth" and row["status"] != "unavailable":
            assessment = row["metrics"].get("assessment") or {}
            if assessment.get("status") in BAND_LIMIT_FLAGS:
                # A claim the content does not support (lossless, hi-res or a
                # bitrate). Reported for review; never scored.
                combined["findings"].append({
                    "code": "AUDIO_CONTENT_BAND_LIMITED", "severity": "low",
                    "message": assessment["reason"] + " This is a heuristic content observation, not a verdict, "
                               "and it does not change any score.",
                    "evidence_hash": row["source_hash"], "diagnostic_id": row["diagnostic_id"],
                    "assessment": assessment["status"], "content_edge_hz": row["metrics"].get("content_edge_hz"),
                    "detail_key": "audio_bandwidth", **NON_SCORING})
    # An AAF-range task compares only the seconds the edit says it used, so a
    # fingerprint match there is content agreeing with the AAF declaration.
    # Absence is not reported as disagreement: weak dialogue can be missed.
    aaf_rows = [row for row in rows if row.get("range_source") == "aaf_source_range"
                and row["provider"] in ("audfprint", "spectromap") and row["analysis_complete"]]
    corresponded = {}
    for row in aaf_rows:
        if row["matches"]:
            corresponded.setdefault((row["source_hash"], row["target_hash"], row.get("selection_id")), []).append(row)
    summary["aaf_range_tasks"] = len({(row["source_hash"], row["target_hash"], row.get("selection_id"))
                                      for row in aaf_rows})
    summary["aaf_range_tasks_with_correspondence"] = len(corresponded)
    if search_rows:
        # A declared window the audio does not support is named; the core
        # score is untouched until a client-agreed accuracy bar exists.
        assessments = _declared_range_assessments(rows, search_rows)
        checks["audio_declared_range_search"] = search_rows
        checks["audio_declared_range"] = assessments
        summary["declared_ranges_assessed"] = len(assessments)
        summary["declared_ranges_not_supported"] = sum(item["outcome"] in NOT_SUPPORTED_OUTCOMES
                                                       for item in assessments)
        combined["findings"].extend(_declared_range_finding(item) for item in assessments
                                    if item["outcome"] in NOT_SUPPORTED_OUTCOMES)
    if unused_rows:
        takes = _unused_range_assessments(unused_rows, {(source, target) for source, target, _ in corresponded})
        checks["audio_aaf_unused_range"] = unused_rows
        checks["audio_take_beyond_final"] = takes
        summary["aaf_unused_range_tasks"] = len({row["diagnostic_id"].rsplit(":", 1)[0] for row in unused_rows})
        summary["takes_with_content_beyond_final"] = sum(take["outcome"] == "content_beyond_final" for take in takes)
        for take in takes:
            if take["outcome"] == "content_beyond_final":
                combined["findings"].append(_beyond_final_finding(take))
            for piece in take["pieces"]:
                if piece["outcome"] == "found_in_target":
                    combined["findings"].append(_unused_found_finding(take, piece))
    coverage = _source_coverage(rows)
    if coverage:
        checks["audio_source_coverage"] = coverage
        summary["sources_with_content_beyond_target"] = sum(item["outcome"] == "content_beyond_target"
                                                           for item in coverage)
        combined["findings"].extend(_coverage_finding(item) for item in coverage
                                    if item["outcome"] == "content_beyond_target")
    checks["audio_diagnostic_summary"] = [summary]
    for (source, target, selection), matched in corresponded.items():
        span = matched[0]["analysed_ranges"].get("source")
        combined["findings"].append({
            "code": "AUDIO_AAF_RANGE_CORRESPONDENCE", "severity": "info",
            "message": (f"Content from the AAF-declared source range {span[0]:.2f}-{span[1]:.2f} s was found in the "
                        f"target by {', '.join(sorted(row['provider'] for row in matched))}. This agrees with the AAF "
                        "edit but is a content observation, not provenance; it does not change any score."),
            "selection_id": selection, "source_hash": source, "target_hash": target,
            "source_range_seconds": span, "providers": sorted(row["provider"] for row in matched),
            "diagnostic_ids": sorted(row["diagnostic_id"] for row in matched),
            "detail_key": "audio_fingerprint", **NON_SCORING})
    if summary.get("reason_code"):
        combined["findings"].append({"code": "AUDIO_DIAGNOSTIC_UNAVAILABLE", "severity": "info",
                                     "message": summary["message"], **NON_SCORING})
    elif not summary["task_count"]:
        # Analysis ran but the graph offers nothing to compare; say so rather
        # than leaving the report silently empty.
        combined["findings"].append({"code": "AUDIO_DIAGNOSTIC_NOT_APPLICABLE", "severity": "info",
                                     "message": "No declared audio-to-audio relationships were retained for "
                                                "analysis, so no content comparisons were made"
                                                + ("; each file's bandwidth was still measured." if
                                                   summary.get("file_task_count") else
                                                   ", and audio diagnostics produced no observations."),
                                     "detail_key": "audio_diagnostic_summary", **NON_SCORING})
    return combined


def augment_response(response, request, root, mode, policy_path, deadline):
    started = time.monotonic()
    summary = {"enabled": True, "no_score_effect": True, **NON_SCORING,
               "providers": [], "relationship_count": 0, "task_count": 0,
               "completed_provider_tasks": 0, "unavailable_provider_tasks": 0}
    rows = []
    try:
        policy = load_policy(policy_path)
        summary.update({key: policy[key] for key in ("policy_id", "policy_version", "policy_sha256", "providers")})
        if response["execution_status"] != "succeeded":
            summary.update(reason_code="core_assessment_unavailable", message="Audio diagnostics require a successful core assessment.")
        else:
            from music_target.contract import to_native
            native, _ = to_native(copy.deepcopy(request), mode, include_report=True)
            # Pair providers compare declared edges; file providers describe each bound file once.
            pair_providers = [name for name in policy["providers"] if name not in FILE_PROVIDERS]
            file_providers = [name for name in policy["providers"] if name in FILE_PROVIDERS]
            tasks = build_tasks(native, response) if pair_providers else []
            files = file_tasks(native, response) if file_providers else []
            search_providers = [name for name in SEARCH_PROVIDERS if name in pair_providers]
            searches = range_search_tasks(tasks) if "audfprint" in search_providers else []
            searches += unused_range_tasks(tasks, response) if search_providers else []
            summary.update(task_count=len(tasks), relationship_count=len({row["relation_id"] for row in tasks}),
                           file_task_count=len(files),
                           declared_range_search_task_count=sum(item["range_source"] == RANGE_SEARCH
                                                                for item in searches))
            root = Path(root).resolve(strict=True)
            snapshots = {}
            failures = {}
            with tempfile.TemporaryDirectory(prefix="music-audio-observations-") as directory:
                for task, providers in ([(item, pair_providers) for item in tasks] +
                                        [(item, search_providers) for item in searches] +
                                        [(item, file_providers) for item in files]):
                    task = dict(task)
                    problem = task.get("unavailable_reason")
                    for role in ("source", "target"):
                        if problem:
                            break
                        digest = task[role + "_hash"]
                        if digest not in snapshots and digest not in failures:
                            try:
                                snapshots[digest] = _snapshot(root, request["objects"][digest], digest,
                                                              Path(directory), deadline)
                            except TimeoutError:
                                failures[digest] = "caller_timeout"
                            except Exception:
                                failures[digest] = "input_changed_since_core"
                        problem = failures.get(digest)
                        task[role + "_path"] = snapshots.get(digest)
                    if problem:
                        values = [unavailable(task, provider, problem, "Diagnostic unavailable: " + problem + "; no core score was changed.")
                                  for provider in providers]
                    else:
                        values = _run_task(task, providers, policy["parameters"], deadline)
                    for value in values:
                        value.update({key: policy[key] for key in ("policy_id", "policy_version", "policy_sha256")})
                        rows.append(value)
    except Exception as exc:
        summary.update(reason_code="diagnostic_configuration_or_preparation_failed",
                       message="Audio diagnostics were not completed: " + str(exc) + "; core results were retained.")
    summary["completed_provider_tasks"] = sum(row["analysis_complete"] for row in rows)
    summary["unavailable_provider_tasks"] = sum(row["status"] == "unavailable" for row in rows)
    summary["processing_ms"] = (time.monotonic() - started) * 1000
    return _attach(response, rows, summary)
