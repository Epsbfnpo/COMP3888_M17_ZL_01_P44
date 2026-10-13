"""Bounded signal-content checks for real audio fixtures.

These passes corroborate or challenge submitted relationship claims.  They do
not create graph edges and never turn acoustic similarity into proof of
authorship or historical provenance.  Expensive searches are deliberately
downsampled and bounded so a request cannot trigger unbounded DSP work.
"""

from __future__ import annotations

import math

import numpy as np

from music_target.audio_derivation import (
    MAX_SOURCE_CORRELATION,
    MIN_SOURCE_IDENTIFIABILITY,
    COMPLEX_PROCESSING_WORDS,
    MasterDerivationPass,
    ResourceBudgetExceeded,
    _analysis_proxy,
    _correlation,
    _fft_normalized_candidates,
    _max_source_correlation,
    _mono_resample,
    _resample_series,
    _rms,
    _source_gram,
    _source_identifiability,
)


ALIGNMENT_RATE_HZ = 200
FEATURE_RATE_HZ = 25
CONTRIBUTION_RATE_HZ = 400
RESIDUAL_RATE_HZ = 1000
MAX_CONTRIBUTION_SOURCES = 16
RIDGE_FACTOR = 1e-8

AUDIO_RELATIONSHIPS = {
    "edited_from", "comped_from", "stemmed_from", "mixed_from",
    "mastered_from", "excerpted_from", "derived_from",
}
CONTRIBUTION_RELATIONSHIPS = {"stemmed_from", "mixed_from"}


def _base(rule, pass_name, target_hash, source_hashes):
    return {
        "rule": rule,
        "pass": pass_name,
        "target_hash": target_hash,
        "source_hashes": sorted(set(source_hashes)),
        "content_match_is_provenance": False,
    }


def _resource_fields(exc):
    if isinstance(exc, ResourceBudgetExceeded):
        return {"reason_code": exc.reason_code,
                "analysis_complete": False,
                "search_complete": False}
    return {}


def _proxy_fields(*audio_objects):
    valid = [audio for audio in audio_objects if audio is not None]
    return {
        "analysis_proxy_sample_rate_hz": min(
            audio.get("proxy_sample_rate_hz", audio["sample_rate_hz"])
            for audio in valid),
        "searched_ranges_seconds": [
            [0.0, audio["duration_seconds"]] for audio in valid],
        "search_complete": all(audio.get("search_complete", True) for audio in valid),
    } if valid else {}


def _is_audio(node):
    kind = node.get("artefact_type", "")
    return kind == "audio" or kind.startswith("audio/")


def _node_index(native):
    return {item["artefact_hash"]: item for item in native["artefacts"]}


def _flagged_edges(native):
    """Edges whose declaration had input errors; their parameters are not used."""
    return (native.get("_input_issues") or {}).get("declaration_edges") or {}


def _input_error_result(base):
    return {**base, "status": "unavailable", "reason_code": "input_error",
            "reason": ("A relationship declaration for this target contains unrecognised "
                       "fields; it was not corrected, so this diagnostic was not run.")}


def _audio_edges(native, relationships=AUDIO_RELATIONSHIPS):
    nodes = _node_index(native)
    flagged = _flagged_edges(native)
    for target in native["artefacts"]:
        if not _is_audio(target):
            continue
        for edge in target.get("evidence", []):
            if (target["artefact_hash"], edge["hash"]) in flagged:
                continue
            source = nodes.get(edge["hash"])
            if source and _is_audio(source) and edge["relationship_type"] in relationships:
                yield source, target, edge


def _read(chain, digest):
    return _analysis_proxy(chain, digest)


def _standardize(rows):
    values = np.asarray(rows, dtype=np.float32)
    if values.size == 0:
        return np.empty((0, 0), dtype=np.float32)
    means = np.mean(values, axis=0)
    scales = np.std(values, axis=0)
    scales = np.where(scales > 1e-6, scales, 1.0)
    return ((values - means) / scales).astype(np.float32)


def _features(samples, sample_rate=ALIGNMENT_RATE_HZ):
    """Return gain-tolerant short-time energy/shape landmarks."""
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    frame_size = max(4, round(sample_rate / FEATURE_RATE_HZ))
    count = values.size // frame_size
    if count < 1:
        return np.empty((0, 4), dtype=np.float32)
    frames = values[:count * frame_size].reshape(count, frame_size)
    energy = np.sqrt(np.mean(frames * frames, axis=1))
    differences = np.diff(frames, axis=1)
    difference_rms = np.sqrt(np.mean(differences * differences, axis=1))
    zero_crossings = np.mean((frames[:, :-1] < 0) != (frames[:, 1:] < 0), axis=1)
    peak = np.max(np.abs(frames), axis=1)
    floor = np.maximum(energy, 1e-9)
    rows = np.column_stack((np.log10(energy + 1e-9),
                            difference_rms / floor,
                            zero_crossings,
                            peak / floor))
    return _standardize(rows)


