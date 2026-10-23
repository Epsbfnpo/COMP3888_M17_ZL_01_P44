# Native input guide for adversarial testing

Status: draft for CSEC review, 23 October 2026, checked against version
0.8.1. The input format does not depend on the scoring changes in progress;
the response fields under "Reading the response" may gain fields.

## Why native input

The public CSEC bundle profile accepts `audio/wav` only and the single
relationship `draft-of.audio`. Recorder metadata, sound reports, AAF edits,
DDEX, MIDI, comp and cue sheets and C2PA ingredients can only be submitted as
a **native request**. For this round, please attack the evaluator through
native requests; changing the shared CSEC contract needs a separate agreement.

## Running the target

```bash
cd program
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -B run.py --native /path/to/case/request.json --root /path/to/case --output response.json
```

- `--root` is the case folder; every object must sit inside it.
- Add `--audio-analysis` for the report-only audio diagnostics (needs
  `requirements-audio.txt`).
- Exit code 0 means a response was produced; read `execution_status`
  (`succeeded`, `unsupported`, `timeout`, `error`). Exit code 2 means the
  input could not be read well enough to answer, with a message on stderr.
- Evaluation is offline. `offline_only: false` does not enable network access.

## Case folder layout

```text
case/
  request.json
  objects/
    <sha256>.wav
    <sha256>.csv
    <sha256>.aaf
```

Each object is stored as `objects/<lower-case sha256 of the bytes>.<lower-case
extension>` and listed in the request's `objects` map. A hash or extension in
upper case, a path that does not match its hash, or a path outside the root is
reported as an input error rather than corrected.

`pack_native_request.py` (next to this guide) builds this layout from ordinary
files; see the worked example below.

## Request structure

Schema: `schemas/native-request.schema.json`.

| Field | Content |
|---|---|
| `run_id` | UUID |
| `case_id` | Any non-empty string |
| `profile` | `music-native-v0.1` |
| `options` | `include_reasoning`, `offline_only`, `timeout_seconds` (all required) |
| `workflow` | `workflow_id`, `modifiers`, `declarations.submitter_confirmation` (required); optional `contributors` and workflow flags |
| `chain` | `schema_version: 0.0.3`, `hash_method: sha256`, `final_artefact_hash`, `artefacts` |
| `objects` | SHA-256 → `objects/<sha256>.<ext>` |

Each entry in `chain.artefacts`:

| Field | Content |
|---|---|
| `artefact_hash` | SHA-256 of the file |
| `artefact_type` | One of the types below |
| `attributes` | Optional. `creation_method` (`recorded`, `synthesised`, `ai-generated`, or a list of them), `technical` (declared `duration_seconds`, `sample_rate_hz`, `channels`, `bit_depth`, ...), `production`, `source_metadata` |
| `evidence` | Incoming edges: `{"hash": <source>, "relationship_type": ..., "attributes": {...}}` |

Edges point from source to target and are listed on the **target**. A master
made from a mix lists `{"hash": <mix>, "relationship_type": "mastered_from"}`
in its own `evidence`. Every node must connect to the final artefact.

### Artefact types and what is checked

| Type | File | What the evaluator does |
|---|---|---|
| `audio/raw-take`, `audio/raw-track`, `audio/edited-take`, `audio/stem`, `audio/mix`, `audio/master`, `audio/sample`, `audio/reference`, `audio/ai-source`, `audio/unclassified` | WAV (integer PCM 8/16/24/32-bit, IEEE float 32/64-bit, `WAVE_FORMAT_EXTENSIBLE`) | Format and declared technical properties, BWF `bext` and iXML recorder metadata, embedded C2PA manifest |
| `text/sound-report` | CSV: metadata lines (`Date:`, `Frame Rate:`), then a take table starting with `File Name` | Take names, scene/take, start timecode and length against recorder metadata |
| `project/aaf` | AAF | Recorder-take references and used source ranges against recorder metadata; takes used but not submitted; non-recorder media |
| `provenance/c2pa` | Standalone C2PA manifest | Validation, trust and ingredients against the graph |
| `metadata/ddex-rin`, `metadata/ddex-ern` | XML (RIN 2.1, ERN 4.3) | Offline XSD validation; file hashes, ISRC and contributors against the graph |
| `project/midi` | Standard MIDI File | Tempo, time signature and duration against declarations and renders |
| `text/comp-sheet`, `text/cue-sheet` | JSON, CSV, text or PDF (`docs/SHEET_FORMATS.md`) | Selections and ranges against the graph and audio |
| `project/daw-session` | Ableton Live set | Tempo, time signature and source audio against MIDI and the graph |
| `project/automation`, `text/session-log`, `text/license-clearance`, `text/generation-record` | Any | Counted for workflow completeness; not parsed |

An unknown non-final type or relationship is quarantined and reported; an
unknown final type returns `unsupported`.

### Relationship types

| Relationship | Target types | Source types |
|---|---|---|
| `edited_from` | `audio/edited-take` | `audio/raw-take`, `audio/raw-track`, `audio/edited-take` |
| `comped_from` | `audio/edited-take` | `audio/raw-take` |
| `stemmed_from` | `audio/stem` | `audio/raw-track`, `audio/edited-take`, `audio/sample` |
| `mixed_from` | `audio/mix` | `audio/stem`, `audio/raw-take`, `audio/raw-track`, `audio/edited-take`, `audio/sample` |
| `mastered_from` | `audio/master` | `audio/mix` |
| `excerpted_from` | `audio/sample` | any |
| `rendered_from` | any audio type | `project/midi`, `project/daw-session`, `project/automation` |
| `derived_from` | any | any |
| `input_to` | any | any (use it to attach a document to the audio it describes) |

