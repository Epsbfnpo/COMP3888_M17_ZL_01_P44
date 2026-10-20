"""Evaluate music_semantics against annotated music, with mir_eval.

Modes (datasets are not included; `fetch` downloads the open ones):

    fetch      SALAMI annotations (CC0) + Internet Archive audio, GuitarSet (CC BY 4.0)
    salami     Section boundaries and labels: music_semantics fixed phrases vs the
               librosa Laplacian segmentation vs MSAF algorithms (optional)
    guitarset  Beat tracking, triad chords and key, through music_semantics' own code
    versions   music_semantics across versions of real songs: transposed and
               time-stretched, a section removed, and an unrelated control

    python -B benchmarks/structure_benchmark.py fetch --data /data/bench --salami-limit 100
    python -B benchmarks/structure_benchmark.py salami --data /data/bench --out /tmp/out \
        [--msaf-python /path/to/msaf-venv/bin/python]
    python -B benchmarks/structure_benchmark.py guitarset --data /data/bench --out /tmp/out
    python -B benchmarks/structure_benchmark.py versions --data /data/bench --out /tmp/out --limit 40

Results are written as rows.json and summary.json plus summary.md. The summary
states whether any method is *measurably* better than fixed phrases: the 95%
bootstrap interval of its paired F-measure difference must lie above zero.
"""

import argparse
import csv
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
import zipfile

import numpy as np

PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAM))
sys.path.insert(0, str(PROGRAM / "vendor"))

from music_target.audio_diagnostics.policy import DEFAULTS  # noqa: E402
from music_target.audio_diagnostics.providers import music_semantics  # noqa: E402

RATE = DEFAULTS["music_semantics"]["sample_rate_hz"]
HOP = DEFAULTS["music_semantics"]["hop_length"]
SALAMI_ZIP = "https://github.com/DDMAL/salami-data-public/archive/refs/heads/master.zip"
GUITARSET = {"annotation.zip": "https://zenodo.org/records/3371780/files/annotation.zip",
             "audio_mono-mic.zip": "https://zenodo.org/records/3371780/files/audio_mono-mic.zip"}
MSAF_ALGORITHMS = [("sf", None), ("foote", None), ("olda", None), ("scluster", "scluster"), ("cnmf", "cnmf")]


# --- downloads -----------------------------------------------------------------

def _download(url, destination):
    destination = Path(destination)
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url.replace("http://", "https://"),
                                     headers={"User-Agent": "COMP3888-benchmark/1.0"})
    with urllib.request.urlopen(request, timeout=300) as response, open(destination.with_suffix(".part"), "wb") as out:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            out.write(block)
    destination.with_suffix(".part").rename(destination)
    return destination


def fetch(data, salami_limit, seed=0):
    data = Path(data)
    archive = _download(SALAMI_ZIP, data / "salami-data-public.zip")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(data)
    root = next(data.glob("salami-data-public-*"))
    index = list(csv.DictReader(open(root / "metadata/id_index_internetarchive.csv", encoding="utf-8")))
    annotated = [row for row in index
                 if (root / "annotations" / row["SONG_ID"] / "parsed").is_dir()]
    random.Random(seed).shuffle(annotated)
    fetched = []
    for row in annotated:
        if len(fetched) >= salami_limit:
            break
        try:
            _download(row["URL"], data / "salami-audio" / f"{row['SONG_ID']}.mp3")
            fetched.append(row["SONG_ID"])
        except Exception as exc:  # a missing archive item is skipped, not fatal
            print(f"skip SALAMI {row['SONG_ID']}: {exc}", flush=True)
    for name, url in GUITARSET.items():
        with zipfile.ZipFile(_download(url, data / "guitarset" / name)) as bundle:
            bundle.extractall(data / "guitarset")
    print(json.dumps({"salami_tracks": len(fetched), "guitarset": sorted(GUITARSET)}))


# --- shared ----------------------------------------------------------------------

def _audio_files(folder):
    files = [path for path in Path(folder).glob("*") if path.suffix.lower() in (".mp3", ".wav")]
    return sorted(files, key=lambda path: int(path.stem) if path.stem.isdigit() else path.stem)


def load(path, offset=0.0, duration=None):
    import librosa
    samples, _ = librosa.load(str(path), sr=RATE, mono=True, offset=offset, duration=duration)
    return samples.astype(np.float32)


