# Start Here: Music Evidence Chain and CSEC Integration

This directory contains an implemented baseline for assessing evidence from music production workflows. It was assembled from the two local projects supplied for this work on 2026-09-10. No code from `audio_evchain` was read, reused, or modified.

The main approach is to reuse Ben's evidence graph and execution framework, connect the music parsers and checks, and expose a runnable interface for CSEC.

## Team responsibilities

| Team | Core responsibility | Important current boundary |
|---|---|---|
| Ben / library team | Native JSON, evidence nodes and relationships, type registration, analysis-pass execution, and score data containers | A framework alone does not provide music analysis or four-dimension assessment algorithms |
| Music team | Music types, file parsing, cross-evidence checks, domain-level assessment meaning, and the runnable target | Parsers and candidate four-dimension assessments are integrated; real standards-based fixtures and customer policy confirmation are still required |
| CSEC team | Test fixtures, target execution, result recording, and red-team assessment | CSEC needs a reproducible entry point but does not define acceptance or rejection rules for the music team |

## Recommended reading order

1. [CSEC handoff](docs/03_CSEC_HANDOFF.md): integration instructions and supported boundaries.
2. [Sheet formats](docs/SHEET_FORMATS.md): supported CompSheet and CueSheet formats.
3. [Future work](docs/NEXT_TASKS.md): consolidated P0-P3 roadmap and unresolved legacy items.
4. [Verification results](reports/verification.md): executed checks and remaining unverified areas.

## Current implementation

- Includes a pinned copy of the library source reviewed for this release, with fixes for audio attributes, binary-file binding, and dictionary attribute checks.
- Registers music audio, MIDI, DAW, RIN, ERN, C2PA, and text evidence types, plus constrained relationship types.
- Uses one streaming RIFF/WAVE reader for structural inspection, metadata parsing, and bounded sample decoding. It supports integer PCM (8/16/24/32-bit), IEEE float (32/64-bit), and integer/float `WAVE_FORMAT_EXTENSIBLE` without loading the whole file.
- Provides a bounded MIDI parser; the RIN, ERN, and C2PA parsers are connected to the production execution path.
- The MIDI parser extracts channel and meta events, notes, tempo, time signatures, controllers, and duration in seconds when it can be calculated.
- CompSheet and CueSheet parsers support strict JSON, CSV, delimited text, and PDF text extraction. See [`docs/SHEET_FORMATS.md`](docs/SHEET_FORMATS.md).
- Runs file, relationship, and cross-evidence analysis through the actual `EvidenceChain` and `Pipeline` implementations.
- `WavMetadataPass` emits standardised technical checks once per audio artefact. `BoundFileIntegrityPass` consumes those cached checks instead of repeating the same comparisons.
- A non-final WAV parser failure is isolated to that node: the program emits `WAV_PARSER_FAILED`, marks only dependent checks unavailable, and continues with independent evidence. An unsupported final WAV still returns `unsupported`.
- `RelationshipIntegrityPass` is a registered libevchain `EvidencePass`. It compares only submitter-declared edge parameters with cached source and target observations and replaces the former diagnostic-only `MusicRelationshipPass`.
- Uses one `PhysicalSimilarityPass` for declared audio relationships. Each relationship selects a bounded comparison mode—pairwise continuity, excerpt search, CompSheet selection timeline, multi-source contribution, or master continuity—but every result has the same four states and report shape.
- Exact reconstruction is no longer executed or scored. Legacy reconstruction code remains isolated for later removal, but its `matched`/`not_applicable` statuses and pass-specific report keys are not exposed by the assessment path.
- WAV samples are decoded with NumPy into C-contiguous `(frames, channels)` `float32` arrays. The pinned `numpy==2.2.6` dependency supports the project's Python 3.11-3.13 range; no SciPy dependency is used. Integer PCM and IEEE-float decoding are vectorised; finite float values outside `[-1, 1]` are preserved rather than clipped.
- RIFF size, data size, partial-frame, byte-rate, and trailing-byte anomalies are reported as stable warnings where safe. Unsupported codecs remain unsupported, while float NaN/Infinity causes only dependent content checks to become unavailable and produces `WAV_SAMPLE_DATA_INVALID`.
- Each audio file also has a request-cached, complete low-rate analysis proxy. Integer-factor Kaiser-windowed sinc filtering produces 2000 Hz for 48 kHz input and 2100 Hz for 44.1 kHz input; later integer and non-integer reductions are low-pass filtered before slicing or interpolation.
- The unified pass reuses bounded waveform, landmark, DTW, envelope, contribution, residual, and excerpt-search measurements. Exploratory alignment and location checks use FFT-normalised cross-correlation across the complete proxy rather than searching only the first 60 seconds. Signal-estimated offsets, gains, and channel matrices are diagnostic only and are never written back into the evidence graph.
- Physical similarity has four public states: `corroborated`, `contradicted`, `inconclusive`, and `unavailable`. Only the first two enter integrity. Every result is labelled `content_match_is_provenance: false` because similarity cannot prove historical direction, authorship, or ownership.
- Resource control combines a 128 MiB active PCM-window bound, a 256 MiB proxy-cache bound, a weighted request work limit, and the caller's requested wall-clock timeout (`options.timeout_seconds`; the evaluator adds no 60-second cap). Objects are limited to 512 MiB each and 1 GiB per request. Audio checks stop starting new work shortly before the caller's deadline, so a slow request still returns its other results. A resource-limited check returns `unavailable` with `reason_code: resource_budget_exhausted`; an incomplete search cannot return `contradicted`.
- Audio check output records the ranges actually analysed or searched, whether search/analysis completed, and the analysis-proxy rate used.
- Separates submitter-declared technical properties from measured file properties and reports inconsistencies.
- Requires `workflow_id`, `modifiers`, and submitter declarations in `submission.json`, and loads policies for 16 workflow types.
- `WorkflowCompletenessPass` reports the state of each required, conditional, and optional expectation.
- A missing non-final object is reported as `MISSING_OBJECT` and its file-binding check is `not_assessed`; absence affects workflow completeness (or legacy declared-object availability when no workflow is selected) but is not evidence of a file-integrity contradiction.
- Reports integrity, C2PA attestation, and AI disclosure independently. The top-level C2PA axis is scoped to the final artefact; supporting-node manifests are reported separately and cannot raise the final score. AI disclosure measures declaration coverage; it does not detect AI-generated content.
- Separates file integrity, structural validity, declaration consistency, physical similarity, CrossEvidence, and cryptographic attestation. Physical relationships are averaged within each target before targets are averaged, so a target with many stems cannot dominate integrity. Structural endpoint validity and C2PA do not inflate integrity; C2PA remains in the separate attestation-strength axis.
- Provides CSEC request conversion, a standard response contract, timeout control, and explicit `unsupported` and `error` states.
- Applies XML size limits and rejects DTD/entity declarations. RIN 2.1 and ERN 4.3 are validated offline with pinned official XSD files, while XSD status remains separate from best-effort field extraction.
- Binds available RIN `FileReference` and ERN `DeliveryFile` SHA-256 values to recordings/components. Cross-evidence checks cover MIDI, ERN, C2PA ingredients, and AI declaration consistency. Embedded and standalone C2PA manifests are normalised to asset-bound records before their ingredients are checked against the graph.
- Invalid free-form comparison values, such as a textual BPM or non-string ISRC, make only that comparison unavailable; they do not turn the complete request into an execution error.
- Each submitted object is hashed once before direct binding, and the source revision is cached once per process.
- Includes successful, hash-error, and unsupported-type examples, and has been run against 11 public CSEC cases.