def _cosine(left, right):
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.sum(left * right) / denominator) if denominator > 1e-12 else 0.0


def _short_correlation(left, right):
    """Correlation for short declared edits; eight bounded samples are enough."""
    count = min(len(left), len(right))
    if count < 8:
        return 0.0
    left_values = np.asarray(left[:count], dtype=np.float64)
    right_values = np.asarray(right[:count], dtype=np.float64)
    left_values -= np.mean(left_values)
    right_values -= np.mean(right_values)
    denominator = float(np.linalg.norm(left_values) * np.linalg.norm(right_values))
    return float(left_values @ right_values / denominator) if denominator > 1e-12 else 0.0


def _sequence_similarity(left, right):
    count = min(len(left), len(right))
    if count < 4:
        return 0.0
    left_values = np.asarray(left[:count], dtype=np.float64)
    right_values = np.asarray(right[:count], dtype=np.float64)
    numerator = np.sum(left_values * right_values, axis=1)
    denominator = (np.linalg.norm(left_values, axis=1) *
                   np.linalg.norm(right_values, axis=1))
    similarities = np.divide(numerator, denominator,
                             out=np.zeros_like(numerator), where=denominator > 1e-12)
    return float(np.clip((np.mean(similarities) + 1.0) / 2.0, 0.0, 1.0))


def _dtw_similarity(left, right, band_ratio=0.12):
    """Small Sakoe-Chiba-banded DTW over short-time landmarks."""
    if len(left) < 4 or len(right) < 4:
        return 0.0
    # Bound quadratic work while preserving the full time span.
    stride = max(1, math.ceil(max(len(left), len(right)) / 500))
    left, right = left[::stride], right[::stride]
    width = max(abs(len(left) - len(right)) + 1,
                round(max(len(left), len(right)) * band_ratio))
    previous = {0: 0.0}
    for row_index, left_value in enumerate(left, 1):
        current = {}
        first = max(1, row_index - width)
        last = min(len(right), row_index + width)
        for column_index in range(first, last + 1):
            right_value = right[column_index - 1]
            distance = math.sqrt(sum((a - b) ** 2
                                     for a, b in zip(left_value, right_value)) /
                                 len(left_value))
            candidates = [previous.get(column_index),
                          current.get(column_index - 1),
                          previous.get(column_index - 1)]
            candidates = [value for value in candidates if value is not None]
            if candidates:
                current[column_index] = distance + min(candidates)
        previous = current
        if not previous:
            return 0.0
    distance = previous.get(len(right))
    if distance is None:
        return 0.0
    return math.exp(-distance / max(len(left), len(right), 1))


