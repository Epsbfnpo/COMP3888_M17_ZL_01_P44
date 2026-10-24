# False-match benchmark: unrelated but similar audio (2026-10-21)

Client feedback asked how often the audio diagnostics report a match for pairs
that are unrelated but similar. This benchmark measures that on a small Lumina
sample. Results are case counts, not population rates, and every audio
observation remains non-scoring.

## Material

- 24 clips generated with Lumina (Seed Audio 1.0), 20 seconds each, 48 kHz
  stereo WAV with an embedded BytePlus C2PA manifest. Nine further requests
  were rejected by the platform's audio risk audit and retried. Net usage was
  120.00 credits (balance 38,891.25 → 38,771.25) of the 200 authorised.
- One 10-second drum loop from an earlier Lumina session, used for the
  shared-loop pairs.
- Prompts, file SHA-256 values, categories, pairs and the loop-mixing
  parameters are in `benchmarks/false_match_lumina_2026-10-21.json`. Audio is
  not stored in the repository; the benchmark refuses clips whose SHA-256
  differs.
- Prompts request tempo and key, but the generator does not always follow them,
  so prompts are not measured labels.

## Method

`benchmarks/false_match.py` runs every pair through the real evaluator (core
assessment plus the optional audio diagnostics) as a `derived_from`
relationship and records, per provider, whether it reports a match:

| Provider | Counted as a match |
|---|---|
| audfprint | a candidate offset whose `support_assessment` is `supported_within_profile` |
| fingerprint agreement | an offset where audfprint and SpectroMap each cast at least 5 votes |
| music_semantics | `correspondence_assessment.status` is `supported_within_profile` |

All providers completed for every pair (no unavailable rows). Two complete runs
gave identical results.

## Results

| Category | Pairs | audfprint | Agreement | music_semantics |
|---|---|---|---|---|
| Same genre, tempo and key (different songs) | 5 | 0 false (0–43%) | 0 false (0–43%) | 0 false (0–43%) |
| Same prompt generated twice | 4 | 0 false (0–49%) | 0 false (0–49%) | 0 false (0–49%) |
| Cover prompts, melody unverified | 6 | 0 false (0–39%) | 0 false (0–39%) | 0 supported, unclassified (0–39%) |
| Shared drum loop, loop gain 0.5 | 4 | 1 detected (5–70%) | 1 detected (5–70%) | 0 false (0–49%) |
| Shared drum loop, loop gain 1.0 | 4 | 4 detected (51–100%) | 4 detected (51–100%) | 0 false (0–49%) |

Bracketed figures are 95% Wilson intervals for the count (added 2026-10-23).
Every detected shared loop was reported at the correct offset (0.0 s, both
songs contain the loop at 5 s).

## FLAC and MP3 versions (2026-10-23)

The same 23 pairs were rerun with `--encode flac` and `--encode mp3`: every
clip and every shared-loop mix was re-encoded by soundfile before evaluation
(MP3: libsndfile 1.2.2 with LAME 3.100, VBR, about 140–163 kbps average). The
WAV run was repeated first and reproduced the counts above exactly. Each run
took about 45 s.

| Category | Pairs | FLAC (all providers) | MP3 audfprint | MP3 agreement | MP3 music_semantics |
|---|---|---|---|---|---|
| Same genre, tempo and key | 5 | as WAV | 0 false (0–43%) | 0 false (0–43%) | 0 false (0–43%) |
| Same prompt generated twice | 4 | as WAV | 0 false (0–49%) | 0 false (0–49%) | 0 false (0–49%) |
| Cover prompts, melody unverified | 6 | as WAV | 0 false (0–39%) | 0 false (0–39%) | 0 supported (0–39%) |
| Shared drum loop, gain 0.5 | 4 | as WAV | **0 detected** (0–49%) | **0 detected** (0–49%) | 0 false (0–49%) |
| Shared drum loop, gain 1.0 | 4 | as WAV | **3 detected** (30–95%) | **3 detected** (30–95%) | 0 false (0–49%) |

