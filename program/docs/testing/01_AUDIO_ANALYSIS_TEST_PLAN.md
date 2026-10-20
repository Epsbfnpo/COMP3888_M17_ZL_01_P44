# Audio Analysis Group — Test Plan

**Owner:** Audio analysis group
**Modules in scope:** `music_target/audio_diagnostics/` — providers `audfprint`, `spectromap`,
`librosa_structure`, `structure`, `music_semantics`, `alignment`, `calibration`, plus `worker.py`,
`runner.py`, `io.py`, `policy.py`.
**Policies in scope:** `policies/audio_analysis_policy.json`, `policies/audio_calibration_profile.json`
(frozen; its `freeze_sha256` is checked at runtime).
**Deliverable:** a *Test and Calibration Report* (template at the end of this file).

> Think of this group as testing the car's **sensors**. You check that each sensor reading is
> computed correctly (unit tests), that the sensor is wired to the rest of the car correctly
> (integration tests), and that the sensor is accurate on real roads (dataset evaluation and calibration).

---

## 1. Three levels of testing

| Level | Question it answers | Needs real audio? | Needs Lumina? |
|---|---|---|---|
| **Unit test** | Is this one rule or calculation written correctly? | No — hand-built arrays | No |
| **Integration test** | Do the decoder, librosa / fingerprint backends, adapters and JSON output work together? | Yes — a WAV file (synthetic or locally derived) | No |
| **Dataset evaluation / calibration** | Does it work on *many kinds* of real music, and how often does it fail or abstain? | Yes — a labelled dataset | Optional (source material only) |

Passing levels 1 and 2 does **not** prove the analysis works on music in general. That is what level 3 is for.

Using Python's `unittest` does not make a test a *unit* test. Classify each test by **which components it
exercises**, not by the framework. For example, in `tests/test_audio_calibration.py`,
`AlignmentGateTests` are unit tests, while `CalibratedBackendTests` (which runs real librosa) are integration tests.

---

## 2. Unit tests

Feed a function a controlled input and check one decision. No decoding, no worker process, no Lumina.

### 2.1 Required unit-test cases

| ID | What to test | Function | Input | Expected result |
|---|---|---|---|---|
| AU-01 | A04 alignment collapse | `music_semantics._path_quality` + `calibration.path_rejections` | A path that maps 12 s of target onto 0.46 s of source | Rejected with `duration_ratio_outside_profile` and `excessive_beat_collapse` |
| AU-02 | A05 speed fit | `music_semantics._aligned_tempo_candidate` | Source times `[0, 6, 12]` s matched to target `[0, 5, 10]` s | `ratio ≈ 1.2` |
| AU-03 | Two speed estimators disagree | `calibration.qualify_comparison` | Time-grid ratio 1.12, beat-aligned ratio 0.94 | `status == "abstained"`, reason `tempo_estimators_disagree`; no speed released |
| AU-04 | Ambiguous transposition | `calibration.qualify_comparison` | Two pitch candidates with identical similarity | Reason `ambiguous_transposition`; no forced transposition |
| AU-05 | Constant material is not split | `structure.detect_sections` | Feature matrix that never changes | Exactly one section `[[0, 40]]`; no fixed 16-beat phrases |
| AU-06 | Boundary at a non-grid time survives small noise | `structure.detect_sections` | Changes at 11.5 s and 30.5 s, plus Gaussian noise σ = 0.005 | Boundaries within ±0.5 s of truth, before and after noise |
| AU-07 | Feature parameters outside calibration | `calibration.qualify_comparison` | `hop_length` changed from the frozen value | Reason `feature_parameters_outside_calibration` |
| AU-08 | Calibration profile tampering | `calibration.profile` | Edit one value in `audio_calibration_profile.json` without updating the digest | `ValueError("Audio calibration profile digest mismatch")` |
| AU-09 | Range validation | `time_ranges.content_range_checks` | Negative, NaN, reversed, zero-length and past-EOF ranges | Each rejected with a specific reason |
| AU-10 | Report-only boundary | `contracts.validate_result` | A provider result with `scoring_eligible: true` | Rejected by the protocol |

Several of these already exist (see `tests/test_audio_calibration.py`, `tests/test_remediation.py`).
Your job is to **review them, fill the gaps in the table, and record each one in the report.**

### 2.2 Worked example — AU-02 speed fit

The target was played faster: 12 s of the original fit into 10 s. The fitted ratio
(source seconds per target second) should therefore be 12 / 10 = 1.2.

```python
import unittest
import numpy as np
from music_target.audio_diagnostics.providers.music_semantics import _aligned_tempo_candidate


class SpeedFitTests(unittest.TestCase):
    def test_known_time_mapping_gives_ratio_1_2(self):
        source = {"times": np.array([0.0, 6.0, 12.0])}   # original song, seconds
        target = {"times": np.array([0.0, 5.0, 10.0])}   # sped-up version, seconds
        path = np.array([[0, 0], [1, 1], [2, 2]])         # source index i ↔ target index i

        estimate = _aligned_tempo_candidate(path, source, target)

        self.assertAlmostEqual(estimate["ratio"], 1.2, places=6)
        self.assertLess(estimate["residual_seconds"], 1e-9)
        # A raw candidate is never a released value on its own:
        self.assertEqual(estimate["status"], "candidate_not_calibrated")
```

