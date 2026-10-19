# Physical Similarity Diagnostic Policy

Status: policy-disabled in v0.7.0.

The active assessment path does not import or invoke
`PhysicalSimilarityPass`. Exact reconstruction, waveform and feature
comparison, source contribution, residual modelling, master continuity, and
excerpt search are not executed. Their narrow signal assumptions produced
misleading results for legitimate high-frequency and processed material, so the
pass is disabled rather than merely removed from the numeric denominator.

For compatibility, every retained relationship receives an `unavailable`
placeholder with `comparison_mode: disabled`,
`reason_code: disabled_by_policy`, `scoring_eligible: false`,
`integrity_contribution: null`, `analysis_complete: false`, and
`content_match_is_provenance: false`. The summary reports `enabled: false`, a
null value, and zero scored relationships. No physical-similarity or
signal-driven manual-review finding is emitted.

The implementation remains in `music_target/physical_audio_passes.py` and
`music_target/audio_derivation.py` as deprecated code for later cleanup. The
remaining sections preserve the historical v0.6 implementation details for
auditability only. Their modes, thresholds, scoring rules, and findings are not
active customer policy.

## Historical v0.6 implementation (inactive)

The text below records the former v1 implementation so that its behaviour can
be reviewed. It is not executed by v0.7.0.

## 1. Purpose and evidence boundary

The framework asks a limited question:

> Are the submitted audio signals physically consistent with the declared
> relationship, clearly inconsistent with it under a supported comparison, or
> insufficient to decide?

It does not reconstruct an exact production session. It also does not prove
that one file historically caused another, or establish authorship, ownership,
creator identity, or the order in which files were made. A copied, re-rendered,
or deliberately constructed signal can still be similar. For that reason every
result includes:

```json
"content_match_is_provenance": false
```

The system evaluates only relationships and parameters supplied in the
submission. It does not infer missing graph edges, repair declarations, or
write estimated offsets, gains, or channel matrices back into the evidence
graph.

## 2. One framework, relationship-specific modes

Every declared relationship produces one primary item in
`assessment_checks.physical_similarity`. The relationship type selects a
comparison mode, but all modes use the same public states, output contract,
absence-safe behaviour, and scoring policy.

| Relationship type | Comparison mode | Physical question |
|---|---|---|
| `derived_from` | `pairwise_continuity` | Do bounded waveform and time-shape features support content continuity? |
| `edited_from` | `pairwise_continuity` | Do the declared or estimated source and target regions contain consistent content? |
| `excerpted_from` | `excerpt_search` | Can the submitted excerpt be located unambiguously within the source? |
| `comped_from` | `selection_timeline` | Do the CompSheet selections correspond to the declared source and target ranges? |
| `stemmed_from` | `multi_source_contribution` | Does this declared source make a measurable, distinguishable contribution to the target stem? |
| `mixed_from` | `multi_source_contribution` | Does this declared source make a measurable, distinguishable contribution to the target mix? |
| `mastered_from` | `master_continuity` | Is there processing-tolerant signal continuity between the pre-master and master? |
| `rendered_from`, `input_to` | `unsupported_relationship` | No reliable acoustic meaning is assumed; the physical result is `unavailable`. |

A relationship with a missing source, an invalid declaration, a non-audio
endpoint, or unsupported semantics is still represented by one primary result.
It is not silently omitted and is not converted into a contradiction.

## 3. Common bounded signal measurements

The comparison modes reuse the following measurements. They are evidence
features, not separate relationship verdicts.

| Measurement | Current implementation | What it contributes |
|---|---|---|
| Waveform correlation | Absolute normalised correlation over the compared regions | Strong evidence for directly related or lightly changed signals; sensitive to larger processing changes |
| Short-time landmarks | Four standardised frame features: log RMS energy, difference RMS relative to energy, zero-crossing rate, and peak-to-RMS ratio | Gain-tolerant local energy and shape continuity |
| DTW similarity | Sakoe-Chiba-banded dynamic time warping over landmark sequences; long sequences are uniformly reduced to at most about 500 points | Limited tolerance for small timing differences while keeping work bounded |
| Envelope correlation | Correlation of the landmark energy component | Slow energy-shape continuity |
| FFT candidate search | Up to 12 normalised cross-correlation candidates across the complete low-rate proxy, followed by landmark refinement | Bounded offset and excerpt-location estimation |
| Leave-one-out residual | Difference between a joint source model and the model after removing one declared source | Whether a source has measurable unique contribution |
| Joint residual model | Diagnostic linear source-channel fit, normalised residual, and predicted/target correlation | Whether the declared sources jointly explain the target well enough to support an adverse per-source conclusion |

