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
from .contracts import NON_SCORING, unavailable, validate_result
from .policy import load_policy
from .tasks import build_tasks

STDERR_TAIL_CHARACTERS = 2000
# Too little usable signal is a normal analytical outcome, not a failure to run.
INSUFFICIENT_SUPPORT_REASONS = frozenset({
    "silent_signal", "insufficient_signal", "insufficient_landmarks", "insufficient_peaks",
    "insufficient_spectral_frames", "channel_cancellation"})


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
    target = directory / (digest + ".wav")
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
                "sample": None, "transforms_tested": [], "not_tested": ["all"], "false_match_rates": None}
    return {"profile_id": status.get("profile_id"), **copy.deepcopy(record)}


def _attach(response, rows, summary):
    # Copy-on-append: do not feed any observations back through core assessors.
    combined = copy.deepcopy(response)
    status, status_sha256 = _calibration_status()
    # Every row states how far its provider has been calibrated, beside the result.
    rows = [{**row, "calibration": _calibration(row["provider"], status)} for row in rows]
    summary = {**summary, "calibration_status_sha256": status_sha256}
    checks = combined.setdefault("assessment_checks", {})
    checks["audio_fingerprint"] = [row for row in rows if row["provider"] in ("audfprint", "spectromap")]
    checks["audio_structure"] = [row for row in rows if row["provider"] == "librosa"]
    checks["audio_fingerprint_agreement"] = [row for row in rows if row["provider"] == AGREEMENT]
    checks["audio_semantics"] = [row for row in rows if row["provider"] == "music_semantics"]
    checks["audio_diagnostic_summary"] = [summary]
    detail_keys = {"librosa": "audio_structure", AGREEMENT: "audio_fingerprint_agreement",
                   "music_semantics": "audio_semantics"}
    observation_codes = {"librosa": "AUDIO_STRUCTURE_OBSERVATION",
                         AGREEMENT: "AUDIO_FINGERPRINT_AGREEMENT_OBSERVATION",
                         "music_semantics": "AUDIO_SEMANTIC_OBSERVATION"}
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
        combined["findings"].append({"code": code, "severity": "info", "message": message, "outcome": outcome,
                                     "diagnostic_id": row["diagnostic_id"], "provider": row["provider"],
                                     "source_hash": row["source_hash"], "target_hash": row["target_hash"],
                                     "status": row["status"], "reason_code": row["reason_code"],
                                     "candidate_count": len(row["matches"]),
                                     "analysis_complete": row["analysis_complete"],
                                     "relationship_type": row["relationship_type"],
                                     "detail_key": detail_keys.get(row["provider"], "audio_fingerprint"),
                                     **NON_SCORING})
    if summary.get("reason_code"):
        combined["findings"].append({"code": "AUDIO_DIAGNOSTIC_UNAVAILABLE", "severity": "info",
                                     "message": summary["message"], **NON_SCORING})
    elif not summary["task_count"]:
        # Analysis ran but the graph offers nothing to compare; say so rather
        # than leaving the report silently empty.
        combined["findings"].append({"code": "AUDIO_DIAGNOSTIC_NOT_APPLICABLE", "severity": "info",
                                     "message": "No declared audio-to-audio relationships were retained for "
                                                "analysis, so audio diagnostics produced no observations.",
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
            tasks = build_tasks(native, response)
            summary.update(task_count=len(tasks), relationship_count=len({row["relation_id"] for row in tasks}))
            root = Path(root).resolve(strict=True)
            snapshots = {}
            failures = {}
            with tempfile.TemporaryDirectory(prefix="music-audio-observations-") as directory:
                for task in tasks:
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
                                  for provider in policy["providers"]]
                    else:
                        values = _run_task(task, policy["providers"], policy["parameters"], deadline)
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
