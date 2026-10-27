# CSEC adversarial testing package

Status: **ready to send, 27 October 2026** (replaces the 25 October package).
Prepared for CSEC's adversarial testing of the music-domain evaluator.

**Pinned baseline:** Git tag `csec-2026-10-27`; `system.version` 0.8.2;
scoring profile `default-v7`; `system.source_revision`
`sha256:90716ba4d277b654807a335106b054e2de7829c3fa3656f822fad0cad278b021`. Every response carries the last two in `system`; please record them
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

## Changes since the 25 October package

- The two DDEX schema dependencies (`schemas/ddex/ern-4.3/allowed-value-sets.xsd`
  and `schemas/ddex/rin-2.1/allowed-value-sets.xsd`) are in the archive. They
  were in the tag but were lost when the earlier archive was made. Without
  them `source_revision` is `sha256:a3c0a5ed…` instead of the pinned value,
  and the DDEX tests fail; that explains both differences you reported.
- Version 0.8.2: damaged or unreadable audio fails the file check; every
  audio file is read in full and NaN/Infinity fails (`audio_samples_valid`);
  a scan that does not finish is `not_completed` and Integrity becomes
  `incomplete` instead of passing; bext `TimeReference` is compared with the
  iXML timestamp. Scoring weights, tiers and the release rule are unchanged.
  Details: `docs/03_CSEC_HANDOFF.md`, "Changes on 27 October 2026".
- The Suno real-sample tests can run: set `C2PA_SUNO_SAMPLE_DIR` to the
  `suno_samples/` folder shipped beside `evaluator/`.

## Install check

On 27 October 2026 a clean export of the pinned code was installed into a new
virtual environment (CPython 3.13.7, macOS) with `requirements.txt`,
`requirements-audio.txt` and `requirements-benchmark.txt`, as described in
[NATIVE_INPUT_GUIDE.md](NATIVE_INPUT_GUIDE.md) ("Running the target"). Its
responses carry the pinned `source_revision`, and all 501 tests passed with
no skips (`C2PA_SUNO_SAMPLE_DIR` set to the shipped Suno samples; the five GUI
tests run one per process). `requirements-benchmark.txt` is needed: under
`REQUIRE_AUDIO_BACKENDS=1` the structure-benchmark tests need `mir_eval`. See
`reports/verification.md`.
