# Music Evidence Bundle Prototype

This program builds a music evidence bundle, loads it with Ben's `libevchain`,
runs domain passes, and writes a four-axis assessment. The builder is the only
ingestion entry point: it combines file hashes, parser observations, submitter
declarations, and explicitly declared relationships.

The system supports WAV audio plus baseline DDEX RIN and ERN XML metadata.
C2PA parsing is optional. It never infers a relationship from filenames, file
order, similar metadata, or similar audio.

## Python version

- Required: Python 3.10 or later.
- Tested in this folder with Python 3.13.7.

## Installation

From the `V1` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e ./COMP3988_Evidence_Chains-master
```

The source files can also locate Ben's repository automatically when
`COMP3988_Evidence_Chains-master` remains inside `V1`. The editable install is
recommended because it makes the dependency explicit.

C2PA support is optional:

```bash
python3 -m pip install c2pa-python
```

Without `c2pa-python`, WAV, RIN, and ERN processing still works. A file entry
that explicitly requests `"parse_c2pa": true` fails with an installation
message instead of silently pretending that C2PA was checked.

## Input directory

A typical run uses this layout:

```text
V1/
├── bundle_builder.py
├── bundle_manifest.json
├── evidence_bundle_schema.json
├── music_evidence_types.py
├── music_profile.json
├── music_profiles.py
├── relationship_extractor.py
├── wav_parser.py
├── rin_parser.py
├── ern_parser.py
├── c2pa_parser.py
├── xml_utils.py
├── COMP3988_Evidence_Chains-master/
├── objects/
│   ├── raw-vocal.wav
│   ├── vocal-sample.wav
│   ├── session-rin.xml
│   └── release-ern.xml
└── output/
```

`bundle_manifest.json` names every input file. Relative paths are resolved from
the directory containing that manifest. Exactly one file must use
`"final": true`. The `id` values are local aliases used while building; the
final bundle uses SHA-256 hashes.

Use [bundle_manifest.example.json](bundle_manifest.example.json) as the
starting manifest. Its RIN and ERN relationships are examples of explicit
machine-readable declarations. The `reference_pointer` records where the
claim came from in the source document.

To inspect C2PA embedded in an audio file, add the optional flag to that audio
entry:

```json
{
  "id": "final-master",
  "path": "objects/final-master.wav",
  "artefact_type": "audio/master",
  "final": true,
  "parse_c2pa": true,
  "attributes": {
    "creation_method": "recorded",
    "production": {"master_name": "Demo master"}
  }
}
```

## Running the program

Create the output directory, build the canonical bundle, then evaluate it:

```bash
mkdir -p output
python3 bundle_builder.py bundle_manifest.json -o output/evidence_bundle.json
python3 music_profiles.py output/evidence_bundle.json --objects-dir objects -o output/assessment.json
```

The first command parses files, computes SHA-256 hashes, validates the JSON
Schema, constructs the graph with `libevchain.EvidenceChain`, and writes:

```text
output/evidence_bundle.json
```

The second command binds files to artefacts by their content hashes, runs the
Ben-compatible pipeline and passes, and writes:

```text
output/assessment.json
```

You can also run each parser directly for diagnosis:

```bash
python3 wav_parser.py objects/raw-vocal.wav
python3 rin_parser.py objects/session-rin.xml
python3 ern_parser.py objects/release-ern.xml
python3 c2pa_parser.py objects/final-master.wav
```

Run the built-in integration checks with:

```bash
python3 bundle_builder.py --self-test
python3 relationship_extractor.py --self-test
python3 music_profiles.py --self-test
```

## Parser routing

| Manifest artefact type | Parser used by `bundle_builder.py` | Bundle fields produced |
|---|---|---|
| `audio/*` | `wav_parser.py` | observed technical WAV fields and RIFF/BEXT/INFO metadata |
| `metadata/ddex-rin` | `rin_parser.py` | sessions, contributors, roles, equipment, recording components, warnings |
| `metadata/ddex-ern` | `ern_parser.py` | release id, title, release type, releases, warnings |
| `audio/*` with `parse_c2pa: true` | `c2pa_parser.py` | embedded manifest presence, assertions, ingredients, signature fields, validation status, warnings |
| `provenance/c2pa` | `c2pa_parser.py` | the same baseline C2PA observations for a separately supplied provenance artefact |

Observed parser fields are kept separate from submitter claims. For WAV files,
a manifest `technical` group becomes `declared_technical`, while the actual WAV
header becomes `technical`. A disagreement remains in the bundle so the
integrity pass can report it.

RIN and ERN processing is a namespace-independent baseline, not full DDEX XSD
validation. Parser warnings are preserved in `parser_diagnostics`. The ERN
parser deliberately does not extract AI disclosure because an authoritative
element mapping has not been established in this version.

## Relationship policy

Every graph relationship must be explicitly listed in the manifest's
`relationships` array. The extractor validates the declaration and resolves
its `target` and `source` aliases to artefact hashes. With an empty array, the
output graph has no edges.

A declaration may be attributed to the submitter, a DAW session, DDEX RIN,
DDEX ERN, or a C2PA ingredient. Machine-readable origins require a non-empty
`reference_pointer`. C2PA ingredients are retained as parser output, but the
builder does not automatically turn them into graph edges because the current
parser cannot reliably map every ingredient to a submitted artefact hash. Add
the corresponding `attested_by` or other permitted relationship explicitly
after confirming that mapping.

## Scores and output semantics

The assessment reports these independent axes:

- `integrity`
- `completeness`
- `attestation_strength`
- `ai_disclosure`

It does not produce an `overall_score` field. This avoids choosing weights or
a target-owned accept/reject rule on behalf of CSEC or the client.

`unavailable` means the system had no basis to assess that axis; it is not a
zero score. A missing C2PA manifest therefore does not count as a failed
attestation.

## What the current system checks

- The manifest shape, allowed fields, unique file ids and paths, and exactly
  one explicitly selected final artefact.
- SHA-256 identity for each supplied file and duplicate-content rejection.
- WAV structure and objective header fields such as duration, sample rate,
  channels, bit depth, format, file size, and selected BEXT/INFO metadata.
- Baseline RIN extraction of sessions, contributors, roles, equipment, and
  recording components.
- Baseline ERN extraction of release identifiers, titles, and release types.
- Optional baseline C2PA reading through the official Python binding.
- Bundle attributes against `music_evidence_types.py` and
  `evidence_bundle_schema.json`.
- Relationship type, endpoint compatibility, required machine-source pointer,
  duplicate edges, cycles, and declared time/format/mastering parameters that
  can be compared with observed metadata.
- File binding by recomputing SHA-256 before the passes run.
- Completeness expectations for the workflow visible in the declared graph.
- Presence and clarity of explicit creation-method or AI-use declarations.
- Four-axis pass execution and score-axis merging through Ben's `Pipeline`.

## What the current system does not check

- It does not infer a relationship or production role from filenames, file
  order, timestamps, matching metadata, or audio similarity.
- It does not compare audio samples to prove that one WAV was excerpted,
  edited, mixed, stemmed, mastered, or otherwise derived from another WAV.
- It does not perform complete DDEX RIN or ERN XSD validation.
- It does not extract the newer ERN AI-disclosure extension in this baseline.
- It does not independently re-run C2PA cryptographic verification, decide
  credential trust, or automatically map C2PA ingredients to graph edges.
- It does not prove creator identity, human authorship, or that a submitter's
  declaration is truthful.
- It does not calculate false-accept/false-reject rates or make an
  accept/reject decision.
- It does not combine the four axes into an overall score.
