# Start Here: Music Evidence Chain and CSEC Integration

Optional audfprint, SpectroMap and librosa **report-only audio diagnostics** are
available with `--audio-analysis`. See [setup, examples and evidence boundaries](docs/AUDIO_DIAGNOSTICS.md).
They do not affect scores, weights, confidence or caps. The legacy signal pass
remains disabled. Install optional dependencies with `python -m pip install -r requirements-audio.txt`.

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
2. [Physical similarity technical reference](docs/PHYSICAL_SIMILARITY.md): policy-disabled legacy signal checks and the compatibility output retained for review.
3. [Sheet formats](docs/SHEET_FORMATS.md): supported CompSheet and CueSheet formats.
4. [Future work](docs/NEXT_TASKS.md): consolidated P0-P3 roadmap and unresolved legacy items.
5. [Verification results](reports/verification.md): executed checks and remaining unverified areas.

## Current implementation

- Includes a pinned copy of the library source reviewed for this release, with fixes for audio attributes, binary-file binding, and dictionary attribute checks.
- Registers music audio, MIDI, DAW, RIN, ERN, C2PA, and text evidence types, plus constrained relationship types.
- Uses one streaming RIFF/WAVE reader for structural inspection, metadata parsing, and bounded sample decoding. It supports integer PCM (8/16/24/32-bit), IEEE float (32/64-bit), and integer/float `WAVE_FORMAT_EXTENSIBLE` without loading the whole file.
- Provides a bounded MIDI parser; the RIN, ERN, C2PA, sound report (`text/sound-report`) and AAF (`project/aaf`) parsers are connected to the production execution path.
- Explains every score in `assessment_checks.score_explanations`: layers, weights, caps, withholding and each failed check in plain words with the compared values (for example a 2.00 s timecode gap). Evidence outside the selected workflow is reported as inconclusive, never penalised.
- Parses Ableton Live sessions (`project/daw-session`) and checks that the session uses the declared source audio and agrees with MIDI tempo and time signature.
- Adds report-only musical semantics (`music_semantics`): per-file acoustic boundaries, key/chords and bounded transposition/tempo observations with explicit abstention. See `docs/AUDIO_CALIBRATION_2026-10-20.md`.
- `benchmarks/music_benchmark.py` runs the evaluator on Slakh/BabySlakh or MUSDB18-HQ with true and control stems; see `benchmarks/README.md`.
- Scores cross-evidence once per rule and subject, with any assessed failure keeping the fact failed; cosmetic copies cannot add support or penalties. Raw observations remain available.
- The `default-v4` release gate requires qualifying assessed evidence and layer coverage; an ordinary contradiction never releases a number, while a severe contradiction releases at most the 0.25 cap. `axes.integrity.status` and `integrity_decision` report status separately from the number.
- Recorder metadata consistency can score and qualify release; its origin-not-authenticated finding is informational and has no Integrity effect. See the repository README, "Evidence tiers".
- The MIDI parser extracts channel and meta events, notes, tempo, time signatures, controllers, and duration in seconds when it can be calculated.
- CompSheet and CueSheet parsers support strict JSON, CSV, delimited text, and PDF text extraction. See [`docs/SHEET_FORMATS.md`](docs/SHEET_FORMATS.md).
- Runs file, relationship, and cross-evidence analysis through the actual `EvidenceChain` and `Pipeline` implementations.
- `WavMetadataPass` emits standardised technical checks once per audio artefact. `BoundFileIntegrityPass` consumes those cached checks instead of repeating the same comparisons.
- A non-final WAV parser failure is isolated to that node: the program emits `WAV_PARSER_FAILED`, marks only dependent checks unavailable, and continues with independent evidence. An unsupported final WAV still returns `unsupported`.
- `RelationshipIntegrityPass` is a registered libevchain `EvidencePass`. It compares only submitter-declared edge parameters with cached source and target observations and replaces the former diagnostic-only `MusicRelationshipPass`.
- Does not execute the legacy `PhysicalSimilarityPass`. Each declared relationship receives an explicit `unavailable` diagnostic placeholder with `reason_code: disabled_by_policy`, `scoring_eligible: false`, and no integrity contribution. The deprecated implementation remains isolated for later cleanup.
- The rationale, compatibility output, and boundary for a future optional fingerprint provider are specified in [`docs/PHYSICAL_SIMILARITY.md`](docs/PHYSICAL_SIMILARITY.md).
- Exact reconstruction is no longer executed or scored. Legacy reconstruction code remains isolated for later removal, but its `matched`/`not_applicable` statuses and pass-specific report keys are not exposed by the assessment path.
- Deprecated sample helpers can decode WAV data with NumPy into C-contiguous `(frames, channels)` `float32` arrays, but the active assessment path does not call them for signal analysis. The pinned `numpy==2.2.6` dependency supports the project's Python 3.11-3.13 range; no SciPy dependency is used.
- RIFF size, data size, partial-frame, byte-rate, and trailing-byte anomalies are reported as stable warnings where safe. Unsupported codecs remain unsupported. NaN/Infinity remains rejected by the direct sample reader, but the active assessment does not scan sample content merely to run a disabled diagnostic.
- Legacy low-rate proxies and waveform, landmark, DTW, contribution, residual, and excerpt-search helpers remain in deprecated modules but are not reached by the assessment path.
- Physical similarity is diagnostic-only and policy-disabled. Every placeholder is `unavailable`, has `scoring_eligible: false` and `integrity_contribution: null`, and carries `content_match_is_provenance: false`.
- Objects are limited to 512 MiB each and 1 GiB per request, and the caller's wall-clock timeout has no evaluator-side 60-second cap. Deprecated signal helpers retain PCM-window, proxy-cache, and weighted-work guards, but the active assessment does not invoke them.
- No sample-content search or signal comparison is started by the active assessment path.
- Separates submitter-declared technical properties from measured file properties and reports inconsistencies.
- Requires `workflow_id`, `modifiers`, and submitter declarations in `submission.json`, and loads policies for 16 workflow types.
- `WorkflowCompletenessPass` reports the state of each required, conditional, and optional expectation.
- A missing non-final object is reported as `MISSING_OBJECT` and its file-binding check is `not_assessed`; absence affects workflow completeness (or legacy declared-object availability when no workflow is selected) but is not evidence of a file-integrity contradiction.
- Reports integrity, C2PA attestation, and AI disclosure independently. The top-level C2PA axis is scoped to the final artefact; supporting-node manifests are reported separately and cannot raise the final score. AI disclosure measures declaration coverage; it does not detect AI-generated content.
- Separates file integrity, structural validity, declaration consistency, disabled physical diagnostics, CrossEvidence, and cryptographic attestation. Physical diagnostics never affect integrity. Structural endpoint validity and C2PA do not inflate integrity; C2PA remains in the separate attestation-strength axis.
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
- `ai_disclosure` keeps its wire key but is displayed as **Creation-method disclosure coverage**. Its structured counts and finding explicitly state that no content AI detector ran and declaration truth was not verified.

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
value. The legacy physical-similarity layer is disabled for every relationship
because its narrow frequency and transformation assumptions can produce
misleading results for legitimate production material. It emits no
physical-similarity findings and never changes integrity. Each declared
relationship remains visible as an `unavailable` compatibility placeholder;
this describes policy state, not a failed analysis.

