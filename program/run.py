#!/usr/bin/env python3
"""One local scoring request per process. Use --help for accepted inputs."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "vendor"))

from music_target.contract import (
    InputError, Unsupported, public_bundle_request, read_json,
    response_shell, to_native, validate,
)
from music_target.engine import evaluate_chain, finding


def evaluate(request, root, mode):
    response = response_shell(request, mode)
    started, cpu = time.perf_counter(), time.process_time()
    try:
        native, input_report = to_native(request, mode, include_report=True)
        (response["axes"], response["findings"], workflow_assessment,
         validation, assessment_checks) = evaluate_chain(
            native, request["objects"], root, request.get("workflow"),
            allow_disconnected=not input_report["coverage"]["complete"])
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
        response["findings"] = [finding("UNSUPPORTED_INPUT", str(exc))]
    except Exception as exc:
        response["execution_status"] = "error"
        response["error"] = str(exc)
        response["findings"] = [finding("INVALID_OR_FAILED_INPUT", str(exc), severity="medium")]
    response["processing_ms"] = (time.perf_counter() - started) * 1000
    response["resource_usage"]["cpu_ms"] = (time.process_time() - cpu) * 1000
    options = request.get("options")
    if isinstance(options, dict) and options.get("include_reasoning") is False:
        for assessment in response["axes"].values():
            assessment["reasoning"] = None
    validate(response, "scoring-response.schema.json")
    return response


def run_isolated(request, root, mode, command=None):
    response = response_shell(request, mode)
    options = request.get("options", {})
    requested = options.get("timeout_seconds", 60) if isinstance(options, dict) else 60
    timeout = min(requested, 60) if type(requested) is int and requested > 0 else 60
    command = command or [sys.executable, "-B", str(BASE / "run.py"), "--worker"]
    payload = json.dumps({"request": request, "root": str(root), "mode": mode}, allow_nan=False)
    started = time.perf_counter()
    try:
        result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise InputError("Evaluation worker failed; no scoring result is available.")
        response = json.loads(result.stdout)
        validate(response, "scoring-response.schema.json")
    except subprocess.TimeoutExpired:
        response["execution_status"] = "timeout"
        response["error"] = f"Evaluation exceeded the enforced {timeout}-second timeout."
        response["findings"] = [finding("TIMEOUT", response["error"], severity="medium")]
    except Exception as exc:
        response["execution_status"] = "error"
        response["error"] = str(exc)
        response["findings"] = [finding("WORKER_ERROR", str(exc), severity="medium")]
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
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker:
            payload = json.load(sys.stdin)
            response = evaluate(payload["request"], payload["root"], payload["mode"])
        else:
            selected = args.request or args.bundle or args.native
            if selected is None or args.root is None:
                parser.error("one input option and --root are required")
            request = read_json(selected)
            if args.bundle:
                request = public_bundle_request(request)
            response = run_isolated(request, args.root, "native" if args.native else "csec")
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