def _best_feature_alignment(source_audio, target_audio):
    source = _mono_resample(source_audio, ALIGNMENT_RATE_HZ)
    target = _mono_resample(target_audio, ALIGNMENT_RATE_HZ)
    if min(len(source), len(target)) < ALIGNMENT_RATE_HZ // 4:
        return None
    source_is_query = len(source) <= len(target)
    query = source if source_is_query else target
    haystack = target if source_is_query else source
    coarse = _fft_normalized_candidates(
        query, haystack, top_k=12,
        min_separation=max(ALIGNMENT_RATE_HZ // 4, len(query) // 8))
    if not coarse:
        return None
    query_features = _features(query)
    haystack_features = _features(haystack)
    refined = []
    for waveform_score, sample_start in coarse:
        feature_start = round(sample_start / ALIGNMENT_RATE_HZ * FEATURE_RATE_HZ)
        comparison = haystack_features[feature_start:
                                       feature_start + len(query_features)]
        feature_score = _sequence_similarity(query_features, comparison)
        combined = 0.65 * feature_score + 0.35 * max(0.0, waveform_score)
        refined.append((combined, sample_start, waveform_score, feature_score))
    refined.sort(reverse=True)
    best_score, best_start, waveform, _ = refined[0]
    separated = [item for item in refined[1:]
                 if abs(item[1] - best_start) >= max(2, FEATURE_RATE_HZ // 4)]
    second_score = separated[0][0] if separated else 0.0
    if source_is_query:
        source_start, target_start = 0.0, best_start / ALIGNMENT_RATE_HZ
    else:
        source_start, target_start = best_start / ALIGNMENT_RATE_HZ, 0.0
    duration = min(len(source) / ALIGNMENT_RATE_HZ - source_start,
                   len(target) / ALIGNMENT_RATE_HZ - target_start)
    count = max(0, round(duration * ALIGNMENT_RATE_HZ))
    source_first = round(source_start * ALIGNMENT_RATE_HZ)
    target_first = round(target_start * ALIGNMENT_RATE_HZ)
    waveform = _correlation(source[source_first:source_first + count],
                            target[target_first:target_first + count])
    return {
        "source_start_seconds": source_start,
        "target_start_seconds": target_start,
        "estimated_offset_seconds": target_start - source_start,
        "feature_similarity": best_score,
        "waveform_correlation": waveform,
        "ambiguity_margin": best_score - second_score,
        "ambiguous": second_score >= 0.70 and best_score - second_score < 0.03,
        "competing_alignment_similarity": second_score,
        "compared_duration_seconds": max(0.0, duration),
        "search_complete": True,
        "searched_source_range_seconds": [0.0, source_audio["duration_seconds"]],
        "searched_target_range_seconds": [0.0, target_audio["duration_seconds"]],
        "analysis_proxy_sample_rate_hz": min(
            source_audio.get("proxy_sample_rate_hz", source_audio["sample_rate_hz"]),
            target_audio.get("proxy_sample_rate_hz", target_audio["sample_rate_hz"])),
    }


def _declared_or_aligned_segments(source_audio, target_audio, attrs, alignment=None,
                                  rate=ALIGNMENT_RATE_HZ):
    source = _mono_resample(source_audio, rate)
    target = _mono_resample(target_audio, rate)
    fields = ("source_start_seconds", "source_end_seconds",
              "target_start_seconds", "target_end_seconds")
    if all(attrs.get(field) is not None for field in fields):
        source_start, source_end = (float(attrs[fields[0]]), float(attrs[fields[1]]))
        target_start, target_end = (float(attrs[fields[2]]), float(attrs[fields[3]]))
        if min(source_start, target_start) < 0 or source_end <= source_start or target_end <= target_start:
            raise ValueError("declared audio ranges must be positive and ordered")
        duration = min(source_end - source_start, target_end - target_start)
        parameter_source = "submitter_declared"
    else:
        alignment = alignment or _best_feature_alignment(source_audio, target_audio)
        if alignment is None:
            raise ValueError("audio alignment is unavailable")
        source_start = alignment["source_start_seconds"]
        target_start = alignment["target_start_seconds"]
        duration = alignment["compared_duration_seconds"]
        parameter_source = "signal_estimated_diagnostic"
    count = min(round(duration * rate), len(source), len(target))
    source_first, target_first = round(source_start * rate), round(target_start * rate)
    count = min(count, len(source) - source_first, len(target) - target_first)
    if count < max(8, rate // 20):
        raise ValueError("aligned audio overlap is too short")
    return (source[source_first:source_first + count],
            target[target_first:target_first + count], parameter_source)


def _solve(matrix, vector):
    try:
        return np.linalg.solve(np.asarray(matrix, dtype=np.float64),
                               np.asarray(vector, dtype=np.float64))
    except np.linalg.LinAlgError:
        return None


def _fit_linear(target, sources):
    target = np.asarray(target, dtype=np.float64).reshape(-1)
    if target.size == 0 or not sources or any(len(source) != len(target) for source in sources):
        return None
    design = np.stack([np.asarray(source, dtype=np.float64) for source in sources], axis=1)
    gram = np.einsum("ni,nj->ij", design, design, optimize=True)
    projection = np.einsum("ni,n->i", design, target, optimize=True)
    ridge = max(float(np.trace(gram)) * RIDGE_FACTOR, 1e-12)
    gram = gram + np.eye(len(sources)) * ridge
    gains = _solve(gram, projection)
    if gains is None:
        return None
    predicted = np.einsum("ni,i->n", design, gains, optimize=True)
    energy = float(np.sum(target * target, dtype=np.float64))
    if energy < 1e-12:
        return None
    difference = target - predicted
    residual = float(np.sum(difference * difference, dtype=np.float64))
    return {
        "gains": gains,
        "normalized_rmse": math.sqrt(residual / energy),
        "correlation": _correlation(target, predicted),
        "predicted": predicted,
    }


def _aligned_source_vector(source_audio, target_audio, attrs, alignment=None,
                           rate=CONTRIBUTION_RATE_HZ):
    source = _mono_resample(source_audio, rate)
    target = _mono_resample(target_audio, rate)
    output = np.zeros(len(target), dtype=np.float32)
    declared = all(attrs.get(field) is not None for field in (
        "source_start_seconds", "source_end_seconds",
        "target_start_seconds", "target_end_seconds"))
    if declared:
        source_start = float(attrs["source_start_seconds"])
        source_end = float(attrs["source_end_seconds"])
        target_start = float(attrs["target_start_seconds"])
        target_end = float(attrs["target_end_seconds"])
        duration = min(source_end - source_start, target_end - target_start)
    else:
        alignment = alignment or _best_feature_alignment(source_audio, target_audio)
        if alignment is None:
            raise ValueError("source alignment is unavailable")
        source_start = alignment["source_start_seconds"]
        target_start = alignment["target_start_seconds"]
        duration = alignment["compared_duration_seconds"]
    source_first, target_first = round(source_start * rate), round(target_start * rate)
    count = min(round(duration * rate), len(source) - source_first,
                len(target) - target_first)
    if count < max(32, rate // 4):
        raise ValueError("source/target overlap is too short")
    output[target_first:target_first + count] = source[source_first:source_first + count]
    return output, ("submitter_declared" if declared else "signal_estimated_diagnostic")


def _gain_stability(target, source, gain, windows=4):
    target = np.asarray(target, dtype=np.float64)
    source = np.asarray(source, dtype=np.float64)
    values = []
    size = max(1, len(target) // windows)
    for start in range(0, len(target), size):
        left, right = source[start:start + size], target[start:start + size]
        denominator = float(np.sum(left * left, dtype=np.float64))
        if denominator > 1e-12:
            values.append(float(np.sum(left * right, dtype=np.float64)) / denominator)
    if len(values) < 2 or abs(gain) < 1e-9:
        return 0.0
    deviation = math.sqrt(sum((value - gain) ** 2 for value in values) / len(values))
    return max(0.0, 1.0 - min(1.0, deviation / max(abs(gain), 1e-9)))


class AudioAlignmentPass:
    """Estimate offsets as diagnostics; never rewrite declared relationship data."""

    @staticmethod
    def evaluate(native, chain):
        results = []
        for source, target, edge in _audio_edges(native):
            base = _base("audio_content_alignment", "AudioAlignmentPass",
                         target["artefact_hash"], [source["artefact_hash"]])
            try:
                alignment = _best_feature_alignment(
                    _read(chain, source["artefact_hash"]),
                    _read(chain, target["artefact_hash"]))
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable", "reason": str(exc),
                                **_resource_fields(exc)})
                continue
            if alignment is None:
                results.append({**base, "status": "unavailable",
                                "reason": "Insufficient non-silent audio for alignment."})
                continue
            if alignment["ambiguous"]:
                status, reason = "not_applicable", "Several offsets have similar landmark support."
            elif alignment["feature_similarity"] >= 0.72:
                status, reason = "corroborated", "Signal landmarks support the estimated source/target alignment."
            elif alignment["feature_similarity"] < 0.30:
                status, reason = "not_applicable", "No reliable automatic alignment was found."
            else:
                status, reason = "not_applicable", "Alignment evidence is inconclusive."
            results.append({**base, "status": status, "reason": reason,
                            "relationship_type": edge["relationship_type"],
                            "parameter_source": "signal_estimated_diagnostic",
                            **{key: round(value, 6) if isinstance(value, float) else value
                               for key, value in alignment.items()}})
        return results


class SourceContributionPass:
    """Use leave-one-out residual change to assess each declared mix source."""

    @staticmethod
    def evaluate(native, chain):
        results = []
        for target in native["artefacts"]:
            edges = [edge for edge in target.get("evidence", [])
                     if edge["relationship_type"] in CONTRIBUTION_RELATIONSHIPS]
            if not edges or not _is_audio(target):
                continue
            base = _base("audio_source_contribution", "SourceContributionPass",
                         target["artefact_hash"], [edge["hash"] for edge in edges])
            if any((target["artefact_hash"], edge["hash"]) in _flagged_edges(native)
                   for edge in edges):
                results.append(_input_error_result(base))
                continue
            if len(edges) > MAX_CONTRIBUTION_SOURCES:
                results.append({**base, "status": "not_applicable",
                                "reason": "The bounded contribution analysis supports at most 16 sources."})
                continue
            try:
                target_audio = _read(chain, target["artefact_hash"])
                target_samples = _mono_resample(target_audio, CONTRIBUTION_RATE_HZ)
                aligned, parameter_sources, source_audio_objects = [], [], []
                for edge in edges:
                    source_audio = _read(chain, edge["hash"])
                    source_audio_objects.append(source_audio)
                    vector, parameter_source = _aligned_source_vector(
                        source_audio, target_audio,
                        edge.get("attributes") or {})
                    aligned.append(vector)
                    parameter_sources.append(parameter_source)
                gram = _source_gram(aligned, range(len(target_samples)))
                max_source_correlation = _max_source_correlation(gram)
                source_identifiability = _source_identifiability(gram)
                full = _fit_linear(target_samples, aligned)
                if full is None:
                    raise ValueError("linear contribution model is singular or silent")
                for index, edge in enumerate(edges):
                    reduced_sources = aligned[:index] + aligned[index + 1:]
                    # A source lying in the span of the other declared sources has
                    # no unique gain and a leave-one-out delta of zero whether or
                    # not it was used, so neither corroborated nor not_supported
                    # is a supportable conclusion for it. Its multiple correlation
                    # with the remaining sources is measured with the same fit and
                    # the same MAX_SOURCE_CORRELATION limit as audio_derivation.py's
                    # _linear_status guard. Only that source is declined: a source
                    # outside that span keeps a unique gain and a well-defined
                    # delta however the others relate to each other.
                    span = (_fit_linear(aligned[index], reduced_sources)
                            if reduced_sources else None)
                    span_correlation = (math.sqrt(max(
                        0.0, 1.0 - span["normalized_rmse"] ** 2)) if span else 0.0)
                    if span_correlation >= MAX_SOURCE_CORRELATION:
                        results.append({**base, "status": "not_applicable",
                                        "reason": ("This source cannot be distinguished from "
                                                   "the other declared sources well enough to "
                                                   "attribute a unique contribution to it."),
                                        "source_hash": edge["hash"],
                                        "relationship_type": edge["relationship_type"],
                                        "source_span_correlation": round(span_correlation, 6),
                                        "max_source_correlation": round(max_source_correlation, 6),
                                        "source_identifiability": round(source_identifiability, 8),
                                        **_proxy_fields(target_audio,
                                                        *source_audio_objects),
                                        "estimated_parameters_are_diagnostic": True})
                        continue
                    reduced = (_fit_linear(target_samples, reduced_sources)
                               if reduced_sources else None)
                    reduced_rmse = reduced["normalized_rmse"] if reduced else 1.0
                    delta = reduced_rmse - full["normalized_rmse"]
                    gain = full["gains"][index]
                    stability = _gain_stability(target_samples, aligned[index], gain)
                    if delta >= 0.02 and abs(gain) >= 0.005 and stability >= 0.15:
                        status = "corroborated"
                        reason = "Removing this source materially increases reconstruction residual."
                    elif delta <= 0.005 or abs(gain) < 0.002:
                        status = "not_supported"
                        reason = "The source has near-zero unique residual contribution in this model."
                    else:
                        status = "not_applicable"
                        reason = "The source contribution is not identifiable with sufficient stability."
                    results.append({**base, "status": status, "reason": reason,
                                    "source_hash": edge["hash"],
                                    "relationship_type": edge["relationship_type"],
                                    "estimated_gain": round(gain, 6),
                                    "gain_stability": round(stability, 6),
                                    "full_model_normalized_rmse": round(
                                        full["normalized_rmse"], 6),
                                    "leave_one_out_normalized_rmse": round(reduced_rmse, 6),
                                    "unique_residual_delta": round(delta, 6),
                                    "parameter_source": parameter_sources[index],
                                    **_proxy_fields(target_audio,
                                                    *source_audio_objects),
                                    "estimated_parameters_are_diagnostic": True})
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "reason": f"Contribution analysis unavailable: {exc}",
                                **_resource_fields(exc)})
        return results


class DecoySourcePass:
    """Flag declared sources with no measurable unique contribution."""

    @staticmethod
    def evaluate(contribution_results):
        results = []
        for contribution in contribution_results:
            if contribution.get("source_hash") is None:
                continue
            base = _base("audio_declared_source_decoy", "DecoySourcePass",
                         contribution["target_hash"], [contribution["source_hash"]])
            if contribution["status"] == "not_supported":
                status = "suspected_decoy"
                reason = "The declared source can be removed without a material residual penalty."
            elif contribution["status"] == "corroborated":
                status = "clear"
                reason = "The declared source has measurable unique contribution."
            else:
                status = "not_applicable"
                reason = "Contribution evidence is insufficient for a decoy assessment."
            results.append({**base, "status": status, "reason": reason,
                            "source_hash": contribution["source_hash"],
                            "unique_residual_delta": contribution.get("unique_residual_delta"),
                            "estimated_gain": contribution.get("estimated_gain"),
                            "analysis_proxy_sample_rate_hz": contribution.get(
                                "analysis_proxy_sample_rate_hz"),
                            "searched_ranges_seconds": contribution.get(
                                "searched_ranges_seconds"),
                            "search_complete": contribution.get("search_complete")})
        return results


class CompVerificationPass:
    """Verify individual CompSheet selections using waveform landmarks and DTW."""

    @staticmethod
    def evaluate(native, chain, parser_observations):
        results = []
        nodes = _node_index(native)
        sheets = [parsed["data"] for parsed in parser_observations.values()
                  if parsed.get("parser") == "comp-sheet" and parsed.get("data")]
        for sheet in sheets:
            default_target = sheet.get("metadata", {}).get("target_hash")
            for selection in sheet.get("selections", []):
                source_hash = selection.get("source_hash")
                target_hash = selection.get("target_hash") or default_target
                base = _base("comp_selection_content", "CompVerificationPass",
                             target_hash or "", [source_hash] if source_hash else [])
                if source_hash not in nodes or target_hash not in nodes:
                    results.append({**base, "status": "unavailable",
                                    "selection_id": selection.get("selection_id"),
                                    "reason": "CompSheet selection does not resolve to submitted audio."})
                    continue
                try:
                    attrs = {
                        "source_start_seconds": selection["source_start_seconds"],
                        "source_end_seconds": selection["source_end_seconds"],
                        "target_start_seconds": selection["target_start_seconds"],
                        "target_end_seconds": selection["target_end_seconds"],
                    }
                    source_audio = _read(chain, source_hash)
                    target_audio = _read(chain, target_hash)
                    source, target, _ = _declared_or_aligned_segments(
                        source_audio, target_audio, attrs)
                    waveform = abs(_short_correlation(source, target))
                    source_features, target_features = _features(source), _features(target)
                    landmarks = _sequence_similarity(source_features, target_features)
                    dtw = _dtw_similarity(source_features, target_features)
                    robust = 0.35 * waveform + 0.30 * landmarks + 0.35 * dtw
                except (KeyError, OSError, ValueError, TypeError) as exc:
                    results.append({**base, "status": "unavailable",
                                    "selection_id": selection.get("selection_id"),
                                    "reason": f"Comp selection comparison unavailable: {exc}",
                                    **_resource_fields(exc)})
                    continue
                if waveform >= 0.98:
                    status, reason = "matched", "The declared take segment matches the edited target segment."
                elif robust >= 0.70:
                    status, reason = "corroborated", "Time-frequency landmarks support the declared take selection."
                elif robust <= 0.25:
                    status, reason = "contradicted", "The declared take segment is inconsistent with the target segment."
                else:
                    status, reason = "not_applicable", "The processed selection comparison is inconclusive."
                results.append({**base, "status": status, "reason": reason,
                                "selection_id": selection.get("selection_id"),
                                "source_hash": source_hash,
                                "waveform_correlation": round(waveform, 6),
                                "landmark_similarity": round(landmarks, 6),
                                "dtw_similarity": round(dtw, 6),
                                "robust_similarity": round(robust, 6),
                                **_proxy_fields(source_audio, target_audio),
                                "verified_scope": "declared_comp_sheet_ranges"})
        return results


class ProcessedAudioMatchPass:
    """Corroborate declared edges after gain, EQ, compression or light timing changes."""

    @staticmethod
    def evaluate(native, chain):
        results = []
        for source, target, edge in _audio_edges(native):
            if edge["relationship_type"] == "excerpted_from":
                continue
            base = _base("processed_audio_content_match", "ProcessedAudioMatchPass",
                         target["artefact_hash"], [source["artefact_hash"]])
            attrs = edge.get("attributes") or {}
            description = str(attrs.get("transformation_description") or "").casefold()
            declared_complex = any(word in description for word in COMPLEX_PROCESSING_WORDS)
            try:
                source_audio = _read(chain, source["artefact_hash"])
                target_audio = _read(chain, target["artefact_hash"])
                alignment = _best_feature_alignment(source_audio, target_audio)
                source_samples, target_samples, parameter_source = _declared_or_aligned_segments(
                    source_audio, target_audio, attrs, alignment)
                waveform = abs(_correlation(source_samples, target_samples))
                source_features, target_features = _features(source_samples), _features(target_samples)
                landmarks = _sequence_similarity(source_features, target_features)
                dtw = _dtw_similarity(source_features, target_features)
                source_envelope = source_features[:, 0]
                target_envelope = target_features[:, 0]
                envelope = abs(_correlation(source_envelope, target_envelope))
                robust = 0.20 * waveform + 0.30 * landmarks + 0.30 * dtw + 0.20 * envelope
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "reason": f"Processed-audio comparison unavailable: {exc}",
                                **_resource_fields(exc)})
                continue
            if robust >= 0.72:
                status, reason = "corroborated", "Gain-tolerant time-frequency features support content continuity."
            elif (robust <= 0.25 and
                  edge["relationship_type"] not in CONTRIBUTION_RELATIONSHIPS):
                status, reason = "contradicted", "Waveform and time-frequency evidence do not support content continuity."
            else:
                status, reason = "not_applicable", "Processing, source masking, or overlap makes the pairwise content comparison inconclusive."
            results.append({**base, "status": status, "reason": reason,
                            "relationship_type": edge["relationship_type"],
                            "declared_complex_processing": declared_complex,
                            "parameter_source": parameter_source,
                            "waveform_correlation": round(waveform, 6),
                            "landmark_similarity": round(landmarks, 6),
                            "dtw_similarity": round(dtw, 6),
                            "envelope_correlation": round(envelope, 6),
                            **_proxy_fields(source_audio, target_audio),
                            "robust_similarity": round(robust, 6)})
        return results


def _channel_vectors(audio, output_rate):
    # Reuses the same anti-aliased decimation audio_derivation.py's
    # _mono_resample applies, one channel at a time (mixdown-then-filter
    # and filter-then-mixdown are equivalent for a linear, per-sample-
    # identical filter, so this is not a behavioural split from mono).
    rate = audio["sample_rate_hz"]
    frames = np.asarray(audio["frames"], dtype=np.float32)
    return [_resample_series(frames[:, channel], rate, output_rate)
            for channel in range(audio["channels"])]


def _place_channel(source, target_count, attrs, offset_seconds=0.0,
                   rate=RESIDUAL_RATE_HZ):
    source = np.asarray(source, dtype=np.float32)
    output = np.zeros(target_count, dtype=np.float32)
    declared = all(attrs.get(field) is not None for field in (
        "source_start_seconds", "source_end_seconds",
        "target_start_seconds", "target_end_seconds"))
    if declared:
        source_start = float(attrs["source_start_seconds"])
        source_end = float(attrs["source_end_seconds"])
        target_start = float(attrs["target_start_seconds"])
        target_end = float(attrs["target_end_seconds"])
        count = round(min(source_end - source_start, target_end - target_start) * rate)
    else:
        source_start = max(0.0, -offset_seconds)
        target_start = max(0.0, offset_seconds)
        count = min(len(source) - round(source_start * rate),
                    target_count - round(target_start * rate))
    source_first, target_first = round(source_start * rate), round(target_start * rate)
    count = min(count, len(source) - source_first, target_count - target_first)
    if count > 0:
        output[target_first:target_first + count] = source[source_first:source_first + count]
    return output


class StemMixResidualPass:
    """Estimate a diagnostic source-channel matrix and residual for stems/mixes."""

    @staticmethod
    def evaluate(native, chain):
        results = []
        for target in native["artefacts"]:
            edges = [edge for edge in target.get("evidence", [])
                     if edge["relationship_type"] in CONTRIBUTION_RELATIONSHIPS]
            if not edges or target.get("artefact_type") not in {"audio/stem", "audio/mix"}:
                continue
            base = _base("audio_stem_mix_residual", "StemMixResidualPass",
                         target["artefact_hash"], [edge["hash"] for edge in edges])
            if any((target["artefact_hash"], edge["hash"]) in _flagged_edges(native)
                   for edge in edges):
                results.append(_input_error_result(base))
                continue
            try:
                target_audio = _read(chain, target["artefact_hash"])
                target_channels = _channel_vectors(target_audio, RESIDUAL_RATE_HZ)
                source_columns, labels, source_audio_objects = [], [], []
                for edge in edges:
                    source_audio = _read(chain, edge["hash"])
                    source_audio_objects.append(source_audio)
                    alignment = _best_feature_alignment(source_audio, target_audio)
                    offset = alignment["estimated_offset_seconds"] if alignment else 0.0
                    for channel, values in enumerate(_channel_vectors(source_audio,
                                                                       RESIDUAL_RATE_HZ)):
                        source_columns.append(_place_channel(
                            values, len(target_channels[0]), edge.get("attributes") or {}, offset))
                        labels.append(f"{edge['hash']}:ch{channel + 1}")
                # Highly correlated or unidentifiable source channels (including
                # the two channels of a dual-mono or panned stem) make the
                # individual matrix entries arbitrary, so the matrix is withheld.
                # The fitted reconstruction is the projection of the target onto
                # the span of the columns, which stays unique, so the residual
                # verdict below is still assessed.
                gram = _source_gram(source_columns, range(len(target_channels[0])))
                max_source_correlation = _max_source_correlation(gram)
                source_identifiability = _source_identifiability(gram)
                if max_source_correlation >= MAX_SOURCE_CORRELATION:
                    matrix_withheld_reason = (
                        "The declared source channels are too highly correlated to "
                        "attribute reliable individual channel gains.")
                elif source_identifiability < MIN_SOURCE_IDENTIFIABILITY:
                    matrix_withheld_reason = (
                        "The declared source channels cannot be distinguished well "
                        "enough to attribute reliable individual channel gains.")
                else:
                    matrix_withheld_reason = None
                channel_results, matrix = [], []
                for target_channel in target_channels:
                    fitted = _fit_linear(target_channel, source_columns)
                    if fitted is None:
                        raise ValueError("channel matrix is singular or target is silent")
                    matrix.append([round(value, 6) for value in fitted["gains"]])
                    channel_results.append(fitted)
                rmse = sum(item["normalized_rmse"] for item in channel_results) / len(channel_results)
                correlation = sum(item["correlation"] for item in channel_results) / len(channel_results)
                peak_amplitude = max(
                    (float(np.max(np.abs(channel))) for channel in target_channels
                     if len(channel)), default=0.0)
                if target_audio.get("sample_format") == "float":
                    level_fields = {"peak_amplitude": round(peak_amplitude, 6)}
                else:
                    clipping = (
                        sum(int(np.count_nonzero(np.abs(channel) >= 0.999))
                            for channel in target_channels) /
                        max(1, sum(len(channel) for channel in target_channels))
                    )
                    level_fields = {
                        "clipping_fraction": round(clipping, 6),
                        "peak_amplitude": round(peak_amplitude, 6),
                    }
                window = max(1, RESIDUAL_RATE_HZ // 4)
                window_rmse = []
                for start in range(0, len(target_channels[0]), window):
                    residual, energy = 0.0, 0.0
                    for channel, fitted in zip(target_channels, channel_results):
                        actual = np.asarray(channel[start:start + window], dtype=np.float64)
                        predicted = np.asarray(
                            fitted["predicted"][start:start + window], dtype=np.float64)
                        difference = actual - predicted
                        residual += float(difference @ difference)
                        energy += float(actual @ actual)
                    if energy > 1e-12:
                        window_rmse.append(math.sqrt(residual / energy))
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "reason": f"Stem/mix residual analysis unavailable: {exc}",
                                **_resource_fields(exc)})
                continue
            if rmse <= 0.05 and correlation >= 0.98:
                status, reason = "matched", "An estimated channel matrix reconstructs the target with low residual."
            elif rmse <= 0.25 and correlation >= 0.70:
                status, reason = "corroborated", "The source-channel model explains substantial target content."
            elif rmse >= 0.65 or correlation < 0.20:
                status, reason = "contradicted", "The submitted sources do not explain the target under the bounded matrix model."
            else:
                status, reason = "not_applicable", "Residual evidence is inconclusive or reflects undocumented processing."
            if matrix_withheld_reason is None:
                matrix_fields = {"estimated_channel_matrix": matrix}
            else:
                # The verdict then describes the declared sources jointly only.
                if status == "matched":
                    reason = ("The declared source channels jointly reconstruct the target "
                              "with low residual; no individual source is attributed.")
                elif status == "corroborated":
                    reason = ("The declared source channels jointly explain substantial "
                              "target content; no individual source is attributed.")
                matrix_fields = {
                    "channel_matrix_withheld_reason": matrix_withheld_reason,
                    "max_source_correlation": round(max_source_correlation, 6),
                    "source_identifiability": round(source_identifiability, 8)}
            results.append({**base, "status": status, "reason": reason,
                            "source_channel_labels": labels,
                            **matrix_fields,
                            "estimated_parameters_are_diagnostic": True,
                            "normalized_rmse": round(rmse, 6),
                            "correlation": round(correlation, 6),
                            **level_fields,
                            "window_normalized_rmse": [round(value, 6)
                                                       for value in window_rmse[:240]],
                            **_proxy_fields(target_audio, *source_audio_objects),
                            "analysis_sample_rate_hz": RESIDUAL_RATE_HZ})
        return results


