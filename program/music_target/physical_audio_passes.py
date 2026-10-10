"""Bounded signal-content checks for real audio fixtures.

These passes corroborate or challenge submitted relationship claims.  They do
not create graph edges and never turn acoustic similarity into proof of
authorship or historical provenance.  Expensive searches are deliberately
downsampled and bounded so a request cannot trigger unbounded DSP work.
"""

from __future__ import annotations

import math
from pathlib import Path

from music_target.audio_derivation import (
    MAX_SOURCE_CORRELATION,
    MIN_SOURCE_IDENTIFIABILITY,
    COMPLEX_PROCESSING_WORDS,
    MasterDerivationPass,
    _correlation,
    _max_source_correlation,
    _mono_resample,
    _read_pcm,
    _resample_series,
    _rms,
    _source_gram,
    _source_identifiability,
)


ALIGNMENT_RATE_HZ = 200
FEATURE_RATE_HZ = 25
CONTRIBUTION_RATE_HZ = 400
RESIDUAL_RATE_HZ = 1000
MAX_SEARCH_SECONDS = 60
MAX_SEARCH_FRAMES = FEATURE_RATE_HZ * MAX_SEARCH_SECONDS
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


def _is_audio(node):
    kind = node.get("artefact_type", "")
    return kind == "audio" or kind.startswith("audio/")


def _node_index(native):
    return {item["artefact_hash"]: item for item in native["artefacts"]}


def _audio_edges(native, relationships=AUDIO_RELATIONSHIPS):
    nodes = _node_index(native)
    for target in native["artefacts"]:
        if not _is_audio(target):
            continue
        for edge in target.get("evidence", []):
            source = nodes.get(edge["hash"])
            if source and _is_audio(source) and edge["relationship_type"] in relationships:
                yield source, target, edge


def _read(chain, digest):
    artefact = chain.artefacts[digest]
    if not artefact.has_file():
        raise ValueError("no verified file is bound to the audio artefact")
    return _read_pcm(Path(artefact.get_file()))


def _standardize(rows):
    if not rows:
        return []
    width = len(rows[0])
    means = [sum(row[column] for row in rows) / len(rows)
             for column in range(width)]
    scales = []
    for column in range(width):
        variance = sum((row[column] - means[column]) ** 2 for row in rows) / len(rows)
        scales.append(math.sqrt(variance) if variance > 1e-12 else 1.0)
    return [tuple((row[column] - means[column]) / scales[column]
                  for column in range(width)) for row in rows]


def _features(samples, sample_rate=ALIGNMENT_RATE_HZ):
    """Return gain-tolerant short-time energy/shape landmarks."""
    frame_size = max(4, round(sample_rate / FEATURE_RATE_HZ))
    rows = []
    for start in range(0, len(samples) - frame_size + 1, frame_size):
        frame = samples[start:start + frame_size]
        energy = math.sqrt(sum(value * value for value in frame) / len(frame))
        differences = [right - left for left, right in zip(frame, frame[1:])]
        difference_rms = _rms(differences)
        zero_crossings = sum((left < 0) != (right < 0)
                             for left, right in zip(frame, frame[1:]))
        peak = max(abs(value) for value in frame)
        rows.append((math.log10(energy + 1e-9),
                     difference_rms / max(energy, 1e-9),
                     zero_crossings / max(len(frame) - 1, 1),
                     peak / max(energy, 1e-9)))
    return _standardize(rows[:MAX_SEARCH_FRAMES])


def _cosine(left, right):
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator if denominator > 1e-12 else 0.0


def _short_correlation(left, right):
    """Correlation for short declared edits; eight bounded samples are enough."""
    count = min(len(left), len(right))
    if count < 8:
        return 0.0
    left, right = left[:count], right[:count]
    left_mean, right_mean = sum(left) / count, sum(right) / count
    numerator = sum((a - left_mean) * (b - right_mean)
                    for a, b in zip(left, right))
    left_energy = sum((value - left_mean) ** 2 for value in left)
    right_energy = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_energy * right_energy)
    return numerator / denominator if denominator > 1e-12 else 0.0


