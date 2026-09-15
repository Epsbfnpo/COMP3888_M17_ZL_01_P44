# CSEC Readme


Please see the regular `README.md` file for detailed information and installation & running instructions.
This document provides information on some basic executions of the current revision.


## Supported media types:
So far, we have implemented support for:
- WAV audio files
- DDEX RIN & ERN documents

## Example outputs:
Example outputs can be found in `output/`.
Outputs from `bundle_builder.py` begin with `evidence_bundle...` and output examples from the entire pipeline begin with `assessment...`.
The primary output example is the pair `evidence_bundle.json` and `assessment.json`.

## Installation:
This is identical to `README.md`
```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e ./COMP3988_Evidence_Chains-master
```


## Honest sample:
To run the honest sample, execute:

``` bash
python3 bundle_builder.py bundle_manifest.example.json -o output/evidence_bundle.json
python3 music_profiles.py output/evidence_bundle.json --objects-dir objects -o output/assessment.json
```

## Honest sample with contradiction:
An honest sample with a contradiction between the bundle manifest and WAV metadata has been included to demonstrate the impact of a contradiction in `assessment.json` output.

``` bash
python3 bundle_builder.py bundle_manifest_contradiction.example.json -o output/evidence_bundle_contradiction.json
python3 music_profiles.py output/evidence_bundle_contradiction.json --objects-dir objects -o output/assessment_contradiction.json
```

To highlight the impact of a contradiction, observe:
``` bash
diff assessment.json assessment_contradiction.json
```

## Malformed sample:
A sample with malformed DDEX RIN file has also been included:
``` bash
python3 bundle_builder.py bundle_manifest_malformed.example.json -o output/evidence_bundle_malformed.json
```

