# Start Here: Music Evidence Chain and CSEC Integration

This directory contains an implemented baseline for assessing evidence from music production workflows. It was assembled from the two local projects supplied for this work on 2026-09-10. No code from `audio_evchain` was read, reused, or modified.

The main approach is to reuse Ben's evidence graph and execution framework, connect the music parsers and checks, and expose a runnable interface for CSEC.

The current local branch is `feature/dynamic-weighting-with-cap`, based on commit
`c6d36fd`, with optional report-only audfprint, SpectroMap and librosa diagnostics
added on 18 October 2026. These diagnostics do not change any score, weight,
confidence, withholding rule or cap. LLM integration is outside this update.

The runnable evaluator is in [`program/`](program/). Unless stated otherwise, file locations and commands below are relative to that directory.

## Team responsibilities

| Team | Core responsibility | Important current boundary |
|---|---|---|
| Ben / library team | Native JSON, evidence nodes and relationships, type registration, analysis-pass execution, and score data containers | A framework alone does not provide music analysis or four-dimension assessment algorithms |
| Music team | Music types, file parsing, cross-evidence checks, domain-level assessment meaning, and the runnable target | Parsers and candidate four-dimension assessments are integrated; real standards-based fixtures and customer policy confirmation are still required |
| CSEC team | Test fixtures, target execution, result recording, and red-team assessment | CSEC needs a reproducible entry point but does not define acceptance or rejection rules for the music team |

## Recommended reading order

1. [CSEC handoff](program/docs/03_CSEC_HANDOFF.md): integration instructions and supported boundaries.
2. [Physical diagnostic policy](program/docs/PHYSICAL_SIMILARITY.md): disabled legacy signal checks and the separate non-scoring boundary.
3. [Audio diagnostics](program/docs/AUDIO_DIAGNOSTICS.md): optional dependencies, execution, providers and output fields.
4. [Sheet formats](program/docs/SHEET_FORMATS.md): supported CompSheet and CueSheet formats.
5. [Future work](program/docs/NEXT_TASKS.md): consolidated P0-P3 roadmap and unresolved legacy items.
6. [Verification results](program/reports/verification.md): historical core verification and remaining unverified areas.
7. [Audio integration verification](program/reports/audio-diagnostics-verification.json): 189 passing tests and core-result invariance on the repeated-excerpt example.

## Current implementation

