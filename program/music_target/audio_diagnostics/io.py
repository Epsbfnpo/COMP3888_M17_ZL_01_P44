"""Exact-range WAV reading and anti-aliased analysis preprocessing."""

import math
from pathlib import Path

import numpy as np
from music_target.wav_io import inspect_wav, read_frames


class AnalysisUnavailable(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def read_audio(path, start, end, output_rate):
    from scipy.signal import resample_poly
    descriptor = inspect_wav(Path(path))
    start = 0.0 if start is None else start
    end = descriptor.duration_seconds if end is None else end
    if (type(start) not in (int, float) or type(end) not in (int, float) or
            not math.isfinite(start) or not math.isfinite(end) or
            not 0 <= start < end <= descriptor.duration_seconds + 1e-9):
        raise AnalysisUnavailable("invalid_analysis_range", "Declared time range is outside the submitted audio.")
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
    divisor = math.gcd(output_rate, descriptor.sample_rate)
    if output_rate != descriptor.sample_rate:
        mono = resample_poly(mono, output_rate // divisor, descriptor.sample_rate // divisor)
    return {"samples": np.asarray(mono, dtype=np.float32), "sample_rate_hz": output_rate,
            "original_sample_rate_hz": descriptor.sample_rate, "channels": descriptor.channels,
            "range": [float(start), float(end)], "channel_policy": "arithmetic_mean"}


def read_pair(task, sample_rate):
    attrs = task["attributes"]
    values = [attrs.get(key) for key in ("source_start_seconds", "source_end_seconds",
                                        "target_start_seconds", "target_end_seconds")]
    if any(value is not None for value in values) and not all(value is not None for value in values):
        raise AnalysisUnavailable("incomplete_declared_ranges", "Partial declared ranges are not silently completed.")
    return (read_audio(task["source_path"], values[0], values[1], sample_rate),
            read_audio(task["target_path"], values[2], values[3], sample_rate))


def ranges(source, target):
    return {"source": source["range"], "target": target["range"],
            "original_sample_rates_hz": [source["original_sample_rate_hz"], target["original_sample_rate_hz"]],
            "analysis_sample_rate_hz": source["sample_rate_hz"], "channel_policy": "arithmetic_mean"}
