"""Phase 2A: production stems against Demucs-derived stems, exploratory and uncalibrated.

    python -B benchmarks/separation_batch.py --dataset ~/datasets/musdb18-7 --songs 10 \
        --exclude "AM Contra - Heart Peripheral" --output-dir ~/experiments/separation_batch/phase2a \
        --model-repo ~/models/demucs

Each song's mix is separated once and reused for all four sources. For every
song/source pair, two candidate "submitted stems" are measured the same way:
the genuine production stem and the Demucs-derived stem. The analysis groups
by song, because stems from one song are not independent, and chooses every
data-driven threshold leave-one-song-out. Nothing here scores or labels a
stem; see docs/SOURCE_SEPARATION_POC.md.
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import separation_probe as probe  # noqa: E402

STATUS = probe.STATUS
SCHEMA_VERSION = "1.0"
KINDS = ("genuine", "derived")
# Declared before the Phase 2A songs were measured, from the one Phase 1 song
# (AM Contra - Heart Peripheral: derived +28 dB, genuine +4 dB), which the batch excludes.
PREREGISTERED = {"metric": "hf_excess_over_mix_db", "direction": "greater_or_equal", "threshold": 10.0,
                 "declared": "2026-10-25, before Phase 2A measurement",
                 "basis": "Phase 1 song only (excluded from this batch)"}
# A pair is excluded when its genuine stem is nearly silent: too little signal to measure.
MIN_GENUINE_RMS = 1e-3
MIN_ACTIVE_FRACTION = 0.5
BOOTSTRAP = 2000
SEED = 0
HF_GUARD_HZ = 500.0
# Metrics that compare a candidate with the separation that produced the
# derived candidate: trivially perfect for it, so excluded from discrimination claims.
CIRCULAR = {"vs_demucs_pearson", "vs_demucs_si_sdr_db", "vs_demucs_log_spectral_distance_db",
            "xt_demucs_sum_pearson", "xt_demucs_explained_db"}
# Uses the genuine other stems, which a real submission review would not have.
ORACLE = {"xt_genuine_sum_pearson", "xt_genuine_explained_db"}
BANDS = ("0-250_hz", "250-2000_hz", "2000-8000_hz", "8000-16000_hz", "16000-nyquist_hz")


def select_songs(dataset, split, count, exclude, skip=0):
    """Usable songs sorted by name, after exclusions; ``skip`` passes over the first ones (held-out runs)."""
    root = Path(dataset) / split
    names = sorted(path.name for path in root.iterdir() if path.is_dir()
                   and all((path / f"{name}.wav").is_file() for name in ("mixture", *probe.SOURCES)))
    chosen = [name for name in names if name not in set(exclude)][skip:skip + count]
    if len(chosen) < count:
        raise probe.ProbeError(f"Only {len(chosen)} usable songs in {root}.")
    return [root / name for name in chosen]


def relative_level(signal, rate, low, high):
    """Median 90th-percentile level (dB) in [low, high) relative to the 1-4 kHz median."""
    starts = probe.frame_starts(len(signal))
    power, rms = probe.power_frames(signal, starts)
    power = power[rms >= probe.SILENCE_RMS]
    if len(power) < 4:
        return None
    freqs = np.fft.rfftfreq(probe.N_FFT, 1 / rate)
    level = 10 * np.log10(np.percentile(power, 90, axis=0) + 1e-20)
    band = (freqs >= low) & (freqs < high)
    return float(np.median(level[band]) - np.median(level[(freqs >= 1000) & (freqs < 4000)])) if band.any() else None


def measure(candidate, mix, rate, reference, demucs_others, genuine_others, mix_edge, max_lag):
    """Flat metrics for one candidate stem (mono arrays at the mix rate)."""
    vs_mix = probe.compare(mix, candidate, rate, max_lag, "mix", "candidate")
    vs_demucs = probe.compare(reference, candidate, rate, max_lag, "demucs", "candidate", include_si_sdr=True)
    lag, polarity = vs_mix["alignment"]["lag_samples"], vs_mix["alignment"]["polarity"]
    xt_demucs = probe.crosstalk(candidate, lag, polarity, demucs_others, rate)
    xt_genuine = probe.crosstalk(candidate, lag, polarity, genuine_others, rate)
    hf = probe.high_frequency_profile(candidate, rate)
    row = {"vs_mix_lag_samples": lag,
           "vs_mix_pearson": vs_mix["waveform"]["pearson_correlation"],
           "vs_mix_residual_db": vs_mix["waveform"]["residual_to_reference_db"],
           "vs_mix_log_spectral_correlation": vs_mix["spectral"]["log_spectral_correlation"],
           "vs_mix_log_spectral_distance_db": vs_mix["spectral"]["log_spectral_distance_db"],
           "vs_demucs_pearson": vs_demucs["waveform"]["pearson_correlation"],
           "vs_demucs_si_sdr_db": vs_demucs["waveform"]["si_sdr_db_query_against_reference"],
           "vs_demucs_log_spectral_distance_db": vs_demucs["spectral"]["log_spectral_distance_db"],
           "xt_demucs_sum_pearson": xt_demucs["pearson_correlation_with_other_sources_sum"],
           "xt_demucs_explained_db": xt_demucs["stem_energy_explained_by_other_sources_db"],
           "xt_genuine_sum_pearson": xt_genuine["pearson_correlation_with_other_sources_sum"],
           "xt_genuine_explained_db": xt_genuine["stem_energy_explained_by_other_sources_db"],
           "content_edge_hz": hf["content_edge_hz"],
           "rolloff_99_hz": hf["rolloff_99_hz_median"],
           "energy_above_16k_db": hf["energy_above_16k_db"]}
    bands = vs_mix["spectral"]["band_level_difference_db"] or {}
    for band in BANDS:
        row["vs_mix_band_" + band.replace("-", "_") + "_db"] = bands.get(band)
    # Level above the mix's own content edge, against the mix's level there: a
    # separator can put energy into a band the mix does not contain.
    if mix_edge is not None and mix_edge + HF_GUARD_HZ < rate / 2 - HF_GUARD_HZ:
        low, high = mix_edge + HF_GUARD_HZ, rate / 2 - HF_GUARD_HZ
        own, base = relative_level(candidate, rate, low, high), relative_level(mix, rate, low, high)
        row["hf_excess_over_mix_db"] = probe._num(own - base) if own is not None and base is not None else None
        row["hf_excess_mix_referenced_db"] = mix_referenced_excess(mix, candidate, lag, polarity, rate, low, high)
    else:
        row["hf_excess_over_mix_db"] = row["hf_excess_mix_referenced_db"] = None
    return row


def mix_referenced_excess(mix, candidate, lag, polarity, rate, low, high):
    """Power of the gain-fitted candidate in [low, high) over the mix's power there (dB).

    Phase 2B variant (designed after Phase 2A): the reference is the mix, not
    the candidate's own 1-4 kHz level, so an instrument with little mid-band
    energy (bass) is not inflated.
    """
    r, q = probe.overlap(mix, candidate * polarity, lag)
    gain = probe.gain_to_reference(r, q)
    starts = probe.frame_starts(len(r))
    mix_power, mix_rms = probe.power_frames(r, starts)
    stem_power, _ = probe.power_frames(q * gain, starts)
    active = mix_rms >= probe.SILENCE_RMS
    if active.sum() < 4:
        return None
    band = (np.fft.rfftfreq(probe.N_FFT, 1 / rate) >= low) & (np.fft.rfftfreq(probe.N_FFT, 1 / rate) < high)
    base, own = float(mix_power[active][:, band].sum()), float(stem_power[active][:, band].sum())
    return probe._db(own / base) if base > 0 and own > 0 else None


def saved_separator(batch_dir):
    """Reuses the Demucs outputs an earlier batch saved, instead of separating again."""
    batch_dir = Path(batch_dir)
    model = json.loads((batch_dir / "measurements.json").read_text())["model"]

    def separate(mix, rate, song):
        sources = {}
        for source in probe.SOURCES:
            audio, stem_rate = sf.read(str(batch_dir / "stems" / song.name / f"demucs_{source}.wav"),
                                       dtype="float64", always_2d=True)
            if stem_rate != rate:
                raise probe.ProbeError(f"Saved stem rate {stem_rate} differs from the mix rate {rate}.")
            sources[source] = probe.fit_length(audio, len(mix))
        return {"sources": sources, "model": {**model, "reused_from": str(batch_dir)}, "warnings": [],
                "reused": True}
    separate.takes_song = True
    return separate


def run_batch(songs, separator, output_dir, max_lag=5.0, overwrite=False, rules=None):
    output_dir = Path(output_dir)
    targets = [output_dir / name for name in ("measurements.csv", "measurements.json", "analysis.json")]
    if any(path.exists() for path in targets) and not overwrite:
        raise probe.ProbeError("Output exists (pass --overwrite to replace): " + str(output_dir))
    rows, songs_meta, model, started = [], [], None, time.perf_counter()
    for song in songs:
        song_started = time.perf_counter()
        mix, rate, mix_info = probe.load_audio(song / "mixture.wav")
        genuine = {}
        for source in probe.SOURCES:
            audio, stem_rate, _ = probe.load_audio(song / f"{source}.wav")
            genuine[source] = probe.fit_length(probe.resample(audio, stem_rate, rate), len(mix))
        separation = separator(mix, rate, song) if getattr(separator, "takes_song", False) else separator(mix, rate)
        separated_at = time.perf_counter()
        model = separation["model"]
        if not separation.get("reused"):
            stem_dir = output_dir / "stems" / song.name
            stem_dir.mkdir(parents=True, exist_ok=True)
            for source, audio in separation["sources"].items():
                sf.write(str(stem_dir / f"demucs_{source}.wav"), np.ascontiguousarray(audio, dtype="<f4"), rate,
                         subtype="FLOAT")
        mix_m = probe.mono(mix)
        mix_edge = probe.high_frequency_profile(mix_m, rate)["content_edge_hz"]
        demucs_m = {name: probe.mono(audio) for name, audio in separation["sources"].items()}
        genuine_m = {name: probe.mono(audio) for name, audio in genuine.items()}
        for source in probe.SOURCES:
            truth = genuine_m[source]
            frames = probe.frame_starts(len(truth))
            _, frame_rms = probe.power_frames(truth, frames)
            active = float(np.mean(frame_rms >= probe.SILENCE_RMS)) if len(frames) else 0.0
            rms = float(np.sqrt(np.mean(truth ** 2)))
            excluded = rms < MIN_GENUINE_RMS or active < MIN_ACTIVE_FRACTION
            demucs_others = {k: v for k, v in demucs_m.items() if k != source}
            genuine_others = {k: v for k, v in genuine_m.items() if k != source}
            for kind, candidate in (("genuine", truth), ("derived", demucs_m[source])):
                row = {"song": song.name, "source": source, "kind": kind, "excluded": excluded,
                       "genuine_rms": probe._num(rms), "genuine_active_fraction": probe._num(active)}
                row.update(measure(candidate, mix_m, rate, demucs_m[source], demucs_others, genuine_others,
                                   mix_edge, max_lag))
                rows.append(row)
        songs_meta.append({"song": song.name, "mix_sha256": mix_info["sha256"], "mix_content_edge_hz": mix_edge,
                           "separation_seconds": probe._num(separated_at - song_started, 3),
                           "total_seconds": probe._num(time.perf_counter() - song_started, 3)})
        print(f"{song.name}: separated in {separated_at - song_started:.1f} s", flush=True)
    analysis = analyse(rows)
    if rules is not None:
        import separation_rules
        analysis["frozen_rules"] = separation_rules.apply_rules(rules, rows)
    meta = {"report_type": "separation_batch", "schema_version": SCHEMA_VERSION, "status": STATUS,
            "notice": probe.NOTICE, "scoring_eligible": False, "content_match_is_provenance": False,
            "model": model, "songs": songs_meta, "exclusion_rule": {"min_genuine_rms": MIN_GENUINE_RMS,
                                                                    "min_active_fraction": MIN_ACTIVE_FRACTION},
            "circular_metrics": sorted(CIRCULAR), "oracle_metrics": sorted(ORACLE),
            "runtime_seconds": probe._num(time.perf_counter() - started, 3), "limitations": LIMITATIONS}
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(targets[0], "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    targets[1].write_text(json.dumps({**meta, "rows": rows}, indent=2, allow_nan=False) + "\n")
    targets[2].write_text(json.dumps({**meta, "analysis": analysis}, indent=2, allow_nan=False) + "\n")
    return meta, rows, analysis


# Analysis


def metric_names(rows):
    skip = {"song", "source", "kind", "excluded", "genuine_rms", "genuine_active_fraction", "vs_mix_lag_samples"}
    return [key for key in rows[0] if key not in skip]


def auc(genuine, derived):
    """P(derived > genuine) + 0.5 P(tie): 0.5 is no separation, 0 or 1 is complete."""
    if not genuine or not derived:
        return None
    g, d = np.asarray(genuine, float), np.asarray(derived, float)
    return float(((d[:, None] > g[None, :]).sum() + 0.5 * (d[:, None] == g[None, :]).sum()) / (len(g) * len(d)))


def _values(rows, metric, kind, songs=None, source=None):
    return [row[metric] for row in rows if row["kind"] == kind and row[metric] is not None
            and (songs is None or row["song"] in songs) and (source is None or row["source"] == source)]


def song_bootstrap(rows, statistic, iterations=BOOTSTRAP, seed=SEED):
    """95% percentile interval of a statistic over songs resampled with replacement."""
    songs = sorted({row["song"] for row in rows})
    by_song = {song: [row for row in rows if row["song"] == song] for song in songs}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(iterations):
        sample = [row for song in rng.choice(songs, len(songs)) for row in by_song[song]]
        value = statistic(sample)
        if value is not None:
            values.append(value)
    return {"lower": probe._num(np.percentile(values, 2.5), 3), "upper": probe._num(np.percentile(values, 97.5), 3),
            "iterations": len(values)} if values else None


def _summary(values):
    if not values:
        return None
    v = np.asarray(values, float)
    return {"n": len(v), "median": probe._num(np.median(v), 3), "q25": probe._num(np.percentile(v, 25), 3),
            "q75": probe._num(np.percentile(v, 75), 3), "min": probe._num(v.min(), 3), "max": probe._num(v.max(), 3)}


def choose_threshold(genuine, derived, max_false_positive=None):
    """Direction and threshold from training values only: best Youden J, or the best
    detection whose training false-positive rate stays within ``max_false_positive``."""
    if not genuine or not derived:
        return None
    higher = np.median(derived) >= np.median(genuine)
    sign = 1.0 if higher else -1.0
    g, d = sign * np.asarray(genuine, float), sign * np.asarray(derived, float)
    values = np.unique(np.concatenate((g, d)))
    candidates = np.concatenate(((values[:-1] + values[1:]) / 2, [values[-1] + 1e-9]))
    best = None
    for threshold in candidates:
        fpr, tpr = float(np.mean(g >= threshold)), float(np.mean(d >= threshold))
        if max_false_positive is not None and fpr > max_false_positive:
            continue
        key = (tpr - fpr, -fpr) if max_false_positive is None else (tpr, -fpr)
        if best is None or key > best[0]:
            best = (key, threshold)
    if best is None:
        return None
    return {"direction": "greater_or_equal" if higher else "less_or_equal", "threshold": float(sign * best[1])}


def flags(value, rule):
    return value >= rule["threshold"] if rule["direction"] == "greater_or_equal" else value <= rule["threshold"]


def _rates(decisions):
    """decisions: (song, kind, flagged). Stem-level rates with Wilson and song-bootstrap intervals."""
    rows = [{"song": song, "kind": k, "flagged": flagged} for song, k, flagged in decisions]
    out = {}
    for kind, name in (("genuine", "false_positive"), ("derived", "detection")):
        values = [flagged for _, k, flagged in decisions if k == kind]
        count = int(sum(values))
        out[name] = {"count": count, "cases": len(values),
                     "rate": probe._num(count / len(values), 3) if values else None,
                     "wilson_95_stem_level": _wilson(count, len(values)),
                     "song_bootstrap_95": song_bootstrap(rows, lambda sample, kind=kind: (
                         float(np.mean([r["flagged"] for r in sample if r["kind"] == kind]))
                         if any(r["kind"] == kind for r in sample) else None))}
    return out


def _wilson(count, cases):
    if not cases:
        return None
    z = 1.959963984540054
    p = count / cases
    centre = (p + z * z / (2 * cases)) / (1 + z * z / cases)
    half = z * np.sqrt(p * (1 - p) / cases + z * z / (4 * cases * cases)) / (1 + z * z / cases)
    return {"lower": probe._num(max(0.0, centre - half), 3), "upper": probe._num(min(1.0, centre + half), 3)}


def leave_one_song_out(rows, metric, max_false_positive=None):
    songs = sorted({row["song"] for row in rows})
    decisions, rules = [], []
    for held in songs:
        train = [row for row in rows if row["song"] != held]
        rule = choose_threshold(_values(train, metric, "genuine"), _values(train, metric, "derived"),
                                max_false_positive)
        if rule is None:
            continue
        rules.append(rule)
        decisions += [(held, row["kind"], bool(flags(row[metric], rule))) for row in rows
                      if row["song"] == held and row[metric] is not None]
    if not decisions:
        return None
    thresholds = [rule["threshold"] for rule in rules]
    return {"rates_held_out": _rates(decisions),
            "directions": sorted({rule["direction"] for rule in rules}),
            "threshold_range": [probe._num(min(thresholds), 3), probe._num(max(thresholds), 3)]}


def analyse(rows):
    usable = [row for row in rows if not row["excluded"]]
    pairs = {(row["song"], row["source"]) for row in usable}
    out = {"status": STATUS, "pairs_analysed": len(pairs), "songs": len({row["song"] for row in usable}),
           "pairs_excluded": sorted({f"{row['song']} / {row['source']}" for row in rows if row["excluded"]}),
           "preregistered": None, "metrics": {}}
    for metric in metric_names(rows):
        genuine, derived = _values(usable, metric, "genuine"), _values(usable, metric, "derived")
        paired = []
        for song, source in sorted(pairs):
            values = {row["kind"]: row[metric] for row in usable if row["song"] == song and row["source"] == source}
            if values.get("genuine") is not None and values.get("derived") is not None:
                paired.append(values["derived"] - values["genuine"])
        entry = {"role": "circular_same_model" if metric in CIRCULAR else "oracle_needs_genuine_others"
                 if metric in ORACLE else "deployable",
                 "genuine": _summary(genuine), "derived": _summary(derived),
                 "auc_derived_higher": auc(genuine, derived),
                 "auc_song_bootstrap_95": song_bootstrap(usable, lambda sample, m=metric: auc(
                     _values(sample, m, "genuine"), _values(sample, m, "derived"))),
                 "paired_derived_minus_genuine": _summary(paired),
                 "paired_fraction_derived_higher": probe._num(np.mean(np.asarray(paired) > 0), 3) if paired else None,
                 "auc_by_source": {source: auc(_values(usable, metric, "genuine", source=source),
                                               _values(usable, metric, "derived", source=source))
                                   for source in probe.SOURCES}}
        if metric not in CIRCULAR:
            entry["loso_youden"] = leave_one_song_out(usable, metric)
            entry["loso_zero_training_false_positives"] = leave_one_song_out(usable, metric, max_false_positive=0.0)
        out["metrics"][metric] = entry
    metric = PREREGISTERED["metric"]
    measured = [row for row in usable if row[metric] is not None]
    def decide(selected):
        return [(row["song"], row["kind"], bool(flags(row[metric], PREREGISTERED))) for row in selected]
    out["preregistered"] = {**PREREGISTERED, "rates": _rates(decide(measured)) if measured else None,
                            "rates_by_source": {source: _rates(decide([r for r in measured if r["source"] == source]))
                                                for source in probe.SOURCES
                                                if any(r["source"] == source for r in measured)}}
    return out


LIMITATIONS = [
    "Exploratory and uncalibrated: a small sample of 7-second excerpts from one dataset.",
    "The derived candidate is the same Demucs run used as the reference, so metrics against that separation "
    "(marked circular) are trivially perfect for it and say nothing about other separators.",
    "MUSDB18-7 excerpts were decoded from AAC: mix and stems stop near 16-17 kHz, which is what makes the "
    "high-frequency excess measurable. A genuine full-band stem with a lossy mix would also exceed the mix there.",
    "MUSDB mixtures are near-linear sums with no mastering chain; real productions differ.",
    "Oracle crosstalk uses the genuine other stems, which a review of one submitted stem would not have.",
    "Stem-level Wilson intervals assume independence that stems from one song lack; the song-bootstrap "
    "intervals are the primary uncertainty, and with few songs they are wide.",
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exploratory, uncalibrated Phase 2A separation batch.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--split", default="test")
    parser.add_argument("--songs", type=int, default=10)
    parser.add_argument("--skip", type=int, default=0, help="Pass over the first songs after exclusions.")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model-repo", type=Path)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--reuse-stems", type=Path, help="An earlier batch directory whose Demucs outputs to reuse.")
    parser.add_argument("--rules", type=Path, help="Frozen rules (separation_rules.py freeze) to evaluate.")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--max-lag-seconds", type=float, default=5.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        separator = (saved_separator(args.reuse_stems) if args.reuse_stems else
                     probe.demucs_separator(args.model_repo, args.allow_download, threads=args.threads))
        rules = json.loads(args.rules.read_text()) if args.rules else None
        songs = select_songs(args.dataset, args.split, args.songs, args.exclude, args.skip)
        _, _, analysis = run_batch(songs, separator, args.output_dir, args.max_lag_seconds, args.overwrite, rules)
    except probe.ProbeError as exc:
        print("separation_batch: " + str(exc), file=sys.stderr)
        return 2
    print(f"{STATUS}: {analysis['pairs_analysed']} pairs from {analysis['songs']} songs; results in {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
