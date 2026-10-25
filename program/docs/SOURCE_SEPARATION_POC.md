# ML source separation for stem provenance (research record)

Status: **completed research, exploratory and uncalibrated**. October 2026.
Outcome: **not integrated.** The evaluator does not import any of this work;
no score, axis, confidence, policy or finding changed.

## Summary

The question was whether an ML source-separation model (Demucs `htdemucs`)
can show that a submitted stem was extracted from the final mix rather than
delivered from the production. Four steps answered it:

1. **Phase 1** (one song): the pipeline works, is deterministic and runs on
   CPU in about 13 s per 7-second excerpt. A genuine stem closely resembles
   the Demucs output (SI-SDR 11.4 dB), as expected for any genuine stem.
2. **Phase 2A** (10 songs, development): similarity to the mix is near
   chance. One indicator, energy above the mix's bandwidth, separated the two
   kinds of stem within each source.
3. **Phase 2B held-out** (39 unseen songs, rules frozen in advance): that
   indicator generalised (best rule 3/155 false positives, 152/155
   detected), but only within one encoding condition.
4. **Phase 2B controls** (MUSDB18-HQ): with genuine full-band stems and an
   MP3 final mix, the same rules flagged 27-30 of 30 genuine stems.

The indicator measures a bandwidth mismatch between stem and mix, which
ordinary deliveries produce, so it is rejected as evidence of ML separation.
No separation-specific signal was found that is both usable in practice and
robust, and no signal needs the ML model at evaluation time. Decision
(recommendation C): integrate neither an ML diagnostic nor a new
deterministic observation; keep this record and the standalone tools.

## 1. Motivation

Client feedback suggested the team may rely too heavily on deterministic and
procedural checks and overlook existing ML and algorithmic tools. A teammate
proposed Demucs and Open-Unmix. The proposed use: given a final mix and a
submitted stem, extract the corresponding instrument from the mix with a
separation model and compare it with the submitted stem, as supporting
provenance evidence, never proof of forgery.

The evaluator already uses algorithmic tools (audfprint, SpectroMap, librosa;
`docs/AUDIO_DIAGNOSTICS.md`). The new element was a learned model.

## 2. Hypothesis and model choice

Hypothesis: a stem extracted from a mix by a separation model differs
measurably from a stem delivered from the production, in ways a reviewer can
measure from the submitted files.

Demucs was tried first: higher published separation quality (about 9.0 dB SDR
on MUSDB18-HQ against about 5-6 dB for Open-Unmix), a maintained fork
(`adefossez/demucs` 4.1.0, July 2026), MIT code, and it is the tool a forger
would most likely use. Open-Unmix (`umxhq`; `umxl` weights are
non-commercial) was kept as a possible second separator but never installed,
because the outcome did not depend on it.

## 3. Scope and safeguards

- Standalone scripts in `benchmarks/`; no file under `music_target/` or the
  evaluator entry points imports them, torch or demucs (checked by a test).
- ML dependencies are optional (`requirements-separation.txt`); the core
  program and every separation test run without torch.
- Reports carry `status: exploratory_uncalibrated`, `scoring_eligible: false`
  and `content_match_is_provenance: false`, and never label a stem genuine or
  otherwise. Thresholds are research measurements, not findings.
- Downloaded audio, model weights and results stay outside the repository.

## 4. Tools

| Script | Purpose |
|---|---|
| `benchmarks/separation_probe.py` | One mix and one submitted stem: separation, three comparisons, JSON report |
| `benchmarks/separation_batch.py` | Many songs: one separation per mix, both candidates per song/source, analysis grouped by song |
| `benchmarks/separation_rules.py` | Freeze candidate rules on development measurements; apply them unchanged to other data |
| `benchmarks/separation_controls.py` | MUSDB18-HQ excerpts by HTTP range requests; codec conditions with ffmpeg |

The probe compares three pairs on a mono downmix at the mix's sample rate:
**A** submitted stem vs mix, **B** derived (Demucs) stem vs mix, **C**
submitted vs derived stem.