Alignment uses a 200 Hz mono representation and short-time features at 25 Hz.
The contribution model uses 400 Hz, and the joint residual model uses 1000 Hz.
These are deliberately low-rate diagnostic representations. They preserve
broad timing, energy, and waveform structure but do not validate high-frequency
detail, perceptual quality, or a complete mix recipe.

The complete cached analysis proxy is searched rather than only the opening
portion of a file. For common source rates it is 2000 Hz for 48 kHz input and
2100 Hz for 44.1 kHz input. Anti-alias filtering is applied before integer or
non-integer rate reduction.

Alignment refines each FFT candidate with
`0.65 * landmark_similarity + 0.35 * max(0, waveform_correlation)`. This is a
location heuristic, not the final relationship score.

When all four time fields are declared—`source_start_seconds`,
`source_end_seconds`, `target_start_seconds`, and `target_end_seconds`—the
comparison uses those ranges and reports `parameter_source:
"submitter_declared"`. Otherwise a supported mode may estimate an alignment
and reports `parameter_source: "signal_estimated_diagnostic"`. An estimated
parameter supports only the diagnostic comparison; it does not validate or
change the submitted relationship parameters.

Automatic alignment is marked ambiguous when a competing location has
similarity at least 0.70 and is within 0.03 of the best candidate. Ambiguous
location evidence cannot produce a search-based contradiction.

## 4. Comparison-mode behaviour

### 4.1 Pairwise continuity

Used by `derived_from` and `edited_from`.

The mode combines four measurements:

```text
robust_similarity =
    0.20 * waveform_correlation
  + 0.30 * landmark_similarity
  + 0.30 * dtw_similarity
  + 0.20 * envelope_correlation
```

The internal v1 endpoints are:

- `robust_similarity >= 0.72`: supports `corroborated`;
- `robust_similarity <= 0.25`: an internal content conflict; and
- values between the endpoints: `inconclusive`.

The public normalisation is intentionally more conservative than the internal
measurement. A generic `derived_from` claim may include arbitrary undocumented
processing, so a low similarity does not become public `contradicted`; it is
`inconclusive`. An `edited_from` result can be `contradicted` only when all four
ranges were supplied, analysis completed, and the transformation description
does not declare complex processing such as compression, limiting, mastering,
time stretching, pitch shifting, distortion, saturation, reverb, or another
nonlinear operation.

This mode tolerates gain and limited processing through its standardised
features. It does not recreate EQ, automation, effects, edits, or plugin state.

### 4.2 Excerpt search

Used by `excerpted_from`.

The shorter signal is searched against the longer signal across the complete
bounded proxy. Candidate locations are ranked using waveform and landmark
evidence. The selected regions are then scored as:

```text
robust_similarity =
    0.40 * waveform_correlation
  + 0.30 * landmark_similarity
  + 0.30 * dtw_similarity
```

The v1 decision rules are:

- waveform correlation at least 0.97, or robust similarity at least 0.70:
  `corroborated`;
- robust similarity at most 0.25 after a complete, unambiguous search:
  `contradicted`;
- repeated or similarly supported locations: `inconclusive`; and
- an incomplete search, missing analysable signal, or exhausted budget:
  `unavailable`.

For example, a two-second drum loop that appears at several bars may be
physically consistent with the source but cannot establish which occurrence
was used. The result is therefore `inconclusive`, not a guessed location and
not a contradiction.

### 4.3 Selection timeline

Used by `comped_from` and requires usable CompSheet selections. Each selection
compares the exact declared source and target ranges using:

```text
robust_similarity =
    0.35 * waveform_correlation
  + 0.30 * landmark_similarity
  + 0.35 * dtw_similarity
```

For each selection, waveform correlation at least 0.98 or robust similarity at
least 0.70 is corroborating; robust similarity at most 0.25 is contradictory;
the middle range is inconclusive. The relationship still produces only one
primary result:

1. any clearly contradictory selection makes the relationship
   `contradicted`;
2. otherwise, any corroborated selection makes it `corroborated`;
3. otherwise, an analysed but ambiguous selection makes it `inconclusive`; and
4. otherwise it is `unavailable`.

The per-selection outcomes are retained under `selection_results` so the
primary result remains auditable. The mode validates declared segment
continuity; it does not reconstruct crossfades, comp-lane state, plugin chains,
or the complete DAW edit history.

### 4.4 Multi-source contribution

Used by `stemmed_from` and `mixed_from`, with at most 16 declared sources per
target. It has two safeguards.

Each source is first placed on the target timeline from the four declared time
fields, or from a diagnostic estimated alignment when those fields are absent.

