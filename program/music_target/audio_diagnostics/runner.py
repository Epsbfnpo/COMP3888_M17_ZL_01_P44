"""Parent-side orchestration after the immutable core response is returned."""

import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from .contracts import NON_SCORING, unavailable, validate_result
from .policy import load_policy
from .tasks import build_tasks

STDERR_TAIL_CHARACTERS = 2000


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


def _run_task(task, provider, parameters, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return unavailable(task, provider, "caller_timeout", "No caller time remains for this diagnostic.")
    command = [sys.executable, "-B", str(Path(__file__).with_name("worker.py"))]
    environment = dict(os.environ)
    # Let cold optional-library imports work without spawning unbounded BLAS
    # threads; this is not a file, frame, duration or task-count limit.
    environment.setdefault("OPENBLAS_NUM_THREADS", "1")
    environment.setdefault("OMP_NUM_THREADS", "1")
    process, stderr = None, ""
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True,
                                   env=environment)
        stdout, stderr = process.communicate(json.dumps({"task": task, "provider": provider,
                                                        "parameters": parameters}, allow_nan=False),
                                              timeout=remaining)
        if process.returncode:
            raise ValueError("Provider process exited with code " + str(process.returncode))
        value = json.loads(stdout)
        return validate_result(value, task, provider)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return unavailable(task, provider, "caller_timeout", "Provider exceeded the caller's remaining timeout; core scores were retained.")
    except Exception as exc:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        reason = "provider_start_failed" if process is None else "invalid_provider_output"
        return unavailable(task, provider, reason, str(exc) + _stderr_tail(stderr))


def _stderr_tail(stderr):
    # Keep the provider's own error text so a crash is diagnosable, bounded
    # so a noisy library cannot flood the response.
    text = (stderr or "").strip()
    if not text:
        return ""
    return " Provider stderr (tail): " + text[-STDERR_TAIL_CHARACTERS:]


def _attach(response, rows, summary):
    # Copy-on-append: do not feed any observations back through core assessors.
    combined = copy.deepcopy(response)
    checks = combined.setdefault("assessment_checks", {})
    checks["audio_fingerprint"] = [row for row in rows if row["provider"] != "librosa"]
    checks["audio_structure"] = [row for row in rows if row["provider"] == "librosa"]
    checks["audio_diagnostic_summary"] = [summary]
    for row in rows:
        code = ("AUDIO_DIAGNOSTIC_UNAVAILABLE" if row["status"] == "unavailable" else
                "AUDIO_STRUCTURE_OBSERVATION" if row["provider"] == "librosa" else
                "AUDIO_FINGERPRINT_OBSERVATION")
        message = (row["provider"] + ": " + str(len(row["matches"])) +
                   " content candidate(s) for " + row["relationship_type"] + ". " + row["message"])
        combined["findings"].append({"code": code, "severity": "info", "message": message,
                                     "diagnostic_id": row["diagnostic_id"], "provider": row["provider"],
                                     "source_hash": row["source_hash"], "target_hash": row["target_hash"],
                                     "status": row["status"], "reason_code": row["reason_code"],
                                     "candidate_count": len(row["matches"]),
                                     "analysis_complete": row["analysis_complete"],
                                     "relationship_type": row["relationship_type"],
                                     "detail_key": "audio_structure" if row["provider"] == "librosa" else "audio_fingerprint",
                                     **NON_SCORING})
    if summary.get("reason_code"):
        combined["findings"].append({"code": "AUDIO_DIAGNOSTIC_UNAVAILABLE", "severity": "info",
                                     "message": summary["message"], **NON_SCORING})
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
                    for provider in policy["providers"]:
                        if problem:
                            value = unavailable(task, provider, problem, "Diagnostic unavailable: " + problem + "; no core score was changed.")
                        else:
                            value = _run_task(task, provider, policy["parameters"][provider], deadline)
                        value.update({key: policy[key] for key in ("policy_id", "policy_version", "policy_sha256")})
                        rows.append(value)
    except Exception as exc:
        summary.update(reason_code="diagnostic_configuration_or_preparation_failed",
                       message="Audio diagnostics were not completed: " + str(exc) + "; core results were retained.")
    summary["completed_provider_tasks"] = sum(row["analysis_complete"] for row in rows)
    summary["unavailable_provider_tasks"] = sum(row["status"] == "unavailable" for row in rows)
    summary["processing_ms"] = (time.monotonic() - started) * 1000
    return _attach(response, rows, summary)
