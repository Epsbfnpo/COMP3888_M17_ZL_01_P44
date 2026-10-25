"""Offset voting and explicitly supported windows, without provenance verdicts."""

from collections import Counter, defaultdict
import math
import numpy as np


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([float(start), float(end)])
    return merged


def subtract_intervals(intervals, removed):
    """Parts of ``intervals`` not covered by ``removed``; both lists of [start, end]."""
    out = []
    removed = merge_intervals(removed)
    for start, end in merge_intervals(intervals):
        cursor = start
        for cut_start, cut_end in removed:
            if cut_end <= cursor or cut_start >= end:
                continue
            if cut_start > cursor:
                out.append([cursor, cut_start])
            cursor = max(cursor, cut_end)
        if cursor < end:
            out.append([cursor, end])
    return out


def content_windows(tokens, step, audio, window):
    """Seconds of the source timeline that carry landmarks: where a match could be found at all.

    Silence and near-silence give no landmarks, so they are not counted as
    content that the target lacks.
    """
    start, end = audio["range"]
    return merge_intervals((max(start, start + time * step - window / 2), min(end, start + time * step + window / 2))
                           for time, _ in set(tokens))


def align_tokens(source_tokens, target_tokens, step, source, target, parameters):
    from scipy.signal import find_peaks
    index = defaultdict(list)
    for time, token in set(source_tokens):
        index[token].append(time)
    votes, hits = Counter(), defaultdict(list)
    bin_seconds = parameters.get("offset_bin_seconds", step * 2)
    source_duration = source["range"][1] - source["range"][0]
    target_duration = target["range"][1] - target["range"][0]
    feasible_bins = (source_duration + target_duration) / bin_seconds + 1
    for query_time, token in set(target_tokens):
        for reference_time in index.get(token, []):
            offset = (reference_time - query_time) * step
            bucket = math.floor(offset / bin_seconds + 0.5)
            votes[bucket] += 1
            hits[bucket].append((float(query_time * step), offset))
    # Every bin's votes, including those below the candidate threshold, so a
    # second algorithm on the same pair can be compared offset by offset.
    profile = {"votes": dict(votes), "bin_seconds": bin_seconds, "feasible_bins": feasible_bins,
               "range_offset_seconds": source["range"][0] - target["range"][0],
               "background": sum(votes.values()) / feasible_bins}
    window = parameters["support_window_seconds"]
    content = content_windows(source_tokens, step, source, window)
    if not votes:
        return [], {"source_tokens": len(source_tokens), "target_tokens": len(target_tokens),
                    "raw_token_pair_matches": 0, "source_content_windows_seconds": content}, profile
    low, high = min(votes), max(votes)
    histogram = np.array([votes.get(key, 0) for key in range(low, high + 1)])
    peaks, _ = find_peaks(np.r_[0, histogram, 0],
                         height=parameters["minimum_candidate_matches"])
    candidates = sorted((low + int(peak) - 1 for peak in peaks),
                        key=lambda key: (-votes[key], key))
    matches = []
    for bucket in candidates:
        entries = hits[bucket]
        offset = float(np.median([entry[1] for entry in entries]))
        intervals = merge_intervals((max(0.0, time - window / 2, -offset),
                                     min(target_duration, time + window / 2, source_duration - offset))
                                    for time, _ in entries)
        if not intervals:
            continue
        supported_seconds = sum(b - a for a, b in intervals)
        matches.append({
            "source_minus_target_offset_seconds": offset + source["range"][0] - target["range"][0],
            "matched_token_pairs": len(entries),
            "target_supported_windows_seconds": [[a + target["range"][0], b + target["range"][0]]
                                                   for a, b in intervals],
            "source_supported_windows_seconds": [[a + offset + source["range"][0], b + offset + source["range"][0]]
                                                   for a, b in intervals],
            "target_supported_window_duration_seconds": supported_seconds,
            "source_supported_window_duration_seconds": supported_seconds,
            "source_supported_fraction": supported_seconds / source_duration if source_duration else None,
            "target_supported_fraction": supported_seconds / target_duration if target_duration else None,
            "support_semantics": "token_neighbourhood_windows_not_verified_continuous_audio",
        })
    return matches, {"source_tokens": len(source_tokens), "target_tokens": len(target_tokens),
                     "raw_token_pair_matches": sum(votes.values()), "candidate_offsets": len(matches),
                     # Chance-collision background against which a candidate's
                     # matched_token_pairs can be read; not a threshold. Averaged
                     # over every feasible offset, not just the voted span, so a
                     # narrow histogram cannot inflate it.
                     "mean_votes_per_offset_bin": float(profile["background"]),
                     "support_window_seconds": window,
                     # Landmark-bearing source seconds, for content-coverage reports.
                     "source_content_windows_seconds": content}, profile
