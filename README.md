# Music Evidence Evaluator

The evaluator checks the evidence submitted for a music production: audio
files, production documents, metadata and C2PA Content Credentials, linked in
a declared evidence graph. It binds every file by SHA-256, runs the parser and
checks for each declared type, and reports four separate results:
**completeness**, **integrity**, **attestation strength** and **AI
disclosure**.

It deliberately produces no overall score, no accept or reject decision and no
human-versus-AI classification. A `null` result means "not assessed", never
zero. Optional audio diagnostics are report-only and never change a score.

The [technical manual](Manual_layout_check.docx) is the complete reference.
This page gives the essentials and points to the relevant manual section.

## Current version

| Item | Value |
|---|---|
| Version | **0.8.3** (28 October 2026; summary refinement 29 October 2026) |
| Scoring profile | `default-v7` (`policies/scoring_profile.json`) |
| `system.source_revision` | `sha256:db9f07a3a2eb8a977a19ec3bf9f43bbfbd6698608cc2b760e1a526c458451172` |
| CSEC baseline | Tag `csec-2026-10-27` (0.8.2, `sha256:90716ba4…b021`); evaluation behaviour is the same in 0.8.3 |
| Tests | Latest full run: 559 passed, 0 skipped (30 October 2026) |
| Coverage | 75.3% lines, 65.7% branches (28 October 2026) |
| Python | 3.11 or later (verified on CPython 3.13.7, macOS) |

`source_revision` is a SHA-256 over the runtime Python, requirements,
policies, schemas and bundled XSD files; it is not the Git commit. A response
with a different value was produced by different code or policies. Changes by
version are in [CHANGELOG.md](CHANGELOG.md).

## Documentation

| Document | Contents |
|---|---|
| [Technical manual](Manual_layout_check.docx) | Complete program description: workflow, types, checks, scoring, C2PA (Section 8), limitations (Section 10) |
| [CSEC handoff](program/docs/03_CSEC_HANDOFF.md) | Integration instructions and supported boundaries |
| [Report guide](program/docs/UI_REPORT_0.8.3.md) | The Full assessment screen, audio outcome mapping and export rules |
| [Audio diagnostics](program/docs/AUDIO_DIAGNOSTICS.md) | Optional dependencies, providers, output fields, declared ranges and content beyond the final |
| [C2PA trust lists](program/policies/c2pa_trust/README.md) | Pinned lists, known AI signers, the five AI music generators checked, update procedure |
| [Forge cost](program/docs/FORGE_COST.md) | How `default-v7` grades checked evidence and the measurements behind it |
| [Audio calibration](program/docs/AUDIO_CALIBRATION_2026-10-20.md) and [false-match benchmark](program/docs/FALSE_MATCH_BENCHMARK_2026-10-21.md) | Calibration method, held-out results and unrelated-but-similar pairs |
| [FLAC and MP3 plan](program/docs/FLAC_MP3_SUPPORT_PLAN.md) | Format support decisions and test results |
| [Project Rock findings](program/docs/PROJECT_ROCK_FINDINGS_2026-10-22.md) | Results on real post-production material and the fixes they led to |
| [Physical diagnostic policy](program/docs/PHYSICAL_SIMILARITY.md) | Why the legacy signal checks are disabled |
| [Sheet formats](program/docs/SHEET_FORMATS.md) | Supported CompSheet and CueSheet formats |
| [Test traceability](program/docs/testing/04_TEST_TRACEABILITY.md) | Each test plan case mapped to its tests |
| [Verification record](program/reports/verification.md) | Dated verification results |
| [Future work](program/docs/NEXT_TASKS.md) | P0–P3 roadmap |

## Quick start

Run all commands from `program/`. The core requirements include the C2PA SDK
(`c2pa-python`), `pyaaf2` and `soundfile`; the audio libraries are optional.

```bash
cd program
python3 -m pip install -r requirements.txt
python3 -B run.py --native examples/valid-generated/submission.json --root examples/valid-generated
```

