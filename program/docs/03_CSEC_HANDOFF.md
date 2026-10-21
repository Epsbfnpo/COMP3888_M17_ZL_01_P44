# Music target: local integration handoff v0.7

Status: locally tested integration baseline, prepared for review. Not yet sent to CSEC or executed in CSEC's runner.

## Contact and collaboration mode

- Technical contact: **pending team input**.
- Collaboration mode: **pending team selection** (full red-team integration, or schema/sample exchange only).
- This package prepares a runnable target; preparing it does not commit the team to an integration mode.

## Pinned target

- Target name: `COMP3888-music-baseline`.
- Target version: `0.7.0`.
- Runtime code revision: see `reports/release.json` and `system.source_revision` in each response.
  This is an explicit SHA-256 content identifier, not a claimed Git commit.
- The supplied Ben library snapshot has no Git metadata; its original files are identified in `reports/source_inventory.json`.
- Three local library corrections are recorded in `docs/ben-local-fixes.patch`.
- Tested interpreter: CPython 3.13.7 on macOS. Code supports Python 3.11-3.13. `numpy==2.2.6` is pinned for that interpreter range; no SciPy dependency is used. Other platforms have not been tested here.
- Direct and transitive dependency versions used for verification are pinned in `requirements.txt`.
- No credentials, online APIs, model weights, external media services or database are needed during evaluation.

## Install and run

