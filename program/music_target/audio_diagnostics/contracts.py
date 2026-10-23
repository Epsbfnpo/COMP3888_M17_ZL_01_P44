"""JSON protocol for observations, deliberately separate from scored checks."""

import json
from functools import lru_cache
from pathlib import Path

PROVIDERS = ("audfprint", "spectromap", "librosa", "music_semantics")
NON_SCORING = {"scoring_eligible": False, "integrity_contribution": None,
               "content_match_is_provenance": False}


@lru_cache(maxsize=1)
def _validator():
    from jsonschema import Draft202012Validator
    path = Path(__file__).resolve().parents[2] / "schemas/audio-diagnostic.schema.json"
    return Draft202012Validator(json.loads(path.read_text()))


def result(task, provider, reason="thresholds_not_calibrated", message=None):
    return {
        "diagnostic_id": task["task_id"] + ":" + provider,
        "relation_id": task["relation_id"],
        "relationship_type": task["relationship_type"],
        "source_hash": task["source_hash"], "target_hash": task["target_hash"],
        "selection_id": task.get("selection_id"), "range_source": task.get("range_source", "whole_file"),
        "provider": provider,
        "provider_version": None, "adapter_version": "1.0",
        "status": "inconclusive", "reason_code": reason,
        "message": message or "Experimental content observations; thresholds are not calibrated.",
        "analysis_complete": False, "analysed_ranges": {},
        "matches": [], "metrics": {}, "limitations": [
            "Content correspondence does not establish authorship or historical direction.",
            "Candidate thresholds are exploratory and do not validate a provenance claim."],
        **NON_SCORING,
    }


def unavailable(task, provider, reason, message):
    value = result(task, provider, reason, message)
    value["status"] = "unavailable"
    return value


def validate_result(value, task, provider):
    # The worker is not allowed to introduce scoreable data, even via a policy.
    for key, expected in NON_SCORING.items():
        if value.get(key) is not expected:
            raise ValueError("Diagnostic attempted to change a non-scoring boundary.")
    for key in ("source_hash", "target_hash", "relation_id", "relationship_type"):
        if value.get(key) != task[key]:
            raise ValueError("Diagnostic task identity changed.")
    if value.get("provider") != provider or value.get("status") not in ("inconclusive", "unavailable"):
        raise ValueError("Invalid diagnostic provider or status.")
    if value.get("diagnostic_id") != task["task_id"] + ":" + provider:
        raise ValueError("Diagnostic identifier changed.")
    if not isinstance(value.get("matches"), list) or not isinstance(value.get("metrics"), dict):
        raise ValueError("Invalid diagnostic measurements.")
    json.dumps(value, allow_nan=False)
    _validator().validate(value)
    return value
