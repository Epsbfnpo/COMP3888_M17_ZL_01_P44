"""Conservative PCM WAV content-derivation assessments.

Each pass verifies only a narrow, declared transformation model. A poor fit
contradicts that model; it does not prove that a source was never used through
some undocumented or nonlinear production process.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from music_target.wav_io import WavDescriptor, WavError, inspect_wav, read_frames

MAX_SOURCES = 16
MAX_COMP_SELECTIONS = 64
PCM_STREAM_CHUNK_SECONDS = 10.0
PROXY_TARGET_RATE_HZ = 2000
PROXY_FILTER_HALF_WIDTH = 8
MAX_ACTIVE_PCM_BYTES = 128 * 1024 * 1024
MAX_PROXY_CACHE_BYTES = 256 * 1024 * 1024
MAX_REQUEST_WORK_UNITS = 20_000.0
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


class ResourceBudgetExceeded(ValueError):
    """The request is valid but cannot be assessed within configured bounds."""

    reason_code = "resource_budget_exhausted"


def _consume_work(chain, units):
    used = float(getattr(chain, "_audio_work_units", 0.0)) + float(units)
    chain._audio_work_units = used
    if used > MAX_REQUEST_WORK_UNITS:
        raise ResourceBudgetExceeded(
            f"audio analysis work budget exceeded ({used:.1f} > "
            f"{MAX_REQUEST_WORK_UNITS:.1f} weighted channel-seconds)")


def _wave_metadata(path, descriptor: WavDescriptor | None = None):
    descriptor = descriptor or inspect_wav(path)
    return {
        "sample_rate_hz": descriptor.sample_rate,
        "channels": descriptor.channels,
        "sample_width": descriptor.container_bits // 8,
        "bit_depth": descriptor.container_bits,
        "valid_bits_per_sample": descriptor.valid_bits,
        "sample_format": descriptor.format_kind,
        "total_frames": descriptor.frame_count,
        "duration_seconds": descriptor.duration_seconds,
        "descriptor": descriptor,
    }


def _read_frame_array(path, descriptor, start_frame, end_frame,
                      block_frames=None):
    result = np.empty(
        (end_frame - start_frame, descriptor.channels), dtype=np.float32)
    for first_frame, block in read_frames(
            path, descriptor, start_frame, end_frame, block_frames):
        offset = first_frame - start_frame
        result[offset:offset + len(block)] = block
    return result


def _read_pcm(path: Path, start_seconds=0.0, end_seconds=None,
              max_seconds=None, byte_budget=MAX_ACTIVE_PCM_BYTES,
              descriptor: WavDescriptor | None = None):
    """Read one PCM window as a C-contiguous float32 ndarray.

    There is intentionally no default 60-second clipping. Long-running checks
    either use the low-rate full-file proxy or call this function for bounded
    streaming windows.
    """
    descriptor = descriptor or inspect_wav(path)
    channels = descriptor.channels
    rate = descriptor.sample_rate
    total_frames = descriptor.frame_count
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
    decoded_bytes = frame_count * channels * np.dtype(np.float32).itemsize
    if decoded_bytes > byte_budget:
        raise ResourceBudgetExceeded(
            f"decoded PCM window requires {decoded_bytes} bytes, exceeding the request budget")
    frames = _read_frame_array(
        path, descriptor, start_frame, end_frame,
        max(1, round(rate * PCM_STREAM_CHUNK_SECONDS)))
    return {"frames": frames, "sample_rate_hz": rate, "channels": channels,
            "bit_depth": descriptor.container_bits,
            "sample_format": descriptor.format_kind,
            "total_frames": total_frames,
            "duration_seconds": total_frames / rate,
            "start_frame": start_frame, "end_frame": end_frame,
            "start_seconds": start_frame / rate, "end_seconds": end_frame / rate,
            "decoded_bytes": int(frames.nbytes)}


def _read_pcm_frame_range(path, start_frame, end_frame, metadata=None,
                          byte_budget=MAX_ACTIVE_PCM_BYTES):
    """Read an exact integer frame range without seconds-to-frame rounding."""
    metadata = metadata or _wave_metadata(path)
    total_frames = metadata["total_frames"]
    start_frame = int(start_frame)
    end_frame = int(end_frame)
    if start_frame < 0 or end_frame < start_frame or end_frame > total_frames:
        raise ValueError("PCM frame range is outside the submitted audio")
    channels = metadata["channels"]
    rate = metadata["sample_rate_hz"]
    descriptor = metadata["descriptor"]
    frame_count = end_frame - start_frame
    decoded_bytes = frame_count * channels * np.dtype(np.float32).itemsize
    if decoded_bytes > byte_budget:
        raise ResourceBudgetExceeded(
            f"active PCM window requires {decoded_bytes} bytes, exceeding "
            f"the {byte_budget}-byte memory limit")
    chunk_frames = max(1, round(rate * PCM_STREAM_CHUNK_SECONDS))
    frames = _read_frame_array(
        path, descriptor, start_frame, end_frame, chunk_frames)
    return {
        "frames": frames, "sample_rate_hz": rate, "channels": channels,
        "bit_depth": descriptor.container_bits,
        "sample_format": descriptor.format_kind,
        "total_frames": total_frames,
        "duration_seconds": metadata["duration_seconds"],
        "start_frame": start_frame, "end_frame": end_frame,
        "start_seconds": start_frame / rate, "end_seconds": end_frame / rate,
        "decoded_bytes": int(frames.nbytes),
    }


def _artefact_descriptor(artefact, path):
    descriptor = getattr(artefact, "_wav_descriptor", None)
    if not isinstance(descriptor, WavDescriptor):
        descriptor = inspect_wav(path)
        artefact._wav_descriptor = descriptor
    return descriptor


def _read_bound_pcm(chain, digest, start_seconds=0.0, end_seconds=None,
                    max_seconds=None):
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
        chain._pcm_cache_hits = 0
        chain._pcm_cache_misses = 0
    if key in cache:
        chain._pcm_cache_hits += 1
        return cache[key]
    descriptor = _artefact_descriptor(artefact, artefact.get_file())
    try:
        audio = _read_pcm(
            Path(artefact.get_file()), start_key, end_key, max_key,
            descriptor=descriptor)
    except WavError as exc:
        artefact._wav_parser_failure = str(exc)
        artefact._wav_parser_failure_reason_code = exc.reason_code
        raise
    # Only small windows are retained. Exact derivation streams long ranges and
    # proxies have their own bounded request cache.
    if audio["decoded_bytes"] <= 16 * 1024 * 1024:
        cache[key] = audio
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
    descriptor = _artefact_descriptor(artefact, artefact.get_file())
    duration = descriptor.duration_seconds
    cache[digest] = duration
    return duration


def _fir_lowpass(rate, cutoff_hz, transition_scale=1.0):
    """Return an odd, unity-DC Kaiser-windowed sinc low-pass filter."""
    if cutoff_hz <= 0 or cutoff_hz >= rate / 2:
        return np.ones(1, dtype=np.float32)
    ratio = max(1.0, rate / (2.0 * cutoff_hz))
    half = max(8, math.ceil(PROXY_FILTER_HALF_WIDTH * ratio * transition_scale))
    positions = np.arange(-half, half + 1, dtype=np.float64)
    normalized_cutoff = cutoff_hz / rate
    taps = (2.0 * normalized_cutoff *
            np.sinc(2.0 * normalized_cutoff * positions) *
            np.kaiser(2 * half + 1, 8.6))
    taps /= np.sum(taps)
    return taps.astype(np.float32)


def _proxy_decimation_factor(rate):
    """Choose a compact integer phase factor, preferring an exact-Hz proxy."""
    maximum = max(1, int(rate // PROXY_TARGET_RATE_HZ))
    for factor in range(maximum, 0, -1):
        if rate % factor == 0 and rate / factor <= PROXY_TARGET_RATE_HZ * 1.10:
            return factor
    return maximum


def _analysis_proxy(chain, digest):
    """Build and cache a full-file, anti-aliased low-rate analysis copy.

    Input is processed in globally phase-aligned ten-second blocks. Each block
    includes half a filter on both sides, so concatenating the retained centres
    is equivalent to filtering the full signal once and then decimating it.
    """
    algorithm = "kaiser-sinc-proxy-v1"
    cache = getattr(chain, "_analysis_proxy_cache", None)
    if cache is None:
        cache = {}
        chain._analysis_proxy_cache = cache
        chain._analysis_proxy_cache_bytes = 0
        chain._analysis_proxy_hits = 0
        chain._analysis_proxy_misses = 0
    key = (digest, algorithm)
    if key in cache:
        chain._analysis_proxy_hits += 1
        return cache[key]

    artefact = chain.artefacts[digest]
    if not artefact.has_file():
        raise ValueError("no verified file is bound to the audio artefact")
    parser_failure = getattr(artefact, "_wav_parser_failure", None)
    if parser_failure:
        raise ValueError(f"WAV parser unavailable for this artefact: {parser_failure}")
    path = Path(artefact.get_file())
    descriptor = _artefact_descriptor(artefact, path)
    metadata = _wave_metadata(path, descriptor)
    rate = metadata["sample_rate_hz"]
    channels = metadata["channels"]
    total_frames = metadata["total_frames"]
    factor = _proxy_decimation_factor(rate)
    proxy_rate = rate / factor
    output_frames = (total_frames + factor - 1) // factor
    estimated_bytes = output_frames * channels * np.dtype(np.float32).itemsize
    if (getattr(chain, "_analysis_proxy_cache_bytes", 0) + estimated_bytes >
            MAX_PROXY_CACHE_BYTES):
        raise ResourceBudgetExceeded(
            "analysis proxy cache would exceed the request memory limit")
    _consume_work(chain, metadata["duration_seconds"] * channels)

    if factor == 1:
        try:
            frames = _read_pcm(
                path, max_seconds=None, descriptor=descriptor)["frames"]
        except WavError as exc:
            artefact._wav_parser_failure = str(exc)
            artefact._wav_parser_failure_reason_code = exc.reason_code
            raise
        filter_length = 1
    else:
        # Leave a transition band below the proxy Nyquist.
        taps = _fir_lowpass(rate, proxy_rate * 0.45)
        half = len(taps) // 2
        filter_length = len(taps)
        chunk_frames = max(factor,
                           (round(rate * PCM_STREAM_CHUNK_SECONDS) // factor) * factor)
        frames = np.empty((output_frames, channels), dtype=np.float32)
        output_position = 0
        try:
            for core_start in range(0, total_frames, chunk_frames):
                core_end = min(total_frames, core_start + chunk_frames)
                read_start = max(0, core_start - half)
                read_end = min(total_frames, core_end + half)
                decoded = _read_frame_array(
                    path, descriptor, read_start, read_end,
                    max(1, read_end - read_start))
                padded = np.zeros(
                    (core_end - core_start + 2 * half, channels),
                    dtype=np.float32)
                insert = read_start - (core_start - half)
                padded[insert:insert + len(decoded)] = decoded
                filtered = np.empty((core_end - core_start, channels),
                                    dtype=np.float32)
                for channel in range(channels):
                    filtered[:, channel] = np.convolve(
                        padded[:, channel], taps, mode="valid")
                decimated = filtered[::factor]
                frames[output_position:output_position + len(decimated)] = decimated
                output_position += len(decimated)
        except WavError as exc:
            artefact._wav_parser_failure = str(exc)
            artefact._wav_parser_failure_reason_code = exc.reason_code
            raise
        frames = np.ascontiguousarray(frames[:output_position])

    proxy = {
        "frames": frames,
        "sample_rate_hz": proxy_rate,
        "proxy_sample_rate_hz": proxy_rate,
        "source_sample_rate_hz": rate,
        "decimation_factor": factor,
        "channels": channels,
        "bit_depth": metadata["bit_depth"],
        "sample_format": metadata["sample_format"],
        "total_frames": len(frames),
        "source_total_frames": total_frames,
        "duration_seconds": metadata["duration_seconds"],
        "start_frame": 0,
        "end_frame": len(frames),
        "start_seconds": 0.0,
        "end_seconds": metadata["duration_seconds"],
        "decoded_bytes": int(frames.nbytes),
        "analysis_proxy": True,
        "analysis_algorithm": algorithm,
        "filter_length": filter_length,
        "analysis_ranges_seconds": [[0.0, metadata["duration_seconds"]]],
        "search_complete": True,
    }
    cache[key] = proxy
    chain._analysis_proxy_cache_bytes += int(frames.nbytes)
    chain._analysis_proxy_misses += 1
    return proxy


def _source_gram(sources, indices):
    if not sources:
        return np.empty((0, 0), dtype=np.float64)
    matrix = np.stack([np.asarray(source, dtype=np.float64) for source in sources], axis=1)
    selected = matrix[np.asarray(indices, dtype=np.int64)]
    return np.einsum("ni,nj->ij", selected, selected, optimize=True)


def _source_identifiability(gram):
    """Return a scale-free pivot ratio for distinguishing source contributions."""
    gram = np.asarray(gram, dtype=np.float64)
    size = gram.shape[0] if gram.ndim == 2 else 0
    if not size:
        return 0.0
    diagonal = np.maximum(np.diag(gram), 0.0)
    denominator = np.sqrt(np.outer(diagonal, diagonal))
    normalized = np.divide(gram, denominator, out=np.zeros_like(gram),
                           where=denominator > 1e-12)
    singular = np.linalg.svd(normalized, compute_uv=False)
    if singular.size == 0 or singular[0] < 1e-12:
        return 0.0
    return float(singular[-1] / singular[0])


def _max_source_correlation(gram):
    gram = np.asarray(gram, dtype=np.float64)
    if gram.ndim != 2 or gram.shape[0] < 2:
        return 0.0
    diagonal = np.maximum(np.diag(gram), 0.0)
    denominator = np.sqrt(np.outer(diagonal, diagonal))
    correlations = np.divide(np.abs(gram), denominator,
                             out=np.zeros_like(gram), where=denominator > 1e-12)
    np.fill_diagonal(correlations, 0.0)
    return float(np.max(correlations))


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


def _bound_audio_path(chain, digest):
    artefact = chain.artefacts[digest]
    if not artefact.has_file():
        raise ValueError("no verified file is bound to the audio artefact")
    parser_failure = getattr(artefact, "_wav_parser_failure", None)
    if parser_failure:
        raise ValueError(f"WAV parser unavailable for this artefact: {parser_failure}")
    path = Path(artefact.get_file())
    descriptor = _artefact_descriptor(artefact, path)
    return path, _wave_metadata(path, descriptor)


def _update_partition(statistics, actual, predicted):
    actual = np.asarray(actual, dtype=np.float64)
    predicted = np.asarray(predicted, dtype=np.float64)
    statistics["count"] += actual.size
    statistics["actual_sum"] += float(np.sum(actual))
    statistics["predicted_sum"] += float(np.sum(predicted))
    statistics["actual_square"] += float(np.sum(actual * actual, dtype=np.float64))
    statistics["predicted_square"] += float(np.sum(predicted * predicted, dtype=np.float64))
    statistics["cross"] += float(np.sum(actual * predicted, dtype=np.float64))
    difference = actual - predicted
    statistics["residual_square"] += float(
        np.sum(difference * difference, dtype=np.float64))


def _partition_result(statistics):
    count = statistics["count"]
    energy = statistics["actual_square"]
    if count < 1 or energy < 1e-12:
        return None
    actual_variance = energy - statistics["actual_sum"] ** 2 / count
    predicted_variance = (statistics["predicted_square"] -
                          statistics["predicted_sum"] ** 2 / count)
    covariance = (statistics["cross"] -
                  statistics["actual_sum"] * statistics["predicted_sum"] / count)
    denominator = math.sqrt(max(actual_variance, 0.0) *
                            max(predicted_variance, 0.0))
    return (math.sqrt(statistics["residual_square"] / energy),
            covariance / denominator if denominator > 1e-12 else 0.0)


def _linear_timeline_metrics(chain, target_digest, edges, group_keys=None):
    """Stream declared exact reconstruction at original sample rate.

    Only one target block and the corresponding source windows are resident at
    a time. All sample placement, fades, residuals and Gram updates are ndarray
    operations; holdout numbering and local-mismatch state remain global across
    block boundaries.
    """
    target_path, target_meta = _bound_audio_path(chain, target_digest)
    rate = target_meta["sample_rate_hz"]
    channels = target_meta["channels"]
    prepared = []
    offsets = []
    declared_gains = []
    keys = list(group_keys) if group_keys is not None else list(range(len(edges)))
    unique_keys = list(dict.fromkeys(keys))
    key_positions = {key: position for position, key in enumerate(unique_keys)}
    for position, edge in enumerate(edges):
        attrs = edge.get("attributes") or {}
        source_path, source_meta = _bound_audio_path(chain, edge["hash"])
        if (source_meta["sample_rate_hz"] != rate or
                source_meta["channels"] != channels):
            raise ValueError("source and target sample rates/channels must match")
        source_start = float(attrs["source_start_seconds"])
        source_end = float(attrs["source_end_seconds"])
        target_start = float(attrs["target_start_seconds"])
        target_end = float(attrs["target_end_seconds"])
        gain = float(attrs["gain"])
        fade_in = float(attrs.get("fade_in_seconds") or 0.0)
        fade_out = float(attrs.get("fade_out_seconds") or 0.0)
        if not all(math.isfinite(value) for value in (
                source_start, source_end, target_start, target_end,
                gain, fade_in, fade_out)):
            raise ValueError("declared linear parameters must be finite")
        if (min(source_start, target_start, fade_in, fade_out) < 0 or
                source_end <= source_start or target_end <= target_start):
            raise ValueError("declared source/target ranges must be positive and ordered")
        if abs((source_end - source_start) - (target_end - target_start)) > max(0.002, 2 / rate):
            raise ValueError("declared ranges imply time stretching")
        if source_end > source_meta["duration_seconds"] + 2 / rate:
            raise ValueError("declared source range exceeds the submitted audio")
        if target_end > target_meta["duration_seconds"] + 2 / rate:
            raise ValueError("declared target range exceeds the submitted audio")
        if fade_in + fade_out > target_end - target_start + 2 / rate:
            raise ValueError("declared fades must fit within the target range")
        offsets.append(target_start - source_start)
        declared_gains.append(gain)
        prepared.append({
            "path": source_path,
            "source_start": source_start,
            "source_end": source_end,
            "target_start": target_start,
            "target_end": target_end,
            "gain": gain,
            "fade_in": fade_in,
            "fade_out": fade_out,
            "group": key_positions[keys[position]],
            "metadata": source_meta,
            "source_start_frame": round(source_start * rate),
            "source_end_frame": round(source_end * rate),
            "target_start_frame": round(target_start * rate),
            "target_end_frame": round(target_end * rate),
        })

    if target_meta["total_frames"] < 32 or not unique_keys:
        return None
    _consume_work(chain, target_meta["duration_seconds"] * channels *
                  (1.0 + len(prepared)))
    holdout_frames = max(1, round(rate * HOLDOUT_BLOCK_SECONDS))
    desired_chunk = max(holdout_frames,
                        round(rate * PCM_STREAM_CHUNK_SECONDS / holdout_frames) *
                        holdout_frames)
    bytes_per_frame = channels * np.dtype(np.float32).itemsize
    memory_chunk = max(holdout_frames,
                       (MAX_ACTIVE_PCM_BYTES //
                        max(bytes_per_frame * (len(unique_keys) + 3), 1) //
                        holdout_frames) * holdout_frames)
    chunk_frames = min(desired_chunk, memory_chunk)
    if chunk_frames < holdout_frames:
        raise ResourceBudgetExceeded("one exact-analysis holdout block exceeds the memory limit")

    empty_stat = lambda: {
        "count": 0, "actual_sum": 0.0, "predicted_sum": 0.0,
        "actual_square": 0.0, "predicted_square": 0.0,
        "cross": 0.0, "residual_square": 0.0,
    }
    fit_stat, validation_stat = empty_stat(), empty_stat()
    gram = np.zeros((len(unique_keys), len(unique_keys)), dtype=np.float64)
    covered_count = 0
    matched_frames = 0
    mismatch_regions = []
    pending_region = None

    def close_pending():
        nonlocal pending_region
        if pending_region is not None:
            duration = pending_region["end_seconds"] - pending_region["start_seconds"]
            if duration + 1e-9 >= LOCAL_MISMATCH_MIN_SECONDS:
                mismatch_regions.append({**pending_region,
                                         "duration_seconds": duration})
            pending_region = None

    for block_start_frame in range(0, target_meta["total_frames"], chunk_frames):
        block_end_frame = min(target_meta["total_frames"],
                              block_start_frame + chunk_frames)
        target_audio = _read_pcm_frame_range(
            target_path, block_start_frame, block_end_frame, target_meta)
        target_frames = target_audio["frames"][:block_end_frame - block_start_frame]
        frame_count = len(target_frames)
        grouped = np.zeros((len(unique_keys), frame_count, channels), dtype=np.float32)
        covered = np.zeros(frame_count, dtype=bool)
        for item in prepared:
            overlap_start_frame = max(block_start_frame,
                                      item["target_start_frame"])
            overlap_end_frame = min(block_end_frame,
                                    item["target_end_frame"])
            if overlap_end_frame <= overlap_start_frame:
                continue
            target_first = overlap_start_frame - block_start_frame
            target_last = overlap_end_frame - block_start_frame
            if target_last <= target_first:
                continue
            source_first_frame = (item["source_start_frame"] +
                                  overlap_start_frame -
                                  item["target_start_frame"])
            source_last_frame = source_first_frame + (target_last - target_first)
            source_audio = _read_pcm_frame_range(
                item["path"], source_first_frame, source_last_frame,
                item["metadata"])
            count = min(target_last - target_first, len(source_audio["frames"]))
            if count <= 0:
                continue
            global_frames = block_start_frame + target_first + np.arange(count)
            target_times = global_frames.astype(np.float64) / rate
            envelope = np.ones(count, dtype=np.float32)
            if item["fade_in"]:
                envelope = np.minimum(
                    envelope,
                    ((target_times - item["target_start"]) /
                     item["fade_in"]).astype(np.float32))
            if item["fade_out"]:
                envelope = np.minimum(
                    envelope,
                    ((item["target_end"] - target_times) /
                     item["fade_out"]).astype(np.float32))
            np.clip(envelope, 0.0, 1.0, out=envelope)
            grouped[item["group"], target_first:target_first + count] += (
                source_audio["frames"][:count] *
                (np.float32(item["gain"]) * envelope[:, None]))
            covered[target_first:target_first + count] = True

        predicted_frames = np.sum(grouped, axis=0, dtype=np.float64)
        target64 = target_frames.astype(np.float64, copy=False)
        global_frame_numbers = block_start_frame + np.arange(frame_count)
        fit_frame_mask = ((global_frame_numbers // holdout_frames) % 2) == 0
        for mask, statistics in ((fit_frame_mask, fit_stat),
                                 (~fit_frame_mask, validation_stat)):
            sample_mask = np.repeat(mask, channels)
            _update_partition(statistics, target64.reshape(-1)[sample_mask],
                              predicted_frames.reshape(-1)[sample_mask])
        fit_grouped = grouped[:, fit_frame_mask, :].reshape(len(unique_keys), -1)
        fit_grouped64 = fit_grouped.astype(np.float64)
        gram += np.einsum("in,jn->ij", fit_grouped64, fit_grouped64,
                          optimize=True)
        covered_count += int(np.count_nonzero(covered))

        for local_start in range(0, frame_count, holdout_frames):
            local_end = min(frame_count, local_start + holdout_frames)
            covered_in_block = int(np.count_nonzero(covered[local_start:local_end]))
            if covered_in_block == 0:
                close_pending()
                continue
            actual = target64[local_start:local_end].reshape(-1)
            estimate = predicted_frames[local_start:local_end].reshape(-1)
            energy = float(np.sum(actual * actual, dtype=np.float64))
            residual_vector = actual - estimate
            residual = float(np.sum(residual_vector * residual_vector,
                                    dtype=np.float64))
            normalized_rmse = (math.sqrt(residual / energy) if energy >= 1e-12
                               else (0.0 if residual < 1e-12 else float("inf")))
            block_rms = math.sqrt(energy / max(actual.size, 1))
            is_mismatch = (
                normalized_rmse >= LOCAL_MISMATCH_RMSE and
                block_rms >= LOCAL_MISMATCH_MIN_RMS and
                covered_in_block >= max(1, (local_end - local_start) // 2))
            if normalized_rmse <= 0.05:
                matched_frames += covered_in_block
            if is_mismatch:
                start_seconds = (block_start_frame + local_start) / rate
                end_seconds = (block_start_frame + local_end) / rate
                if (pending_region is not None and
                        start_seconds <= pending_region["end_seconds"] + 1.5 / rate):
                    pending_region["end_seconds"] = end_seconds
                    pending_region["peak_normalized_rmse"] = max(
                        pending_region["peak_normalized_rmse"], normalized_rmse)
                    pending_region["block_count"] += 1
                else:
                    close_pending()
                    pending_region = {
                        "start_seconds": start_seconds,
                        "end_seconds": end_seconds,
                        "peak_normalized_rmse": normalized_rmse,
                        "block_count": 1,
                    }
            else:
                close_pending()
    close_pending()

    partition_metrics = _partition_result(fit_stat)
    validation_metrics = _partition_result(validation_stat)
    if partition_metrics is None or validation_metrics is None:
        return None
    total_frames = max(1, target_meta["total_frames"])
    return {
        "declared_gains": declared_gains,
        "offsets_seconds": offsets,
        "analysis_sample_rate_hz": rate,
        "analysis_ranges_seconds": [[0.0, target_meta["duration_seconds"]]],
        "analysis_complete": True,
        "declaration_partition_normalized_rmse": partition_metrics[0],
        "declaration_partition_correlation": partition_metrics[1],
        "validation_normalized_rmse": validation_metrics[0],
        "validation_correlation": validation_metrics[1],
        "source_identifiability": _source_identifiability(gram),
        "max_source_correlation": _max_source_correlation(gram),
        "target_duration_seconds": target_meta["duration_seconds"],
        "covered_duration_seconds": covered_count / rate,
        "coverage_ratio": covered_count / total_frames,
        "matched_duration_seconds": matched_frames / rate,
        "matched_coverage_ratio": matched_frames / total_frames,
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
    """Find full-file alternative positions with FFT coarse search."""
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
    exclusion = max(1, round(0.1 * SEGMENT_SCAN_RATE_HZ))
    alternatives = []
    for score, candidate in _fft_normalized_candidates(
            template, samples, top_k=32,
            min_separation=max(exclusion, len(template) // 2)):
        if abs(candidate - first) <= exclusion:
            continue
        # Re-evaluate candidates in the time domain before reporting them.
        comparison = samples[candidate:candidate + len(template)]
        # Discard only the small FIR boundary region for the precise check.
        # Identical source material can otherwise differ at a cut because its
        # neighbouring samples (or file-edge padding) are different.
        trim = min(round(0.04 * SEGMENT_SCAN_RATE_HZ),
                   max(0, (len(template) - 32) // 2))
        precise_template = template[trim:len(template) - trim or None]
        precise_comparison = comparison[trim:len(comparison) - trim or None]
        if _correlation(precise_template, precise_comparison) >= \
                SEGMENT_AMBIGUITY_CORRELATION:
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
            assessed = _linear_timeline_metrics(
                chain, target["artefact_hash"], edges,
                [edge["hash"] for edge in edges])
            alternative_locations = []
            if check_segment_ambiguity:
                for edge in edges:
                    audio = _analysis_proxy(chain, edge["hash"])
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
                            **({"reason_code": exc.reason_code,
                               "analysis_complete": False}
                              if isinstance(exc, ResourceBudgetExceeded) else {}),
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
                        "analysis_sample_rate_hz": assessed["analysis_sample_rate_hz"],
                        "analysis_ranges_seconds": assessed[
                            "analysis_ranges_seconds"],
                        "analysis_complete": assessed["analysis_complete"]})
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
                entries = []
                alternative_locations = []
                for selection in selections:
                    source_hash = selection["source_hash"]
                    if source_hash not in nodes:
                        raise ValueError("a CompSheet source hash is absent from the graph")
                    entries.append({
                        "hash": source_hash,
                        "attributes": {
                            "source_start_seconds": selection["source_start_seconds"],
                            "source_end_seconds": selection["source_end_seconds"],
                            "target_start_seconds": selection["target_start_seconds"],
                            "target_end_seconds": selection["target_end_seconds"],
                            "gain": selection["gain"],
                        },
                    })
                    source_search_audio = _analysis_proxy(chain, source_hash)
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
                    chain, target["artefact_hash"], entries,
                    [selection["source_hash"] for selection in selections])
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "claim_status": "declared_unverified",
                                "reason": f"Comp audio analysis unavailable: {exc}",
                                **({"reason_code": exc.reason_code,
                                   "analysis_complete": False}
                                  if isinstance(exc, ResourceBudgetExceeded) else {}),
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
                            "analysis_sample_rate_hz": assessed["analysis_sample_rate_hz"],
                            "analysis_ranges_seconds": assessed[
                                "analysis_ranges_seconds"],
                            "analysis_complete": assessed["analysis_complete"]})
        return results


def _moving_average(samples, window):
    """Centred, boundary-shrinking moving average without sample loops."""
    values = np.asarray(samples, dtype=np.float32)
    count = values.size
    if window <= 1 or count == 0:
        return values.copy()
    half = window // 2
    positions = np.arange(count)
    low = np.maximum(0, positions - half)
    high = np.minimum(count, positions + (window - half))
    prefix = np.concatenate((np.zeros(1, dtype=np.float64),
                             np.cumsum(values, dtype=np.float64)))
    return ((prefix[high] - prefix[low]) / (high - low)).astype(np.float32)


def _decimate_stage(samples, factor):
    """Compatibility helper: low-pass first, then integer decimation."""
    values = np.asarray(samples, dtype=np.float32)
    if factor <= 1:
        return values.copy()
    taps = _fir_lowpass(1.0, 0.45 / factor)
    return np.convolve(values, taps, mode="same")[::factor].astype(np.float32)


def _antialias_filter(samples, rate, output_rate):
    """Band-limit at the requested output Nyquist without changing rate."""
    values = np.asarray(samples, dtype=np.float32)
    if values.size == 0 or output_rate >= rate:
        return values.copy(), rate
    taps = _fir_lowpass(rate, output_rate * 0.45)
    return np.convolve(values, taps, mode="same").astype(np.float32), rate


def _resample_series(samples, rate, output_rate):
    """Vectorized, bandwidth-safe resampling for analysis diagnostics."""
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size == 0 or not rate or not output_rate:
        return np.empty(0, dtype=np.float32)
    if math.isclose(rate, output_rate, rel_tol=0.0, abs_tol=1e-9):
        return values.copy()
    filtered, _ = _antialias_filter(values, rate, output_rate)
    ratio = rate / output_rate
    rounded_ratio = round(ratio)
    count = int(values.size / rate * output_rate)
    if count <= 0:
        return np.empty(0, dtype=np.float32)
    # Integer downsampling still follows the low-pass above; direct slicing is
    # only the sample-pick stage, never an assertion that filtering is optional.
    if output_rate < rate and math.isclose(ratio, rounded_ratio, abs_tol=1e-9):
        return np.ascontiguousarray(filtered[::rounded_ratio][:count], dtype=np.float32)
    source_times = np.arange(values.size, dtype=np.float64) / rate
    output_times = np.arange(count, dtype=np.float64) / output_rate
    return np.interp(output_times, source_times, filtered).astype(np.float32)


def _mono_resample(audio, output_rate):
    samples = np.mean(np.asarray(audio["frames"], dtype=np.float32),
                      axis=1, dtype=np.float32)
    return _resample_series(samples, audio["sample_rate_hz"], output_rate)


def _correlation(left, right):
    count = min(len(left), len(right))
    if count < 32:
        return 0.0
    left_values = np.asarray(left[:count], dtype=np.float64)
    right_values = np.asarray(right[:count], dtype=np.float64)
    left_values = left_values - np.mean(left_values)
    right_values = right_values - np.mean(right_values)
    denominator = float(np.linalg.norm(left_values) * np.linalg.norm(right_values))
    numerator = float(np.sum(left_values * right_values, dtype=np.float64))
    return numerator / denominator if denominator > 1e-12 else 0.0


def _fft_normalized_candidates(query, haystack, top_k=8, min_separation=1):
    """Return top linear normalized cross-correlation locations via FFT."""
    query = np.asarray(query, dtype=np.float64).reshape(-1)
    haystack = np.asarray(haystack, dtype=np.float64).reshape(-1)
    width = query.size
    candidate_count = haystack.size - width + 1
    if width < 8 or candidate_count < 1:
        return []
    centred = query - np.mean(query)
    query_energy = float(np.sum(centred * centred, dtype=np.float64))
    if query_energy < 1e-12:
        return []
    convolution_size = haystack.size + width - 1
    fft_size = 1 << (convolution_size - 1).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(haystack, fft_size) *
        np.fft.rfft(centred[::-1], fft_size), fft_size)
    numerator = convolution[width - 1:width - 1 + candidate_count]
    prefix = np.concatenate(([0.0], np.cumsum(haystack, dtype=np.float64)))
    square_prefix = np.concatenate(
        ([0.0], np.cumsum(haystack * haystack, dtype=np.float64)))
    local_sum = prefix[width:] - prefix[:-width]
    local_square = square_prefix[width:] - square_prefix[:-width]
    local_variance = np.maximum(local_square - local_sum * local_sum / width, 0.0)
    denominator = np.sqrt(query_energy * local_variance)
    scores = np.divide(numerator, denominator, out=np.zeros_like(numerator),
                       where=denominator > 1e-12)
    ranking = np.abs(scores).copy()
    candidates = []
    separation = max(1, int(min_separation))
    for _ in range(min(top_k, candidate_count)):
        index = int(np.argmax(ranking))
        if not math.isfinite(ranking[index]) or ranking[index] < 0:
            break
        candidates.append((float(scores[index]), index))
        ranking[max(0, index - separation):
                min(candidate_count, index + separation + 1)] = -1.0
    return candidates


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

    # Search the complete master proxy. Competing locations can reduce
    # certainty but never replace the submitter-declared range.
    declared_coarse_start = round((target_start - target["start_seconds"]) * rate)
    exclusion = round(MASTER_PEAK_EXCLUSION_SECONDS * rate)
    alternatives = [
        (score, candidate) for score, candidate in _fft_normalized_candidates(
            source_selected, target_all, top_k=12,
            min_separation=max(exclusion, len(source_selected) // 8))
        if abs(candidate - declared_coarse_start) >= exclusion
    ]
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
            target["start_seconds"] + alternative_start / rate
            if math.isfinite(alternative_score) else None),
        "alignment_margin": alignment_margin if math.isfinite(alignment_margin) else None,
        "alignment_ambiguous": alignment_ambiguous,
        "alignment_coverage_ratio": coverage,
        "search_complete": True,
        "searched_ranges_seconds": [[0.0, target["duration_seconds"]]],
        "analysis_proxy_sample_rate_hz": target.get(
            "proxy_sample_rate_hz", target["sample_rate_hz"]),
    }
    return source_selected, target_selected, diagnostics


def _block_rms(samples, block_size):
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size == 0:
        return np.empty(0, dtype=np.float32)
    full = values.size // block_size
    rows = values[:full * block_size].reshape(full, block_size)
    result = np.sqrt(np.mean(rows * rows, axis=1)) if full else np.empty(0)
    remainder = values[full * block_size:]
    if remainder.size >= block_size // 2:
        result = np.concatenate((result, [np.sqrt(np.mean(remainder * remainder))]))
    return np.asarray(result, dtype=np.float32)


def _rms(samples):
    values = np.asarray(samples, dtype=np.float64).reshape(-1)
    return float(np.sqrt(np.mean(values * values))) if values.size else 0.0


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
                source = _analysis_proxy(chain, edges[0]["hash"])
                master = _analysis_proxy(chain, target["artefact_hash"])
                source_samples, master_samples, alignment = _declared_master_samples(
                    source, master, attrs)
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "unavailable",
                                "claim_status": "declared_unverified",
                                "reason": f"Master comparison unavailable: {exc}",
                                **({"reason_code": exc.reason_code,
                                   "analysis_complete": False,
                                   "search_complete": False}
                                  if isinstance(exc, ResourceBudgetExceeded) else {}),
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
            source_delta = _rms(np.diff(source_samples))
            master_delta = _rms(np.diff(master_samples))
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
                            "search_complete": alignment["search_complete"],
                            "searched_ranges_seconds": alignment[
                                "searched_ranges_seconds"],
                            "analysis_proxy_sample_rate_hz": alignment[
                                "analysis_proxy_sample_rate_hz"],
                            "duration_delta_seconds": round(duration_delta, 6),
                            "waveform_correlation": round(waveform_correlation, 6),
                            "envelope_correlation": round(envelope_correlation, 6),
                            "spectral_similarity": round(spectral_similarity, 6),
                            "loudness_delta_db": round(loudness_delta_db, 4),
                            "manual_review_reasons": manual_review_reasons,
                            "content_match_is_provenance": False})
        return results
