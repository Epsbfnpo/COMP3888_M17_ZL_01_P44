"""Conservative PCM WAV content-derivation assessments.

Each pass verifies only a narrow, declared transformation model. A poor fit
contradicts that model; it does not prove that a source was never used through
some undocumented or nonlinear production process.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from array import array
from collections.abc import Sequence
from pathlib import Path

MAX_SOURCES = 16
MAX_COMP_SELECTIONS = 64
MAX_ANALYSIS_SECONDS = 60
ANALYSIS_RATE_HZ = 2000
PCM_READ_CHUNK_FRAMES = 65_536
MAX_REQUEST_PCM_BYTES = 256 * 1024 * 1024
MAX_OFFSET_SECONDS = 5.0
MIN_TARGET_COVERAGE = 0.90
MAX_SOURCE_CORRELATION = 0.995
MIN_SOURCE_IDENTIFIABILITY = 1e-4
HOLDOUT_BLOCK_SECONDS = 0.05
LOCAL_MISMATCH_RMSE = 0.35
LOCAL_MISMATCH_MIN_SECONDS = 0.20
LOCAL_MISMATCH_MIN_RMS = 1e-4
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


class PackedPCMFrames(Sequence):
    """Frame-compatible view over contiguous interleaved float32 PCM samples."""

    __slots__ = ("_samples", "channels")

    def __init__(self, samples, channels):
        self._samples = samples
        self.channels = channels

    def __len__(self):
        return len(self._samples) // self.channels

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[position] for position in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        first = index * self.channels
        return tuple(self._samples[first:first + self.channels])

    @property
    def nbytes(self):
        return len(self._samples) * self._samples.itemsize

    @property
    def storage(self):
        return self._samples


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


def _decode_pcm_chunk(raw, width, output):
    """Append one frame-aligned PCM byte chunk to an array('f')."""
    if width == 1:
        output.extend((value - 128) / 128.0 for value in raw)
        return
    if width in (2, 4):
        values = array("h" if width == 2 else "i")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        scale = 32768.0 if width == 2 else 2147483648.0
        output.extend(value / scale for value in values)
        return
    if width == 3:
        for position in range(0, len(raw), 3):
            output.append(_pcm_value(raw[position:position + 3], 3))
        return
    raise ValueError("Only 8/16/24/32-bit integer PCM WAV is supported.")


def _read_pcm(path: Path, start_seconds=0.0, end_seconds=None,
              max_seconds=MAX_ANALYSIS_SECONDS, byte_budget=MAX_REQUEST_PCM_BYTES):
    """Read a bounded PCM window into contiguous float32 storage.

    Explicit end_seconds requests are not clipped to MAX_ANALYSIS_SECONDS.  The
    request-level decoded-byte budget remains the hard resource bound.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getcomptype() != "NONE":
                raise ValueError("compressed WAV is not supported")
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            total_frames = handle.getnframes()
            if channels < 1 or rate < 1 or width not in (1, 2, 3, 4):
                raise ValueError("unsupported PCM channel, rate, or sample width")
            start_seconds = float(start_seconds or 0.0)
            if not math.isfinite(start_seconds) or start_seconds < 0:
                raise ValueError("PCM window start must be a finite non-negative number")
            start_frame = math.floor(start_seconds * rate)
            if start_frame > total_frames:
                raise ValueError("PCM window starts beyond the submitted audio")
            if end_seconds is None:
                end_frame = total_frames
                if max_seconds is not None:
                    end_frame = min(end_frame, start_frame + round(max_seconds * rate))
            else:
                end_seconds = float(end_seconds)
                if not math.isfinite(end_seconds) or end_seconds <= start_seconds:
                    raise ValueError("PCM window end must be finite and after its start")
                end_frame = math.ceil(end_seconds * rate)
                if end_frame > total_frames + 1:
                    raise ValueError("PCM window ends beyond the submitted audio")
                end_frame = min(end_frame, total_frames)
            frame_count = max(0, end_frame - start_frame)
            decoded_bytes = frame_count * channels * array("f").itemsize
            if decoded_bytes > byte_budget:
                raise ValueError(
                    f"decoded PCM window requires {decoded_bytes} bytes, exceeding the remaining request budget")
            handle.setpos(start_frame)
            samples = array("f")
            remaining = frame_count
            while remaining:
                count = min(remaining, PCM_READ_CHUNK_FRAMES)
                raw = handle.readframes(count)
                expected = count * channels * width
                if len(raw) != expected:
                    raise ValueError("truncated PCM frame data")
                _decode_pcm_chunk(raw, width, samples)
                remaining -= count
    except wave.Error as exc:
        raise ValueError(f"invalid WAV: {exc}") from exc
    frames = PackedPCMFrames(samples, channels)
    return {"frames": frames, "sample_rate_hz": rate, "channels": channels,
            "bit_depth": width * 8, "total_frames": total_frames,
            "duration_seconds": total_frames / rate,
            "start_frame": start_frame, "end_frame": end_frame,
            "start_seconds": start_frame / rate, "end_seconds": end_frame / rate,
            "decoded_bytes": frames.nbytes}


