# Program Correctness Group — Test Plan

**Owner:** Program correctness (system) group
**Scope:** the whole program, from submitted files to the final JSON report — `run.py`, `music_target/engine.py`,
`music_target/contract.py`, `music_target/audio_diagnostics/runner.py`, `schemas/scoring-response.schema.json`,
and the boundaries between the audio and scoring components.
**Deliverable:** an *End-to-End Acceptance Matrix* (template at the end of this file).

> The audio group tests the **sensors**, the scoring group tests the **dashboard**. This group drives the
> **whole car**: does everything still work when it is connected, and does connecting it break any boundary?

You do not re-test individual formulas or algorithms. You test **system behaviour**: what goes in through
the real entry point, what comes out, and whether the parts respect each other's boundaries.

---

## 1. The most important acceptance rule

> **Same request, same files, same scoring policy — switching audio analysis on or off must not change
> any core scoring result.**

"Core result" means: `axes`, `workflow_assessment`, `validation`, `coverage`, `unknown_inputs`,
`execution_status`, every non-`AUDIO_*` finding, and every pre-existing `assessment_checks` entry.
Audio analysis may only **add** `audio_*` entries and `AUDIO_*` findings.

### 1.1 Worked example — audio on/off parity (black-box, through the CLI)

```bash
# Core only
python3 -B run.py --native examples/audio-diagnostics/request.json \
    --root examples/audio-diagnostics --output /tmp/core.json

# Same input with audio analysis
python3 -B run.py --native examples/audio-diagnostics/request.json \
    --root examples/audio-diagnostics --audio-analysis --output /tmp/audio.json
```

Then compare:

```python
import json

core = json.load(open("/tmp/core.json"))
audio = json.load(open("/tmp/audio.json"))

for key in ("axes", "workflow_assessment", "validation", "coverage", "unknown_inputs", "execution_status"):
    assert core.get(key) == audio.get(key), f"core field changed: {key}"

core_findings = core["findings"]
audio_core_findings = [f for f in audio["findings"] if not f["code"].startswith("AUDIO_")]
assert core_findings == audio_core_findings, "non-audio findings changed"

for key, value in core["assessment_checks"].items():
    assert audio["assessment_checks"][key] == value, f"assessment_checks.{key} changed"

added = set(audio["assessment_checks"]) - set(core["assessment_checks"])
assert all(k.startswith("audio_") for k in added), f"unexpected new keys: {added}"
print("PARITY OK; audio added:", sorted(added))
```

Run this parity check on **every** case in your matrix, not just one example. The white-box version of
this check is `assert_core_unchanged` in `tests/test_audio_diagnostics.py::AudioIntegrationTests` — reuse it.

---

## 2. What to test

### 2.1 Boundary tests (cross-module)

| ID | Scenario | Expected result |
|---|---|---|
| PC-01 | Audio analysis succeeds | Core result identical to audio-off run (§1) |
| PC-02 | Audio analysis times out (tiny `options.timeout` or expired deadline) | Core result kept; audio rows `reason_code: caller_timeout`; response still schema-valid |
| PC-03 | Audio worker crashes mid-run | Rows finished before the crash are kept; crash recorded with stderr tail; core unchanged |
| PC-04 | Audio worker prints invalid JSON | Diagnostic-only failure; core unchanged |
| PC-05 | Audio backend not installed | Rows `unavailable`; core unchanged; no `error` execution status |
| PC-06 | Invalid audio policy file passed with `--audio-analysis-policy` | Core response is **not** turned into an error |
| PC-07 | Audio similarity leaking into Integrity | Every audio row has `scoring_eligible: false` and `integrity_contribution: null`; Integrity layers contain no audio layer |
| PC-08 | Audio trying to lift a severe cap | A capped Integrity stays capped with audio on |
| PC-09 | A file changes between core assessment and audio snapshot | Snapshot rejected; no analysis of changed content |
| PC-10 | Object path escapes the bundle (`../`) | Rejected; nothing outside the root is read |

### 2.2 End-to-end input handling

| ID | Input | Expected `execution_status` | Expected findings / behaviour |
|---|---|---|---|
| PC-20 | Complete valid submission (`examples/valid-generated`) | `succeeded` | Matches stored `response.json` (except timestamps) |
| PC-21 | CSEC WAV request (`examples/csec-wav`) | `succeeded` | `C2PA_PARSER_UNAVAILABLE` if SDK not installed |
| PC-22 | Final artefact hash mismatch (`examples/invalid-hash`) | `error` | `OBJECT_HASH_MISMATCH`; stops on the final artefact |
| PC-23 | Unsupported final type (`examples/unsupported-png`) | `unsupported` | `UNSUPPORTED_INPUT` |
| PC-24 | **Non-final** object missing | `succeeded` | `MISSING_OBJECT`; that node `not_assessed`; not counted as an integrity contradiction |
| PC-25 | Non-final WAV corrupted | `succeeded` | `WAV_PARSER_FAILED` isolated to that node; independent evidence still assessed; axes `partial` |
| PC-26 | FLAC / MP3 bytes saved as `.wav` | `unsupported` (final) / `succeeded` with node isolated (non-final) | Format reported by **content**, not extension |
| PC-27 | Misspelled field (`sample_rat_hz`) | `succeeded` | `UNKNOWN_FIELD` with suggested spelling; that relationship's checks disabled, not failed |
| PC-28 | Unknown non-final `artefact_type` | `succeeded` | Quarantined; reported in `coverage` / `unknown_inputs`; axes `partial` |
| PC-29 | Malformed JSON / dangling hash / duplicate node | `error` | Clear error message, no stack trace |
| PC-30 | Evaluation exceeds `options.timeout` | `timeout` | `TIMEOUT` finding |
| PC-31 | Object over 512 MiB, or bundle over 1 GiB | `unsupported` (final) / isolated (non-final) | `OBJECT_SKIPPED_SIZE_LIMIT` |

