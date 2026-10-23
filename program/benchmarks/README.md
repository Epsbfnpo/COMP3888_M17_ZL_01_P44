# Music benchmark

For the October pilot remediation status, remaining decisions and changed
observation semantics, see [the remediation record](../docs/REMEDIATION_2026-10-20.md).
Structure benchmark evaluation additionally needs `requirements-benchmark.txt`
(`mir_eval`); install it alongside the runtime and audio dependencies.

## Case counts and intervals

`audio_calibration.py` stores a `summary` beside its rows and prints it: each
case count (derivatives supported, correct transpositions, controls supported,
or per fingerprint density the mixtures found at the correct offset and the
controls with a supported offset) with a 95% Wilson interval. `false_match.py`
adds a `wilson_95` interval to every category and marks its record
`split: held_out`. Both use `music_target/case_intervals.py`. The intervals
describe single small counts, not population rates; cases from one family are
not independent.

## Calibration manifest checks

`python -B benchmarks/audio_dataset.py /path/to/manifest.json` validates the
offline manifest described below. It never accesses Lumina or spends credits.
Keep originals and every derivative in the same source family and partition.
Mixtures must list every parent family. Identical audio hashes cannot appear
across development, validation and test, even under different family IDs.

```json
{
  "schema_version": "1.0",
  "cases": [{
    "case_id": "song01-original",
    "family_ids": ["song01"],
    "partition": "development",
    "audio_sha256": "<64 lowercase hex digits from the downloaded file>",
    "parent_case_ids": [],
    "requested_properties": {"tempo_bpm": 120, "form": "ABA"},
    "verified_labels": [],
    "generation": {
      "status": "succeeded",
      "submitted_prompt_text": "The exact final text actually submitted to Lumina",
      "charged_credits": 8,
      "refund_status": "unknown"
    }
  }]
}
```

Replace the illustrative hash before validation. `requested_properties` are
requests, never ground truth. Add `verified_labels` only after verification,
with `property`, `value`, `method` and `annotator_or_tool`. Allowed methods are
`manual_review`, `deterministic_transform`, `independent_measurement` and
`dataset_annotation`. For example a known time-stretch can label its exact
factor, but does not establish verse/chorus labels. Record the transform/tool
version and parameters in the label as additional metadata. The analyzer under
test cannot validate its own labels. The validator checks recorded provenance,
not annotation correctness. Failed generation rows use `status: failed` and
`audio_sha256: null`. Local/external dataset rows may omit `generation`.
Use `refund_status: unknown` when the refund is unverified; do not invent
an actual prompt or assume a failure was refunded. A row whose actual submitted
prompt is unknown cannot yet pass this schema and must be repaired from logs.
Refund states: `unknown`, `not_applicable`, `pending`, `refunded`, `not_refunded`.
The five pilot families still require independent labels and more families.

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
