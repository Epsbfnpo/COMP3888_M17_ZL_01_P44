# Music target: local integration handoff v0.8.1

Status (updated 25 October 2026): pinned baseline for CSEC's adversarial testing. Git tag `csec-2026-10-25`,
`system.version` 0.8.1, scoring profile default-v7, `system.source_revision`
`sha256:ac048707f1bc29f53bc8ffa2ca27d8e121f6da0008fd397dddfc3bea4b9cbf5c`. Not yet executed in CSEC's runner. The scoring update (profile default-v6) and FLAC/MP3 support were
merged on 24 October 2026; on 25 October 2026 the default profile became default-v7 (forge cost scales Integrity
confidence only; values, tiers and the release rule are unchanged), and four report-only audio findings were added.
See "Changes on 25 October 2026" below and the red-team package in `docs/csec_redteam/`.

## Contact and collaboration mode

- Technical contact (COMP3888_M17_01_ZL): **Hongjie Deng**. CSEC contact: pending.
- Collaboration mode: **pending team selection** (full red-team integration, or schema/sample exchange only).
  The adversarial testing package in [`csec_redteam/`](csec_redteam/README.md) assumes CSEC runs the target locally.
- This package prepares a runnable target; preparing it does not commit the team to an integration mode.

## Pinned target

- Target name: `COMP3888-music-baseline`.
- Target version: `0.8.1`. Version 0.8.1 adds optional `assessment_checks` entries (`aaf_reference_coverage`, `aaf_non_recorder_media`, `declared_chronology`) and counts in `integrity_decision`; the response `schema_version` stays `0.2-candidate`.
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
- Public media types: `audio/wav`, `audio/flac` and `audio/mpeg`. Each maps to Ben's base `audio`, without invented
  production roles. The file content decides the container, not the declared type or the extension. The request
  schema's object-path pattern now also accepts `.flac` and `.mp3`; this local extension of the supplied candidate
  schema needs CSEC's agreement before it is relied on.
- WAV: RIFF/WAVE with uncompressed integer PCM (8/16/24/32 bit) or IEEE float (32/64 bit), including matching
  `WAVE_FORMAT_EXTENSIBLE` SubFormats, 1–32 channels, sample rate 1–384000 Hz. RF64/BW64 and compressed WAV codecs
  remain unsupported.
- FLAC: 1–8 channels, sample rate 1–384000 Hz; metadata blocks parsed with bounds; WAV chunks kept by
  `flac --keep-foreign-metadata` are read as recorder metadata; the decoded-audio MD5 is checked against STREAMINFO.
- MP3 (MPEG-1/2/2.5 Layer III): exact length only from a Xing/Info header with encoder delay and padding; no bit
  depth or recorder metadata, so those checks are reported as unavailable (`AUDIO_FORMAT_LIMITED_CHECKS`). MPEG
  Layer I/II and free-format bitrates are unsupported.
- Native input additionally supports MIDI, RIN 2.1, ERN 4.3, standalone C2PA manifests,
  CompSheet and CueSheet, field-recorder sound reports (`text/sound-report`), AAF edits (`project/aaf`) and Ableton Live
  sets (`project/daw-session`). Other DAW session formats are accepted but not parsed. BWF `bext` and iXML recorder
  metadata are read from WAV and FLAC files. See [`csec_redteam/NATIVE_INPUT_GUIDE.md`](csec_redteam/NATIVE_INPUT_GUIDE.md).
- PNG/MP4 return `unsupported` for this audio target.
- At most 128 nodes, 512 MiB per object, 1 GiB across objects, 2 MiB input JSON.
- WAV headers and metadata chunks, FLAC metadata blocks and MP3 frame headers are inspected incrementally without whole-file `read_bytes()` or the version-dependent standard-library `wave` decoder. The active assessment path does not decode sample content for signal comparison. Deprecated sample readers and low-rate proxy helpers remain isolated for later cleanup.
- Safe RIFF anomalies are retained as warnings. The direct sample reader still rejects IEEE-float NaN/Infinity, but the active assessment does not scan sample content merely to run a disabled signal diagnostic.
- The deprecated proxy implementation is not reached by assessment.
- The final object is checked before any other object and must exist, stay inside the root, fit the size limits, match its lower-case SHA-256 and objects mapping, and be a supported WAV, FLAC or MP3. Any problem with it stops the request (`error`; `unsupported` for the size limit or an unsupported container) and only the final object's problem is reported.
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
| completeness | Native submissions evaluate applicable workflow expectations; public audio-only requests retain limited declared-object availability. |
| integrity | Separately reports file binding, structural validity, declaration consistency, CrossEvidence, cryptographic attestation, and a policy-disabled physical diagnostic layer. Signal results never contribute to integrity; structural validity and C2PA attestation also do not raise it. The claim-ledger scoring redesign is future work. |
| attestation_strength | C2PA manifest/validation/trust levels assessed by the C2PA SDK (a required dependency); unavailable only if the installation is incomplete. |
| ai_disclosure | Wire key retained; display Creation-method disclosure coverage. Structured counts in `assessment_checks.ai_disclosure_coverage`; no content AI detection or declaration-truth verification. |

