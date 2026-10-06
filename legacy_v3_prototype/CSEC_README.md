# CSEC Readme


Please see the regular `README.md` file for detailed information and installation & running instructions.
This document provides the shortest execution path for the current revision.


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

Install `requirements-dev.txt` when running the test suite, or
`requirements-c2pa.txt` when optional C2PA support is needed.

## Bundled sample

To build and assess the bundled WAV sample, execute:

``` bash
python3 run_pipeline.py bundle_manifest.json --objects-dir objects -o output
```

The detailed output is written to `output/evidence_bundle.json` and
`output/assessment.json`. If an applicable stem-to-mix comparison cannot run,
integrity is marked `partial` and its aggregate numeric score is withheld.