The four dimensions respect separate evidence boundaries:

- `completeness` assesses applicable expectations for the selected workflow;
- `integrity` assesses consistency across files, declared parameters, relationship parameters, and available cross-evidence;
- `attestation_strength` assesses the available C2PA reading and validation level; and
- `ai_disclosure` assesses explicit `creation_method` declaration coverage.

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
visible, but WAV-dependent metadata and physical-audio checks are unavailable.
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
relationship's content checks instead of being ignored, so a typo cannot turn
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
value. When physical evidence is insufficient, the relationship remains a
submitted claim and its content result is `inconclusive` (the comparison ran
but could not decide) or `unavailable` (the comparison could not run). Neither
state enters the numeric denominator.

The relationship selects the comparison mode:

| Relationship | Comparison mode | Conservative interpretation |
|---|---|---|
| `derived_from`, `edited_from` | `pairwise_continuity` | Gain- and light-processing-tolerant waveform and feature comparison; arbitrary generic derivation differences remain inconclusive |
| `excerpted_from` | `excerpt_search` | Bounded full-proxy search for a clear source segment; ambiguous repeated locations are inconclusive |
| `comped_from` | `selection_timeline` | Parsed CompSheet selections are compared and combined into one primary result per relationship |
| `stemmed_from`, `mixed_from` | `multi_source_contribution` | Leave-one-out source contribution plus a joint residual guard; a source is contradicted only when unsupported while the remaining joint model is supported |
| `mastered_from` | `master_continuity` | Processing-tolerant continuity evidence; mastering operations are not reconstructed |
| `rendered_from`, `input_to`, or a non-audio endpoint | `unsupported_relationship` | `unavailable`; metadata relationships are not converted into acoustic claims |

