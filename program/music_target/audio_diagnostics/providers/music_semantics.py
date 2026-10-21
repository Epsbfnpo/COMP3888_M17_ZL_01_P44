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
from .structure import section_features, detect_sections
from .calibration import qualify_comparison, path_rejections

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

    Legacy compatibility form, distinct from the acoustic sections now inferred
    from per-file novelty. Labels group aligned harmony, not verse/chorus roles.
    A short final remainder joins the last phrase. Grid offsets are retained
    only for these legacy summaries and do not select acoustic boundaries.
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
    beat_status = "tracked" if len(beat_frames) >= 8 else "insufficient_beats_using_time_grid"
    if len(beat_frames) >= 8:
        frames = librosa.util.fix_frames(beat_frames, x_min=0, x_max=chroma.shape[1])
    else:
        # Acoustic sections can still be measured in rubato/ambient material.
        grid = max(1, round(0.5 * rate / hop))
        frames = librosa.util.fix_frames(np.arange(0, chroma.shape[1], grid), x_min=0, x_max=chroma.shape[1])
    beat_chroma = librosa.util.sync(chroma, frames, aggregate=np.median)
    beat_timbre = librosa.util.sync(timbre, frames)
    times = np.minimum(librosa.frames_to_time(frames, sr=rate, hop_length=hop) + audio["range"][0],
                       audio["range"][1])
    grid_stride = max(1, round(parameters["alignment_grid_seconds"] * rate / hop))
    grid_frames = librosa.util.fix_frames(np.arange(0, chroma.shape[1], grid_stride), x_min=0, x_max=chroma.shape[1])
    grid_chroma = librosa.util.sync(chroma, grid_frames, aggregate=np.median)
    grid_times = np.minimum(librosa.frames_to_time(grid_frames, sr=rate, hop_length=hop) + audio["range"][0], audio["range"][1])
    bounds, labels = _phrases(beat_chroma, beat_timbre, parameters, librosa)
    structural, section_times = section_features(chroma, timbre, hop / rate, audio["range"],
                                               parameters.get("section_aggregation_seconds", 0.5))
    sections, section_details = detect_sections(structural, section_times, audio["range"], parameters)
    return {"grid_chroma": grid_chroma, "grid_times": grid_times, "sections": sections, "section_details": section_details,
            "section_features": structural, "section_times": section_times, "beat_tracking_status": beat_status,
            "audio_range": audio["range"], "chroma": beat_chroma, "times": times, "chords": _chords(beat_chroma), "bounds": bounds,
            "labels": labels, "tempo_bpm": float(np.atleast_1d(tempo)[0]) if len(beat_frames) >= 8 else None, "key": _key(beat_chroma),
            # Raw beat times, so beat tracking can be evaluated through this code path.
            "beat_times": librosa.frames_to_time(beat_frames, sr=rate, hop_length=hop) + audio["range"][0]}


