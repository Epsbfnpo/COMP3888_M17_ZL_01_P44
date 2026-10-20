# V3 Remediation Record and Remaining Work (2026-10-20)

The initial remediation deferred scoring-policy decisions and confidence changes. The user subsequently
authorized S02, S03 and S06, and confirmed that the S05 finding about unauthenticated origin must not score,
while metadata-consistency checks must continue to score and may release Integrity. The statuses below
reflect those decisions; confidence formulas remain unchanged. The original report used 2b4fd69; this work
uses actual HEAD 588d7e5. The original report, historical responses and original audio are not rewritten.
The intervening changes added beat_times output and a structure-benchmark script, both retained here.
Issue IDs follow the conversation's 18-item master list and must not be confused with the 7-item local
numbering in the earlier Extended_Test_Report.

“Fixed” means that the deterministic defect described here has an implementation and regression validation.
“Partially addressed” means that only the executable portion is complete and the original issue remains open.
“Deferred” means that the corresponding behavior was not changed in this round.
No real C2PA signature chain was installed or verified. The subsequent audio remediation added Lumina
generation, using a net 60/200 credits without changing the subscription; see the
[audio calibration record](AUDIO_CALIBRATION_2026-10-20.md).

## Issue-by-Issue Results

| ID | Issue | Status | Changes in this round | Remaining work / boundaries |
|---|---|---|---|---|
| S01 | Asymmetric source/target range validation | Fixed | Both ends share finite-number, nonnegative, positive-length, start-boundary and end-boundary validation. Endpoints allow one original-sample rounding tolerance. Rejects JSON numeric overflow and non-finite values supplied through direct calls | Ranges in this interface select audio content, so zero-length ranges are invalid. Future instantaneous event markers require a separate type |
| S02 | Report copies dilute contradictions | Fixed | One vote per rule/object fact; any failure preserves the contradiction. Neither correct nor incorrect copies change that fact's score. Raw checks remain available | Document count is not treated as the number of independent sources |
| S03 | Detecting a contradiction releases a high Integrity value | Fixed | Ordinary contradictions no longer bypass the numeric-release gate; insufficient evidence leaves the value null. A severe contradiction needs no independent evidence and releases min(weighted mean, 0.25) (default-v4). `axes.integrity.status` and `integrity_decision` report status and release reason | Do not treat null as 0 or rank it alongside numeric values |
| S04 | confidence is interpreted as probability of correctness | Deferred | None | All axes' confidence fields, formulas, naming and statistical calibration remain pending a decision |
| S05 | Embedded metadata is interpreted as proof of origin | Changed according to the user's decision | Consistency checks retain scoring and release eligibility. Added the explicitly non-scoring RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED finding | Actual origin authentication is not implemented and is not claimed by the score |
| S06 | AI disclosure is interpreted as AI detection | Fixed | Retains the compatible wire key, with the display name Creation-method disclosure coverage. Reports declaration-coverage counts, that no AI detection was performed and that declarations are unverified; adds an explanatory finding | No content-based AI detector was added |
| S07 | Runtime and policy generation-record types disagree | Fixed | All three policy references use the registered text/generation-record type. Policy loading checks every declared artefact type. Configuration patch version becomes 0.1.1-candidate | No automatic inference or alias for the old incorrect type |
| A01 | Changing the target changes the same source's structure | Fixed | Sections and repetitions normalise MFCC/energy with each file's own mean/std; the cross-file similarity keeps a joint pair scale so level and timbre differences still count. Regression checks require exactly equal source sections across targets and a lower similarity for a quieter copy | Fixes a deterministic defect; it does not establish accuracy against actual musical section boundaries |
| A02 | Fixed 16-beat segmentation and unstable labels | Section detection implemented, with limitations | Added boundaries inferred from per-file chroma/MFCC novelty. Used 16 human-annotated SALAMI recordings, split 8/8 for parameter selection/validation. sections_seconds and section_matches use detected sections; old grid form remains explicitly marked as a compatibility diagnostic. Added perturbation-consistency tests | Not a verse/chorus naming system. Held-out boundary F@3s is about 0.538, with missed and fragmented boundaries. Repeated-section identities and more data still require validation |
| A03 | Preferring positions after the previous phrase biases the order metric upward | Fixed | Best position no longer depends on previous_end. Reports distinct near-equivalent locations. Ambiguity makes the order metric null, while retaining an explicitly marked candidate-order value | Closes the location-selection bias; overall rearrangement-detection performance still requires validation |
| A04 | DTW compresses a long phrase into a very short span | Acceptance gates fixed, with bounded calibration | Global/local paths check duration ratio, span, coverage, repeated mappings, diagonal steps and residuals. A 12-second-to-0.46-second mapping is rejected. Local paths remain candidates even if their geometry passes | Local relationship-acceptance thresholds have not been independently calibrated. Plausible geometry does not establish the same work |
| A05 | Dividing BPM estimates biases the speed ratio | Replaced and validated within a limited scope | tempo_ratio now uses a robust fixed-time-grid slope, cross-checked against a beat-aligned fit; disagreement produces null. The old quotient moves to beat_tracker_tempo_ratio. Validated on 72 DSP derivatives from 8 recording families in a separate task | 57 supported and 15 abstained; maximum relative error among supported cases is about 1.27%. Complex rearrangement/nonuniform speed can still cause abstention. No claim of general accuracy |
| A06 | Independent music can score above known relationships; a best transposition is always chosen | Bounded support/abstention added | Sequence-searches all 12 shifts; every shuffled baseline also searches all shifts. Joint gates include temporal information and pitch margin. Unsupported transposition is null; raw candidates remain separate | No probability model. Zero supported results among 29 new-task controls does not imply zero population false positives. Natural covers and difficult shared-chord controls need further validation |
| A07 | audfprint misses a known mixture source | Original reproduction fixed; recall remains limited | Density increases 20→40 with upstream extraction unchanged and the five-vote floor retained. Added supported-time/background gates. The previously missing source now has 17 votes; held-out recovery improves 14/16→15/16 | N02_mix0.25 remains missed. Not all weak components are guaranteed to be recovered, and absence of a candidate does not establish source absence |
| T01 | Prompt requests risk being treated as musical ground truth | Partially addressed | Known DSP parameters provide this round's pitch/speed/mixture labels. Sections use human SALAMI annotations. Prompt attributes are not labels; manifests retain verification sources | Generated recordings' actual keys, beats and functional sections have not been manually annotated one by one. Historical labels are not fabricated |
| T02 | Too few original families and risk of related-source leakage | Partially addressed | Added 6 Lumina originals for 11 families in total, plus 16 natural recordings. Families, derivatives and mixture parents stay partitioned. After a first-version held-out failure, those cases became regressions and version two used recordings from a new task | Samples remain small and do not cover all genres. Natural recordings were previously used for section testing and are not claimed to have been unseen throughout the study |
| T03 | Missing real C2PA SDK/signature-chain validation | Deferred | None | Real valid, tampered and untrusted-chain cases and trust policy remain to be added. Simulated tests are not presented as empirical signature validation |
| T04 | Incomplete records of submitted text, generation failures and refunds | Partially addressed | Recorded this round's 12 actual submissions, 6 successes/6 failures and failed task IDs. Balance changed 39045→38985. Tested browser downloads and failure observation; unknown individual refunds remain unknown | No transaction-level billing evidence is available, so historical and current per-task refunds remain unknown. Account settings were not changed to investigate billing |

