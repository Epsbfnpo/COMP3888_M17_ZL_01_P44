# Issue report

Use one report per defect. A result that matches a known weakness in
[ATTACK_GOALS.md](ATTACK_GOALS.md) Section 3 belongs in a cost record, not an
issue, unless it shows the weakness is worse than documented.

```markdown
## Summary
One sentence: what the evaluator reported, and why that is wrong.

## Goal
G1 | G2a | G2b | G2c | G3 | G4 | G5 | G6 | G7

## Severity (tester's view)
- critical: crash, path escape, network access, or a non-schema-valid response
- high: a fabricated or misrepresented package reported as consistent
  evidence by a check that claims to cover it, or genuine evidence reported
  as contradicted
- medium: a wrong finding, wrong availability or partial status, or a hidden
  input error, without a wrong axis value
- low: wording, a misleading explanation, or a report-only diagnostic

## Target
- system.version:
- system.source_revision:
- Python version and OS:
- Command line (including --audio-analysis if used):

## Reproduction
- Bundle: path or archive name (request.json plus objects/), or the script
  that generates it. Project Rock files: name or SHA-256 only; do not attach
  the client's files.
- Steps:
  1.
  2.

## Observed
Quote the relevant response fields: execution_status, the affected axis
(value, status, availability), finding codes, and the
assessment_checks entry involved.

## Expected
What the response should have said, and which documented behaviour this
relies on (README, technical manual or this handoff).

## Determinism
Ran N times on the same version: same result / different results.

## Cost record
COST-<case id>.md, if this came from an attack attempt.
```

## What happens next

We reproduce the report on the stated `source_revision`, confirm or reject it
with a reason, and for a confirmed defect add a regression test with the
fix. The fix lists the issue in `reports/verification.md`. Please keep the
bundle so the case can be rerun against the fixed version.
