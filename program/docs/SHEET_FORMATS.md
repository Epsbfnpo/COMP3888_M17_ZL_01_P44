# CompSheet and CueSheet input formats

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
      "notes": "First chorus phrase"
    }
  ]
}
```

The equivalent CSV/TXT header is:

```text
selection_id,take_id,source_hash,target_hash,source_start_seconds,source_end_seconds,target_start_seconds,target_end_seconds,notes
```

Times may be numeric seconds or `MM:SS.sss` / `HH:MM:SS.sss` in CSV/TXT.

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