def describe(samples):
    import librosa
    return music_semantics._describe({"samples": samples, "range": [0.0, len(samples) / RATE]},
                                     DEFAULTS["music_semantics"], librosa)


def bootstrap_difference(first, second, seed=0, rounds=2000):
    """Mean paired difference (first - second) and its 95% bootstrap interval."""
    difference = np.asarray(first, float) - np.asarray(second, float)
    if len(difference) == 0:
        return None
    rng = np.random.default_rng(seed)
    means = [float(np.mean(rng.choice(difference, size=len(difference)))) for _ in range(rounds)]
    return {"mean": float(np.mean(difference)), "ci95": [float(np.percentile(means, 2.5)),
                                                         float(np.percentile(means, 97.5))]}


# --- SALAMI structure ---------------------------------------------------------------

def salami_reference(root, song_id):
    """Large-scale (uppercase) sections from the first available annotator."""
    parsed = Path(root) / "annotations" / str(song_id) / "parsed"
    for annotator in (1, 2):
        path = parsed / f"textfile{annotator}_uppercase.txt"
        if path.exists():
            events = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    events.append((float(parts[0]), parts[1].strip()))
            intervals, labels = [], []
            for (start, label), (end, _) in zip(events, events[1:]):
                if end > start and label.lower() not in ("silence", "end"):
                    intervals.append([start, end])
                    labels.append(label)
            if intervals:
                return np.array(intervals), labels
    return None, None


def fixed_phrases(samples):
    description = describe(samples)
    times = description["times"]
    intervals = np.array([[times[first], times[last]] for first, last in description["bounds"]])
    return intervals, list(description["labels"])


def laplacian_segments(samples, max_types=10):
    """librosa's documented Laplacian segmentation (McFee & Ellis 2014), k by eigengap."""
    import librosa
    import scipy
    from scipy.cluster.vq import kmeans2
    with np.errstate(all="ignore"):
        cqt = librosa.amplitude_to_db(np.abs(librosa.cqt(samples, sr=RATE, hop_length=HOP,
                                                         bins_per_octave=36, n_bins=252)), ref=np.max)
        _, beats = librosa.beat.beat_track(y=samples, sr=RATE, hop_length=HOP)
        frames = librosa.util.fix_frames(beats, x_min=0, x_max=cqt.shape[1])
        cqt_sync = librosa.util.sync(cqt, frames, aggregate=np.median)
        mfcc = librosa.util.sync(librosa.feature.mfcc(y=samples, sr=RATE, hop_length=HOP), frames)
        recurrence = librosa.segment.recurrence_matrix(cqt_sync, width=3, mode="affinity", sym=True)
        recurrence = librosa.segment.timelag_filter(scipy.ndimage.median_filter)(recurrence, size=(1, 7))
        path_distance = np.sum(np.diff(mfcc, axis=1) ** 2, axis=0)
        path_similarity = np.exp(-path_distance / (np.median(path_distance) or 1.0))
        path = np.diag(path_similarity, k=1) + np.diag(path_similarity, k=-1)
        degree_path, degree_rec = path.sum(axis=1), recurrence.sum(axis=1)
        mu = degree_path.dot(degree_path + degree_rec) / np.sum((degree_path + degree_rec) ** 2)
        laplacian = scipy.sparse.csgraph.laplacian(mu * recurrence + (1 - mu) * path, normed=True)
        values, vectors = scipy.linalg.eigh(laplacian)
        vectors = scipy.ndimage.median_filter(vectors, size=(9, 1))
        types = int(np.argmax(np.diff(values[:max_types + 1])[1:max_types]) + 2)
        norm = np.cumsum(vectors ** 2, axis=1) ** 0.5
        features = vectors[:, :types] / np.maximum(norm[:, types - 1:types], 1e-12)
        _, ids = kmeans2(features, types, seed=0, minit="++")
    times = librosa.frames_to_time(frames, sr=RATE, hop_length=HOP)
    starts = [0] + [index for index in range(1, len(ids)) if ids[index] != ids[index - 1]]
    bounds = list(zip(starts, starts[1:] + [len(ids)]))
    intervals = np.array([[times[first], times[min(last, len(times) - 1)]] for first, last in bounds])
    return intervals, [f"L{ids[first]}" for first, _ in bounds]


