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