class MasteringDerivationPass:
    """Public name for the existing conservative mix-to-master continuity pass."""

    @staticmethod
    def evaluate(native, chain):
        return [{**item, "pass": "MasteringDerivationPass",
                 "legacy_pass": "MasterDerivationPass"}
                for item in MasterDerivationPass.evaluate(native, chain)]


class ExcerptedFromPass:
    """Search for a submitted sample inside its declared source recording."""

    @staticmethod
    def evaluate(native, chain):
        results = []
        for source, target, edge in _audio_edges(native, {"excerpted_from"}):
            base = _base("audio_excerpted_from", "ExcerptedFromPass",
                         target["artefact_hash"], [source["artefact_hash"]])
            try:
                source_audio = _read(chain, source["artefact_hash"])
                target_audio = _read(chain, target["artefact_hash"])
                alignment = _best_feature_alignment(source_audio, target_audio)
                if alignment is None:
                    raise ValueError("no bounded landmark alignment was found")
                source_samples, target_samples, parameter_source = _declared_or_aligned_segments(
                    source_audio, target_audio, edge.get("attributes") or {}, alignment)
                waveform = abs(_correlation(source_samples, target_samples))
                source_features, target_features = _features(source_samples), _features(target_samples)
                landmarks = _sequence_similarity(source_features, target_features)
                dtw = _dtw_similarity(source_features, target_features)
                robust = 0.40 * waveform + 0.30 * landmarks + 0.30 * dtw
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "reason": f"Excerpt comparison unavailable: {exc}",
                                **_resource_fields(exc)})
                continue
            if alignment["ambiguous"]:
                status, reason = "not_applicable", "The excerpt appears at several similarly supported source locations."
            elif waveform >= 0.97:
                status, reason = "matched", "The sample waveform matches a source excerpt."
            elif robust >= 0.70:
                status, reason = "corroborated", "Landmarks and DTW support the declared excerpt relationship."
            elif robust <= 0.25:
                status, reason = "contradicted", "No matching source excerpt was found within the bounded search."
            else:
                status, reason = "not_applicable", "The excerpt comparison is inconclusive."
            results.append({**base, "status": status, "reason": reason,
                            "source_hash": source["artefact_hash"],
                            "parameter_source": parameter_source,
                            "estimated_source_start_seconds": round(
                                alignment["source_start_seconds"], 6),
                            "estimated_target_start_seconds": round(
                                alignment["target_start_seconds"], 6),
                            "alignment_ambiguity_margin": round(
                                alignment["ambiguity_margin"], 6),
                            "waveform_correlation": round(waveform, 6),
                            "landmark_similarity": round(landmarks, 6),
                            "dtw_similarity": round(dtw, 6),
                            **_proxy_fields(source_audio, target_audio),
                            "robust_similarity": round(robust, 6)})
        return results
