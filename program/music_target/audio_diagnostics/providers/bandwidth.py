"""Report-only content bandwidth of one audio file: where its audio really stops.

A lossy encoder removes everything above its low-pass frequency, and the gap
survives decoding. A WAV or FLAC made from an MP3 still stops where the MP3
did; an MP3 re-encoded at a higher bitrate still stops where the lower bitrate
did; audio resampled up to 96 kHz has nothing above the original Nyquist
frequency. The long-term spectrum of the decoded audio is compared with what
the container and bitrate claim.

This is a heuristic, never a score. A band limit says where the audio stops,
not why: AI generators produce band-limited audio natively (all 25 Lumina
benchmark WAVs stop between 17.4 and 19.9 kHz), mastering can low-pass a recording,
quiet material can hide a limit in its noise, and none of it shows who made a
file. The messages therefore name the alternatives instead of a verdict.
"""

import numpy as np

from ..contracts import result
from ..io import AnalysisUnavailable, _open

# Typical LAME/FFmpeg low-pass frequency (Hz) for MPEG-1 stereo at a nominal
# bitrate (kbps). An MP3 whose content stops well below this was probably made
# from a lower-bitrate source.
TYPICAL_MP3_LOWPASS = ((320, 20000), (256, 19500), (224, 19000), (192, 18500), (160, 17500),
                       (128, 16500), (112, 15500), (96, 15000), (80, 13500), (64, 11000))
STRONG_FLOOR_MARGIN_DB = 12.0
# Standard sample rates audio is commonly resampled up from.
STANDARD_RATES = (8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000)

LIMITATIONS = [
    "A band limit shows where the decoded audio stops, not who made the file or why.",
    "Mastering with a low-pass filter, band-limited source material (speech, old recordings) or very quiet "
    "audio can look the same as a lossy or resampled history.",
    "Thresholds are exploratory and uncalibrated; absence of a band limit does not show a file is original.",
]