The example chains four audio nodes (`raw-track -> stem -> mix -> master`) and
should return `execution_status: succeeded`, four WAV observations, three
`RELATIONSHIP_INTEGRITY_OBSERVATION` findings and three policy-disabled
physical placeholders. It is a software-generated teaching fixture, not
evidence of human-recorded creation.

Two failure boundaries:

```bash
python3 -B run.py --native examples/invalid-hash/request.json --root examples/invalid-hash
python3 -B run.py --request examples/unsupported-png/request.json --root examples/unsupported-png
```

The first returns `error` (hash mismatch), the second `unsupported`. A
returned, schema-valid response always exits with code 0; read
`execution_status` for the outcome. Exit code 2 means no valid response could
be produced.

**CSEC requests and bundles.** The CSEC example uses the supplied candidate
request format with one WAV and invents no roles:

```bash
python3 -B run.py --request examples/csec-wav/request.json --root examples/csec-wav
python3 -B run.py --bundle /path/to/EVAL-case/bundle.json --root /path/to/EVAL-case
```

**Audio diagnostics (optional).**

```bash
python3 -m pip install -r requirements-audio.txt
python3 -B make_audio_diagnostic_example.py
python3 -B run.py --native examples/audio-diagnostics/request.json \
  --root examples/audio-diagnostics --audio-analysis \
  --output examples/audio-diagnostics/response.json
```

`requirements-audio-lock.txt` records the tested environment. FFmpeg is not
needed. Dependency origins and licences are in
`music_target/audio_diagnostics/vendor/UPSTREAM.json`. A custom local policy
is enabled with `--audio-analysis-policy policies/audio_analysis_policy.json`.

**Desktop manifest builder.** Install `requirements.txt`, make sure Tkinter is
available, then run `python3 builder_gui.py`. The builder imports and hashes
files in the background, helps declare the workflow and relationships, runs a
preflight with the evaluator's limits (128 artefacts, 512 MiB per object,
1 GiB per request) and a full assessment through the same production worker
as the command line, using a verified temporary snapshot. Audio diagnostics
are on by default there (untick to skip); without the audio packages the
result is the core assessment plus a note. Creation method takes `recorded`,
`synthesised`, `ai-generated` or a JSON list; sound reports and AAF edits are
available as document types; FLAC and MP3 capability notes are shown on import.

Portable exports copy files into `objects/<sha256>.<extension>`; originals are
never renamed or overwritten. A manifest can instead reference the originals
(`options.local_file_references`). Evaluating it directly needs both that
option and an explicit flag, which works only for native manifests, never for
CSEC or public bundles:

```bash
python3 -B run.py --native /path/to/submission.json \
  --root /path/to/submission-folder --allow-local-files
```

## What the evaluator reports

| Axis | Measures | Does not measure |
|---|---|---|
| `completeness` | Applicable required and conditional evidence for the selected workflow (16 workflow policies) | Whether a production event took place |
| `integrity` | Consistency across files, declared parameters, relationship parameters and available cross-evidence | Who created the material or when |
| `attestation_strength` | The C2PA reading and validation level of the final artefact | Whether signed claims are true |
| `ai_disclosure` | Coverage of `creation_method` declarations, shown as **Creation-method disclosure coverage** | AI content; no detector runs and no AI probability is given |

Each axis carries `availability`, `value` (0–1 or `null`), `confidence` (not a
probability) and `reasoning`. `assessment_checks.score_explanations` explains
every axis; `assessment_checks.integrity_decision` records why Integrity was
released, withheld, contradicted or incomplete, with the contradiction count.

## How it works, in brief

**Declarations are authoritative.** The submitter's types, relationships,
workflow and parameters drive evaluation. The program never infers a type
from a file, never creates or corrects a relationship, and never fills in a
missing parameter. Missing evidence lowers availability and confidence; it is
not a contradiction. *Manual Section 1.*