def _summary(description):
    return {"tempo_bpm": description["tempo_bpm"], "key": description["key"],
            "beats": int(description["chroma"].shape[1]),
            "chords": [[_chord_name(label), count] for label, count in _run_length(description["chords"])],
            "form": _form(description["labels"]),
            "segmentation_method": "multiscale_feature_novelty",
            "section_labels_available": False,
            "sections_seconds": description["sections"],
            "section_detection": description["section_details"],
            "beat_tracking_status": description["beat_tracking_status"],
            "legacy_form_method": "fixed_beat_phrases",
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


def _locate_candidates(segment, reference, librosa):
    """Independent best position plus distinct near-equal alternatives.

    Never use the preceding phrase to break ties: doing so would assume the
    very order that the downstream order metric is intended to measure.
    Overlapping paths describing the same location are grouped together.
    """
    with np.errstate(all="ignore"):
        cost, steps = librosa.sequence.dtw(X=segment, Y=reference, metric="cosine", subseq=True,
                                           backtrack=False, return_steps=True)
        final = cost[-1] / segment.shape[1]
        near = np.flatnonzero(final <= final.min() + 0.01)
        alternatives = []
        best = None
        for end in sorted(near, key=lambda index: (final[index], index)):
            path = np.asarray(librosa.sequence.dtw_backtracking(steps, subseq=True, start=int(end)))[::-1]
            cosine = float(np.mean(np.sum(segment[:, path[:, 0]] * reference[:, path[:, 1]], axis=0)))
            if best is None:
                best = (cosine, path)
            start, stop = int(path[:, 1].min()), int(path[:, 1].max()) + 1
            if any(max(0, min(stop, other["end_beat"]) - max(start, other["start_beat"])) /
                   (max(stop, other["end_beat"]) - min(start, other["start_beat"])) >= 0.5
                   for other in alternatives):
                continue
            alternatives.append({"start_beat": start, "end_beat": stop,
                                 "mean_dtw_cost": float(final[end]), "harmonic_similarity": cosine})
    return best[0], best[1], alternatives


def _locate(segment, reference, previous_end, librosa):
    """Compatibility wrapper; previous_end no longer influences the match."""
    cosine, path, _ = _locate_candidates(segment, reference, librosa)
    return cosine, path[:, 1]


def _path_quality(path, source, target):
    source_indices, target_indices = path[:, 0], path[:, 1]
    def span(description, indices):
        return float(description["times"][int(indices.max()) + 1] -
                     description["times"][int(indices.min())])
    source_span, target_span = span(source, source_indices), span(target, target_indices)
    return {"source_span_seconds": source_span, "target_span_seconds": target_span,
            "source_to_target_duration_ratio": source_span / target_span if target_span > 0 else None,
            "source_unique_beats": len(set(map(int, source_indices))),
            "source_beat_coverage_fraction": len(set(map(int, source_indices))) / source["chroma"].shape[1],
            "target_beat_coverage_fraction": len(set(map(int, target_indices))) / target["chroma"].shape[1],
            "target_unique_beats": len(set(map(int, target_indices))),
            "max_target_beats_per_source_beat": int(np.bincount(source_indices).max()),
            "max_source_beats_per_target_beat": int(np.bincount(target_indices).max()),
            "diagonal_step_fraction": (float(np.mean(np.all(np.diff(path, axis=0) > 0, axis=1)))
                                       if len(path) > 1 else 0.0),
            "acceptance_status": "not_calibrated"}


def _aligned_tempo_candidate(path, source, target):
    """Robust source-seconds per target-second; release requires profile gates."""
    x, y = target["times"][path[:, 1]], source["times"][path[:, 0]]
    # Collapse DTW repetitions before fitting so a stretched path cannot
    # multiply one target timestamp's influence on the estimate.
    times = np.unique(x)
    values = np.array([np.median(y[x == time]) for time in times])
    if len(times) < 3 or np.ptp(times) <= 0:
        return {"ratio": None, "residual_seconds": None, "status": "insufficient_alignment"}
    design = np.column_stack((times - times.mean(), np.ones(len(times))))
    weights = np.ones(len(times))
    for _ in range(4):
        fit = np.linalg.lstsq(design * np.sqrt(weights[:, None]), values * np.sqrt(weights), rcond=None)[0]
        residuals = values - design @ fit
        scale = max(float(np.median(np.abs(residuals - np.median(residuals)))) * 1.4826, 1e-6)
        weights = np.minimum(1.0, 1.345 * scale / np.maximum(np.abs(residuals), 1e-12))
    return {"ratio": float(fit[0]), "residual_seconds": float(np.sqrt(np.mean(residuals ** 2))),
            "status": "candidate_not_calibrated", "method": "robust_aligned_source_seconds_per_target_second"}


def _comparison(source, target, parameters, librosa):
    """All 12 pitch candidates and an identically searched shuffled reference."""
    source_unit, target_unit = _unit(source["chroma"]), _unit(target["chroma"])
    target_is_query = target_unit.shape[1] <= source_unit.shape[1]
    candidates, paths = [], []
    for step in range(12):
        shifted = np.roll(source_unit, step, axis=0)
        query, reference = (target_unit, shifted) if target_is_query else (shifted, target_unit)
        cosine, path = _alignment(query, reference, librosa)
        absolute = path[:, [1, 0]] if target_is_query else path
        quality = _path_quality(absolute, source, target)
        aligned = _aligned_tempo_candidate(absolute, source, target)
        candidates.append({"transposition_semitones": step if step <= 6 else step - 12,
                           "harmonic_similarity": cosine, "alignment_quality": quality,
                           "aligned_tempo_candidate": aligned})
        paths.append(absolute)
    rng = np.random.default_rng(0)
    baseline = []
    for _ in range(parameters["baseline_shuffles"]):
        permutation = rng.permutation(min(source_unit.shape[1], target_unit.shape[1]))
        trials = []
        for step in range(12):
            shifted = np.roll(source_unit, step, axis=0)
            query, reference = (target_unit, shifted) if target_is_query else (shifted, target_unit)
            trials.append(_alignment(query[:, permutation], reference, librosa)[0])
        baseline.append(max(trials))
    mean, spread = float(np.mean(baseline)), float(np.std(baseline))
    for item in candidates:
        item["harmonic_similarity_z"] = (item["harmonic_similarity"] - mean) / max(spread, 1e-6)
        quality = item["alignment_quality"]
        target_span = quality["target_span_seconds"]
        item["alignment_residual_fraction"] = (item["aligned_tempo_candidate"]["residual_seconds"] / target_span
                                               if target_span > 0 and item["aligned_tempo_candidate"]["residual_seconds"] is not None else None)
    # An independent fixed-time raster checks tempo without inheriting beat-count/octave errors.
    best = max(candidates, key=lambda c: c["harmonic_similarity"])
    grid_source = {"chroma": source["grid_chroma"], "times": source["grid_times"]}
    grid_target = {"chroma": target["grid_chroma"], "times": target["grid_times"]}
    first = _unit(np.roll(grid_source["chroma"], best["transposition_semitones"], axis=0))
    second = _unit(grid_target["chroma"])
    target_query = second.shape[1] <= first.shape[1]
    _, raster_path = _alignment(second if target_query else first, first if target_query else second, librosa)
    raster_path = raster_path[:, [1, 0]] if target_query else raster_path
    raster_tempo = _aligned_tempo_candidate(raster_path, grid_source, grid_target)
    raster_quality = _path_quality(raster_path, grid_source, grid_target)
    raster_residual = (raster_tempo["residual_seconds"] / raster_quality["target_span_seconds"]
                       if raster_tempo["residual_seconds"] is not None and raster_quality["target_span_seconds"] > 0 else None)
    information = min(float(np.mean(np.linalg.norm(np.diff(unit, axis=1), axis=0)))
                      for unit in (source_unit, target_unit))
    return {"candidates": candidates, "paths": paths, "shuffled_mean": mean, "shuffled_std": spread,
            "temporal_information": information, "target_is_query": target_is_query,
            "time_grid_tempo": raster_tempo, "time_grid_quality": raster_quality, "time_grid_residual_fraction": raster_residual}


def _unassessed_local_match(target, first, last, status, error=None):
    """A local location that could not be searched; never a match or a rejection."""
    row = {"target_range_seconds": [float(target["times"][first]), float(target["times"][last])],
           "source_range_seconds": None, "harmonic_similarity": None, "match_status": status,
           "location_ambiguous": False, "alternative_source_ranges_seconds": [], "alignment_quality": None}
    if error is not None:
        row["error"] = f"{type(error).__name__}: {error}"
    return row, np.array([], dtype=int)


def _local_match(source, target, first, last, shifted, target_unit, librosa):
    # Subsequence DTW needs the query to fit inside the reference. When it does
    # not, librosa transposes the cost matrix internally and the manual
    # backtracking below would return swapped indices, so do not search.
    if last - first > shifted.shape[1]:
        return _unassessed_local_match(target, first, last, "not_assessed_query_longer_than_source")
    cosine, path, alternatives = _locate_candidates(target_unit[:, first:last], shifted, librosa)
    mapped = path[:, 1]
    absolute = np.column_stack((mapped, path[:, 0] + first))
    quality = _path_quality(absolute, source, target)
    quality["query_beat_coverage_fraction"] = len(set(map(int, path[:, 0]))) / (last - first)
    aligned = _aligned_tempo_candidate(absolute, source, target)
    residual = (aligned["residual_seconds"] / quality["target_span_seconds"]
                if aligned["residual_seconds"] is not None and quality["target_span_seconds"] > 0 else None)
    reasons = path_rejections(quality, aligned, residual)
    quality["acceptance_status"] = "rejected" if reasons else "geometry_within_profile"
    quality["rejection_reasons"] = reasons
    return {"target_range_seconds": [float(target["times"][first]), float(target["times"][last])],
            "source_range_seconds": [float(source["times"][mapped.min()]), float(source["times"][mapped.max() + 1])],
            "harmonic_similarity": cosine,
            # Local paths lack an independently calibrated local null reference.
            "match_status": "rejected_path_quality" if reasons else "candidate_only",
            "location_ambiguous": len(alternatives) > 1,
            "alternative_source_ranges_seconds": [[float(source["times"][a["start_beat"]]),
                                                    float(source["times"][a["end_beat"]])] for a in alternatives],
            "alignment_quality": quality}, mapped


def analyse(task, parameters, context=None):
    import librosa
    source_audio, target_audio = read_pair(task, parameters["sample_rate_hz"], (context or {}).get("audio_cache"))
    source, target = _describe(source_audio, parameters, librosa), _describe(target_audio, parameters, librosa)
    comparison = _comparison(source, target, parameters, librosa)
    best_index = max(range(12), key=lambda i: comparison["candidates"][i]["harmonic_similarity"])
    best = comparison["candidates"][best_index]
    assessment = qualify_comparison(comparison, parameters)
    # A fallback time grid is useful for sections but was not the tempo calibration population.
    if any(d["beat_tracking_status"] != "tracked" for d in (source, target)):
        assessment["status"] = "abstained"
        assessment["rejection_reasons"].append("insufficient_tracked_beats")
    supported = assessment["status"] == "supported_within_profile"
    shift = best["transposition_semitones"]
    shifted, target_unit = _unit(np.roll(source["chroma"], shift, axis=0)), _unit(target["chroma"])
    def local_match(first, last):
        # One unsearchable location must not discard every other observation.
        try:
            return _local_match(source, target, first, last, shifted, target_unit, librosa)
        except Exception as exc:
            return _unassessed_local_match(target, first, last, "not_assessed_local_match_failed", exc)

    matches = []
    # Legacy beat phrases remain explicit compatibility diagnostics, never section boundaries.
    for (first, last), label in zip(target["bounds"], target["labels"]):
        row, mapped = local_match(first, last)
        row.update(target_phrase_label=label, source_phrase_labels=sorted({
            source["labels"][i] for i, (a, b) in enumerate(source["bounds"])
            if ((mapped >= a) & (mapped < b)).any()}))
        matches.append(row)
    section_matches = []
    for section in target["sections"]:
        first = min(int(np.searchsorted(target["times"], section[0], side="right") - 1), target_unit.shape[1] - 1)
        last = min(max(first + 1, int(np.searchsorted(target["times"], section[1]))), target_unit.shape[1])
        row, _ = local_match(max(0, first), last)
        row["detected_target_section_seconds"] = section
        section_matches.append(row)
    searched = [row for row in matches if row["harmonic_similarity"] is not None]
    qualifying = [row for row in searched if row["harmonic_similarity"] >= parameters["label_similarity"]
                  and row["match_status"] != "rejected_path_quality"]
    order_candidates = [row for row in searched if row["harmonic_similarity"] >= parameters["label_similarity"]]
    starts = [row["source_range_seconds"][0] for row in order_candidates]
    order_ambiguous = any(row["location_ambiguous"] for row in order_candidates)
    order_rejected = any(row["match_status"] == "rejected_path_quality" for row in order_candidates)
    candidate_order = (sum(b >= a for a, b in zip(starts, starts[1:])) / (len(starts) - 1)
                       if len(starts) > 1 else None)
    aligned_tempo = dict(comparison["time_grid_tempo"])
    aligned_tempo["status"] = assessment["status"]
    quality = dict(best["alignment_quality"])
    quality.update(acceptance_status=assessment["status"], rejection_reasons=assessment["rejection_reasons"])
    transposed = [None if chord is None else ((chord[0] + shift) % 12, chord[1]) for chord in source["chords"]]
    chord_similarity = difflib.SequenceMatcher(
        None, [label for label, _ in _run_length(transposed)],
        [label for label, _ in _run_length(target["chords"])], autojunk=False).ratio()
    bpm_ratio = target["tempo_bpm"] / source["tempo_bpm"] if source["tempo_bpm"] and target["tempo_bpm"] else None
    value = result(task, "music_semantics", "bounded_music_observation",
                   "Musical correspondence observations with bounded calibration and explicit abstention; no provenance verdict.")
    value.update(provider_version=librosa.__version__, adapter_version="1.2", analysis_complete=True,
                 analysed_ranges=ranges(source_audio, target_audio), matches=matches, metrics={
                     "source": _summary(source), "target": _summary(target),
                     "correspondence_assessment": assessment,
                     "transposition_semitones": shift if supported else None,
                     "transposition_status": assessment["status"],
                     "best_transposition_candidate_semitones": shift,
                     "transposition_candidate_scores": [c["harmonic_similarity"] for c in comparison["candidates"]],
                     "transposition_candidates": comparison["candidates"],
                     "segmentation_phrase_beats": parameters["phrase_beats"],
                     "section_matches": section_matches,
                     "tempo_ratio_method": "fixed_time_grid_alignment_confirmed_by_beat_alignment_with_profile_gates",
                     "aligned_tempo_candidate": aligned_tempo,
                     "beat_aligned_tempo_candidate": best["aligned_tempo_candidate"],
                     "time_grid_alignment_quality": comparison["time_grid_quality"],
                     "alignment_quality": quality,
                     "shuffled_baseline_scope": "best_of_12_transpositions_for_each_beat_shuffle",
                     "beat_tracker_tempo_ratio": bpm_ratio,
                     "tempo_ratio": aligned_tempo["ratio"] if supported else None,
                     "harmonic_similarity": best["harmonic_similarity"],
                     "harmonic_similarity_shuffled_mean": comparison["shuffled_mean"],
                     "harmonic_similarity_shuffled_std": comparison["shuffled_std"],
                     "harmonic_similarity_z": best["harmonic_similarity_z"],
                     "temporal_information": comparison["temporal_information"],
                     "chord_sequence_similarity": chord_similarity,
                     "matched_phrases": len(qualifying), "target_phrases": len(matches),
                     "phrase_order_preserved_fraction": None if order_ambiguous or order_rejected else candidate_order,
                     "candidate_phrase_order_fraction": candidate_order,
                     "phrase_order_status": ("ambiguous_locations" if order_ambiguous else
                                             "path_quality_rejected" if order_rejected else
                                             "candidate_only" if candidate_order is not None else "insufficient_matches"),
                     "phrase_order_basis": "legacy_fixed_beat_phrases_not_detected_sections",
                     "aligned_version": "target_within_source" if comparison["target_is_query"] else "source_within_target",
                 })
    value["limitations"] = [
        "Content correspondence does not establish authorship, historical direction or provenance.",
        "Acoustic-change sections are inferred from per-file chroma/MFCC novelty, not named verses or choruses; boundaries can be missed or fragmented.",
        "Legacy form/phrases and phrase-order fields use fixed beat phrases for compatibility; use sections_seconds for detected boundaries.",
        "Profile support is bounded to the documented controlled DSP and annotated-audio experiment; it is not a probability or general accuracy claim.",
        "Local section/phrase locations remain candidates; geometry passing alone does not establish correspondence.",
        "Shared chords, repeated material and unsupported timing can cause abstention; beat-shuffled z is not a relatedness probability.",
        "Chroma transposition is modulo 12 semitones and cannot identify octave shifts; chord templates cover major/minor triads.",
    ]
    return value
