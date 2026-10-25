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
verified temporary audio snapshots and starts **one isolated worker per task**.
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
- `assessment_checks.audio_bandwidth`: one row per bound audio file, giving
  where its decoded audio stops compared with its container and bitrate (see
  below);
- `assessment_checks.audio_format_conversion`: for each `derived_from` edge
  declared as a format conversion, whether audfprint supports whole-file
  alignment within 0.1 s of 0 and the target's bandwidth assessment, beside
  the core `format_conversions` checks;
- `assessment_checks.audio_declared_range`: for each edge that declares all
  four range fields, whether the declared source window is supported and, if
  not, where the target's content is in the source (see "Declared ranges"
  below); `assessment_checks.audio_declared_range_search` holds the
  whole-source fingerprint rows behind it;
- `assessment_checks.audio_take_beyond_final`: for each AAF-used take, whether
  the seconds the edit did not use are absent from the target, with the
  fingerprint rows in `assessment_checks.audio_aaf_unused_range`; and
  `assessment_checks.audio_source_coverage`: without an AAF, which stretches of
  a source's content have no match in the target (see "Content beyond the
  final" below);
- `assessment_checks.audio_diagnostic_summary`: policy/version, task counts
  (pair tasks, `file_task_count` and `declared_range_search_task_count`),
  `declared_ranges_assessed` and `declared_ranges_not_supported`, decoder
  versions, diagnostic runtime, and the explicit non-scoring boundary.

When the graph has no retained audio-to-audio relationships, one
`AUDIO_DIAGNOSTIC_NOT_APPLICABLE` info finding says so, instead of the report
being silently empty. Per-file providers still run on every bound audio file,
and the message then says that each file's bandwidth was still measured.

Every record has `scoring_eligible: false`, `integrity_contribution: null`, and
`content_match_is_provenance: false`. Protocol validation rejects attempts to
change these fields. No diagnostics are fed into parser observations, cross
evidence, the scored boolean checks, or integrity layers.

The five providers have different roles:

| Provider | Measurements |
|---|---|
| audfprint | Pinned upstream landmark extraction; local deterministic offset voting; multiple repeated-source positions; supported time windows |
| SpectroMap | Upstream peak detection run on fixed-duration time windows; exact local peak-pair vocabulary and offset voting; records the frequency/time grid, overlap, peak window and pair zone |
| librosa | Chroma, MFCC and energy features; time aggregation; candidate boundaries and repetitions within each file. Cross-file similarity is a summary only (`best_feature_cosine_quantiles`), because on real recordings unrelated material scored as high as related material |
| music_semantics | Per-file chroma/MFCC novelty boundaries; beat-chroma DTW across all 12 transpositions, a best-of-12 shuffled reference, independent time-grid tempo verification and bounded support/abstention. Fixed-phrase form remains a legacy diagnostic |
| bandwidth | Per file, not per pair: the 90th-percentile spectrum over the whole file, the frequency where the content stops, and how that compares with the container, sample rate and MP3 bitrate. Heuristic and uncalibrated |

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

### Declared ranges

A declared window is compared only inside the declared seconds, so on its own
it cannot tell a false interval from audio that is hard to fingerprint. For
each edge that declares source and target start and end, the runner therefore
adds one companion task (`range_source: "declared_range_search"`). It runs only
audfprint and SpectroMap, reads the whole source and the declared target
window, and uses no file outside the declared edge.

The decision uses audfprint, the provider with a calibrated support profile.
The declared offset is `source_start - target_start`; offsets within 0.25 s
count as that alignment. `audio_declared_range[].outcome` is one of:

