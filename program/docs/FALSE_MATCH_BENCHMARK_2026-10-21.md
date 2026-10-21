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
| Same genre, tempo and key (different songs) | 5 | 0 false | 0 false | 0 false |
| Same prompt generated twice | 4 | 0 false | 0 false | 0 false |
| Cover prompts, melody unverified | 6 | 0 false | 0 false | 0 supported (unclassified) |
| Shared drum loop, loop gain 0.5 | 4 | 1 detected | 1 detected | 0 false |
| Shared drum loop, loop gain 1.0 | 4 | 4 detected | 4 detected | 0 false |

Every detected shared loop was reported at the correct offset (0.0 s, both
songs contain the loop at 5 s).

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

The counts are recorded under `false_match_rates` in
`policies/audio_calibration_status.json` and appear beside each diagnostic row
and in the builder's calibration summary.

To reproduce: `python -B benchmarks/false_match.py
benchmarks/false_match_lumina_2026-10-21.json --audio <clip folder> --out
<result.json>`; add `--write-status` to update the calibration status.
