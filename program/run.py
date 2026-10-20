#!/usr/bin/env python3
"""One local scoring request per process. Use --help for accepted inputs."""

import argparse
import copy
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "vendor"))

from music_target.contract import (
    DEFAULT_TIMEOUT_SECONDS, INPUT_ERROR_CODES, SHA256, InputError, Unsupported,
    public_bundle_request, read_json, response_shell, source_revision, to_native,
    validate,
)
from music_target.engine import ObjectsChangedDuringEvaluation, evaluate_chain, finding
from music_target.score_explanations import build as build_score_explanations


HASH_FIELDS = ("evidence_hash", "source_hash", "target_hash", "artefact_hash")


def _sanitise_hash_fields(response):
    """Keep malformed submitted hashes out of fields the response schema types.

    Hashes are never corrected; a value that is not lower-case SHA-256 is
    reported under ``submitted_<field>`` instead (findings) or dropped
    (unknown_inputs, whose items allow no extra fields).
    """
    for item in response.get("findings", []):
        for field in HASH_FIELDS:
            value = item.get(field)
            if isinstance(value, str) and not SHA256.fullmatch(value):
                item[f"submitted_{field}"] = item.pop(field)
    for item in response.get("unknown_inputs", []):
        for field in HASH_FIELDS:
            value = item.get(field)
            if isinstance(value, str) and not SHA256.fullmatch(value):
                item.pop(field)


def _summarise_input_errors(response):
    errors = [item for item in response["findings"] if item["code"] in INPUT_ERROR_CODES]
    if not errors:
        return
    response["findings"].append(finding(
        "SCORE_AFFECTED_BY_INPUT_ERRORS",
        f"{len(errors)} input error(s) made the checks that depend on them unavailable; "
        "nothing was corrected, and scores may change once the input is fixed.",
        severity="medium"))
    note = (f" {len(errors)} input error(s) made dependent checks unavailable; "
            "see SCORE_AFFECTED_BY_INPUT_ERRORS.")
    for assessment in response["axes"].values():
        if assessment["availability"] == "available":
            assessment["availability"] = "partial"
        assessment["reasoning"] = (assessment.get("reasoning") or "") + note


def _evaluate_with_change_retry(native, request, root, input_report, deadline,
                                allow_local_files):
    """Evaluate once; if non-final files changed meanwhile, re-run once with them isolated."""
    changed = {}
    for attempt in range(2):
        try:
            return evaluate_chain(
                copy.deepcopy(native), request["objects"], root, request.get("workflow"),
                allow_disconnected=not input_report["coverage"]["complete"],
                deadline=deadline, changed_objects=changed,
                allow_external_objects=allow_local_files)
        except ObjectsChangedDuringEvaluation as exc:
            if attempt:
                problems = list(changed.values()) + list(exc.changed.values())
                raise InputError("Objects kept changing during evaluation; no stable result "
                                 "could be produced.", problems) from None
            changed.update(exc.changed)


def evaluate(request, root, mode, deadline=None, allow_local_files=False):
    response = response_shell(request, mode)
    started, cpu = time.perf_counter(), time.process_time()
    try:
        native, input_report = to_native(
            request, mode, include_report=True, allow_local_files=allow_local_files)
        (response["axes"], response["findings"], workflow_assessment,
         validation, assessment_checks) = _evaluate_with_change_retry(
            native, request, root, input_report, deadline, allow_local_files)
        response["coverage"] = input_report["coverage"]
        response["unknown_inputs"] = input_report["unknown_inputs"]
        response["findings"] = input_report["findings"] + response["findings"]
        if not input_report["coverage"]["complete"]:
            note = (f" Results cover {input_report['coverage']['evaluated_artefacts']}/"
                    f"{input_report['coverage']['submitted_artefacts']} artefacts and "
                    f"{input_report['coverage']['evaluated_relationships']}/"
                    f"{input_report['coverage']['submitted_relationships']} relationships; "
                    "unknown values were not inferred.")
            for assessment in response["axes"].values():
                if assessment["availability"] == "available":
                    assessment["availability"] = "partial"
                assessment["reasoning"] = (assessment.get("reasoning") or "") + note
        _summarise_input_errors(response)
        if workflow_assessment is not None:
            response["workflow_assessment"] = workflow_assessment
        if assessment_checks:
            response["assessment_checks"] = assessment_checks
        if validation:
            response["validation"].update(validation)
        response["execution_status"] = "succeeded"
    except Unsupported as exc:
        response["execution_status"] = "unsupported"
        response["error"] = str(exc)
        response["findings"] = exc.findings or [finding("UNSUPPORTED_INPUT", str(exc))]
    except Exception as exc:
        response["execution_status"] = "error"
        response["error"] = str(exc)
        response["findings"] = (getattr(exc, "findings", None) or
                                [finding("INVALID_OR_FAILED_INPUT", str(exc), severity="medium")])
    response["processing_ms"] = (time.perf_counter() - started) * 1000
    response["resource_usage"]["cpu_ms"] = (time.process_time() - cpu) * 1000
    _sanitise_hash_fields(response)
    options = request.get("options")
    if isinstance(options, dict) and options.get("include_reasoning") is False:
        for assessment in response["axes"].values():
            assessment["reasoning"] = None
    elif response["execution_status"] == "succeeded":
        # Built last, from the final axes, so every adjustment is reflected.
        response.setdefault("assessment_checks", {})["score_explanations"] = build_score_explanations(response)
    validate(response, "scoring-response.schema.json")
    return response


