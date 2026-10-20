"""Per-file multiscale acoustic-change segmentation, without musical names.

Local checkerboard novelty is computed from pooled chroma/MFCC features.
Unlike a beat grid, boundary positions and the number of sections are inferred
from the audio. The mean-difference form below is the linear-kernel equivalent
of within-block minus cross-block self-similarity (Foote-style novelty).
"""

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks


def _unit(features):
    return features / np.maximum(np.linalg.norm(features, axis=0, keepdims=True), 1e-12)


def section_features(chroma, timbre, frame_seconds, span, aggregation_seconds=0.5):
    """Time pooling is independent of the beat tracker and comparison target."""
    length = chroma.shape[1]
    stride = max(1, round(aggregation_seconds / frame_seconds))
    starts = np.arange(0, length, stride)
    pool = lambda features: np.stack([np.median(features[:, first:first + stride], axis=1)
                                      for first in starts], axis=1)
    harmony, colour = pool(chroma), pool(timbre)
    centre = np.median(colour, axis=1, keepdims=True)
    scale = np.maximum(np.median(np.abs(colour - centre), axis=1, keepdims=True) * 1.4826, 1.0)
    colour = _unit(np.clip((colour - centre) / scale, -4, 4))
    features = _unit(np.vstack((0.7 * _unit(harmony), 0.3 * colour)))
    seconds = np.minimum(starts * frame_seconds + span[0], span[1])
    return features, seconds


def novelty_curve(features, seconds, context_seconds=4.0):
    """Multiscale local self-similarity contrast, without a forced section count."""
    count = features.shape[1]
    if count < 4:
        return np.zeros(count)
    step = max(float(np.median(np.diff(seconds))), 1e-6)
    curves = []
    cumulative = np.column_stack((np.zeros(features.shape[0]), np.cumsum(features, axis=1)))
    for scale in (0.5, 1.0, 2.0):
        width = max(2, round(context_seconds * scale / step))
        curve = np.zeros(count)
        for index in range(1, count):
            left, right = max(0, index - width), min(count, index + width)
            if min(index - left, right - index) < width / 2:
                continue
            before = (cumulative[:, index] - cumulative[:, left]) / (index - left)
            after = (cumulative[:, right] - cumulative[:, index]) / (right - index)
            curve[index] = float(np.sum((before - after) ** 2))
        curves.append(curve)
    return gaussian_filter1d(np.mean(curves, axis=0), sigma=max(0.5, 0.35 / step), mode="nearest")


def detect_sections(features, seconds, span, parameters):
    """Return full-coverage intervals and boundary strengths from acoustic novelty."""
    count = features.shape[1]
    if count < 4:
        return [list(map(float, span))], {"boundary_times_seconds": [], "boundary_strengths": []}
    curve = novelty_curve(features, seconds, parameters.get("section_context_seconds", 12.0))
    minimum = parameters.get("section_minimum_seconds", 6.0)
    prominence = parameters.get("section_prominence", 0.03)
    step = max(float(np.median(np.diff(seconds))), 1e-6)
    peaks, _ = find_peaks(curve, distance=max(1, round(minimum / step)), prominence=prominence)
    peaks = [int(index) for index in peaks
             if span[0] + minimum <= seconds[index] <= span[1] - minimum]
    boundaries = [float(span[0])] + [float(seconds[index]) for index in peaks] + [float(span[1])]
    return [[a, b] for a, b in zip(boundaries, boundaries[1:]) if b > a], {
        "boundary_times_seconds": boundaries[1:-1],
        "boundary_strengths": [float(curve[index]) for index in peaks],
        "method": "multiscale_feature_novelty", "context_seconds": parameters.get("section_context_seconds", 12.0),
        "minimum_section_seconds": minimum, "prominence": prominence,
        "musical_function_labels_available": False}
