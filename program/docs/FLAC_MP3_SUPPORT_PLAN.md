# Plan: adding FLAC and MP3 support

Status: proposal, 23 October 2026. Nothing implemented yet.
Authors: Hongjie Deng, for William Alexander.

## Why

The client (Liviu, Alt.VFX, 23 October 2026) suggested that MP3 and FLAC
would be a solid addition, but asked us not to sacrifice depth for breadth:
it is fine to scope to a set of formats suited to our work. FLAC is the
preferred lossless delivery format of streaming services; MP3 is the default
export of AI music tools such as Suno and ElevenLabs.

Design rule that follows from the client's comment: every check we run on a
new format must be as rigorous as on WAV, and every check we cannot run must
be reported as not performed, never silently skipped.

## 1. What we tested (project venv, 23 October 2026)

| Test | Result |
|---|---|
| Decode FLAC with `soundfile` | PCM sample-identical to the source WAV (24-bit, 3 channels) |
| Encode with `flac --keep-foreign-metadata` | BWF `bext` and `iXML` are stored in FLAC `APPLICATION` blocks with ID `riff`; decoding back gives a byte-identical WAV |
| Encode with plain `flac` | `iXML` is dropped (flac warns that it skips the chunk); recorder metadata is lost |
| FLAC `STREAMINFO` | Carries an MD5 of the decoded PCM, usable as a file-integrity check |
| Decode MP3 (LAME 192 kbps) with `soundfile` | Exact sample count (144000, same as the WAV), sample values differ (lossy) |
| C2PA sign and read with c2pa-python 0.37.10 | Works for both FLAC (`audio/flac`) and MP3 (`audio/mpeg`); our existing `parse_c2pa_file` reads both unchanged |
| Where C2PA is stored | MP3: an ID3v2.4 `GEOB` frame with MIME type `application/c2pa`. FLAC: the SDK **prepends an ID3v2 tag before the `fLaC` marker** and stores the manifest there |

The last row causes a real bug: `music_target/wav_io.py` currently decides
the container from the first bytes and treats a leading `ID3` as MP3, so a
C2PA-signed FLAC would be classified as MP3. Format detection must skip an
ID3v2 tag before looking for `fLaC`.

## 2. Libraries

| Library | Licence | Use | Decision |
|---|---|---|---|
| soundfile 0.14.0 (bundles libsndfile 1.2.2 with libFLAC and mpg123) | BSD-3-Clause (libsndfile LGPL-2.1, dynamically loaded) | Decode FLAC and MP3 to PCM: FLAC MD5 check and audio diagnostics | Use. Already pinned in `requirements-audio-lock.txt` |
| c2pa-python 0.37.10 | MIT OR Apache-2.0 | Read and sign C2PA in FLAC and MP3 | Already a dependency |
| tinytag 2.3.2 | MIT | Duration, bitrate and tags | Optional, test cross-checks only; cannot see raw `riff` blocks or `GEOB` frames |
| mutagen 1.48.1 | GPL-2.0-or-later | Most complete ID3/FLAC metadata library | Do not ship; GPL licence |
| audioread, pydub | MIT | Both depend on an external ffmpeg | Do not use |
| flac / metaflac (BSD), LAME (LGPL) | Command-line tools | Create test fixtures and check our parser's output | Development only, not a runtime dependency |

### What soundfile does and does not give us

`STREAMINFO`, `VORBIS_COMMENT` and `APPLICATION` are metadata blocks defined
by the FLAC format itself (RFC 9639), not by any library. soundfile exposes
only part of them:

| FLAC block | Contents | Available through soundfile |
|---|---|---|
| `STREAMINFO` | Sample rate, channels, bit depth, total samples, MD5 of the PCM | `samplerate`, `channels`, `frames`, `subtype` (for example `PCM_24`). **No MD5** and no way to verify it |
| `VORBIS_COMMENT` | Encoder string and arbitrary `KEY=VALUE` tags | A fixed set of common tags through `copy_metadata()` (for example `artist`, `comment`); custom keys and the encoder string are not guaranteed |
| `APPLICATION` `riff` | WAV chunks kept by `flac --keep-foreign-metadata` (`bext`, `iXML`, ...) | **Not available** |

So the work splits into two layers:

- **soundfile decodes audio** (PCM for the MD5 check and for diagnostics).
- **We parse metadata ourselves** with a small, size-bounded parser, as
  `wav_io.py` already does for WAV. Each FLAC metadata block is a 4-byte
  header (last-block flag, type, 24-bit length) followed by its contents, so
  the parser is short. This gives us the raw MD5 and the `riff` blocks.

## 3. Implementation

### Step 0. Shared format detection (about half a day)

- New `music_target/audio_formats.py`: skip a leading ID3v2 tag, then
  recognise `fLaC`, an MPEG frame sync or `RIFF/WAVE`. Only `ID3` not followed
  by `fLaC` is MP3.
- One entry point, `inspect_audio(path)`, returning format, codec, sample
  rate, channels, bit depth, total samples, duration, lossless flag and
  embedded metadata. WAV keeps using the existing reader.
- Update `_OTHER_CONTAINERS` in `wav_io.py`, `WavMetadataPass` in `engine.py`
  and the audio branch in `assessment_passes.py` to dispatch on format.

### Step 1. FLAC (about 2–3 days; do first)

1. **`music_target/flac_io.py`**, a bounded parser (limits on block count and
   size) for:
   - `STREAMINFO`: sample rate, channels, bit depth, total samples, MD5;
   - `VORBIS_COMMENT`: encoder and tags;
   - `APPLICATION` `riff`: rebuild the `bext` and `iXML` chunks;
   - skip `PICTURE`, `PADDING`, `SEEKTABLE`.
