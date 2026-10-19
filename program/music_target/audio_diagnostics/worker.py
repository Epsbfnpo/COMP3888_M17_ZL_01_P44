"""One task per process, running each requested provider in turn.

stdout is a JSON-lines protocol only: one validated result per provider,
written as soon as that provider finishes, so a later timeout cannot discard
earlier results.
"""

import contextlib
import importlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from music_target.audio_diagnostics.agreement import AGREEMENT, expected_rows, fingerprint_agreement
from music_target.audio_diagnostics.contracts import unavailable, validate_result
from music_target.audio_diagnostics.io import AnalysisUnavailable


def new_context():
    # Shared by the providers of one task: decoded audio, plus the offset
    # votes the fingerprint providers record for cross-provider comparison.
    return {"audio_cache": {}, "votes": {}}


def execute(task, provider, parameters, context=None):
    started = time.perf_counter()
    context = new_context() if context is None else context
    try:
        name = "librosa_structure" if provider == "librosa" else provider
        with contextlib.redirect_stdout(sys.stderr):
            module = importlib.import_module("music_target.audio_diagnostics.providers." + name)
            value = module.analyse(task, parameters, context)
    except ImportError as exc:
        value = unavailable(task, provider, "dependency_unavailable", str(exc))
    except AnalysisUnavailable as exc:
        value = unavailable(task, provider, exc.code, str(exc))
    except Exception as exc:
        value = unavailable(task, provider, "provider_failed", type(exc).__name__ + ": " + str(exc))
    try:
        return _finish(value, parameters, started, task, provider)
    except Exception as exc:
        # A provider result that breaks the protocol (NaN, changed identity,
        # schema mismatch) becomes a structured unavailability, not a crash.
        context["votes"].pop(provider, None)
        value = unavailable(task, provider, "invalid_provider_output", type(exc).__name__ + ": " + str(exc))
        return _finish(value, parameters, started, task, provider)


def _finish(value, parameters, started, task, provider):
    value["parameters"] = parameters
    value["processing_ms"] = (time.perf_counter() - started) * 1000
    return validate_result(value, task, provider)


def run_task(task, providers, parameters, emit):
    context, rows = new_context(), {}
    for provider in providers:
        rows[provider] = execute(task, provider, parameters[provider], context)
        emit(rows[provider])
    if AGREEMENT in expected_rows(providers):
        started = time.perf_counter()
        try:
            value = fingerprint_agreement(task, rows, context["votes"])
            emit(_finish(value, {}, started, task, AGREEMENT))
        except Exception as exc:
            value = unavailable(task, AGREEMENT, "invalid_provider_output", type(exc).__name__ + ": " + str(exc))
            emit(_finish(value, {}, started, task, AGREEMENT))


def _emit(value):
    sys.stdout.write(json.dumps(value, allow_nan=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    run_task(payload["task"], payload["providers"], payload["parameters"], _emit)