def spectrum_profile(blocks, total_frames, rate, parameters):
    """90th-percentile power spectrum (dB) of ``spectrum_frames`` frames spread evenly over the whole file.

    Frames are sampled across the full duration (not a prefix), so the count
    bounds cost without limiting which part of the file is described.
    """
    size, limit = parameters["frame_size"], parameters["spectrum_frames"]
    if total_frames < size:
        raise AnalysisUnavailable("insufficient_signal", "Too little audio to measure its bandwidth.")
    stride = max(size, total_frames // limit)
    window = np.hanning(size)
    rows, carry, position, next_start = [], np.zeros(0, dtype=np.float32), 0, 0
    for block in blocks(0, total_frames):
        carry = np.concatenate((carry, block.mean(axis=1)))
        while next_start + size <= position + len(carry) and len(rows) < limit:
            offset = next_start - position
            frame = carry[offset:offset + size]
            if np.sqrt(np.mean(frame.astype(np.float64) ** 2)) >= 1e-4:
                rows.append(np.abs(np.fft.rfft(frame * window)) ** 2)
            next_start += stride
        keep = max(0, next_start - position)
        position += min(keep, len(carry))
        carry = carry[min(keep, len(carry)):]
    if len(rows) < 4:
        raise AnalysisUnavailable("insufficient_signal", "Too few non-silent frames to measure bandwidth.")
    level = np.percentile(np.array(rows), parameters["level_percentile"], axis=0)
    freqs = np.fft.rfftfreq(size, 1 / rate)
    db = 10 * np.log10(level + 1e-20)
    reference = float(np.median(db[(freqs >= 1000) & (freqs < 4000)]))
    return freqs, db - reference, len(rows)


def _median(values):
    return float(np.median(values)) if len(values) else float("nan")


def _transition(freqs, rel, cutoff):
    """Where, within 1 kHz of a candidate, the level first falls halfway from below to above."""
    middle = (cutoff["level_below_db"] + cutoff["level_above_db"]) / 2
    window = (freqs >= cutoff["frequency_hz"] - 1000) & (freqs <= cutoff["frequency_hz"] + 1000)
    span = freqs[window]
    # Smooth over about 100 Hz so a single noisy bin cannot set the edge.
    width = max(1, int(round(100 / (freqs[1] - freqs[0]))))
    level = np.convolve(rel[window], np.ones(width) / width, mode="same")
    below_middle = np.nonzero(level < middle)[0]
    return float(span[below_middle[0]]) if len(below_middle) else cutoff["frequency_hz"]


def find_cutoffs(freqs, rel, rate, parameters):
    """The strongest sharp cliff and the strongest step into a flat noise floor, if any."""
    nyquist = rate / 2
    low = float(np.ceil(max(4000.0, 0.35 * nyquist) / 100.0) * 100.0)
    cliff = step = None
    for fc in np.arange(low, nyquist - 600, 100.0):
        below = _median(rel[(freqs >= fc - 1000) & (freqs < fc - 150)])
        above = _median(rel[(freqs >= fc + 150) & (freqs < min(fc + 2000, nyquist - 100))])
        rest = rel[(freqs >= fc + 300) & (freqs < nyquist - 100)]
        if not len(rest) or np.isnan(above):
            continue
        drop = below - above
        if (drop >= parameters["cliff_drop_db"] and float(np.percentile(rest, 95)) <= below - STRONG_FLOOR_MARGIN_DB
                and (cliff is None or drop > cliff["drop_db"])):
            cliff = {"kind": "sharp_cliff", "frequency_hz": float(fc), "drop_db": round(drop, 1),
                     "level_below_db": round(below, 1), "level_above_db": round(above, 1)}
    top = parameters["step_search_top_fraction"] * nyquist
    for fc in np.arange(low, top - 1000, 100.0):
        below = _median(rel[(freqs >= fc - 1500) & (freqs < fc - 500)])
        bands = [_median(rel[(freqs >= f) & (freqs < f + 500)]) for f in np.arange(fc + 500, top, 500.0)]
        if len(bands) < 2:
            continue
        above, spread = float(np.median(bands)), float(max(bands) - min(bands))
        drop = below - above
        if (drop >= parameters["step_drop_db"] and spread <= parameters["flat_floor_spread_db"]
                and (step is None or drop > step["drop_db"])):
            step = {"kind": "step_into_flat_floor", "frequency_hz": float(fc), "drop_db": round(drop, 1),
                    "level_below_db": round(below, 1), "level_above_db": round(above, 1),
                    "floor_spread_db": round(spread, 1)}
    found = [item for item in (cliff, step) if item]
    for item in found:
        item["frequency_hz"] = round(_transition(freqs, rel, item), 1)
    return found


def typical_lowpass(bitrate_kbps):
    for bitrate, lowpass in TYPICAL_MP3_LOWPASS:
        if bitrate_kbps >= bitrate:
            return lowpass
    return None


def assess(edge, rate, container, bitrate_kbps=None):
    """Compare the content edge with what the container and bitrate claim."""
    nyquist = rate / 2
    if container == "MP3":
        expected = typical_lowpass(bitrate_kbps or 0) if rate >= 32000 else None
        if expected is None:
            return {"status": "not_assessed", "reason": "No typical low-pass frequency is known for this MP3's "
                                                        "sample rate or bitrate."}
        if edge is not None and edge < expected - 1500:
            return {"status": "content_band_lower_than_bitrate_suggests", "typical_lowpass_hz": expected,
                    "reason": f"The audio stops at {edge / 1000:.1f} kHz, but a {bitrate_kbps} kbps MP3 usually "
                              f"reaches about {expected / 1000:.1f} kHz: it was probably re-encoded from a lower "
                              "bitrate, or its source was already band-limited."}
        return {"status": "consistent_with_bitrate", "typical_lowpass_hz": expected,
                "reason": "The content band matches what this bitrate usually keeps."}
    if edge is None:
        return {"status": "no_band_limit_found",
                "reason": "No sharp band limit below the Nyquist frequency was found."}
    base = next((value for value in STANDARD_RATES
                 # Content ending just below a lower standard rate's Nyquist frequency.
                 if value < rate and 0.9 * value / 2 <= edge <= 1.01 * value / 2), None)
    if 11000 <= edge <= 20500 and edge < 0.9 * nyquist:
        # Checked first: lossy low-passes are far more common in music than
        # material resampled up from 22.05-32 kHz, whose Nyquist falls here too.
        also = f", or resampled up from {base} Hz audio" if base else ""
        return {"status": "band_limit_like_lossy_encoding",
                "reason": f"This {container} file is lossless, but its audio stops at {edge / 1000:.1f} kHz with a "
                          "sharp limit like a lossy encoder's low-pass. It may have been made from an MP3 or similar "
                          f"file{also}, or generated or filtered to this bandwidth (AI generators often are)."}
    if base:
        return {"status": "consistent_with_upsampling", "likely_source_rate_hz": base,
                "reason": f"The file is sampled at {rate} Hz, but its audio stops at {edge / 1000:.2f} kHz, the "
                          f"Nyquist frequency of {base} Hz audio: it was probably resampled up from {base} Hz, "
                          "or its source never had content above that band."}
    return {"status": "band_limited",
            "reason": f"The audio stops at {edge / 1000:.1f} kHz, below the Nyquist frequency of "
                      f"{nyquist / 1000:.1f} kHz."}


def analyse(task, parameters, context=None):
    path = task["source_path"]
    from music_target.audio_formats import detect
    from music_target.mp3_io import inspect_mp3
    container = detect(path).container
    bitrate = inspect_mp3(path).nominal_bitrate_kbps if container == "MP3" else None
    descriptor, blocks = _open(path)
    freqs, rel, frames_used = spectrum_profile(blocks, descriptor.frame_count, descriptor.sample_rate, parameters)
    cutoffs = find_cutoffs(freqs, rel, descriptor.sample_rate, parameters)
    edge = min((item["frequency_hz"] for item in cutoffs), default=None)
    assessment = assess(edge, descriptor.sample_rate, container, bitrate)
    value = result(task, "bandwidth", "bandwidth_observation", assessment["reason"])
    value.update(
        analysis_complete=True,
        analysed_ranges={"whole_file": [0.0, descriptor.duration_seconds],
                         "original_sample_rates_hz": [descriptor.sample_rate],
                         "channel_policy": "arithmetic_mean"},
        metrics={"container": container, "sample_rate_hz": descriptor.sample_rate,
                 "nyquist_hz": descriptor.sample_rate / 2, "nominal_bitrate_kbps": bitrate,
                 "content_edge_hz": edge, "cutoffs": cutoffs, "frames_analysed": frames_used,
                 "assessment": assessment},
        limitations=list(LIMITATIONS))
    return value
