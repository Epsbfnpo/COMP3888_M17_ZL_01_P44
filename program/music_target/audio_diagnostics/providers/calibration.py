"""Frozen, bounded diagnostic gates; never score or provenance evidence."""
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def profile():
    path = Path(__file__).resolve().parents[3] / "policies/audio_calibration_profile.json"
    value = json.loads(path.read_text())
    digest = value.pop("freeze_sha256")
    if hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() != digest:
        raise ValueError("Audio calibration profile digest mismatch")
    value["freeze_sha256"] = digest
    for group in ("semantics", "fingerprint", "feature_parameters"):
        if not all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in value[group].values()):
            raise ValueError("Invalid audio calibration limits")
    return value


def path_rejections(quality, aligned, residual_fraction, limits=None):
    """Physical/time-path gates apply to every reported global/local alignment."""
    p = limits or profile()["semantics"]
    reasons = []
    ratio = quality["source_to_target_duration_ratio"]
    slope = aligned["ratio"]
    if ratio is None or not p["minimum_duration_ratio"] <= ratio <= p["maximum_duration_ratio"]:
        reasons.append("duration_ratio_outside_profile")
    if min(quality["source_span_seconds"], quality["target_span_seconds"]) < p["minimum_aligned_seconds"]:
        reasons.append("aligned_span_too_short")
    if quality.get("query_beat_coverage_fraction", max(quality["source_beat_coverage_fraction"], quality["target_beat_coverage_fraction"])) < p["minimum_query_coverage"]:
        reasons.append("insufficient_query_coverage")
    if quality["diagonal_step_fraction"] < p["minimum_diagonal_fraction"]:
        reasons.append("too_few_diagonal_steps")
    if max(quality["max_target_beats_per_source_beat"], quality["max_source_beats_per_target_beat"]) > p["maximum_repeat"]:
        reasons.append("excessive_beat_collapse")
    if slope is None or not p["minimum_duration_ratio"] <= slope <= p["maximum_duration_ratio"]:
        reasons.append("aligned_speed_outside_profile")
    if residual_fraction is None or residual_fraction > p["maximum_residual_fraction"]:
        reasons.append("non_affine_or_unstable_timing")
    return reasons


def qualify_comparison(comparison, parameters):
    config = profile()
    p = config["semantics"]
    ranked = sorted(comparison["candidates"], key=lambda c: -c["harmonic_similarity"])
    best = ranked[0]
    reasons = path_rejections(best["alignment_quality"], best["aligned_tempo_candidate"],
                              best["alignment_residual_fraction"], p)
    if any(parameters.get(key) != val for key, val in config["feature_parameters"].items()):
        reasons.append("feature_parameters_outside_calibration")
    raster = comparison.get("time_grid_tempo")
    if raster is None:
        reasons.append("missing_independent_time_grid")
    else:
        reasons.extend("time_grid_" + r for r in path_rejections(comparison["time_grid_quality"], raster,
                                                               comparison["time_grid_residual_fraction"], p))
        beat_ratio = best["aligned_tempo_candidate"]["ratio"]
        if beat_ratio is None or raster["ratio"] is None or raster["ratio"] <= 0 or abs(beat_ratio / raster["ratio"] - 1) > p["maximum_tempo_disagreement_fraction"]:
            reasons.append("tempo_estimators_disagree")
    if comparison["shuffled_std"] < 1e-6:
        reasons.append("degenerate_shuffled_reference")
    if comparison["temporal_information"] < p["minimum_temporal_information"]:
        reasons.append("insufficient_temporal_information")
    if best["harmonic_similarity"] < p["minimum_similarity"]:
        reasons.append("low_harmonic_similarity")
    if best["harmonic_similarity_z"] < p["minimum_shuffle_z"]:
        reasons.append("insufficient_sequence_advantage")
    margin = best["harmonic_similarity"] - ranked[1]["harmonic_similarity"]
    if margin < p["minimum_pitch_margin"]:
        reasons.append("ambiguous_transposition")
    return {"status": "supported_within_profile" if not reasons else "abstained",
            "rejection_reasons": reasons, "pitch_margin": float(margin),
            "profile_id": config["profile_id"], "profile_sha256": config["freeze_sha256"],
            "scope": config["scope"]}


def fingerprint_support(match, metrics, parameters):
    p = profile()["fingerprint"]
    reasons = []
    if parameters["density"] != p["density"] or parameters["sample_rate_hz"] != 11025 or parameters["support_window_seconds"] != 0.25 or parameters["offset_bin_seconds"] != 0.05:
        reasons.append("parameters_outside_calibration")
    if match["matched_token_pairs"] < p["minimum_candidate_matches"]:
        reasons.append("too_few_token_pairs")
    if match["target_supported_window_duration_seconds"] < p["minimum_supported_seconds"]:
        reasons.append("insufficient_supported_time")
    background = metrics.get("mean_votes_per_offset_bin", 0.0)
    if match["matched_token_pairs"] < p["minimum_peak_background_ratio"] * background:
        reasons.append("insufficient_peak_over_background")
    return {"status": "supported_within_profile" if not reasons else "candidate_only",
            "rejection_reasons": reasons, "profile_id": profile()["profile_id"]}
