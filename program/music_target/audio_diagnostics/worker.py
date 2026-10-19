"""One provider task per process; stdout is a JSON protocol only."""

import contextlib
import importlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from music_target.audio_diagnostics.contracts import unavailable, validate_result
from music_target.audio_diagnostics.io import AnalysisUnavailable


def execute(task, provider, parameters):
    started = time.perf_counter()
    try:
        name = "librosa_structure" if provider == "librosa" else provider
        with contextlib.redirect_stdout(sys.stderr):
            module = importlib.import_module("music_target.audio_diagnostics.providers." + name)
            value = module.analyse(task, parameters)
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
        value = unavailable(task, provider, "invalid_provider_output", type(exc).__name__ + ": " + str(exc))
        return _finish(value, parameters, started, task, provider)


def _finish(value, parameters, started, task, provider):
    value["parameters"] = parameters
    value["processing_ms"] = (time.perf_counter() - started) * 1000
    return validate_result(value, task, provider)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    value = execute(payload["task"], payload["provider"], payload["parameters"])
    sys.stdout.write(json.dumps(value, allow_nan=False) + "\n")