2. **Reuse the recorder-metadata checks.** Change
   `recorder_metadata.recorder_fields` to take raw `bext`/`iXML` bytes rather
   than a WAV path, so WAV and FLAC share it. The sound-report and AAF
   cross-checks then work on FLAC takes unchanged.
3. **New integrity check `flac_md5_matches`.** Decode with soundfile in
   streaming blocks and compare the MD5 of the PCM with the `STREAMINFO` MD5.
   An all-zero MD5 (not written by the encoder) gives `unavailable`.
   **Whether this check is scored, and with what weight, must be agreed with
   the teammate working on scoring.**
4. **Report missing recorder metadata honestly.** A FLAC without `riff` blocks
   gets an info finding such as `FLAC_FOREIGN_METADATA_ABSENT`, explaining the
   metadata may have been dropped during conversion. Not a contradiction.
5. **C2PA presence check.** `_reconcile_with_c2pa_chunk` currently uses the
   WAV `C2PA` chunk. It must also recognise a `GEOB application/c2pa` frame
   in the leading ID3v2 tag.

### Step 2. MP3 (about 2–3 days)

1. **`music_target/mp3_io.py`**, a bounded parser for:
   - ID3v2.3/2.4 and ID3v1 tags;
   - the first MPEG frame header (version, layer, bitrate, sample rate,
     channel mode);
   - Xing/Info and LAME headers (frame count, encoder delay and padding,
     giving the exact sample count);
   - the VBRI header.
   Without these headers, estimate the duration from a constant bitrate and
   mark it approximate.
2. **Report the reduced checks explicitly** (the client's "depth" point):
   - bit depth is `null`; declared-bit-depth comparisons are `unavailable`;
   - recorder metadata has no standard MP3 form, so it is reported as not
     applicable to the format, not as missing or contradicted;
   - duration and sample-rate comparisons allow for encoder delay;
   - a new info finding `AUDIO_FORMAT_LIMITED_CHECKS` lists the check
     families that were not run.
3. **WAV-to-MP3 encodes** use `derived_from` with the existing
   `source_format` / `target_format` edge attributes; declared and observed
   formats are compared. No new relationship type.

### Step 3. Audio diagnostics (about 1 day)

- `read_pair` in `music_target/audio_diagnostics/io.py` reads WAV only. Add a
  soundfile path for FLAC and MP3, keeping the current windowed reads and
  memory limits.
- Add `mp3_encoded_queries` to `not_tested` in
  `policies/audio_calibration_status.json`. Optionally encode the Lumina
  false-match clips at 128, 192 and 320 kbps and rerun
  `benchmarks/false_match.py` to see whether results change.

### Step 4. Contract, builder and documentation (about 1 day)

- **Native requests:** no schema change. Object paths may already end in
  `.flac` or `.mp3`, and the file content decides the format.
- **Public CSEC profile:** stays WAV-only. Adding `audio/flac` or `audio/mpeg`
  needs CSEC's agreement.
- **Desktop builder:** `manifest_logic.py`, `wav_probe.py` and the file
  dialogs use the new `inspect_audio`; the error text lists the supported
  formats.
- **Docs:** README, technical manual, `docs/03_CSEC_HANDOFF.md` supported
  scope, `docs/csec_redteam/NATIVE_INPUT_GUIDE.md`.

### Safety limits

- Existing size limits stay (512 MiB per object, 1 GiB per request).
- ID3 frame sizes are bounded; **compressed ID3v2.4 frames are refused**
  (decompression-bomb risk).
- Rebuilt `riff` chunks use the existing `MAX_METADATA_CHUNK_BYTES` limit;
  `iXML` still refuses DTDs and entities.
- The search for the first MPEG frame scans at most 64 KiB after the tags.

### Tests

- **Fixtures:**
  - FLAC written by soundfile, with `riff` `APPLICATION` blocks assembled in
    the test helper;
  - MP3 written by libsndfile (it can encode MPEG Layer III);
  - a few small committed fixtures made with the reference `flac` and `lame`
    tools.
- **Cases:**
  - FLAC with and without recorder metadata;
  - FLAC MD5 matching, mismatching and not written;
  - a C2PA-signed FLAC is not detected as MP3;
  - MP3 exact sample count from the LAME header, and the constant-bitrate
    estimate;
  - C2PA-signed FLAC and MP3 (extend `_sign` in
    `tests/test_c2pa_integrity.py` with a MIME-type argument);
  - wrong extensions, oversized and compressed ID3 frames;
  - core results unchanged with audio analysis on and off.

## 4. Decisions needed first

1. **Make soundfile a core dependency?** Without it, the FLAC MD5 check is
   `unavailable` when the audio extras are not installed. Recommendation: yes
   (BSD licence, version already pinned).
2. **Scoring of the new checks.** Whether `flac_md5_matches` is scored, and
   how MP3's reduced checks affect availability, is for the teammate working
   on scoring. Build the parsers now; wire them into scoring after that work
   is merged.
3. **Public CSEC profile.** Whether to add FLAC and MP3 there needs CSEC.

## 5. Order and split

- Order: Step 0 → FLAC → MP3 → diagnostics and docs; about 6–8 days in total.
- Suggested split: Hongjie takes Step 0 and FLAC (touches recorder metadata
  and the C2PA presence check). William takes MP3 in parallel once Step 0 is
  in. Diagnostics and docs together at the end.