def _read_bound_pcm(chain, digest, start_seconds=0.0, end_seconds=None,
                    max_seconds=MAX_ANALYSIS_SECONDS):
    """Read and cache a bound PCM window unless metadata parsing failed."""
    artefact = chain.artefacts[digest]
    if not artefact.has_file():
        raise ValueError("no verified file is bound to the audio artefact")
    parser_failure = getattr(artefact, "_wav_parser_failure", None)
    if parser_failure:
        raise ValueError(f"WAV parser unavailable for this artefact: {parser_failure}")
    start_key = round(float(start_seconds or 0.0), 9)
    end_key = round(float(end_seconds), 9) if end_seconds is not None else None
    max_key = round(float(max_seconds), 9) if max_seconds is not None else None
    key = (digest, start_key, end_key, max_key)
    cache = getattr(chain, "_pcm_window_cache", None)
    if cache is None:
        cache = {}
        chain._pcm_window_cache = cache
        chain._pcm_cache_bytes = 0
        chain._pcm_cache_hits = 0
        chain._pcm_cache_misses = 0
    if key in cache:
        chain._pcm_cache_hits += 1
        return cache[key]
    remaining = MAX_REQUEST_PCM_BYTES - chain._pcm_cache_bytes
    audio = _read_pcm(Path(artefact.get_file()), start_key, end_key, max_key, remaining)
    cache[key] = audio
    chain._pcm_cache_bytes += audio["decoded_bytes"]
    chain._pcm_cache_misses += 1
    return audio


