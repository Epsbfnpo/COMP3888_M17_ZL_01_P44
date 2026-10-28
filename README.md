# Start Here: Music Evidence Chain and CSEC Integration

This directory contains an implemented baseline for assessing evidence from music production workflows. It was assembled from the two local projects supplied for this work on 2026-09-10. No code from `audio_evchain` was read, reused, or modified.

The main approach is to reuse Ben's evidence graph and execution framework, connect the music parsers and checks, and expose a runnable interface for CSEC.

The current version is **0.8.3** (28 October 2026); see
[Version 0.8.3](#version-083-28-october-2026) below. It follows the released
0.8.2 (27 October 2026) and 0.8.1
(22 October 2026), which builds on 0.8.0, which
focused on C2PA provenance: pinned trust lists, asymmetric trust and a
plain-language finding for every state of the final artefact's C2PA manifest
(see [C2PA provenance and trust](#c2pa-provenance-and-trust)). Optional
report-only audio diagnostics now carry their calibration status and the
results of a first false-match benchmark. Audio diagnostics do not change any
score, weight, confidence, withholding rule or cap. LLM integration is outside
this update.

Version 0.8.1 applies the fixes from running the Project Rock post-production
material through the evaluator. It narrows recorder metadata to files that name
their recording, reports AAF-used takes that were not submitted and the AAF's
other media, stores contradiction counts in `integrity_decision` and leads a
contradicted Integrity with them, lets `mixed_from` take raw takes, limits
audio diagnostics to the source seconds an AAF says it used, and warns when a
declared source was originated after its target. Only the `mixed_from` change
can alter a score: such edges are no longer skipped. See
[Evidence tiers](#evidence-tiers) below and
[`program/docs/PROJECT_ROCK_FINDINGS_2026-10-22.md`](program/docs/PROJECT_ROCK_FINDINGS_2026-10-22.md).

**FLAC and MP3 (after 0.8.1).** Besides
WAV/BWF, the evaluator now reads FLAC and MP3 (MPEG Layer III), in native
requests and in the public CSEC profile (`audio/flac`, `audio/mpeg`). FLAC keeps
nearly every WAV check, including recorder metadata kept by
`flac --keep-foreign-metadata`, and adds an MD5 check of the decoded audio. MP3
is lossy, so checks that need a bit depth, recorder metadata or an exact length
are reported as unavailable rather than as contradictions. See
[FLAC and MP3](#flac-and-mp3) below. Scoring follows the same rules as WAV
under the default profile (rules of `default-v6`, unchanged in `default-v7`): technical claims are scored in declaration
consistency, and the FLAC MD5 check in file integrity. **WAV is the assessed
format**: FLAC and MP3 keep the checks implemented today, and every other
check on them is reported as not assessed (see the scope limitation below).

**After 0.8.1 (24-25 October 2026).** Four changes answer the client's
first-demo feedback. Only the last changes a number, and it changes
confidence, never the Integrity value.

- A declared excerpt window the audio does not support is named
  (`AUDIO_DECLARED_RANGE_NOT_SUPPORTED`), with the best-supported offset found
  by a whole-source search. Report-only.
- Recorder-take content the final lacks is reported: the seconds an AAF does
  not use (`AUDIO_TAKE_CONTENT_BEYOND_FINAL`), or, without an AAF, source
  content with no match in the target (`AUDIO_SOURCE_CONTENT_BEYOND_TARGET`).
  A stem separated out of the final cannot hold such material. Report-only.
- A line and branch coverage report of the full test suite
  (`program/reports/coverage/`).
- Scoring profile `default-v7` grades how hard the checked cross-evidence would
  be to forge (`forge_cost`) and scales Integrity confidence by it. The costs
  were measured by forging claims in Project Rock copies and checked against
  stated rankings; the tiers, release gate, weights and value are unchanged.
  See [`program/docs/FORGE_COST.md`](program/docs/FORGE_COST.md). On Project
  Rock Ep01 the value stays 1.0 and confidence goes from 0.533 to 0.427.

The forgery runs also found a gap: the AAF checks only that each used range
lies inside the recording, so a start timecode moved by one second in both the
WAV and the sound report was not caught. Version 0.8.2 closes the cheapest form
of it (see below); the remaining fixes are listed in
[`program/docs/NEXT_TASKS.md`](program/docs/NEXT_TASKS.md).

### Version 0.8.3 (28 October 2026)

This release changes the desktop GUI and human report presentation. The response
schema, `axes.integrity` field, score values, confidence formulas, thresholds,
policies and audio diagnostic records retain their existing meanings. The program
version and its source digest identify this release; the 0.8.2 CSEC pin remains
an earlier baseline.

The GUI displays **Core Evidence Consistency** for `integrity`. The output order
is assessment summary, prominent important finding, four dimensions, coverage and
detailed evidence. Execution completion, core contradictions, non-scoring audio
review findings, inconclusive comparisons and incomplete analysis are stated
separately. A high core value does not validate every audio interval.

Declared-range results are read from structured outcomes. A linked finding and
its detail count once; distinct windows stay separate. Reported candidate
positions and ambiguity are retained. Qualified recording checks show their
numerator and denominator and distinguish consistent from contradicted recordings.

**Save result JSON…** preserves the worker response; no summary or display field
is added. **Save review report…** exports an offline HTML report from the same
presentation model. **Copy report text** uses that model too. Submitted files or
options changed after assessment are labelled as a previous snapshot.

**Coverage:** 75.3% lines and 65.7% branches, including the new presentation modules.

**Validation:** 538 tests passed, no skips (the original 501 plus 37 new tests); 530 non-GUI tests and eight GUI tests run separately. Four example replays preserve the original axes, findings and checks. See `program/reports/test-results-0.8.3.txt` and `program/reports/ui-boundary-0.8.3.json`.

See [the report guide](program/docs/UI_REPORT_0.8.3.md) and the updated technical
manual appendix for the screen, field mappings and regression checks.

### Version 0.8.2 (27 October 2026)

These changes answer CSEC's feedback on the 25 October package. Scoring
weights, evidence tiers and the release rule are unchanged; a file check can
now fail or be incomplete where it used to pass.

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
  checked as stored, before any conversion. On 236 Project Rock WAVs
  (3.37 GiB) this took 1.2 s; results are not cached across runs.
- **Unfinished is never a pass.** A scan stopped by the time limit, a file
  change or a missing decoder is `not_completed`. The file-integrity layer
  then has no value, Integrity is withheld and `axes.integrity.status` is the
  new value `incomplete` (`release_reason: file_checks_not_completed`). The
  builder shows "Incomplete" and one "Check incomplete" row per file.
- **bext against iXML.** A recorder file's bext `TimeReference` must equal
  its iXML `TIMESTAMP_SAMPLES_SINCE_MIDNIGHT` (they agree on all 233 Project
  Rock takes). A start timecode shifted in bext alone now fails; a shift
  written into both still passes, so the gap is narrowed, not closed.
- **Builder.** Audio diagnostics are on by default in the desktop builder
  (untick to skip); `run.py` and the CSEC path keep `--audio-analysis`
  opt-in. Creation method accepts `recorded`, `synthesised`, `ai-generated`
  or a JSON list of these, and sound reports and AAF edits can be added.
- **Packaging.** The 25 October archive sent to CSEC lacked the two DDEX
  `allowed-value-sets.xsd` files, which changed its `source_revision` and
  failed the DDEX tests. The 27 October archive is built from the files Git
  tracks and carries a SHA-256 manifest.

The physical-similarity comparison stays disabled; the sample check reads
each file on its own and compares nothing between files.

The [technical manual](Manual_layout_check.docx) describes the whole program;
its Section 8 covers the C2PA work in detail and Section 10 lists the known
limitations. The runnable evaluator is in
[`program/`](program/). Unless stated otherwise, file locations and commands
below are relative to that directory.

## Team responsibilities

| Team | Core responsibility | Important current boundary |
|---|---|---|
| Ben / library team | Native JSON, evidence nodes and relationships, type registration, analysis-pass execution, and score data containers | A framework alone does not provide music analysis or four-dimension assessment algorithms |
| Music team | Music types, file parsing, cross-evidence checks, domain-level assessment meaning, and the runnable target | Parsers and candidate four-dimension assessments are integrated; real standards-based fixtures and customer policy confirmation are still required |
| CSEC team | Test fixtures, target execution, result recording, and red-team assessment | CSEC needs a reproducible entry point but does not define acceptance or rejection rules for the music team |

## Recommended reading order

1. [Technical manual](Manual_layout_check.docx): the complete program description; Section 8 covers C2PA provenance, trust and findings, and Section 10 lists the known limitations.
2. [CSEC handoff](program/docs/03_CSEC_HANDOFF.md): integration instructions and supported boundaries.
3. [C2PA trust lists](program/policies/c2pa_trust/README.md): pinned lists, known AI signers, the AI music generators checked (Suno, Stability AI, Google Lyria, ElevenLabs, Adobe Firefly) with their limitations, and the update procedure.
4. [Physical diagnostic policy](program/docs/PHYSICAL_SIMILARITY.md): disabled legacy signal checks and the separate non-scoring boundary.
5. [Audio diagnostics](program/docs/AUDIO_DIAGNOSTICS.md): optional dependencies, execution, providers and output fields, including declared ranges and content beyond the final.
6. [Forge cost](program/docs/FORGE_COST.md): how `default-v7` grades checked evidence, the forgery measurements behind the costs, the ranking check and the held-out result.
7. [False-match benchmark](program/docs/FALSE_MATCH_BENCHMARK_2026-10-21.md): unrelated-but-similar Lumina pairs and their results.
8. [Sheet formats](program/docs/SHEET_FORMATS.md): supported CompSheet and CueSheet formats.
9. [Future work](program/docs/NEXT_TASKS.md): consolidated P0-P3 roadmap and unresolved legacy items.
10. [Verification results](program/reports/verification.md): historical core verification and remaining unverified areas.
11. [Audio integration verification](program/reports/audio-diagnostics-verification.json): historical record of 189 passing tests (19 October 2026) and core-result invariance on the repeated-excerpt example.

## Current implementation

- Includes a pinned copy of the library source reviewed for this release, with fixes for audio attributes, binary-file binding, and dictionary attribute checks.
- Registers music audio, MIDI, DAW, RIN, ERN, C2PA, and text evidence types, plus constrained relationship types.
- Uses one streaming RIFF/WAVE reader for structural inspection, metadata parsing, and bounded sample decoding. It supports integer PCM (8/16/24/32-bit), IEEE float (32/64-bit), and integer/float `WAVE_FORMAT_EXTENSIBLE` without loading the whole file.
- Provides a bounded MIDI parser; the RIN, ERN, C2PA, sound report (`text/sound-report`) and AAF (`project/aaf`) parsers are connected to the production execution path.
- The MIDI parser extracts channel and meta events, notes, tempo, time signatures, controllers, and duration in seconds when it can be calculated.
- CompSheet and CueSheet parsers support strict JSON, CSV, delimited text, and PDF text extraction. See [`program/docs/SHEET_FORMATS.md`](program/docs/SHEET_FORMATS.md).
- Runs file, relationship, and cross-evidence analysis through the actual `EvidenceChain` and `Pipeline` implementations.
- `WavMetadataPass` emits standardised technical checks once per audio artefact. The checks that compare submitted technical claims with the file (`technical.*_matches_observed`) are scored in declaration consistency (`declared_file_properties`); `BoundFileIntegrityPass` keeps the checks with no submitter claim: hash binding, readability (`audio_file_readable`, `audio_stream_complete`), the full-length sample check (`audio_samples_valid`), the FLAC MD5 and the bext/iXML timestamp agreement.
- A non-final WAV parser failure is isolated to that node: the program emits `WAV_PARSER_FAILED`, marks only dependent checks unavailable, and continues with independent evidence. When the file is malformed rather than unsupported, `audio_file_readable` also fails in file integrity. An unsupported final WAV still returns `unsupported`.
- `RelationshipIntegrityPass` is a registered libevchain `EvidencePass`. It compares only submitter-declared edge parameters with cached source and target observations and replaces the former diagnostic-only `MusicRelationshipPass`.
- The core assessment does not execute the legacy `PhysicalSimilarityPass`, exact reconstruction, waveform comparison, landmark/DTW matching, source-contribution analysis, residual modelling, master continuity, or excerpt search.
- Keeps one compatibility placeholder per retained relationship. Every placeholder has `comparison_mode: disabled`, `status: unavailable`, `reason_code: disabled_by_policy`, `scoring_eligible: false`, and `integrity_contribution: null`.
- The disabled legacy layer emits no physical-similarity findings. The separate optional diagnostic runner appends `AUDIO_*` findings without affecting integrity; deprecated signal code remains isolated.
- With `--audio-analysis`, audfprint and SpectroMap report candidate content offsets and supported windows, while librosa reports feature correspondences, candidate boundaries and repetition. Their outputs are observations, not independent votes or proof of historical provenance.
- Deprecated sample and proxy helpers remain directly testable. The core assessment decodes each file only to check that its samples are readable and finite (`music_target/sample_validation.py`), never to compare files; optional diagnostic workers read independently verified WAV snapshots.
- Objects remain limited to 512 MiB each and 1 GiB per request, and the caller's wall-clock timeout has no evaluator-side 60-second cap.
- Separates submitter-declared technical properties from measured file properties and reports inconsistencies.
- Requires `workflow_id`, `modifiers`, and submitter declarations in `submission.json`, and loads policies for 16 workflow types.
- `WorkflowCompletenessPass` reports the state of each required, conditional, and optional expectation.
- Reports integrity, C2PA attestation, and AI disclosure independently. The top-level C2PA axis is scoped to the final artefact; supporting-node manifests are reported separately and cannot raise the final score. AI disclosure measures declaration coverage; it does not detect AI-generated content.
- Separates file integrity, structural validity, declaration consistency, CrossEvidence, cryptographic attestation, and a policy-disabled physical diagnostic layer. The physical layer, structural validity, and C2PA do not inflate integrity.
- Provides CSEC request conversion, a standard response contract, timeout control, and explicit `unsupported` and `error` states.
- Applies XML size limits and rejects DTD/entity declarations. RIN 2.1 and ERN 4.3 are validated offline with pinned official XSD files, while XSD status remains separate from best-effort field extraction.
- Binds available RIN `FileReference` and ERN `DeliveryFile` SHA-256 values to recordings/components. Cross-evidence checks cover MIDI, ERN, C2PA ingredients, and AI declaration consistency. Embedded and standalone C2PA manifests are normalised to asset-bound records before their ingredients are checked against the graph.
- Invalid free-form comparison values, such as a textual BPM or non-string ISRC, make only that comparison unavailable; they do not turn the complete request into an execution error.
- Core evaluation hashes each submitted object once before direct binding and caches the source revision once per process. Optional diagnostics separately rehash temporary snapshots to confirm they still correspond to the core-bound bytes.
- Includes successful, hash-error, and unsupported-type examples, and has been run against 11 public CSEC cases.

The four dimensions respect separate evidence boundaries:

- `completeness` assesses applicable expectations for the selected workflow;
- `integrity` assesses consistency across files, declared parameters, relationship parameters, and available cross-evidence;
- `attestation_strength` assesses the available C2PA reading and validation level; and
- `ai_disclosure` retains its wire key for compatibility and is displayed as
  **Creation-method disclosure coverage**. The structured
  `ai_disclosure_coverage` record reports declared/assessed/excluded counts,
  `content_ai_detection_performed: false`, and no AI probability. Complete
  "recorded" and complete "ai-generated" declarations both give coverage 1.0;
  neither verifies whether those declarations are true.

A `null` value means not assessed and must not be interpreted as zero. The system intentionally does not produce an overall score, an acceptance/rejection decision, or a human-versus-AI creator classification. Those omissions are design boundaries, not unfinished functionality.

An unknown non-final `artefact_type` or unknown `relationship_type` is
quarantined instead of aborting the whole evaluation. Known files and
relationships are still checked, while `coverage`, `unknown_inputs`, and
detailed findings report what was omitted. The program does not infer, correct,
or map an unknown value. Every otherwise available axis is downgraded to
`partial` when coverage is incomplete. An unknown final artefact type remains
`unsupported`, and structurally unsafe input such as a dangling hash, duplicate
node, malformed request, or unrelated supported node remains an `error`.
A registered relationship whose endpoint roles are incompatible is isolated
and reported without being applied to the retained graph.

A known non-final WAV whose encoding is unsupported or malformed follows the
same isolation principle at parser level. Its graph node and declaration remain
visible, but WAV-dependent metadata checks are unavailable; the physical
diagnostic placeholder remains policy-disabled for every relationship.
Independent files and evidence continue to be assessed. The final artefact is
strict: if its WAV cannot be parsed by the supported WAV implementation, the
request returns `unsupported` or `error` as appropriate.

Input errors are reported, never corrected. The final artefact is checked
first: if its hash, objects mapping, path, size, or file content cannot be
confirmed, the request stops and reports only the final artefact's problem
(`error`; `unsupported` for the size limit or an unsupported audio container).
A problem with a non-final artefact or a relationship isolates only what
depends on it: an upper-case hash or extension (`INVALID_HASH_FORMAT`,
`INVALID_OBJECT_PATH`), an unknown or misspelled field (`UNKNOWN_FIELD`, with a
suggested spelling), a SHA-256 mismatch (`OBJECT_HASH_MISMATCH`), an object
beyond the size limits (`OBJECT_SKIPPED_SIZE_LIMIT`), or a path outside the
bundle (`OBJECT_PATH_INVALID`). Dependent checks become `unavailable`, workflow
expectations that rely on the node become `not_assessed` rather than missing,
and a closing `SCORE_AFFECTED_BY_INPUT_ERRORS` finding marks every otherwise
available axis `partial`. A misspelled relationship field disables that
relationship's deterministic checks instead of being ignored, so a typo cannot turn
an honest declaration into a contradiction. File content, not the extension,
decides the audio format: FLAC or MP3 data in a `.wav` file is read as FLAC or
MP3, and AIFF, Ogg, RF64/BW64 or MPEG Layer I/II data is reported as that
format and unsupported. If a non-final file changes while it is being
evaluated, the request is evaluated once more with that file isolated
(`OBJECT_CHANGED_DURING_EVALUATION`); a second change, or a change to the final
artefact, returns `error`.

The program does not infer relationships or fill in missing relationship
parameters. It evaluates only graph edges and transformation parameters supplied
in the submission. Missing evidence lowers assessment availability and
confidence; it is not a contradiction and does not directly lower the integrity
value. Integrity confidence reflects how many of the three evidence layers were
checked and, under `default-v7`, how hard the checked evidence would be to forge
(see [Integrity weighting and cap](#integrity-weighting-and-cap)). To see how many checks passed, refer to the integrity value, the layer
results (`assessment_checks.integrity_layers`) and the findings. The legacy
signal layer is disabled for every relationship because its narrow assumptions
can produce misleading results for legitimate production material. No range, offset, gain, channel matrix, contribution, or content
similarity is estimated by the core assessment. When explicitly enabled, the
separate diagnostic workers may estimate candidate offsets and feature
correspondences; these observations never replace submitted parameters.

Each retained relationship remains visible through a compatibility placeholder:

```json
{
  "relationship_type": "edited_from",
  "comparison_mode": "disabled",
  "status": "unavailable",
  "integrity_contribution": null,
  "scoring_eligible": false,
  "analysis_complete": false,
  "threshold_policy": null,
  "content_match_is_provenance": false,
  "reason_code": "disabled_by_policy"
}
```

This placeholder reports policy state, not an attempted analysis failure. It
does not emit a physical finding and cannot change integrity. Optional audio fingerprinting now uses separate report-only providers, enabled
explicitly with `--audio-analysis` or `--audio-analysis-policy`.

## Optional audio diagnostics

From the repository root:

```bash
cd program
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-audio.txt
python3 -B make_audio_diagnostic_example.py
python3 -B run.py --native examples/audio-diagnostics/request.json \
  --root examples/audio-diagnostics --audio-analysis \
  --output examples/audio-diagnostics/response.json
```

In the builder GUI the diagnostics are on by default: the "Include audio
diagnostics" box on the Full assessment tab starts ticked and can be unticked
for a run. If the audio packages are not installed, the builder returns the
core result with a note instead. The command line and the CSEC path are
unchanged: `run.py` adds diagnostics only with `--audio-analysis` (or
`--audio-analysis-policy`). Either way the diagnostics are report-only and the
core result is identical with or without them. On Project Rock Ep01 they add
about 20-30 s and up to about 815 MB of memory to a 0.9 s core run.

Use the existing virtual environment where available. `requirements-audio-lock.txt`
records the tested environment. The vendored audfprint extractor consumes decoded
arrays, so this integration does not require FFmpeg. Dependency origins and
licences are recorded in `music_target/audio_diagnostics/vendor/UPSTREAM.json`.

| Provider | Observation |
|---|---|
| audfprint | Landmark fingerprints, candidate time offsets and supported windows, including repeated-source candidates |
| SpectroMap | Spectral peaks detected in fixed 4-second windows, so density does not depend on file length; exact peak-pair comparison and offset voting |
| librosa | Chroma, MFCC and energy features; candidate boundaries, repetitions and cross-file correspondences |
| bandwidth | Per file: where the decoded audio stops compared with its container, sample rate and MP3 bitrate (heuristic, uncalibrated) |

The parent process first retains the existing core scoring response, then runs
isolated diagnostic workers on retained, declared audio-to-audio edges. It does
not search undeclared files or create graph relationships. CompSheet comparison
requires a matching selection and complete declared ranges. Supported WAV
encodings are read directly; FLAC and MP3 are decoded with soundfile
(libsndfile). For MP3 the decoder's gapless length is used, so diagnostics
compare an approximation of the original recording; the calibration status
lists `mp3_encoded_queries` as not tested. Rerunning the false-match benchmark
on FLAC and MP3 versions (23 October 2026) gave identical results for FLAC; for
MP3 there was still no false match, but audfprint found the shared loop in 0 of
4 half-gain pairs (WAV: 1) and 3 of 4 full-gain pairs (WAV: 4).

When a submitted AAF uses a bound recorder take, the take is analysed only over
the seconds the edit used (`range_source: aaf_source_range`, `selection_id`
`aaf:<mob>:<n>`) against the whole target. The AAF range is tape timecode minus
the take's recorder start; identical ranges from several channel slots are
merged, and a range more than one frame outside the recording is left to the
core `aaf_source_range_within_recorded_timecode` check and not analysed. This is
the only case in which a target range may stay open. A fingerprint match in such
a task is reported as `AUDIO_AAF_RANGE_CORRESPONDENCE` (content agreeing with the
AAF, not provenance), and `audio_diagnostic_summary` counts `aaf_range_tasks` and
`aaf_range_tasks_with_correspondence`. No match is not reported as disagreement.
On Project Rock Ep01, 6 of 14 AAF ranges were found in the WIP mix and a
whole-take false match disappeared.

**Declared ranges (after 0.8.1).** An edge that declares source and target
start and end gets a second, fingerprint-only task that searches the whole
source for the declared target window (`range_source: declared_range_search`).
When calibrated audfprint does not support the declared window at the declared
offset (within 0.25 s), `AUDIO_DECLARED_RANGE_NOT_SUPPORTED` names it: severity
`low` with the best-supported source range when the content is found elsewhere,
`info` when it is not found anywhere or the search did not finish. Details are in
`assessment_checks.audio_declared_range`. On the repeated-excerpt fixture a
declared 7-11 s window is reported as found at 2-6 s, and 2-6 s or 10-14 s give
no finding.

**Content beyond the final (after 0.8.1).** A stem separated out of the final
cannot hold material the final lacks; a recorder take usually does. For each
AAF-used take, the seconds the edit does not use (less 0.5 s handles, in pieces
of at least 3 s; `range_source: aaf_unused_range`) are compared with the same
target. If the take's used ranges were found there (the positive control),
unused seconds with no candidate are reported as
`AUDIO_TAKE_CONTENT_BEYOND_FINAL`; any candidate in an unused piece is
`AUDIO_UNUSED_RANGE_FOUND_IN_FINAL`, with the strongest candidate's size, and is
not a contradiction. Without an AAF, whole-file `mixed_from`, `stemmed_from`,
`edited_from`, `comped_from` and `derived_from` edges get
`assessment_checks.audio_source_coverage`. It compares the source seconds that
carry landmarks with the regions matched in the target and reports uncovered
stretches as `AUDIO_SOURCE_CONTENT_BEYOND_TARGET`. Both are info, report-only
and support a take; they never count against one. On Project Rock Ep01 the 6
takes with a positive control each had 10.1-32.8 s of unused material not
found in the mix, the other 7 were not assessed, and the run took 35.5 s
(previously about 19 s) at a 465 MB peak. In a no-AAF variant a real take had
15.1 s beyond the mix, while two "takes" cut from the mix (one exact, one
band-passed) were contained in it.

Detailed results are object arrays in `assessment_checks.audio_fingerprint`,
`assessment_checks.audio_structure`, `assessment_checks.audio_declared_range`,
`assessment_checks.audio_take_beyond_final`, `assessment_checks.audio_source_coverage`
and `assessment_checks.audio_diagnostic_summary`.
Findings use the `AUDIO_*` prefix and link to details by `diagnostic_id`. Each
observation has `scoring_eligible: false`, `integrity_contribution: null` and
`content_match_is_provenance: false`.

Audfprint and music_semantics now have bounded experimental support/abstention gates
(see `program/docs/AUDIO_CALIBRATION_2026-10-20.md`); the other adapters and local
correspondences remain exploratory. Completed observations stay
`inconclusive`, even when profile support exists; analyses that cannot run are
`unavailable`. Missing dependencies, invalid local policies, provider failures,
silence, changed inputs and exhausted caller time do not alter the completed
core result. A crashed provider's diagnostic includes the tail of its stderr. The synthetic example yields fingerprint candidates near source
2 and 10 seconds for the same four-second excerpt, demonstrating ambiguity
rather than a unique historical source.

The proposed development caps of 30 seconds per diagnostic run, 10 seconds per
provider, 16 relations, 600 seconds per file, 64 MiB active PCM and 1500 feature
frames were not added. Existing file validation and the caller's
`options.timeout_seconds` remain active. Diagnostics use only the remaining
caller time and do not silently truncate a file. A custom local parameter
policy is enabled with `--audio-analysis-policy policies/audio_analysis_policy.json`.

**Calibration status.** Every audio row carries a `calibration` object from
`policies/audio_calibration_status.json`: `bounded_calibration` for audfprint
and music_semantics, `exploratory_uncalibrated` for SpectroMap, librosa and
fingerprint agreement, with case counts, transforms tested and not tested, and
false-match counts. The frozen `audio_calibration_profile.json` is unchanged,
so this explains results without changing them. `case_counts` separates
`development` (cases used to choose or revise the gates), `held_out` (cases
evaluated only after the gates were frozen) and `real_material` (Project Rock,
never used for tuning), and every count, including each false-match category,
carries a 95% Wilson interval (`music_target/case_intervals.py`). For example,
0 of 29 held-out controls supported gives an upper bound of 11.7%, and 15 of 16
held-out mixtures found gives 71.7–98.9%. Cases derived from one family are not
independent, so the real uncertainty is wider; an interval is not a population
rate. A provider that ran but had too
little audio to compare (silence, too few landmarks, peaks or frames, channel
cancellation) reports `AUDIO_INSUFFICIENT_SUPPORT` (outcome
`insufficient_support`) instead of an apparent non-match;
`AUDIO_DIAGNOSTIC_UNAVAILABLE` (outcome `not_run`) remains for providers that
did not run.

**False-match benchmark.** `benchmarks/false_match.py` runs unrelated but
similar pairs through the real evaluator. The 21 October 2026 run
([results](program/docs/FALSE_MATCH_BENCHMARK_2026-10-21.md)) used 23 pairs
from 25 SHA-256-pinned Lumina clips:

| Category | Pairs | Result | 95% Wilson interval |
|---|---|---|---|
| Same genre, tempo and key | 5 | No fingerprint, agreement or semantics match | 0–43% false |
| Same-prompt regeneration | 4 | No match | 0–49% false |
| Cover prompts (melody unverified) | 6 | No fingerprint match; semantics 0/6, left unclassified | 0–39% |
| Shared drum loop, gain 0.5 / 1.0 | 4 / 4 | Loop detected 1/4 / 4/4 at the inserted offset; semantics 0/8 | 5–70% / 51–100% detected |

A shared loop is real shared audio, so detecting it is correct but does not
show derivation. These are case counts on a small sample, not population
false-positive rates; real covers remain untested. The intervals show how
little so few pairs establish: zero false matches in 4 pairs is still
consistent with a false-match rate of up to 49%. The benchmark ran after the
gates were frozen, so it counts as held-out.

## C2PA provenance and trust

Version 0.8.0 separates four questions that one "valid" flag used to mix:
whether the content still matches the signed hash (binding), whether the
signature verifies, whether the signer is known (identity), and whether the
certificate was revoked. The first three are checked. Revocation is never
checked because validation runs offline, so `revocation_status` is always
`not_checked`. Full details are in Section 8 of the
[technical manual](Manual_layout_check.docx).

**Pinned trust lists.** Signer trust comes only from lists stored in
`policies/c2pa_trust/` (see its [README](program/policies/c2pa_trust/README.md)), each
pinned by source commit SHA and file SHA-256 in `trust_lists.json`. The
evaluator refuses to run if a pinned file changes, and every result records
the list versions in `assessment_checks.c2pa_trust_configuration`.

| List | Pin | Use |
|---|---|---|
| C2PA Trust List | `c2pa-org/conformance-public` @ `47009ee` (2026-10-17) | Trust anchors; a signer chaining to them is `trusted_c2pa` |
| C2PA TSA Trust List | same commit | Pinned and recorded only; c2pa-python 0.37.10 has no time-stamp authority setting |
| Interim Trust List (legacy, frozen) | `contentauth/verify-site` @ `83b2e30`; frozen 2026-01-01 | `trusted_legacy_itl` only when the SDK reports a signing time before the freeze |
| Known AI signers | `known_ai_signers.json` (BytePlus Lumina certificate, valid until 2028-03-16; Suno certificate, valid 2026-07-23 to 2028-10-25) | Not a trust list: identifies AI generators whose AI labels are accepted |

**Attestation strength** is ordered by what was verified:

| Final manifest state | Attestation strength | Meaning |
|---|---|---|
| `trusted_c2pa` | 1.0 | Binding and signature verify; signer on the C2PA Trust List |
| `trusted_legacy_itl` | 0.9 | Signer on the frozen ITL and signed before 2026-01-01 |
| `untrusted` | 0.3, partial | Binding and signature verify, signer unknown (anyone can make such a certificate) |
| unclear | 0.25 with a signature, else 0.1, partial | Readable but nothing verified |
| `invalid` | 0.0 | Binding, signature or another check failed |
| absent | 0.0 | No manifest; not evidence against the submission |
| unreadable | null, partial | A `C2PA` chunk exists but no manifest could be read |
| not checked | unavailable | The required SDK is not installed |

Across several final manifests, `invalid` wins, then the strongest trust found.
The `validation` object records `trust_status`, `trust_source`,
`revocation_status` and `signer`; these are optional schema fields, so
`schema_version` stays `0.2-candidate` for CSEC.

**Asymmetric trust.** A self-made certificate still produces a valid
signature, so a valid signature alone identifies no accountable signer.
Evidence that lowers the human share of a work (an AI-generation source type,
or any manifest signed by a certificate in `known_ai_signers.json`, even an
invalid one) is accepted as `signed_attestation`: nobody gains by forging a
claim that only hurts their own assessment. Such manifests report
`C2PA_KNOWN_AI_SIGNER` when the signer is listed, at medium severity if the
artefact was not declared `ai-generated`. A manifest that supports human
authorship needs a trusted signer; a valid but untrusted one is a submitter
document, cannot qualify Integrity for release, and reports
`C2PA_SIGNER_UNTRUSTED`. A trusted signature identifies who signed, not that
the claims are true. Known-signer fingerprints are base64 SHA-256 digests of
the signing certificate; the C2PA SDK ignores other formats silently.

**Findings.** Every assessment reports the final artefact's C2PA state in one
`C2PA_FINAL_ATTESTATION` finding (`final_c2pa_state`: `not_checked`, `absent`,
`unreadable`, `invalid`, `untrusted`, `trusted_c2pa`, `trusted_legacy_itl` or
`unclear`) that states what was found, what it means and its effect on each
score. An invalid manifest also reports `C2PA_CONTENT_BINDING_FAILED` (for
example audio edited after signing), `C2PA_SIGNATURE_INVALID` or
`C2PA_VALIDATION_FAILED`, with every SDK failure code explained in plain
language. These are medium severity for the final artefact and low for
supporting artefacts.

**The C2PA chunk precondition.** A reader failure counts as an unreadable
manifest only when the WAV parser found a `C2PA` chunk; without one the file
simply has no manifest. A `C2PA` chunk with no readable manifest is unreadable
rather than absent. FLAC and MP3 keep a manifest in an ID3v2 `GEOB` frame of
type `application/c2pa` (for FLAC the C2PA SDK places the tag before the
`fLaC` marker), and the same rule uses that frame.

**Effects on Integrity.** An invalid final manifest fails the ordinary rule
`final_c2pa_manifest_validates`: Integrity is marked contradicted, but the
severe cap is not applied. If the final artefact's own manifest cannot be read,
Integrity is withheld (`release_reason: final_attestation_unreadable`) even
when other files carry qualifying evidence; an absent manifest withholds
nothing. `integrity_decision.qualifying_facts` lists the facts that released a
value.

Tested on BytePlus Lumina output (20-21 October 2026): unmodified files are
valid but untrusted (0.3) with an accepted AI label; files edited after signing
are invalid with `C2PA_CONTENT_BINDING_FAILED`, and the AI label is still
accepted. Two Suno MP3 downloads (24 October 2026, separate generations) carry
a valid manifest in an ID3 `GEOB` frame, declare `trainedAlgorithmicMedia` and
are signed by the same certificate (`CN=Suno Content Credentials`, issued by
Suno's private root): they are recognised as Suno, stay untrusted (0.3), and
declaring them `recorded` is a contradiction. Each listed signer was checked on
its own output; this does not validate other signers. See
[`policies/c2pa_trust/README.md`](program/policies/c2pa_trust/README.md) for the
evidence and how to add a signer.

**AI music generators checked (24 October 2026).** Original output from five
generators was checked. A fingerprint is added to `known_ai_signers.json` only
when it is verified on real output and changes the result, because an
unverified one could label audio wrongly.

| Generator | Result in the evaluator | Fingerprint added? |
|---|---|---|
| Suno (two MP3s) | Valid manifest with an AI label, Suno's private certificate: recognised as Suno, `untrusted` (0.3) | Yes |
| Stability AI (WAV) | Valid manifest with an AI-generation declaration, trusted certificate: handled by trust validation | No, not needed |
| Google Lyria (MP3) | Valid, trusted Google manifest with AI-generation metadata: handled by trust validation | No, not needed |
| ElevenLabs (WAV) | No manifest in the file: `absent`. ElevenLabs signs only MP3 from its API, and only when the caller opts in | No, no certificate could be verified |
| Adobe Firefly (WAV) | The file references a valid manifest hosted remotely; validation is offline and does not fetch it, so the file reads as `absent` | No, a fingerprint would not change the result |

Two limitations follow. AI audio that carries no manifest looks the same as
an unsigned human recording to C2PA, and a manifest hosted remotely is not
read. Possible solutions (verifying a signed ElevenLabs API output, an
optional online mode or a submitted `.c2pa` sidecar for remote manifests) are
in the [trust-list README](program/policies/c2pa_trust/README.md#ai-music-generators-checked-24-october-2026).

## Integrity weighting and cap

The active profile is `policies/scoring_profile.json`. Integrity combines three
available score-eligible layers: `file_integrity`, `declaration_consistency` and
`cross_evidence_support`, each with default weight 1.0. `file_integrity` holds
only checks with no submitter claim: hash binding, readability, the
full-length sample check, the bext/iXML timestamp agreement and, for FLAC, the
MD5 of the decoded audio. A file check that did not finish (`not_completed`)
leaves this layer without a value and withholds Integrity, whose status is
then `incomplete`.
`declaration_consistency` holds every comparison of a submitted claim with a
measured property: a file's own technical claims and a relationship's declared
ranges and input/output sample rate and bit depth. A property claimed for the
same file in more than one place is one fact, and it fails if any claim fails
(`declaration_scored_facts`). With `one_vote_per_claim` (on since `default-v6`),
the same claim about one recording repeated in several documents (sound report
and AAF scene/take or track names) is one cross-evidence fact, since all
documents were uploaded by the same user and their independence is unknown.
`assessment_checks.evidence_sources` reports the document count, fact count,
the known common source (the uploader), any trusted C2PA signers, recordings
that several documents agree about and the creator/software fields already
read from WAV and DAW files; `independence` stays `unknown` and the section
never changes a score. `assessment_checks.support_breadth` reports, for each
recording the selected workflow requires (`audio/raw-take`, `audio/raw-track`),
whether qualifying evidence was checked and agreed (`supported`), checked and
disagreed (`contradicted`), or was not found (`no_qualifying_evidence`).
Recorder takes an AAF edit uses that no submitted recording matches join the
same list as `not_submitted`, so leaving out a hard-to-check take lowers the
coverage instead of raising it; a take used several times counts once. When a
submitted recording carries no recorder identity (for example a plain FLAC
take), an unmatched AAF take may be that file, so it is an
`unmatched_reference` and `coverage_scope` is `provisional`. Two ratios share
one denominator: `material_coverage` (required recordings submitted) and
`support_coverage` (required recordings with qualifying evidence checked; also
`checked_share`). A recording that was not submitted is missing material, never
a contradiction. With no required recordings the section is `not_applicable`,
not 0/0. It is report-only by default; an optional
`withholding_rule.min_supported_asset_share` withholds the value
(`insufficient_support_breadth`) when `support_coverage` is below it, counting
contradicted recordings as checked. On Project Rock Ep01 with one AAF-used take
removed it reads 12 of 13. The default withholding rule (`default-v7`, unchanged since `default-v6`)
requires at least two populated layers and an assessed
cross-evidence fact from an allowed tier (`signed_attestation` or
`recorder_metadata`). An ordinary contradiction never releases a number; it is
reported and sets `axes.integrity.status` and `integrity_decision.status` to
`contradicted`, and without qualifying support Integrity stays null. A severe
contradiction is computed by the evaluator and needs no independent support, so
it releases `min(weighted mean, 0.25)` with `release_reason`
`severe_contradiction_cap`.
Confidence is `0.8 * populated_layers / 3 * factor`, where under `default-v7`
`factor = 0.5 + 0.5 * strength / 5` and `strength` is the mean, over the
recordings and documents checked, of the forge cost of the strongest assessed
cross-evidence fact about each: trusted signature 5, recorder metadata 3, an
AAF or DAW session not checked against recorder metadata 2, any other
submitter document or an unbound file 1. The tiers keep their role as the
release gate, and the profile loader rejects costs that would rank a document
above recorder metadata. `assessment_checks.evidence_strength` lists each
subject's cost. A profile without `forge_cost` keeps factor 1. Confidence is
not a probability of authorship, and material or support coverage is reported
beside it, never folded into it.
Consistency does not authenticate origin: recorder metadata (BWF, iXML) can be
rewritten together with the audio and the file rehashed, and a FLAC's stored
MD5 can be rewritten with its audio, so matching checks show agreement between
the submitted files, not who recorded them or when.
Integrity shows that the files and claims have been checked against each other,
but this does not guarantee who created the material, when it was recorded or
that the recording session took place. See
[C2PA provenance and trust](#c2pa-provenance-and-trust) for signed origin
evidence.

### Evidence tiers

Every cross-evidence check records an `evidence_tier`:

| Tier | Source | Qualifies release with the required layer coverage? |
|---|---|---|
| `signed_attestation` | A C2PA manifest from a signer on a pinned trust list (`trusted_c2pa` or `trusted_legacy_itl`); or an AI-generation label from any valid signature or from a known AI generation signer (see below) | Yes |
| `recorder_metadata` | Submitted documents compared with BWF `bext`/iXML fields observed in a hash-bound WAV; their writer is not authenticated | Yes |
| `submitter_document` | RIN, ERN, CompSheet, CueSheet, MIDI, DAW sessions, AAF, sound reports and unverified C2PA, compared with the graph, declarations or measured file properties | No |

C2PA evidence is classified by asymmetric trust; see
[C2PA provenance and trust](#c2pa-provenance-and-trust).

Submitter documents are declarations in another format: the submitter could
export an AAF and build the graph from it, so agreement between the two proves
nothing. They still contribute to the `cross_evidence_support` value and still
reveal contradictions. The policy permits metadata consistency to qualify
release even though embedded fields can be rewritten. Every bound WAV with
recorder BWF/iXML metadata receives `RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED`, an
informational finding with `scoring_eligible: false` and no Integrity
contribution. It neither lowers nor raises the score; the metadata consistency
checks still score and may release the value. Only a file that names its
recording (file name, tape, scene or take) counts as recorder metadata. BWF
written by a DAW, editor or sound library (for example a Pro Tools bounce) is
reported as `bwf_kind: daw_or_library`, and its time reference is a session
timeline position (`timeline_reference_seconds`), not a recorder start
timecode. For example:

- **Sound report vs recorder:** the sound report says `2T01.WAV` (18 Dec)
  starts at `08:49:43:00`, and the take's `bext` time reference says the same.
  This is checked by `sound_report_start_timecode_matches_recorder`.
- **AAF vs recorder:** an AAF edit uses 4.6 s of tape `24Y12M17` from 14:04:59,
  and `2T05.WAV`'s recorded span, 14:04:31 + 43 s, contains it. This is checked
  by `aaf_source_range_within_recorded_timecode`.

The cross layer scores `cross_evidence_scored_facts`, one vote per rule and
subject, not per document. A failed observation keeps its fact failed even
when many agreeing reports are supplied; repeating a failed report cannot add
another penalty either. Local cue/selection/mob labels do not add votes.
Multiple range claims about the same recording and property must all pass.
Raw rows remain in `cross_evidence`; `evidence_tier_counts` counts those raw
rows, while `check_count`/`scored_fact_count` counts eligible facts. Document
hash counts are not independent-witness counts. The decision's release reason
and contradiction status are separate from the consistency number.

The cross layer reports `evidence_tier_counts`. A profile that omits
`withholding_rule.substantive_support_tiers` keeps the original rule, in which
any assessed cross-evidence check counts. Audio-content agreement is not a
tier: diagnostics remain report-only; bounded threshold calibration does not authorize scoring integration.
The canonical profile key is now `require_substantive_support`. The old
`require_substantive_support_or_contradiction` spelling remains readable as an
alias, but no longer enables a contradiction-only bypass; specifying both is
an error. Weights, confidence formulas and the 0.25 severe ceiling are unchanged.

On the Project Rock dataset the three episode chains (takes, both sound
reports and the episode AAF) produced 0 recorder contradictions and
Integrity 1.0. A copy of the sound report with one start timecode shifted by
2 s failed exactly that one check.

An AAF also tells the evaluator what it does not hold. `assessment_checks.aaf_reference_coverage`
lists the recorder takes the edit uses and which are bound; an unsubmitted take
is reported as `AAF_REFERENCED_TAKES_NOT_SUBMITTED` (low, not scored).
`assessment_checks.aaf_non_recorder_media` lists the edit's other media
(library effects, music, renders) with `AAF_REFERENCES_NON_RECORDER_MEDIA`;
no licensing determination is made, and creation-method coverage states that
it counts submitted artefacts only. When Integrity is contradicted, its
reasoning and score explanation start with the contradiction count (for
example "1 of 96 assessed facts is contradicted (cross_evidence_support 1 of
78)" on Project Rock Ep01 under `default-v6`), because one contradiction among many honest facts barely moves the
number. `mixed_from` accepts
`audio/raw-take` as a source, since dialogue post-production mixes takes
directly after the AAF edit. `assessment_checks.declared_chronology` compares
the BWF origination date (and time, when both files carry one) across each
declared edge between bound WAVs; a source originated after its target is
reported as `DECLARED_SOURCE_NEWER_THAN_TARGET` (low, not scored), because BWF
dates can be reset or rewritten. An audio edge where either file has no date is
listed as `not_comparable` with a reason (`format_has_no_bwf` for MP3,
`bwf_not_kept` for FLAC without its WAV chunks, `no_origination_date`), so a
missing comparison is visible; no time is guessed. Findings and experiments on the rest of the
Project Rock material are in
[`program/docs/PROJECT_ROCK_FINDINGS_2026-10-22.md`](program/docs/PROJECT_ROCK_FINDINGS_2026-10-22.md).

An assessed failure of a rule listed in `severe_contradiction_rules` caps an
otherwise available weighted result with `min(weighted_mean, 0.25)`. The cap
cannot raise a lower value, and optional audio matches cannot cancel it. Final
input problems that stop evaluation still return an execution error rather
than a capped score. When the cap applies, the Integrity reasoning names the
triggering rule(s) and the uncapped weighted mean, and
`assessment_checks.severe_contradiction_cap` records `cap`, `triggered_rules`,
`weighted_mean`, `value` and `value_lowered`. The record is absent when no
severe rule failed. Diagnostic threshold calibration is separate from weight
calibration; this integration does not add a fourth scored layer.

### Score explanations

Every successful response carries `assessment_checks.score_explanations`, one
entry per axis. The response schema is a shared contract, so it is not
extended. The Integrity entry lists:

- each contributing layer's value and weight;
- the weighted mean, and whether a cap applied (`cap_applied`, `cap`) or the
  value was withheld (`withheld`);
- the evidence-tier counts;
- `contradiction_count`, `assessed_fact_count` and `fact_counts_by_layer`;
- every failed check as a sentence with the compared values.

For example: *"Sound report start timecode for 2T01.WAV is 08:49:45:00, but
the recorder wrote 08:49:43:00 into the file (2.00 s apart)."* The same
sentences are appended to the Integrity `reasoning` after "Contradictions:".
When anything is contradicted, both the reasoning and the first statement
start with the count and name the contradicted layers, for example *"2 of 122
assessed facts are contradicted (cross_evidence_support 2 of 104)"*. The counts
are computed once and stored in `assessment_checks.integrity_decision`
(`contradiction_count`, `assessed_fact_count`, `fact_counts_by_layer`), so they
remain available when `include_reasoning: false` omits the explanations. The
desktop results read them from there and show the count beside the value
("0.990 (contradicted: 2)").

A timecode mismatch is an ordinary contradiction, not a severe one. Honest
productions produce mismatches too: hand-typed reports, timecode drift, files
re-exported with rewritten `bext`. So it lowers the cross-evidence layer and
is named explicitly; it does not cap the score.

The completeness entry also names any evidence the selected workflow does not
describe. Such artefacts get one info finding,
`WORKFLOW_EVIDENCE_OUTSIDE_POLICY`, with `status: "inconclusive"` per
artefact, and they neither raise nor lower completeness.
`include_reasoning: false` omits the explanations.

## FLAC and MP3

**Scope limitation.** The evaluator is designed and calibrated for WAV/BWF. FLAC and MP3 files are
accepted and receive the checks implemented today: hash binding, structure,
declared technical values, the FLAC MD5, recorder metadata kept in FLAC with
`--keep-foreign-metadata`, embedded C2PA, declared format conversions and the
optional audio diagnostics. Every other check is reported as not assessed,
never as a contradiction and never in a score. Each FLAC or MP3 file lists
them in `limited_checks` (also in `AUDIO_FORMAT_LIMITED_CHECKS` and
`format_capabilities`) with `limitation`: `program_scope` where only WAV is
implemented (a limitation of this program, for example dates in ID3 or Vorbis
tags, or MP3 decoded-sample integrity), `format` where the format holds no
such information (MP3 bit depth or recorder metadata), or `file` where this
file lacks it (a plain FLAC without BWF chunks, an MP3 without a Xing/LAME
header). Audio diagnostics on FLAC and MP3 use thresholds calibrated on WAV
only (`format_scope: calibrated_on_wav_only`). For a full assessment, submit
WAV; a delivered FLAC or MP3 can be declared `derived_from` its WAV original
with `transformation: format_conversion`. No further FLAC or MP3 checks are
planned.

The file content, not the extension, decides the format
(`music_target/audio_formats.py`). An ID3v2 tag before the audio does not
decide it: the bytes after the tags do, because the C2PA SDK writes an ID3v2
tag before the `fLaC` marker of a signed FLAC.

**FLAC** (`music_target/flac_io.py`). Metadata blocks are parsed directly and
bounded: `STREAMINFO` (rate, channels, bit depth, total samples, MD5),
`VORBIS_COMMENT`, and `APPLICATION` blocks with ID `riff`, where
`flac --keep-foreign-metadata` keeps the source WAV's `bext`, `iXML` and `LIST`
chunks. Those chunks feed the same recorder-metadata, sound-report, AAF and
chronology checks as a WAV take. The new check `flac_md5_matches_decoded_audio`
decodes the audio with soundfile and compares the MD5 of the samples with
`STREAMINFO`. It compares the file with itself, not with a submitter's claim,
so it sits in the file-integrity layer and a mismatch is a
`FILE_INTEGRITY_CONTRADICTION`. Declared FLAC and MP3 technical values
(`technical.*_matches_observed`) are scored in declaration consistency, as for
WAV. An MD5 the encoder
did not store, a bit depth libsndfile cannot decode (for example 32-bit FLAC)
or the caller's deadline makes the check `unavailable`. A FLAC recording
(`audio/raw-take`, `audio/raw-track`) without kept WAV chunks gets the
informational `FLAC_RECORDER_METADATA_ABSENT`: plain FLAC encoding drops them.

**MP3** (`music_target/mp3_io.py`). Every frame header is walked, so frame
count, stream parameters and lost sync are measured. The exact length is known
only from a Xing/Info header with a LAME-style encoder delay and padding
(`frames x samples_per_frame - delay - padding`, the length gapless decoders
produce); otherwise `duration_seconds` is `null` and only
`estimated_duration_seconds` is given. MP3 has no bit depth and no standard
place for recorder metadata. Declared fields that cannot be measured give
checks with `passed: null` and `status: unavailable`, never contradictions,
and each FLAC or MP3 file gets the informational `AUDIO_FORMAT_LIMITED_CHECKS`
listing the check families not run, with their limitation. ID3v1, APEv2 and appended ID3v2 tags are excluded from
the audio; compressed or encrypted ID3 frames are not decoded.

Real Lumina MP3 output (Seed Speech 2.0, Format mp3) was checked on 24
October 2026: its C2PA manifest sits in an ID3v2.4 `GEOB` frame, validates,
and is signed by the same BytePlus certificate as Lumina's WAV, so it is
recognised as the known AI signer; declaring such a file `recorded` is a
contradiction. These files have no Xing header, so their exact length is
unknown. libsndfile estimates such lengths from the file size and decodes
only that far, so audio diagnostics decode only the MPEG frames and report
`decoder_length_mismatch` rather than analyse a truncated signal. Without
gapless information a shortfall of up to two frames is accepted, because
libsndfile also drops part of the last frames of a constant-bitrate file.

**Fake formats and content bandwidth.** A FLAC made from an MP3, a 320 kbps
MP3 re-encoded from 128 kbps, or a 96 kHz file upsampled from CD audio passes
every structural check: the FLAC MD5 is valid and the MP3 frames really are
320 kbps. With `--audio-analysis`, the report-only `bandwidth` provider now
measures where each file's decoded audio stops and compares it with the
container and bitrate (`assessment_checks.audio_bandwidth`, with an
`AUDIO_CONTENT_BAND_LIMITED` finding for flagged files). On the Testing_music
set (24 October 2026) it flagged all four locally made fake files and none of
the four genuine lossless files, and all six free-site "320 kbps" MP3s stop at
16.5–18.2 kHz. It never scores and is uncalibrated: AI generators produce
band-limited audio too (all 25 Lumina benchmark WAVs stop at 17.4–19.9 kHz),
so a flag says where the audio stops, not who made the file. Five of those
free-site MP3s also share short sounds at fixed times, which audfprint
correctly matches at offset 0 (6 of 10 pairs); see
[the false-match benchmark](program/docs/FALSE_MATCH_BENCHMARK_2026-10-21.md).

FLAC and MP3 observations use the format-neutral codes `AUDIO_OBSERVATION` and
`AUDIO_PARSER_FAILED` (rule `audio_parser_available`) with an `audio_format`
field; WAV keeps `WAV_OBSERVATION` and `WAV_PARSER_FAILED`. soundfile
0.14.0 is now a core requirement. Metadata parsing uses only the standard
library, so the desktop builder reads FLAC and MP3 with the same parsers and
fills in only the fields each format carries (no MP3 bit depth; an MP3
duration only when exact). The plan and test results are in
[`program/docs/FLAC_MP3_SUPPORT_PLAN.md`](program/docs/FLAC_MP3_SUPPORT_PLAN.md).
On the Project Rock Ep01 chain transcoded locally, FLAC takes encoded with
`--keep-foreign-metadata` gave the same result as WAV; plain FLAC takes lost
the recorder evidence, so Integrity was withheld; an MP3 final mix (320 or
128 kbps) changed nothing except the chronology comparison
([findings](program/docs/PROJECT_ROCK_FINDINGS_2026-10-22.md)).

### Client decisions on formats (24 October 2026)

**FLAC and MP3 follow the WAV rules (confirmed).** The client confirmed the
current treatment: FLAC and MP3 use the same analysis and scoring rules as
WAV, read in the way each format allows. The FLAC MD5 is an internal check
that never adds origin confidence, and a check a file cannot support is
reported as not assessed (insufficient evidence), never as a failure or a
contradiction.

**Recordings do not have to be WAV/BWF (confirmed).** Raw takes and raw tracks
may be submitted as WAV/BWF, FLAC or MP3. WAV/BWF is preferred, not required:
the format never makes Completeness fail and never lowers a score, and no
workflow can require it. What a file lacks is reported instead, in
`limited_checks`, `AUDIO_FORMAT_LIMITED_CHECKS`, `FLAC_RECORDER_METADATA_ABSENT`
and `format_capabilities` (with advice for recordings that lost their recorder
evidence).

Limitations that follow from these decisions:

- **Fewer checks, sometimes no Integrity value.** A plain FLAC or an MP3
  recording carries no recorder metadata, so the recorder, sound-report, AAF
  and chronology checks cannot run. If no other qualifying evidence exists,
  Integrity is withheld (`null`). That means "not enough evidence to assess",
  not "suspicious"; it must be presented that way to end users.
- **Completeness does not show the limit.** Completeness counts a FLAC or MP3
  recording as present, so a package of MP3 takes can reach the same
  Completeness as one of WAV/BWF takes. The difference is visible only in the
  findings, `format_capabilities` and the Integrity status. A separate
  Completeness label for "present with limits" is not implemented.
- **Evidence a submitter could have kept is lost.** Converting to MP3 or to
  FLAC without `--keep-foreign-metadata` removes recorder evidence, and a
  fabricated package converted that way shows fewer contradictions than its
  WAV version would. This is reported, not counted as a contradiction.
- **Thresholds are WAV-only.** Audio diagnostics on FLAC and MP3 use thresholds
  calibrated on WAV (`format_scope: calibrated_on_wav_only`), and dates in ID3
  or Vorbis tags are not read.

**Submission guidance.** For the most complete assessment, submit recordings
as WAV/BWF, or as FLAC encoded with `flac --keep-foreign-metadata`, which keeps
the recorder chunks and gives the same result as WAV. Use MP3 for release or
reference copies rather than recordings. When a FLAC or MP3 is delivered and
the WAV original exists, submit both and declare the delivered file
`derived_from` the original with `transformation: format_conversion`.

### Mixing formats in one submission

Every role accepts WAV, FLAC and MP3. The format never lowers a score; it
decides which checks can run.

- `assessment_checks.format_capabilities` lists, for each audio file, whether
  it carries recorder metadata (`present`, `not_kept` for a plain FLAC,
  `not_supported_by_format` for MP3, `absent`), an exact length, a bit depth,
  a BWF date and an embedded C2PA manifest, with advice for recordings that
  lost their recorder evidence. It is report-only.
- A `derived_from` edge may declare `"transformation": "format_conversion"`:
  the target is the same audio as the source in another file format, for
  example the original WAV take and the delivered FLAC or MP3. The claim is
  checked against both files (`format_conversion_sample_rate_preserved`,
  `..._channels_preserved`, `..._bit_depth_preserved` between lossless files,
  `..._duration_preserved` within one sample for lossless files and one MPEG
  frame for MP3), and these checks are scored in declaration consistency like
  other relationship claims. A length that is not exact (an MP3 without a
  Xing/LAME header) is `unavailable`. Declared on another relationship type
  the flag is ignored (`FORMAT_CONVERSION_NOT_APPLIED`).
- Between lossless integer files with the same rate, channels and bit depth,
  `assessment_checks.format_conversions[].sample_identity` compares the
  decoded samples exactly with a canonical SHA-256
  (`pcm-int-le-interleaved-sha256-v1`: signed little-endian integers,
  channels interleaved, as FLAC's MD5 is defined). `identical` describes an
  exact content relation, not provenance; `different` adds the report-only
  `FORMAT_CONVERSION_SAMPLES_DIFFER`. Float WAV, lossy files and differing
  parameters are `not_comparable`. The SoniqTools Bach WAV and FLAC (16/44.1
  and 24/96) are identical; the fake 24/96 FLAC upsampled from CD audio,
  declared as a conversion, fails the rate and bit-depth checks.
- With `--audio-analysis`, `assessment_checks.audio_format_conversion` adds,
  per conversion, whether audfprint supports alignment at 0 s and the
  target's bandwidth assessment (report-only).
- The desktop builder shows the same capability notes when a file is added
  and before saving, and offers "same audio, other file format" on
  `derived_from` relationships.

## Run the native example

From the repository root, enter the evaluator directory and use Python 3.11 or later:

```bash
cd program
# Includes the required C2PA SDK (c2pa-python) used for attestation checks:
python3 -m pip install -r requirements.txt
python3 -B run.py --native examples/valid-generated/submission.json --root examples/valid-generated
```

The example processes four audio nodes in sequence: `raw-track -> stem -> mix -> master`. The expected result contains `execution_status: succeeded`, four WAV observations, three `RELATIONSHIP_INTEGRITY_OBSERVATION` findings, and three policy-disabled physical placeholders. This is a software-generated teaching fixture, not verified evidence of human-recorded creation.

Exercise two failure boundaries:

```bash
python3 -B run.py --native examples/invalid-hash/request.json --root examples/invalid-hash
python3 -B run.py --request examples/unsupported-png/request.json --root examples/unsupported-png
```

The first command should return `error` for a hash mismatch. The second should return `unsupported`. A successfully returned JSON response uses process exit code 0; inspect `execution_status` in the JSON for the actual evaluation outcome.

## CSEC entry point

```bash
python3 -B run.py --request examples/csec-wav/request.json --root examples/csec-wav
```

This example uses the supplied CSEC candidate request format. It contains one WAV file and does not invent stem, demo, or other roles. A CSEC bundle can also be processed directly:

```bash
python3 -B run.py --bundle /path/to/EVAL-case/bundle.json --root /path/to/EVAL-case
```

## Desktop manifest builder

From `program/`, install `requirements.txt` and make sure Tkinter is available, then run:

```bash
python3 builder_gui.py
```

The Full assessment page uses the summary-first layout described above.
Its label **Core Evidence Consistency** maps to the API field `integrity`;
all API names and JSON structures remain unchanged. Audio review findings
never alter any axis value, confidence or status. Important findings appear
above the scores, and the details tab separates attention items from all checks
and raw evidence. Confidence is labelled **Assessment confidence** and explained
separately from recording coverage. The report's timestamp records when the GUI
captured the result, not a signed production time.

The builder imports and hashes files in the background, provides workflow
preflight and full assessment, and can save the full machine-readable result.
Full assessment uses a verified temporary snapshot and the same production
worker as the command-line evaluator. The "Include audio diagnostics" box
starts ticked (untick it to skip them); without the audio packages the result
is the core assessment plus a note. Creation method takes `recorded`,
`synthesised`, `ai-generated` or a JSON list such as
`["recorded", "ai-generated"]`; leave it blank to omit the claim. Sound
reports (`text/sound-report`) and AAF edits (`project/aaf`) are available as
document types. Its preflight
uses the evaluator's limits (128 artefacts, 512 MiB per object, 1 GiB per
request), so it refuses exactly what the evaluator would refuse.

Portable exports place files in `objects/` under content-addressed names
(`objects/<sha256>.<extension>`) accepted by the evaluator. The source files
are never renamed or overwritten. By default, saving a manifest can instead
reference the selected originals without copying them; every save checks that
each original still matches its declared SHA-256. These manifests set
`options.local_file_references` and require explicit authorization for direct
evaluation:

```bash
python3 -B run.py --native /path/to/submission.json \
  --root /path/to/submission-folder --allow-local-files
```

Both the manifest option and the command-line flag are required. The flag is
restricted to trusted native manifests and cannot authorize CSEC/public bundle
paths. The builder's full-assessment action stages verified originals inside a
temporary bundle root and does not need external-path authorization.

## Verification

```bash
python3 -B -m unittest discover -s tests -v
python3 -B check_public.py /path/to/public-bundles
```

The second command writes results to `reports/`. Both commands operate locally and do not transmit data.
On 22 October 2026 (version 0.8.0) discovery found 312 tests. With
`REQUIRE_AUDIO_BACKENDS=1`, all 308 non-GUI tests passed with no skips; this
needs the audio libraries and the benchmark-only `mir_eval` from
`requirements-benchmark.txt` (without it, the 5 structure-benchmark cases are
skipped). The four GUI tests each passed when run in a separate process; run
them that way, because they can hang inside the full suite on macOS Tk.
Version 0.8.1 (22 October 2026): the same procedure found 332 tests; all 328
non-GUI tests passed in one process (31 s) and the four GUI tests passed
separately. After the Wilson intervals were added (23 October 2026) it found
342 tests; all 338 non-GUI tests passed in one process (30 s). With FLAC and MP3
support it found 376 tests; all 372 non-GUI tests passed in one process (31 s) and the four
GUI tests passed separately. With the bandwidth provider (24 October 2026) it
found 386 tests; all 382 non-GUI tests passed in one process (32 s) and the
four GUI tests passed separately. After merging main (scoring profile
`default-v6`) with the FLAC/MP3 work it found 403 tests; all 399 non-GUI tests
passed in one process (32 s) and the four GUI tests passed separately. After
the cross-format review changes (AAF coverage, undated chronology rows, decoder
versions) it found 409 tests; all 405 non-GUI tests passed in one process
(33 s) and the four GUI tests passed separately. With mixed-format evidence
(format capabilities, declared format conversions, sample identity) it found
422 tests; all 418 non-GUI tests passed in one process (35 s) and the four GUI
tests passed separately. After the WAV-focus scope change it found 425 tests;
all 421 non-GUI tests passed in one process (34 s) and the four GUI tests passed
separately. After the declared-range, content-beyond-final and forge-cost
changes (25 October 2026) it found 466 tests; 459 of the 462 non-GUI tests
passed in one process (40 s) with 3 skipped (the Suno real-sample tests need
`C2PA_SUNO_SAMPLE_DIR`), and the four GUI tests passed separately
(`reports/test-results.txt`, `reports/release.json`). Version 0.8.2 (27 October 2026) was checked on
a fresh export in a new virtual environment with all three requirement files:
501 tests, all passed. The 496 non-GUI tests passed in one process (59 s) with
no skips (`C2PA_SUNO_SAMPLE_DIR` set to the verified Suno downloads), and the
five GUI tests passed separately. Version 0.8.3 (28 October 2026) adds 37
presentation tests (34 in `tests/test_builder_review.py` and three GUI cases):
538 tests, all passed with no skips. The 530 non-GUI tests passed in one process
(49 s) and the eight GUI tests passed separately (`reports/test-results.txt`;
full per-test log in `reports/test-results-0.8.3.txt`).

A line and branch coverage report of the same runs is in
`reports/coverage/` (`index.html` and `coverage-summary.txt`); see
[Coverage report](#coverage-report).
`tests/test_builder.py` now also passes on its own: it imports `run` (and so
c2pa) before its worker tests mock `subprocess.Popen`, because c2pa's import
calls `platform.processor()`, which spawns a subprocess. The
33 C2PA tests cover pinned-list hashes, trust status, asymmetric trust, known AI
signers, the C2PA chunk precondition and every final state. The synthetic tests
are engineering checks, not real-session accuracy calibration. Run the full dependency-required suite with
`REQUIRE_AUDIO_BACKENDS=1 python3 -B -m unittest discover -s tests -v`.
Core tests include coverage for non-final
input isolation and final-first stopping, unknown-field suggestions, audio
container detection by content, byte-capped decoding, deadline degradation,
non-final WAV isolation, integer/float extensible PCM, float32/64 decoding, non-finite
sample rejection, RIFF warnings, field-level unavailability, final-scoped C2PA,
standalone C2PA ingredient checks, hash caching, ndarray PCM decoding, 24-bit
sign extension, legacy signal-helper isolation, and an explicit assertion that
the policy-disabled `PhysicalSimilarityPass` is never invoked by assessment.

## Coverage report

`reports/coverage/` holds the line and branch coverage of the full suite
(`index.html`, and `coverage-summary.txt` per file). To reproduce it from
`program/`, install `coverage` and run the suite in the same split as the
tests, because the GUI tests can hang inside one process on macOS Tk:

```bash
python3 -m pip install coverage
```

```bash
cd tests && REQUIRE_AUDIO_BACKENDS=1 python3 -m coverage run --branch --parallel-mode --data-file=../.coverage --source=music_target,builder_operations,builder_results,builder_review,builder_report,builder_gui,manifest_logic,relationship_modes,run,check_public,wav_probe --omit='*/vendor/*' -m unittest $(ls test_*.py | grep -v test_builder.py | sed 's/\.py$//') test_builder.ManifestLimitTests test_builder.ExportTests test_builder.AssessmentTests
```

Then run each `test_builder.GuiTests.<name>` the same way in its own process,
and combine:

```bash
python3 -m coverage combine && python3 -m coverage html -d reports/coverage && python3 -m coverage report
```

On 28 October 2026 (version 0.8.3, 538 tests, all passed with no skips) line
coverage was 75.3% and branch coverage 65.7%. The policy-disabled legacy
signal modules (`physical_audio_passes.py`, `audio_derivation.py`, 20%)
account for most of the gap: active code is at 85%. The core evaluator and
scoring are at 91%, the audio diagnostics at 93%, the parsers at 82% and the
desktop builder at 75% (the new presentation modules `builder_review.py` 94%
and `builder_report.py` 100%; `builder_gui.py` 63%, because its Tk widgets are
tested end to end). Code run in builder worker subprocesses is not counted.
Earlier the same day 0.8.2 (501 tests) measured 74.7% and 65.0%; on
25 October (466 tests) the figures were 73.3% and 63.5%.

[`program/docs/testing/04_TEST_TRACEABILITY.md`](program/docs/testing/04_TEST_TRACEABILITY.md)
maps each numbered case in the three test plans (AU, AI, SU, SI, PC) to the
tests that check it, and lists the cases that are only partly automated or
not automated: 77 of 86 are fully automated.

## Directory overview

The locations below are relative to `program/`.

| Location | Purpose |
|---|---|
| `vendor/libevchain/` | Local copy of Ben's source with three reviewable fixes |
| `music_target/domain.py` | Music nodes, relationships, and permitted endpoint roles (`mixed_from` accepts raw takes) |
| `music_target/wav_io.py` | Streaming RIFF/WAVE inspection and vectorised integer/float sample decoding |
| `music_target/audio_formats.py` | Content-based container detection (WAV, FLAC, MP3, and unsupported AIFF, Ogg, RF64, MPEG Layer I/II) and bounded ID3v2 reading |
| `music_target/flac_io.py` | Bounded FLAC metadata parsing, kept WAV chunks and the decoded-audio MD5 check |
| `music_target/mp3_io.py` | MP3 frame walking, Xing/Info/LAME/VBRI headers, exact or estimated length and tag boundaries |
| `music_target/audio_observations.py` | FLAC and MP3 observations in the WAV result shape, and the MP3 limited-check list |
| `music_target/wav_parser.py` | Local copy of the supplied WAV parser |
| `music_target/engine.py` | File binding, `WavMetadataPass`, `RelationshipIntegrityPass`, library pipeline, and assessment-stage orchestration |
| `music_target/workflow_policy.py` | Safe policy loading, condition evaluation, and per-expectation workflow completeness |
| `music_target/assessment_passes.py` | Cached file and relationship result aggregation, CrossEvidence, C2PA attestation with pinned trust and final-state findings, and AI disclosure assessment logic |
| `music_target/audio_derivation.py` | Deprecated signal helpers and exact-reconstruction code retained for later cleanup; not executed by assessment |
| `music_target/physical_audio_passes.py` | Deprecated physical-similarity implementation retained for later cleanup; not imported by assessment |
| `music_target/midi_parser.py` | Size- and structure-bounded Standard MIDI File parser |
| `music_target/sheet_parsers.py` | CompSheet/CueSheet JSON, CSV, TXT, and PDF parsers plus field normalisation |
| `music_target/recorder_metadata.py` | Recorder-written BWF `bext`/iXML fields (tape, file name, scene/take, tracks, start timecode, iXML timestamp) from a bound WAV and the bext/iXML timestamp check; other BWF is reported as `daw_or_library` |
| `music_target/sample_validation.py` | Full-length sample check of every bound WAV, FLAC and MP3: NaN/Infinity fails, an unfinished scan is `not_completed` |
| `music_target/production_documents.py` | Sound report CSV and AAF (pyaaf2) parsers, their checks against recorder metadata, AAF take coverage and the AAF's non-recorder media |
| `music_target/evidence_tiers.py` | Evidence tiers for cross-evidence checks and their counts, including the asymmetric C2PA tier |
| `music_target/score_explanations.py` | Per-axis score explanations, contradiction and assessed-fact counts, and plain-language statements of failed checks |
| `music_target/daw_session.py` | Ableton Live session parser and its checks against the graph and MIDI |
| `music_target/audio_diagnostics/providers/music_semantics.py` | Report-only key, tempo, chord and phrase-form comparison across versions |
| `benchmarks/` | Public music dataset benchmark (Slakh/BabySlakh, MUSDB18-HQ) with true and control relationships; `false_match.py` and its pinned Lumina manifest for the false-match benchmark |
| `music_target/contract.py` | CSEC/native format adaptation, input validation, and version identification |
| `policies/workflow_evidence_policy.json` | Candidate required, conditional, and optional evidence policy for 16 workflows |
| `music_target/audio_diagnostics/` | Report-only task selection (including AAF-declared source ranges), verified snapshots, isolated workers, audfprint, SpectroMap, librosa and music_semantics adapters, fingerprint agreement, calibration status and result assembly |
| `policies/audio_analysis_policy.json` | Local provider and candidate-parameter configuration; no scoring controls or proposed development caps |
| `policies/audio_calibration_status.json` | Per-provider calibration status, case counts split into development, held-out and real material, and false-match counts, each with a 95% Wilson interval; attached to every audio row and does not change scoring |
| `music_target/case_intervals.py` | 95% Wilson score intervals for case counts, used by the calibration status and both benchmark scripts |
| `policies/c2pa_trust/` | Pinned C2PA Trust List, TSA list and frozen legacy ITL (`trust_lists.json` with commits and SHA-256), `known_ai_signers.json` and the update procedure |
| `schemas/audio-diagnostic.schema.json` | Diagnostic result validation and fixed non-scoring fields |
| `requirements-audio.txt`, `requirements-audio-lock.txt` | Optional audio dependencies and the tested environment |
| `make_audio_diagnostic_example.py`, `examples/audio-diagnostics/` | Reproducible repeated-excerpt example, request and core/audio reports |
| `tests/test_audio_diagnostics.py` | 61 provider, protocol, score-isolation, insufficient-support, calibration-status, declared-range and content-beyond-final tests |
| `tests/test_case_intervals.py` | 10 tests: interval values and bounds, stored intervals matching their counts, and the benchmark summaries |
| `tests/test_audio_formats.py` | 29 FLAC and MP3 tests: detection by content, ID3v2, FLAC blocks, kept WAV chunks and MD5, MP3 exact and estimated length, sync loss and tags, evaluator results, C2PA in FLAC and MP3, diagnostics decoding, the public profile and the builder probe |
| `tests/test_c2pa_integrity.py` | 33 C2PA tests: runtime-signed fixtures, pinned lists, trust status, asymmetric trust, known AI signers (including Suno by fingerprint only; the three real-sample tests run when `C2PA_SUNO_SAMPLE_DIR` is set), the C2PA chunk precondition and every final state |
| `reports/audio-diagnostics-verification.json` | Historical 19 October 2026 verification and real-provider example results |
| `docs/AUDIO_DIAGNOSTICS.md` | Detailed audio setup, usage, output and interpretation |
| `music_target/scoring_profile.py`, `policies/scoring_profile.json` | Validated three-layer Integrity weights, tier-aware withholding, confidence, forge cost (`default-v7`) and severe-contradiction cap |
| `music_target/forge_cost.py` | Per-fact forge cost, per-subject evidence strength and the confidence factor; never changes the Integrity value |
| `benchmarks/forge_cost.py`, `benchmarks/results/forge_cost_project_rock_2026-10-25.json` | Forgery measurements behind the `default-v7` costs, the ranking check and the held-out result |
| `docs/FORGE_COST.md` | Forge cost method, costs, ranking check, held-out result and limits |
| `tests/test_forge_cost.py` | 12 tests: profile validation and tier order, fact and subject costs, value invariance and reasoning |
| `reports/coverage/` | Line and branch coverage of the full test suite (HTML and text summary) |
| `run.py` | CSEC/local execution and explicit parent-side audio diagnostic orchestration |
| `builder_gui.py`, `builder_operations.py`, `manifest_logic.py` | Tkinter desktop manifest builder, its background import/export/assessment operations, and manifest validation |
| `builder_results.py` | Artist-facing adapters for checks, attention items and audio diagnostic lines |
| `builder_review.py` | Detached, read-only review model (summary, grouped findings, coverage) shared by the GUI, copied text and HTML report; never written into the response JSON |
| `builder_report.py` | Offline, escaped HTML review report rendered from that model |
| `docs/UI_REPORT_0.8.3.md` | 0.8.3 result screen: reading order, audio outcome mapping and contract boundaries |
| `tests/test_builder_review.py` | 34 presentation tests: response immutability, outcome mapping, deduplication, coverage wording, HTML escaping |
| `schemas/` | Original CSEC candidate schemas and local music schemas |
| `schemas/ddex/` | Pinned RIN 2.1 and ERN 4.3 XSD files, source hashes, and licence reminder |
| `legacy_parsers/` | RIN, ERN, and C2PA parsers used by the production path, plus XSD validation and XML helpers; the C2PA parser verifies the pinned trust lists and matches known AI signers |
| `examples/` | Runnable requests, fixture files, and generated responses |
| `reports/source_inventory.json` | Source files reviewed for this release and their SHA-256 values |

## Decisions still required from the team

1. Confirm the CSEC technical contact and whether the engagement uses full integration or schema/sample exchange only.
2. Resolve whether stems and raw takes are universally required, as one PDF suggests, or conditional by workflow, as the profile specifies.
3. Obtain genuine successful-creation fixtures; the generated teaching fixture is not validated human-creation ground truth.
4. Confirm that the internal weighting and interpretation of integrity checks match customer expectations. The project keeps the four dimensions separate and does not add a decision policy.
5. Agree with CSEC whether the new optional C2PA `validation` fields (`trust_status`, `trust_source`, `revocation_status`, `signer`) need a new response `schema_version`; it currently stays `0.2-candidate`.
6. Confirm the BytePlus Lumina and Suno signing certificates with the vendors, decide which other AI generators to add to the known AI signer list (Stability AI and Google Lyria need no entry because their certificates are already trusted; ElevenLabs needs a signed MP3 from its opt-in API workflow before a certificate can be verified; Adobe Firefly needs remote manifest support rather than an entry), decide whether an optional online mode may fetch remote manifests, and agree how often the pinned C2PA trust lists are reviewed.
7. Decide with the client which recordings a workflow requires (every take the AAF edit uses, or a sample), what substitutes are acceptable, and whether `min_supported_asset_share` should gate release. Chronology warnings stay report-only until relationship-specific time rules, time sources and tolerances are agreed. See `program/docs/NEXT_TASKS.md` items 34–37.

8. Set the accuracy a report-only audio diagnostic must reach before it may affect a score (declared ranges, content beyond the final), and review the `default-v7` forge costs, which come from forgery measurements on one production, not calibrated probabilities.
9. Choose the remaining fixes for the start-timecode gap (0.8.2 catches a bext-only shift; see `program/docs/NEXT_TASKS.md` item 42) and confirm the next ML step: Demucs source-separation comparison for submitted stems, with Whisper dialogue matching on the roadmap.

These decisions do not block the local executable baseline, but the directory must not yet be described as a complete music-authenticity assessment system.
