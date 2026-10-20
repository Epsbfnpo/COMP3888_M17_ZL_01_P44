# Scoring Group — Test Plan

**Owner:** Scoring group
**Modules in scope:** `music_target/evidence_audit.py` (fact aggregation), `music_target/evidence_tiers.py`,
`music_target/assessment_passes.py` (`assess_axes`, release gate, severe cap),
`music_target/scoring_profile.py`, `music_target/score_explanations.py`, `music_target/recorder_metadata.py`.
**Policy in scope:** `policies/scoring_profile.json` — **`profile_id: default-v4`, `profile_version: 4`**.
**Deliverable:** a *Test Report* plus a *Policy Expectation Table* (templates at the end of this file).

> This group tests the car's **dashboard**: given sensor inputs, does the gauge compute and display
> the right number? You do not test whether the sensors are accurate — that is the audio group's job.

---

## 0. Pin the policy version first

Every expected value in this plan assumes **`default-v4`**. Under v4:

- Integrity is the weighted mean of three layers: `file_integrity`, `declaration_consistency`,
  `cross_evidence_support`, all weight **1.0**.
- A number is released only if at least **2** layers are assessed **and** qualifying cross-evidence
  (`signed_attestation` or `recorder_metadata` tier) was assessed.
- An **ordinary** contradiction never releases a withheld number.
- A **severe** contradiction (rules listed in `severe_contradiction_rules`, e.g. `object_hash_bound`)
  releases `min(weighted_mean, 0.25)` even when the value would otherwise be withheld.
- Confidence uses ceiling 0.8 over 3 layers.

Each test must record the `profile_id` it was written against. **Do not mix v3 and v4 expectations.**
If the profile changes, the expected values in this plan must be reviewed, not silently updated.

---

## 1. Two things you are testing — keep them separate

| | Implementation correctness | Policy reasonableness |
|---|---|---|
| Question | Does the code apply the written formula and policy exactly? | Are these weights, caps and release rules right for the project? |
| Answer type | Pass / fail with an exact expected value | A reviewed argument and a team decision |
| Method | Unit and integration tests | Case review, worked scenarios, sign-off |

Passing tests proves only the left column. **A passing test does not mean `confidence` is a probability
of being correct.** It is a coverage-based number defined by the profile.

---

## 2. Unit tests

Skip audio and document parsing. Feed already-decided check results straight into the scoring logic.

### 2.1 Required unit-test cases

| ID | What to test | Input | Expected result |
|---|---|---|---|
| SU-01 | Weighted mean | Released; layers 1.00 / 0.50 / 0.75, equal weights | `(1.00 + 0.50 + 0.75) / 3 = 0.75` |
| SU-02 | Fact de-duplication | The same correct report submitted 20 times | One fact; score identical to submitting it once |
| SU-03 | Contradiction is not diluted | One failed observation + 20 passing copies of the same fact | Fact stays `passed: False`, `conflicting_outcomes: True` |
| SU-04 | Order independence | Same rows, reversed order | Same `fact_id`, same `passed` |
| SU-05 | Renaming is not a new vote | Same fact with a different `cue_id` / `selection_id` | Still one fact |
| SU-06 | Different recordings stay different | Same rule on `take1` and `take2` | Two facts |
| SU-07 | Release gate, ordinary contradiction | Insufficient qualifying evidence + an ordinary contradiction | Value `null`, status `contradicted`, `release_reason: insufficient_qualifying_cross_evidence` |
| SU-08 | Severe cap lowers | Weighted mean 0.75, severe rule triggered | Value **0.25**, `value_lowered: true` |
| SU-09 | Cap is a ceiling, not an assignment | Weighted mean 0.10, severe rule triggered | Value stays **0.10**, never raised to 0.25 |
| SU-10 | Non-scoring finding | `RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED` present | `severity: info`, `scoring_eligible: false`, `integrity_contribution: null`; Integrity unchanged |
| SU-11 | Unavailable tier cannot promote | Assessed `submitter_document` + unassessed (`passed: None`) `signed_attestation` | `assessed_evidence_tiers == ["submitter_document"]` |
| SU-12 | Profile validation | Duplicate key, unknown layer, weight ≤ 0, cap outside [0, 1], rules without cap, `denominator_layers ≠ 3` | Each raises `ScoringProfileError` |
| SU-13 | Profile without severe release (v3 behaviour) | Remove `release_capped_value_on_severe_contradiction` | Severe case returns `null`, `severe_contradiction_release: disabled` |

### 2.2 Worked example — SU-02 / SU-03 fact aggregation

