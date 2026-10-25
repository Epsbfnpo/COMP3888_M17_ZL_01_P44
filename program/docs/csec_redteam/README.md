# CSEC adversarial testing package

Status: **ready to send, 25 October 2026.** Prepared for CSEC's adversarial
testing of the music-domain evaluator.

**Pinned baseline:** Git tag `csec-2026-10-25`; `system.version` 0.8.1;
scoring profile `default-v7`; `system.source_revision`
`sha256:ac048707f1bc29f53bc8ffa2ca27d8e121f6da0008fd397dddfc3bea4b9cbf5c`. Every response carries the last two in `system`; please record them
in each report.

| Document | Content |
|---|---|
| [ATTACK_GOALS.md](ATTACK_GOALS.md) | What the evaluator claims, attack goals G1–G8 with success criteria, known weaknesses, scope, test material, rules of engagement |
| [NATIVE_INPUT_GUIDE.md](NATIVE_INPUT_GUIDE.md) | How to build native requests: layout, types, relationships, workflows, limits, a worked example, reading the response |
| [pack_native_request.py](pack_native_request.py) | Packs ordinary files into a case folder and `request.json` |
| [COST_RECORD_TEMPLATE.md](COST_RECORD_TEMPLATE.md) | Time, skill, compute and resulting evidence strength for every attack attempt |
| [ISSUE_REPORT_TEMPLATE.md](ISSUE_REPORT_TEMPLATE.md) | Defect report and how we handle it |

## What we ask

All goals can be tested on the pinned baseline. We most want the forge-cost
goal (G8) tested: `default-v7` grades Integrity confidence by how hard the
checked evidence would be to forge, using costs we measured on one
production (`docs/FORGE_COST.md`). Your cost records are the independent
check on those numbers, so please fill one in for every attempt, successful
or not. We would value your feedback before our next client meeting, so that
we can answer it there. Contact: Hongjie Deng.

## Test data

CSEC already holds the client's Project Rock dataset; it is not
redistributed with this package. Its sound reports contain crew contact
details, which must stay out of reports.

## Install check

On 25 October 2026 a clean export of the pinned code was installed into a new
virtual environment (CPython 3.13.7, macOS) with `requirements.txt`,
`requirements-audio.txt` and `requirements-benchmark.txt`, as described in
[NATIVE_INPUT_GUIDE.md](NATIVE_INPUT_GUIDE.md) ("Running the target"). Its
responses carry the pinned `source_revision`, and the test suite passed
(without `requirements-benchmark.txt`, 3 of the 5 structure-benchmark tests
fail under `REQUIRE_AUDIO_BACKENDS=1`, as they need `mir_eval`). See
`reports/verification.md`.
