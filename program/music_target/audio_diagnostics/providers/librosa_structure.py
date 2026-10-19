"""Abstract structure and cross-file feature correspondences, without labels."""

import numpy as np
from scipy.signal import find_peaks

from ..contracts import result
from ..io import AnalysisUnavailable, read_pair, ranges


def _features(audio, parameters, librosa):
    samples = audio["samples"]
    if len(samples) < parameters["n_fft"]:
        raise AnalysisUnavailable("insufficient_signal", "Too little audio for musical structure features.")
    power = np.abs(librosa.stft(samples, n_fft=parameters["n_fft"], hop_length=parameters["hop_length"])) ** 2
    chroma = librosa.feature.chroma_stft(S=power, sr=parameters["sample_rate_hz"], tuning=0)
    mel = librosa.feature.melspectrogram(S=power, sr=parameters["sample_rate_hz"], n_mels=40)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(mel), n_mfcc=14)[1:]
    rms = librosa.feature.rms(S=np.sqrt(power), frame_length=parameters["n_fft"])
    stride = max(1, round(parameters["aggregation_seconds"] * parameters["sample_rate_hz"] / parameters["hop_length"]))
    boundaries = np.arange(0, power.shape[1], stride)
    pool = lambda data: np.stack([np.mean(data[:, start:start + stride], axis=1) for start in boundaries], axis=1)
    data = np.vstack((pool(chroma), pool(mfcc), pool(np.log1p(rms))))
    seconds = boundaries * parameters["hop_length"] / parameters["sample_rate_hz"]
    return data, seconds


def _unit(data):
    norm = np.linalg.norm(data, axis=0, keepdims=True)
    return data / np.maximum(norm, 1e-12)


def _sections(features, times, audio):
    if features.shape[1] < 3:
        return [audio["range"]]
    novelty = np.linalg.norm(np.diff(features, axis=1), axis=0)
    prominence = float(np.median(np.abs(novelty - np.median(novelty))))
    indices, _ = find_peaks(novelty, prominence=max(prominence, 1e-6))
    positions = [audio["range"][0]] + [float(times[index + 1] + audio["range"][0]) for index in indices] + [audio["range"][1]]
    return [[a, b] for a, b in zip(positions, positions[1:]) if b > a]


def _repetitions(features, times, audio, parameters, librosa):
    if features.shape[1] < 5:
        return []
    width = min(2, (features.shape[1] - 1) // 2 - 1)
    recurrence = librosa.segment.recurrence_matrix(features, width=width, metric="cosine", sparse=True, sym=True)
    left, right = recurrence.nonzero()
    return [{"first_seconds": float(times[a] + audio["range"][0]),
             "repeat_seconds": float(times[b] + audio["range"][0]),
             "feature_cosine": float(np.clip(features[:, a] @ features[:, b], -1, 1))}
            for a, b in zip(left, right) if b > a and
            features[:, a] @ features[:, b] >= parameters["candidate_cosine_similarity"]]


def analyse(task, parameters, context=None):
    import librosa
    source, target = read_pair(task, parameters["sample_rate_hz"], (context or {}).get("audio_cache"))
    source_data, source_times = _features(source, parameters, librosa)
    target_data, target_times = _features(target, parameters, librosa)
    joined = np.concatenate((source_data[12:], target_data[12:]), axis=1)
    mean = np.mean(joined, axis=1, keepdims=True)
    scale = np.maximum(np.std(joined, axis=1, keepdims=True), 1e-5)
    def normalize(data):
        return _unit(np.vstack((0.7 * _unit(data[:12]), 0.3 * _unit((data[12:] - mean) / scale))))
    source_features, target_features = normalize(source_data), normalize(target_data)
    # Cross-file frame correspondences are summarised, not listed: on real
    # recordings unrelated material scored as high as related material, so
    # individual "matches" would mislead. Version relationships are measured
    # by the music_semantics provider instead.
    with np.errstate(all="ignore"):  # Accelerate BLAS raises spurious FP flags
        best = np.max(np.clip(source_features.T @ target_features, -1, 1), axis=0)
    value = result(task, "librosa")
    value.update(provider_version=librosa.__version__, analysis_complete=True,
                 analysed_ranges=ranges(source, target), matches=[], metrics={
                     "source_sections_seconds": _sections(source_features, source_times, source),
                     "target_sections_seconds": _sections(target_features, target_times, target),
                     "source_repetition_candidates": _repetitions(source_features, source_times, source, parameters, librosa),
                     "target_repetition_candidates": _repetitions(target_features, target_times, target, parameters, librosa),
                     "mean_best_feature_cosine": float(np.mean(best)),
                     "best_feature_cosine_quantiles": {str(q): float(np.quantile(best, q)) for q in (0.1, 0.5, 0.9)},
                     "source_aggregated_frames": int(source_features.shape[1]),
                     "target_aggregated_frames": int(target_features.shape[1]),
                 })
    value["limitations"].append("Abstract feature sections have no verse/chorus labels; similar music can have similar structure.")
    value["limitations"].append("Cross-file feature similarity is summary context only; it did not separate related "
                                "from unrelated recordings and lists no correspondences.")
    return value