**How to extend it:** add a negative case. With only two matched points the function must return
`ratio: None` and `status: "insufficient_alignment"` instead of guessing.

### 2.3 Worked example — AU-03 estimators disagree

Reuse the `comparison()` helper in `tests/test_audio_calibration.py::AlignmentGateTests`, then change
only the two speed estimates:

```python
comparison = AlignmentGateTests.comparison()
comparison["candidates"][0]["aligned_tempo_candidate"] = {"ratio": 0.94}   # beat-based
comparison["time_grid_tempo"] = {"ratio": 1.12}                            # time-grid based
decision = qualify_comparison(comparison, DEFAULTS["music_semantics"])
assert decision["status"] == "abstained"
assert "tempo_estimators_disagree" in decision["rejection_reasons"]
```

The point of this test: **disagreement must produce abstention, not an average.**

---

## 3. Integration tests

Give the pipeline a real WAV file and let several components run together:

```text
original WAV
  → decode and resample (io.py)
  → feature extraction (librosa / fingerprint tokens)
  → alignment
  → accept / abstain gate (calibration.py)
  → diagnostic JSON (contracts.py)
```

### 3.1 What every integration test must check

Not only "did it produce output", but:

1. The file was read (`analysis_complete` is `true`, or a specific `reason_code` explains why not).
2. Transposition and speed are **inside a tolerance agreed before the run**, *or* the result explicitly abstains (`null` value + rejection reasons).
3. Declared crop ranges are converted back to the **original file timeline**
   (e.g. a crop declared at source 1–7 s yields matches whose source windows lie in 1–7 s).
4. Output validates against the result contract; **no NaN or Infinity** anywhere.
5. Missing dependency, timeout and backend crash each return a clear status (`unavailable`, `caller_timeout`, …), never a stack trace.
6. `scoring_eligible` is `false` and `integrity_contribution` is `null` on every row.

### 3.2 Required integration-test cases

| ID | Scenario | Expected result |
|---|---|---|
| AI-01 | Original vs. same audio sped up 1.12× and pitched +2 semitones | `tempo_ratio` within ±0.03 of 1.12 and `transposition_semitones == 2`, **or** both `null` with reasons |
| AI-02 | A 20 s excerpt cut from a 180 s source at 97.3 s | Both `audfprint` and `spectromap` find offset 97.3 s ± 0.15 s |
| AI-03 | Declared crop `source 1–7 s → target 0–4 s` | All supported source windows lie inside 1–7 s |
| AI-04 | Only `source_start_seconds` declared | `reason_code == "incomplete_declared_ranges"`; nothing inferred |
| AI-05 | Silent file | `status == "unavailable"`, `reason_code == "silent_signal"`; no contradiction |
| AI-06 | A sustained single tone | No transposition or tempo released (both `null`) |
| AI-07 | Same source paired with two different targets | Source section boundaries are identical in both runs |
| AI-08 | librosa uninstalled (or mocked absent) | Row is `unavailable`, not a failed scored parser |
| AI-09 | Beat tracker returns zero beats | Falls back to time grid (`insufficient_beats_using_time_grid`), no division by `None` |

### 3.3 Worked example — AI-01 speed + pitch change

Requires `pip install -r requirements-audio.txt`. Run with `REQUIRE_AUDIO_BACKENDS=1` so a missing
backend **fails** instead of silently skipping.

```python
import tempfile, unittest
from pathlib import Path
import librosa
from music_target.audio_diagnostics.policy import DEFAULTS
from music_target.audio_diagnostics.worker import execute
from test_audio_diagnostics import tone_sequence, write_task

RATE = 22050
TEMPO_TOLERANCE = 0.03          # agree this BEFORE running; record it in the report


class SpeedPitchIntegrationTests(unittest.TestCase):
    def test_known_dsp_transform_is_recovered_or_abstained(self):
        original = tone_sequence(24, rate=RATE)
        derived = librosa.effects.time_stretch(original, rate=1.12)          # 1.12× faster
        derived = librosa.effects.pitch_shift(derived, sr=RATE, n_steps=2)  # +2 semitones

        with tempfile.TemporaryDirectory() as folder:
            task = write_task(Path(folder), original, derived, rate=RATE)
            value = execute(task, "music_semantics", DEFAULTS["music_semantics"])

        self.assertTrue(value["analysis_complete"], value)
        self.assertFalse(value["scoring_eligible"])
        self.assertIsNone(value["integrity_contribution"])

        metrics = value["metrics"]
        if metrics["tempo_ratio"] is None:
            # Abstention is acceptable, but it must explain itself.
            self.assertEqual(metrics["transposition_status"], "abstained")
            self.assertTrue(metrics["alignment_quality"]["rejection_reasons"])
        else:
            self.assertAlmostEqual(metrics["tempo_ratio"], 1.12, delta=TEMPO_TOLERANCE)
            self.assertEqual(metrics["transposition_semitones"], 2)
```