```python
import unittest
from music_target.evidence_audit import aggregate_cross_evidence

ROW = {"rule": "sound_report_start_timecode_matches_recorder", "audio_hash": "take",
       "evidence_tier": "recorder_metadata", "passed": True}


class FactAggregationExample(unittest.TestCase):
    def test_twenty_identical_copies_count_once(self):
        once = aggregate_cross_evidence([dict(ROW, evidence_hash="report-0")])
        twenty = aggregate_cross_evidence([dict(ROW, evidence_hash=f"report-{i}") for i in range(20)])
        self.assertEqual(len(once), 1)
        self.assertEqual(len(twenty), 1)
        self.assertEqual(once[0]["passed"], twenty[0]["passed"])

    def test_one_failure_is_not_diluted_by_twenty_passes(self):
        rows = [dict(ROW, evidence_hash="bad", passed=False)]
        rows += [dict(ROW, evidence_hash=f"good-{i}") for i in range(20)]
        facts = aggregate_cross_evidence(rows)
        self.assertEqual(len(facts), 1)
        self.assertIs(facts[0]["passed"], False)
        self.assertTrue(facts[0]["conflicting_outcomes"])
```

### 2.3 Worked example — SU-01, SU-08, SU-09 release and cap arithmetic

The release / cap logic currently lives **inline** inside `assess_axes()`
(`assessment_passes.py`, around the `severe_contradiction_cap` handling). That makes SU-08 and SU-09 hard to
unit-test in isolation, because `assess_axes()` needs a whole evidence graph.

**Recommended action for this group:** propose extracting a pure helper, e.g.

```python
def release_integrity(layer_values, layer_weights, layers_assessed, qualifying_support,
                      severe_rules_triggered, profile) -> dict:
    """Returns {"value", "release_reason", "cap": {...} | None}."""
```

so the policy can be tested as a table:

```python
CASES = [
    # layers (file, decl, cross)  qualifying  severe   expected value  expected reason
    ((1.00, 0.50, 0.75),          True,       False,   0.75,           "qualifying_cross_evidence"),
    ((1.00, 0.50, 0.75),          True,       True,    0.25,           "qualifying_cross_evidence"),  # cap lowers
    ((0.10, 0.10, 0.10),          True,       True,    0.10,           "qualifying_cross_evidence"),  # cap does NOT raise
    ((1.00, 0.00, None),          False,      False,   None,           "insufficient_qualifying_cross_evidence"),
    ((0.00, 1.00, None),          False,      True,    0.25,           "severe_contradiction_cap"),
]
```

Until that refactor lands, cover these cases through `assess_axes()` with a minimal graph (see
`test_legacy_support_key_is_an_alias_without_a_contradiction_bypass` in `tests/test_scoring_remediation.py`
for how to build one) or through the integration tests in §3. Either way, assert on the
`severe_contradiction_cap` record: `weighted_mean`, `cap`, `value_lowered`, `triggered_rules`.

---

## 3. Integration tests

Start from real submission files and run the whole scoring chain:

```text
WAV + sound report + declaration + file hashes
  → parse files
  → produce check facts
  → assign evidence tiers
  → de-duplicate and aggregate
  → apply release gate and cap
  → output value, status and explanation
```

This catches errors that unit tests cannot: *the formula is right, but an upstream pass dropped a failing
check, or put evidence in the wrong tier.*

### 3.1 Required integration-test cases

| ID | Scenario | Expected result |
|---|---|---|
| SI-01 | One correct sound report + BWF recorder WAV | Integrity released (≈ 1.0), `release_reason: qualifying_cross_evidence`, origin-not-authenticated **info** finding |
| SI-02 | SI-01 + 1 / 3 / 10 / 30 cosmetic copies (good and bad) | Integrity value, confidence and `cross_evidence_support.check_count` identical to baseline |
| SI-03 | Sound report with wrong start timecode | Fact `passed: False`; Integrity < 1; `integrity_decision.status: contradicted`; explanation lists the contradiction **once** |
| SI-04 | Declared output sample rate changed to 44100 on a 22050 Hz file | Ordinary contradiction; value stays `null` if it was `null` before |
| SI-05 | Source file bytes tampered after hashing | Severe `object_hash_bound` triggers; value `min(weighted_mean, 0.25)`; `release_reason: severe_contradiction_cap` |
| SI-06 | Required evidence removed | `insufficient_evidence` status; no number released |
| SI-07 | Explanation consistency | For every response: the numbers in `score_explanations` equal the numbers in `axes` and `integrity_layers` |
| SI-08 | Creation-method disclosure | Complete "recorded" and complete "ai-generated" declarations both give 1.0; `content_ai_detection_performed: false` |