def _sequence_similarity(left, right):
    count = min(len(left), len(right))
    if count < 4:
        return 0.0
    similarities = [_cosine(left[index], right[index]) for index in range(count)]
    return max(0.0, min(1.0, (sum(similarities) / count + 1.0) / 2.0))


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
    source_features, target_features = _features(source), _features(target)
    if len(source_features) < 4 or len(target_features) < 4:
        return None
    source_is_query = len(source_features) <= len(target_features)
    query = source_features if source_is_query else target_features
    haystack = target_features if source_is_query else source_features
    candidates = []
    candidate_count = len(haystack) - len(query) + 1
    candidate_step = max(1, math.ceil(candidate_count / 600))
    feature_step = max(1, math.ceil(len(query) / 250))

    def score_at(start):
        comparison = haystack[start:start + len(query)]
        return _sequence_similarity(query[::feature_step], comparison[::feature_step])

    for start in range(0, candidate_count, candidate_step):
        candidates.append((score_at(start), start))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    coarse_start = candidates[0][1]
    already_scored = {start for _, start in candidates}
    for start in range(max(0, coarse_start - candidate_step),
                       min(candidate_count, coarse_start + candidate_step + 1)):
        if start not in already_scored:
            candidates.append((score_at(start), start))
    candidates.sort(reverse=True)
    best_score, best_start = candidates[0]
    separated = [item for item in candidates[1:]
                 if abs(item[1] - best_start) >= max(2, FEATURE_RATE_HZ // 4)]
    second_score = separated[0][0] if separated else 0.0
    if source_is_query:
        source_start, target_start = 0.0, best_start / FEATURE_RATE_HZ
    else:
        source_start, target_start = best_start / FEATURE_RATE_HZ, 0.0
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
    size = len(vector)
    augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [current - factor * base
                              for current, base in zip(augmented[row], augmented[column])]
    return [augmented[row][-1] for row in range(size)]


def _fit_linear(target, sources):
    if not target or not sources or any(len(source) != len(target) for source in sources):
        return None
    gram = [[0.0 for _ in sources] for _ in sources]
    projection = [0.0 for _ in sources]
    for index, actual in enumerate(target):
        values = [source[index] for source in sources]
        for left, value in enumerate(values):
            projection[left] += value * actual
            for right in range(left, len(values)):
                gram[left][right] += value * values[right]
                gram[right][left] = gram[left][right]
    ridge = max(sum(gram[index][index] for index in range(len(sources))) *
                RIDGE_FACTOR, 1e-12)
    for index in range(len(sources)):
        gram[index][index] += ridge
    gains = _solve(gram, projection)
    if gains is None:
        return None
    predicted = [sum(gain * source[index] for gain, source in zip(gains, sources))
                 for index in range(len(target))]
    energy = sum(value * value for value in target)
    if energy < 1e-12:
        return None
    residual = sum((actual - estimate) ** 2
                   for actual, estimate in zip(target, predicted))
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
    output = [0.0] * len(target)
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
    values = []
    size = max(1, len(target) // windows)
    for start in range(0, len(target), size):
        left, right = source[start:start + size], target[start:start + size]
        denominator = sum(value * value for value in left)
        if denominator > 1e-12:
            values.append(sum(a * b for a, b in zip(left, right)) / denominator)
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
                results.append({**base, "status": "unavailable", "reason": str(exc)})
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
            if len(edges) > MAX_CONTRIBUTION_SOURCES:
                results.append({**base, "status": "not_applicable",
                                "reason": "The bounded contribution analysis supports at most 16 sources."})
                continue
            try:
                target_audio = _read(chain, target["artefact_hash"])
                target_samples = _mono_resample(target_audio, CONTRIBUTION_RATE_HZ)
                aligned, parameter_sources = [], []
                for edge in edges:
                    vector, parameter_source = _aligned_source_vector(
                        _read(chain, edge["hash"]), target_audio,
                        edge.get("attributes") or {})
                    aligned.append(vector)
                    parameter_sources.append(parameter_source)
                # Mirror audio_derivation.py's _linear_status guard: a least-squares
                # fit over highly correlated or otherwise unidentifiable sources can
                # reach a good reconstruction residual while attributing that residual
                # to the wrong source, or arbitrarily among several. Decline to report
                # a per-source fit at all when the declared sources, as a group, are
                # not well enough separated to trust individual gains from -- rather
                # than silently reporting an unreliable corroborated/not_supported
                # conclusion for any of them.
                gram = _source_gram(aligned, range(len(target_samples)))
                max_source_correlation = _max_source_correlation(gram)
                source_identifiability = _source_identifiability(gram)
                if max_source_correlation >= MAX_SOURCE_CORRELATION:
                    guard_reason = ("The declared sources are too highly correlated to "
                                    "attribute unique contributions reliably.")
                elif source_identifiability < MIN_SOURCE_IDENTIFIABILITY:
                    guard_reason = ("The declared sources cannot be distinguished well "
                                    "enough to attribute unique contributions reliably.")
                else:
                    guard_reason = None
                if guard_reason is not None:
                    for edge in edges:
                        results.append({**base, "status": "not_applicable", "reason": guard_reason,
                                        "source_hash": edge["hash"],
                                        "relationship_type": edge["relationship_type"],
                                        "max_source_correlation": round(max_source_correlation, 6),
                                        "source_identifiability": round(source_identifiability, 8),
                                        "estimated_parameters_are_diagnostic": True})
                    continue
                full = _fit_linear(target_samples, aligned)
                if full is None:
                    raise ValueError("linear contribution model is singular or silent")
                for index, edge in enumerate(edges):
                    reduced_sources = aligned[:index] + aligned[index + 1:]
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
                                    "estimated_parameters_are_diagnostic": True})
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "reason": f"Contribution analysis unavailable: {exc}"})
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
                            "estimated_gain": contribution.get("estimated_gain")})
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
                    source, target, _ = _declared_or_aligned_segments(
                        _read(chain, source_hash), _read(chain, target_hash), attrs)
                    waveform = abs(_short_correlation(source, target))
                    source_features, target_features = _features(source), _features(target)
                    landmarks = _sequence_similarity(source_features, target_features)
                    dtw = _dtw_similarity(source_features, target_features)
                    robust = 0.35 * waveform + 0.30 * landmarks + 0.35 * dtw
                except (KeyError, OSError, ValueError, TypeError) as exc:
                    results.append({**base, "status": "unavailable",
                                    "selection_id": selection.get("selection_id"),
                                    "reason": f"Comp selection comparison unavailable: {exc}"})
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
                source_envelope = [row[0] for row in source_features]
                target_envelope = [row[0] for row in target_features]
                envelope = abs(_correlation(source_envelope, target_envelope))
                robust = 0.20 * waveform + 0.30 * landmarks + 0.30 * dtw + 0.20 * envelope
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "reason": f"Processed-audio comparison unavailable: {exc}"})
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
                            "robust_similarity": round(robust, 6)})
        return results


