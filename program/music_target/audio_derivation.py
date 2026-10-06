"""Conservative PCM WAV content-derivation assessments.

Each pass verifies only a narrow, declared transformation model. A poor fit
contradicts that model; it does not prove that a source was never used through
some undocumented or nonlinear production process.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

MAX_SOURCES = 16
MAX_COMP_SELECTIONS = 64
MAX_ANALYSIS_SECONDS = 60
ANALYSIS_RATE_HZ = 2000
MAX_OFFSET_SECONDS = 5.0
MIN_TARGET_COVERAGE = 0.90
MAX_SOURCE_CORRELATION = 0.995
MIN_SOURCE_IDENTIFIABILITY = 1e-4
HOLDOUT_BLOCK_SECONDS = 0.05
SEGMENT_SCAN_RATE_HZ = 100
SEGMENT_AMBIGUITY_CORRELATION = 0.999
MASTER_ALIGNMENT_MARGIN = 0.02
MASTER_PEAK_EXCLUSION_SECONDS = 0.25
COMPLEX_PROCESSING_WORDS = {
    "compress", "limiter", "master", "time stretch", "pitch shift",
    "distortion", "saturat", "reverb", "nonlinear",
}
LINEAR_DECLARATION_FIELDS = (
    "source_start_seconds", "source_end_seconds",
    "target_start_seconds", "target_end_seconds", "gain",
)
MASTER_DECLARATION_FIELDS = (
    "source_start_seconds", "source_end_seconds",
    "target_start_seconds", "target_end_seconds",
)


def _pcm_value(raw: bytes, width: int) -> float:
    if width == 1:
        return (raw[0] - 128) / 128.0
    if width == 2:
        return struct.unpack("<h", raw)[0] / 32768.0
    if width == 3:
        value = int.from_bytes(raw, "little", signed=False)
        if value & 0x800000:
            value -= 1 << 24
        return value / 8388608.0
    if width == 4:
        return struct.unpack("<i", raw)[0] / 2147483648.0
    raise ValueError("Only 8/16/24/32-bit integer PCM WAV is supported.")


def _read_pcm(path: Path):
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getcomptype() != "NONE":
                raise ValueError("compressed WAV is not supported")
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frame_count = min(handle.getnframes(), rate * MAX_ANALYSIS_SECONDS)
            raw = handle.readframes(frame_count)
    except wave.Error as exc:
        raise ValueError(f"invalid WAV: {exc}") from exc
    if channels < 1 or rate < 1 or width not in (1, 2, 3, 4):
        raise ValueError("unsupported PCM channel, rate, or sample width")
    stride = channels * width
    if len(raw) % stride:
        raise ValueError("truncated PCM frame data")
    frames = []
    for position in range(0, len(raw), stride):
        frames.append(tuple(_pcm_value(raw[position + channel * width:
                                           position + (channel + 1) * width], width)
                            for channel in range(channels)))
    return {"frames": frames, "sample_rate_hz": rate, "channels": channels,
            "bit_depth": width * 8}


def _source_gram(sources, indices):
    gram = [[0.0 for _ in sources] for _ in sources]
    for index in indices:
        values = [source[index] for source in sources]
        for left, value in enumerate(values):
            for right in range(left, len(values)):
                gram[left][right] += value * values[right]
                gram[right][left] = gram[left][right]
    return gram


def _source_identifiability(gram):
    """Return a scale-free pivot ratio for distinguishing source contributions."""
    size = len(gram)
    if not size:
        return 0.0
    normalized = [[0.0 for _ in range(size)] for _ in range(size)]
    for row in range(size):
        for column in range(size):
            denominator = math.sqrt(max(gram[row][row], 0.0) *
                                    max(gram[column][column], 0.0))
            normalized[row][column] = gram[row][column] / denominator if denominator > 1e-12 else 0.0
    pivots = []
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(normalized[row][column]))
        value = abs(normalized[pivot][column])
        if value < 1e-12:
            return 0.0
        normalized[column], normalized[pivot] = normalized[pivot], normalized[column]
        pivots.append(value)
        for row in range(column + 1, size):
            factor = normalized[row][column] / normalized[column][column]
            for current in range(column, size):
                normalized[row][current] -= factor * normalized[column][current]
    return min(pivots) / max(pivots)


def _max_source_correlation(gram):
    highest = 0.0
    for left in range(len(gram)):
        for right in range(left + 1, len(gram)):
            denominator = math.sqrt(max(gram[left][left], 0.0) *
                                    max(gram[right][right], 0.0))
            if denominator > 1e-12:
                highest = max(highest, abs(gram[left][right] / denominator))
    return highest


def _evaluate_declared_reconstruction(target, sources, indices):
    if not indices:
        return None
    actual = [target[index] for index in indices]
    predicted = [sum(source[index] for source in sources) for index in indices]
    target_energy = sum(value * value for value in actual)
    if target_energy < 1e-12:
        return None
    residual_energy = sum((value - estimate) ** 2
                          for value, estimate in zip(actual, predicted))
    target_mean = sum(actual) / len(actual)
    predicted_mean = sum(predicted) / len(predicted)
    covariance = sum((value - target_mean) * (estimate - predicted_mean)
                     for value, estimate in zip(actual, predicted))
    target_variance = sum((value - target_mean) ** 2 for value in actual)
    predicted_variance = sum((estimate - predicted_mean) ** 2 for estimate in predicted)
    denominator = math.sqrt(target_variance * predicted_variance)
    correlation = covariance / denominator if denominator > 1e-12 else 0.0
    return math.sqrt(residual_energy / target_energy), correlation


def _holdout_indices(count, block_size):
    fit = [index for index in range(count) if (index // block_size) % 2 == 0]
    validation = [index for index in range(count) if (index // block_size) % 2 == 1]
    return fit, validation


def _base(rule, pass_name, target, edges):
    return {
        "rule": rule,
        "pass": pass_name,
        "target_hash": target["artefact_hash"],
        "source_hashes": sorted({edge["hash"] for edge in edges}),
    }


def _missing_parameters(edges, fields):
    missing = []
    for edge in edges:
        attrs = edge.get("attributes") or {}
        absent = [field for field in fields if attrs.get(field) is None]
        if absent:
            missing.append({"source_hash": edge["hash"], "fields": absent})
    return missing


def _claim_status(status):
    if status == "matched":
        return "declared_supported"
    if status == "contradicted":
        return "declared_contradicted"
    return "declared_unverified"


def _processing_is_complex(edges):
    descriptions = " ".join(str((edge.get("attributes") or {}).get(
        "transformation_description") or "") for edge in edges).casefold()
    return any(word in descriptions for word in COMPLEX_PROCESSING_WORDS)


def _read_target_and_sources(target, edges, chain):
    target_audio = _read_pcm(Path(chain.artefacts[target["artefact_hash"]].get_file()))
    sources = [_read_pcm(Path(chain.artefacts[edge["hash"]].get_file()))
               for edge in edges]
    if any(item["sample_rate_hz"] != target_audio["sample_rate_hz"] or
           item["channels"] != target_audio["channels"] for item in sources):
        raise ValueError("source and target sample rates/channels must match")
    return target_audio, sources


def _linear_timeline_metrics(target_audio, edge_audio, group_keys=None):
    """Evaluate only submitter-declared ranges and gains on alternating blocks."""
    rate = target_audio["sample_rate_hz"]
    channels = target_audio["channels"]
    step = max(1, rate // ANALYSIS_RATE_HZ)
    analysis_rate = rate / step
    target_indices = list(range(0, len(target_audio["frames"]), step))
    target_samples = [sample for index in target_indices
                      for sample in target_audio["frames"][index]]
    grouped = {}
    offsets = []
    declared_gains = []
    covered_frames = [False] * len(target_indices)
    for position, (edge, audio) in enumerate(edge_audio):
        attrs = edge.get("attributes") or {}
        source_start = float(attrs["source_start_seconds"])
        target_start = float(attrs["target_start_seconds"])
        source_end = float(attrs["source_end_seconds"])
        target_end = float(attrs["target_end_seconds"])
        gain = float(attrs["gain"])
        if not math.isfinite(gain):
            raise ValueError("declared gain must be finite")
        if min(source_start, target_start) < 0 or source_end <= source_start or target_end <= target_start:
            raise ValueError("declared source/target ranges must be positive and ordered")
        if abs((source_end - source_start) - (target_end - target_start)) > max(0.002, 2 / rate):
            raise ValueError("declared ranges imply time stretching")
        if source_end > len(audio["frames"]) / rate + 2 / rate:
            raise ValueError("declared source range exceeds the analysed audio")
        if target_end > len(target_audio["frames"]) / rate + 2 / rate:
            raise ValueError("declared target range exceeds the analysed audio")
        fade_in = float(attrs.get("fade_in_seconds") or 0)
        fade_out = float(attrs.get("fade_out_seconds") or 0)
        target_duration = target_end - target_start
        if min(fade_in, fade_out) < 0 or fade_in + fade_out > target_duration + 2 / rate:
            raise ValueError("declared fades must fit within the target range")
        offsets.append(target_start - source_start)
        declared_gains.append(gain)
        aligned = []
        silence = (0.0,) * channels
        for frame_position, target_index in enumerate(target_indices):
            target_time = target_index / rate
            frame = silence
            if target_start <= target_time < target_end:
                source_time = source_start + target_time - target_start
                source_index = round(source_time * rate)
                if 0 <= source_index < len(audio["frames"]):
                    covered_frames[frame_position] = True
                    envelope = 1.0
                    relative_time = target_time - target_start
                    if fade_in:
                        envelope = min(envelope, relative_time / fade_in)
                    if fade_out:
                        envelope = min(envelope, (target_end - target_time) / fade_out)
                    frame = tuple(value * gain * max(0.0, min(1.0, envelope))
                                  for value in audio["frames"][source_index])
            aligned.extend(frame)
        key = group_keys[position] if group_keys else position
        if key in grouped:
            grouped[key] = [left + right for left, right in zip(grouped[key], aligned)]
        else:
            grouped[key] = aligned
    if len(target_indices) < 32 or not grouped:
        return None
    sources = list(grouped.values())
    block_size = max(32 * channels,
                     round(analysis_rate * HOLDOUT_BLOCK_SECONDS) * channels)
    fit_indices, validation_indices = _holdout_indices(len(target_samples), block_size)
    gram = _source_gram(sources, fit_indices)
    identifiability = _source_identifiability(gram)
    source_correlation = _max_source_correlation(gram)
    partition_metrics = _evaluate_declared_reconstruction(
        target_samples, sources, fit_indices)
    validation_metrics = _evaluate_declared_reconstruction(
        target_samples, sources, validation_indices)
    if partition_metrics is None or validation_metrics is None:
        return None

    predicted = [sum(source[index] for source in sources)
                 for index in range(len(target_samples))]
    block_frames = max(1, round(analysis_rate * HOLDOUT_BLOCK_SECONDS))
    matched_frames = 0
    for frame_start in range(0, len(target_indices), block_frames):
        frame_end = min(len(target_indices), frame_start + block_frames)
        covered = sum(covered_frames[frame_start:frame_end])
        if covered == 0:
            continue
        sample_start, sample_end = frame_start * channels, frame_end * channels
        actual = target_samples[sample_start:sample_end]
        estimate = predicted[sample_start:sample_end]
        energy = sum(value * value for value in actual)
        residual = sum((value - comparison) ** 2
                       for value, comparison in zip(actual, estimate))
        if ((energy < 1e-12 and residual < 1e-12) or
                (energy >= 1e-12 and math.sqrt(residual / energy) <= 0.05)):
            matched_frames += covered

    covered_count = sum(covered_frames)
    target_duration = len(target_indices) / analysis_rate
    return {
        "declared_gains": declared_gains,
        "offsets_seconds": offsets,
        "analysis_sample_rate_hz": round(analysis_rate, 3),
        "declaration_partition_normalized_rmse": partition_metrics[0],
        "declaration_partition_correlation": partition_metrics[1],
        "validation_normalized_rmse": validation_metrics[0],
        "validation_correlation": validation_metrics[1],
        "source_identifiability": identifiability,
        "max_source_correlation": source_correlation,
        "target_duration_seconds": target_duration,
        "covered_duration_seconds": covered_count / analysis_rate,
        "coverage_ratio": covered_count / len(target_indices),
        "matched_duration_seconds": matched_frames / analysis_rate,
        "matched_coverage_ratio": matched_frames / len(target_indices),
    }


def _linear_status(assessment, match_rmse, match_correlation,
                   contradiction_rmse=0.35, contradiction_correlation=0.5):
    if assessment["coverage_ratio"] < MIN_TARGET_COVERAGE:
        return "not_applicable", "insufficient_target_coverage"
    if assessment["max_source_correlation"] >= MAX_SOURCE_CORRELATION:
        return "not_applicable", "redundant_or_highly_correlated_sources"
    if assessment["source_identifiability"] < MIN_SOURCE_IDENTIFIABILITY:
        return "not_applicable", "source_contributions_not_identifiable"
    normalized_rmse = assessment["validation_normalized_rmse"]
    correlation = assessment["validation_correlation"]
    if normalized_rmse <= match_rmse and correlation >= match_correlation:
        return "matched", None
    if normalized_rmse >= contradiction_rmse or correlation < contradiction_correlation:
        return "contradicted", None
    return "not_applicable", "inconclusive_declared_reconstruction"


def _segment_alternative_locations(audio, attrs):
    """Find near-identical alternative positions for an explicitly selected segment."""
    if attrs.get("source_start_seconds") is None or attrs.get("source_end_seconds") is None:
        return []
    start = float(attrs["source_start_seconds"])
    end = float(attrs["source_end_seconds"])
    samples = _mono_resample(audio, SEGMENT_SCAN_RATE_HZ)
    first = round(start * SEGMENT_SCAN_RATE_HZ)
    last = round(end * SEGMENT_SCAN_RATE_HZ)
    template = samples[first:last]
    if len(template) < 25 or len(samples) < len(template) + 25:
        return []
    stride = max(1, round(0.05 * SEGMENT_SCAN_RATE_HZ))
    exclusion = max(stride, round(0.1 * SEGMENT_SCAN_RATE_HZ))
    alternatives = []
    for candidate in range(0, len(samples) - len(template) + 1, stride):
        if abs(candidate - first) <= exclusion:
            continue
        comparison = samples[candidate:candidate + len(template)]
        if _correlation(template, comparison) >= SEGMENT_AMBIGUITY_CORRELATION:
            alternatives.append(round(candidate / SEGMENT_SCAN_RATE_HZ, 3))
            if len(alternatives) >= 5:
                break
    return alternatives


def _linear_pass(native, chain, *, relationship, target_type, scopes, rule,
                 pass_name, match_rmse, match_correlation,
                 check_segment_ambiguity=False):
    results = []
    for target in native["artefacts"]:
        edges = [edge for edge in target.get("evidence", [])
                 if edge["relationship_type"] == relationship]
        if not edges:
            continue
        base = _base(rule, pass_name, target, edges)
        if target["artefact_type"] != target_type:
            results.append({**base, "status": "not_applicable",
                            "reason": f"Target is not declared as {target_type}."})
            continue
        missing = _missing_parameters(
            edges, ("derivation_scope",) + LINEAR_DECLARATION_FIELDS)
        if missing:
            results.append({
                **base,
                "status": "unavailable",
                "claim_status": "declared_unverified",
                "reason": "The relationship was declared, but the submitted parameters are insufficient for content reconstruction.",
                "reason_code": "missing_derivation_parameters",
                "missing_parameters": missing,
                "content_match_is_provenance": False,
            })
            continue
        declared_scopes = {(edge.get("attributes") or {}).get("derivation_scope")
                           for edge in edges}
        if not declared_scopes.issubset(scopes):
            expected = " or ".join(sorted(scopes))
            results.append({**base, "status": "not_applicable",
                            "reason": f"Every {relationship} edge must explicitly declare derivation_scope={expected}."})
            continue
        if _processing_is_complex(edges):
            results.append({**base, "status": "not_applicable",
                            "reason": "The relationship declares processing outside the constant-gain PCM model."})
            continue
        if len(edges) > MAX_SOURCES:
            results.append({**base, "status": "not_applicable",
                            "reason": f"More than {MAX_SOURCES} sources exceeds the bounded analysis limit."})
            continue
        try:
            target_audio, sources = _read_target_and_sources(target, edges, chain)
            assessed = _linear_timeline_metrics(target_audio, list(zip(edges, sources)),
                                                [edge["hash"] for edge in edges])
            alternative_locations = []
            if check_segment_ambiguity:
                for edge, audio in zip(edges, sources):
                    alternatives = _segment_alternative_locations(
                        audio, edge.get("attributes") or {})
                    if alternatives:
                        alternative_locations.append({
                            "source_hash": edge["hash"],
                            "declared_start_seconds": (edge.get("attributes") or {}).get(
                                "source_start_seconds"),
                            "alternative_start_seconds": alternatives,
                        })
        except (OSError, ValueError, TypeError) as exc:
            results.append({**base, "status": "unavailable",
                            "claim_status": "declared_unverified",
                            "reason": f"PCM analysis unavailable: {exc}",
                            "content_match_is_provenance": False})
            continue
        if assessed is None:
            results.append({**base, "status": "unavailable",
                            "claim_status": "declared_unverified",
                            "reason": "There is insufficient non-silent audio for a stable comparison.",
                            "content_match_is_provenance": False})
            continue
        status, review_reason = _linear_status(assessed, match_rmse, match_correlation)
        if alternative_locations and status == "matched":
            status, review_reason = "not_applicable", "ambiguous_source_segment_location"
        if status == "matched":
            reason = "Held-out target blocks are reconstructed by the declared aligned linear sources within tolerance."
        elif status == "contradicted":
            reason = "Held-out target blocks contradict the explicitly declared aligned constant-gain model."
        elif review_reason == "insufficient_target_coverage":
            reason = "The declared sources do not cover enough of the target for a reliable match."
        elif review_reason == "redundant_or_highly_correlated_sources":
            reason = "The submitted sources are too highly correlated to identify stable independent gains."
        elif review_reason == "source_contributions_not_identifiable":
            reason = "The submitted sources cannot be distinguished well enough to verify their declared individual contributions."
        elif review_reason == "ambiguous_source_segment_location":
            reason = "The declared source segment has near-identical alternatives at other positions."
        else:
            reason = "The independent holdout fit is inconclusive; nonlinear or undocumented processing may be present."
        results.append({**base, "status": status,
                        "claim_status": _claim_status(status), "reason": reason,
                        "verified_scope": next(iter(declared_scopes)),
                        "declared_gains": [round(value, 6)
                                           for value in assessed["declared_gains"]],
                        "parameter_source": "submitter_declared",
                        "offsets_seconds": assessed["offsets_seconds"],
                        "normalized_rmse": round(assessed["validation_normalized_rmse"], 6),
                        "correlation": round(assessed["validation_correlation"], 6),
                        "declaration_partition_normalized_rmse": round(
                            assessed["declaration_partition_normalized_rmse"], 6),
                        "declaration_partition_correlation": round(
                            assessed["declaration_partition_correlation"], 6),
                        "validation_normalized_rmse": round(
                            assessed["validation_normalized_rmse"], 6),
                        "validation_correlation": round(assessed["validation_correlation"], 6),
                        "source_identifiability": round(
                            assessed["source_identifiability"], 8),
                        "gain_estimation_performed": False,
                        "max_source_correlation": round(
                            assessed["max_source_correlation"], 6),
                        "target_duration_seconds": round(
                            assessed["target_duration_seconds"], 6),
                        "covered_duration_seconds": round(
                            assessed["covered_duration_seconds"], 6),
                        "coverage_ratio": round(assessed["coverage_ratio"], 6),
                        "matched_duration_seconds": round(
                            assessed["matched_duration_seconds"], 6),
                        "matched_coverage_ratio": round(
                            assessed["matched_coverage_ratio"], 6),
                        "alternative_source_locations": alternative_locations,
                        "manual_review_reasons": [review_reason] if review_reason else [],
                        "content_match_is_provenance": False,
                        "analysis_sample_rate_hz": assessed["analysis_sample_rate_hz"]})
    return results


class AudioDerivationPass:
    """Assess explicitly declared, aligned linear PCM mixes."""

    @staticmethod
    def evaluate(native, chain):
        return _linear_pass(
            native, chain, relationship="mixed_from", target_type="audio/mix",
            scopes={"linear_mix"}, rule="audio_linear_mix_derivation",
            pass_name="AudioDerivationPass", match_rmse=0.02,
            match_correlation=0.995)


class EditDerivationPass:
    """Verify declared constant-gain crops, placements and simple splices."""

    @staticmethod
    def evaluate(native, chain):
        return _linear_pass(
            native, chain, relationship="edited_from", target_type="audio/edited-take",
            scopes={"linear_edit"}, rule="audio_linear_edit_derivation",
            pass_name="EditDerivationPass", match_rmse=0.03,
            match_correlation=0.99, check_segment_ambiguity=True)


class StemDerivationPass:
    """Verify stems declared as constant-gain sums of aligned source audio."""

    @staticmethod
    def evaluate(native, chain):
        return _linear_pass(
            native, chain, relationship="stemmed_from", target_type="audio/stem",
            scopes={"linear_stem", "linear_mix"}, rule="audio_linear_stem_derivation",
            pass_name="StemDerivationPass", match_rmse=0.025,
            match_correlation=0.993)


class CompDerivationPass:
    """Verify CompSheet selections against the audio/edited-take they describe."""

    @staticmethod
    def evaluate(native, chain, parser_observations):
        nodes = {node["artefact_hash"]: node for node in native["artefacts"]}
        sheets = [parsed["data"] for parsed in parser_observations.values()
                  if parsed.get("parser") == "comp-sheet" and parsed.get("data")]
        results = []
        for target in native["artefacts"]:
            graph_edges = [edge for edge in target.get("evidence", [])
                           if edge["relationship_type"] == "comped_from"]
            if not graph_edges:
                continue
            base = _base("audio_comp_sheet_derivation", "CompDerivationPass",
                         target, graph_edges)
            selections = []
            for sheet in sheets:
                default_target = (sheet.get("metadata") or {}).get("target_hash")
                selections.extend(selection for selection in sheet.get("selections", [])
                                  if (selection.get("target_hash") or default_target) ==
                                  target["artefact_hash"])
            if not selections:
                results.append({**base, "status": "unavailable",
                                "claim_status": "declared_unverified",
                                "reason": "No parsed CompSheet selections identify this edited take.",
                                "content_match_is_provenance": False})
                continue
            if len(selections) > MAX_COMP_SELECTIONS:
                results.append({**base, "status": "not_applicable",
                                "reason": f"More than {MAX_COMP_SELECTIONS} selections exceeds the bounded analysis limit."})
                continue
            allowed_sources = {edge["hash"] for edge in graph_edges}
            selected_sources = {selection.get("source_hash") for selection in selections}
            if None in selected_sources:
                results.append({**base, "status": "unavailable",
                                "claim_status": "declared_unverified",
                                "reason": "Every assessed CompSheet selection needs a source_hash.",
                                "reason_code": "missing_derivation_parameters",
                                "missing_parameters": ["source_hash"],
                                "content_match_is_provenance": False})
                continue
            missing_selections = []
            for selection in selections:
                absent = [field for field in
                          ("source_start_seconds", "source_end_seconds",
                           "target_start_seconds", "target_end_seconds", "gain")
                          if selection.get(field) is None]
                if absent:
                    missing_selections.append({
                        "selection_id": selection.get("selection_id"),
                        "fields": absent,
                    })
            if missing_selections:
                results.append({
                    **base,
                    "status": "unavailable",
                    "claim_status": "declared_unverified",
                    "reason": "The CompSheet was submitted, but one or more selections lack parameters required for content reconstruction.",
                    "reason_code": "missing_derivation_parameters",
                    "missing_parameters": missing_selections,
                    "content_match_is_provenance": False,
                })
                continue
            if not selected_sources.issubset(allowed_sources):
                results.append({**base, "status": "contradicted",
                                "reason": "A CompSheet source is not connected by a comped_from relationship.",
                                "selection_count": len(selections)})
                continue
            try:
                target_audio = _read_pcm(Path(chain.artefacts[target["artefact_hash"]].get_file()))
                entries = []
                alternative_locations = []
                for selection in selections:
                    source_hash = selection["source_hash"]
                    if source_hash not in nodes:
                        raise ValueError("a CompSheet source hash is absent from the graph")
                    source_audio = _read_pcm(Path(chain.artefacts[source_hash].get_file()))
                    if (source_audio["sample_rate_hz"] != target_audio["sample_rate_hz"] or
                            source_audio["channels"] != target_audio["channels"]):
                        raise ValueError("source and target sample rates/channels must match")
                    entries.append(({
                        "hash": source_hash,
                        "attributes": {
                            "source_start_seconds": selection["source_start_seconds"],
                            "source_end_seconds": selection["source_end_seconds"],
                            "target_start_seconds": selection["target_start_seconds"],
                            "target_end_seconds": selection["target_end_seconds"],
                            "gain": selection["gain"],
                        },
                    }, source_audio))
                    alternatives = _segment_alternative_locations(source_audio, {
                        "source_start_seconds": selection["source_start_seconds"],
                        "source_end_seconds": selection["source_end_seconds"],
                    })
                    if alternatives:
                        alternative_locations.append({
                            "selection_id": selection["selection_id"],
                            "source_hash": source_hash,
                            "declared_start_seconds": selection["source_start_seconds"],
                            "alternative_start_seconds": alternatives,
                        })
                assessed = _linear_timeline_metrics(
                    target_audio, entries, [selection["source_hash"] for selection in selections])
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "claim_status": "declared_unverified",
                                "reason": f"Comp audio analysis unavailable: {exc}",
                                "content_match_is_provenance": False})
                continue
            if assessed is None:
                results.append({**base, "status": "unavailable",
                                "claim_status": "declared_unverified",
                                "reason": "There is insufficient non-silent comp audio for a stable comparison.",
                                "content_match_is_provenance": False})
                continue
            status, review_reason = _linear_status(assessed, 0.04, 0.985)
            if alternative_locations and status == "matched":
                status, review_reason = "not_applicable", "ambiguous_source_segment_location"
            if status == "matched":
                reason = "Held-out target blocks are reconstructed from the declared CompSheet selections within tolerance."
            elif status == "contradicted":
                reason = "Held-out target blocks conflict with the declared CompSheet selection timeline."
            elif review_reason == "ambiguous_source_segment_location":
                reason = "A CompSheet selection has near-identical alternatives elsewhere in its source take."
            elif review_reason == "insufficient_target_coverage":
                reason = "The CompSheet selections do not cover enough of the edited take."
            elif review_reason == "redundant_or_highly_correlated_sources":
                reason = "The selected takes are too highly correlated to identify stable independent gains."
            elif review_reason == "source_contributions_not_identifiable":
                reason = "The selected takes cannot be distinguished well enough to verify their declared individual contributions."
            else:
                reason = "The independent CompSheet holdout fit is inconclusive; crossfades or undocumented processing may be present."
            results.append({**base, "status": status,
                            "claim_status": _claim_status(status), "reason": reason,
                            "verified_scope": "comp_sheet_selections",
                            "selection_count": len(selections),
                            "declared_gains": [round(value, 6)
                                               for value in assessed["declared_gains"]],
                            "parameter_source": "submitter_declared",
                            "offsets_seconds": assessed["offsets_seconds"],
                            "normalized_rmse": round(assessed["validation_normalized_rmse"], 6),
                            "correlation": round(assessed["validation_correlation"], 6),
                            "declaration_partition_normalized_rmse": round(
                                assessed["declaration_partition_normalized_rmse"], 6),
                            "declaration_partition_correlation": round(
                                assessed["declaration_partition_correlation"], 6),
                            "validation_normalized_rmse": round(
                                assessed["validation_normalized_rmse"], 6),
                            "validation_correlation": round(
                                assessed["validation_correlation"], 6),
                            "source_identifiability": round(
                                assessed["source_identifiability"], 8),
                            "gain_estimation_performed": False,
                            "max_source_correlation": round(
                                assessed["max_source_correlation"], 6),
                            "target_duration_seconds": round(
                                assessed["target_duration_seconds"], 6),
                            "covered_duration_seconds": round(
                                assessed["covered_duration_seconds"], 6),
                            "coverage_ratio": round(assessed["coverage_ratio"], 6),
                            "matched_duration_seconds": round(
                                assessed["matched_duration_seconds"], 6),
                            "matched_coverage_ratio": round(
                                assessed["matched_coverage_ratio"], 6),
                            "alternative_source_locations": alternative_locations,
                            "manual_review_reasons": [review_reason] if review_reason else [],
                            "content_match_is_provenance": False,
                            "analysis_sample_rate_hz": assessed["analysis_sample_rate_hz"]})
        return results


def _mono_resample(audio, output_rate):
    rate = audio["sample_rate_hz"]
    count = int(len(audio["frames"]) / rate * output_rate)
    result = []
    for index in range(count):
        source_index = min(len(audio["frames"]) - 1, round(index * rate / output_rate))
        frame = audio["frames"][source_index]
        result.append(sum(frame) / len(frame))
    return result


def _correlation(left, right):
    count = min(len(left), len(right))
    if count < 32:
        return 0.0
    left, right = left[:count], right[:count]
    left_mean, right_mean = sum(left) / count, sum(right) / count
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_energy = sum((a - left_mean) ** 2 for a in left)
    right_energy = sum((b - right_mean) ** 2 for b in right)
    denominator = math.sqrt(left_energy * right_energy)
    return numerator / denominator if denominator > 1e-12 else 0.0


def _declared_master_samples(source, target, attrs, rate=400):
    """Compare the declared ranges; alternatives may only reduce certainty."""
    source_all = _mono_resample(source, rate)
    target_all = _mono_resample(target, rate)
    source_start = float(attrs["source_start_seconds"])
    source_end = float(attrs["source_end_seconds"])
    target_start = float(attrs["target_start_seconds"])
    target_end = float(attrs["target_end_seconds"])
    if min(source_start, target_start) < 0 or source_end <= source_start or target_end <= target_start:
        raise ValueError("declared master ranges must be positive and ordered")
    if source_end > len(source_all) / rate + 2 / rate:
        raise ValueError("declared master source range exceeds the submitted mix")
    if target_end > len(target_all) / rate + 2 / rate:
        raise ValueError("declared master target range exceeds the submitted master")
    if abs((source_end - source_start) - (target_end - target_start)) > 2 / rate:
        raise ValueError("declared master ranges must describe equal durations")

    source_first, source_last = round(source_start * rate), round(source_end * rate)
    target_first, target_last = round(target_start * rate), round(target_end * rate)
    source_selected = source_all[source_first:source_last]
    target_selected = target_all[target_first:target_last]
    count = min(len(source_selected), len(target_selected))
    source_selected, target_selected = source_selected[:count], target_selected[:count]
    declared_score = _correlation(source_selected, target_selected)

    # Search only for competing locations. An alternative can make the declared
    # range ambiguous, but it is never substituted for the submitted range.
    coarse_source = source_selected[::4]
    coarse_target = target_all[::4]
    declared_coarse_start = round(target_start * rate / 4)
    radius = round(MAX_OFFSET_SECONDS * rate / 4)
    exclusion = round(MASTER_PEAK_EXCLUSION_SECONDS * rate / 4)
    first_candidate = max(0, declared_coarse_start - radius)
    last_candidate = min(len(coarse_target) - len(coarse_source),
                         declared_coarse_start + radius)
    alternatives = []
    if len(coarse_source) >= 32 and last_candidate >= first_candidate:
        for candidate in range(first_candidate, last_candidate + 1):
            if abs(candidate - declared_coarse_start) < exclusion:
                continue
            comparison = coarse_target[candidate:candidate + len(coarse_source)]
            alternatives.append((_correlation(coarse_source, comparison), candidate))
    alternative_score, alternative_start = (
        max(alternatives, key=lambda item: item[0])
        if alternatives else (float("-inf"), declared_coarse_start))
    alignment_margin = (declared_score - alternative_score
                        if math.isfinite(alternative_score) else float("inf"))
    alignment_ambiguous = (
        declared_score >= 0.65 and alternative_score >= 0.65 and
        alignment_margin < MASTER_ALIGNMENT_MARGIN)
    coverage = count / max(len(source_all), len(target_all), 1)
    diagnostics = {
        "declared_offset_seconds": target_start - source_start,
        "declared_alignment_correlation": declared_score,
        "strongest_alternative_alignment_correlation": (
            alternative_score if math.isfinite(alternative_score) else None),
        "alternative_target_start_seconds": (
            alternative_start * 4 / rate if math.isfinite(alternative_score) else None),
        "alignment_margin": alignment_margin if math.isfinite(alignment_margin) else None,
        "alignment_ambiguous": alignment_ambiguous,
        "alignment_coverage_ratio": coverage,
    }
    return source_selected, target_selected, diagnostics


def _block_rms(samples, block_size):
    return [math.sqrt(sum(value * value for value in samples[start:start + block_size]) /
                      len(samples[start:start + block_size]))
            for start in range(0, len(samples), block_size)
            if len(samples[start:start + block_size]) >= block_size // 2]


def _rms(samples):
    return math.sqrt(sum(value * value for value in samples) / len(samples)) if samples else 0.0


class MasterDerivationPass:
    """Corroborate mix-to-master continuity without claiming exact reconstruction."""

    @staticmethod
    def evaluate(native, chain):
        results = []
        for target in native["artefacts"]:
            edges = [edge for edge in target.get("evidence", [])
                     if edge["relationship_type"] == "mastered_from"]
            if not edges:
                continue
            base = _base("audio_master_content_continuity", "MasterDerivationPass",
                         target, edges)
            if target["artefact_type"] != "audio/master" or len(edges) != 1:
                results.append({**base, "status": "not_applicable",
                                "reason": "Master analysis requires one audio/mix source and one audio/master target."})
                continue
            missing = _missing_parameters(
                edges, ("derivation_scope",) + MASTER_DECLARATION_FIELDS)
            if missing:
                results.append({
                    **base,
                    "status": "unavailable",
                    "claim_status": "declared_unverified",
                    "reason": "The mix-to-master relationship was declared, but no complete comparison ranges were supplied.",
                    "reason_code": "missing_derivation_parameters",
                    "missing_parameters": missing,
                    "content_match_is_provenance": False,
                })
                continue
            attrs = edges[0].get("attributes") or {}
            declared_scope = attrs.get("derivation_scope")
            if declared_scope != "master_similarity":
                results.append({**base, "status": "not_applicable",
                                "reason": "A mastered_from scope must be master_similarity."})
                continue
            try:
                source = _read_pcm(Path(chain.artefacts[edges[0]["hash"]].get_file()))
                master = _read_pcm(Path(chain.artefacts[target["artefact_hash"]].get_file()))
                source_samples, master_samples, alignment = _declared_master_samples(
                    source, master, attrs)
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "claim_status": "declared_unverified",
                                "reason": f"Master comparison unavailable: {exc}",
                                "content_match_is_provenance": False})
                continue
            if len(source_samples) < 128 or _rms(source_samples) < 1e-7 or _rms(master_samples) < 1e-7:
                results.append({**base, "status": "unavailable",
                                "claim_status": "declared_unverified",
                                "reason": "There is insufficient non-silent audio for master comparison.",
                                "content_match_is_provenance": False})
                continue
            waveform_correlation = _correlation(source_samples, master_samples)
            source_envelope = _block_rms(source_samples, 100)
            master_envelope = _block_rms(master_samples, 100)
            envelope_correlation = _correlation(source_envelope, master_envelope)
            source_delta = _rms([right - left for left, right in zip(source_samples, source_samples[1:])])
            master_delta = _rms([right - left for left, right in zip(master_samples, master_samples[1:])])
            source_brightness = source_delta / max(_rms(source_samples), 1e-12)
            master_brightness = master_delta / max(_rms(master_samples), 1e-12)
            spectral_balance_delta = abs(math.log(max(master_brightness, 1e-12) /
                                                  max(source_brightness, 1e-12)))
            spectral_similarity = math.exp(-2 * spectral_balance_delta)
            duration_source = len(source["frames"]) / source["sample_rate_hz"]
            duration_master = len(master["frames"]) / master["sample_rate_hz"]
            duration_delta = duration_master - duration_source
            loudness_delta_db = 20 * math.log10(_rms(master_samples) / _rms(source_samples))
            manual_review_reasons = []
            if alignment["alignment_ambiguous"]:
                status = "not_applicable"
                reason = "Several distinct time offsets produce similarly strong master alignment; the relationship is ambiguous."
                manual_review_reasons.append("ambiguous_master_alignment")
            elif alignment["alignment_coverage_ratio"] < MIN_TARGET_COVERAGE:
                status = "not_applicable"
                reason = "The aligned mix and master do not overlap enough for reliable continuity assessment."
                manual_review_reasons.append("insufficient_master_coverage")
            elif (abs(duration_delta) <= 2.0 and spectral_similarity >= 0.7 and
                    ((waveform_correlation >= 0.65 and envelope_correlation >= 0.75) or
                     waveform_correlation >= 0.9)):
                status = "corroborated"
                reason = "Waveform, dynamics, timing and coarse spectral continuity support the declared mix-to-master relationship."
            elif (abs(duration_delta) > max(5.0, duration_source * 0.1) or
                  (waveform_correlation < 0.15 and envelope_correlation < 0.25 and
                   spectral_similarity < 0.4)):
                status = "contradicted"
                reason = "The submitted mix and master lack the minimum content continuity expected after mastering."
            else:
                status = "not_applicable"
                reason = "The comparison is inconclusive for real-world mastering; no exact derivation claim is made."
                manual_review_reasons.append("inconclusive_master_processing")
            results.append({**base, "status": status,
                            "claim_status": ("declared_supported" if status == "corroborated"
                                             else _claim_status(status)),
                            "reason": reason,
                            "verified_scope": "content_continuity_only",
                            "declared_scope": declared_scope,
                            "parameter_source": "submitter_declared",
                            "content_relationship_verified": None,
                            "content_continuity_corroborated": status == "corroborated",
                            "declared_offset_seconds": round(
                                alignment["declared_offset_seconds"], 6),
                            "declared_alignment_correlation": round(
                                alignment["declared_alignment_correlation"], 6),
                            "strongest_alternative_alignment_correlation": (
                                round(alignment["strongest_alternative_alignment_correlation"], 6)
                                if alignment["strongest_alternative_alignment_correlation"] is not None
                                else None),
                            "alternative_target_start_seconds": alignment[
                                "alternative_target_start_seconds"],
                            "alignment_margin": (round(alignment["alignment_margin"], 6)
                                                 if alignment["alignment_margin"] is not None
                                                 else None),
                            "alignment_ambiguous": alignment["alignment_ambiguous"],
                            "alignment_coverage_ratio": round(
                                alignment["alignment_coverage_ratio"], 6),
                            "duration_delta_seconds": round(duration_delta, 6),
                            "waveform_correlation": round(waveform_correlation, 6),
                            "envelope_correlation": round(envelope_correlation, 6),
                            "spectral_similarity": round(spectral_similarity, 6),
                            "loudness_delta_db": round(loudness_delta_db, 4),
                            "manual_review_reasons": manual_review_reasons,
                            "content_match_is_provenance": False})
        return results