Deterministic fixes, calibration on limited samples and statistical validation remain distinct; they must
not all be described as eliminated risks. See the [audio calibration record](AUDIO_CALIBRATION_2026-10-20.md)
for this round's measurements, abstentions, first-version failure and remaining issues, and the
[scoring remediation record](SCORING_REMEDIATION_2026-10-20.md) for scoring changes.
S04 and T03 remain unchanged in this round.

## Interface and Compatibility

- librosa, music_semantics and audfprint are now adapter version 1.2; the audio policy is version 2.
- phrase_order_preserved_fraction already allowed null; ambiguous repeated phrases now produce null more often.
  Clients must inspect phrase_order_status and must not treat null as 0 or as preserved order.
- candidate_phrase_order_fraction is an exploratory summary of independently selected best positions, not an acceptance result.
- tempo_ratio has changed meaning: the time-grid fit is released only when its gates pass and it agrees with the beat-aligned fit; otherwise it is null. The old BPM quotient remains as beat_tracker_tempo_ratio. All values remain diagnostic only.
- Candidate-position grouping uses interval IoU >= 0.5. Near-equivalent positions use the existing mean DTW
  cost margin of 0.01. These are candidate-reporting heuristics, not calibrated relationship-acceptance thresholds.
- source_supported_fraction / target_supported_fraction measure coverage of fingerprint-token neighborhood
  windows, not exact matched duration or confidence.