**Input errors are reported, never corrected.** A problem with the final
artefact (hash, path, size or content) stops the request with `error`, or
`unsupported` for the size limit or an unsupported container. A problem with
another artefact or relationship isolates only what depends on it and marks
the otherwise available axes `partial` (`SCORE_AFFECTED_BY_INPUT_ERRORS`). An
unknown non-final type or relationship is quarantined, misspelled fields get a
suggested spelling (`UNKNOWN_FIELD`), and a file that changes during
evaluation is re-evaluated once in isolation
(`OBJECT_CHANGED_DURING_EVALUATION`). *Manual Section 1, "Unknown and invalid
input".*

**Integrity.** Three layers with equal weights: file integrity (hash binding,
readability, a full scan of every sample, the bext/iXML timestamp agreement,
the FLAC MD5), declaration consistency (declared technical values and
relationship ranges against the files) and cross-evidence support (RIN, ERN,
MIDI, sheets, C2PA, sound reports, AAF, DAW sessions; one vote per claim and
subject). A value is released only with at least two populated layers and an
assessed fact from a qualifying evidence tier (`signed_attestation` or
`recorder_metadata`); an ordinary contradiction is reported but never
releases a number. A configured severe contradiction releases
`min(weighted mean, 0.25)`. Confidence reflects how many layers were checked
and, under `default-v7`, how hard the checked evidence would be to forge.
Consistency is not authentication: recorder metadata can be rewritten with the
audio and rehashed. *Manual Section 7.*

**C2PA.** Binding, signature and signer identity are checked separately;
revocation is never checked because validation runs offline. Trust comes only
from pinned, hash-verified lists in `policies/c2pa_trust/`. Trust is
asymmetric: an AI-generation label is accepted from any valid signature or a
known AI signer (BytePlus Lumina, Suno), while a claim supporting human
authorship needs a trusted signer. Every state of the final manifest produces
one plain-language `C2PA_FINAL_ATTESTATION` finding. *Manual Section 8.*

**Audio diagnostics.** With `--audio-analysis` (or in the builder),
audfprint, SpectroMap, librosa, `music_semantics` and a bandwidth check report
candidate offsets, supported windows, structure, musical correspondence and
where each file's audio stops, on declared audio relationships only. They name
declared excerpt windows the audio does not support and recorder-take content
the final lacks. Every row has `scoring_eligible: false` and
`integrity_contribution: null`; a content match is not provenance. Each row
carries its calibration status with case counts and 95% Wilson intervals; on
Project Rock Ep01 they add about 20–30 s and up to about 815 MB of memory to a
0.9 s core run. *Manual Section 6.*

**Formats.** WAV/BWF is the assessed format. FLAC and MP3 are accepted in
every role and follow the same rules; a check a file cannot support is
reported as not assessed, never as a contradiction. FLAC encoded with
`flac --keep-foreign-metadata` keeps the recorder evidence and gives the same
result as WAV. A delivered FLAC or MP3 can be declared `derived_from` its WAV
original with `transformation: format_conversion`. *Manual Section 4.*

**Reading results.** The builder's Full assessment page shows a five-section
summary (Evaluation Status, Core Evidence Consistency, Review Required,
Coverage & Limitations, Other Key Results), then the important finding, the
four dimensions and the detailed evidence. **Core Evidence Consistency** is
the display label of `integrity`. **Save result JSON…** keeps the original
response unchanged; **Save review report…** writes an offline HTML report.
*Manual Section 1, "Reading assessment results", and the
[report guide](program/docs/UI_REPORT_0.8.3.md).*

## Verification

```bash
REQUIRE_AUDIO_BACKENDS=1 python3 -B -m unittest discover -s tests -v
python3 -B check_public.py /path/to/public-bundles
```

Both run locally and transmit nothing; the second writes results to
`reports/`. `REQUIRE_AUDIO_BACKENDS=1` fails instead of skipping when an audio
library is missing; the structure benchmark also needs `mir_eval` from
`requirements-benchmark.txt`, and the three Suno real-sample tests need
`C2PA_SUNO_SAMPLE_DIR`. Run the `test_builder.GuiTests` cases one per process:
they can hang inside the full suite on macOS Tk.