### 3.2 Worked example — SI-03 "change one fact, watch the score move"

This is the most useful pattern for the scoring group. Score a good submission, change **one field**,
score again, and check that *exactly* the expected things changed.

```python
import tempfile, types, unittest
from pathlib import Path
import run
from music_target.contract import validate
import test_production_documents as pd


class OneFactChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.harness = types.SimpleNamespace(folder=self.folder, take=pd.recorder_wav(self.folder, *pd.TAKE))

    def report(self, timecode):
        row = ["2T01.WAV", "2", "01", "00:00:02", timecode, '"MixL"', '"MixR"', '"Boom"', "note"]
        digest = pd.store(self.folder, pd.sound_report("18/12/24", [row]), "csv")
        return digest, "text/sound-report", "csv"

    def score(self, documents):
        request = pd.ProductionDocumentTests.request(self.harness, documents)
        response = run.evaluate(request, self.folder, "native")
        validate(response, "scoring-response.schema.json")
        return response

    def test_wrong_timecode_contradicts_and_lowers_integrity(self):
        good = self.score([self.report("08:49:43:00")])     # matches the recorder
        bad = self.score([self.report("08:49:45:00")])      # 2 s off

        # 1. Step 1 baseline: released and consistent
        self.assertAlmostEqual(good["axes"]["integrity"]["value"], 1.0)
        self.assertEqual(good["assessment_checks"]["integrity_decision"][0]["status"], "consistent")

        # 2. The changed fact is failed, and only that fact
        facts = {row["rule"]: row for row in bad["assessment_checks"]["cross_evidence_scored_facts"]}
        self.assertIs(facts["sound_report_start_timecode_matches_recorder"]["passed"], False)

        # 3. Score and status moved in the policy-defined direction
        self.assertLess(bad["axes"]["integrity"]["value"], 1.0)
        self.assertEqual(bad["assessment_checks"]["integrity_decision"][0]["status"], "contradicted")

        # 4. Arithmetic matches the layers
        layers = [r for r in bad["assessment_checks"]["integrity_layers"]
                  if r["contributes_to_integrity"] and r["value"] is not None]
        expected = sum(r["value"] * r["weight"] for r in layers) / sum(r["weight"] for r in layers)
        self.assertAlmostEqual(bad["axes"]["integrity"]["value"], expected)
```

Repeat the same pattern for: duplicating the report (SI-02), removing the report (SI-06), tampering
the source file (SI-05). Existing examples to copy from: `tests/test_scoring_remediation.py`
(`ProductionScoringRegressionTests`, `ReleaseAndDisclosureTests`).

---

## 4. Policy review (not a pass/fail test)

For each rule in the profile, write one short scenario and ask the team "is this the outcome we want?"

| Policy item | Scenario to review | Current v4 outcome | Team decision |
|---|---|---|---|
| Equal layer weights | File integrity perfect, declarations half wrong | 0.75 | keep / change |
| Severe cap = 0.25 | Hash mismatch on a supporting file, everything else perfect | 0.25 | keep / change |
| Severe releases without qualifying support | Hash mismatch, no recorder metadata | 0.25 released (v3 withheld) | keep / change |
| Recorder metadata counts as qualifying | Unsigned BWF timecode agrees with sound report | Released, with info finding | keep / change |
| Confidence ceiling 0.8 | All three layers assessed | ≤ 0.8 | keep / change |

Record the decision, who made it, and the date. If a decision changes the profile, bump
`profile_version` and update every affected expected value in §2–§3.

---

## 5. How to run

```bash
python3 -B -m unittest tests.test_scoring_remediation tests.test_production_documents -v
python3 -B -m unittest tests.test_target -v -k integrity
```

---

## 6. Templates

**Test report (one row per case)**

| Case ID | Level | Input / change | Expected | Actual | Pass? | Code commit | `profile_id` | Failure reason |
|---|---|---|---|---|---|---|---|---|
| SU-03 | Unit | 1 fail + 20 pass copies | 1 fact, `passed: False` | 1 fact, `passed: False` | ✅ | `588d7e5` | default-v4 | — |
| SI-05 | Integration | Source bytes tampered | `min(mean, 0.25)`, reason `severe_contradiction_cap` | 0.25 | ✅ | … | default-v4 | — |

**Policy expectation table** — the §4 table, filled in and signed off.