| Outcome | Meaning | Finding |
|---|---|---|
| `declared_range_supported` | audfprint supports the declared window at the declared offset | none |
| `content_found_elsewhere` | the window is not supported, but the target's content is supported at another source offset | `AUDIO_DECLARED_RANGE_NOT_SUPPORTED`, severity `low`, with `best_offset_seconds` and `best_source_range_seconds` |
| `content_not_located` | neither the window nor the whole source supports the target's content | the same code, severity `info`: processing such as time-stretching can prevent a match, so this is not a contradiction |
| `search_unavailable` | the window is not supported and the whole-source search did not complete | the same code, severity `info` |
| `insufficient_support`, `not_assessed` | the declared-window analysis itself could not conclude | none |
| `not_assessed_durations_differ` | the source and target windows differ by more than 0.05 s, which declares a speed change fingerprints cannot align | none |

Every supported offset is listed (`search_supported_offsets_seconds`), so
repeated material shows each place it occurs, and `spectromap_agrees` says
whether any SpectroMap candidate lies near the best offset. Like every other
diagnostic the finding is report-only: Integrity, confidence and caps are
unchanged. On the repeated-excerpt fixture a declared 7-11 s window (the
content is at 2-6 s and 10-14 s) gives `content_found_elsewhere` at 2.0 s,
and declared 2-6 s or 10-14 s windows give `declared_range_supported`.

### Content beyond the final

A stem separated out of the final mix matches that mix, so a match alone says
little about where a part came from. What a separated stem cannot hold is
material the final lacks. A recorder take normally does: other passes, false
starts, room tone. These reports look for it. Like every diagnostic they are
report-only and never change a score.

**With an AAF.** The AAF names the seconds of each recorder take that the edit
used. The unused seconds, less a 0.5 s handle on each side of every used range,
are split into pieces of at least 3 s (`range_source: "aaf_unused_range"`).
audfprint and SpectroMap compare each piece with the same target as the
used-range comparison. Absence only means something if that comparison can see
the take in the target at all. So the take's used ranges act as a positive
control, and a take whose used ranges were not found is reported
`no_positive_control` with no finding.

| `audio_take_beyond_final[].outcome` | Finding |
|---|---|
| `content_beyond_final`: control found; some unused seconds have no candidate in the target | `AUDIO_TAKE_CONTENT_BEYOND_FINAL` (info), with `unused_seconds_not_found` and `unused_seconds_found` |
| `unused_content_found_in_final`: control found; every analysed unused piece has a candidate | none for the take |
| `no_positive_control`, `not_analysable` | none |

Any unused piece with a candidate also gets `AUDIO_UNUSED_RANGE_FOUND_IN_FINAL`
(info), naming the strongest candidate (provider, matched pairs, supported
seconds). A weak candidate can be chance; a strong one can be another clip,
repeated material or an AAF that does not list every use. It is never a
contradiction. Every candidate counts as found, so seconds beyond the final
err low.

**Without an AAF.** For whole-file `mixed_from`, `stemmed_from`, `edited_from`,
`comped_from` and `derived_from` edges, `audio_source_coverage` uses the
audfprint row. Its metrics now include `source_content_windows_seconds`: the
source seconds that carry landmarks, so silence is never counted as missing
content. audfprint picks landmarks differently in a short file and a long one,
so even an exact copy of part of the mix matches only part of its landmarks.
Coverage is therefore judged by region. Windows of one candidate closer than
3 s are joined, and only uncovered stretches of at least 3 s that still hold at
least 2 s of landmark content count. A supported audfprint candidate is the
positive control.

| `audio_source_coverage[].outcome` | Finding |
|---|---|
| `content_beyond_target` | `AUDIO_SOURCE_CONTENT_BEYOND_TARGET` (info), with the uncovered regions |
| `contained_in_target` | none: a stem rendered for the mix is expected to be fully contained, so this is not evidence against it |
| `no_positive_control` | none |

These thresholds (0.5 s handles, 3 s pieces and regions, 2 s content) are
heuristic and uncalibrated. Missing a quiet or heavily processed part also
looks like "not found", so the reports support a take; they never count
against one.