The four result states are deliberately asymmetric. `corroborated` contributes
1 and `contradicted` contributes 0. `inconclusive` and `unavailable` contribute
`null` and are reported as findings. The output never calls physical similarity
`matched` or `verified`, and every result carries
`content_match_is_provenance: false`.

Each item in `assessment_checks.physical_similarity` follows one shape:

```json
{
  "relationship_type": "derived_from",
  "comparison_mode": "pairwise_continuity",
  "source_hashes": ["<source-sha256>"],
  "target_hash": "<target-sha256>",
  "status": "corroborated",
  "integrity_contribution": 1.0,
  "similarity_score": 0.87,
  "declared_ranges_used": true,
  "analysis_complete": true,
  "metrics": {},
  "threshold_policy": "pairwise_continuity-v1",
  "content_match_is_provenance": false,
  "reason": "Independent signal features support content continuity."
}
```

`assessment_checks.physical_similarity_summary` records status counts and
per-target values. For example, four corroborated and one contradicted
relationship for Target A produce 0.8; one corroborated relationship for
Target B produces 1.0; the layer value is `(0.8 + 1.0) / 2 = 0.9`. This target
normalisation prevents a large mix from dominating a smaller target merely
because it declares more source edges.

For example, declared ranges can focus an edit comparison without requiring an
exact construction recipe:

```json
{
  "hash": "<source-sha256>",
  "relationship_type": "edited_from",
  "attributes": {
    "source_start_seconds": 10.0,
    "source_end_seconds": 15.0,
    "target_start_seconds": 0.0,
    "target_end_seconds": 5.0,
    "transformation_description": "edited and lightly compressed"
  }
}
```

If ranges are absent, supported modes may estimate a bounded diagnostic
alignment. Estimated parameters are labelled diagnostic and do not alter the
edge. If the result does not reach a reliable endpoint, it is `inconclusive`,
not adverse evidence.

## Run the native example

From this directory, use Python 3.11 or later:

```bash
python3 -m pip install -r requirements.txt
# Install the optional dependency only when C2PA SDK verification is required:
python3 -m pip install -r requirements-c2pa.txt
python3 -B run.py --native examples/valid-generated/submission.json --root examples/valid-generated
```

The example processes four audio nodes in sequence: `raw-track -> stem -> mix -> master`. The expected result contains `execution_status: succeeded`, four WAV observations, three `RELATIONSHIP_INTEGRITY_OBSERVATION` findings, and three corroborated unified physical-similarity results. This is a software-generated teaching fixture, not verified evidence of human-recorded creation.

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
The local regression suite contains 101 tests, including coverage for non-final
input isolation and final-first stopping, unknown-field suggestions, audio
container detection by content, byte-capped decoding, deadline degradation,
non-final WAV isolation, integer/float extensible PCM, float32/64 decoding, non-finite
sample rejection, RIFF warnings, field-level unavailability, final-scoped C2PA,
standalone C2PA ingredient checks, hash caching, ndarray PCM decoding, 24-bit
sign extension, full-file proxy caching, non-integer anti-aliasing, unified
four-state physical output, relationship-specific comparison modes, per-target
aggregation, and resource-budget degradation without contradiction.

## Directory overview

| Location | Purpose |
|---|---|
| `vendor/libevchain/` | Local copy of Ben's source with three reviewable fixes |
| `music_target/domain.py` | Music nodes, relationships, and permitted endpoint roles |
| `music_target/wav_io.py` | Streaming RIFF/WAVE inspection and vectorised integer/float sample decoding |
| `music_target/wav_parser.py` | Local copy of the supplied WAV parser |
| `music_target/engine.py` | File binding, `WavMetadataPass`, `RelationshipIntegrityPass`, library pipeline, and assessment-stage orchestration |
| `music_target/workflow_policy.py` | Safe policy loading, condition evaluation, and per-expectation workflow completeness |
| `music_target/assessment_passes.py` | Cached file and relationship result aggregation, CrossEvidence, C2PA, and AI disclosure assessment logic |
| `music_target/audio_derivation.py` | Shared audio utilities plus deprecated exact-reconstruction passes retained for later removal; they are not executed by assessment |
| `music_target/physical_audio_passes.py` | Unified physical-similarity framework and its bounded comparison modes |
| `music_target/midi_parser.py` | Size- and structure-bounded Standard MIDI File parser |
| `music_target/sheet_parsers.py` | CompSheet/CueSheet JSON, CSV, TXT, and PDF parsers plus field normalisation |
| `music_target/contract.py` | CSEC/native format adaptation, input validation, and version identification |
| `policies/workflow_evidence_policy.json` | Candidate required, conditional, and optional evidence policy for 16 workflows |
| `run.py` | CSEC and local execution entry point |
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
