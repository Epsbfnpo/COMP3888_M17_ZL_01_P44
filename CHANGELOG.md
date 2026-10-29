# Changelog

Changes to the music evidence evaluator, newest first. The
[README](README.md) and the [technical manual](Manual_layout_check.docx)
describe the current behaviour; this file records when each change arrived.
Unless stated otherwise, paths are relative to `program/`.

## 0.8.3 summary refinement (29 October 2026, no version change)

- The assessment summary has five sections, in this order: Evaluation Status,
  Core Evidence Consistency, Review Required, Coverage & Limitations and Other
  Key Results. The core section names its scope (file identity and
  readability, declared technical properties and relationships, and
  cross-evidence consistency) and states that optional audio analysis is
  excluded from that score.
- A withheld score states its reason. Audio provider task counts are kept
  apart from interval-level review items. Missing C2PA, untrusted signers,
  workflow requirements and creation-method declarations each have their own
  explanation; missing records stay unknown.
- "Qualified recording cross-checks" is now worded as source cross-evidence
  coverage.
- The Full assessment page scrolls on small windows (mouse wheel, Page Up/Down)
  and **View findings** moves to the detailed findings panel. The GUI, the
  HTML report and the copied text use the same sections.
- Source digest, scoring, policies and response JSON unchanged. 555 tests
  passed with no skips (17 new summary tests; 547 non-GUI in 43 s and eight GUI
  cases). Coverage was not re-measured. Log:
  `reports/assessment-summary-2026-10-29-tests.txt`.

## 0.8.3 (28 October 2026)

Presentation-only release. The response schema, the `axes.integrity` field,
score values, confidence formulas, thresholds, policies and audio diagnostic
records keep their meaning. The program version and its source digest
(`sha256:db9f07a3…1172`) identify this release; only `music_target/__init__.py`
differs from 0.8.2. The 0.8.2 CSEC pin remains an earlier baseline.

- The GUI displays **Core Evidence Consistency** for `integrity`. The output
  order is assessment summary, prominent important finding, four dimensions,
  coverage and detailed evidence. Execution completion, core contradictions,
  non-scoring audio review findings, inconclusive comparisons and incomplete
  analysis are stated separately.
- Declared-range results are read from structured outcomes. A linked finding
  and its detail count once; distinct windows stay separate. Reported candidate
  positions and ambiguity are retained.
- **Save result JSON…** preserves the worker response; **Save review report…**
  exports an offline HTML report; **Copy report text** copies the same model.
  Submitted files or options changed after assessment are labelled as a
  previous snapshot.
- New modules `builder_review.py` and `builder_report.py`.
- 538 tests passed, no skips (501 plus 37 new: 34 in `test_builder_review.py`
  and three GUI cases); 530 non-GUI tests in 49 s. Coverage 75.3% lines and
  65.7% branches. Four example replays preserve axes, findings and checks
  (`reports/ui-boundary-0.8.3.json`, `reports/test-results-0.8.3.txt`).
- Test plan traceability: `docs/testing/04_TEST_TRACEABILITY.md` maps the 86
  numbered plan cases to tests (77 automated, 7 partly, 1 manual, 1 none).

## 0.8.2 (27 October 2026, CSEC pin `csec-2026-10-27`)

Answers CSEC's feedback on the 25 October package. Scoring weights, evidence
tiers and the release rule are unchanged; a file check can now fail or be
incomplete where it used to pass. `source_revision`
`sha256:90716ba4d277b654807a335106b054e2de7829c3fa3656f822fad0cad278b021`.

- **Damaged audio fails the file check.** A non-final audio file that is
  malformed or is not audio (truncated RIFF, broken FLAC metadata, no MP3
  frames) fails `audio_file_readable`; audio the parser had to skip (a WAV's
  partial last frame, MP3 sync loss or truncation) fails
  `audio_stream_complete`. Before, an unreadable stem left file integrity at
  1.0. Valid formats the reader does not support (AIFF, RF64, A-law) are not
  counted as damage.
- **Every sample is read.** `audio_samples_valid` reads each bound WAV, FLAC
  and MP3 in full in every evaluation, the final included. A NaN or Infinity
  fails it, with the first one's time and channel. WAV float samples are
  checked as stored. On 236 Project Rock WAVs (3.37 GiB) this took 1.2 s.
- **Unfinished is never a pass.** A scan stopped by the time limit, a file
  change or a missing decoder is `not_completed`; Integrity is then withheld
  with the new status `incomplete` (`release_reason:
  file_checks_not_completed`).