- Includes a pinned copy of the library source reviewed for this release, with fixes for audio attributes, binary-file binding, and dictionary attribute checks.
- Registers music audio, MIDI, DAW, RIN, ERN, C2PA, and text evidence types, plus constrained relationship types.
- Uses one streaming RIFF/WAVE reader for structural inspection, metadata parsing, and bounded sample decoding. It supports integer PCM (8/16/24/32-bit), IEEE float (32/64-bit), and integer/float `WAVE_FORMAT_EXTENSIBLE` without loading the whole file.
- Provides a bounded MIDI parser; the RIN, ERN, C2PA, sound report (`text/sound-report`) and AAF (`project/aaf`) parsers are connected to the production execution path.
- The MIDI parser extracts channel and meta events, notes, tempo, time signatures, controllers, and duration in seconds when it can be calculated.
- CompSheet and CueSheet parsers support strict JSON, CSV, delimited text, and PDF text extraction. See [`program/docs/SHEET_FORMATS.md`](program/docs/SHEET_FORMATS.md).
- Runs file, relationship, and cross-evidence analysis through the actual `EvidenceChain` and `Pipeline` implementations.
- `WavMetadataPass` emits standardised technical checks once per audio artefact. `BoundFileIntegrityPass` consumes those cached checks instead of repeating the same comparisons.
- A non-final WAV parser failure is isolated to that node: the program emits `WAV_PARSER_FAILED`, marks only dependent checks unavailable, and continues with independent evidence. An unsupported final WAV still returns `unsupported`.
- `RelationshipIntegrityPass` is a registered libevchain `EvidencePass`. It compares only submitter-declared edge parameters with cached source and target observations and replaces the former diagnostic-only `MusicRelationshipPass`.
- The core assessment does not execute the legacy `PhysicalSimilarityPass`, exact reconstruction, waveform comparison, landmark/DTW matching, source-contribution analysis, residual modelling, master continuity, or excerpt search.
- Keeps one compatibility placeholder per retained relationship. Every placeholder has `comparison_mode: disabled`, `status: unavailable`, `reason_code: disabled_by_policy`, `scoring_eligible: false`, and `integrity_contribution: null`.
- The disabled legacy layer emits no physical-similarity findings. The separate optional diagnostic runner appends `AUDIO_*` findings without affecting integrity; deprecated signal code remains isolated.
- With `--audio-analysis`, audfprint and SpectroMap report candidate content offsets and supported windows, while librosa reports feature correspondences, candidate boundaries and repetition. Their outputs are observations, not independent votes or proof of historical provenance.
- Deprecated sample and proxy helpers remain directly testable. The core assessment does not decode signal content for comparison; optional diagnostic workers read independently verified WAV snapshots.
- Objects remain limited to 512 MiB each and 1 GiB per request, and the caller's wall-clock timeout has no evaluator-side 60-second cap.
- Separates submitter-declared technical properties from measured file properties and reports inconsistencies.
- Requires `workflow_id`, `modifiers`, and submitter declarations in `submission.json`, and loads policies for 16 workflow types.
- `WorkflowCompletenessPass` reports the state of each required, conditional, and optional expectation.
- Reports integrity, C2PA attestation, and AI disclosure independently. The top-level C2PA axis is scoped to the final artefact; supporting-node manifests are reported separately and cannot raise the final score. AI disclosure measures declaration coverage; it does not detect AI-generated content.
- Separates file integrity, structural validity, declaration consistency, CrossEvidence, cryptographic attestation, and a policy-disabled physical diagnostic layer. The physical layer, structural validity, and C2PA do not inflate integrity.
- Provides CSEC request conversion, a standard response contract, timeout control, and explicit `unsupported` and `error` states.
- Applies XML size limits and rejects DTD/entity declarations. RIN 2.1 and ERN 4.3 are validated offline with pinned official XSD files, while XSD status remains separate from best-effort field extraction.
- Binds available RIN `FileReference` and ERN `DeliveryFile` SHA-256 values to recordings/components. Cross-evidence checks cover MIDI, ERN, C2PA ingredients, and AI declaration consistency. Embedded and standalone C2PA manifests are normalised to asset-bound records before their ingredients are checked against the graph.
- Invalid free-form comparison values, such as a textual BPM or non-string ISRC, make only that comparison unavailable; they do not turn the complete request into an execution error.
- Core evaluation hashes each submitted object once before direct binding and caches the source revision once per process. Optional diagnostics separately rehash temporary snapshots to confirm they still correspond to the core-bound bytes.
- Includes successful, hash-error, and unsupported-type examples, and has been run against 11 public CSEC cases.

The four dimensions respect separate evidence boundaries:

- `completeness` assesses applicable expectations for the selected workflow;
- `integrity` assesses consistency across files, declared parameters, relationship parameters, and available cross-evidence;
- `attestation_strength` assesses the available C2PA reading and validation level; and
- `ai_disclosure` retains its wire key for compatibility and is displayed as
  **Creation-method disclosure coverage**. The structured
  `ai_disclosure_coverage` record reports declared/assessed/excluded counts,
  `content_ai_detection_performed: false`, and no AI probability. Complete
  "recorded" and complete "ai-generated" declarations both give coverage 1.0;
  neither verifies whether those declarations are true.

A `null` value means not assessed and must not be interpreted as zero. The system intentionally does not produce an overall score, an acceptance/rejection decision, or a human-versus-AI creator classification. Those omissions are design boundaries, not unfinished functionality.