- cross_evidence_audit remains available for audit; the actual cross layer uses cross_evidence_scored_facts.
  Independent-source counts are not inferred from different document hashes.
- Complete range validation is reported item by item in relationship_range_validation. Scoring retains the
  existing range checks and sends newly detected failures into the original contradiction flow. Newly added
  successful validity/start/target-order checks do not add support votes, avoiding dilution of an existing
  reversed-source score from 0.889 to 0.967. Aggregation formulas and policy weights are unchanged; the
  existing reversed-source example retains its original score.
- Range tolerance uses the file's original sample rate and applies only to endpoint rounding. Decoding clips
  to the actual EOF. A start at or after EOF still has no valid audio content and is rejected. Missing ranges
  are not completed automatically.
- Existing examples, reports/verification.md and release.json remain historical snapshots. They do not imply
  that this uncommitted working tree has been released. Do not treat the historical order metric of 1.0 as
  the new expected result.

## Validation and Reproduction

Before remediation, the suite was rerun on HEAD 588d7e5: 193 tests passed.
The initial Desktop/V3 remediation passed 214 tests, including 21 new regressions. Two existing phrase-order
assertions were changed to validate ambiguity and candidate-value semantics; a separate test demonstrates
that unique reordered phrases are located out of order. Real audio backends were enabled, with no relevant
dependency checks skipped. The existing isolated Python 3.13 environment was used, with mir_eval 0.8.2
added as a structure-benchmark dependency. No C2PA SDK was installed.

```sh
python -m pip install -r requirements.txt -r requirements-audio.txt -r requirements-benchmark.txt
REQUIRE_AUDIO_BACKENDS=1 python -B -m unittest discover -s tests -v
python -B benchmarks/audio_dataset.py /path/to/manifest.json
```

New regressions cover invalid ranges on both ends, zero length and single-sample tolerance, input overflow,
a reversed target in an actual request, recognition of generation-record by conditional rules, failure
when loading an incorrect type, correct/incorrect-copy auditing, source structure invariant across targets,
repeated-phrase ambiguity, unambiguous rearrangement, extreme DTW compression, speed candidates under a
known time mapping, fingerprint-window coverage, and manifest label provenance/partition isolation.

The 214-test result above belongs to the initial remediation. Subsequent scoring-fix tests and reruns are
recorded in the separate delivery record. Passing unit tests and rerunning a few old recordings do not
replace independent test-set calibration or establish population accuracy.

The subsequent audio remediation passed the full 237-test suite with real backends enabled and zero skips.
It also includes partitioned calibration, historical-mixture regression and replay-tool reproduction checks.
See the audio calibration delivery directory for logs from the patched Desktop version and specific held-out cases.