| Measure | Meaning |
|---|---|
| `alignment` | Lag and polarity from a pooled cross-correlation refined at the full rate; `secondary_peak_ratio` near 1 means repeated material; `stronger_peak_outside_limit` means the search window was too small |
| `waveform.gain_db`, `pearson_correlation`, `residual_to_reference_db` | Least-squares gain, correlation, and energy left after subtracting the gain-fitted query |
| `waveform.si_sdr_db_query_against_reference` | Pair C only: scale-invariant SDR |
| `spectral.*` | Log-spectrogram correlation, long-term spectrum correlation, log-spectral distance (floored 80 dB below each frame's peak), level difference in five bands |
| `high_frequency` | Per signal: band energy fractions, median 99% roll-off, content edge (highest frequency within 70 dB of the 1-4 kHz median), energy above 16 kHz |
| `crosstalk` | Correlation of a stem with the separator's other outputs, and the energy they explain |

The batch adds per candidate:

- `hf_excess_over_mix_db`: the candidate's level above the mix's content
  edge (+0.5 kHz) relative to its own 1-4 kHz level, minus the mix's level
  there measured the same way.
- `hf_excess_mix_referenced_db` (designed after Phase 2A): the gain-fitted
  candidate's power in that band over the mix's power in it.
- Crosstalk against the genuine other stems (**oracle**: a reviewer does not
  have them).

Metrics comparing a candidate with the separation that produced the derived
candidate (SI-SDR and correlation against the Demucs output, crosstalk
against Demucs' other outputs) are **circular** for that candidate and never
enter a rate.

## 5. Phase 1: proof of concept (one song)

Environment: CPython 3.12.2, torch 2.14.1, demucs 4.1.0
(`requirements-separation-lock.txt`), Apple M2 8 GB, CPU, 4 torch threads.
Weights: `955717e8-8726e21a.th` from `dl.fbaipublicfiles.com`, SHA-256
`8726e21a993978c7…`, matching the checksum in its file name. Demucs loads
`.th` files with `torch.load(weights_only=False)` (a pickle), so only use
weights from the official source with a verified checksum.

Data: MUSDB18-7-WAV (Zenodo 3270814, MD5 `567eb10c…` verified), test split,
"AM Contra - Heart Peripheral", mixture and vocals (7.0 s, 44.1 kHz, 16-bit
stereo). htdemucs was not trained on the test split. These excerpts were
decoded from the AAC-compressed MUSDB18 release: content stops near 17 kHz,
and the mixture is not an exact sum of the stems (largest difference 0.058).

| Measure | A submitted vs mix | B derived vs mix | C submitted vs derived |
|---|---|---|---|
| Pearson correlation | 0.541 | 0.558 | 0.966 |
| Log-spectral correlation | 0.700 | 0.663 | 0.870 |
| Log-spectral distance (dB) | 17.9 | 20.4 | 9.6 |
| SI-SDR (dB) | | | 11.4 |

| Per signal | Mix | Submitted stem | Derived stem |
|---|---|---|---|
| Energy fraction 0-250 Hz | 0.644 | 0.035 | 0.013 |
| Energy above 16 kHz (dB) | -32.4 | -39.0 | -43.2 |
| Content edge (Hz) | 17087 | 17076 | 22050 |
| Correlation with the other separated sources | | 0.042 | 0.042 |

A and B were nearly the same; C was high. Crosstalk did not differ. Above
17 kHz the mix and submitted stem sat 75-79 dB below their 1-4 kHz level and
the derived stem only 48-51 dB below: Demucs put energy into a band the mix
lacks. Four runs took 11.3-12.3 s to separate and 11.8-12.8 s in total, at
about 1.0 GB peak. All four gave identical samples and measurements (the
first two compared directly, the last two by `samples_sha256`).

## 6. Phase 2A: development (10 songs)

The first 10 MUSDB18-7 test songs alphabetically, excluding the Phase 1 song:
40 song/source pairs, none excluded, 188 s, 1.10 GB peak. Data-driven
thresholds were chosen leave-one-song-out (LOSO); intervals resample songs.
A rule `hf_excess_over_mix_db >= 10 dB` was pre-registered from the Phase 1
song before this run.

| Metric | Role | AUC (song bootstrap 95%) | LOSO false positives | LOSO detection |
|---|---|---|---|---|
| `hf_excess_over_mix_db` | usable | 0.85 (0.82-0.88) | 10/40 (Wilson 0.14-0.40) | 37/40 |
| `content_edge_hz` | usable | 0.78 (0.69-0.84) | 18/40 | 40/40 |
| Similarity to the mix (correlation, spectral distance) | usable | 0.58-0.59 | 24-25/40 | 24-25/40 |
| Crosstalk against the genuine other stems | oracle | 0.98 (0.96-1.00) | 2/40 (0.01-0.17) | 36/40 |
| Crosstalk against Demucs' other outputs | circular | 0.48-0.50 | not used | not used |

- The pre-registered rule detected 40/40 derived stems and flagged 19/40
  genuine stems (all 10 bass, 6 drums, 2 other, 1 vocals).
- Within one source the high-frequency excess did not overlap for vocals,
  drums or other; bass overlapped, because the measure is relative to each
  stem's own 1-4 kHz level, which bass barely has.
- Derived stems carry leakage of the other instruments (oracle crosstalk),
  but against the separator's own other outputs it is invisible.

## 7. Phase 2B: held-out validation (39 songs)

Five rules were frozen from the 10 development songs
(`separation_rules.py freeze`; SHA-256 `15e18e57…`, re-checked after every
later run) and applied once to the remaining 39 test songs: 155 pairs (one
silent bass excluded), 481 s, 1.11 GB peak, no development song included.
Rule P was the declared primary hypothesis. S2 and S3 use the metric designed
after Phase 2A, so this run is their first test.

| Rule | Metric, scope | False positives | Detection |
|---|---|---|---|
| P (primary) | `hf_excess_over_mix_db`, per source | 9/155 = 5.8% (Wilson 3.1-10.7%) | 147/155 = 94.8% |
| S0 | same, fixed 10 dB | 83/155 = 53.5% | 155/155 |
| S1 | same, pooled | 36/155 = 23.2% | 143/155 |
| S2 | `hf_excess_mix_referenced_db`, pooled | 7/155 = 4.5% (2.2-9.0%) | 150/155 = 96.8% |
| S3 | same, per source | 3/155 = 1.9% (0.7-5.5%) | 152/155 = 98.1% |

S3 by source (false positives, detection): vocals 0/39, 39/39; drums 3/39,
39/39; bass 0/38, 35/38; other 0/39, 39/39. Every held-out mix stopped at
17.0-20.3 kHz, so all of this is one encoding condition: mix and stems both
AAC-encoded.

## 8. Phase 2B: realistic controls C1-C4

Excerpts (30-37 s) of 10 held-out songs were fetched from MUSDB18-HQ
(Zenodo 3338373, non-commercial research licence) by HTTP range requests,
about 300 MB transferred. The stems sum to the mix within 16-bit rounding
(66-72 dB) and every mix is full-band. Conditions were built with ffmpeg 8.0;
the frozen rules were applied unchanged. 34 pairs per condition (6 stems are
silent in their excerpt).

| Condition | Mix | Candidate stems | P false positives | S3 false positives | Detection (P, S3) |
|---|---|---|---|---|---|
| C1/C2 MP3 | MP3 128 kbps (content to 16.0-16.9 kHz) | genuine full-band WAV | **27/30** | **30/30** | 30/30, 30/30 |
| C1/C2 AAC | AAC 256 kbps (content to 21.6-22.05 kHz) | genuine full-band WAV | not computable | not computable | not computable |
| C3 | full-band WAV | genuine full-band WAV | not computable | not computable | not computable |
| C4 | MP3 128 kbps | genuine stems also MP3 128 kbps | 0/30 | 1/30 | 30/30, 30/30 |

C1 is the false-positive control (genuine rows), C2 the detection on the same
lossy mixes (derived rows). One MP3 song, whose measured mix edge was
22.0 kHz, gave no value and is left out of the counts. In C1, genuine stems
exceeded the mix more than derived ones (mix-referenced excess 13-94 dB
against 26-50 dB; AUC 0.43).

## 9. False-positive findings

| Setting | Best rule false positives | Meaning |
|---|---|---|
| Stems and mix share one lossy codec | held-out S3 3/155 (1.9%); C4 P 0/30, S3 1/30 | The favourable case MUSDB18-7 represents |
| Genuine full-band stems, lossy mix (C1) | P 27/30 (90%), S3 30/30 | The realistic delivery case: fails |
| Full-band or high-bitrate mix (C3, AAC 256 kbps) | undefined | The indicator cannot be computed |
| Similarity to the mix (any phase) | about 60% at chance-level detection | No discrimination |

Intervals: with tens of stems from 10-39 songs, every rate above is a case
count, not a population rate. Song-bootstrap intervals collapse when every
song contributes the same count (for example one bass stem per song), so the
stem-level Wilson interval is the floor of the real uncertainty.

## 10. Why bandwidth mismatch is not proof of ML separation

The high-frequency indicator measures energy in a stem **above the mix's
bandwidth**. That has at least two ordinary causes besides separation:

- The final mix was lossy-encoded (MP3, AAC) while the stems were delivered
  as WAV. Every genuine stem then extends above the mix; C1 showed this
  produces the signature more strongly than Demucs does.
- The stems and the mix went through different resampling, filtering or
  mastering.

It also needs a band-limited mix: with a full-band WAV or a high-bitrate AAC
mix there is no empty band to measure. The indicator only worked when stems
and mix shared one codec, which is not how productions are delivered. It is
a statement about encoding history, which the existing per-file `bandwidth`
diagnostic already reports for each file, not about where a stem came from.

## 11. Why similarity alone cannot establish provenance

- Separation is designed to recover the original stem; the better the model,
  the more a genuine stem and a separated one look alike.
- Both match the mix by construction, which is why `audio_source_coverage`
  reports `contained_in_target` as "not evidence against" a stem.
- Similarity is symmetric: it does not show the direction of derivation.
- The stronger model-free test is already in the evaluator ("content beyond
  the final", `docs/AUDIO_DIAGNOSTICS.md`): a stem separated from the mix
  cannot hold material the mix lacks.
- Earlier signal-similarity scoring (`PhysicalSimilarityPass`) was disabled
  for misleading results on legitimate material (`docs/PHYSICAL_SIMILARITY.md`).

## 12. Why Demucs is not being integrated

- **No signal needs it at evaluation time.** Every rule that discriminated
  uses only the candidate and the mix; Demucs only generated positive
  examples for testing.
- **Its own comparisons are circular or useless.** Against its output a
  derived stem is trivially identical; crosstalk against its other outputs
  did not discriminate (AUC 0.48-0.50). The informative crosstalk needs the
  genuine other stems.
- **The best indicator fails the realistic control** and is not specific to
  Demucs (section 10).
- **Generalisation is untested and unlikely:** one model, four music sources,
  7-second excerpts; another separator or light post-processing changes every
  measurement.
- **Cost:** torch (about 0.9 GB environment), pickled weights, about 12 s
  per 7-second excerpt and 1 GB memory per run.

A deterministic stem-versus-mix bandwidth observation was also rejected: it
would mostly report encoding differences the per-file `bandwidth` diagnostic
already shows, and invite the misreading C1 demonstrates.

## 13. Known false-positive risks

| Risk | Effect |
|---|---|
| Lossy final mix with WAV stems | Genuine stems exceed the mix's bandwidth (C1) |
| Live recordings with microphone bleed | Genuine crosstalk with other instruments |
| Mix-bus or mastering processing | The mix is no longer a linear sum; residuals and band levels move |
| Sparse or band-limited instruments (bass, filtered synths) | Little mid or high-frequency energy distorts self-normalised measures |
| Shared reverbs, bus effects, sidechain ducking | A stem's envelope follows other instruments |
| Repeated loops | Ambiguous alignment (`secondary_peak_ratio`) |
| Stems that differ from the mix version | Lower similarity unrelated to separation |
| Mono downmix | Channel cancellation hides content (warned) |
| Training-set overlap | Unusually good separation inflates similarity |

False negatives are equally possible: another separator, post-processing,
re-encoding or gain changes can hide every trace measured here.

## 14. Limitations

- One model (htdemucs), four music sources, 7-second excerpts from one
  dataset family (MUSDB18); dialogue and sound effects (Project Rock) are out
  of its domain.
- MUSDB mixtures are near-linear sums without a mastering chain.
- Mono comparison only; stereo artefacts were not measured.
- Five rules were evaluated on the held-out set; P was the declared primary,
  and the best of five carries a small selection advantage.
- No threshold is calibrated; every number here is a case count.

## 15. Reproducibility

Results from October 2026 are kept outside the repository under
`~/experiments/` (`separation_probe/`, `separation_batch/phase2a/`,
`separation_batch/phase2b/{dev,heldout,frozen_rules.json,controls/}`), with
data under `~/datasets/` and weights under `~/models/demucs/`.

Environment (separate from the evaluator's):

```sh
python3.12 -m venv .venv-separation        # excluded from git via .git/info/exclude
.venv-separation/bin/python -m pip install -r program/requirements-separation-lock.txt
```

Weights (84,141,911 bytes; the SHA-256 must start with `8726e21a`):

```sh
mkdir -p ~/models/demucs
curl -fL -o ~/models/demucs/955717e8-8726e21a.th \
  https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th
cp .venv-separation/lib/python3.12/site-packages/demucs/remote/htdemucs.yaml ~/models/demucs/
```

Data: MUSDB18-7-WAV (933 MB, Zenodo 3270814) unzipped to
`~/datasets/musdb18-7`. MUSDB18-HQ excerpts are fetched by step 5 below.

Commands, from `program/`, with `PY=../.venv-separation/bin/python` and
`B=~/experiments/separation_batch`:

```sh
# 1. Phase 1 probe (one song)
$PY -B benchmarks/separation_probe.py --mix "$SONG/mixture.wav" --genuine-stem "$SONG/vocals.wav" \
  --source vocals --output-dir ~/experiments/separation_probe/run --model-repo ~/models/demucs
# 2. Phase 2A development batch
$PY -B benchmarks/separation_batch.py --dataset ~/datasets/musdb18-7 --songs 10 \
  --exclude "AM Contra - Heart Peripheral" --output-dir $B/phase2a --model-repo ~/models/demucs
# 3. Recompute development measurements (reusing saved stems) and freeze the rules
$PY -B benchmarks/separation_batch.py --dataset ~/datasets/musdb18-7 --songs 10 \
  --exclude "AM Contra - Heart Peripheral" --reuse-stems $B/phase2a --output-dir $B/phase2b/dev
$PY -B benchmarks/separation_rules.py freeze $B/phase2b/dev/measurements.json --out $B/phase2b/frozen_rules.json
# 4. Held-out validation
$PY -B benchmarks/separation_batch.py --dataset ~/datasets/musdb18-7 --exclude "AM Contra - Heart Peripheral" \
  --skip 10 --songs 39 --rules $B/phase2b/frozen_rules.json --output-dir $B/phase2b/heldout \
  --model-repo ~/models/demucs
# 5. Controls: fetch, verify, build, evaluate (C4 reuses the C1C2_mp3_128 separations)
$PY -B benchmarks/separation_controls.py fetch --songs-from $B/phase2b/heldout/measurements.json \
  --count 10 --out ~/datasets/musdb18hq-excerpts
$PY -B benchmarks/separation_controls.py verify ~/datasets/musdb18hq-excerpts
$PY -B benchmarks/separation_controls.py build ~/datasets/musdb18hq-excerpts --out $B/phase2b/controls/datasets
for c in C1C2_mp3_128 C1C2_aac_256 C3_fullband; do
  $PY -B benchmarks/separation_batch.py --dataset $B/phase2b/controls/datasets/$c --songs 10 \
    --rules $B/phase2b/frozen_rules.json --output-dir $B/phase2b/controls/results/$c --model-repo ~/models/demucs
done
$PY -B benchmarks/separation_batch.py --dataset $B/phase2b/controls/datasets/C4_mp3_128_both --songs 10 \
  --reuse-stems $B/phase2b/controls/results/C1C2_mp3_128 --rules $B/phase2b/frozen_rules.json \
  --output-dir $B/phase2b/controls/results/C4_mp3_128_both
```

Each batch writes `measurements.csv`, `measurements.json` and
`analysis.json`; frozen-rule results are under `analysis.frozen_rules`.
Outputs are never overwritten without `--overwrite`, and a frozen rules file
is never overwritten.

Determinism: the probe sets `shifts=0`, seeds `random`, numpy and torch, and
enables torch's deterministic algorithms. Repeated CPU runs with the same
weights, versions and thread count gave identical samples. Compare runs by
`samples_sha256`, not the file `sha256`: libsndfile writes the current time
into the `PEAK` chunk of every float WAV. A different device, thread count or
torch build can change the last digits.

## 16. Tests

```sh
cd tests && python -B -m unittest test_separation_probe test_separation_batch test_separation_controls
```

28 tests, using generated audio and a stand-in separator, with no torch,
demucs, weights, network access or real audio. They cover alignment, gain,
spectral and high-frequency measures, report structure and hashes,
determinism, overwrite protection, the refusal without weights, batch reuse
of one separation per song, leave-one-song-out isolation, frozen-rule
application, excerpt extraction from a deflated zip, and the codec
conditions (ffmpeg). One test checks that `music_target` never references the
probe or torch.