Project Rock Ep01 (13 AAF-used takes, the WIP mix): 26 unused pieces were
analysed. The 6 takes whose used ranges were found all report
`content_beyond_final`, with 10.1-32.8 s of unused material not found in the
mix (119.9 s in total). The other 7 have no positive control and are not
assessed. One unused piece has a SpectroMap candidate of 6 matched pairs over
0.5 s, reported as `AUDIO_UNUSED_RANGE_FOUND_IN_FINAL`. With audio analysis the
run took 35.5 s at a 465 MB peak (previously about 19 s), and the core result
was identical with analysis on or off. In a no-AAF variant built from the same
files, a real take reports `content_beyond_target` with 15.1 s not found. Two
"takes" cut from the mix itself, one exact and one band-passed to 200-4000 Hz
at gain 0.8, both report `contained_in_target`.

The formats the core accepts are analysed: supported WAV encodings are read
directly, and FLAC and MP3 are decoded with soundfile (libsndfile). FLAC decodes
to the same samples as the WAV it came from. For MP3 the decoder's gapless
length is used when the file carries encoder delay and padding, otherwise its
padded frame length; decoded MP3 samples approximate the original, and
the calibration status lists the one tested MP3 configuration (libsndfile
1.2.2 with LAME 3.100, VBR, false-match and shared-loop pairs only) under
`encoded_configurations_tested` and
`mp3_encoded_queries_beyond_tested_configuration` under `not_tested`. The
summary records the decoder (`decoders`: soundfile and libsndfile versions),
because decoded FLAC and MP3 samples depend on it. Decoded audio is not peak
normalised before analysis.
Only the MPEG frames are passed to the decoder, and a decode that stops short
of the expected length is `unavailable` with `decoder_length_mismatch`, never
silently truncated. Without gapless information (no Xing/Info header) the
expected length is the frame count times the samples per frame, and a
shortfall of up to two frames is accepted: libsndfile drops part of the last
frames of such files (439 samples on a 2-minute CBR file). The
optional libraries do not silently extend the evaluator's accepted file
formats. Silence and mono
channel cancellation are reported as unavailable, never as contradictions.
Every threshold was calibrated on WAV; each row that reads a FLAC or MP3 file
carries `calibration.format_scope: calibrated_on_wav_only` with the endpoint
formats and a note, and no FLAC/MP3 calibration is planned.

Too little usable signal is a normal analytical outcome, not a failure to run.
Rows that are unavailable because of `silent_signal`, `insufficient_signal`,
`insufficient_landmarks`, `insufficient_peaks`, `insufficient_spectral_frames` or
`channel_cancellation` (and agreement rows that lack inputs only for those
reasons) append `AUDIO_INSUFFICIENT_SUPPORT` with `outcome:
insufficient_support`. `AUDIO_DIAGNOSTIC_UNAVAILABLE` with `outcome: not_run` is
kept for diagnostics that could not run, such as `provider_failed`,
`caller_timeout`, `dependency_unavailable` or an invalid declared range.
Completed observations carry `outcome: completed`; a completed non-match or an
abstained musical comparison is already a normal inconclusive observation.

Every row also carries a `calibration` object from
`policies/audio_calibration_status.json`: `status` (`bounded_calibration` for
audfprint and music_semantics; `exploratory_uncalibrated` for SpectroMap,
librosa and fingerprint agreement), a summary with case counts, `sample`,
`case_counts`, `transforms_tested`, `not_tested` and `false_match_rates` from
`benchmarks/false_match.py` (see `FALSE_MATCH_BENCHMARK_2026-10-21.md`).
`case_counts` lists each count under `development`, `held_out` or
`real_material` with `measure`, `count`, `cases` and a 95% Wilson interval
`wilson_95` (`lower`, `upper`); every false-match category carries the same
interval, and the record is marked `split: held_out`. The status file's
`intervals` object defines the splits. An interval is not a population rate,
and cases from one family are not independent (see the interval table in
`AUDIO_CALIBRATION_2026-10-20.md`). The frozen gate profile is unchanged. The summary records the status file's
SHA-256 as `calibration_status_sha256`.

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