From the package root, a fresh environment can be prepared as follows:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -B run.py --request examples/csec-wav/request.json --root examples/csec-wav
```

The test in this local task used an existing interpreter with those exact dependencies. A new environment installation has not been performed in this task.

Actual target invocation:

```bash
python3 -B run.py --request /path/to/request.json --root /path/to/case-root
```

- `--root` is supplied by the caller and contains `objects/`.
- A JSON scoring response is written to stdout. Use `--output /path/to/response.json` to save it instead.
- Process exit code 0 means a protocol response was produced. Read `execution_status` to distinguish succeeded, unsupported, timeout and error.
- Unreadable/malformed JSON, or an input without sufficient valid identity fields to construct a response, produces a diagnostic on stderr and exit code 2. No fake asset hash is inserted.
- Logs are not mixed into stdout.
- Local convenience: `--bundle /path/to/bundle.json --root /path/to/case-root` constructs the request envelope and file map from the public content-addressed layout.
- Local music graph input: `--native examples/valid-generated/request.json --root examples/valid-generated`.

## Supported scope

- CSEC request profile: `csec-public-bundle-v0.2-candidate`, schema version `0.2-candidate`.
- The original supplied candidate request and response schemas are retained under `schemas/`.
- Public media type: `audio/wav` only. Maps to Ben's base `audio`, without invented production roles.
- Container/codec: RIFF/WAVE with uncompressed integer PCM (8/16/24/32 bit) or IEEE float (32/64 bit), including matching `WAVE_FORMAT_EXTENSIBLE` SubFormats, 1–32 channels, sample rate 1–384000 Hz. RF64/BW64 and compressed WAV codecs remain unsupported.
- Public CSEC remains WAV-only. Native input additionally supports MIDI, RIN 2.1, ERN 4.3, C2PA, CompSheet and CueSheet evidence; DAW parsing remains out of scope.
- PNG/MP4 return `unsupported` for this audio target.
- At most 128 nodes, 512 MiB per object, 1 GiB across objects, 2 MiB input JSON.
- WAV headers and metadata chunks are inspected incrementally without whole-file `read_bytes()` or the version-dependent standard-library `wave` decoder. The active assessment path does not decode sample content for signal comparison. Deprecated sample readers and low-rate proxy helpers remain isolated for later cleanup.
- Safe RIFF anomalies are retained as warnings. The direct sample reader still rejects IEEE-float NaN/Infinity, but the active assessment does not scan sample content merely to run a disabled signal diagnostic.
- The deprecated proxy implementation is not reached by assessment.
- The final object is checked before any other object and must exist, stay inside the root, fit the size limits, match its lower-case SHA-256 and objects mapping, and be a supported WAV. Any problem with it stops the request (`error`; `unsupported` for the size limit or an unsupported container) and only the final object's problem is reported.
- Input errors are never corrected. A non-final problem isolates only what depends on it: `OBJECT_HASH_MISMATCH`, `OBJECT_SKIPPED_SIZE_LIMIT`, `OBJECT_PATH_INVALID`, `INVALID_HASH_FORMAT` (upper-case hash) and `INVALID_OBJECT_PATH` (upper-case extension or file name not matching the hash) leave the file unbound; `UNKNOWN_FIELD` (with a suggested spelling) disables only the checks that use that declaration. Dependent checks are `unavailable`, workflow rules that rely on the node are `not_assessed`, and `SCORE_AFFECTED_BY_INPUT_ERRORS` marks every otherwise available axis `partial`. Missing non-final evidence files are reported as partial availability. Objects entries that name no declared artefact are reported as `UNUSED_OBJECT_MAPPING` and ignored.
- The native request schema accepts upper-case hashes, any file extension and extra attribute fields so that these problems can be isolated and reported; the CSEC request and response schemas are unchanged. Hash values that are not lower-case SHA-256 appear in findings as `submitted_hash`, never as `evidence_hash`.
- Dangling graph references (including a hash that differs from its node only in letter case), cycles, duplicate hashes, duplicate source edges per target, disconnected nodes and invalid role endpoints are errors.
- Object paths must stay within the supplied root after symlink resolution. Files are SHA-256 checked before binding.
- Public audio edges currently accept only an explicitly named `draft-of.audio`; any other public relationship needs an agreed mapping first.
- The separate local native profile includes seven music audio roles and seven domain relationship names; it is a local proposal, not a frozen cross-team standard.

## Timeout and process model

- Evaluation runs in a separate subprocess for each request.
- Wall-clock timeout: the request's `options.timeout_seconds`, including worker startup. The evaluator adds no ceiling of its own; 60 seconds is used only when a request carries no valid value (for example `--bundle` mode).
- Deprecated signal helpers retain weighted channel-second, PCM-window, and proxy-cache limits, but the active assessment does not call them.
- The worker still receives a deadline slightly before the timeout for bounded evaluation and future optional diagnostics.
- A final object that changes while it is being evaluated stops the request (`FINAL_ARTEFACT_CHANGED`). If a non-final object changes, the request is evaluated once more with that object isolated from the start (`OBJECT_CHANGED_DURING_EVALUATION`), so no result read from a half-replaced file survives; if objects change again during the second attempt, the request returns `error`.
- The active physical diagnostic placeholders always use `reason_code: disabled_by_policy`; no sample search starts and no signal-based contradiction can be produced.
- On timeout, the worker is terminated and a schema-valid `timeout` response is produced.
- Execution is always local/offline, including when `offline_only` is false; that option does not enable network behaviour.
- `include_reasoning: false` clears axis reasoning. Required finding messages and factual observations remain in the response.

## Axis semantics and decision policy

| Axis | Current meaning |
|---|---|
| completeness | Native submissions evaluate applicable workflow expectations; public WAV-only requests retain limited declared-object availability. |
| integrity | Separately reports file binding, structural validity, declaration consistency, CrossEvidence, cryptographic attestation, and a policy-disabled physical diagnostic layer. Signal results never contribute to integrity; structural validity and C2PA attestation also do not raise it. The claim-ledger scoring redesign is future work. |
| attestation_strength | C2PA manifest/validation/trust levels assessed by the C2PA SDK (a required dependency); unavailable only if the installation is incomplete. |
| ai_disclosure | Wire key retained; display Creation-method disclosure coverage. Structured counts in `assessment_checks.ai_disclosure_coverage`; no content AI detection or declaration-truth verification. |

All unsupported axes have null value and confidence. Confidence is also null for the limited completeness metric because no confidence model is defined.
Provenance validation fields are null, not false. No C2PA absence, validity or trust claim is inferred from an unperformed check.

There is **no target-owned authorship decision policy**. `decision` and `overall_score` are omitted.
Execution status must not be converted into accepted/rejected. Please report execution and available axis observations only; false-accept/false-reject rates are not supported.

The active assessment performs neither exact reconstruction nor physical signal
comparison. `PhysicalSimilarityPass` is not imported or invoked. For output
compatibility, each declared relationship receives an `unavailable` placeholder
with `comparison_mode: disabled`, `reason_code: disabled_by_policy`,
`scoring_eligible: false`, and `integrity_contribution: null`. These placeholders
emit no physical-similarity findings and never affect integrity. Every item
retains `content_match_is_provenance: false`. The deprecated implementation and
the future optional-diagnostic boundary are documented in
[`PHYSICAL_SIMILARITY.md`](PHYSICAL_SIMILARITY.md).

## Included examples and outputs

- `examples/csec-wav/request.json` and `response.json`: schema-compliant public-profile request/actual response with a generated PCM WAV.
- `examples/valid-generated/`: four-node native music graph with three relationship observations.
- `examples/invalid-hash/`: valid request structure with a modified final media object; returns error with `OBJECT_HASH_MISMATCH`.
- `examples/unsupported-png/`: candidate request with unsupported media; returns unsupported.
- All examples are engineering fixtures. The generated valid example is **not** an authenticated human recording session or a supplied honest-human ground-truth sample.
- A real, provenance-explained honest sample remains a team deliverable if required for the intended red-team evaluation.

## Local public-corpus verification

Eleven supplied public bundles were exercised. Two WAV cases returned succeeded; seven PNG and two MP4 cases returned unsupported.
All eleven responses were validated against the supplied CSEC response schema.
No private oracle, expected decision or attack label was used. See `reports/public-summary.json` and individual response files.

## Outstanding coordination

1. Fill the technical contact and select collaboration mode.
2. Confirm the Ben upstream commit to use when replacing the local snapshot.
3. Confirm whether the candidate profile and the limited completeness metric are acceptable for initial execution reporting.
4. Supply a genuine honest sample with an explained creation history; the invalid/unsupported fixtures are already included.
5. If binary classification is later desired, provide a named/versioned target decision policy and appropriate evaluation ground truth.

The user-provided CSEC message requests revision, install/run command, supported types, one output, timeout and any existing policy preferably by Friday 11 September 2026; then a runnable revision plus honest and invalid/unsupported samples by Sunday 13 September 2026.

## Scoring remediation (2026-10-20)

The default profile is now default-v4. Cross-evidence scores one rule/subject
fact once, with any failure retaining a contradiction; raw checks and source
document hashes remain available. An ordinary contradiction cannot release an
otherwise withheld Integrity value. Read `axes.integrity.status` or
`assessment_checks.integrity_decision` for status and release reason. A severe
contradiction releases min(weighted mean, 0.25) even without qualifying evidence.

Recorder metadata comparisons remain scored and release-eligible. The
`RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED` informational finding has no
score effect and does not claim the recording origin was verified. Confidence
formulas and layer weights are unchanged. See
[the scoring repair record](SCORING_REMEDIATION_2026-10-20.md).
