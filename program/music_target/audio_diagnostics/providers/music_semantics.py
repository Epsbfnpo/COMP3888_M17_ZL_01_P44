"""Musical semantics: key, tempo, chords and phrase form, compared across versions.

A demo, a mid-production mix and a final master legitimately differ as
signals, so fingerprints miss their relationship. Their harmony and form
usually persist: the same chord sequence, possibly transposed or at another
tempo, and the same order of phrases. This provider measures that with
librosa (beat tracking, CQT chroma, DTW), all
prior art, and reports it without scoring. Shared harmony is common across
unrelated songs, so a high similarity indicates musical relatedness, never
copying, direction or authorship; the beat-shuffled baseline shows how much
of it is sequence rather than a shared chord vocabulary.
"""

import difflib

import numpy as np

from ..contracts import result
from ..io import AnalysisUnavailable, read_pair, ranges

NOTES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
# Krumhansl–Kessler key profiles.
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _unit(data, axis=0):
    return data / np.maximum(np.linalg.norm(data, axis=axis, keepdims=True), 1e-12)


def _chord_templates():
    labels, rows = [], []
    for root in range(12):
        for suffix, third in (("", 4), ("m", 3)):
            row = np.zeros(12)
            row[[root, (root + third) % 12, (root + 7) % 12]] = 1
            labels.append((root, suffix))
            rows.append(row / np.linalg.norm(row))
    return labels, np.array(rows)


CHORDS, TEMPLATES = _chord_templates()


def _chord_name(chord):
    return "N" if chord is None else NOTES[chord[0]] + chord[1]


def _key(chroma):
    mean = chroma.mean(axis=1)
    best = max(((float(np.corrcoef(np.roll(profile, tonic), mean)[0, 1]), tonic, mode)
                for tonic in range(12) for mode, profile in (("major", MAJOR), ("minor", MINOR))),
               key=lambda item: item[0])
    return {"tonic": NOTES[best[1]], "mode": best[2], "profile_correlation": best[0]}


def _chords(chroma):
    unit = _unit(chroma)
    energy = np.linalg.norm(chroma, axis=0)
    with np.errstate(all="ignore"):  # Accelerate BLAS raises spurious FP flags
        scores = TEMPLATES @ unit
    out = []
    for column in range(chroma.shape[1]):
        best = int(np.argmax(scores[:, column]))
        quiet = energy[column] < 1e-3 * max(float(energy.max()), 1e-12)
        out.append(None if quiet or scores[best, column] < 0.5 else CHORDS[best])
    return out


def _run_length(labels):
    runs = []
    for label in labels:
        if not runs or runs[-1][0] != label:
            runs.append([label, 0])
        runs[-1][1] += 1
    return runs


def _sequence_similarity(first, second, librosa):
    """Mean cosine along a DTW path between two beat-chroma sequences."""
    with np.errstate(all="ignore"):  # Accelerate BLAS raises spurious FP flags
        _, path = librosa.sequence.dtw(X=first, Y=second, metric="cosine")
        return float(np.mean(np.sum(first[:, path[:, 0]] * second[:, path[:, 1]], axis=0)))


def _phrases(chroma, timbre, parameters, librosa):
    """Fixed phrases of ``phrase_beats`` beats, labelled by aligned harmony.

    Detected section boundaries fragment music whose sections differ mainly
    in chord order, and their accuracy can only be judged against annotated
    recordings; fixed phrases (4 bars in 4/4 by default) are explainable and
    still expose repeats and form. A short final remainder joins the last
    phrase. Phrases share a label when their beat-by-beat harmony aligns,
    not when their average chroma is similar: a verse and chorus built from
    the same chords in another order have near-identical averages.
    """
    beats, length = chroma.shape[1], parameters["phrase_beats"]
    unit = _unit(chroma)
    best = None
    # Intros and pickups shift where phrases begin. Try each bar-aligned grid
    # offset and keep the one on which the repeats line up best (fewest
    # distinct phrase types); beats before the first phrase form a pickup.
    for offset in range(0, min(length, max(beats - length, 1)), 4):
        starts = ([0] if offset else []) + list(range(offset, beats, length))
        if len(starts) > 1 and beats - starts[-1] < length / 2:
            starts.pop()
        bounds = list(zip(starts, starts[1:] + [beats]))
        labels = _label(unit, bounds, parameters, librosa)
        key = (len(set(labels[1:] if offset else labels)), offset)
        if best is None or key < best[0]:
            best = (key, bounds, labels)
    return best[1], best[2]


