"""Pinned upstream landmark extraction with deterministic local offset voting."""

from pathlib import Path
import sys

from ..contracts import result
from ..io import AnalysisUnavailable, read_pair, ranges
from .alignment import align_tokens
from .calibration import fingerprint_support, profile

UPSTREAM_COMMIT = "cb03ba99feafd41b8874307f0f4e808a6ce34362"


def analyse(task, parameters, context=None):
    # Upstream uses sibling absolute imports. Only the isolated diagnostic
    # worker loads them; the scoring interpreter never sees this path.
    vendor = Path(__file__).resolve().parents[1] / "vendor/audfprint"
    sys.path.insert(0, str(vendor))
    import audfprint_analyze as upstream
    source, target = read_pair(task, parameters["sample_rate_hz"], (context or {}).get("audio_cache"))
    analyzer = upstream.Analyzer(density=parameters["density"])
    analyzer.target_sr = parameters["sample_rate_hz"]
    tokens = []
    for audio in (source, target):
        if len(audio["samples"]) < analyzer.n_fft:
            raise AnalysisUnavailable("insufficient_signal", "Too little signal for audfprint landmarks.")
        peaks = analyzer.find_peaks(audio["samples"], analyzer.target_sr)
        hashes = upstream.landmarks2hashes(analyzer.peaks2landmarks(peaks))
        tokens.append([(int(row[0]), int(row[1])) for row in hashes])
    if not all(tokens):
        raise AnalysisUnavailable("insufficient_landmarks", "Insufficient audfprint landmarks in a selected range.")
    matches, metrics, offset_profile = align_tokens(*tokens, analyzer.n_hop / analyzer.target_sr,
                                     source, target, parameters)
    for match in matches:
        match["support_assessment"] = fingerprint_support(match, metrics, parameters)
    metrics["supported_offsets"] = sum(m["support_assessment"]["status"] == "supported_within_profile" for m in matches)
    metrics["calibration_profile_id"] = profile()["profile_id"]
    value = result(task, "audfprint", "bounded_fingerprint_observation",
                   "Landmark offset candidates with bounded support checks; no provenance verdict.")
    if context is not None:
        context["votes"]["audfprint"] = offset_profile
    value.update(provider_version=UPSTREAM_COMMIT, adapter_version="1.2", analysis_complete=True,
                 analysed_ranges=ranges(source, target), matches=matches, metrics=metrics)
    value["limitations"][1] = "Profile support is bounded to the documented mixture/excerpt experiment, not calibrated probability or provenance evidence."
    value["limitations"].append("Supported windows surround matched landmarks; they are not exact edit boundaries.")
    value["limitations"].append("Support fractions are candidate window coverage, not calibrated match confidence; "
                                "mixing can hide a real source and no candidate does not disprove a relationship.")
    if not matches:
        value.update(reason_code="no_match_candidates", message="No reliable provenance conclusion follows from the absence of fingerprint candidates.")
    return value
