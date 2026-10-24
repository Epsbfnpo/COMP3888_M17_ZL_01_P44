"""Local analysis configuration. No weights, verdicts or development caps."""

import hashlib
import json
import math
from pathlib import Path
from .contracts import PROVIDERS

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "policies/audio_analysis_policy.json"
DEFAULTS = {
    "audfprint": {"sample_rate_hz": 11025, "density": 40.0,
                 "offset_bin_seconds": 0.05, "minimum_candidate_matches": 5,
                 "support_window_seconds": 0.25},
    "spectromap": {"sample_rate_hz": 22050, "n_fft": 512, "noverlap": 64,
                   "fraction": 0.1, "condition": 2, "peak_window_seconds": 4.0,
                   "minimum_candidate_matches": 5, "support_window_seconds": 0.25},
    "librosa": {"sample_rate_hz": 22050, "n_fft": 2048, "hop_length": 512,
                "aggregation_seconds": 1.0, "candidate_cosine_similarity": 0.90},
    "music_semantics": {"sample_rate_hz": 22050, "hop_length": 512, "phrase_beats": 16,
                        "label_similarity": 0.9, "baseline_shuffles": 20, "minimum_seconds": 8.0, "alignment_grid_seconds": 0.25,
                        "section_aggregation_seconds": 0.5, "section_context_seconds": 12.0,
                        "section_minimum_seconds": 6.0, "section_prominence": 0.03},
    "bandwidth": {"frame_size": 8192, "spectrum_frames": 600, "level_percentile": 90, "cliff_drop_db": 20.0,
                  "step_drop_db": 6.0, "flat_floor_spread_db": 3.0, "step_search_top_fraction": 0.9},
}


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate audio policy key: " + key)
        value[key] = item
    return value


def load_policy(path=DEFAULT_PATH):
    raw = Path(path).read_bytes()
    policy = json.loads(raw, object_pairs_hook=_unique_pairs)
    if not isinstance(policy, dict) or set(policy) != {"policy_id", "policy_version", "providers", "parameters"}:
        raise ValueError("Audio policy requires only policy_id, policy_version, providers and parameters.")
    if not isinstance(policy["policy_id"], str) or not policy["policy_id"]:
        raise ValueError("Invalid audio policy_id.")
    if type(policy["policy_version"]) is not int or policy["policy_version"] < 1:
        raise ValueError("Invalid audio policy_version.")
    names = policy["providers"]
    if (not isinstance(names, list) or not names or any(name not in PROVIDERS for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Unknown, duplicate or empty audio providers.")
    overrides = policy["parameters"]
    if not isinstance(overrides, dict) or set(overrides) - set(PROVIDERS):
        raise ValueError("Invalid audio parameters.")
    params = {}
    integer_keys = {"sample_rate_hz", "n_fft", "noverlap", "condition", "hop_length", "minimum_candidate_matches",
                    "phrase_beats", "baseline_shuffles", "frame_size", "spectrum_frames", "level_percentile"}
    for name in names:
        supplied = overrides.get(name, {})
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS[name]):
            raise ValueError("Unknown parameters for " + name)
        params[name] = {**DEFAULTS[name], **supplied}
        for key, value in params[name].items():
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Audio parameters must be finite numbers.")
            if key in integer_keys and type(value) is not int:
                raise ValueError(key + " must be an integer.")
            if value < 0 or (value == 0 and key not in {"noverlap", "condition"}):
                raise ValueError("Invalid audio parameter: " + key)
        if name == "spectromap":
            if params[name]["noverlap"] >= params[name]["n_fft"] or params[name]["condition"] not in (0, 1, 2):
                raise ValueError("Invalid SpectroMap overlap/condition.")
            if not 0 < params[name]["fraction"] <= 1:
                raise ValueError("SpectroMap fraction must be in (0, 1].")
        if name == "librosa" and not 0 < params[name]["candidate_cosine_similarity"] <= 1:
            raise ValueError("Invalid cosine candidate threshold.")
        if name == "music_semantics" and not 0 < params[name]["label_similarity"] <= 1:
            raise ValueError("Invalid section label similarity.")
        if name == "bandwidth":
            if not 1 <= params[name]["level_percentile"] <= 100 or params[name]["frame_size"] < 1024:
                raise ValueError("Invalid bandwidth frame size or level percentile.")
            if not 0 < params[name]["step_search_top_fraction"] <= 1:
                raise ValueError("Bandwidth step_search_top_fraction must be in (0, 1].")
    return {**policy, "parameters": params,
            "policy_sha256": hashlib.sha256(raw).hexdigest()}
