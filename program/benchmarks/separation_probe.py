"""Exploratory probe: how does a Demucs-separated stem differ from a submitted stem?

Phase 1 of the source-separation investigation (docs/SOURCE_SEPARATION_POC.md).
It is standalone: the evaluator never imports it, and nothing here scores,
labels or classifies a stem. Every measurement is exploratory and
uncalibrated.

    python -B benchmarks/separation_probe.py --mix final.wav --genuine-stem vocals.wav \
        --source vocals --output-dir /tmp/probe --model-repo /path/to/demucs_models

Demucs ``htdemucs`` extracts ``--source`` from the mix, then three pairs are
compared on a mono downmix: A submitted stem vs mix, B derived stem vs mix,
C submitted stem vs derived stem. Weights are read from ``--model-repo``
(offline); fetching them from the network needs ``--allow-download``.
torch and demucs are imported only when the separation runs, so this module
and its diagnostics work with numpy and soundfile alone.
"""

import argparse
import hashlib
import json
import random
import sys
import time
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf

REPORT_TYPE = "separation_probe"
SCHEMA_VERSION = "1.0"
STATUS = "exploratory_uncalibrated"
MODEL_NAME = "htdemucs"
SOURCES = ("drums", "bass", "vocals", "other")
REPORT_NAME = "separation_report.json"

N_FFT = 4096
HOP = 1024
MAX_FRAMES = 2000
# Frames quieter than -80 dBFS RMS carry no spectral information.
SILENCE_RMS = 1e-4
# Spectral levels are floored 80 dB below each frame's reference peak, so
# empty bins cannot dominate a dB comparison.
FLOOR_DB = -80.0
BANDS_HZ = ((0, 250), (250, 2000), (2000, 8000), (8000, 16000), (16000, None))
CONTENT_EDGE_DB = -70.0
COARSE_RATE_HZ = 4000
AMBIGUOUS_PEAK_RATIO = 0.8

NOTICE = ("Exploratory and uncalibrated. These measurements describe how three signals differ; they do not "
          "label any stem, establish where it came from, or feed any score.")
LIMITATIONS = [
    "No threshold has been calibrated: no value here separates production stems from separated stems.",
    "A good separator makes a production stem resemble its own output, so high similarity between the "
    "submitted and derived stems is expected either way and does not show derivation.",
    "Only Demucs htdemucs is used. A stem produced by another separator, or processed afterwards, can differ "
    "from this model's output in every measurement.",
    "All comparisons use a mono downmix; stereo image and channel-specific artefacts are not measured.",
    "Mix-bus processing (compression, limiting, reverb), live-recording bleed and sparse instruments all change "
    "these measurements without any separation.",
    "Crosstalk of the derived stem is measured against the other outputs of the same separation run, which are "
    "constrained to sum to the mix, so it is not independent of the separation.",
    "Demucs works at 44.1 kHz: a mix at a higher rate is resampled and the derived stem holds nothing above "
    "22.05 kHz by construction.",
]


class ProbeError(Exception):
    """An input, dependency or output problem reported without a traceback."""


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _num(value, digits=6):
    """A JSON-safe number: rounded, or None when not finite."""
    value = float(value)
    return round(value, digits) if np.isfinite(value) else None


def _db(ratio):
    return _num(10 * np.log10(ratio)) if ratio > 0 else None


def load_audio(path):
    path = Path(path)
    try:
        info = sf.info(str(path))
        audio, rate = sf.read(str(path), dtype="float64", always_2d=True)
    except (RuntimeError, sf.LibsndfileError) as exc:
        raise ProbeError(f"Cannot read audio {path}: {exc}") from exc
    if not len(audio):
        raise ProbeError(f"Audio file {path} is empty.")
    return audio, rate, {"path": str(path), "sha256": sha256(path), "format": info.format,
                         "subtype": info.subtype, "sample_rate_hz": rate, "channels": audio.shape[1],
                         "frames": len(audio), "duration_seconds": _num(len(audio) / rate)}


