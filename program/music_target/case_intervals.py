"""Wilson score intervals for small case counts.

Calibration and benchmark figures are case counts such as 0 of 29 controls or
15 of 16 mixtures. A 95% Wilson interval shows how little such a count pins
down. It is not a population rate, and cases derived from one source family
are not independent, so the real uncertainty is wider than the interval.
"""

import math

Z_95 = 1.959963984540054


def wilson_interval(count, cases, z=Z_95):
    """Return (lower, upper) for count out of cases, or None when cases is 0."""
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (count, cases)):
        raise TypeError("count and cases must be integers")
    if not 0 <= count <= cases:
        raise ValueError("count must lie between 0 and cases")
    if cases == 0:
        return None
    rate = count / cases
    denominator = 1 + z * z / cases
    centre = (rate + z * z / (2 * cases)) / denominator
    half_width = z * math.sqrt(rate * (1 - rate) / cases + z * z / (4 * cases * cases)) / denominator
    # The bounds are exactly 0 and 1 at the extremes; clamp rounding error.
    lower = 0.0 if count == 0 else max(0.0, centre - half_width)
    upper = 1.0 if count == cases else min(1.0, centre + half_width)
    return lower, upper


def interval_record(count, cases):
    """The stored form: bounds rounded to three decimals, or None when cases is 0."""
    bounds = wilson_interval(count, cases)
    return None if bounds is None else {"lower": round(bounds[0], 3), "upper": round(bounds[1], 3)}


def case_count(measure, count, cases, note=None):
    """One case-count entry with its 95% Wilson interval."""
    entry = {"measure": measure, "count": count, "cases": cases, "wilson_95": interval_record(count, cases)}
    if note:
        entry["note"] = note
    return entry
