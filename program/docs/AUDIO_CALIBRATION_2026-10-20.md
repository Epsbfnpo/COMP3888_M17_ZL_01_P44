# A02, A04–A07: Implementation, Calibration Results and Remaining Limitations

These changes apply to the current Desktop/V3/program working tree (HEAD 588d7e5, plus the previously completed fixes).
Audio observations remain non-scoring: scoring_eligible=false, integrity_contribution=null,
and content_match_is_provenance=false. The existing S02/S03/S05/S06 decisions,
scoring weights, confidence, subscription plan and top-up settings were not changed.

## Five Changes with Examples

| Issue | Small example | Current implementation | Remaining limitations |
|---|---|---|---|
| A02 Fixed phrases used as sections | The music changes section at 11.5 seconds, but a 16-beat grid might still split at 8 or 16 seconds | Added per-file multiscale chroma/MFCC novelty detection; the audio determines the number and boundaries of sections. sections_seconds and section_matches use detected sections. Fixed phrases remain only in explicitly marked legacy form/phrases fields | Detects acoustic changes without verse/chorus names; boundaries can be missed or fragmented. Repeated-section labels still refer to legacy local phrase labels and cannot identify actual sections |
| A04 Extreme DTW compression | 12 seconds of content maps into 0.46 seconds while mean similarity remains high | Global, phrase and detected-section paths are checked for duration ratio, effective span, coverage, diagonal-step fraction, repeated mappings and timing residuals. Invalid paths are explicitly rejected. Local paths remain candidates even when their geometry passes | A plausible path does not establish that two recordings represent the same work; local relationship thresholds have not been separately calibrated |
| A05 Distortion from dividing two BPM estimates | A known 1.12× speed-up may produce a BPM quotient of 1.1579; the first beat-alignment implementation also estimated N04_fast at about 0.941 | tempo_ratio now uses a robust slope from fixed-time-grid alignment. An independent beat-aligned fit cross-checks it; disagreement above 5% or an invalid path produces null. The old quotient moves to beat_tracker_tempo_ratio | Complex rearrangement, strong repetition or nonuniform speed changes can cause abstention. The validated controlled speed factors are 0.8–1.35 |
| A06 Always selecting a best similarity/transposition | Even a sustained single note always has a “best transposition,” which does not establish an arrangement relationship | Sequence alignment searches all 12 shifts, and each shuffled baseline also searches all 12. Joint gates assess similarity, sequence advantage, temporal information, pitch margin and path quality. Unsupported transposition_semitones/tempo_ratio values are null; raw candidates remain available for audit | This is not a probability model or proof of provenance. Difficult controls and natural covers/reorchestrations have not been validated at scale. Chroma cannot distinguish octave shifts |
| A07 Missing a known mixture source | The second source in the historical C_A_mix case had no candidate at density 20 | Default audfprint density increases from 20→40, with upstream code and the five-vote floor unchanged. Support also requires at least 1 second of windows and a peak at least 10 times background. The missing source is recovered at offset 0 with 17 votes | Weak mixture components can still be missed; recovery of every source is not guaranteed. Supported windows are not exact continuous matched durations |

## Data, Responsibilities and Ground-Truth Boundaries

Lumina generates original material; the local program creates derivatives, runs analysis and verifies scoring isolation.
This round made 12 requests: 6 succeeded, each producing 40 seconds, and 6 failed.
The starting balance was 39,045; reopening the page confirmed a balance of 38,985.
Net usage was 60/200 credits, leaving 140 within the authorized cap. No additional generation followed.
No transaction-level refund statement was available for verification, so every individual refund status remains
unknown; an individual refund cannot be inferred from the aggregate balance. See run_manifest for the actual
submitted text and failed task IDs. A watermark/c2pa filename is not treated as a signature-verification result.

The 5 existing Lumina originals and new N05/N11 form 7 development families. New N02/N04/N06/N08 were
originally the first version's held-out families. All time-stretching, pitch-shifting, cropping, noise addition
and mixing were generated deterministically locally, with all parent families kept in the same partition.
Each semantic benchmark explicitly uses only the first 24 seconds of its original; this is an experimental
choice and introduces no new truncation at product runtime. Derivation parameters provide ground truth;
prompt requests for BPM, key or section count are not measured labels. Independent-generation and
distinct-recording controls do not prove musical unrelatedness or the absence of shared training sources.

For A02, 16 recordings by different artists and human large-scale section annotations were obtained from
the official SALAMI/Internet Archive sources. They were assigned in advance to 8 development and 8 test
recordings, using the first available annotator's uppercase layer. Filenames were matched to official entries.
For all 16 recordings, decoded duration differed from both metadata and the annotation endpoint by less
than 3 seconds; no recording was removed based on performance. The development set includes an entry titled
interview; it was retained, and this limitation is disclosed. Duration checks do not replace manual
recording-to-annotation synchronization checks, which were not claimed in this round. CC0 annotations do
not imply that all audio is CC0. Downloaded audio was not copied into the program repository.

## Freezing and Validation, Including Failures

The first version used the development set to select gates. Subsequent Lumina held-out testing found an
approximately 16% error in the supported tempo estimate for N04_fast. The first profile, evaluation and
failure case are retained. Version two therefore adds an independent time-grid check with a predeclared
5% agreement threshold. The original four held-out families become regression cases and cannot be
presented as a fully independent test set for version two.