def _label(unit, bounds, parameters, librosa):
    labels, representatives = [], []
    for first, last in bounds:
        segment = unit[:, first:last]
        match = next((index for index, other in enumerate(representatives)
                      if _sequence_similarity(segment, other, librosa) >= parameters["label_similarity"]), None)
        if match is None:
            representatives.append(segment)
            match = len(representatives) - 1
        labels.append(_letter(match))
    return labels


def _form(labels):
    return " ".join(label if count == 1 else f"{label}×{count}" for label, count in _run_length(labels))


def _letter(index):
    return chr(65 + index) if index < 26 else f"S{index + 1}"


def _describe(audio, parameters, librosa):
    y, rate, hop = audio["samples"], parameters["sample_rate_hz"], parameters["hop_length"]
    if len(y) < rate * parameters["minimum_seconds"]:
        raise AnalysisUnavailable("insufficient_signal", "Too little audio for beat-level harmony and form.")
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=rate, hop_length=hop)
    chroma = librosa.feature.chroma_cqt(y=y, sr=rate, hop_length=hop)
    timbre = librosa.feature.mfcc(y=y, sr=rate, hop_length=hop, n_mfcc=13)[1:]
    if len(beat_frames) < 8:
        raise AnalysisUnavailable("insufficient_beats", "Too few beats were detected for beat-level harmony.")
    frames = librosa.util.fix_frames(beat_frames, x_min=0, x_max=chroma.shape[1])
    beat_chroma = librosa.util.sync(chroma, frames, aggregate=np.median)
    beat_timbre = librosa.util.sync(timbre, frames)
    times = librosa.frames_to_time(frames, sr=rate, hop_length=hop) + audio["range"][0]
    bounds, labels = _phrases(beat_chroma, beat_timbre, parameters, librosa)
    return {"chroma": beat_chroma, "times": times, "chords": _chords(beat_chroma), "bounds": bounds,
            "labels": labels, "tempo_bpm": float(np.atleast_1d(tempo)[0]), "key": _key(beat_chroma),
            # Raw beat times, so beat tracking can be evaluated through this code path.
            "beat_times": librosa.frames_to_time(beat_frames, sr=rate, hop_length=hop) + audio["range"][0]}


def _summary(description):
    return {"tempo_bpm": description["tempo_bpm"], "key": description["key"],
            "beats": int(description["chroma"].shape[1]),
            "chords": [[_chord_name(label), count] for label, count in _run_length(description["chords"])],
            "form": _form(description["labels"]),
            "phrases": [{"label": label, "range_seconds": [float(description["times"][first]),
                                                           float(description["times"][last])]}
                        for (first, last), label in zip(description["bounds"], description["labels"])]}


def _alignment(query, reference, librosa):
    """Subsequence DTW of query beats inside reference beats; mean cosine on the path."""
    with np.errstate(all="ignore"):  # Accelerate BLAS raises spurious FP flags
        _, path = librosa.sequence.dtw(X=query, Y=reference, metric="cosine", subseq=True)
        path = path[::-1]
        similarity = float(np.mean(np.sum(query[:, path[:, 0]] * reference[:, path[:, 1]], axis=0)))
    return similarity, path


def _locate(segment, reference, previous_end, librosa):
    """Best place for a phrase in the reference; among near-equal places,
    the one nearest after the previous phrase (repeated choruses tie)."""
    with np.errstate(all="ignore"):  # Accelerate BLAS raises spurious FP flags
        cost, steps = librosa.sequence.dtw(X=segment, Y=reference, metric="cosine", subseq=True,
                                           backtrack=False, return_steps=True)
        final = cost[-1] / segment.shape[1]
        near = np.flatnonzero(final <= final.min() + 0.01)
        end = int(min(near, key=lambda index: (index < previous_end, abs(index - previous_end))))
        path = np.asarray(librosa.sequence.dtw_backtracking(steps, subseq=True, start=end))[::-1]
        cosine = float(np.mean(np.sum(segment[:, path[:, 0]] * reference[:, path[:, 1]], axis=0)))
    return cosine, path[:, 1]