Each item in `assessment_checks.physical_similarity` follows one shape:

```json
{
  "relationship_type": "derived_from",
  "comparison_mode": "disabled",
  "source_hashes": ["<source-sha256>"],
  "target_hash": "<target-sha256>",
  "status": "unavailable",
  "integrity_contribution": null,
  "scoring_eligible": false,
  "similarity_score": null,
  "declared_ranges_used": false,
  "analysis_complete": false,
  "metrics": {},
  "threshold_policy": null,
  "content_match_is_provenance": false,
  "reason_code": "disabled_by_policy",
  "reason": "Legacy signal comparisons are disabled by policy."
}
```

`assessment_checks.physical_similarity_summary` reports `enabled: false`,
`scoring_eligible: false`, a null value, zero scored relationships, and the
number of declared relationships represented by disabled placeholders.

Declared ranges remain available to deterministic relationship checks even
though no signal comparison is run:

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

No offset, gain, channel matrix, or other signal parameter is estimated.

## Run the native example

From this directory, use Python 3.11 or later:

```bash
# Includes the required C2PA SDK (c2pa-python) used for attestation checks:
python3 -m pip install -r requirements.txt
python3 -B run.py --native examples/valid-generated/submission.json --root examples/valid-generated
```

The example processes four audio nodes in sequence: `raw-track -> stem -> mix -> master`. The expected result contains `execution_status: succeeded`, four WAV observations, three `RELATIONSHIP_INTEGRITY_OBSERVATION` findings, and three policy-disabled physical diagnostic placeholders. This is a software-generated teaching fixture, not verified evidence of human-recorded creation.

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

## Desktop manifest builder

Install `requirements.txt` and make sure Tkinter is available, then run:

```bash
python3 builder_gui.py
```

The builder imports and hashes files in the background, provides workflow
preflight and full assessment, and can save the full machine-readable result.
Full assessment uses a verified temporary snapshot and the same production
worker as the command-line evaluator.

Portable exports place files in `objects/` under content-addressed names
(`objects/<sha256>.<extension>`) accepted by the evaluator. The source files
are never renamed or overwritten. By default, saving a manifest can instead
reference the selected originals without copying them; every save checks that
each original still matches its declared SHA-256. These manifests set
`options.local_file_references` and require explicit authorization for direct
evaluation:

```bash
python3 -B run.py --native /path/to/submission.json \
  --root /path/to/submission-folder --allow-local-files
```

Both the manifest option and the command-line flag are required. The flag is
restricted to trusted native manifests and cannot authorize CSEC/public bundle
paths. The builder's full-assessment action stages verified originals inside a
temporary bundle root and does not need external-path authorization.

## Verification

```bash
python3 -B -m unittest discover -s tests -v
python3 -B check_public.py /path/to/public-bundles
```

The second command writes results to `reports/`. Both commands operate locally and do not transmit data.
The regression suite includes core and optional audio tests, with coverage for non-final
input isolation and final-first stopping, unknown-field suggestions, audio
container detection by content, byte-capped decoding, deadline degradation,
non-final WAV isolation, integer/float extensible PCM, float32/64 decoding, non-finite
sample rejection, RIFF warnings, field-level unavailability, final-scoped C2PA,
standalone C2PA ingredient checks, hash caching, ndarray PCM decoding, 24-bit
sign extension, legacy signal-helper isolation, and an explicit assertion that
the policy-disabled `PhysicalSimilarityPass` is never invoked by assessment.
Severe-cap tests also check that a capped Integrity value is explained in its
reasoning and recorded in `assessment_checks.severe_contradiction_cap`. Audio tests
include a 3-minute long-source excerpt and length-independent SpectroMap peak density.
Run them with `REQUIRE_AUDIO_BACKENDS=1` so that missing optional backends fail
instead of being skipped.
`tests/test_production_documents.py` covers recorder metadata, sound reports and
AAF edits, including tampered timecodes and same-named takes from different days.

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
| `music_target/physical_audio_passes.py` | Deprecated physical-similarity implementation retained for later cleanup; not imported by the assessment path |
| `docs/PHYSICAL_SIMILARITY.md` | Policy record for the disabled signal layer and future optional diagnostic boundary |
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
