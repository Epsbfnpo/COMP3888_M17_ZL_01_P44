# Project Rock: findings from the untapped material (2026-10-22)

The `certivina_audio_domain/` folder (8.4 GB, gitignored) holds real
post-production material from Project Rock. Earlier work used it only for the
recorder-metadata checks (takes, sound reports, episode AAF). This round used
the rest of it: the Pro Tools WIP mixes, the editorial reference exports, the
video masters, the Final Cut Pro XML conform and the sound-effect library
folder. All runs used the real evaluator (`run.py --native`) on version 0.8.0.
The source data, which contains personal contact details in the sound reports,
stays outside the repository.

## Status after the first batch of fixes

Findings 1, 2, 3, 5 and the reporting half of 4 (4a) were fixed on the same day.
None changes a score except finding 5, where takes declared `mixed_from` are no
longer skipped. Rerunning the experiments gave:

| Run | Before | After |
|---|---|---|
| Ep01 chain | Axes as above; 14 origin findings | Axes unchanged; 13 origin findings (the Pro Tools WIP is `daw_or_library`); 72 non-recorder AAF media listed |
| Shifted timecode | Integrity 0.995, `contradicted` | Unchanged value; reasoning starts "1 of 122 assessed facts is contradicted (cross_evidence_support 1 of 104)"; counts stored in `integrity_decision` |
| Dropped take | No finding | `AAF_REFERENCED_TAKES_NOT_SUBMITTED` names 24Y12M17/6T03.WAV; axes unchanged |
| Takes declared `mixed_from` | 13 edges skipped, completeness 0.67 | 0 skipped, completeness 1.0 |
| Audio on/off parity (dropped-take bundle) | — | Core result identical, including the new AAF records |

## Status after the second batch (version 0.8.1)

Findings 7 and 10 were implemented in version 0.8.1. Neither changes a score.

| Run | Before | After |
|---|---|---|
| Ep01 chain with `--audio-analysis` | 14 whole-take tasks; matches inside the AAF-used range for 5 takes; TOASTPANT01 matched only outside its used range; 29 s, 815 MB peak | 14 tasks limited to AAF-used ranges (`range_source: aaf_source_range`); 6 report `AUDIO_AAF_RANGE_CORRESPONDENCE` (10T07, 1T04, 5T15, 6T03, 6T08, 6T11); TOASTPANT01 has no match; 19 s, 461 MB peak |
| Ep01 chain, chronology | No check | `declared_chronology` compares 13 take-to-mix edges; none is reversed, so no `DECLARED_SOURCE_NEWER_THAN_TARGET` |
| Audio on/off parity | Held | Still holds with both new records |

Restricting the take to its used seconds helped SpectroMap (6 of 14 tasks) but
left audfprint with one candidate (6T03, below the support gates) instead of two
supported matches (5T15, 6T11) in the whole-take run, because a few seconds of
dialogue give it fewer landmarks. With 95% Wilson intervals: SpectroMap 6/14
(21–67%), audfprint supported 0/14 (0–22%) in the used ranges and 2/13 (4–42%)
over whole takes. These counts are recorded as `real_material` in
`policies/audio_calibration_status.json`.
Absence of a match is still not reported as disagreement with the AAF.

Findings 6 and 4b need a client decision; 8 needs no code change; 9 waits for
an FCP XML parser; the missing Ep02/Ep03 final audio must come from the client.

## FLAC and MP3 versions of the Ep01 chain (2026-10-23)

The dataset contains no FLAC or MP3 (all 251 audio files are WAV; the videos
carry PCM or AAC), so the Ep01 chain (13 recorder takes, 2 sound reports, the
AAF, the reference and the WIP mix) was transcoded locally and rerun with
`--audio-analysis` on the FLAC/MP3 branch. Every transcoded object got a new
SHA-256 and every reference to it was rewritten.

| Variant | Axes and Integrity | Cross-evidence | AAF-range diagnostics | Other differences |
|---|---|---|---|---|
| WAV (baseline) | Integrity 1.0 consistent, released; 0 of 122 facts contradicted | 134 recorder-metadata checks pass | 6 of 14 ranges with a correspondence | 13 chronology rows |
| Takes as FLAC, `flac --keep-foreign-metadata` | Same | Same | Same | 13 more assessed facts (the FLAC MD5 checks, all matched); recorder metadata identical for every take |
| Takes as FLAC, plain `flac` | Integrity withheld (`insufficient_assessed_layers`) | All 134 recorder-metadata checks gone | No AAF ranges (takes cannot be matched to the AAF), whole-take tasks instead | `FLAC_RECORDER_METADATA_ABSENT` on all 13 takes; no chronology; one AAF take reported as not submitted |
| Mix as MP3, 320 kbps | Same | Same | Same 6 of 14 | Exact length (39.68 s) from the LAME header; `AUDIO_FORMAT_LIMITED_CHECKS`; no chronology (the MP3 has no BWF date) |
| Mix as MP3, 128 kbps | Same | Same | Same 6 of 14 | As for 320 kbps |
| Every audio file as FLAC (kept metadata) | Same | Same | Same | 15 more assessed facts (MD5, all matched) |