On 30 October 2026, 559 tests passed with no skips: 551 non-GUI tests in one
process (45 s) and the eight GUI tests separately. Results by date are in
[`reports/test-results.txt`](program/reports/test-results.txt). The synthetic tests are
engineering checks, not real-session accuracy calibration.

**Coverage.** `reports/coverage/` holds the line and branch coverage
(`index.html`, `coverage-summary.txt`). To reproduce it, install `coverage`
and run the suite in the same split:

```bash
cd tests && REQUIRE_AUDIO_BACKENDS=1 python3 -m coverage run --branch --parallel-mode --data-file=../.coverage --source=music_target,builder_operations,builder_results,builder_review,builder_report,builder_gui,manifest_logic,relationship_modes,run,check_public,wav_probe --omit='*/vendor/*' -m unittest $(ls test_*.py | grep -v test_builder.py | sed 's/\.py$//') test_builder.ManifestLimitTests test_builder.ExportTests test_builder.AssessmentTests
```

Then run each `test_builder.GuiTests.<name>` the same way in its own process,
and combine:

```bash
python3 -m coverage combine && python3 -m coverage html -d reports/coverage && python3 -m coverage report
```

On 28 October 2026 (538 tests) coverage was 75.3% of lines and 65.7% of
branches; 85% without the policy-disabled legacy signal modules
(`physical_audio_passes.py`, `audio_derivation.py`). Core evaluator and
scoring 91%, audio diagnostics 93%, parsers 82%, desktop builder 75%
(`builder_review.py` 94%, `builder_report.py` 100%, `builder_gui.py` 63%,
because its Tk widgets are tested end to end). Code run in builder worker
subprocesses is not counted.

## Directory overview

Paths are relative to `program/`. Every module is described in Section 9 of
the technical manual.

| Location | Purpose |
|---|---|
| `run.py` | Command-line entry point and core worker; adds the optional audio stage |
| `music_target/` | Engine, passes, parsers, scoring, C2PA assessment and the optional `audio_diagnostics/` stage |
| `legacy_parsers/` | RIN, ERN and C2PA parsers, XSD validation and the pinned trust-list check |
| `builder_*.py`, `manifest_logic.py`, `wav_probe.py` | Desktop manifest builder, result screen and offline report |
| `policies/` | Scoring profile, workflow policy, audio policies, calibration status and C2PA trust lists |
| `schemas/` | CSEC candidate schemas, local music schemas and pinned DDEX XSD files |
| `vendor/libevchain/` | Pinned evidence-graph library |
| `benchmarks/` | Public dataset, false-match and forge-cost benchmarks |
| `examples/` | Runnable requests, fixtures and stored responses |
| `tests/` | 21 test modules, 559 cases; all passed on 30 October 2026 |
| `reports/` | Test results, verification record, release record and coverage report |
| `docs/` | Detailed documents listed under [Documentation](#documentation) |

## Open decisions

The local executable baseline is complete, but the directory must not yet be
described as a complete music-authenticity assessment system. Decisions that
remain with the client and CSEC:

- Which recordings a workflow requires, which substitutes are acceptable, and
  whether `min_supported_asset_share` should gate release.
- Whether the weighting of Integrity checks matches expectations, including
  recorder-tier contradictions and the `default-v7` forge costs.
- The accuracy a report-only audio diagnostic must reach before it may affect
  a score.
- Whether an optional online mode may fetch remotely hosted C2PA manifests,
  which further AI generators to list, and how often the trust lists are
  reviewed.
- Whether the optional C2PA `validation` fields need a new response
  `schema_version` (it stays `0.2-candidate`).

The full list is in [`program/docs/TEAM_NOTES.md`](program/docs/TEAM_NOTES.md)
and the roadmap in [`program/docs/NEXT_TASKS.md`](program/docs/NEXT_TASKS.md).
