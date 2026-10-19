# Music benchmark

`music_benchmark.py` runs the evaluator, with audio analysis, on public music
datasets whose metadata states which stems make each mix. That metadata is the
ground truth. For every track it declares the true stems as `mixed_from` the
mix, plus one **control**: a stem from another track declared as a source of
the same mix. It then reports, per audio observation, how often a true
relationship outranks a control (ROC AUC). This is how thresholds should be
calibrated and how detected-section methods can later be compared with fixed
phrases. Synthetic fixtures cannot do that.

No dataset is included. Check each dataset's licence before use.

| Dataset | Content | Download (checked 2026-10-19) | Licence |
|---|---|---|---|
| BabySlakh (16 kHz) | 20 tracks: MIDI per instrument → rendered stems → mix | `https://zenodo.org/records/4603870/files/babyslakh_16k.tar.gz`, 883 MB | CC BY 4.0 |
| Slakh2100 | 2100 tracks, same layout (FLAC) | Zenodo record 4599666, about 100 GB | CC BY 4.0 |
| MUSDB18-HQ | 150 tracks: drums, bass, other, vocals → mixture (WAV) | `https://zenodo.org/records/3338373/files/musdb18hq.zip`, 22.7 GB | Research use only; see the dataset terms |

## Run

```sh
python -B benchmarks/music_benchmark.py slakh /data/babyslakh_16k --out /tmp/bench-slakh
python -B benchmarks/music_benchmark.py musdb /data/musdb18hq/test --out /tmp/bench-musdb --limit 10
```

- **Requests and workflows:** Slakh tracks become `project/midi` → `audio/stem`
  (`rendered_from`) → `audio/mix` (`mixed_from`) under
  `midi_virtual_instrument_production`. MUSDB tracks become stems → mixture
  under `multitrack_recording_mix_master`. FLAC is converted to WAV with
  soundfile.
- **One process per track:** each track runs as its own `run.py` process. With
  `psutil` installed, the whole process tree is killed above 2 GB RSS and the
  run is recorded as `killed_over_memory_limit`.
- **Outputs:** `rows.json` holds one row per relationship and observation.
  `summary.json` holds per-run time and memory and the AUC per metric.

The full datasets are large and the run is CPU-heavy. Run them on a cloud or
lab machine rather than a laptop. The harness itself is covered by
`tests/test_music_benchmark.py` on a generated two-track dataset.