First, a low-rate ridge-regularised linear model estimates all declared source
gains. Each source is removed in turn. A source is internally corroborating
when:

- removing it increases normalised residual by at least 0.02;
- its absolute estimated gain is at least 0.005; and
- its gain-stability measure is at least 0.15.

It is internally `not_supported` when the residual increase is at most 0.005
or its absolute gain is below 0.002. Values between those endpoints are
ambiguous.

Second, the framework checks a joint source-channel residual at 1000 Hz. The
joint model is internally:

- strongly matched when normalised RMSE is at most 0.05 and correlation is at
  least 0.98;
- corroborating when RMSE is at most 0.25 and correlation is at least 0.70;
- contradictory when RMSE is at least 0.65 or correlation is below 0.20; and
- otherwise inconclusive.

A declared source becomes public `contradicted` only when its unique
contribution is `not_supported` **and** the complete joint model is supported.
This guard matters: a near-zero contribution is meaningful adverse evidence
only if the other submitted sources explain the target. If the whole model is
poor, undocumented effects, missing sources, nonlinear processing, or a wrong
alignment may be responsible, so the per-source result remains
`inconclusive`.

If a source can be predicted from the other sources with correlation at least
0.995, its unique contribution cannot be attributed reliably. That edge is
`inconclusive`. Likewise, highly correlated or poorly identifiable source
channels cause the individual channel matrix to be withheld; a joint residual
result may still be reported without assigning individual channel gains.

The linear fit is a diagnostic test, not a claim that real mixing is purely
linear. Compression, limiting, EQ, reverb, automation, nonlinear effects,
side-chain processing, time-varying routing, missing buses, and undocumented
sources can all move an honest production into `inconclusive`.

### 4.5 Master continuity

Used by `mastered_from`. It reuses the processing-tolerant pairwise features;
the deprecated exact mastering-reconstruction pass is not executed.

- The normal pairwise corroboration endpoint applies.
- Waveform correlation of at least 0.95 also permits `corroborated` when other
  features changed.
- A completed internal low-similarity conflict becomes `contradicted`.
- Intermediate evidence is `inconclusive`, and a comparison that cannot run is
  `unavailable`.

This mode seeks continuity through gain and light processing. It does not prove
which mastering operations were applied and may be inconclusive after strong
EQ, compression, limiting, clipping, resampling, fades, inserted silence, or
other substantial changes. These cases are explicit calibration targets in
`NEXT_TASKS.md`.

### 4.6 Unsupported acoustic semantics

`rendered_from` and `input_to` remain valid graph relationships, but the
program does not assign them an acoustic test without a narrower definition.
Their physical result is `unavailable` with reason code
`unsupported_relationship_semantics`. Metadata or workflow claims must not be
silently converted into signal claims.

## 5. Public states and scoring

| Public state | Meaning | Integrity contribution |
|---|---|---|
| `corroborated` | Clear signal evidence supports consistency with the declared relationship | `1.0` |
| `contradicted` | A complete supported comparison clearly conflicts with the declared relationship | `0.0` |
| `inconclusive` | The comparison ran, but the evidence did not reach either reliable endpoint | `null`; excluded from the denominator |
| `unavailable` | The comparison could not run because of input, evidence, format, resource, or semantic limits | `null`; excluded from the denominator |

Internal helper passes still contain historical labels such as `matched`,
`not_supported`, and `not_applicable`. `PhysicalSimilarityPass` normalises them
to the four public states. Those legacy labels are not exposed as primary
physical-similarity statuses.

Only clear endpoints are scored. This is asymmetric by design: missing or
ambiguous evidence is not proof of tampering. It affects availability,
completeness, confidence, or manual-review findings instead of being counted as
zero.

Scoring is normalised by target:

```text
target_value = mean(scoreable relationship contributions for that target)
physical_similarity_value = mean(target_value for each assessed target)
```

For example, if Target A has four corroborated edges and one contradicted edge,
its value is `4 / 5 = 0.8`. If Target B has one corroborated edge, its value is
`1.0`. The layer value is `(0.8 + 1.0) / 2 = 0.9`. The six states in this
example belong to six different declared relationships; each individual
relationship still has exactly one primary state. Target normalisation prevents
a large mix with many declared stems from dominating the complete assessment.

The summary status and numeric value are separate. The summary status is
`contradicted` if any relationship is contradicted; otherwise it is
`corroborated` if any relationship is corroborated, then `inconclusive`, then
`unavailable`. The value is calculated only from scoreable targets.

## 6. Uniform output contract

Every primary result contains the same core fields:

