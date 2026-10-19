"""Offsets where both fingerprint algorithms voted, including sub-threshold votes.

audfprint (landmark hashes) and SpectroMap (peak pairs) tokenise the same
audio differently, so their chance collisions largely fall at different
offsets. An offset where both have votes is therefore a stronger content
candidate than either provider's votes alone, even when one is below its own
candidate threshold. Both still analyse the same signal: agreement is not
independent evidence, never establishes provenance, and never scores.
"""

import bisect
import math

from .contracts import result, unavailable

AGREEMENT = "fingerprint_agreement"
PAIR = ("audfprint", "spectromap")
MINIMUM_VOTES = 2


def expected_rows(providers):
    """Rows a worker emits for a provider list: the providers, then agreement."""
    providers = list(providers)
    return providers + ([AGREEMENT] if all(name in providers for name in PAIR) else [])


def _poisson_tail(mean, minimum):
    """P(X >= minimum) for X ~ Poisson(mean), without optional dependencies."""
    if mean <= 0:
        return 0.0
    term = total = math.exp(-mean)
    for k in range(1, minimum):
        term *= mean / k
        total += term
    return max(0.0, 1.0 - total)


def _points(profile):
    rows = sorted((bucket * profile["bin_seconds"] + profile["range_offset_seconds"], votes)
                  for bucket, votes in profile["votes"].items() if votes >= MINIMUM_VOTES)
    return [offset for offset, _ in rows], [votes for _, votes in rows]


def fingerprint_agreement(task, rows, profiles):
    if any(rows.get(name, {}).get("status") != "inconclusive" or name not in profiles for name in PAIR):
        return unavailable(task, AGREEMENT, "provider_inputs_unavailable",
                           "Agreement needs completed audfprint and SpectroMap analyses of this pair.")
    first, second = (profiles[name] for name in PAIR)
    tolerance = max(first["bin_seconds"], second["bin_seconds"])
    offsets, counts = _points(second)
    matches = []
    for bucket, votes in first["votes"].items():
        if votes < MINIMUM_VOTES:
            continue
        offset = bucket * first["bin_seconds"] + first["range_offset_seconds"]
        low = bisect.bisect_left(offsets, offset - tolerance)
        high = bisect.bisect_right(offsets, offset + tolerance)
        if low == high:
            continue
        best = max(range(low, high), key=lambda index: counts[index])
        matches.append({
            "source_minus_target_offset_seconds": (offset + offsets[best]) / 2,
            "audfprint_votes": votes, "spectromap_votes": counts[best],
            "audfprint_votes_over_background": votes / max(first["background"], 1e-12),
            "spectromap_votes_over_background": counts[best] / max(second["background"], 1e-12),
        })
    # Neighbouring audfprint bins can meet the same SpectroMap bin; keep the
    # strongest pairing per agreed offset, strongest weaker side first.
    matches.sort(key=lambda row: -min(row["audfprint_votes_over_background"],
                                      row["spectromap_votes_over_background"]))
    kept = []
    for row in matches:
        if all(abs(row["source_minus_target_offset_seconds"] - other["source_minus_target_offset_seconds"]) > tolerance
               for other in kept):
            kept.append(row)
    # Under independent Poisson backgrounds, the number of offsets at which
    # both algorithms would reach MINIMUM_VOTES by chance alone.
    chance = (min(first["feasible_bins"], second["feasible_bins"]) *
              _poisson_tail(first["background"], MINIMUM_VOTES) *
              _poisson_tail(second["background"], MINIMUM_VOTES) *
              (2 * tolerance / second["bin_seconds"] + 1))
    value = result(task, AGREEMENT)
    value.update(provider_version=None, analysis_complete=True, matches=kept,
                 analysed_ranges=rows[PAIR[0]]["analysed_ranges"],
                 metrics={"agreed_offsets": len(kept), "tolerance_seconds": tolerance,
                          "minimum_votes_per_provider": MINIMUM_VOTES,
                          "audfprint_background": first["background"],
                          "spectromap_background": second["background"],
                          "expected_chance_agreements": chance})
    value["limitations"].append(
        "Both algorithms analyse the same audio; agreement reduces algorithm-specific chance matches "
        "but is not independent evidence. The chance estimate assumes Poisson backgrounds.")
    if not kept:
        value.update(reason_code="no_match_candidates",
                     message="No offset received votes from both fingerprint algorithms; non-matching is inconclusive.")
    return value
