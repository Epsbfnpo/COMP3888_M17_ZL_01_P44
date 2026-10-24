# Plan: adding FLAC and MP3 support

Status: **implemented on branch `feature/flac-mp3-support` (23 October 2026)**,
Steps 0–4 below. Authors: Hongjie Deng, for William Alexander.

## Implementation status

| Step | Done | Where |
|---|---|---|
| 0. Format detection | Content decides; ID3v2 tags are skipped before deciding, so a signed FLAC is FLAC | `music_target/audio_formats.py`, `wav_io.py` |
| 1. FLAC | Bounded block parser, kept `bext`/`iXML`/`LIST` chunks feed the recorder, sound-report, AAF and chronology checks; `flac_md5_matches_decoded_audio`; C2PA presence from the leading ID3v2 `GEOB` frame | `flac_io.py`, `audio_observations.py`, `recorder_metadata.py`, `engine.py`, `assessment_passes.py` |
| 2. MP3 | Every frame header walked; exact length from Xing/Info + LAME delay/padding, otherwise estimated and `duration_seconds: null`; unmeasurable fields give `unavailable` checks; `AUDIO_FORMAT_LIMITED_CHECKS` | `mp3_io.py`, `audio_observations.py`, `engine.py` |
| 3. Diagnostics | FLAC/MP3 decoded with soundfile; snapshots keep their extension; `mp3_encoded_queries` added to `not_tested` | `audio_diagnostics/io.py`, `runner.py`, `policies/audio_calibration_status.json` |
| 4. Contract, builder, docs | Public types `audio/flac` and `audio/mpeg`; request-schema object paths accept `.flac`/`.mp3`; builder probe uses the same parsers; README, CSEC handoff, red-team guide, diagnostics doc | `contract.py`, `schemas/scoring-request.schema.json`, `check_public.py`, `wav_probe.py`, `builder_*.py`, `manifest_logic.py` |

Decisions taken (23 October 2026): soundfile 0.14.0 is a core requirement;
scoring follows the existing WAV rules for now and will be revisited with
the scoring update; the public CSEC profile accepts FLAC and MP3.

After the merge with main (24 October 2026, scoring profile `default-v6`),
FLAC and MP3 still follow the WAV rules, which are now: declared technical
values (`technical.*_matches_observed`) are scored in declaration consistency,
one fact per declared property, and claims MP3 cannot measure stay unavailable
facts that are not counted; the FLAC MD5 check compares the file with itself,
so it stays in file integrity. On the Project Rock Ep01 variants the
conclusions below are unchanged; the assessed fact counts are lower (96
instead of 122 for WAV) because the sound-report and AAF claims about each
take now count once.

Differences from the plan below:

- **Finding codes.** FLAC and MP3 use format-neutral `AUDIO_OBSERVATION` and
  `AUDIO_PARSER_FAILED` (rule `audio_parser_available`) with an
  `audio_format` field. WAV keeps its existing codes, so WAV responses are
  unchanged.
- **`FLAC_RECORDER_METADATA_ABSENT`** is reported only for recordings
  (`audio/raw-take`, `audio/raw-track`), where recorder metadata is expected;
  a FLAC master without WAV chunks is normal.
- **LAME tag CRC.** Recorded as `lame_tag_crc_valid` but not required: FFmpeg
  writes a wrong CRC for some sample rates while its delay and padding are
  correct, and decoders use them anyway.
- **Edge `source_format`/`target_format` are still not compared.** These
  free-text fields have no defined vocabulary ("wav", "WAV", "audio/wav"), so
  comparing them would create false contradictions. The artefact-level
  `technical.format` claim is compared, with the values `WAV`, `FLAC`, `MP3`.
- **False-match benchmark on FLAC and MP3** (`benchmarks/false_match.py --encode`,
  23 October 2026): FLAC identical to WAV; MP3 no false match, but shared-loop
  recall fell (half gain 0/4 instead of 1/4, full gain 3/4 instead of 4/4).
  See `docs/FALSE_MATCH_BENCHMARK_2026-10-21.md`.
- **Real material** (Project Rock Ep01 transcoded, 23 October 2026): FLAC
  takes with kept metadata give identical results to WAV; plain FLAC takes
  lose the recorder evidence and Integrity is withheld; an MP3 mix at 320 or
  128 kbps keeps every result except chronology. See
  `docs/PROJECT_ROCK_FINDINGS_2026-10-22.md`.