An unknown non-final `artefact_type` or unknown `relationship_type` is
quarantined instead of aborting the whole evaluation. Known files and
relationships are still checked, while `coverage`, `unknown_inputs`, and
detailed findings report what was omitted. The program does not infer, correct,
or map an unknown value. Every otherwise available axis is downgraded to
`partial` when coverage is incomplete. An unknown final artefact type remains
`unsupported`, and structurally unsafe input such as a dangling hash, duplicate
node, malformed request, or unrelated supported node remains an `error`.
A registered relationship whose endpoint roles are incompatible is isolated
and reported without being applied to the retained graph.

A known non-final WAV whose encoding is unsupported or malformed follows the
same isolation principle at parser level. Its graph node and declaration remain
visible, but WAV-dependent metadata checks are unavailable; the physical
diagnostic placeholder remains policy-disabled for every relationship.
Independent files and evidence continue to be assessed. The final artefact is
strict: if its WAV cannot be parsed by the supported WAV implementation, the
request returns `unsupported` or `error` as appropriate.

Input errors are reported, never corrected. The final artefact is checked
first: if its hash, objects mapping, path, size, or file content cannot be
confirmed, the request stops and reports only the final artefact's problem
(`error`; `unsupported` for the size limit or an unsupported audio container).
A problem with a non-final artefact or a relationship isolates only what
depends on it: an upper-case hash or extension (`INVALID_HASH_FORMAT`,
`INVALID_OBJECT_PATH`), an unknown or misspelled field (`UNKNOWN_FIELD`, with a
suggested spelling), a SHA-256 mismatch (`OBJECT_HASH_MISMATCH`), an object
beyond the size limits (`OBJECT_SKIPPED_SIZE_LIMIT`), or a path outside the
bundle (`OBJECT_PATH_INVALID`). Dependent checks become `unavailable`, workflow
expectations that rely on the node become `not_assessed` rather than missing,
and a closing `SCORE_AFFECTED_BY_INPUT_ERRORS` finding marks every otherwise
available axis `partial`. A misspelled relationship field disables that
relationship's deterministic checks instead of being ignored, so a typo cannot turn
an honest declaration into a contradiction. File content, not the extension,
decides the audio format: AIFF, FLAC, Ogg or MP3 data in a `.wav` file is
reported as that format. If a non-final file changes while it is being
evaluated, the request is evaluated once more with that file isolated
(`OBJECT_CHANGED_DURING_EVALUATION`); a second change, or a change to the final
artefact, returns `error`.

The program does not infer relationships or fill in missing relationship
parameters. It evaluates only graph edges and transformation parameters supplied
in the submission. Missing evidence lowers assessment availability and
confidence; it is not a contradiction and does not directly lower the integrity
value. The legacy signal layer is disabled for every relationship because its
narrow assumptions can produce misleading results for legitimate production
material. No range, offset, gain, channel matrix, contribution, or content
similarity is estimated by the core assessment. When explicitly enabled, the
separate diagnostic workers may estimate candidate offsets and feature
correspondences; these observations never replace submitted parameters.

Each retained relationship remains visible through a compatibility placeholder:

```json
{
  "relationship_type": "edited_from",
  "comparison_mode": "disabled",
  "status": "unavailable",
  "integrity_contribution": null,
  "scoring_eligible": false,
  "analysis_complete": false,
  "threshold_policy": null,
  "content_match_is_provenance": false,
  "reason_code": "disabled_by_policy"
}
```

This placeholder reports policy state, not an attempted analysis failure. It
does not emit a physical finding and cannot change integrity. Optional audio fingerprinting now uses separate report-only providers, enabled
explicitly with `--audio-analysis` or `--audio-analysis-policy`.

## Optional audio diagnostics

From the repository root:

```bash
cd program
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-audio.txt
python3 -B make_audio_diagnostic_example.py
python3 -B run.py --native examples/audio-diagnostics/request.json \
  --root examples/audio-diagnostics --audio-analysis \
  --output examples/audio-diagnostics/response.json
```

Use the existing virtual environment where available. `requirements-audio-lock.txt`
records the tested environment. The vendored audfprint extractor consumes decoded
arrays, so this integration does not require FFmpeg. Dependency origins and
licences are recorded in `music_target/audio_diagnostics/vendor/UPSTREAM.json`.

| Provider | Observation |
|---|---|
| audfprint | Landmark fingerprints, candidate time offsets and supported windows, including repeated-source candidates |
| SpectroMap | Spectral peaks detected in fixed 4-second windows, so density does not depend on file length; exact peak-pair comparison and offset voting |
| librosa | Chroma, MFCC and energy features; candidate boundaries, repetitions and cross-file correspondences |

The parent process first retains the existing core scoring response, then runs
isolated diagnostic workers on retained, declared audio-to-audio edges. It does
not search undeclared files or create graph relationships. CompSheet comparison
requires a matching selection and complete declared ranges. Only the currently
supported WAV encodings are accepted.

Detailed results are object arrays in `assessment_checks.audio_fingerprint`,
`assessment_checks.audio_structure` and `assessment_checks.audio_diagnostic_summary`.
Findings use the `AUDIO_*` prefix and link to details by `diagnostic_id`. Each
observation has `scoring_eligible: false`, `integrity_contribution: null` and
`content_match_is_provenance: false`.

Audfprint and music_semantics now have bounded experimental support/abstention gates
(see `program/docs/AUDIO_CALIBRATION_2026-10-20.md`); the other adapters and local
correspondences remain exploratory. Completed observations stay
`inconclusive`, even when profile support exists; analyses that cannot run are
`unavailable`. Missing dependencies, invalid local policies, provider failures,
silence, changed inputs and exhausted caller time do not alter the completed
core result. A crashed provider's diagnostic includes the tail of its stderr. The synthetic example yields fingerprint candidates near source
2 and 10 seconds for the same four-second excerpt, demonstrating ambiguity
rather than a unique historical source.

The proposed development caps of 30 seconds per diagnostic run, 10 seconds per
provider, 16 relations, 600 seconds per file, 64 MiB active PCM and 1500 feature
frames were not added. Existing file validation and the caller's
`options.timeout_seconds` remain active. Diagnostics use only the remaining
caller time and do not silently truncate a file. A custom local parameter
policy is enabled with `--audio-analysis-policy policies/audio_analysis_policy.json`.

## Integrity weighting and cap

The active profile is `policies/scoring_profile.json`. Integrity combines three
available score-eligible layers: `file_integrity`, `declaration_consistency` and
`cross_evidence_support`, each with default weight 1.0. The default withholding
rule (`default-v4`) requires at least two populated layers and an assessed
cross-evidence fact from an allowed tier (`signed_attestation` or
`recorder_metadata`). An ordinary contradiction never releases a number; it is
reported and sets `axes.integrity.status` and `integrity_decision.status` to
`contradicted`, and without qualifying support Integrity stays null. A severe
contradiction is computed by the evaluator and needs no independent support, so
it releases `min(weighted mean, 0.25)` with `release_reason`
`severe_contradiction_cap`.
Confidence is `0.8 * populated_layers / 3`; it is not a probability of authorship.

### Evidence tiers

Every cross-evidence check records an `evidence_tier`:

| Tier | Source | Qualifies release with the required layer coverage? |
|---|---|---|
| `signed_attestation` | A C2PA manifest whose signature validated (`Valid`/`Trusted`) | Yes |
| `recorder_metadata` | Submitted documents compared with BWF `bext`/iXML fields observed in a hash-bound WAV; their writer is not authenticated | Yes |
| `submitter_document` | RIN, ERN, CompSheet, CueSheet, MIDI, DAW sessions, AAF, sound reports and unverified C2PA, compared with the graph, declarations or measured file properties | No |