All unsupported axes have null value and confidence. Confidence is also null for the limited completeness metric because no confidence model is defined.
Provenance validation fields are null, not false. No C2PA absence, validity or trust claim is inferred from an unperformed check.

From system version 0.8.0, `validation` may also carry `trust_status` (`trusted_c2pa`, `trusted_legacy_itl`, `untrusted`, `invalid`), `trust_source` (pinned list and commit), `revocation_status` (`not_checked`) and `signer`. These are optional schema properties, so `schema_version` remains `0.2-candidate`; changing the shared contract version needs agreement with CSEC.

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

Eleven supplied public bundles were exercised with version 0.1.0; this is a historical snapshot and has not been
rerun with 0.8.1 (see `reports/public-results/README.md`). Two WAV cases returned succeeded; seven PNG and two MP4
cases returned unsupported.
All eleven responses were validated against the supplied CSEC response schema.
No private oracle, expected decision or attack label was used. See `reports/public-summary.json` and individual response files.

## Adversarial testing package

The draft package for CSEC's adversarial testing (attack goals and success
criteria, native-input guide, packing script, cost-record and issue-report
templates) is in [`csec_redteam/`](csec_redteam/README.md). Native requests
are used because the public profile carries single audio files and the `draft-of.audio` relationship only. CSEC already holds the
client's Project Rock dataset, which is not redistributed.

## Outstanding coordination

1. Name the CSEC contact and select the collaboration mode.
2. Confirm the Ben upstream commit to use when replacing the local snapshot.
3. Confirm whether the candidate profile and the limited completeness metric are acceptable for initial execution reporting.
4. Genuine honest material: CSEC already holds the client's Project Rock dataset, which serves as the honest
   baseline and is not redistributed; the invalid/unsupported fixtures are already included.
6. Now that the scoring update is merged: pin the version and Git tag, rerun the public bundles, and check a fresh-environment install.
5. If binary classification is later desired, provide a named/versioned target decision policy and appropriate evaluation ground truth.

## Scoring remediation (2026-10-20)

This section originally described 0.8.1 (default-v4). The scoring update has
since been merged: the default profile is now **default-v6** (see the next
paragraph and the README section "Integrity weighting and cap").

In default-v6, a file's declared technical values are scored in declaration
consistency instead of file integrity, one fact per declared property; the
same claim about one recording in several documents (sound report and AAF
scene/take or track names) is one cross-evidence fact; and two report-only
sections are added, `assessment_checks.evidence_sources` (documents, facts,
the uploader as known common source, independence `unknown`) and
`assessment_checks.support_breadth` (which required recordings have
qualifying evidence). The optional `withholding_rule.min_supported_asset_share`
gate is off. A submission with declared technical values now usually has three
populated layers, so its Integrity confidence is 0.8 instead of 0.533. G1 and
G3 baselines must be measured against the pinned profile.

## Changes on 25 October 2026

- **Profile default-v7.** `integrity.forge_cost` multiplies Integrity confidence
  by `0.5 + 0.5 x strength / 5`, where strength is the mean, over checked
  recordings and documents, of the forge cost of the strongest assessed
  cross-evidence fact (trusted signature 5, recorder metadata 3, AAF or DAW
  not checked against recorder metadata 2, other documents and unbound files
  1). The Integrity value, status, tiers and release rule are unchanged, so
  value-based G1 and G3 baselines carry over; confidence baselines must be
  re-measured. New field: `assessment_checks.evidence_strength`. Method and
  measurements: `docs/FORGE_COST.md`.
- **Report-only audio findings** (only with `--audio-analysis`):
  `AUDIO_DECLARED_RANGE_NOT_SUPPORTED`, `AUDIO_TAKE_CONTENT_BEYOND_FINAL`,
  `AUDIO_UNUSED_RANGE_FOUND_IN_FINAL` and `AUDIO_SOURCE_CONTENT_BEYOND_TARGET`,
  with `assessment_checks.audio_declared_range`, `audio_declared_range_search`,
  `audio_take_beyond_final`, `audio_aaf_unused_range` and
  `audio_source_coverage`. None changes a score. The diagnostic schema accepts
  the new `range_source` values `declared_range_search` and `aaf_unused_range`.
- **Known gap.** A start timecode moved by one second in both the WAV (bext)
  and the sound report raises no contradiction: the AAF only checks
  containment, and bext and iXML timestamps are not yet compared
  (`docs/NEXT_TASKS.md` item 42). Finding it again is not a new defect; its
  cost is still worth recording.

As in default-v4, cross-evidence scores one rule/subject
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