def resample(audio, from_rate, to_rate):
    if from_rate == to_rate:
        return audio
    try:
        from scipy.signal import resample_poly
    except ImportError as exc:
        raise ProbeError("Sample rates differ and scipy is needed to resample: "
                         "pip install -r requirements-separation.txt") from exc
    factor = gcd(int(from_rate), int(to_rate))
    return resample_poly(audio, int(to_rate) // factor, int(from_rate) // factor, axis=0)


def to_channels(audio, channels):
    if audio.shape[1] == channels:
        return audio
    if audio.shape[1] == 1:
        return np.repeat(audio, channels, axis=1)
    return np.repeat(audio.mean(axis=1, keepdims=True), channels, axis=1)


def fit_length(audio, frames):
    if len(audio) >= frames:
        return audio[:frames]
    return np.pad(audio, ((0, frames - len(audio)), (0, 0)))


def mono(audio):
    return audio.mean(axis=1)


# Alignment and waveform measures


def _xcorr(reference, query):
    """Full cross-correlation c[k] = sum r[n + k] q[n], lags -(len(q) - 1) .. len(r) - 1."""
    size = 1 << int(len(reference) + len(query) - 1).bit_length()
    product = np.fft.rfft(reference, size) * np.conj(np.fft.rfft(query, size))
    full = np.fft.irfft(product, size)
    lags = np.concatenate((np.arange(-(len(query) - 1), 0), np.arange(len(reference))))
    return lags, np.concatenate((full[size - (len(query) - 1):], full[:len(reference)]))


def overlap(reference, query, lag):
    """Equal-length segments where query[n] sits at reference[n + lag]."""
    start_r, start_q = max(lag, 0), max(-lag, 0)
    count = max(min(len(reference) - start_r, len(query) - start_q), 0)
    return reference[start_r:start_r + count], query[start_q:start_q + count]


def _ncc(reference, query, lag):
    r, q = overlap(reference, query, lag)
    norm = np.sqrt(np.dot(r, r) * np.dot(q, q))
    return float(np.dot(r, q) / norm) if norm > 0 else 0.0


def _peaks(lags, magnitude, guard, count):
    """The strongest local maxima, at least ``guard`` lags apart."""
    inner = np.nonzero((magnitude[1:-1] >= magnitude[:-2]) & (magnitude[1:-1] >= magnitude[2:]))[0] + 1
    order = np.concatenate((inner, [0, len(magnitude) - 1]))
    picked = []
    for index in order[np.argsort(-magnitude[order], kind="stable")]:
        if all(abs(int(lags[index]) - int(lags[other])) > guard for other in picked):
            picked.append(index)
            if len(picked) == count:
                break
    return picked


def align(reference, query, rate, max_lag_seconds, candidates=8):
    """Lag of a mono query against a mono reference.

    A pooled (about 4 kHz) cross-correlation proposes the strongest peaks; each
    is refined at the full rate, because on periodic material the coarse
    maximum can sit one period away from the true lag.
    """
    factor = max(1, int(rate // COARSE_RATE_HZ))
    def pool(x):
        usable = len(x) // factor * factor
        return x[:usable].reshape(-1, factor).mean(axis=1) if usable else x[:1]
    all_lags, corr = _xcorr(pool(reference), pool(query))
    limit = int(np.ceil(max_lag_seconds * rate / factor))
    window = np.abs(all_lags) <= limit
    lags, magnitude = all_lags[window], np.abs(corr[window])
    guard = max(1, int(0.05 * rate / factor))
    max_lag = int(round(max_lag_seconds * rate))
    best_score, lag = 0.0, 0
    for index in _peaks(lags, magnitude, 2 * factor, candidates):
        centre = int(lags[index]) * factor
        for candidate in range(max(centre - 2 * factor, -max_lag), min(centre + 2 * factor, max_lag) + 1):
            score = _ncc(reference, query, candidate)
            if abs(score) > abs(best_score):
                best_score, lag = score, candidate
    coarse = np.abs(lag / factor - lags) <= 2
    peak = magnitude[coarse].max() if coarse.any() else 0.0
    rest = magnitude[np.abs(lags - lag / factor) > guard]
    outside = np.abs(corr[~window])
    return {"lag_samples": lag, "lag_seconds": _num(lag / rate), "polarity": -1 if best_score < 0 else 1,
            "normalised_correlation_at_lag": _num(best_score),
            "secondary_peak_ratio": _num(rest.max() / peak) if len(rest) and peak > 0 else 0.0,
            "lag_at_search_limit": abs(lag) >= max_lag - factor,
            "stronger_peak_outside_limit": bool(len(outside) and outside.max() > peak),
            "search_limit_seconds": _num(max_lag_seconds)}


def gain_to_reference(reference, query):
    """Least-squares scale that best maps query onto reference."""
    energy = np.dot(query, query)
    return float(np.dot(reference, query) / energy) if energy > 0 else 0.0


def waveform_measures(reference, query, gain):
    centred_r, centred_q = reference - reference.mean(), query - query.mean()
    norm = np.sqrt(np.dot(centred_r, centred_r) * np.dot(centred_q, centred_q))
    residual = reference - gain * query
    return {"pearson_correlation": _num(np.dot(centred_r, centred_q) / norm) if norm > 0 else None,
            "gain_db": _num(20 * np.log10(abs(gain))) if gain else None,
            "residual_to_reference_db": _db(np.dot(residual, residual) / np.dot(reference, reference))
            if np.dot(reference, reference) > 0 else None}


def si_sdr(reference, estimate):
    """Scale-invariant signal-to-distortion ratio (dB) of estimate against reference."""
    energy = np.dot(reference, reference)
    if energy <= 0:
        return None
    target = np.dot(estimate, reference) / energy * reference
    noise = estimate - target
    noise_energy = np.dot(noise, noise)
    return _db(np.dot(target, target) / noise_energy) if noise_energy > 0 else None


# Spectral measures


def frame_starts(frames):
    if frames < N_FFT:
        return np.zeros(0, dtype=int)
    count = min(MAX_FRAMES, (frames - N_FFT) // HOP + 1)
    return np.unique(np.linspace(0, frames - N_FFT, count).round().astype(int))


def power_frames(signal, starts):
    """Hann-windowed power spectra at the given starts, plus each frame's RMS."""
    if not len(starts):
        return np.zeros((0, N_FFT // 2 + 1), dtype=np.float32), np.zeros(0)
    frames = np.stack([signal[start:start + N_FFT] for start in starts])
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return (np.abs(np.fft.rfft(frames * np.hanning(N_FFT), axis=1)) ** 2).astype(np.float32), rms


def _floored_db(power, peak):
    return 10 * np.log10(np.maximum(power, peak * 10 ** (FLOOR_DB / 10)))


def _band_energy(power, freqs, rate):
    total = float(power.sum())
    out = {}
    for low, high in BANDS_HZ:
        name = f"{low}-{high if high else 'nyquist'}_hz"
        if low >= rate / 2:
            out[name] = None
            continue
        mask = (freqs >= low) & (freqs < (high if high else np.inf))
        out[name] = float(power[mask].sum()) / total if total > 0 else None
    return out


def spectral_measures(reference, query, gain, rate):
    starts = frame_starts(len(reference))
    ref_power, ref_rms = power_frames(reference, starts)
    query_power, query_rms = power_frames(query, starts)
    active = (ref_rms >= SILENCE_RMS) & (query_rms >= SILENCE_RMS)
    result = {"frames_compared": int(active.sum()), "frames_sampled": int(len(starts))}
    if active.sum() < 4:
        return {**result, "log_spectral_correlation": None, "ltas_db_correlation": None,
                "log_spectral_distance_db": None, "band_level_difference_db": None}
    ref_power, query_power = ref_power[active], query_power[active] * gain ** 2
    peak = ref_power.max(axis=1, keepdims=True)
    ref_db, query_db = _floored_db(ref_power, peak), _floored_db(query_power, peak)
    ltas_r, ltas_q = ref_power.mean(axis=0), query_power.mean(axis=0)
    ltas_peak = ltas_r.max()
    freqs = np.fft.rfftfreq(N_FFT, 1 / rate)
    bands_r, bands_q = _band_energy(ltas_r, freqs, rate), _band_energy(ltas_q, freqs, rate)
    total_r, total_q = float(ltas_r.sum()), float(ltas_q.sum())
    band_difference = {}
    for name in bands_r:
        energy_r = bands_r[name] * total_r if bands_r[name] is not None else 0.0
        energy_q = bands_q[name] * total_q if bands_q[name] is not None else 0.0
        band_difference[name] = _db(energy_q / energy_r) if energy_r > 0 and energy_q > 0 else None
    return {**result,
            "log_spectral_correlation": _num(np.corrcoef(ref_db.ravel(), query_db.ravel())[0, 1]),
            "ltas_db_correlation": _num(np.corrcoef(_floored_db(ltas_r, ltas_peak),
                                                    _floored_db(ltas_q, ltas_peak))[0, 1]),
            "log_spectral_distance_db": _num(np.mean(np.sqrt(np.mean((ref_db - query_db) ** 2, axis=1)))),
            "band_level_difference_db": band_difference}


def high_frequency_profile(signal, rate):
    """Where one signal's energy sits: band fractions, 99% roll-off and content edge."""
    starts = frame_starts(len(signal))
    power, rms = power_frames(signal, starts)
    power = power[rms >= SILENCE_RMS]
    if len(power) < 4:
        return {"active_frames": int(len(power)), "band_energy_fraction": None, "rolloff_99_hz_median": None,
                "content_edge_hz": None, "energy_above_16k_db": None}
    freqs = np.fft.rfftfreq(N_FFT, 1 / rate)
    mean_power = power.mean(axis=0)
    cumulative = np.cumsum(power, axis=1)
    rolloff = freqs[np.argmax(cumulative >= 0.99 * cumulative[:, -1:], axis=1)]
    level = 10 * np.log10(np.percentile(power, 90, axis=0) + 1e-20)
    reference = np.median(level[(freqs >= 1000) & (freqs < 4000)])
    above = np.nonzero(level - reference >= CONTENT_EDGE_DB)[0]
    high = mean_power[freqs >= 16000].sum()
    return {"active_frames": int(len(power)),
            "band_energy_fraction": {key: _num(value) if value is not None else None
                                     for key, value in _band_energy(mean_power, freqs, rate).items()},
            "rolloff_99_hz_median": _num(np.median(rolloff), 1),
            "content_edge_hz": _num(freqs[above[-1]], 1) if len(above) else None,
            "content_edge_threshold_db": CONTENT_EDGE_DB,
            "energy_above_16k_db": _db(high / mean_power.sum()) if rate / 2 > 16000 else None}


def compare(reference, query, rate, max_lag_seconds, reference_name, query_name, include_si_sdr=False):
    """One pair: align query to reference, fit gain, then waveform and spectral measures."""
    alignment = align(reference, query, rate, max_lag_seconds)
    r, q = overlap(reference, query, alignment["lag_samples"])
    q = q * alignment["polarity"]
    gain = gain_to_reference(r, q)
    row = {"reference": reference_name, "query": query_name, "alignment": alignment,
           "overlap_seconds": _num(len(r) / rate),
           "waveform": waveform_measures(r, q, gain),
           "spectral": spectral_measures(r, q, gain, rate)}
    if include_si_sdr:
        row["waveform"]["si_sdr_db_query_against_reference"] = si_sdr(r, q)
    return row


def crosstalk(stem, lag, polarity, others, rate):
    """Correlation of a stem with the separator's other outputs, on the mix timeline."""
    rows = {}
    for name, estimate in others.items():
        estimate_segment, stem_segment = overlap(estimate, stem * polarity, lag)
        rows[name] = waveform_measures(estimate_segment, stem_segment, 0.0)["pearson_correlation"]
    total = sum(others.values())
    total_segment, stem_segment = overlap(total, stem * polarity, lag)
    norm = np.sqrt(np.dot(total_segment, total_segment) * np.dot(stem_segment, stem_segment))
    projected = (np.dot(total_segment, stem_segment) / norm) ** 2 if norm > 0 else 0.0
    return {"pearson_correlation_with_each_other_source": rows,
            "pearson_correlation_with_other_sources_sum": waveform_measures(total_segment, stem_segment,
                                                                            0.0)["pearson_correlation"],
            "stem_energy_explained_by_other_sources_db": _db(projected),
            "spectral_against_other_sources_sum": spectral_measures(
                total_segment, stem_segment, gain_to_reference(total_segment, stem_segment), rate)}


# Separation


def _hash_model_repo(repo):
    return {str(path.relative_to(repo)): sha256(path) for path in sorted(Path(repo).rglob("*"))
            if path.is_file() and path.suffix in {".th", ".yaml", ".yml", ".txt"}}


def demucs_separator(model_repo=None, allow_download=False, device="cpu", seed=0, shifts=0,
                     overlap_fraction=0.25, segment=None, threads=None):
    """A separator running Demucs htdemucs. Refuses before importing torch unless weights are local or allowed."""
    if model_repo is None and not allow_download:
        raise ProbeError("Demucs weights are needed: pass --model-repo DIR with local htdemucs weights, or "
                         "--allow-download to let Demucs fetch them from the network.")
    if model_repo is not None and not Path(model_repo).is_dir():
        raise ProbeError(f"--model-repo {model_repo} is not a directory.")
    loaded = {}  # The model is loaded once and reused by later calls (batch runs).

    def separate(mix, rate):
        try:
            import torch
            from demucs.apply import apply_model
            from demucs.pretrained import get_model
        except ImportError as exc:
            raise ProbeError("Demucs is not installed: pip install -r requirements-separation.txt "
                             f"({exc})") from exc
        from importlib.metadata import PackageNotFoundError, version
        # apply_model draws its time shifts from the random module; shifts=0 avoids them.
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(True, warn_only=True)
        if threads:
            torch.set_num_threads(threads)
        if "model" not in loaded:
            loaded["model"] = get_model(MODEL_NAME, repo=Path(model_repo) if model_repo else None)
            loaded["model"].eval()
        model = loaded["model"]
        model_rate, channels = model.samplerate, model.audio_channels
        if any(name not in model.sources for name in SOURCES):
            raise ProbeError(f"{MODEL_NAME} reports sources {model.sources}, expected {SOURCES}.")
        wav = to_channels(resample(mix, rate, model_rate), channels)
        tensor = torch.from_numpy(np.ascontiguousarray(wav.T, dtype=np.float32))
        # Same normalisation as demucs.separate: whole-track mean and std of the mono reference.
        reference = tensor.mean(0)
        mean, std = reference.mean(), reference.std()
        std = std if float(std) > 0 else torch.tensor(1.0)
        with torch.no_grad():
            out = apply_model(model, ((tensor - mean) / std)[None], shifts=shifts, split=True,
                              overlap=overlap_fraction, segment=segment, device=device, progress=False)[0]
        out = (out * std + mean).cpu().numpy().astype(np.float64)
        sources = {name: fit_length(resample(out[index].T, model_rate, rate), len(mix))
                   for index, name in enumerate(model.sources)}
        try:
            demucs_version = version("demucs")
        except PackageNotFoundError:
            demucs_version = None
        warnings = []
        if model_repo is None:
            warnings.append("Weights were loaded from the network or a download cache and were not hashed.")
        if shifts:
            warnings.append(f"shifts={shifts}: Demucs averages random time shifts, seeded with {seed}.")
        return {"sources": sources, "warnings": warnings, "model": {
            "name": MODEL_NAME, "demucs_version": demucs_version, "torch_version": torch.__version__,
            "model_class": type(model).__name__, "bag_size": len(getattr(model, "models", [model])),
            "sources": list(model.sources), "model_sample_rate_hz": model_rate, "model_channels": channels,
            "weights_source": "local_repo" if model_repo else "remote_allowed",
            "model_repo": str(model_repo) if model_repo else None,
            "weights_sha256": _hash_model_repo(model_repo) if model_repo else None,
            "settings": {"device": device, "seed": seed, "shifts": shifts, "split": True,
                         "overlap": overlap_fraction, "segment_seconds": segment,
                         "torch_threads": torch.get_num_threads(), "deterministic_algorithms": True}}}
    return separate


# Probe


def _input_warnings(mix_info, stem_info, mix, stem):
    warnings = []
    if mix_info["sample_rate_hz"] != stem_info["sample_rate_hz"]:
        warnings.append(f"The submitted stem ({stem_info['sample_rate_hz']} Hz) was resampled to the mix rate "
                        f"({mix_info['sample_rate_hz']} Hz) for comparison.")
    if mix_info["channels"] != stem_info["channels"]:
        warnings.append("The mix and submitted stem have different channel counts; both are compared as mono.")
    if abs(mix_info["duration_seconds"] - stem_info["duration_seconds"]) > 1.0:
        warnings.append("The mix and submitted stem durations differ by more than 1 s; only the aligned "
                        "overlap is compared.")
    if np.max(np.abs(mix)) >= 0.999:
        warnings.append("The mix reaches full scale; clipping or limiting changes separation and comparison.")
    for name, audio in (("mix", mix), ("submitted stem", stem)):
        stereo, downmix = np.sqrt(np.mean(audio ** 2)), np.sqrt(np.mean(mono(audio) ** 2))
        if audio.shape[1] > 1 and stereo > 0 and downmix < 0.25 * stereo:
            warnings.append(f"The {name} largely cancels in its mono downmix; mono measurements understate it.")
    return warnings


def _comparison_warnings(label, row):
    out = []
    if row["alignment"]["lag_at_search_limit"] or row["alignment"]["stronger_peak_outside_limit"]:
        out.append(f"{label}: the best alignment may lie beyond the search limit; raise --max-lag-seconds.")
    if (row["alignment"]["secondary_peak_ratio"] or 0) >= AMBIGUOUS_PEAK_RATIO:
        out.append(f"{label}: a second alignment peak is nearly as strong (repeated material); the lag may be "
                   "ambiguous.")
    if row["spectral"]["frames_compared"] < 4:
        out.append(f"{label}: too few non-silent frames for spectral measures.")
    return out


def run_probe(mix_path, stem_path, source, output_dir, separator, max_lag_seconds=5.0, overwrite=False):
    if source not in SOURCES:
        raise ProbeError(f"--source must be one of {', '.join(SOURCES)}.")
    output_dir = Path(output_dir)
    report_path, stem_out = output_dir / REPORT_NAME, output_dir / f"derived_{source}.wav"
    existing = [str(path) for path in (report_path, stem_out) if path.exists()]
    if existing and not overwrite:
        raise ProbeError("Output exists (pass --overwrite to replace): " + ", ".join(existing))
    started = time.perf_counter()
    mix, rate, mix_info = load_audio(mix_path)
    stem, stem_rate, stem_info = load_audio(stem_path)
    stem = resample(stem, stem_rate, rate)
    loaded = time.perf_counter()
    separation = separator(mix, rate)
    separated = time.perf_counter()
    derived = separation["sources"][source]
    output_dir.mkdir(parents=True, exist_ok=True)
    samples = np.ascontiguousarray(derived, dtype="<f4")
    sf.write(str(stem_out), samples, rate, subtype="FLOAT")

    mix_m, stem_m, derived_m = mono(mix), mono(stem), mono(derived)
    a = compare(mix_m, stem_m, rate, max_lag_seconds, "mix", "submitted_stem")
    b = compare(mix_m, derived_m, rate, max_lag_seconds, "mix", "derived_stem")
    c = compare(stem_m, derived_m, rate, max_lag_seconds, "submitted_stem", "derived_stem", include_si_sdr=True)
    others = {name: mono(audio) for name, audio in separation["sources"].items() if name != source}
    talk = {"submitted_stem": crosstalk(stem_m, a["alignment"]["lag_samples"], a["alignment"]["polarity"],
                                        others, rate),
            "derived_stem": {**crosstalk(derived_m, b["alignment"]["lag_samples"], b["alignment"]["polarity"],
                                         others, rate), "same_separation_run": True}}
    warnings = _input_warnings(mix_info, stem_info, mix, stem) + list(separation.get("warnings", []))
    for label, row in (("A", a), ("B", b), ("C", c)):
        warnings += _comparison_warnings(label, row)
    model_rate = separation["model"].get("model_sample_rate_hz")
    if model_rate and rate > model_rate:
        warnings.append(f"The mix ({rate} Hz) was separated at {model_rate} Hz: the derived stem has no content "
                        f"above {model_rate / 2:.0f} Hz by construction.")
    finished = time.perf_counter()
    report = {
        "report_type": REPORT_TYPE, "schema_version": SCHEMA_VERSION, "status": STATUS, "notice": NOTICE,
        "scoring_eligible": False, "content_match_is_provenance": False,
        "source": source,
        "inputs": {"mix": mix_info, "submitted_stem": {
            **stem_info, "note": "Supplied as --genuine-stem; the probe does not verify how it was made."}},
        "model": separation["model"],
        "analysis": {"signal": "mono downmix at the mix sample rate", "analysis_sample_rate_hz": rate,
                     "n_fft": N_FFT, "hop": HOP, "max_frames": MAX_FRAMES, "silence_rms": SILENCE_RMS,
                     "floor_db": FLOOR_DB, "max_lag_seconds": max_lag_seconds},
        "comparisons": {"A_submitted_stem_vs_mix": a, "B_derived_stem_vs_mix": b,
                        "C_submitted_stem_vs_derived_stem": c},
        "high_frequency": {"mix": high_frequency_profile(mix_m, rate),
                           "submitted_stem": high_frequency_profile(stem_m, rate),
                           "derived_stem": high_frequency_profile(derived_m, rate)},
        "crosstalk": talk,
        # libsndfile stamps the write time into a float WAV's PEAK chunk, so the
        # file hash changes on every run; the sample hash identifies the audio.
        "outputs": {"derived_stem": {"path": str(stem_out), "sha256": sha256(stem_out),
                                     "samples_sha256": hashlib.sha256(samples.tobytes()).hexdigest(),
                                     "sample_rate_hz": rate, "subtype": "FLOAT"}, "report": str(report_path)},
        "runtime_seconds": {"load": _num(loaded - started, 3), "separation": _num(separated - loaded, 3),
                            "diagnostics": _num(finished - separated, 3), "total": _num(finished - started, 3)},
        "warnings": warnings,
        "limitations": LIMITATIONS,
    }
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Exploratory, uncalibrated Demucs separation probe.")
    parser.add_argument("--mix", required=True, type=Path)
    parser.add_argument("--genuine-stem", required=True, type=Path,
                        help="The submitted stem to compare; its origin is not assumed or verified.")
    parser.add_argument("--source", required=True, choices=SOURCES)
    parser.add_argument("--output-dir", required=True, type=Path)
    weights = parser.add_argument_group("weights (one is required)")
    weights.add_argument("--model-repo", type=Path, help="Directory with local htdemucs weights (offline).")
    weights.add_argument("--allow-download", action="store_true",
                         help="Allow Demucs to download weights from the network.")
    parser.add_argument("--device", default="cpu", choices=("cpu", "mps", "cuda"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--shifts", type=int, default=0, help="Demucs random shifts (0 is deterministic).")
    parser.add_argument("--overlap", type=float, default=0.25)
    parser.add_argument("--segment", type=float, default=None)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--max-lag-seconds", type=float, default=5.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        separator = demucs_separator(args.model_repo, args.allow_download, args.device, args.seed, args.shifts,
                                     args.overlap, args.segment, args.threads)
        report = run_probe(args.mix, args.genuine_stem, args.source, args.output_dir, separator,
                           args.max_lag_seconds, args.overwrite)
    except ProbeError as exc:
        print("separation_probe: " + str(exc), file=sys.stderr)
        return 2
    print(f"{STATUS}: report written to {report['outputs']['report']}")
    for warning in report["warnings"]:
        print("warning: " + warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