Conventions to remember when writing expected values:

- `tempo_ratio` is **source seconds per target second**. A target that is 1.12× faster gives **1.12**.
- `transposition_semitones` is target minus source. Target pitched up 2 semitones gives **+2**.
- Record whether the case *recovered* or *abstained*. Both are legal; a **wrong released value** is the failure.

---

## 4. Dataset evaluation and calibration

A single passing example proves nothing about music in general. This level measures accuracy,
error, misses and abstentions on a labelled dataset, with a strict train/test discipline.

### 4.1 What to evaluate

| Capability | Ground truth source | Metric(s) to report |
|---|---|---|
| Section boundaries | Human annotations (e.g. SALAMI) | Hit rate at ±0.5 s and ±3 s, precision, recall, F-measure |
| Speed / pitch / crop | Deterministic local DSP transforms with recorded parameters | Absolute error of released values, abstention rate |
| Source recovery in mixes | Known mixes (local mixing, Slakh / MUSDB stems) | Recall of each source, offset error |
| False positives | Hard controls: unrelated songs, same key / tempo, shuffled harmony | False-release rate |

**Always report four numbers together:** correct, wrong (released but outside tolerance), missed, abstained.
A method that abstains on everything has zero errors and is useless; a method that never abstains hides its errors.

### 4.2 Rules for ground truth

- **Lumina prompt values are not ground truth.** A prompt asking for "120 BPM, ABA form" is a *request*.
  Store it under `requested_properties`; only verified facts go in `verified_labels`.
- Allowed label methods: `manual_review`, `deterministic_transform`, `independent_measurement`, `dataset_annotation`.
- The analyzer under test can never label its own data.
- A known time-stretch labels the exact factor; it does **not** establish verse/chorus labels.

### 4.3 Partition discipline

1. Split by **source family** (an original and all its derivatives share one family). Mixes list every parent family.
2. Use the **development** set to choose thresholds.
3. **Freeze** the profile (`audio_calibration_profile.json` + `freeze_sha256`) and commit it.
4. Only then run the **held-out test** set, once. Do not retune on test results; if you must, the old test
   set becomes a regression set and you need a new held-out set.

### 4.4 Worked example — one calibration run

```bash
# 1. Validate the manifest (offline, never calls Lumina, checks partitions and hash leakage)
python3 -B benchmarks/audio_dataset.py data/manifest.json

# 2. Tune on development data
python3 -B benchmarks/audio_calibration.py data/manifest.json --partition development --mode semantics --out results/dev

# 3. Freeze the profile, commit it, write down the SHA256

# 4. Evaluate the held-out set exactly once
python3 -B benchmarks/audio_calibration.py data/manifest.json --partition test --mode semantics --out results/test
```

A manifest row for a locally derived case:

```json
{
  "case_id": "song03-fast112-up2",
  "family_ids": ["song03"],
  "partition": "test",
  "audio_sha256": "<sha256 of the derived file>",
  "parent_case_ids": ["song03-original"],
  "requested_properties": {},
  "verified_labels": [
    {"property": "tempo_ratio", "value": 1.12, "method": "deterministic_transform",
     "annotator_or_tool": "librosa 0.11.0 time_stretch(rate=1.12)"},
    {"property": "transposition_semitones", "value": 2, "method": "deterministic_transform",
     "annotator_or_tool": "librosa 0.11.0 pitch_shift(n_steps=2)"}
  ]
}
```

See `benchmarks/README.md` for the full manifest schema and `docs/AUDIO_CALIBRATION_2026-10-20.md`
for the current calibration history (including the N04_fast failure that led to the version-two profile).

---

## 5. How to run

```bash
python3 -B -m unittest tests.test_audio_calibration tests.test_remediation -v
REQUIRE_AUDIO_BACKENDS=1 python3 -B -m unittest tests.test_audio_diagnostics -v
```

---

## 6. Report template (one row per case)

| Case ID | Level | Input | Expected | Actual | Pass? | Code commit | Policy / profile SHA | Failure reason |
|---|---|---|---|---|---|---|---|---|
| AU-02 | Unit | src `[0,6,12]`, tgt `[0,5,10]` | ratio 1.2 | 1.2000 | ✅ | `588d7e5` | n/a | — |
| AI-01 | Integration | tone_sequence(24) → ×1.12, +2 st | 1.12 ± 0.03 & +2, or abstain | abstained (`tempo_estimators_disagree`) | ✅ | … | `freeze_sha256=…` | — |

For dataset runs, add a summary table per capability and partition:
`n cases | correct | wrong | missed | abstained | median error | profile SHA`.
