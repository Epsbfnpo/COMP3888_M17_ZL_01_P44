# Music target: local integration handoff v0.4

Status: locally tested integration baseline, prepared for review. Not yet sent to CSEC or executed in CSEC's runner.

## Contact and collaboration mode

- Technical contact: **pending team input**.
- Collaboration mode: **pending team selection** (full red-team integration, or schema/sample exchange only).
- This package prepares a runnable target; preparing it does not commit the team to an integration mode.

## Pinned target

- Target name: `COMP3888-music-baseline`.
- Target version: `0.4.0`.
- Runtime code revision: see `reports/release.json` and `system.source_revision` in each response.
  This is an explicit SHA-256 content identifier, not a claimed Git commit.
- The supplied Ben library snapshot has no Git metadata; its original files are identified in `reports/source_inventory.json`.
- Three local library corrections are recorded in `docs/ben-local-fixes.patch`.
- Tested interpreter: CPython 3.13.7 on macOS. Code requires Python 3.11 or newer; other platforms have not been tested here.
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
- Container/codec: RIFF/WAVE, uncompressed integer PCM, 8/16/24/32 bit, 1–32 channels, sample rate 1–384000 Hz.
- Public CSEC remains WAV-only. Native input additionally supports MIDI, RIN 2.1, ERN 4.3, C2PA, CompSheet and CueSheet evidence; DAW parsing remains out of scope.
- PNG/MP4 return `unsupported` for this audio target.
- At most 128 nodes, 32 MiB per object, 128 MiB across objects, 2 MiB input JSON.
- A final object must exist. Missing non-final evidence files can be reported as partial availability if their nodes are declared.
- Dangling graph references, cycles, duplicate hashes, duplicate source edges per target, disconnected nodes, invalid role endpoints and invalid paths are errors.
- Object paths must stay within the supplied root after symlink resolution. Files are SHA-256 checked before binding.
- Public audio edges currently accept only an explicitly named `draft-of.audio`; any other public relationship needs an agreed mapping first.
- The separate local native profile includes seven music audio roles and seven domain relationship names; it is a local proposal, not a frozen cross-team standard.

## Timeout and process model

- Evaluation runs in a separate subprocess for each request.
- Enforced wall-clock timeout: the smaller of `options.timeout_seconds` and 60 seconds, including worker startup.
- On timeout, the worker is terminated and a schema-valid `timeout` response is produced.
- Execution is always local/offline, including when `offline_only` is false; that option does not enable network behaviour.
- `include_reasoning: false` clears axis reasoning. Required finding messages and factual observations remain in the response.

## Axis semantics and decision policy

| Axis | Current meaning |
|---|---|
| completeness | Native submissions evaluate applicable workflow expectations; public WAV-only requests retain limited declared-object availability. |
| integrity | Separately reports file binding, structural validity, declaration consistency, CrossEvidence, held-out edit/comp/stem/mix reconstruction, and master content-continuity corroboration. Structural validity and C2PA attestation do not raise integrity. Remaining weights require calibration. |
| attestation_strength | C2PA manifest/validation/trust levels when the optional SDK assesses them; otherwise unavailable. |
| ai_disclosure | Explicit `creation_method` disclosure coverage, not AI-content detection. |

All unsupported axes have null value and confidence. Confidence is also null for the limited completeness metric because no confidence model is defined.
Provenance validation fields are null, not false. No C2PA absence, validity or trust claim is inferred from an unperformed check.

There is **no target-owned authorship decision policy**. `decision` and `overall_score` are omitted.
Execution status must not be converted into accepted/rejected. Please report execution and available axis observations only; false-accept/false-reject rates are not supported.

## Included examples and outputs

- `examples/csec-wav/request.json` and `response.json`: schema-compliant public-profile request/actual response with a generated PCM WAV.
- `examples/valid-generated/`: four-node native music graph with three relationship observations.
- `examples/invalid-hash/`: valid request structure with one modified media object; returns error.
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