MSAF_SNIPPET = """
import json, sys, msaf
boundaries_id, labels_id = sys.argv[2], (None if sys.argv[3] == "none" else sys.argv[3])
bounds, labels = msaf.process(sys.argv[1], boundaries_id=boundaries_id, labels_id=labels_id)
print(json.dumps({"bounds": [float(x) for x in bounds], "labels": [str(x) for x in labels]}))
"""


def msaf_segments(audio_path, python, boundaries_id, labels_id, timeout=900):
    result = subprocess.run([python, "-c", MSAF_SNIPPET, str(audio_path), boundaries_id, labels_id or "none"],
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "msaf failed")
    value = json.loads(result.stdout.strip().splitlines()[-1])
    bounds = value["bounds"]
    intervals = np.array([[a, b] for a, b in zip(bounds, bounds[1:]) if b > a])
    labels = value["labels"][:len(intervals)] if labels_id else [f"S{index}" for index in range(len(intervals))]
    return intervals, labels


def segment_scores(reference, estimate):
    import mir_eval
    (ref_intervals, ref_labels), (est_intervals, est_labels) = reference, estimate
    end = float(ref_intervals[-1, 1])
    start = float(ref_intervals[0, 0])
    est_intervals, est_labels = mir_eval.util.adjust_intervals(np.asarray(est_intervals, float), list(est_labels),
                                                               t_min=start, t_max=end)
    ref_intervals = np.asarray(ref_intervals, float) - start
    est_intervals = np.asarray(est_intervals, float) - start
    scores = {"segments_estimated": int(len(est_intervals)), "segments_reference": int(len(ref_intervals))}
    for window in (0.5, 3.0):
        _, _, f = mir_eval.segment.detection(ref_intervals, est_intervals, window=window, trim=True)
        scores[f"boundary_f_{window:g}s"] = float(f)
    _, _, pairwise = mir_eval.segment.pairwise(ref_intervals, ref_labels, est_intervals, est_labels)
    scores["pairwise_label_f"] = float(pairwise)
    return scores


def run_salami(data, out, limit=None, msaf_python=None):
    data, out = Path(data), Path(out)
    root = next(data.glob("salami-data-public-*"))
    audio = _audio_files(data / "salami-audio")[:limit]
    methods = {"fixed_phrases": None, "acoustic_novelty": None, "librosa_laplacian": None}
    if msaf_python:
        methods.update({f"msaf_{b}" + (f"+{l}" if l else ""): (b, l) for b, l in MSAF_ALGORITHMS})
    rows = []
    for path in audio:
        reference = salami_reference(root, path.stem)
        if reference[0] is None:
            continue
        samples = load(path)
        for method, spec in methods.items():
            started = time.time()
            try:
                if method == "fixed_phrases":
                    estimate = fixed_phrases(samples)
                elif method == "acoustic_novelty":
                    intervals = describe(samples)["sections"]
                    estimate = (intervals, [f"S{i}" for i in range(len(intervals))])
                elif method == "librosa_laplacian":
                    estimate = laplacian_segments(samples)
                else:
                    estimate = msaf_segments(path, msaf_python, *spec)
                row = {"track": path.stem, "method": method, "status": "ok", **segment_scores(reference, estimate)}
            except Exception as exc:
                row = {"track": path.stem, "method": method, "status": "failed",
                       "error": f"{type(exc).__name__}: {exc}"[:300]}
            row["seconds"] = round(time.time() - started, 2)
            rows.append(row)
        print(f"SALAMI {path.stem}: done", flush=True)
    summary = summarise_structure(rows)
    write_results(out / "salami", rows, summary, structure_markdown(summary))
    return summary