# Technical guard only: subprocess waits overflow on extremely large timeouts.
# Roughly 11.6 days, so it never shortens a realistic caller timeout.
MAX_WAIT_SECONDS = 1_000_000


def effective_timeout(options):
    """Return the caller's ``timeout_seconds``; the evaluator adds no ceiling.

    The default applies only when a request carries no valid value, for
    example a request built from a public bundle without options.
    """
    requested = options.get("timeout_seconds") if isinstance(options, dict) else None
    if type(requested) is int and requested > 0:
        return min(requested, MAX_WAIT_SECONDS)
    return DEFAULT_TIMEOUT_SECONDS


def run_isolated(request, root, mode, command=None, allow_local_files=False):
    if allow_local_files and mode != "native":
        raise ValueError("--allow-local-files is restricted to trusted native manifests.")
    # A successful worker supplies its own source revision. Avoid hashing the
    # full source tree in the parent unless a parent-generated failure response
    # must be returned.
    response = response_shell(request, mode, include_source_revision=False)
    timeout = effective_timeout(request.get("options", {}))
    # Audio checks stop starting new work shortly before the caller's deadline,
    # so a slow request still returns the results it has instead of timing out.
    reserve = min(5.0, max(0.5, 0.1 * timeout))
    deadline = time.time() + max(0.0, timeout - reserve)
    command = command or [sys.executable, "-B", str(BASE / "run.py"), "--worker"]
    payload = json.dumps({"request": request, "root": str(root), "mode": mode,
                          "deadline": deadline,
                          "allow_local_files": allow_local_files}, allow_nan=False)
    started = time.perf_counter()
    try:
        result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise InputError("Evaluation worker failed; no scoring result is available.")
        response = json.loads(result.stdout)
        validate(response, "scoring-response.schema.json")
    except subprocess.TimeoutExpired:
        response["execution_status"] = "timeout"
        response["error"] = f"Evaluation exceeded the requested {timeout}-second timeout."
        response["findings"] = [finding("TIMEOUT", response["error"], severity="medium")]
    except Exception as exc:
        response["execution_status"] = "error"
        response["error"] = str(exc)
        response["findings"] = [finding("WORKER_ERROR", str(exc), severity="medium")]
    if response["system"]["source_revision"] is None:
        response["system"]["source_revision"] = source_revision()
    response["processing_ms"] = (time.perf_counter() - started) * 1000
    validate(response, "scoring-response.schema.json")
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--request", type=Path, help="CSEC scoring-request JSON")
    inputs.add_argument("--bundle", type=Path, help="CSEC public bundle.json (local convenience mode)")
    inputs.add_argument("--native", type=Path, help="Music native-request JSON with a Ben-format chain")
    parser.add_argument("--root", type=Path, help="Directory containing the declared objects/ files")
    parser.add_argument("--output", type=Path, help="Save the response to this file instead of stdout")
    parser.add_argument("--audio-analysis", action="store_true",
                        help="Append optional audfprint, SpectroMap and librosa observations; never changes scores")
    parser.add_argument("--audio-analysis-policy", type=Path,
                        help="Enable report-only audio analysis using a local policy JSON")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker:
            payload = json.load(sys.stdin)
            response = evaluate(payload["request"], payload["root"], payload["mode"],
                                deadline=payload.get("deadline"),
                                allow_local_files=payload.get("allow_local_files", False))
        else:
            selected = args.request or args.bundle or args.native
            if selected is None or args.root is None:
                parser.error("one input option and --root are required")
            if args.allow_local_files and not args.native:
                raise InputError("--allow-local-files is restricted to trusted native manifests.")
            request = read_json(selected)
            if args.bundle:
                request = public_bundle_request(request)
            mode = "native" if args.native else "csec"
            started = time.monotonic()
            deadline = started + effective_timeout(request.get("options", {}))
            response = run_isolated(request, args.root, mode)
            if args.audio_analysis or args.audio_analysis_policy:
                from music_target.audio_diagnostics.policy import DEFAULT_PATH
                from music_target.audio_diagnostics.runner import augment_response
                response = augment_response(response, request, args.root, mode,
                                            args.audio_analysis_policy or DEFAULT_PATH, deadline)
                response["processing_ms"] = (time.monotonic() - started) * 1000
                validate(response, "scoring-response.schema.json")
        serialized = json.dumps(response, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(serialized)
        else:
            sys.stdout.write(serialized)
        return 0  # The response's execution_status is authoritative, independent of decision.
    except Exception as exc:
        print(json.dumps({"execution_status": "error", "error": str(exc)}), file=sys.stderr)
        return 2  # No valid request identity/response could be established.


if __name__ == "__main__":
    sys.exit(main())
