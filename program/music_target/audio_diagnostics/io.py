"""Exact-range WAV reading and anti-aliased analysis preprocessing."""

import math
from pathlib import Path

import numpy as np
from music_target.wav_io import inspect_wav, read_frames
from music_target.time_ranges import content_range_checks


class AnalysisUnavailable(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _decode_mono(path, start, end):
    descriptor = inspect_wav(Path(path))
    start = 0.0 if start is None else start
    end = descriptor.duration_seconds if end is None else end
    checks = content_range_checks(start, end, descriptor.duration_seconds, descriptor.sample_rate)
    if any(not check["passed"] for check in checks):
        raise AnalysisUnavailable("invalid_analysis_range", "Declared time range is outside the submitted audio.")
    end = min(end, descriptor.duration_seconds)
    first = round(start * descriptor.sample_rate)
    last = min(descriptor.frame_count, round(end * descriptor.sample_rate))
    chunks = [block for _, block in read_frames(path, descriptor, first, last)]
    if not chunks:
        raise AnalysisUnavailable("insufficient_signal", "The selected audio range is empty.")
    frames = np.concatenate(chunks, axis=0)
    mono = np.mean(frames, axis=1)
    rms_channels = float(np.sqrt(np.mean(frames.astype(np.float64) ** 2)))
    rms_mono = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
    if rms_channels < 1e-9:
        raise AnalysisUnavailable("silent_signal", "Selected audio is silent; no content correspondence can be assessed.")
    if rms_mono < rms_channels * 1e-5:
        raise AnalysisUnavailable("channel_cancellation", "Mono conversion cancels the submitted channels; no non-match is inferred.")
    mono.setflags(write=False)
    return mono, descriptor, [float(start), float(end)]


def _cached(cache, key, compute):
    # One worker runs every provider for a task, so each file range is decoded
    # once and each analysis rate is resampled once. Failures are cached too,
    # so every provider reports the same input limitation.
    if cache is None:
        return compute()
    if key not in cache:
        try:
            cache[key] = (True, compute())
        except AnalysisUnavailable as exc:
            cache[key] = (False, exc)
    ok, value = cache[key]
    if not ok:
        raise value
    return value


def read_audio(path, start, end, output_rate, cache=None):
    from scipy.signal import resample_poly
    def resampled():
        mono, descriptor, span = _cached(cache, ("mono", str(path), start, end),
                                         lambda: _decode_mono(path, start, end))
        divisor = math.gcd(output_rate, descriptor.sample_rate)
        if output_rate != descriptor.sample_rate:
            mono = resample_poly(mono, output_rate // divisor, descriptor.sample_rate // divisor)
        samples = np.array(mono, dtype=np.float32)
        samples.setflags(write=False)  # shared by providers through the cache
        return {"samples": samples, "sample_rate_hz": output_rate,
                "original_sample_rate_hz": descriptor.sample_rate, "channels": descriptor.channels,
                "range": span, "channel_policy": "arithmetic_mean"}
    return _cached(cache, ("rate", str(path), start, end, output_rate), resampled)


RANGE_FIELDS = ("source_start_seconds", "source_end_seconds", "target_start_seconds", "target_end_seconds")


def read_pair(task, sample_rate, cache=None):
    attrs = task["attributes"]
    values = [attrs.get(key) for key in RANGE_FIELDS]
    # An AAF range names only the source seconds used; the target is analysed whole.
    aaf_source_only = (task.get("range_source") == "aaf_source_range" and values[2] is None
                       and values[3] is None and values[0] is not None and values[1] is not None)
    if (not aaf_source_only and any(value is not None for value in values)
            and not all(value is not None for value in values)):
        missing = ", ".join(key for key, value in zip(RANGE_FIELDS, values) if value is None)
        raise AnalysisUnavailable("incomplete_declared_ranges",
                                  "Partial declared ranges are not silently completed; missing: " + missing + ".")
    source = read_audio(task["source_path"], values[0], values[1], sample_rate, cache)
    try:
        target = read_audio(task["target_path"], values[2], values[3], sample_rate, cache)
    except AnalysisUnavailable as exc:
        if exc.code == "invalid_analysis_range" and task.get("range_source") == "derived_from_declared_source_duration":
            raise AnalysisUnavailable("derived_range_outside_target",
                                      "The target end derived from the declared source duration lies outside "
                                      "the target audio; declare target_end_seconds explicitly.") from None
        raise
    return source, target


def ranges(source, target):
    return {"source": source["range"], "target": target["range"],
            "original_sample_rates_hz": [source["original_sample_rate_hz"], target["original_sample_rate_hz"]],
            "analysis_sample_rate_hz": source["sample_rate_hz"], "channel_policy": "arithmetic_mean"}
