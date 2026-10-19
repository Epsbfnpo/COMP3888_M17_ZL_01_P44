"""SpectroMap peak detection plus an explicit, experimental pair matcher."""

from importlib.metadata import version
import numpy as np
from scipy.signal import spectrogram

from ..contracts import result
from ..io import AnalysisUnavailable, read_pair, ranges
from .alignment import align_tokens

PAIR_FANOUT = 3
PAIR_ZONE_FRAMES = 32


def window_frames(parameters):
    step = (parameters["n_fft"] - parameters["noverlap"]) / parameters["sample_rate_hz"]
    return max(1, round(parameters["peak_window_seconds"] / step))


def _windowed_peak_search(power, parameters):
    # Upstream derives its peak distance from the size of the array it is
    # given, so one call over a whole file makes peaks sparser as the file
    # gets longer and a short excerpt stops sharing peaks with a long source.
    # Fixed-duration, zero-padded windows keep that distance constant in
    # seconds for every file; padding is all-zero and yields no peaks.
    from spectromap.spectromap import peak_search
    width = window_frames(parameters)
    mask = np.zeros(power.shape, dtype=bool)
    for start in range(0, power.shape[1], width):
        block = power[:, start:start + width]
        padded = np.zeros((power.shape[0], width), dtype=power.dtype)
        padded[:, :block.shape[1]] = block
        found, _ = peak_search(padded, fraction=parameters["fraction"], condition=parameters["condition"])
        mask[:, start:start + block.shape[1]] = found[:, :block.shape[1]]
    return mask


def _tokens(audio, parameters):
    fft = parameters["n_fft"]
    if len(audio["samples"]) < fft:
        raise AnalysisUnavailable("insufficient_signal", "Too little signal for SpectroMap.")
    _, _, power = spectrogram(audio["samples"], fs=parameters["sample_rate_hz"],
                              nperseg=fft, nfft=fft, noverlap=parameters["noverlap"])
    if min(power.shape[0], window_frames(parameters)) * parameters["fraction"] < 1:
        raise AnalysisUnavailable("insufficient_spectral_frames", "SpectroMap needs more spectral bins/frames for its peak distance.")
    mask = _windowed_peak_search(power, parameters)
    # Exclude zero/near-zero power, including upstream flatten-boundary peaks.
    mask &= power > float(np.max(power)) * 1e-6
    frequency, time = np.nonzero(mask)
    points = sorted(zip(time.tolist(), frequency.tolist()))
    tokens = []
    # Local peak pairs are the matching vocabulary, not raw dB values. Each
    # peak pairs with its next PAIR_FANOUT later peaks within PAIR_ZONE_FRAMES;
    # both are algorithm parameters, not input caps. Exact bins and frame
    # gaps keep chance collisions low now that windowed peaks are denser.
    for index, (time, frequency) in enumerate(points):
        paired = 0
        for next_time, next_frequency in points[index + 1:]:
            if next_time - time > PAIR_ZONE_FRAMES or paired == PAIR_FANOUT:
                break
            if next_time > time:
                tokens.append((time, (frequency, next_frequency, next_time - time)))
                paired += 1
    return tokens, len(points)


def analyse(task, parameters):
    source, target = read_pair(task, parameters["sample_rate_hz"])
    source_tokens, source_count = _tokens(source, parameters)
    target_tokens, target_count = _tokens(target, parameters)
    if not source_tokens or not target_tokens:
        raise AnalysisUnavailable("insufficient_peaks", "Insufficient SpectroMap peak pairs in a selected range.")
    step = (parameters["n_fft"] - parameters["noverlap"]) / parameters["sample_rate_hz"]
    matches, metrics = align_tokens(source_tokens, target_tokens, step, source, target, parameters)
    metrics.update(source_peaks=source_count, target_peaks=target_count,
                   peak_pair_frequency_bin_width=1, peak_pair_time_bin_width_frames=1,
                   peak_pair_fanout=PAIR_FANOUT, peak_pair_zone_frames=PAIR_ZONE_FRAMES,
                   peak_window_frames=window_frames(parameters),
                   peak_window_seconds=window_frames(parameters) * step)
    value = result(task, "spectromap")
    value.update(provider_version=version("spectromap"), analysis_complete=True,
                 analysed_ranges=ranges(source, target), matches=matches, metrics=metrics)
    value["limitations"].append("Peak-pair alignment is a local experimental adapter, not an upstream provenance classifier.")
    if not matches:
        value.update(reason_code="no_match_candidates", message="No SpectroMap candidate offsets; non-matching is inconclusive.")
    return value