def _pcm_duration(chain, digest):
    """Return full file duration from the WAV header without decoding samples."""
    cache = getattr(chain, "_pcm_duration_cache", None)
    if cache is None:
        cache = {}
        chain._pcm_duration_cache = cache
    if digest in cache:
        return cache[digest]
    artefact = chain.artefacts[digest]
    if not artefact.has_file():
        raise ValueError("no verified file is bound to the audio artefact")
    try:
        with wave.open(str(artefact.get_file()), "rb") as handle:
            rate = handle.getframerate()
            if rate < 1:
                raise ValueError("invalid WAV sample rate")
            duration = handle.getnframes() / rate
    except wave.Error as exc:
        raise ValueError(f"invalid WAV: {exc}") from exc
    cache[digest] = duration
    return duration


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
    target_start = min(float((edge.get("attributes") or {})["target_start_seconds"])
                       for edge in edges)
    target_end = max(float((edge.get("attributes") or {})["target_end_seconds"])
                     for edge in edges)
    target_audio = _read_bound_pcm(
        chain, target["artefact_hash"], target_start, target_end, max_seconds=None)
    sources = []
    for edge in edges:
        attrs = edge.get("attributes") or {}
        sources.append(_read_bound_pcm(
            chain, edge["hash"], float(attrs["source_start_seconds"]),
            float(attrs["source_end_seconds"]), max_seconds=None))
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
        if source_end > audio["duration_seconds"] + 2 / rate:
            raise ValueError("declared source range exceeds the submitted audio")
        if target_end > target_audio["duration_seconds"] + 2 / rate:
            raise ValueError("declared target range exceeds the submitted audio")
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
            target_time = target_audio["start_seconds"] + target_index / rate
            frame = silence
            if target_start <= target_time < target_end:
                source_time = source_start + target_time - target_start
                source_index = round(source_time * rate) - audio["start_frame"]
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
    mismatch_blocks = []
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
        normalized_rmse = (math.sqrt(residual / energy)
                           if energy >= 1e-12 else (0.0 if residual < 1e-12 else float("inf")))
        block_rms = math.sqrt(energy / max(len(actual), 1))
        if normalized_rmse <= 0.05:
            matched_frames += covered
        elif (normalized_rmse >= LOCAL_MISMATCH_RMSE and
              block_rms >= LOCAL_MISMATCH_MIN_RMS and
              covered >= max(1, (frame_end - frame_start) // 2)):
            mismatch_blocks.append({
                "start_seconds": (target_audio["start_seconds"] +
                                  frame_start / analysis_rate),
                "end_seconds": (target_audio["start_seconds"] +
                                frame_end / analysis_rate),
                "normalized_rmse": normalized_rmse,
                "signal_rms": block_rms,
            })

    mismatch_regions = []
    for block in mismatch_blocks:
        if (mismatch_regions and
                block["start_seconds"] <= mismatch_regions[-1]["end_seconds"] +
                1.5 / analysis_rate):
            region = mismatch_regions[-1]
            region["end_seconds"] = block["end_seconds"]
            region["peak_normalized_rmse"] = max(
                region["peak_normalized_rmse"], block["normalized_rmse"])
            region["block_count"] += 1
        else:
            mismatch_regions.append({
                "start_seconds": block["start_seconds"],
                "end_seconds": block["end_seconds"],
                "peak_normalized_rmse": block["normalized_rmse"],
                "block_count": 1,
            })
    mismatch_regions = [
        {**region,
         "duration_seconds": region["end_seconds"] - region["start_seconds"]}
        for region in mismatch_regions
        if region["end_seconds"] - region["start_seconds"] + 1e-9 >=
        LOCAL_MISMATCH_MIN_SECONDS
    ]

    covered_count = sum(covered_frames)
    target_duration = target_audio["duration_seconds"]
    total_analysis_frames = max(1, math.ceil(target_audio["total_frames"] / step))
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
        "coverage_ratio": covered_count / total_analysis_frames,
        "matched_duration_seconds": matched_frames / analysis_rate,
        "matched_coverage_ratio": matched_frames / total_analysis_frames,
        "local_mismatch_regions": mismatch_regions,
        "longest_local_mismatch_seconds": max(
            (region["duration_seconds"] for region in mismatch_regions), default=0.0),
    }


def _linear_status(assessment, match_rmse, match_correlation,
                   contradiction_rmse=0.35, contradiction_correlation=0.5):
    if assessment["coverage_ratio"] < MIN_TARGET_COVERAGE:
        return "not_applicable", "insufficient_target_coverage"
    if assessment["local_mismatch_regions"]:
        return "contradicted", "sustained_local_mismatch"
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
    window_start = audio.get("start_seconds", 0.0)
    first = round((start - window_start) * SEGMENT_SCAN_RATE_HZ)
    last = round((end - window_start) * SEGMENT_SCAN_RATE_HZ)
    if first < 0 or last > len(samples):
        return []
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
            alternatives.append(round(window_start + candidate / SEGMENT_SCAN_RATE_HZ, 3))
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
                for edge in edges:
                    audio = _read_bound_pcm(chain, edge["hash"])
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
        elif status == "contradicted" and review_reason == "sustained_local_mismatch":
            reason = "A sustained, non-silent target interval contradicts the declared aligned constant-gain model."
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
                        "local_mismatch_regions": [{
                            "start_seconds": round(region["start_seconds"], 6),
                            "end_seconds": round(region["end_seconds"], 6),
                            "duration_seconds": round(region["duration_seconds"], 6),
                            "peak_normalized_rmse": round(
                                region["peak_normalized_rmse"], 6),
                            "block_count": region["block_count"],
                        } for region in assessed["local_mismatch_regions"]],
                        "longest_local_mismatch_seconds": round(
                            assessed["longest_local_mismatch_seconds"], 6),
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
                target_start = min(float(selection["target_start_seconds"])
                                   for selection in selections)
                target_end = max(float(selection["target_end_seconds"])
                                 for selection in selections)
                target_audio = _read_bound_pcm(
                    chain, target["artefact_hash"], target_start, target_end,
                    max_seconds=None)
                entries = []
                alternative_locations = []
                for selection in selections:
                    source_hash = selection["source_hash"]
                    if source_hash not in nodes:
                        raise ValueError("a CompSheet source hash is absent from the graph")
                    source_audio = _read_bound_pcm(
                        chain, source_hash,
                        float(selection["source_start_seconds"]),
                        float(selection["source_end_seconds"]), max_seconds=None)
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
                    source_search_audio = _read_bound_pcm(chain, source_hash)
                    alternatives = _segment_alternative_locations(source_search_audio, {
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
            elif status == "contradicted" and review_reason == "sustained_local_mismatch":
                reason = "A sustained, non-silent interval conflicts with the declared CompSheet selection timeline."
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
                            "local_mismatch_regions": [{
                                "start_seconds": round(region["start_seconds"], 6),
                                "end_seconds": round(region["end_seconds"], 6),
                                "duration_seconds": round(region["duration_seconds"], 6),
                                "peak_normalized_rmse": round(
                                    region["peak_normalized_rmse"], 6),
                                "block_count": region["block_count"],
                            } for region in assessed["local_mismatch_regions"]],
                            "longest_local_mismatch_seconds": round(
                                assessed["longest_local_mismatch_seconds"], 6),
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
    if source_end > source["duration_seconds"] + 2 / rate:
        raise ValueError("declared master source range exceeds the submitted mix")
    if target_end > target["duration_seconds"] + 2 / rate:
        raise ValueError("declared master target range exceeds the submitted master")
    if abs((source_end - source_start) - (target_end - target_start)) > 2 / rate:
        raise ValueError("declared master ranges must describe equal durations")

    source_first = round((source_start - source["start_seconds"]) * rate)
    source_last = round((source_end - source["start_seconds"]) * rate)
    target_first = round((target_start - target["start_seconds"]) * rate)
    target_last = round((target_end - target["start_seconds"]) * rate)
    source_selected = source_all[source_first:source_last]
    target_selected = target_all[target_first:target_last]
    count = min(len(source_selected), len(target_selected))
    source_selected, target_selected = source_selected[:count], target_selected[:count]
    declared_score = _correlation(source_selected, target_selected)

    # Search only for competing locations. An alternative can make the declared
    # range ambiguous, but it is never substituted for the submitted range.
    coarse_source = source_selected[::4]
    coarse_target = target_all[::4]
    declared_coarse_start = round(
        (target_start - target["start_seconds"]) * rate / 4)
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
    expected = max(1, round((source_end - source_start) * rate))
    coverage = count / expected
    diagnostics = {
        "declared_offset_seconds": target_start - source_start,
        "declared_alignment_correlation": declared_score,
        "strongest_alternative_alignment_correlation": (
            alternative_score if math.isfinite(alternative_score) else None),
        "alternative_target_start_seconds": (
            target["start_seconds"] + alternative_start * 4 / rate
            if math.isfinite(alternative_score) else None),
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
                source_start = float(attrs["source_start_seconds"])
                source_end = float(attrs["source_end_seconds"])
                target_start = float(attrs["target_start_seconds"])
                target_end = float(attrs["target_end_seconds"])
                source = _read_bound_pcm(
                    chain, edges[0]["hash"], source_start, source_end,
                    max_seconds=None)
                master = _read_bound_pcm(
                    chain, target["artefact_hash"],
                    max(0.0, target_start - MAX_OFFSET_SECONDS),
                    min(target_end + MAX_OFFSET_SECONDS,
                        _pcm_duration(chain, target["artefact_hash"])),
                    max_seconds=None)
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
            duration_source = source["duration_seconds"]
            duration_master = master["duration_seconds"]
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