### 2.3 Report consistency

For **every** response in the matrix, check automatically:

1. It validates against `schemas/scoring-response.schema.json`.
2. No `NaN` / `Infinity` anywhere in the JSON.
3. Each axis in `axes` has a matching entry in `assessment_checks.score_explanations`, and the numbers agree.
4. Every failed check mentioned in an explanation also appears as a failed fact, and vice versa.
5. `null` means "not assessed" — never shown or treated as 0.
6. No overall score, accept/reject verdict, or human-vs-AI verdict appears (these are design boundaries).

### 2.4 C2PA — real integration, not a stub

A test that hand-writes `"trust": "Trusted"` into a fixture is **not** a C2PA integration test. For C2PA:

- Install `requirements.txt`, which pins the required `c2pa-python==0.37.10`.
- Use real signed files: one valid, one with the asset modified after signing, one with an untrusted certificate.
- Expected: valid → attestation available; modified → validation failure reported; untrusted → not reported as trusted.
- Run the same three files **without** the SDK installed: attestation must be `unavailable` with
  `C2PA_PARSER_UNAVAILABLE`, never inferred as present or absent.
- Supporting-node manifests must not raise the final artefact's `attestation_strength`.

---

## 3. Worked example — one full scenario, step by step (PC-25)

**Goal:** a corrupted supporting WAV must be isolated; the rest of the submission must still be assessed.

1. **Copy** `examples/valid-generated/` to a scratch folder. It has four WAVs:
   `audio/raw-track`, `audio/stem`, `audio/mix` (non-final) and `audio/master` (final).
2. **Truncate** the non-final `audio/raw-track` to its first 100 bytes and **re-hash it**, so file binding
   still passes. (If you skip the re-hash you are testing PC-22-style hash mismatch, not PC-25.)
   ```python
   import hashlib, json, pathlib
   root = pathlib.Path("scratch")
   path = root / "request.json"
   text = path.read_text()
   request = json.loads(text)
   old = request["chain"]["artefacts"][0]["artefact_hash"]          # the audio/raw-track
   relative = request["objects"][old]
   data = (root / relative).read_bytes()[:100]                        # truncated RIFF
   new = hashlib.sha256(data).hexdigest()
   (root / relative).unlink()
   (root / relative.replace(old, new)).write_bytes(data)
   path.write_text(text.replace(old, new))                            # hash, objects map and edges
   ```
3. **Run:**
   ```bash
   python3 -B run.py --native scratch/request.json --root scratch --output scratch/out.json
   ```
4. **Check:**
   ```python
   r = json.load(open("scratch/out.json"))
   codes = [f["code"] for f in r["findings"]]
   assert r["execution_status"] == "succeeded"          # not error: the broken file is non-final
   assert codes.count("WAV_PARSER_FAILED") == 1         # isolated to that one node
   assert codes.count("WAV_OBSERVATION") == 3           # the other three WAVs, incl. the final, still parsed
   assert r["axes"]["integrity"]["availability"] == "partial"
   ```
   Verified on commit `588d7e5`: `succeeded`, one `WAV_PARSER_FAILED`, three `WAV_OBSERVATION`,
   Integrity `partial`.
5. **Repeat with audio analysis on** and run the §1 parity comparison.
6. **Record** the row in the acceptance matrix (§5), including the commit hash and both profile IDs.

Make each scenario a small reusable fixture folder under `tests/fixtures/` (or generated in a test's
`setUp`, as `tests/test_audio_diagnostics.py::fixture` does) so the matrix can be re-run on every change.

---

## 4. How to run

```bash
# Full regression suite, failing (not skipping) if audio backends are missing
REQUIRE_AUDIO_BACKENDS=1 python3 -B -m unittest discover -s tests -v

# Public CSEC bundles (writes to reports/)
python3 -B check_public.py /path/to/public-bundles
```

---

## 5. End-to-end acceptance matrix (template)

| Case ID | Input bundle | Audio | Expected status | Expected key findings | Actual | Parity with audio off | Schema valid | Pass? | Commit | `profile_id` | Audio profile SHA | Failure reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PC-20 | valid-generated | off | succeeded | — | succeeded | n/a | ✅ | ✅ | `588d7e5` | default-v4 | n/a | — |
| PC-20 | valid-generated | on | succeeded | `AUDIO_*` only added | succeeded | ✅ | ✅ | ✅ | `588d7e5` | default-v4 | `…` | — |
| PC-02 | audio-diagnostics, timeout 1 s | on | succeeded | `caller_timeout` rows | … | … | … | … | … | … | … | … |

Every row should be reproducible from the recorded input, commit, and policy versions alone.