Each run took 20–30 s. Conclusions:

- A recorder take delivered as FLAC with its WAV chunks kept supports every
  check a WAV take does, plus an MD5 of the audio.
- Plain FLAC encoding is the real risk: the recorder metadata is gone, so the
  strongest evidence tier disappears and Integrity is withheld. The response
  says why (`FLAC_RECORDER_METADATA_ABSENT`); nothing is reported as a
  contradiction. Submitters should be told to use `--keep-foreign-metadata`.
- An MP3 final mix keeps the recorder evidence of its WAV/FLAC sources and the
  AAF-range correspondences found here, but loses the chronology comparison;
  `AUDIO_FORMAT_LIMITED_CHECKS` now names that loss too.
- These are dialogue-heavy takes; the false-match benchmark showed that MP3
  can lose weak shared material, so the unchanged 6 of 14 is a case
  observation, not a recall guarantee.

## What was run

| Experiment | Input | Result |
|---|---|---|
| Ep01 version chain | 13 AAF-used takes, both sound reports, Ep01 audio AAF, 30 Jan reference export, 1 Feb WIP mix as final (18 artefacts, 369 MB) | `succeeded`; 134 cross-evidence rows, all passed; Integrity 1.0 (partial, 2/3 layers); core run 0.9 s, 88 MB peak |
| Shifted timecode | Same, one sound-report start timecode +2 s (2T05) | Exactly `sound_report_start_timecode_matches_recorder` failed, explained with both values; Integrity 0.995, status `contradicted` |
| Dropped take | Same, 6T03 removed (the AAF uses it) | No finding; completeness 1.0; Integrity 1.0 |
| Audio diagnostics | Ep01 chain with `--audio-analysis` | Core result unchanged; 14 relations in 29 s, 815 MB peak |
| Shared sound effect | A 96 kHz library SFX mixed at gain 0.7 into Ep01 (at 10 s) and Ep02 (at 20 s) | See finding 8 |
| Relationship vocabulary | Ep01 chain with takes declared `mixed_from` | 13 edges skipped (`RELATIONSHIP_ENDPOINTS_SKIPPED`), completeness 0.67 |
| Workflow fit | Ep01 chain under `sync_or_audiovisual_delivery` | Completeness 0.6 (no cue sheet exists for this production) |

## Findings

### 1. DAW, editor and library BWF is treated as recorder metadata (medium, fixed)

`recorder_fields` reports `present: true` for any WAV with a `bext` or iXML
chunk. In this material that includes the Pro Tools WIP mixes, library effects
written by iZotope RX, REAPER and Pro Tools, and Artlist files whose `bext`
description is a keyword list. None of them has a recorder file name, tape,
scene or take. The `bext` time reference of a DAW bounce is a session timeline
position (360.84 s for the Ep01 WIP), yet it is reported as
`start_timecode_seconds`. Each such file also receives
`RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED`, so the Ep01 run reports 14 of
these where 13 are genuine recorder takes.

No check compared these files with a document, because the document checks
match on recorder file names. The risk is therefore mislabelling and noise
rather than a wrong score today. **Suggested fix:** treat BWF as recorder
metadata only when recorder identity fields are present (iXML scene/take/tape,
or `sFILENAME` in the `bext` description), and report other BWF as
`daw_or_library_bwf` without a recorder start timecode.

### 2. AAF-referenced takes that are not submitted are silently ignored (medium, fixed)

The Ep01 AAF uses 13 recorder takes. Removing one of them from the submission
produced no finding, and completeness stayed 1.0. The AAF itself is evidence
that the take exists and was used. **Suggested fix:** report a coverage
observation such as "the AAF references 13 recorder takes; 12 are bound", and
let workflows that require source takes mark the missing one as `missing`.

### 3. Non-recorder media referenced by the AAF is invisible (medium, fixed)

The three episode AAFs reference roughly 70–120 master mobs each that are not
recorder takes: commercial effect libraries, loops (for example a 90 bpm bass
loop), a compilation of old French songs, and other music. The AAF parser
extracts only recorder-take references, so none of this appears anywhere in
the response. Creation-method disclosure coverage reports 15/15 "recorded"
artefacts, which is accurate for the submitted files but says nothing about
third-party material the edit is known to contain. **Suggested fix:** list
AAF-referenced media that was not submitted as declared but unsubmitted
sources, and let the sample and licence-clearance expectations use that list.

### 4. One contradiction barely moves Integrity in a large production (policy; count shown, weighting open)