Version-two parameters and their digest were fixed before evaluating DSP relationships in natural_test.
Version-two semantic testing uses known DSP derivatives from 8 additional SALAMI recording families.
Those 8 recordings had already been used in a separate section-boundary test, so they cannot be described
as unseen throughout the entire study. Their derived pitch/speed results had not previously been used for
tuning. See policies/audio_calibration_profile.json and the benchmark-results directory for the exact
profile and SHA256. The digest is verified at runtime; changing feature-sampling parameters removes
profile support and causes abstention.

| Evaluation | Development | Held-out / subsequent validation |
|---|---|---|
| A02 Human-annotated boundary F@3s | 8 recordings: fixed phrases 0.316 → new method 0.527 | 8 recordings: 0.339 → 0.538; paired difference approximately +0.199, with an 8-recording bootstrap interval of [0.137,0.260] |
| A02 Human-annotated boundary F@0.5s | 0.013 → 0.306 | 0.064 → 0.299 |
| A02 Predicted-boundary consistency under perturbation | These results were not used for tuning | Across 8 recordings: F@3s 0.934 with 30 dB noise; 0.867 after a 1.12× speed change mapped back to original time. This measures consistency with original predictions, not accuracy against human annotations |
| A04–A06 Version-two known DSP derivatives | 7 families, 63 cases: 59 supported, 4 abstained; supported pitch estimates correct in 59/59 cases, maximum relative tempo error 1.76% | New semantic validation: 8 families, 72 cases: 57 supported, 15 abstained; supported pitch estimates correct in 57/57 cases, maximum relative tempo error 1.27% |
| A04–A06 Version-two controls | 21 independent-generation pairs + sustained tone: 0/22 supported | 28 distinct-recording pairs + sustained tone: 0/29 supported; this small sample cannot establish a population false-positive rate |
| First-version held-out set reused for version-two regression | — | Of 36 derivatives, 31 supported and 5 abstained; maximum relative tempo error 1.34%. N04_fast now abstains; 0 of 7 controls supported |
| A07 Mixture components and excerpts, offset error ≤0.1s | With identical support gates: 22/28 at density 20 and 26/28 at density 40 | Original 4 held-out families: 14/16 → 15/16; 0 of 6 controls supported. N02_mix0.25 remains missed |

These are case observations and measurements on limited samples. Error among supported cases must be
read alongside abstention counts. Neither 57/57 correct supported pitch estimates nor zero supported
controls implies 100% population accuracy. The section bootstrap reflects paired differences in these
8 recordings only, not all music genres.

## Interface, Thresholds and Reproduction

The audio policy is now audio-observations-bounded-v2/version 2; the music_semantics and audfprint
adapters are version 1.2. status remains inconclusive/unavailable, and no scoring layer is added.
Clients must handle the changed tempo_ratio method and null values, and inspect correspondence_assessment,
transposition_status and rejection_reasons. Local candidates must not be displayed as verified matches.
See AUDIO_DIAGNOSTICS.md for detailed gates and field semantics. The confidence issue remains deferred
as requested by the user.

Complete experimental records are in the workspace at output/lumina_repair_2026-10-20; shareable JSON
snapshots are in program/benchmarks/results/audio_calibration_2026-10-20. Original audio stays in the
workspace and is not distributed with the code patch. replay_manifest.json records local paths and original
file hashes. Update paths when moving to another machine, while preserving the original hashes.

```sh
python -m pip install -r requirements.txt -r requirements-audio.txt -r requirements-benchmark.txt
REQUIRE_AUDIO_BACKENDS=1 python -B -m unittest discover -s tests
python -B benchmarks/audio_calibration.py /path/to/replay_manifest.json --partition natural_test --out /tmp/music.json
python -B benchmarks/audio_calibration.py /path/to/replay_manifest.json --partition lumina_regression --mode fingerprint --out /tmp/fingerprint.json
python -B benchmarks/structure_benchmark.py salami --data /path/to/natural_benchmark --out /tmp/sections
```

Unit tests cover extreme long-to-short compression, tempo disagreement caused by beat-alignment aliasing,
pitch ties, parameters outside the calibration signature, constant material without forced sections,
non-grid section changes, stability under feature noise, exact section invariance across targets,
fallback with insufficient beats, real backends and core-scoring isolation. All previous scoring-fix
regressions are retained.

## Remaining Work

- A02: Verse/chorus function labels, stable identities for repeated actual sections, more genres/larger samples and multiple-annotator validation.
- A04/A06: Independent calibration of local section/phrase relationships, difficult shared-chord controls, natural covers and reorchestrations.
- A05: Reliable speed estimation under nonuniform changes, complex rearrangement and strong repetition; the current system permits abstention instead of forcing a value.
- A07: Weak mixture components may still be missed; the system does not conclude that a source was not used.
- S04 confidence and T03 real signature chains/trust policies are unchanged; historical transaction-level refunds and manual musical labels remain incomplete.

Algorithm and data references: [Official SALAMI data](https://github.com/DDMAL/salami-data-public),
[AudioLabs novelty-based segmentation explanation](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C4/C4S4_NoveltySegmentation.html),
[librosa segmentation benchmark example](https://librosa.org/doc/0.11.0/auto_examples/plot_segmentation.html),
and [upstream audfprint](https://github.com/dpwe/audfprint). These references do not establish provenance
or a probability of correctness.