- **bext against iXML.** A recorder file's bext `TimeReference` must equal its
  iXML `TIMESTAMP_SAMPLES_SINCE_MIDNIGHT` (all 233 Project Rock takes agree). A
  shift in bext alone now fails; a shift written into both still passes.
- **Builder.** Audio diagnostics on by default (untick to skip); `run.py` and
  the CSEC path keep `--audio-analysis` opt-in. Creation method accepts a JSON
  list; sound reports and AAF edits can be added.
- **Packaging.** The 25 October archive lacked the two DDEX
  `allowed-value-sets.xsd` files, which changed its `source_revision`
  (`sha256:a3c0a5ed…` instead of the pinned `sha256:ac048707…`) and failed the
  DDEX tests. The 27 October archive is built from Git-tracked files and
  carries a SHA-256 manifest.
- Example responses regenerated; every axis value, confidence and status is
  unchanged. 501 tests passed on a fresh export (496 non-GUI in 59 s, five GUI).

## After 0.8.1 (24–25 October 2026, CSEC pin `csec-2026-10-25`)

Answers the client's first-demo feedback. Only the forge cost changes a number,
and it changes confidence, never the Integrity value.

- **FLAC and MP3** are read in native requests and in the public CSEC profile
  (`audio/flac`, `audio/mpeg`). WAV stays the assessed format; other checks on
  FLAC and MP3 are reported as not assessed. Client decisions of 24 October:
  FLAC and MP3 follow the WAV rules, and recordings do not have to be WAV/BWF.
  `soundfile` 0.14.0 became a core requirement.
- Report-only **bandwidth** provider, **format conversions** with exact sample
  identity, and **format capabilities** per file.
- **Suno** added as a known AI signer; five AI music generators checked.
- **Declared ranges**: `AUDIO_DECLARED_RANGE_NOT_SUPPORTED` names a declared
  window the audio does not support, with the best-supported offset.
- **Content beyond the final**: `AUDIO_TAKE_CONTENT_BEYOND_FINAL` and
  `AUDIO_SOURCE_CONTENT_BEYOND_TARGET`.
- **Scoring profile `default-v6`**: declared technical values scored in
  declaration consistency, `one_vote_per_claim`, `evidence_sources` and
  `support_breadth` (AAF-used takes that were not submitted lower coverage).
- **Scoring profile `default-v7`**: forge cost scales Integrity confidence. On
  Project Rock Ep01 the value stays 1.0 and confidence goes from 0.533 to
  0.427.
- First line and branch coverage report: 73.3% lines, 63.5% branches (466
  tests).
- The forgery runs found the start-timecode gap: the AAF checks only that each
  used range lies inside the recording, so a start timecode moved by one second
  in both the WAV and the sound report was not caught (narrowed in 0.8.2).

## 0.8.1 (22 October 2026)

Applies the fixes from running the Project Rock post-production material
through the evaluator. Only the `mixed_from` change can alter a score.

- Recorder metadata only for files that name their recording.
- AAF-used takes that were not submitted are reported, and so are the AAF's
  other media.
- Contradiction counts stored in `integrity_decision`, leading a contradicted
  Integrity.
- `mixed_from` accepts raw takes (such edges were skipped before).
- Audio diagnostics limited to the source seconds an AAF says it used.
- Warning when a declared source was originated after its target.
- The desktop builder's size limits match the evaluator (it refused files over
  32 MiB before); duplicate builder and `relationship_modes` modules removed
  from `music_target/`.
- Example responses regenerated: since their 0.7.0 snapshots,
  `attestation_strength` reads 0.0 rather than null for files without a C2PA
  manifest, as defined in 0.8.0.
- 23 October 2026: every audio calibration and false-match count gained a 95%
  Wilson interval and a development, held-out or real-material split; no
  score, gate or finding changed.

## 0.8.0 (21 October 2026)

C2PA provenance.

- Pinned, hash-verified trust lists; asymmetric trust; known AI signer
  matching (BytePlus Lumina); the C2PA chunk precondition.
- A `C2PA_FINAL_ATTESTATION` finding for every state of the final manifest;
  Integrity withheld for an unreadable final manifest.
- Insufficient-support audio outcomes, calibration status and the first
  false-match benchmark.
- Checked end to end on BytePlus Lumina output generated on 20 and 21 October
  2026.

## Before 0.8.0

- Revision 9085427 added the required `c2pa-python` dependency, explicit C2PA
  failure findings, the ordinary `final_c2pa_manifest_validates` contradiction
  and isolation of unsearchable `music_semantics` local locations.
- The 19 October 2026 audio verification at `2b4fd69` reported 189 passing
  tests (`reports/audio-diagnostics-verification.json`).
