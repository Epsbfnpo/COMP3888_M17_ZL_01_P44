"""Pinned upstream landmark extraction with deterministic local offset voting."""

from pathlib import Path
import sys

from ..contracts import result
from ..io import AnalysisUnavailable, read_pair, ranges
from .alignment import align_tokens

UPSTREAM_COMMIT = "cb03ba99feafd41b8874307f0f4e808a6ce34362"


def analyse(task, parameters):
    # Upstream uses sibling absolute imports. Only the isolated diagnostic
    # worker loads them; the scoring interpreter never sees this path.
    vendor = Path(__file__).resolve().parents[1] / "vendor/audfprint"
    sys.path.insert(0, str(vendor))
    import audfprint_analyze as upstream
    source, target = read_pair(task, parameters["sample_rate_hz"])
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
    matches, metrics = align_tokens(*tokens, analyzer.n_hop / analyzer.target_sr,
                                     source, target, parameters)
    value = result(task, "audfprint")
    value.update(provider_version=UPSTREAM_COMMIT, analysis_complete=True,
                 analysed_ranges=ranges(source, target), matches=matches, metrics=metrics)
    value["limitations"].append("Supported windows surround matched landmarks; they are not exact edit boundaries.")
    if not matches:
        value.update(reason_code="no_match_candidates", message="No reliable provenance conclusion follows from the absence of fingerprint candidates.")
    return value
