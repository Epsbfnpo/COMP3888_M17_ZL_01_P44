# CompSheet, CueSheet, sound report, AAF and DAW session input formats

The program accepts `.json`, `.csv`, `.txt`, and `.pdf` for `text/comp-sheet`
and `text/cue-sheet` artefacts. JSON is the strict canonical format. CSV and
delimited TXT are normalized to the same fields. PDF text extraction uses
`pypdf`; a PDF whose table cannot be recovered is reported as `partial`, not
silently treated as structured evidence.

These parsers validate document structure and cross-reference hashes and time
ranges. They do not prove that credits, take choices, usages, or rights claims
are true.

## Canonical CompSheet JSON

```json
{
  "schema_version": "1.0",
  "sheet_type": "comp",
  "song_title": "Example Song",
  "comp_name": "Lead vocal comp",
  "target_hash": "<sha256-of-edited-take>",
  "selections": [
    {
      "selection_id": "S1",
      "take_id": "Take 03",
      "source_hash": "<sha256-of-raw-take>",
      "source_start_seconds": 12.4,
      "source_end_seconds": 16.8,
      "target_start_seconds": 8.0,
      "target_end_seconds": 12.4,
      "gain": 1.0,
      "notes": "First chorus phrase"
    }
  ]
}
```

The equivalent CSV/TXT header is:

```text
selection_id,take_id,source_hash,target_hash,source_start_seconds,source_end_seconds,target_start_seconds,target_end_seconds,gain,notes
```

Times may be numeric seconds or `MM:SS.sss` / `HH:MM:SS.sss` in CSV/TXT.
`target_end_seconds` and `gain` may be omitted when a CompSheet is used only as
descriptive evidence, but both are required before `CompDerivationPass` can run.
The parser does not derive a missing target end or gain.

## Canonical CueSheet JSON

```json
{
  "schema_version": "1.0",
  "sheet_type": "cue",
  "production_title": "Example Film",
  "episode_title": "Episode 1",
  "territory": "Worldwide",
  "cues": [
    {
      "cue_id": "C1",
      "title": "Opening Theme",
      "composer": "Example Composer",
      "publisher": "Example Publishing",
      "usage": "background",
      "isrc": "EX-AAA-26-00001",
      "iswc": "T-000000000-0",
      "asset_hash": "<sha256-of-referenced-audio>",
      "start_seconds": 0,
      "duration_seconds": 25.5,
      "notes": "Opening titles"
    }
  ]
}
```

The equivalent CSV/TXT header is:

```text
cue_id,title,start_seconds,duration_seconds,end_seconds,composer,publisher,usage,isrc,iswc,asset_hash,notes
```

Either `duration_seconds` or `end_seconds` is required. If both are supplied,
they must agree with `start_seconds` within two milliseconds.

## Cross-evidence checks

- A CompSheet `source_hash` must name a submitted artefact.
- When both source and target hashes are supplied, they must match a submitted
  `comped_from` edge.
- CompSheet target ranges are checked against parsed target WAV duration.
- A CueSheet `asset_hash` must name a submitted artefact.
- Cue ranges are checked against parsed referenced WAV duration.
- A structurally unreadable required sheet produces `not_assessed` and lowers
  completeness confidence; merely uploading an arbitrary PDF does not satisfy
  the expectation.

All checks above are `evidence_tier: submitter_document`: they compare one
submitter document with the graph, another declaration or a measured duration.

## Sound reports (`text/sound-report`)

A field-recorder sound report CSV such as Sound Devices writes: `Key:,value`
lines (`Date`, `Frame Rate`, `Sample Rate`, ...) followed by a table whose
header row starts with `File Name`, then `Scene`, `Take`, `Length`, `Start TC`,
`Trk 1`…`Trk n` and `Notes`:

```csv
Date:,"18/12/24",
Frame Rate:,"25 ND",
File Name,Scene,Take,Length,Start TC,Trk 1,Trk 2,Trk 3,Notes
2T01.WAV,2,01,00:00:29,08:49:43:00,"MixL","MixR","Boom","EP2",
```

A row is matched to a submitted WAV by the recorder file name in its `bext`
(`sFILENAME`) **and** the report date, read as dd/mm/yy or mm/dd/yy. Recorders
restart numbering each day, so a same-named file from another day is a
different take, not a contradiction. Takes the submission does not include are
skipped. Checks for a matched row:

| Rule | Compared with | Tier |
|---|---|---|
| `sound_report_start_timecode_matches_recorder` | `bext` time reference, within one frame | `recorder_metadata` |
| `sound_report_scene_take_matches_recorder` | iXML/`bext` scene and take | `recorder_metadata` |
| `sound_report_track_names_match_recorder` | iXML/`bext` track names | `recorder_metadata` |
| `sound_report_length_matches_audio` | measured duration, ±1 s | `submitter_document` |

Drop-frame timecode is not compared (the check is `unavailable`).

## AAF edits (`project/aaf`)

AAF files are read with pyaaf2 (MIT). Each master mob that cuts from a
recorder tape becomes a reference: file name (`TapeID` comment or locator),
tape name, `Scene`/`Take`/`Shoot Date`/`TRKn` comments, and the source
timecode range taken from the tape. Library sound effects, renders and tone
have no tape source and are not references. A reference is matched to a WAV
by (tape, recorder file name):

| Rule | Compared with | Tier |
|---|---|---|
| `aaf_source_range_within_recorded_timecode` | the take's recorded span: `bext` start + duration, ±1 frame | `recorder_metadata` |
| `aaf_shoot_date_matches_recording` | `bext` origination date | `recorder_metadata` |
| `aaf_scene_take_matches_recorder` | iXML/`bext` scene and take | `recorder_metadata` |
| `aaf_track_names_match_recorder` | iXML/`bext` track names | `recorder_metadata` |

Attach both documents to the artefact they describe with an `input_to` edge.

## Ableton Live sessions (`project/daw-session`)

An Ableton Live set (`.als`, gzip-compressed XML, Live 10 and 11 file
references) is parsed for tempo, time signature, tracks and every audio clip's
sample: file name, relative path, original file size, length in samples and
sample rate. Other DAW formats are not parsed yet and stay `not_assessed`. A
set that declares a DTD or entity is refused. All checks are
`submitter_document` tier, because the session is written by the submitter's
own software:

| Rule | Compared with |
|---|---|
| `daw_session_uses_declared_source_audio` | For the artefact the session is attached to (`input_to`): the declared audio sources. Exported stems usually share size, length and rate, so within each group of identical shape the session must use at least as many samples as the graph declares. Files are never matched one by one by guesswork. |
| `daw_session_tempo_matches_midi` | First tempo event of each submitted MIDI file |
| `daw_session_time_signature_matches_midi` | First time-signature event of each submitted MIDI file |

A failed group check is explained with its numbers, for example: "the graph
declares 3 source(s) … with size 88244 bytes, 44100 frames at 22050 Hz, but
the DAW session uses 2 such sample(s)".
