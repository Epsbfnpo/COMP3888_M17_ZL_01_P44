# Optional audio observations

This integration adds **report findings only**. It does not change any axis,
weight, confidence, completeness requirement, withholding rule or severe cap.
The old PhysicalSimilarityPass remains disabled and is never re-enabled.
LLM integration is outside this change.

Install the optional dependencies in the program's virtual environment:

```sh
python -m pip install -r requirements-audio.txt
```

Run the normal input with audio observations explicitly enabled:

```sh
python -B run.py --native examples/valid-generated/request.json \
  --root examples/valid-generated --audio-analysis --output response-audio.json
```

The example above is the existing teaching fixture. It does not provide
calibration ground truth. To inspect a meaningful repeated excerpt, generate
the new synthetic example and run it:

```sh
python -B make_audio_diagnostic_example.py
python -B run.py --native examples/audio-diagnostics/request.json \
  --root examples/audio-diagnostics --audio-analysis \
  --output examples/audio-diagnostics/response.json
```

A local JSON policy can select a subset of providers and override analysis
parameters:

```sh
python -B run.py --native submission.json --root bundle \
  --audio-analysis-policy policies/audio_analysis_policy.json --output response.json
```

The native/CSEC request options stay unchanged. Both `--request` and `--bundle`
also accept the local audio flag. Without either flag, no optional imports,
snapshots or audio worker processes are started. Invalid policies and missing
providers append an informational diagnostic-unavailable finding while the
successful core response is retained.

The enabled pipeline runs the existing scoring worker first, then prepares
verified temporary WAV snapshots and invokes one isolated worker per task and
provider. It appends only `AUDIO_*` findings and the following object arrays:

- `assessment_checks.audio_fingerprint`: audfprint and SpectroMap observations;
- `assessment_checks.audio_structure`: librosa observations;
- `assessment_checks.audio_diagnostic_summary`: policy/version, task counts,
  diagnostic runtime, and the explicit non-scoring boundary.

Every record has `scoring_eligible: false`, `integrity_contribution: null`, and
`content_match_is_provenance: false`. Protocol validation rejects attempts to
change these fields. No diagnostics are fed into parser observations, cross
evidence, the scored boolean checks, or integrity layers.

The three providers have different roles:

| Provider | Measurements |
|---|---|
| audfprint | Pinned upstream landmark extraction; local deterministic offset voting; multiple repeated-source positions; supported time windows |
| SpectroMap | Upstream peak detection run on fixed-duration time windows; exact local peak-pair vocabulary and offset voting; records the frequency/time grid, overlap, peak window and pair zone |
| librosa | Chroma, MFCC and energy features; time aggregation; candidate boundaries, repetition and cross-file feature correspondences |

The upstream audfprint extraction files are vendored unmodified with their
MIT notice and original source notices. The integration passes sample arrays
to them and does not invoke their decoders or load uploaded databases, so no
FFmpeg executable is needed. SpectroMap is pinned to an upstream commit;
its package declares GPL-3.0. Dependency provenance is in
`music_target/audio_diagnostics/vendor/UPSTREAM.json`.

SpectroMap's `peak_search` derives its minimum peak distance from the size of
the array it receives (`fraction` × frame count on the time axis). Called once
on a whole file, peaks therefore became sparser as files grew longer: about
120 peaks/s for a 4-second clip but about 8 peaks/s for 3 minutes. A short
excerpt then shared few peaks with a long source. The adapter now calls
`peak_search` unchanged on consecutive `peak_window_seconds` windows (default
4.0 s, policy-configurable); a final partial window is zero-padded, and padding
cannot produce peaks. Peak density is therefore independent of file length.
Each peak is paired with its next three later peaks within 32 frames, using
exact frequency bins and frame gaps. Exact pairs keep chance collisions low at
the higher peak density. Fingerprint metrics also report
`mean_votes_per_offset_bin`, the chance-collision background against which a
candidate's `matched_token_pairs` can be read. It is context, not a threshold.

Only retained, declared audio-to-audio graph edges are considered. Core
isolated nodes/edges remain unavailable. Comparison does not create graph
edges, correct fields, or write estimated timings into the submission.
CompSheet checks require selections bound by source and target hashes. Missing
selection ranges remain unavailable rather than being invented. A supplied
range must include all four source/target start/end values. Time measurements
are converted back to the original file's timeline.

Only currently supported WAV encodings are accepted. The optional libraries do
not silently extend the evaluator's accepted file formats. Silence and mono
channel cancellation are reported as unavailable, never as contradictions.

Provider failures stay structured. A result that breaks the protocol inside
the worker, for example a non-finite metric or a changed identity field,
becomes `unavailable` with `invalid_provider_output`. If a worker process
crashes, the diagnostic message includes the tail of its stderr (at most 2000
characters). If a worker cannot be started, that one task and provider is
`unavailable` with `provider_start_failed`, and the remaining tasks still run.

All matching thresholds are **exploratory candidate thresholds**. Until
representative data are used for calibration, completed analyses use
`inconclusive`, even when matches exist. `unavailable` means analysis could not
run. Neither state affects scores. Matching is evidence of content
correspondence, not authorship or historical direction. The three observations
are not independent votes and are never averaged into a provenance score.

The previously proposed development caps (30 seconds per diagnostic run,
10 seconds per provider, 16 relations, 600 seconds per file, 64 MiB active PCM,
and 1500 feature frames) are **not implemented**, as requested. There is no
silent opening-only truncation. Existing file validation and the caller's
`options.timeout_seconds` remain in effect. Diagnostics use only the remaining
caller budget; when that budget is exhausted, core scores remain available and
unfinished diagnostics are marked unavailable. Longer recordings and first-run
librosa imports can take time; increase the caller timeout explicitly when
needed. Structure uses time aggregation and sparse recurrence, without a
fixed frame-count cap.

Run validation, including actual installed providers:

```sh
REQUIRE_AUDIO_BACKENDS=1 python -B -m unittest discover -s tests -v
```

Tests cover known repeated excerpts, a 20-second excerpt in a 3-minute source,
length-independent SpectroMap peak density, original-timeline restoration, real
SpectroMap peaks and librosa structure, provider stderr and start failures,
worker-side protocol violations, silence, partial declarations,
snapshot changes/path escape, provider timeout/invalid output, and preservation
of all existing core axes/checks/findings. Existing severe-cap and legacy-pass
isolation tests remain applicable. Synthetic fixtures are engineering tests,
not real-session accuracy or provenance validation.
