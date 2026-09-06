# WAV metadata parser

Small baseline for extracting raw metadata from WAV/RIFF files. Deliberately
**not** a complete implementation of every WAV extension in the wild, and it
does **not** score, validate, or normalize anything into any downstream
Evidence Bundle format -- see [Out of scope](#out-of-scope) below.

This package is standalone and has no dependency on, or relationship to,
any DDEX/C2PA parser package -- it can be developed, versioned, and merged
independently.

## What's implemented

`wav_parser.py` reads a `.wav` file and reports:

- **Core format** (from the mandatory `fmt ` chunk): audio format, channel
  count, sample rate, byte rate, block align, bits per sample, plus derived
  `data_size_bytes` and `duration_seconds`.
- **Optional metadata**, if present:
  - `LIST`/`INFO` chunk (title, artist, comment, etc.)
  - Broadcast Wave Format `bext` chunk
  - `cue ` chunk (cue points/markers)
  - `smpl` chunk (sampler/loop data)
  - `fact` chunk (sample count, mainly for compressed formats)
  - A best-effort ID3v2 header (non-standard in WAV but seen in the wild;
    only the header is parsed, not individual frames)
  - Any other/unrecognized chunk, recorded by id/size only -- never guessed

Result is a `@dataclass` (`WavParseResult`), emitted as JSON via
`dataclasses.asdict`. Missing/optional data is always left `None`/`[]`/`{}`
-- never invented. A `warnings` list flags things like a missing mandatory
`fmt ` chunk, a malformed/non-RIFF file, or an unreadable path, so a
mismatch is visible rather than silently producing empty output.

## Installation

```bash
pip install -e ".[dev]"
```

No third-party runtime dependencies -- only the standard library. `pytest`
is pulled in via the `dev` extra for running the test suite.

## CLI usage

```bash
python wav_parser.py path/to/file.wav
```

Prints the result as indented JSON to stdout.

## Out of scope

Explicitly not implemented in this baseline:
- Scoring, or any Completeness/Integrity/Attestation-style calculation
- Decoding individual ID3v2 frames (only the header is parsed)
- Normalization into any Evidence Bundle / provenance schema
- Handling of WAV extensions beyond the chunk types listed above (e.g.
  `iXML`, `axml`/ADM, `resu`, DTS/Dolby metadata chunks)

## Limitations

- **Chunk parsing follows the documented public RIFF/WAVE, BWF (`bext`),
  and ID3v2 layouts** -- these are established formats, not guessed -- but
  has only been exercised against hand-built synthetic fixtures (see
  `tests/fixtures/`), not a broad sample of real files from different DAWs,
  recorders, or mastering tools. Field offsets/sizes for `bext` in
  particular assume the standard (non-extended) 602-byte layout.
- **`tests/fixtures/*.wav` are hand-generated synthetic files** (via the
  stdlib `wave` module, with the `LIST`/`INFO` chunk manually spliced in
  for one fixture) for exercising parser behaviour only -- they say nothing
  about how the parser performs against real-world WAV files.
- **ID3v2 support is header-only.** `id3.note` says as much in the output;
  no frame decoding is attempted.

## Testing

```bash
pip install -e ".[dev]"
pytest
```
