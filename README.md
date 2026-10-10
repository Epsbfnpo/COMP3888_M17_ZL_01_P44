# Start Here: Music Evidence Chain and CSEC Integration

This directory contains an implemented baseline for assessing evidence from music production workflows. It was assembled from the two local projects supplied for this work on 2026-09-10. No code from `audio_evchain` was read, reused, or modified.

The main approach is to reuse Ben's evidence graph and execution framework, connect the music parsers and checks, and expose a runnable interface for CSEC.

The runnable evaluator is in [`program/`](program/). Unless stated otherwise, file locations and commands below are relative to that directory.

## Team responsibilities

| Team | Core responsibility | Important current boundary |
|---|---|---|
| Ben / library team | Native JSON, evidence nodes and relationships, type registration, analysis-pass execution, and score data containers | A framework alone does not provide music analysis or four-dimension assessment algorithms |
| Music team | Music types, file parsing, cross-evidence checks, domain-level assessment meaning, and the runnable target | Parsers and candidate four-dimension assessments are integrated; real standards-based fixtures and customer policy confirmation are still required |
| CSEC team | Test fixtures, target execution, result recording, and red-team assessment | CSEC needs a reproducible entry point but does not define acceptance or rejection rules for the music team |

## Recommended reading order

1. [CSEC handoff](program/docs/03_CSEC_HANDOFF.md): integration instructions and supported boundaries.
2. [Sheet formats](program/docs/SHEET_FORMATS.md): supported CompSheet and CueSheet formats.
3. [Future work](program/docs/NEXT_TASKS.md): consolidated P0-P3 roadmap and unresolved legacy items.
4. [Verification results](program/reports/verification.md): executed checks and remaining unverified areas.

## Current implementation

- Includes a pinned copy of the library source reviewed for this release, with fixes for audio attributes, binary-file binding, and dictionary attribute checks.
- Registers music audio, MIDI, DAW, RIN, ERN, C2PA, and text evidence types, plus constrained relationship types.
- Reuses the WAV parser with additional RIFF/PCM structural validation.
- Provides a bounded MIDI parser; the RIN, ERN, and C2PA parsers are connected to the production execution path.
- The MIDI parser extracts channel and meta events, notes, tempo, time signatures, controllers, and duration in seconds when it can be calculated.
- CompSheet and CueSheet parsers support strict JSON, CSV, delimited text, and PDF text extraction. See [`program/docs/SHEET_FORMATS.md`](program/docs/SHEET_FORMATS.md).
- Runs file, relationship, and cross-evidence analysis through the actual `EvidenceChain` and `Pipeline` implementations.
- `WavMetadataPass` emits standardised technical checks once per audio artefact. `BoundFileIntegrityPass` consumes those cached checks instead of repeating the same comparisons.
- `RelationshipIntegrityPass` is a registered libevchain `EvidencePass`. It compares only submitter-declared edge parameters with cached source and target observations and replaces the former diagnostic-only `MusicRelationshipPass`.
- Includes scoped PCM content checks for `edited_from`, `comped_from`, `stemmed_from`, and `mixed_from`, plus conservative mix-to-master continuity corroboration. Exact checks use only submitter-declared ranges, placements, gains, and optional fades; no missing transformation parameter is estimated. Declared reconstruction is evaluated on alternating partitions and reports coverage, matched duration, source redundancy, identifiability, and repeated-segment ambiguity. Complex or undocumented processing is not automatically treated as a contradiction.
- Adds eight bounded physical-audio diagnostics: `AudioAlignmentPass`, `SourceContributionPass`, `CompVerificationPass`, `ProcessedAudioMatchPass`, `StemMixResidualPass`, `MasteringDerivationPass`, `ExcerptedFromPass`, and `DecoySourcePass`. Signal-estimated offsets, gains, and channel matrices are labelled diagnostic, never written back into the evidence graph, and do not silently become provenance proof or exact integrity credit.
- Master comparison uses only the submitted source and target ranges. A search for competing locations may reduce certainty by exposing ambiguity, but an alternative location is never substituted for the declared one.
- Separates submitter-declared technical properties from measured file properties and reports inconsistencies.
- Requires `workflow_id`, `modifiers`, and submitter declarations in `submission.json`, and loads policies for 16 workflow types.
- `WorkflowCompletenessPass` reports the state of each required, conditional, and optional expectation.
- Reports integrity, C2PA attestation, and AI disclosure independently. AI disclosure measures declaration coverage; it does not detect AI-generated content.
- Separates file integrity, structural validity, declaration consistency, content reconstruction, CrossEvidence, and cryptographic attestation. Structural endpoint validity and C2PA do not inflate the integrity value; C2PA remains in the separate attestation-strength axis.
- Provides CSEC request conversion, a standard response contract, timeout control, and explicit `unsupported` and `error` states.
- Applies XML size limits and rejects DTD/entity declarations. RIN 2.1 and ERN 4.3 are validated offline with pinned official XSD files, while XSD status remains separate from best-effort field extraction.
- Binds available RIN `FileReference` and ERN `DeliveryFile` SHA-256 values to recordings/components. Cross-evidence checks cover MIDI, ERN, C2PA ingredients, and AI declaration consistency.
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