def summarise_structure(rows, baseline="fixed_phrases"):
    metrics = ("boundary_f_0.5s", "boundary_f_3s", "pairwise_label_f")
    methods = sorted({row["method"] for row in rows})
    ok = {method: {row["track"]: row for row in rows if row["method"] == method and row["status"] == "ok"}
          for method in methods}
    summary = {"tracks": len({row["track"] for row in rows}), "methods": {}}
    for method in methods:
        entry = {"evaluated": len(ok[method]),
                 "failed": sum(1 for row in rows if row["method"] == method and row["status"] != "ok"),
                 "mean_seconds": float(np.mean([row["seconds"] for row in rows if row["method"] == method]))}
        for metric in metrics + ("segments_estimated",):
            values = [row[metric] for row in ok[method].values()]
            entry[metric] = float(np.mean(values)) if values else None
        if method != baseline:
            shared = sorted(set(ok[method]) & set(ok.get(baseline, {})))
            for metric in metrics:
                entry[f"vs_{baseline}_{metric}"] = bootstrap_difference(
                    [ok[method][track][metric] for track in shared], [ok[baseline][track][metric] for track in shared])
            boundary = entry.get(f"vs_{baseline}_boundary_f_3s")
            entry["measurably_better_than_baseline"] = bool(boundary and boundary["ci95"][0] > 0)
        summary["methods"][method] = entry
    reference = [row["segments_reference"] for row in rows if row.get("segments_reference")]
    summary["mean_reference_segments"] = float(np.mean(reference)) if reference else None
    better = [method for method, entry in summary["methods"].items() if entry.get("measurably_better_than_baseline")]
    summary["recommendation"] = (
        f"Replace fixed phrases with {max(better, key=lambda m: summary['methods'][m]['boundary_f_3s'])}: "
        "its boundary F@3s is measurably higher." if better else
        "Keep fixed phrases: no method is measurably better on boundary F@3s.")
    return summary


def structure_markdown(summary):
    lines = ["| Method | Tracks | Failed | Boundary F@0.5s | Boundary F@3s | Pairwise label F | Segments (ref "
             f"{summary['mean_reference_segments'] or 0:.1f}) | Δ F@3s vs fixed (95% CI) |", "|---" * 8 + "|"]
    for method, entry in summary["methods"].items():
        delta = entry.get("vs_fixed_phrases_boundary_f_3s")
        cell = (f"{delta['mean']:+.3f} [{delta['ci95'][0]:+.3f}, {delta['ci95'][1]:+.3f}]" if delta else "baseline")
        fmt = lambda value: "–" if value is None else f"{value:.3f}"
        lines.append(f"| {method} | {entry['evaluated']} | {entry['failed']} | {fmt(entry['boundary_f_0.5s'])} | "
                     f"{fmt(entry['boundary_f_3s'])} | {fmt(entry['pairwise_label_f'])} | "
                     f"{fmt(entry['segments_estimated'])} | {cell} |")
    return "\n".join(lines + ["", summary["recommendation"]])


# --- GuitarSet beats, chords and key --------------------------------------------------