A local location (a legacy phrase or a detected section) longer than the whole
source cannot be searched inside it, for example a short loop or stem compared
with a longer mix. Such a row reports `match_status:
not_assessed_query_longer_than_source` with null `harmonic_similarity`,
`source_range_seconds` and `alignment_quality`. If a single location fails
unexpectedly, only that row reports `not_assessed_local_match_failed` with an
`error` message; the provider and the other rows still complete. Unassessed rows
are excluded from `matched_phrases` and phrase-order measures.

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

The audio policy is `audio-observations-bounded-v2`, version 3 (version 3
adds the `bandwidth` provider). All three
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

**Content bandwidth (per-file, report-only).** A lossy encoder removes
everything above its low-pass frequency, and the gap survives decoding. A WAV
or FLAC made from an MP3 still stops where the MP3 did, an MP3 re-encoded at a
higher bitrate still stops where the lower bitrate did, and audio resampled up
to 96 kHz has nothing above the original Nyquist frequency. These are the
"trick files" listed by SoniqTools' test-audio page. The provider decodes the
file once and takes the 90th-percentile power spectrum of up to 600 frames of
8192 samples, spaced evenly over the whole file (silent frames are skipped).
Levels are relative to the 1–4 kHz median. It looks for two kinds of edge:

- a `sharp_cliff`: a drop of at least 20 dB, with everything above at least
  12 dB below the level just under the edge;
- a `step_into_flat_floor`: a drop of at least 6 dB into a floor whose 500 Hz
  bands vary by at most 3 dB. This catches a lossy decode re-quantised to
  16 bits, where dither fills the removed band (the SoniqTools fake FLAC drops
  only about 8 dB). The step search stops at 0.9 of the Nyquist frequency,
  where anti-alias filters roll off.

The edge is refined to where the smoothed level first falls halfway between
the levels below and above. `metrics.assessment.status` is then one of:

| Status | Meaning |
|---|---|
| `content_band_lower_than_bitrate_suggests` | MP3 whose content stops more than 1.5 kHz below the typical LAME/FFmpeg low-pass for its bitrate (320 kbps → 20 kHz, 128 kbps → 16.5 kHz, …) |
| `consistent_with_bitrate` | MP3 whose content reaches what its bitrate usually keeps |
| `not_assessed` | MP3 below 32 kHz, or a bitrate with no typical low-pass |
| `band_limit_like_lossy_encoding` | WAV or FLAC that stops at 11–20.5 kHz with a lossy-like edge. The message names the alternatives: made from an MP3 or similar, resampled up, or generated or filtered to this bandwidth |
| `consistent_with_upsampling` | WAV or FLAC that stops at 0.90–1.01 of a lower standard rate's Nyquist frequency; `likely_source_rate_hz` names that rate |
| `band_limited` | Any other edge below the Nyquist frequency |
| `no_band_limit_found` | No edge found |

Each flagged file also gets an `AUDIO_CONTENT_BAND_LIMITED` finding (severity
low, non-scoring, with the file's `evidence_hash`, the assessment and
`content_edge_hz`). Rows stay `inconclusive`; too little non-silent audio is
`unavailable` with `insufficient_signal`.

A band limit says where the audio stops, not why or who made the file. AI
generators produce band-limited audio natively: all 25 Lumina benchmark WAVs
stop at 17.4–19.9 kHz and are reported as `band_limit_like_lossy_encoding`, as
are 6 of 7 other Lumina exports (12.6–19.3 kHz). Mastering low-passes, speech,
old recordings and very quiet material can look the same, and no band limit
does not show that a file is original. The thresholds were set on the
SoniqTools Bach clip, locally made trick files and six free-site MP3s, so the
calibration status is `exploratory_uncalibrated`. The counts are in the
`bandwidth` record of `policies/audio_calibration_status.json`. On Project
Rock, 14 of 15 files show no band limit; the 48 kHz edit reference stops at
22.05 kHz and is reported as resampled up from 44.1 kHz, which matches the
44.1 kHz audio in the dataset's reference videos.

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

Tests cover content bandwidth on synthetic low-passed, floor-masked and
upsampled noise (`tests/test_bandwidth.py`), musical semantics on synthetic songs (transposition, tempo,
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
