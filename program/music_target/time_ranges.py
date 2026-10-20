"""Symmetric validation of content spans; no scoring or confidence policy."""

import math


def finite_number(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def content_range_checks(start, end, duration=None, sample_rate=None, role="source"):
    """Validate supplied endpoints independently, without completing missing ones.

    Content selections have positive length. End-of-file rounding gets one
    observed sample of tolerance; a start at/after EOF cannot select content.
    Absent ranges remain absent and are not zero-length event markers.
    """
    checks = []
    tolerance = 1 / sample_rate if finite_number(sample_rate) and sample_rate > 0 else 0.0
    bounded = finite_number(duration) and duration >= 0
    for name, value in (("start", start), ("end", end)):
        if value is None:
            continue
        valid = finite_number(value) and value >= 0
        checks.append({"rule": f"{role}_{name}_valid", "passed": valid,
                       "reason_code": "finite_nonnegative_endpoint", "declared": value})
        if valid and bounded:
            # Retain the existing end-bound rule IDs for consumers.
            rule = f"{role}_time_within_duration" if name == "end" else f"{role}_start_within_duration"
            inside = value <= duration + tolerance if name == "end" else value < duration
            checks.append({"rule": rule, "passed": inside, "declared": value,
                           "observed_duration_seconds": duration, "tolerance_seconds": tolerance})
    if start is not None and end is not None:
        checks.append({"rule": f"{role}_time_order_valid",
                       "passed": finite_number(start) and finite_number(end) and end > start,
                       "reason_code": "positive_content_span"})
    return checks