def analyse(task, parameters, context=None):
    import librosa
    source_audio, target_audio = read_pair(task, parameters["sample_rate_hz"], (context or {}).get("audio_cache"))
    source, target = _describe(source_audio, parameters, librosa), _describe(target_audio, parameters, librosa)
    source_mean, target_mean = _unit(source["chroma"].mean(axis=1)), _unit(target["chroma"].mean(axis=1))
    # Target relative to source: rolling the source up by `shift` semitones best matches the target.
    shift = int(np.argmax([float(np.roll(source_mean, step) @ target_mean) for step in range(12)]))
    shifted = _unit(np.roll(source["chroma"], shift, axis=0))
    target_unit = _unit(target["chroma"])
    # Align the shorter version inside the longer one (an excerpt, or a full
    # version with added intro/outro), then measure along that path.
    target_is_query = target_unit.shape[1] <= shifted.shape[1]
    query, reference = (target_unit, shifted) if target_is_query else (shifted, target_unit)
    similarity, path = _alignment(query, reference, librosa)
    rng = np.random.default_rng(0)
    baseline = [_alignment(query[:, rng.permutation(query.shape[1])], reference, librosa)[0]
                for _ in range(parameters["baseline_shuffles"])]
    # Each target phrase is located in the source on its own, so a phrase
    # moved to another place in the form is visible (a single global path
    # would be monotonic by construction).
    matches, previous_end = [], 0
    for (first, last), label in zip(target["bounds"], target["labels"]):
        cosine, mapped = _locate(target_unit[:, first:last], shifted, previous_end, librosa)
        previous_end = int(mapped.max()) + 1
        covered = sorted({source["labels"][index] for index, (a, b) in enumerate(source["bounds"])
                          if ((mapped >= a) & (mapped < b)).any()})
        matches.append({"target_range_seconds": [float(target["times"][first]), float(target["times"][last])],
                        "source_range_seconds": [float(source["times"][mapped.min()]),
                                                 float(source["times"][min(mapped.max() + 1, len(source["times"]) - 1)])],
                        "target_phrase_label": label, "source_phrase_labels": covered,
                        "harmonic_similarity": cosine})
    # Order is judged only over phrases that genuinely match where they were
    # placed; new material (an added intro or bridge) has no true location.
    starts = [row["source_range_seconds"][0] for row in matches
              if row["harmonic_similarity"] >= parameters["label_similarity"]]
    transposed = [None if chord is None else ((chord[0] + shift) % 12, chord[1]) for chord in source["chords"]]
    chord_similarity = difflib.SequenceMatcher(
        None, [label for label, _ in _run_length(transposed)],
        [label for label, _ in _run_length(target["chords"])], autojunk=False).ratio()
    spread = float(np.std(baseline)) if baseline else 0.0
    value = result(task, "music_semantics")
    value.update(provider_version=librosa.__version__, analysis_complete=True,
                 analysed_ranges=ranges(source_audio, target_audio), matches=matches, metrics={
                     "source": _summary(source), "target": _summary(target),
                     "transposition_semitones": shift if shift <= 6 else shift - 12,
                     "tempo_ratio": target["tempo_bpm"] / source["tempo_bpm"] if source["tempo_bpm"] else None,
                     "harmonic_similarity": similarity,
                     "harmonic_similarity_shuffled_mean": float(np.mean(baseline)) if baseline else None,
                     "harmonic_similarity_shuffled_std": spread,
                     "harmonic_similarity_z": ((similarity - float(np.mean(baseline))) / max(spread, 1e-6)
                                               if baseline else None),
                     "chord_sequence_similarity": chord_similarity,
                     "matched_phrases": len(starts), "target_phrases": len(matches),
                     "phrase_order_preserved_fraction": (
                         sum(b >= a for a, b in zip(starts, starts[1:])) / (len(starts) - 1)
                         if len(starts) > 1 else None),
                     "aligned_version": "target_within_source" if target_is_query else "source_within_target",
                 })
    value["limitations"].append(
        "Shared harmony and form are common across unrelated songs; similarity indicates musical relatedness, "
        "not copying, direction or authorship. Chords are triad templates on beat-level chroma.")
    return value