def _channel_vectors(audio, output_rate):
    # Reuses the same anti-aliased decimation audio_derivation.py's
    # _mono_resample applies, one channel at a time (mixdown-then-filter
    # and filter-then-mixdown are equivalent for a linear, per-sample-
    # identical filter, so this is not a behavioural split from mono).
    rate = audio["sample_rate_hz"]
    return [_resample_series([frame[channel] for frame in audio["frames"]], rate, output_rate)
            for channel in range(audio["channels"])]


def _place_channel(source, target_count, attrs, offset_seconds=0.0,
                   rate=RESIDUAL_RATE_HZ):
    output = [0.0] * target_count
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
            try:
                target_audio = _read(chain, target["artefact_hash"])
                target_channels = _channel_vectors(target_audio, RESIDUAL_RATE_HZ)
                source_columns, labels = [], []
                for edge in edges:
                    source_audio = _read(chain, edge["hash"])
                    alignment = _best_feature_alignment(source_audio, target_audio)
                    offset = alignment["estimated_offset_seconds"] if alignment else 0.0
                    for channel, values in enumerate(_channel_vectors(source_audio,
                                                                       RESIDUAL_RATE_HZ)):
                        source_columns.append(_place_channel(
                            values, len(target_channels[0]), edge.get("attributes") or {}, offset))
                        labels.append(f"{edge['hash']}:ch{channel + 1}")
                # Same guard as SourceContributionPass: a channel-matrix fit over
                # highly correlated or unidentifiable source channels can reach a
                # low residual while the estimated matrix itself is numerically
                # fragile and not attributable to any particular source. Decline to
                # report a matrix at all in that case, rather than an unreliable
                # matched/corroborated/contradicted conclusion.
                gram = _source_gram(source_columns, range(len(target_channels[0])))
                max_source_correlation = _max_source_correlation(gram)
                source_identifiability = _source_identifiability(gram)
                if max_source_correlation >= MAX_SOURCE_CORRELATION:
                    guard_reason = ("The declared sources are too highly correlated to "
                                    "attribute a reliable channel matrix.")
                elif source_identifiability < MIN_SOURCE_IDENTIFIABILITY:
                    guard_reason = ("The declared sources cannot be distinguished well "
                                    "enough to attribute a reliable channel matrix.")
                else:
                    guard_reason = None
                if guard_reason is not None:
                    results.append({**base, "status": "not_applicable", "reason": guard_reason,
                                    "source_channel_labels": labels,
                                    "max_source_correlation": round(max_source_correlation, 6),
                                    "source_identifiability": round(source_identifiability, 8),
                                    "estimated_parameters_are_diagnostic": True})
                    continue
                channel_results, matrix = [], []
                for target_channel in target_channels:
                    fitted = _fit_linear(target_channel, source_columns)
                    if fitted is None:
                        raise ValueError("channel matrix is singular or target is silent")
                    matrix.append([round(value, 6) for value in fitted["gains"]])
                    channel_results.append(fitted)
                rmse = sum(item["normalized_rmse"] for item in channel_results) / len(channel_results)
                correlation = sum(item["correlation"] for item in channel_results) / len(channel_results)
                clipping = (
                    sum(abs(value) >= 0.999
                        for channel in target_channels for value in channel) /
                    max(1, sum(len(channel) for channel in target_channels))
                )
                window = max(1, RESIDUAL_RATE_HZ // 4)
                window_rmse = []
                for start in range(0, len(target_channels[0]), window):
                    residual, energy = 0.0, 0.0
                    for channel, fitted in zip(target_channels, channel_results):
                        actual = channel[start:start + window]
                        predicted = fitted["predicted"][start:start + window]
                        residual += sum((a - b) ** 2 for a, b in zip(actual, predicted))
                        energy += sum(value * value for value in actual)
                    if energy > 1e-12:
                        window_rmse.append(math.sqrt(residual / energy))
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "reason": f"Stem/mix residual analysis unavailable: {exc}"})
                continue
            if rmse <= 0.05 and correlation >= 0.98:
                status, reason = "matched", "An estimated channel matrix reconstructs the target with low residual."
            elif rmse <= 0.25 and correlation >= 0.70:
                status, reason = "corroborated", "The source-channel model explains substantial target content."
            elif rmse >= 0.65 or correlation < 0.20:
                status, reason = "contradicted", "The submitted sources do not explain the target under the bounded matrix model."
            else:
                status, reason = "not_applicable", "Residual evidence is inconclusive or reflects undocumented processing."
            results.append({**base, "status": status, "reason": reason,
                            "source_channel_labels": labels,
                            "estimated_channel_matrix": matrix,
                            "estimated_parameters_are_diagnostic": True,
                            "normalized_rmse": round(rmse, 6),
                            "correlation": round(correlation, 6),
                            "clipping_fraction": round(clipping, 6),
                            "window_normalized_rmse": [round(value, 6)
                                                       for value in window_rmse[:240]],
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
                                "reason": f"Excerpt comparison unavailable: {exc}"})
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
                            "robust_similarity": round(robust, 6)})
        return results