- **Real AI-platform MP3** (BytePlus Lumina, Seed Speech 2.0 with Format
  mp3, 24 October 2026, 1.5 credits for three 4-second clips; two were
  downloaded, 24 kHz and 48 kHz). Each file is CBR 64 kbps mono with an
  ID3v2.4 tag of about 21.7 KB holding `TSSE` (FFmpeg `Lavf58.76.100`) and a
  `GEOB application/c2pa` frame, and no Xing/LAME header, so the exact length
  is unknown (estimated 3.864 s and 3.432 s, equal to ffprobe). The C2PA
  manifest validates; the signer is the same BytePlus certificate as Lumina's
  WAV output, so it matches the known AI signer (listed as "BytePlus Lumina
  (Seed Audio)", which therefore covers Seed Speech too) and stays `untrusted`
  (not on the C2PA Trust List). Declared `ai-generated`: Integrity 1.0
  consistent; declared `recorded`: `c2pa_ai_source_type_matches_creation_method`
  fails and Integrity is 0.5 contradicted. Declared bit depth and duration
  give `unavailable` checks, sample rate and channels match.
- **libsndfile MP3 length bug found and handled.** Without a Xing header,
  libsndfile estimates an MP3's length from the bytes it is given and decodes
  only that far: for the Lumina files it counted the 21.7 KB tag as audio
  (6.58 s instead of 3.86 s), and for a variable-bitrate file it stopped early
  and dropped real audio. The diagnostics reader now decodes only the MPEG
  frames (which fixes constant-bitrate files such as Lumina's), takes the
  expected length from the frame walk, and reports `decoder_length_mismatch`
  instead of analysing a truncated signal when the decoder delivers fewer
  samples (variable-bitrate files without a Xing header). Without gapless
  information a shortfall of up to two frames is accepted, because libsndfile
  also drops part of the last frames of a constant-bitrate file (439 samples
  on the 2-minute Jingle Bells MP3 from the Testing_music set, which the
  first version of the guard refused).
- **Testing_music run and fake formats** (24 October 2026). The program was
  run on 11 downloaded files (6 free-site MP3s, Bach as WAV and FLAC at
  16/44.1 and 24/96, one Lumina 16 kHz MP3) and 7 locally made files
  following SoniqTools' test-audio page: genuine 128 and 320 kbps MP3s, a
  320 kbps MP3 re-encoded from 128, a FLAC made from the 128 kbps MP3, and a
  24/96 FLAC and WAV upsampled from CD audio. Every case ran. The pair
  diagnostics found offset 0.0 between FLAC and WAV, 96 and 44.1 kHz, MP3 and
  WAV, and between each fake and its source. The structural checks could not
  see the fakes: a fake FLAC has a valid MD5 and a fake 320 kbps MP3 has
  valid 320 kbps frames. The new report-only `bandwidth` diagnostics
  provider (`audio_diagnostics/providers/bandwidth.py`, see
  `AUDIO_DIAGNOSTICS.md`) measures where each file's audio really stops. It
  flags all four trick files and none of the four genuine lossless files;
  both genuine encodes read `consistent_with_bitrate`. All six free-site
  "320 kbps" MP3s stop at 16.5–18.2 kHz, so they were probably made from
  lower-bitrate files. It never scores, because AI generators also produce
  band-limited audio (all 25 Lumina benchmark WAVs stop at 17.4–19.9 kHz).
- **Also fixed:** the builder's `mixed_from` endpoint list now allows
  `audio/raw-take`, as the evaluator has since 0.8.1.

Verification: 29 new tests in `tests/test_audio_formats.py`, then 9 in
`tests/test_bandwidth.py` and one builder test (386 tests in total; 403 after the merge
with main); the existing WAV
examples regenerate identically apart from `source_revision` and the new
`mp3_encoded_queries` calibration note.

## Review of the cross-format improvement plan (24 October 2026)

The team's plan "跨格式音频分析与证据评分改进方案" (24 October 2026) was checked
against the merged program. Each proposal was adopted, deferred or rejected:

| Proposal | Decision | What changed or why not |
|---|---|---|
| Shared analysis, format-specific reading and timing | Already in place | `audio_diagnostics/io.py` decodes once per format; `bandwidth` keeps the original rate |
| Do not hide decoded-level changes | Already in place | Decoded audio is not peak normalised; mono cancellation is `channel_cancellation` |
| Record the decoder | Adopted | `audio_diagnostic_summary.decoders` (soundfile, libsndfile) |
| Split the MP3 "not tested" label | Adopted | `encoded_configurations_tested` plus `mp3_encoded_queries_beyond_tested_configuration` |
| Multi-bitrate cross-format validation set | Deferred | Needs licensed source recordings; design in `NEXT_TASKS.md` item 31 |
| Bandwidth table as exploratory only | Already in place | `exploratory_uncalibrated`, messages name the alternatives |
| FLAC MD5 as an internal check, never origin proof | Already in place | Stays in file integrity, one check per file; unavailable is never a failure |
| Show sample count apart from the MD5 | Rejected for now | libsndfile decodes exactly the STREAMINFO count, so the comparison is not independent; a wrong count already fails the MD5 or decoding (`NEXT_TASKS.md` item 37) |
| Cross-file sample identity and sample-digest cache keys | Deferred | New report-only feature; needs canonical rules for 8-bit, EXTENSIBLE and float WAV (item 35) |
| AAF-used takes in the coverage denominator; material and support coverage | Adopted | `support_breadth`: `not_submitted`, `unmatched_reference`, `coverage_scope`, `material_coverage`, `support_coverage`; report-only |
| Missing AAF take is not a contradiction, no per-item penalty | Already in place, kept | The gate stays off; a missing take only lowers coverage |
| Which recordings a workflow requires | Client decision | `NEXT_TASKS.md` item 34 |
| Undated or incomparable times reported as unknown | Adopted | `declared_chronology` rows with `status: not_comparable` and a reason |
| Chronology scoring rules | Client decision | Stays report-only (item 36) |
| Keep confidence as layers checked; show coverage beside it | Adopted | Coverage is a separate field; the confidence formula is unchanged |
| Version and shadow-run any scoring change | Followed | No profile change; only report fields and the reasoning text changed |

The workflow policy is now loaded once per evaluation and shared by
completeness and `support_breadth`. Recorder metadata and a FLAC's MD5 can be
rewritten together with the audio and the file rehashed; this is listed as a
known limitation in the README, the technical manual and the red-team guide.

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
