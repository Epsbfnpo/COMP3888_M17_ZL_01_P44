# CSEC adversarial testing package

Status: **draft, 23 October 2026.** Prepared for CSEC's adversarial testing of
the music-domain evaluator. Not yet sent.

| Document | Content | Depends on scoring update |
|---|---|---|
| [ATTACK_GOALS.md](ATTACK_GOALS.md) | What the evaluator claims, attack goals G1–G7 with success criteria, known weaknesses, scope, test material, rules of engagement | G1 and G3 thresholds, pinned version |
| [NATIVE_INPUT_GUIDE.md](NATIVE_INPUT_GUIDE.md) | How to build native requests: layout, types, relationships, workflows, limits, a worked example, reading the response | No (response fields may be added) |
| [pack_native_request.py](pack_native_request.py) | Packs ordinary files into a case folder and `request.json` | No |
| [COST_RECORD_TEMPLATE.md](COST_RECORD_TEMPLATE.md) | Time, skill and compute record for every attack attempt | No |
| [ISSUE_REPORT_TEMPLATE.md](ISSUE_REPORT_TEMPLATE.md) | Defect report and how we handle it | No |

## What can start now

Goals G4 (robustness), G5 (hidden input errors) and G7 (determinism) do not
depend on scoring and can be tested on version 0.8.1. Goals G1–G3 should be
reported against the pinned version. The scoring update (`default-v6`) is
merged, and `default-v7` (25 October 2026) adds forge-cost-graded confidence
without changing values; only the version pin and the G1/G3 thresholds are
outstanding.

## Test data

CSEC already holds the client's Project Rock dataset; it is not
redistributed with this package. Its sound reports contain crew contact
details, which must stay out of reports.

## Still to fill in

- **[PENDING PIN]** Pinned version, Git tag and `source_revision`; G1 and G3
  thresholds. Response fields added by `default-v6` are listed in the native
  input guide.
- Our technical contact: Hongjie Deng.
- **[PENDING TEAM INPUT]** The CSEC contact, the collaboration mode, the
  testing window and where issues are filed.
- A fresh-environment install check of the pinned version.