Submitter documents are declarations in another format: the submitter could
export an AAF and build the graph from it, so agreement between the two proves
nothing. They still contribute to the `cross_evidence_support` value and still
reveal contradictions. The policy permits metadata consistency to qualify
release even though embedded fields can be rewritten. Every bound WAV with
BWF/iXML metadata receives `RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED`, an
informational finding with `scoring_eligible: false` and no Integrity
contribution. It neither lowers nor raises the score; the metadata consistency
checks still score and may release the value. For example:

- **Sound report vs recorder:** the sound report says `2T01.WAV` (18 Dec)
  starts at `08:49:43:00`, and the take's `bext` time reference says the same.
  This is checked by `sound_report_start_timecode_matches_recorder`.
- **AAF vs recorder:** an AAF edit uses 4.6 s of tape `24Y12M17` from 14:04:59,
  and `2T05.WAV`'s recorded span, 14:04:31 + 43 s, contains it. This is checked
  by `aaf_source_range_within_recorded_timecode`.

The cross layer scores `cross_evidence_scored_facts`, one vote per rule and
subject, not per document. A failed observation keeps its fact failed even
when many agreeing reports are supplied; repeating a failed report cannot add
another penalty either. Local cue/selection/mob labels do not add votes.
Multiple range claims about the same recording and property must all pass.
Raw rows remain in `cross_evidence`; `evidence_tier_counts` counts those raw
rows, while `check_count`/`scored_fact_count` counts eligible facts. Document
hash counts are not independent-witness counts. The decision's release reason
and contradiction status are separate from the consistency number.

The cross layer reports `evidence_tier_counts`. A profile that omits
`withholding_rule.substantive_support_tiers` keeps the original rule, in which
any assessed cross-evidence check counts. Audio-content agreement is not a
tier: diagnostics remain report-only; bounded threshold calibration does not authorize scoring integration.
The canonical profile key is now `require_substantive_support`. The old
`require_substantive_support_or_contradiction` spelling remains readable as an
alias, but no longer enables a contradiction-only bypass; specifying both is
an error. Weights, confidence formulas and the 0.25 severe ceiling are unchanged.

On the Project Rock dataset the three episode chains (takes, both sound
reports and the episode AAF) produced 0 recorder contradictions and
Integrity 1.0. A copy of the sound report with one start timecode shifted by
2 s failed exactly that one check.

An assessed failure of a rule listed in `severe_contradiction_rules` caps an
otherwise available weighted result with `min(weighted_mean, 0.25)`. The cap
cannot raise a lower value, and optional audio matches cannot cancel it. Final
input problems that stop evaluation still return an execution error rather
than a capped score. When the cap applies, the Integrity reasoning names the
triggering rule(s) and the uncapped weighted mean, and
`assessment_checks.severe_contradiction_cap` records `cap`, `triggered_rules`,
`weighted_mean`, `value` and `value_lowered`. The record is absent when no
severe rule failed. Diagnostic threshold calibration is separate from weight
calibration; this integration does not add a fourth scored layer.

### Score explanations

Every successful response carries `assessment_checks.score_explanations`, one
entry per axis. The response schema is a shared contract, so it is not
extended. The Integrity entry lists:

- each contributing layer's value and weight;
- the weighted mean, and whether a cap applied (`cap_applied`, `cap`) or the
  value was withheld (`withheld`);
- the evidence-tier counts;
- every failed check as a sentence with the compared values.

For example: *"Sound report start timecode for 2T01.WAV is 08:49:45:00, but
the recorder wrote 08:49:43:00 into the file (2.00 s apart)."* The same
sentences are appended to the Integrity `reasoning` after "Contradictions:".

A timecode mismatch is an ordinary contradiction, not a severe one. Honest
productions produce mismatches too: hand-typed reports, timecode drift, files
re-exported with rewritten `bext`. So it lowers the cross-evidence layer and
is named explicitly; it does not cap the score.