Optional edge attributes include `source_start_seconds`, `source_end_seconds`,
`target_start_seconds`, `target_end_seconds`, `gain`, `fade_in_seconds`,
`fade_out_seconds`, input/output sample rate and bit depth, source/target
format and `assertion_origin`. Declared values are compared with what can be
measured; the evaluator never infers missing ones.

### Workflows and modifiers

`workflow_id` selects the completeness policy
(`policies/workflow_evidence_policy.json`):
`recorded_single_performance_to_final`, `multitrack_recording_mix_master`,
`take_comping_and_editing`, `midi_virtual_instrument_production`,
`hybrid_recorded_and_midi_production`, `sample_and_loop_based_production`,
`remix_or_derivative_production`, `mixing_service_only`,
`mastering_service_only`, `live_performance_recording`,
`remote_collaborative_production`, `ai_assisted_or_generated_production`,
`commercial_release_delivery`, `sync_or_audiovisual_delivery`,
`stem_delivery_or_reconstruction`, `custom_or_unknown_workflow`.

Modifiers: `includes_recorded_performance`, `includes_multitrack_recording`,
`includes_take_editing`, `includes_take_selection`,
`includes_midi_or_sequencing`, `includes_samples_or_loops`,
`includes_mixing`, `includes_stem_export`, `includes_mastering`,
`includes_ai_assistance`, `includes_ai_generated_audio`,
`is_commercial_release`, `is_sync_or_audiovisual_delivery`.

### Limits

128 nodes, 512 MiB per object, 1 GiB across objects, 2 MiB of request JSON.
The wall-clock limit is the request's `timeout_seconds`, including worker
start-up.

## Worked example: a recorded take with a sound report and an AAF

`spec.json`, next to the four files it names:

```json
{
  "case_id": "REDTEAM-EXAMPLE-RECORDED",
  "final": "master",
  "workflow": {
    "workflow_id": "recorded_single_performance_to_final",
    "modifiers": [],
    "declarations": {"submitter_confirmation": true}
  },
  "artefacts": {
    "take": {"file": "2T01.wav", "type": "audio/raw-take",
             "attributes": {"creation_method": "recorded"}},
    "report": {"file": "sound_report.csv", "type": "text/sound-report"},
    "edit": {"file": "edit.aaf", "type": "project/aaf"},
    "master": {"file": "master.wav", "type": "audio/master",
               "attributes": {"creation_method": "recorded"},
               "evidence": [
                 {"from": "take", "relationship": "derived_from"},
                 {"from": "report", "relationship": "input_to"},
                 {"from": "edit", "relationship": "input_to"}
               ]}
  }
}
```

```bash
python3 docs/csec_redteam/pack_native_request.py /path/to/spec.json /path/to/case
.venv/bin/python -B run.py --native /path/to/case/request.json --root /path/to/case --output response.json
```

The four files in our check were synthetic: noise written as a 3-channel
24-bit poly BWF with `bext` and iXML fields (`2T01.WAV`, tape `24Y12M18`,
scene 2, take 01, start 08:49:43:00), a sound report listing the same take
and an AAF using one second of it, starting 0.5 s in. The helpers `recorder_wav`, `sound_report` and
`aaf_bytes` in `tests/test_production_documents.py` generate such files.

Result on 0.8.1: `succeeded`; Integrity 1.0, status `consistent`, released on
qualifying recorder-metadata cross-evidence, 0 of 12 assessed facts
contradicted; `RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED` reported. Writing
a take start timecode half a second away from the sound report's instead gave
one contradiction ("Sound report start timecode for 2T01.WAV is 08:49:43:00,
but the recorder wrote 08:49:43:13") and Integrity 0.9375 (`contradicted`). The example is
itself a fabricated package. It shows the known G1 weakness in
[ATTACK_GOALS.md](ATTACK_GOALS.md): consistent fabricated recorder metadata
is not detected, while inconsistent fabrication is.

## Using the Project Rock dataset

CSEC already holds the client's Project Rock material; we do not redistribute
it. To submit it, pack the files you use (takes as `audio/raw-take`, sound
reports as `text/sound-report`, AAF files as `project/aaf`, the WIP mix as
`audio/mix` or the final) with a spec like the one above. The masters are
ProRes video without audio and cannot be submitted as audio. Our run is in
`docs/PROJECT_ROCK_FINDINGS_2026-10-22.md`. Keep crew contact details from
the sound reports out of any shared result.

## Reading the response

Schema: `schemas/scoring-response.schema.json`.

| Field | Meaning |
|---|---|
| `execution_status` | `succeeded`, `unsupported`, `timeout` or `error` |
| `axes.<axis>.value`, `.availability`, `.confidence`, `.reasoning` | The four axes; `null` means not assessed |
| `axes.integrity.status` | For example `consistent`, `contradicted`, `insufficient_evidence` |
| `assessment_checks.integrity_decision` | `numeric_release`, `release_reason`, `contradiction_count`, `assessed_fact_count`, per-layer counts |
| `assessment_checks.cross_evidence` | Every cross-check with its `evidence_tier` and `passed` |
| `workflow_assessment.rule_results` | Each workflow expectation: `satisfied`, `missing`, `not_applicable`, `not_assessed` |
| `validation` | C2PA state of the final artefact (`validation_state`, signature and trust fields) |
| `findings` | Coded findings with severity and evidence hash |
| `coverage`, `unknown_inputs` | What was quarantined or not assessed |
| `system.version`, `system.source_revision` | Record both in every report |