The program does not infer relationships or fill in missing relationship
parameters. It evaluates only graph edges and transformation parameters supplied
in the submission. Missing evidence lowers assessment availability and
confidence; it is not a contradiction and does not directly lower the integrity
value. When relationship evidence is insufficient, the claim remains
`declared_unverified`, the content check is `unavailable`, and the integrity
value may remain `null`.

Content derivation is activated conservatively:

| Relationship | Required evidence/scope | Content result |
|---|---|---|
| `edited_from` | `derivation_scope: linear_edit` plus source start/end, target start/end, and gain on every edge; fades are optional | Exact declared crop, placement, splice, gain, and simple-fade reconstruction |
| `comped_from` | Parsed CompSheet with source/target hashes, source/target start/end, and gain for every assessed selection | Declared comp timeline reconstruction |
| `stemmed_from` | `derivation_scope: linear_stem` (legacy `linear_mix` is accepted) plus source/target start/end and gain on every edge | Exact declared source summation |
| `mixed_from` | `derivation_scope: linear_mix` plus source/target start/end and gain on every edge | Exact declared mix reconstruction |
| `mastered_from` | `derivation_scope: master_similarity` plus declared source and target start/end ranges | Non-proving waveform, dynamics, loudness, and coarse spectral corroboration at the declared position |

`matched` is limited to the declared reconstruction model and independent holdout blocks. `corroborated` is weaker and is reported without being silently scored as exact proof. `contradicted` is an assessed conflict. `not_applicable` means the submitted processing falls outside the supported model or is ambiguous/inconclusive; `unavailable` means there was no evidence to run the check. None of these statuses proves historical provenance by itself.

For example, this edge contains enough information for a linear edit check:

```json
{
  "hash": "<source-sha256>",
  "relationship_type": "edited_from",
  "attributes": {
    "derivation_scope": "linear_edit",
    "source_start_seconds": 10.0,
    "source_end_seconds": 15.0,
    "target_start_seconds": 0.0,
    "target_end_seconds": 5.0,
    "gain": 0.8
  }
}
```

If any required field is absent, the edge is still retained as a submitter
claim, but content reconstruction returns `unavailable` with
`reason_code: missing_derivation_parameters`. The program does not search for a
replacement range or fit a replacement gain.

## Run the native example

From the repository root, enter the evaluator directory and use Python 3.11 or later:

```bash
cd program
python3 -m pip install -r requirements.txt
# Install the optional dependency only when C2PA SDK verification is required:
python3 -m pip install -r requirements-c2pa.txt
python3 -B run.py --native examples/valid-generated/submission.json --root examples/valid-generated
```

The example processes four audio nodes in sequence: `raw-track -> stem -> mix -> master`. The expected result contains `execution_status: succeeded`, four WAV observations, and three `RELATIONSHIP_INTEGRITY_OBSERVATION` findings produced by the edge Pass. Its legacy relationships intentionally omit detailed derivation parameters, so their parameter-check lists are empty and their content checks are `unavailable` rather than inferred. This is a software-generated teaching fixture, not verified evidence of human-recorded creation.

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

## Directory overview

The locations below are relative to `program/`.

| Location | Purpose |
|---|---|
| `vendor/libevchain/` | Local copy of Ben's source with three reviewable fixes |
| `music_target/domain.py` | Music nodes, relationships, and permitted endpoint roles |
| `music_target/wav_parser.py` | Local copy of the supplied WAV parser |
| `music_target/engine.py` | File binding, `WavMetadataPass`, `RelationshipIntegrityPass`, library pipeline, and assessment-stage orchestration |
| `music_target/workflow_policy.py` | Safe policy loading, condition evaluation, and per-expectation workflow completeness |
| `music_target/assessment_passes.py` | Cached file and relationship result aggregation, CrossEvidence, C2PA, and AI disclosure assessment logic |
| `music_target/audio_derivation.py` | Edit, CompSheet, stem, mix, and master-content derivation checks |
| `music_target/physical_audio_passes.py` | Bounded alignment, per-source contribution, processed-content, residual, excerpt, mastering, and decoy diagnostics |
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