The completeness entry also names any evidence the selected workflow does not
describe. Such artefacts get one info finding,
`WORKFLOW_EVIDENCE_OUTSIDE_POLICY`, with `status: "inconclusive"` per
artefact, and they neither raise nor lower completeness.
`include_reasoning: false` omits the explanations.

## Run the native example

From the repository root, enter the evaluator directory and use Python 3.11 or later:

```bash
cd program
python3 -m pip install -r requirements.txt
# Install the optional dependency only when C2PA SDK verification is required:
python3 -m pip install -r requirements-c2pa.txt
python3 -B run.py --native examples/valid-generated/submission.json --root examples/valid-generated
```

The example processes four audio nodes in sequence: `raw-track -> stem -> mix -> master`. The expected result contains `execution_status: succeeded`, four WAV observations, three `RELATIONSHIP_INTEGRITY_OBSERVATION` findings, and three policy-disabled physical placeholders. This is a software-generated teaching fixture, not verified evidence of human-recorded creation.

Exercise two failure boundaries:

```bash
python3 -B run.py --native examples/invalid-hash/request.json --root examples/invalid-hash
python3 -B run.py --request examples/unsupported-png/request.json --root examples/unsupported-png
```

The first command should return `error` for a hash mismatch. The second should return `unsupported`. A successfully returned JSON response uses process exit code 0; inspect `execution_status` in the JSON for the actual evaluation outcome.

## CSEC entry point

```bash
python3 -B run.py --request examples/csec-wav/request.json --root examples/csec-wav
```

This example uses the supplied CSEC candidate request format. It contains one WAV file and does not invent stem, demo, or other roles. A CSEC bundle can also be processed directly:

```bash
python3 -B run.py --bundle /path/to/EVAL-case/bundle.json --root /path/to/EVAL-case
```

## Verification

```bash
python3 -B -m unittest discover -s tests -v
python3 -B check_public.py /path/to/public-bundles
```

The second command writes results to `reports/`. Both commands operate locally and do not transmit data.
The 18 October 2026 verification passed 144 tests with no failures or skips:
124 core tests and 20 new diagnostic tests. It exercised the real installed
providers and verified unchanged axes and original findings when diagnostics
were enabled. The synthetic tests are engineering checks, not real-session
accuracy calibration. Run the full dependency-required suite with
`REQUIRE_AUDIO_BACKENDS=1 python3 -B -m unittest discover -s tests -v`.
Core tests include coverage for non-final
input isolation and final-first stopping, unknown-field suggestions, audio
container detection by content, byte-capped decoding, deadline degradation,
non-final WAV isolation, integer/float extensible PCM, float32/64 decoding, non-finite
sample rejection, RIFF warnings, field-level unavailability, final-scoped C2PA,
standalone C2PA ingredient checks, hash caching, ndarray PCM decoding, 24-bit
sign extension, legacy signal-helper isolation, and an explicit assertion that
the policy-disabled `PhysicalSimilarityPass` is never invoked by assessment.

## Directory overview

The locations below are relative to `program/`.

