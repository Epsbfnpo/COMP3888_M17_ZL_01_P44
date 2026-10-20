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
verified temporary WAV snapshots and starts **one isolated worker per task**.
That worker runs every policy provider in turn and writes one JSON line per
provider as soon as it finishes. Each file range is decoded once and each
analysis rate resampled once, and the shared arrays are read-only. If the
caller's time runs out or the worker crashes, rows already received are kept
and only the unfinished providers become `unavailable`. On the real-data
check this cut a 25-relationship chain from 54 s to 29 s at the same memory.
It appends only `AUDIO_*` findings and the following object arrays:

- `assessment_checks.audio_fingerprint`: audfprint and SpectroMap observations;
- `assessment_checks.audio_structure`: librosa observations;
- `assessment_checks.audio_semantics`: musical semantics (key, tempo, chords,
  phrase form) compared across versions (see below);
- `assessment_checks.audio_fingerprint_agreement`: offsets where both
  fingerprint algorithms voted (see below);
- `assessment_checks.audio_diagnostic_summary`: policy/version, task counts,
  diagnostic runtime, and the explicit non-scoring boundary.

When the graph has no retained audio-to-audio relationships, one
`AUDIO_DIAGNOSTIC_NOT_APPLICABLE` info finding says so, instead of the report
being silently empty.

Every record has `scoring_eligible: false`, `integrity_contribution: null`, and
`content_match_is_provenance: false`. Protocol validation rejects attempts to
change these fields. No diagnostics are fed into parser observations, cross
evidence, the scored boolean checks, or integrity layers.

The four providers have different roles:

| Provider | Measurements |
|---|---|
| audfprint | Pinned upstream landmark extraction; local deterministic offset voting; multiple repeated-source positions; supported time windows |
| SpectroMap | Upstream peak detection run on fixed-duration time windows; exact local peak-pair vocabulary and offset voting; records the frequency/time grid, overlap, peak window and pair zone |
| librosa | Chroma, MFCC and energy features; time aggregation; candidate boundaries and repetitions within each file. Cross-file similarity is a summary only (`best_feature_cosine_quantiles`), because on real recordings unrelated material scored as high as related material |
| music_semantics | Per-file chroma/MFCC novelty boundaries; beat-chroma DTW across all 12 transpositions, a best-of-12 shuffled reference, independent time-grid tempo verification and bounded support/abstention. Fixed-phrase form remains a legacy diagnostic |

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
candidate's `matched_token_pairs` can be read. It is averaged over every
feasible offset, not only the offsets that received votes. It is context, not
a threshold.

Only retained, declared audio-to-audio graph edges are considered. Core
isolated nodes/edges remain unavailable. Comparison does not create graph
edges, correct fields, or write estimated timings into the submission.
CompSheet checks require selections bound by source and target hashes. A
selection that declares its source range and `target_start_seconds` but no
`target_end_seconds` copies its declared source span:
`target_end = target_start + (source_end - source_start)`. It is flagged
`range_source: "derived_from_declared_source_duration"`, and if the derived end
falls outside the target audio the result is `derived_range_outside_target`.
Every row carries `range_source` (`whole_file`, `declared` or derived). Other
partial ranges are not completed: `incomplete_declared_ranges` names the
missing fields. Time measurements are converted back to the original file's
timeline.

Only currently supported WAV encodings are accepted. The optional libraries do
not silently extend the evaluator's accepted file formats. Silence and mono
channel cancellation are reported as unavailable, never as contradictions.

Provider failures stay structured. A result that breaks the protocol inside
the worker, for example a non-finite metric or a changed identity field,
becomes `unavailable` with `invalid_provider_output`. If a worker process
crashes, the diagnostic message includes the tail of its stderr (at most 2000
characters). If a worker cannot be started, that one task and provider is
`unavailable` with `provider_start_failed`, and the remaining tasks still run.

Completed diagnostics remain `inconclusive`; `unavailable` means analysis could not
run. Neither state affects scores. The audfprint and music_semantics adapters
now have **bounded experimental calibration**, documented in
[AUDIO_CALIBRATION_2026-10-20.md](AUDIO_CALIBRATION_2026-10-20.md).
Other adapters and local correspondence candidates remain exploratory. Support
within the frozen profile is not a probability, authorship claim, or provenance
verdict. Providers are never averaged into a provenance score.

**Musical semantics (adapter 1.2).** Per-file CQT chroma and robustly scaled MFCCs
are pooled in time, independently of beats and comparison targets. Multiscale
local self-similarity novelty supplies `sections_seconds`, acoustic boundaries
with strengths, and `segmentation_method: multiscale_feature_novelty`.
The frozen development selection uses 12-second context, 6-second minimum
spacing, 0.03 prominence and 0.5-second pooling. These are inferred boundaries;
there are no verse/chorus names (`section_labels_available: false`). The `librosa`
provider retains its separate per-file feature-section algorithm.

