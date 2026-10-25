# Attack cost record

Fill in one record per attack attempt, whether it succeeded or not. The
project brief asks for the time and compute an attack costs and for which
checks held; failed attempts are as useful as successful ones.

Copy the block below into a file named `COST-<case id>.md` (or collect the
JSON blocks into one `.jsonl` file).

## Record

```json
{
  "case_id": "",
  "goal": "G1 | G2a | G2b | G2c | G3 | G4 | G5 | G6 | G7",
  "date": "YYYY-MM-DD",
  "tester": "",
  "target": {
    "system_version": "",
    "source_revision": "",
    "command": "run.py --native ... [--audio-analysis]"
  },
  "starting_material": "e.g. Project Rock Ep01 takes 1T04 and 6T03, or Lumina clip <sha256>",
  "outcome": "succeeded | partly succeeded | failed",
  "cost": {
    "human_time_minutes": {
      "research": 0,
      "building_the_package": 0,
      "testing_and_iterating": 0
    },
    "attempts_until_result": 0,
    "skill_level": "none | scripting | audio-engineering | cryptography | specialist",
    "tools": ["e.g. Python 3.13, pyaaf2 1.7.1, bwfmetaedit, c2patool"],
    "custom_code_lines": 0,
    "fields_or_files_forged": ["e.g. bext TimeReference, iXML SCENE/TAKE, sound report Start TC"],
    "compute": {
      "cpu_seconds": 0,
      "peak_memory_mb": 0,
      "gpu_used": false,
      "paid_services_or_credits": "none"
    },
    "evaluator_run": {
      "processing_ms": 0,
      "peak_rss_kib": null
    }
  },
  "result": {
    "execution_status": "",
    "axes": {"completeness": null, "integrity": null, "attestation_strength": null, "ai_disclosure": null},
    "integrity_status": "",
    "contradiction_count": null,
    "integrity_confidence": null,
    "evidence_strength": {"mean_subject_forge_cost": null, "confidence_factor": null, "subjects_by_basis": {}},
    "checks_that_held": ["finding codes or cross_evidence rules that caught the attack"],
    "checks_that_did_not_hold": ["checks that should have caught it but passed"]
  },
  "notes": ""
}
```

## How to measure

- **Human time.** Record working time, not elapsed time. Split it into
  research, building and iterating, so the cost of a reusable script is not
  counted again on every later case.
- **Skill level.** The lowest level needed to repeat the attack with the
  record in hand: `none` (a text editor), `scripting`, `audio-engineering`
  (DAW, recorder or AAF knowledge), `cryptography` (certificates, signing) or
  `specialist` (for example access to a trusted signing key).
- **Compute for building the forgery.** Run the build step under
  `/usr/bin/time -l` on macOS or `/usr/bin/time -v` on Linux and copy the user
  plus system CPU time and maximum resident size.
- **Compute for the evaluator.** Copy `processing_ms` and
  `resource_usage.peak_rss_kib` from the response. `peak_rss_kib` can be
  `null` (it was on macOS in our run); then wrap the `run.py` command in
  `/usr/bin/time` as above.
- **Checks that held.** List the finding codes (for example
  `C2PA_SIGNATURE_INVALID`) or `assessment_checks.cross_evidence` rules (for
  example `c2pa_ai_source_type_matches_creation_method`) that exposed the
  attack, so the cost of defeating each check can be compared.
- **Evidence strength.** Copy `axes.integrity.confidence` and, from
  `assessment_checks.evidence_strength`, `mean_subject_forge_cost`,
  `confidence_factor` and `subjects_by_basis`. Together with
  `fields_or_files_forged`, `skill_level` and the time spent, this lets each
  attempt be compared with the forge costs in `default-v7` (goal G8).
- **Personal data.** Refer to Project Rock files by name or SHA-256. Do not
  copy contact details from the sound reports.