| Location | Purpose |
|---|---|
| `vendor/libevchain/` | Local copy of Ben's source with three reviewable fixes |
| `music_target/domain.py` | Music nodes, relationships, and permitted endpoint roles |
| `music_target/wav_io.py` | Streaming RIFF/WAVE inspection and vectorised integer/float sample decoding |
| `music_target/wav_parser.py` | Local copy of the supplied WAV parser |
| `music_target/engine.py` | File binding, `WavMetadataPass`, `RelationshipIntegrityPass`, library pipeline, and assessment-stage orchestration |
| `music_target/workflow_policy.py` | Safe policy loading, condition evaluation, and per-expectation workflow completeness |
| `music_target/assessment_passes.py` | Cached file and relationship result aggregation, CrossEvidence, C2PA, and AI disclosure assessment logic |
| `music_target/audio_derivation.py` | Deprecated signal helpers and exact-reconstruction code retained for later cleanup; not executed by assessment |
| `music_target/physical_audio_passes.py` | Deprecated physical-similarity implementation retained for later cleanup; not imported by assessment |
| `music_target/midi_parser.py` | Size- and structure-bounded Standard MIDI File parser |
| `music_target/sheet_parsers.py` | CompSheet/CueSheet JSON, CSV, TXT, and PDF parsers plus field normalisation |
| `music_target/recorder_metadata.py` | Recorder-written BWF `bext`/iXML fields (tape, file name, scene/take, tracks, start timecode) from a bound WAV |
| `music_target/production_documents.py` | Sound report CSV and AAF (pyaaf2) parsers, and their checks against recorder metadata |
| `music_target/evidence_tiers.py` | Evidence tiers for cross-evidence checks and their counts |
| `music_target/score_explanations.py` | Per-axis score explanations and plain-language statements of failed checks |
| `music_target/daw_session.py` | Ableton Live session parser and its checks against the graph and MIDI |
| `music_target/audio_diagnostics/providers/music_semantics.py` | Report-only key, tempo, chord and phrase-form comparison across versions |
| `benchmarks/` | Public music dataset benchmark (Slakh/BabySlakh, MUSDB18-HQ) with true and control relationships |
| `music_target/contract.py` | CSEC/native format adaptation, input validation, and version identification |
| `policies/workflow_evidence_policy.json` | Candidate required, conditional, and optional evidence policy for 16 workflows |
| `music_target/audio_diagnostics/` | Report-only task selection, verified snapshots, isolated workers, three provider adapters and result assembly |
| `policies/audio_analysis_policy.json` | Local provider and candidate-parameter configuration; no scoring controls or proposed development caps |
| `schemas/audio-diagnostic.schema.json` | Diagnostic result validation and fixed non-scoring fields |
| `requirements-audio.txt`, `requirements-audio-lock.txt` | Optional audio dependencies and the tested environment |
| `make_audio_diagnostic_example.py`, `examples/audio-diagnostics/` | Reproducible repeated-excerpt example, request and core/audio reports |
| `tests/test_audio_diagnostics.py` | Twenty provider, protocol and score-isolation tests |
| `reports/audio-diagnostics-verification.json` | Current 144-test verification and real-provider example results |
| `docs/AUDIO_DIAGNOSTICS.md` | Detailed audio setup, usage, output and interpretation |
| `music_target/scoring_profile.py`, `policies/scoring_profile.json` | Validated three-layer Integrity weights, tier-aware withholding, confidence and severe-contradiction cap |
| `run.py` | CSEC/local execution and explicit parent-side audio diagnostic orchestration |
| `schemas/` | Original CSEC candidate schemas and local music schemas |
| `schemas/ddex/` | Pinned RIN 2.1 and ERN 4.3 XSD files, source hashes, and licence reminder |
| `legacy_parsers/` | RIN, ERN, and C2PA parsers used by the production path, plus XSD validation and XML helpers |
| `examples/` | Runnable requests, fixture files, and generated responses |
| `reports/source_inventory.json` | Source files reviewed for this release and their SHA-256 values |

## Decisions still required from the team

1. Confirm the CSEC technical contact and whether the engagement uses full integration or schema/sample exchange only.
2. Resolve whether stems and raw takes are universally required, as one PDF suggests, or conditional by workflow, as the profile specifies.
3. Obtain genuine successful-creation fixtures; the generated teaching fixture is not validated human-creation ground truth.
4. Confirm that the internal weighting and interpretation of integrity checks match customer expectations. The project keeps the four dimensions separate and does not add a decision policy.

These decisions do not block the local executable baseline, but the directory must not yet be described as a complete music-authenticity assessment system.