The old `form`, `phrases`, and phrase-order fields remain compatibility
observations, explicitly marked `legacy_form_method: fixed_beat_phrases` and
`phrase_order_basis: legacy_fixed_beat_phrases_not_detected_sections`. They are
not used as detected boundaries. `section_matches` locates detected target
sections independently; local paths remain candidates even when their geometry
passes, because local relationship thresholds have not been separately calibrated.

Across versions:

- All 12 transpositions are searched by sequence alignment. Every shuffled
  reference also searches all 12, avoiding a selected-shift advantage. The
  `harmonic_similarity_z` is a diagnostic against this reference, never a
  probability. All raw candidates remain visible.
- `correspondence_assessment` is `supported_within_profile` or `abstained`, with
  reasons and a frozen profile digest. Support requires similarity >= 0.90,
  shuffled z >= 4, pitch margin >= 0.02, temporal variation >= 0.05, duration
  and aligned speed ratios in [0.5, 2], >= 8 aligned seconds on both sides,
  >= 0.8 query coverage, >= 0.6 diagonal steps, <= 4 repeated mappings and
  normalized timing residual <= 0.04. The observed speed calibration range
  was 0.8–1.35; the wider geometric guard is not evidence of validated accuracy
  throughout [0.5, 2]. Custom feature parameters outside the frozen signature
  cause abstention.
- `transposition_semitones` is null on abstention. The best raw candidate is
  separately available as `best_transposition_candidate_semitones`, so a tie,
  constant tone or independent control does not force a reported shift.
  Chroma cannot identify octave shifts.
- `tempo_ratio` now uses a robust slope of aligned source seconds against target
  seconds on an independent 0.25-second chroma raster. Beat-aligned and raster
  estimates must agree within 5%, both paths must pass the geometric gates,
  and both files must have tracked beats. Unsupported estimates are null.
  The old BPM quotient is retained as `beat_tracker_tempo_ratio`; the old
  beat-aligned fit is `beat_aligned_tempo_candidate`. This changes the
  meaning of `tempo_ratio`, and consumers must inspect its method/status.
- Independent local phrase locations never use the preceding phrase to break
  ties. Distinct near-equal locations are still reported using the existing
  mean-cost margin 0.01 and IoU grouping 0.5. These location heuristics are not
  calibrated relationship decisions. Ambiguous or geometrically rejected
  locations cannot release `phrase_order_preserved_fraction`; the separately
  marked candidate order remains exploratory.
- A low beat count uses a time grid for sections but abstains from the
  calibrated tempo/pitch result, rather than dividing missing BPM values.

**Fingerprint sensitivity (audfprint adapter 1.2).** The pinned upstream extractor
is unchanged. Default density increases from 20 to 40; the existing five-vote
floor is retained. Raw offset candidates also receive `support_assessment`:
profile support needs at least 1 second of token-neighbourhood windows and a
peak >= 10 times the mean feasible-bin background. The 1-second support
requirement does not certify continuous audio or imply accuracy for short clips.
Profile support applies only to the tested extraction/window/bin parameters.
No candidate does not establish source absence. Weak mixture components can
still be hidden. Two algorithms reading the same audio do not become independent
sources of provenance evidence.

The audio policy is `audio-observations-bounded-v2`, version 2. All three
non-scoring fields remain fixed. `librosa` is adapter 1.2: sections and repetitions use per-file feature scaling, while `mean_best_feature_cosine` uses a joint pair scale. Historical example
responses retain their old meanings and are not current expected outputs.

**Fingerprint agreement.** audfprint (landmark hashes) and SpectroMap (peak
pairs) tokenise the same audio differently, so their chance collisions mostly
fall at different offsets. When both run, the worker compares their raw offset
votes, including votes below either provider's candidate threshold, and
reports each offset where both have at least two votes within one offset bin.
Each agreed offset lists both vote counts, each count relative to that
provider's background, and `expected_chance_agreements` (a Poisson estimate of
how many such offsets chance alone would give). Agreement is **not independent
evidence**, because both algorithms read the same signal. It stays report-only
and never scores. On the Project Rock data it found 2 of 29 true takes inside
the final masters (`1T04` at 8.45 s and `6T03` at 3.61 s, where audfprint alone
had 4 votes, below its threshold) and 0 of 33 controls. It adds precision, not
recall.

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

Tests cover musical semantics on synthetic songs (transposition, tempo,
form, unrelated and reordered versions), a demo that differs from its master
leaving every core result unchanged, the per-task worker (one decode per file, rows kept after a timeout
or crash, real misbehaving child processes), fingerprint agreement including
sub-threshold votes, CompSheet end derivation, the not-applicable finding,
known repeated excerpts, a 20-second excerpt in a 3-minute source,
length-independent SpectroMap peak density, original-timeline restoration, real
SpectroMap peaks and librosa structure, provider stderr and start failures,
worker-side protocol violations, silence, partial declarations,
snapshot changes/path escape, provider timeout/invalid output, and preservation
of all existing core axes/checks/findings. Existing severe-cap and legacy-pass
isolation tests remain applicable. Synthetic fixtures are engineering tests,
not real-session accuracy or provenance validation.