def _jams(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def guitarset_reference(path):
    """Beat times, performed chords (falls back to the first chord layer) and key from a JAMS file."""
    document = _jams(path)
    annotations = document.get("annotations", [])
    def layers(namespace):
        return [item for item in annotations if item.get("namespace") == namespace]
    beats = layers("beat_position") or layers("beat")
    chords = layers("chord")
    performed = [item for item in chords if "perform" in json.dumps(item.get("annotation_metadata", {})).lower()]
    chord_layer = (performed or chords[-1:] or [None])[0]
    keys = layers("key_mode")
    reference = {"beats": np.array(sorted(float(obs["time"]) for obs in beats[0]["data"])) if beats else None}
    if chord_layer:
        data = sorted((obs for obs in chord_layer["data"] if float(obs["duration"]) > 0), key=lambda obs: float(obs["time"]))
        starts = [float(obs["time"]) for obs in data]
        # time + duration can overshoot the next start by float rounding; clip it.
        ends = [min(start + float(obs["duration"]), following)
                for start, obs, following in zip(starts, data, starts[1:] + [float("inf")])]
        reference["chord_intervals"] = np.array([[start, end] for start, end in zip(starts, ends)])
        reference["chord_labels"] = [str(obs["value"]) for obs in data]
        reference["chord_layer"] = "performed" if performed else "first"
    if keys and keys[0]["data"]:
        reference["key"] = str(keys[0]["data"][0]["value"]).replace(":", " ")
    return reference


def chord_label(chord):
    return "N" if chord is None else f"{music_semantics.NOTES[chord[0]]}:{'min' if chord[1] == 'm' else 'maj'}"


def run_guitarset(data, out, limit=None):
    import mir_eval
    data, out = Path(data), Path(out)
    annotations = sorted((data / "guitarset").rglob("*.jams"))[:limit]
    audio = {path.name.replace("_mic.wav", ""): path for path in (data / "guitarset").rglob("*_mic.wav")}
    rows = []
    for jams_path in annotations:
        stem = jams_path.stem
        if stem not in audio:
            continue
        row = {"track": stem}
        try:
            reference = guitarset_reference(jams_path)
            description = describe(load(audio[stem]))
            if reference.get("beats") is not None and len(reference["beats"]):
                row["beat_f"] = float(mir_eval.beat.f_measure(mir_eval.beat.trim_beats(reference["beats"]),
                                                              mir_eval.beat.trim_beats(description["beat_times"])))
            if "chord_intervals" in reference:
                times = description["times"]
                est_intervals = np.array([[times[i], times[i + 1]] for i in range(len(times) - 1)])
                est_labels = [chord_label(chord) for chord in description["chords"]][:len(est_intervals)]
                scores = mir_eval.chord.evaluate(reference["chord_intervals"], reference["chord_labels"],
                                                 est_intervals, est_labels)
                row.update(chord_root=float(scores["root"]), chord_majmin=float(scores["majmin"]),
                           chord_layer=reference["chord_layer"])
            if "key" in reference:
                estimate = f"{description['key']['tonic']} {description['key']['mode']}"
                row["key_reference"], row["key_estimate"] = reference["key"], estimate
                row["key_score"] = float(mir_eval.key.weighted_score(reference["key"], estimate))
            parts = stem.split("_")[1].split("-") if "_" in stem else []
            if len(parts) >= 2 and parts[1].isdigit():
                row["tempo_reference"], row["tempo_estimate"] = int(parts[1]), description["tempo_bpm"]
                ratio = description["tempo_bpm"] / int(parts[1])
                row["tempo_within_4pct"] = abs(ratio - 1) <= 0.04
                row["tempo_within_4pct_octave"] = any(abs(ratio * factor - 1) <= 0.04 for factor in (0.5, 1, 2, 1 / 3, 3))
            row["status"] = "ok"
        except Exception as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
        rows.append(row)
    ok = [row for row in rows if row["status"] == "ok"]
    mean = lambda key: float(np.mean([row[key] for row in ok if key in row])) if any(key in row for row in ok) else None
    summary = {"tracks": len(rows), "failed": len(rows) - len(ok), "beat_f": mean("beat_f"),
               "chord_root": mean("chord_root"), "chord_majmin": mean("chord_majmin"), "key_score": mean("key_score"),
               "tempo_within_4pct": mean("tempo_within_4pct"), "tempo_within_4pct_octave": mean("tempo_within_4pct_octave")}
    markdown = "\n".join(["| Measure | Mean |", "|---|---|"] +
                         [f"| {key} | {'–' if value is None else f'{value:.3f}'} |" for key, value in summary.items()
                          if key not in ("tracks", "failed")] + ["", f"{summary['tracks']} tracks, {summary['failed']} failed."])
    write_results(out / "guitarset", rows, summary, markdown)
    return summary


# --- versions of real songs ------------------------------------------------------------

def _write_wav(path, samples):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        stream.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return str(path)


def _semantics(source, target, folder, name):
    from music_target.audio_diagnostics.worker import execute
    task = {"task_id": f"{name}:0", "relation_id": name, "relationship_type": "derived_from", "selection_id": None,
            "unavailable_reason": None, "source_hash": "1" * 64, "target_hash": "2" * 64,
            "attributes": {key: None for key in ("source_start_seconds", "source_end_seconds",
                                                 "target_start_seconds", "target_end_seconds")},
            "source_path": _write_wav(Path(folder) / f"{name}-source.wav", source),
            "target_path": _write_wav(Path(folder) / f"{name}-target.wav", target)}
    value = execute(task, "music_semantics", DEFAULTS["music_semantics"])
    return value["status"], value["reason_code"], value.get("metrics") or {}


def run_versions(data, out, limit=40, offset=30.0, duration=90.0, semitones=2, stretch=1.08, cut=(30.0, 45.0)):
    """Original excerpt vs a transposed/stretched version, a cut version, and another song."""
    import librosa
    data, out = Path(data), Path(out)
    tracks = _audio_files(data / "salami-audio")[:limit]
    excerpts = {}
    for path in tracks:
        samples = load(path, offset=offset, duration=duration)
        if len(samples) >= RATE * duration * 0.9:
            excerpts[path.stem] = samples
    names = sorted(excerpts, key=int)
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        for index, name in enumerate(names):
            original = excerpts[name]
            shifted = librosa.effects.time_stretch(librosa.effects.pitch_shift(original, sr=RATE, n_steps=semitones),
                                                   rate=stretch)
            a, b = int(cut[0] * RATE), int(cut[1] * RATE)
            variants = {"transposed_stretched": shifted, "section_removed": np.concatenate([original[:a], original[b:]]),
                        "unrelated_song": excerpts[names[(index + 1) % len(names)]]}
            for variant, target in variants.items():
                status, reason, metrics = _semantics(original, target, folder, f"{name}-{variant}")
                rows.append({"track": name, "variant": variant, "status": status, "reason_code": reason,
                             "transposition": metrics.get("transposition_semitones"),
                             "tempo_ratio": metrics.get("tempo_ratio"),
                             "harmonic_z": metrics.get("harmonic_similarity_z"),
                             "chord_similarity": metrics.get("chord_sequence_similarity"),
                             "matched_phrases": metrics.get("matched_phrases"),
                             "target_phrases": metrics.get("target_phrases"),
                             "phrase_order": metrics.get("phrase_order_preserved_fraction")})
            print(f"versions {name}: done", flush=True)
    from music_benchmark import auc
    def values(variant, key):
        return [row[key] for row in rows if row["variant"] == variant and row["status"] != "unavailable"
                and row[key] is not None]
    shifted_rows = [row for row in rows if row["variant"] == "transposed_stretched" and row["status"] != "unavailable"]
    summary = {
        "pairs": len(names),
        "unavailable": sum(1 for row in rows if row["status"] == "unavailable"),
        "transposition_correct": float(np.mean([row["transposition"] == semitones for row in shifted_rows])) if shifted_rows else None,
        # time_stretch(rate) shortens the audio, so the tempo rises by `stretch`.
        "tempo_ratio_within_4pct": float(np.mean([abs((row["tempo_ratio"] or 0) / stretch - 1) <= 0.04 for row in shifted_rows])) if shifted_rows else None,
        "auc_harmonic_z_transposed_vs_unrelated": auc(values("transposed_stretched", "harmonic_z"), values("unrelated_song", "harmonic_z")),
        "auc_harmonic_z_cut_vs_unrelated": auc(values("section_removed", "harmonic_z"), values("unrelated_song", "harmonic_z")),
        "auc_chord_similarity_transposed_vs_unrelated": auc(values("transposed_stretched", "chord_similarity"), values("unrelated_song", "chord_similarity")),
        "median_harmonic_z": {variant: (float(np.median(values(variant, "harmonic_z"))) if values(variant, "harmonic_z") else None)
                              for variant in ("transposed_stretched", "section_removed", "unrelated_song")},
        "mean_phrase_order_cut": (float(np.mean(values("section_removed", "phrase_order"))) if values("section_removed", "phrase_order") else None),
    }
    markdown = "\n".join(["| Measure | Value |", "|---|---|"] +
                         [f"| {key} | {json.dumps(value)} |" for key, value in summary.items()])
    write_results(out / "versions", rows, summary, markdown)
    return summary


# --- output ---------------------------------------------------------------------------

def write_results(folder, rows, summary, markdown):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rows.json").write_text(json.dumps(rows, indent=1, default=float))
    (folder / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    (folder / "summary.md").write_text(markdown + "\n")
    print(markdown, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["fetch", "salami", "guitarset", "versions"])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--salami-limit", type=int, default=100)
    parser.add_argument("--msaf-python", help="Python of a separate environment with msaf installed")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if args.mode == "fetch":
        fetch(args.data, args.salami_limit)
    elif args.mode == "salami":
        run_salami(args.data, args.out, args.limit, args.msaf_python)
    elif args.mode == "guitarset":
        run_guitarset(args.data, args.out, args.limit)
    else:
        run_versions(args.data, args.out, args.limit or 40)


if __name__ == "__main__":
    main()