FLAC gave identical results for every pair, as expected for identical
samples. MP3 produced no false match, but fingerprint recall of a shared loop
fell: the half-gain loop in SL_SG2a_SG4a and the full-gain loop in
SLF_SG1a_SG2a, both found at 0.0 s in WAV, were not found in MP3. Lossy
encoding removes some of the spectral peaks the fingerprints use. With four
pairs per category this is a case observation, not a recall estimate; MP3
recall for excerpts and mixture components is still uncalibrated, so
`mp3_encoded_queries_beyond_tested_configuration` stays under `not_tested`, and
this configuration is recorded under `encoded_configurations_tested`. The counts are recorded under
`encoded_false_match_rates` in `policies/audio_calibration_status.json`.

## Free-site MP3s with shared bursts (2026-10-24)

The Testing_music set includes five unrelated tracks by different artists,
downloaded as "320 kbps" MP3s from one free-music site (Alex MakeMusic,
Flint, The Lonely Ramblers, Tristan Barton, ZISO). In 6 of the 10 pairs
between them (95% CI 31–83%), audfprint reports a supported match at offset
0.0 s. The supported windows fall at the same times in each of these pairs: about
10.3, 30.5–33, 60.5–61.5, 80.5–82.5, 100.9, 111.1 and 131 s. For example,
Alex MakeMusic and Flint share 9 landmark pairs, about 265 times the
background of 0.034 votes per offset bin, in five windows of 0.25–0.55 s.
The site appears to insert the same short sound at fixed times, like an
audio watermark.

This is real shared audio, so the match is correct in the same sense as a
shared drum loop: the files contain the same sound at the same time. It does
not mean the songs derive from each other. Fingerprint agreement also lists
offset 0.0 s in these pairs, often with ±50.1 s and 150.3 s, the spacing
between the bursts. music_semantics abstained in all 10 pairs, so no musical
correspondence was supported. The frozen gates were not changed; the case is recorded here because
any distributor that stamps a tag onto every file creates the same pattern,
and a short, offset-0 match spread across fixed times should be read
together with the supported windows. These files are not part of the
benchmark counts above.

## Interpretation and limits

- No provider reported a match between unrelated songs of the same genre,
  tempo and key, or between two generations of the same prompt.
- A shared loop is real shared audio, so a fingerprint match is correct; it
  does not mean one song derives from the other. A loop mixed at half level was
  missed in 3 of 4 pairs, consistent with the documented limitation that weak
  mixture components can be missed.
- Covers are still untested. The cover prompts asked for Ode to Joy and Twinkle
  Twinkle Little Star, but pyin melody tracking (110–1400 Hz, voiced
  probability > 0.5, notes of at least 3 frames, repeated notes merged) found no
  clip matching more than 3 of 9 (Ode to Joy) or 1 of 7 (Twinkle) template
  intervals. The generator therefore did not recognisably perform the melodies,
  and these pairs cannot measure cover recall. Real cover recordings are needed.
- 19 unrelated pairs and 8 shared-loop pairs are far too few to estimate a
  false-positive rate; zero false matches here does not imply zero in general.
  The intervals make this concrete: 0 false matches in 4 same-prompt pairs is
  consistent with a true rate anywhere from 0 to 49%. The categories cannot be
  pooled into one rate, because they test different kinds of similarity.
- The benchmark ran on 21 October, after the gate profile was frozen on
  20 October, and no gate was changed afterwards, so its split is `held_out`.

The counts are recorded under `false_match_rates` in
`policies/audio_calibration_status.json`, each category with its `wilson_95`
interval and the record with `split: held_out`, and appear beside each
diagnostic row and in the builder's calibration summary (for example
"same prompt regeneration 0/4 false (95% CI 0-49%)").

To reproduce: `python -B benchmarks/false_match.py
benchmarks/false_match_lumina_2026-10-21.json --audio <clip folder> --out
<result.json>`; add `--write-status` to update the calibration status.