```json
{
  "relationship_type": "edited_from",
  "comparison_mode": "pairwise_continuity",
  "source_hashes": ["<source-sha256>"],
  "target_hash": "<target-sha256>",
  "status": "corroborated",
  "integrity_contribution": 1.0,
  "similarity_score": 0.87,
  "parameter_source": "submitter_declared",
  "declared_ranges_used": true,
  "analysis_complete": true,
  "metrics": {
    "waveform_correlation": 0.76,
    "landmark_similarity": 0.92,
    "dtw_similarity": 0.89,
    "envelope_correlation": 0.84
  },
  "threshold_policy": "pairwise_continuity-v1",
  "content_match_is_provenance": false,
  "reason": "Independent signal features support content continuity."
}
```

`reason_code` appears when a stable machine-readable explanation is available.
Mode-specific audit fields can also appear, including `selection_results` and
`joint_model_status`. `similarity_score` may be `null` for modes whose decision
is based on contribution and residual measurements rather than one combined
similarity. Scores from different modes must not be compared as if they were
the same metric.

The final report also contains
`assessment_checks.physical_similarity_summary`, including state counts,
declared and scored relationship counts, assessed-target count, per-target
values, the layer value, and the scoring-policy description.

The framework emits these findings:

- `PHYSICAL_SIMILARITY_CONTRADICTION` for a clear conflict;
- `PHYSICAL_SIMILARITY_INCONCLUSIVE` when analysis ran but could not decide; and
- `PHYSICAL_SIMILARITY_UNAVAILABLE` when analysis could not run.

## 7. Absence, errors, and resource safety

The following conditions cannot directly lower the physical-similarity score:

- a missing source object;
- an invalid or unrecognised relationship field;
- a non-audio endpoint;
- no usable CompSheet selection;
- insufficient or silent analysable signal;
- ambiguous repeated content;
- too many contribution sources;
- a deadline or audio-work budget being exhausted; or
- an incomplete search.

They produce `unavailable` or `inconclusive`, with the reason retained in the
result. In particular, an incomplete search is never allowed to return
`contradicted`.

Current request-level guards include:

- 128 MiB maximum active decoded PCM windows;
- 256 MiB maximum cached analysis proxies;
- 20,000 weighted channel-seconds of audio work;
- 16 sources for multi-source contribution;
- 512 MiB per submitted object and 1 GiB per request; and
- the caller's wall-clock deadline.

These bounds control cost and denial-of-service risk. They also mean that
`unavailable` can describe an engineering limit rather than a problem with the
relationship itself.

## 8. Known limitations and validation requirements

The framework currently supports the program's accepted WAV encodings; it is
not a general decoder for all music formats. The low-rate features deliberately
sacrifice spectral and perceptual detail. Similarity can be forged and cannot
establish causal direction. Strong production processing can conceal genuine
continuity, while repeated loops, duplicated sources, source-separated stems,
or highly correlated tracks can create ambiguous or misleading support.

The v1 thresholds and weights are implementation-locked for reproducibility,
but they are not yet calibrated acceptance criteria. Before external claims are
made, evaluation must cover licensed real sessions and adversarial cases,
including unrelated same-tempo music, repeated loops, phase inversion,
clipping, silence, resampling, heavy mastering, nonlinear effects, decoy stems,
missing buses, and source-separated reconstructions.

Until that calibration is complete, physical similarity should be presented as
one evidence layer with explicit findings and manual-review context, not as a
standalone authenticity detector or decision policy.

## 9. Implementation references

- `music_target/physical_audio_passes.py`: comparison modes, public
  normalisation, v1 thresholds, and uniform results.
- `music_target/audio_derivation.py`: WAV proxy generation, resampling,
  resource accounting, and deprecated reconstruction code.
- `music_target/assessment_passes.py`: findings, per-target aggregation, and
  integrity-layer integration.
- `docs/SHEET_FORMATS.md`: CompSheet fields used by `selection_timeline`.
- `docs/NEXT_TASKS.md`: threshold calibration and real-fixture requirements.

## Active future boundary

The optional report-only audio providers are documented in
[`AUDIO_DIAGNOSTICS.md`](AUDIO_DIAGNOSTICS.md). They run after the core scoring
worker and do not invoke the legacy implementation described above. Installing
dependencies does not activate them; use the explicit local audio flag.

The policy for these providers is the same boundary originally recorded in
v0.7.0: a provider may be added
only as an optional, local, resource-bounded diagnostic. It must remain outside
integrity, retain `scoring_eligible: false` and
`content_match_is_provenance: false`, and treat a non-match as inconclusive
rather than proof of contradiction. Enabling a provider requires an explicit
versioned policy, dependency and licence review, and representative fixtures;
installing a library alone must never activate it.