With 122 assessed facts (104 of them cross-evidence), one forged timecode lowers the cross-evidence layer to
0.990 and Integrity to 0.995. The `contradicted` status and the explanation are
correct, but a reader who looks only at the number sees almost no change.
**Decision for the client:** show the contradiction count next to the value,
or treat recorder-tier contradictions as more serious than ordinary ones.

### 5. `mixed_from` does not accept raw takes (low, fixed)

In dialogue post-production, takes are edited in the AAF and go straight into
the mix. Declaring that as `mixed_from` skips all 13 edges, because the
endpoint rules allow `audio/raw-track`, `audio/edited-take`, `audio/stem` and
`audio/sample` as sources but not `audio/raw-take`. Submitters must instead use
`derived_from` or mislabel takes as raw tracks. **Suggested fix:** allow
`audio/raw-take` as a `mixed_from` source, or document the intended modelling.

### 6. No workflow describes film or TV dialogue post (low)

None of the 16 workflows describes takes + sound reports + AAF edit + mix to
picture. `recorded_single_performance_to_final` fits only loosely (the
reference export is outside its policy), and `sync_or_audiovisual_delivery`
requires a cue sheet that this production never had (completeness 0.6).
**Suggested fix:** add a dialogue post-production workflow whose required
evidence is recorder takes, a sound report or AAF, and the final mix.

### 7. AAF source ranges could target the audio diagnostics (improvement, done in 0.8.1)

The AAF states which seconds of each take were used. Comparing the
diagnostics' matched source windows with those ranges gives real ground
truth:

| Measure (Ep01, 13 AAF-used takes) | Result |
|---|---|
| Takes with a match inside the AAF-used range | 5 / 13 (1T04, 5T15, 6T03, 6T08, 6T11) |
| audfprint `supported_within_profile` | 2 / 13 (5T15, 6T11), both inside the used range |
| Matches outside the used range | TOASTPANT01 only (SpectroMap, 5–6 token pairs) |

Restricting each take to its AAF range would turn this into an explicit
observation ("the AAF-declared range is corroborated by content") and remove
whole-file coincidences like TOASTPANT01. The diagnostics would stay
report-only.

### 8. Shared library effects look like derivation (confirms the design)

| Case | audfprint | SpectroMap |
|---|---|---|
| SFX (96 kHz) declared as a sample of the mix that contains it | missed | found at 10.00 s (22 token pairs) |
| Same SFX declared as a sample of the plain mix | none | none |
| Ep02+SFX declared `derived_from` Ep01+SFX | none | correspondence at the inserted offsets (5 token pairs) |
| Plain Ep02 declared `derived_from` plain Ep01 | none | none |

Two unrelated episodes that share one licensed effect show a content
correspondence. This is correct as an observation and confirms why content
matches must never be read as provenance. audfprint missed an effect mixed at
gain 0.7, which matches the earlier finding that weak mixture components can
be missed. Resampling from 96 kHz to the analysis rate worked.

### 9. Final Cut Pro XML always carries a bare DOCTYPE (for a future parser)

The FCP 7 `xmeml` conform starts with `<!DOCTYPE xmeml>` and has no internal
subset or entity declarations. The current iXML and DAW hardening refuses any
`<!DOCTYPE`, so a parser built the same way would reject every real FCP 7 XML.
A future parser should refuse internal subsets and `<!ENTITY` declarations
instead (or use a hardened parser such as defusedxml). This conform mostly
references picture media, so its audio-provenance value is limited.

### 10. The material itself: masters have no audio (data; chronology warning added in 0.8.1)

The three ProRes masters contain picture and timecode only, with no audio
stream, so the true final audio is not in the dataset. The Ep01 WIP mix
(39.68 s) matches its master's length exactly; the Ep02 and Ep03 WIP mixes are
1.12 s and 0.80 s shorter than their masters, so the final audio for those
episodes is missing. The 30 January reference exports predate the WIP mixes
(1–3 February). The evaluator has no chronology check; a warning when a
declared source's origination date is later than its target's would catch
reversed declarations.

## Reproduction

The bundles were built in a scratch directory by hashing and copying the
source files into content-addressed `objects/`. The Ep01 chain uses the takes
returned by `parse_aaf_file` for `audio_250108_EP01_edit08.aaf`, both sound
reports, that AAF, `ProjectRock_Ep01_16x9_250130_1538.wav` as
`audio/reference` (`input_to`) and `ProjectRock_Drive_Ep01_WIP_2025_02_01.wav`
as the final `audio/mix` (takes `derived_from`, documents `input_to`), under
`recorded_single_performance_to_final`. The effect experiments mixed
`BOOM Library - Animal close-ups horse trotting on gritty concrete.wav` into
the WIP mixes with ffmpeg (`adelay`, `volume=0.7`, `amix normalize=0`).
