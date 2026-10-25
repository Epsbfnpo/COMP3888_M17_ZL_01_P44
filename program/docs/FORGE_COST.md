# Forge cost and Integrity confidence

Scoring profile `default-v7` grades how hard the checked cross-evidence would be
to forge and scales Integrity **confidence** by it. The Integrity **value**,
the evidence tiers, the release gate, the layer weights and the severe cap are
unchanged. Profiles without `integrity.forge_cost` keep the previous
layer-count formula.

## What the tiers and forge cost each do

| | Tier | Forge cost |
|---|---|---|
| Question | May this submission release a numeric Integrity value? | How much should we trust the value that was released? |
| Granularity | Three tiers: `signed_attestation`, `recorder_metadata`, `submitter_document` | 1-5 per assessed cross-evidence fact |
| Effect | Release gate: needs an assessed fact from a qualifying tier | Confidence factor only |

The profile loader enforces the tier order on the costs:
`signed_attestation >= recorder_metadata > every submitter document`. Forge
cost grades evidence within and below the tiers; it can never reorder them.

## Formula

```
fact cost      = tier cost (signed, recorder), else the document type's cost,
                 else the plain-document cost; 1 if any document or the
                 recording it is about is not hash-bound
subject cost   = the highest fact cost about that subject (one recording,
                 or one document collection), whatever the check calls the hash
strength       = mean subject cost over subjects with an assessed fact
factor         = strength_floor + (1 - strength_floor) x strength / scale_max
confidence     = ceiling x checked layers / 3 x factor
```

With no assessed cross-evidence the strength is the plain-document cost: only
the submission's own claims were checked. Taking the strongest fact per subject
means an extra, weaker document about the same recording never lowers
confidence. A recording backed only by a weak document does lower it, because
more of the result rests on weak evidence.

`assessment_checks.evidence_strength` lists every subject with its cost and
basis, and the Integrity reasoning states the mean cost and the factor.

## Costs in default-v7 and how they were derived

`benchmarks/forge_cost.py` forges claims in copies of Project Rock Ep01 and
runs the core evaluator on each (results:
`benchmarks/results/forge_cost_project_rock_2026-10-25.json`). The measured
cost is the size of the smallest set of files that must change together for
the forged claim to raise no contradiction, plus one when the set includes a
binary container (BWF/iXML chunks, AAF) that needs specialist tools.

| Forged claim (take 10T07) | Forged in | Contradictions | Measured cost |
|---|---|---|---|
| Take number 07 to 08 | WAV only | 1 (sound report) | |
| | WAV + sound report | 1 (AAF) | |
| | WAV + AAF | 1 (sound report) | |
| | WAV + sound report + AAF | 0 | **4** |
| Start timecode +1 s | WAV only | 1 (sound report) | |
| | WAV + AAF | 1 (sound report) | |
| | WAV + sound report | 0 | **3** |
| Cue range start 0 s to 2 s | cue sheet only | 0 | **1** |

| Evidence | Cost | Basis |
|---|---|---|
| Trusted C2PA signature | 5 | Top of the scale: it cannot be produced by editing files; an edited signed Lumina file becomes `invalid` |
| Recorder metadata | 3 | The cheaper of the two measured recorder claims (rule stated before the run) |
| AAF or DAW session, not checked against recorder metadata | 2 | One file plus the binary surcharge |
| Any other submitter document (sound report, cue or comp sheet, DDEX, declarations) | 1 | Measured cue-sheet cost |
| Unbound document or recording | 1 | Anti-gaming rule: no cost above the plain-document level |

The timecode result is a finding in its own right. The AAF only checks that
each used range lies inside the recording, so a start timecode moved by one
second in both the WAV and the sound report passes. Comparing the AAF-used
seconds with the audio of the mix (the report-only `AUDIO_AAF_RANGE_CORRESPONDENCE`
diagnostic) is the natural way to close it.

## Ranking check

Six evidence mixes built from half of the Ep01 takes were scored under strength
floors 0, 0.25, 0.5, 0.75 and 0.9. Five orderings were stated before the run.
Strict orderings had to hold by at least 0.02; non-decreasing ones could not
fall.

| Ordering | default-v6 | default-v7 (any floor 0-0.9) |
|---|---|---|
| sound report on recorder takes > cue sheet only | fails (equal) | holds |
| AAF on recorder takes > cue sheet only | fails (equal) | holds |
| cue sheet only > no documents | holds | holds |
| cue sheet + sound report >= sound report | holds | holds |
| AAF + sound report >= AAF | holds | holds |

Confidence at floor 0.5: recorder-backed mixes 0.427, cue sheet only 0.32, no
documents 0.16 (default-v6: 0.533, 0.533 and 0.267). The ranking bounds the
floor to an interval and cannot pin a value, so the pre-registered 0.5 was
kept.

## Held-out check

The unmodified Ep01 submission was evaluated once, after the costs and floor
were fixed. Integrity value 1.0 (unchanged), confidence 0.427 (default-v6:
0.533), all 13 recordings backed by recorder metadata. This equals the
development mix with half the takes, so more takes with the same evidence
neither raise nor lower it.

## Limits

- These are measured forgery costs on one production and a ranking check, not
  calibrated probabilities. A confidence of 0.43 does not mean a 43% chance of
  anything.
- The costs count files and tools, not skill or time. The CSEC red-team cost
  records (`docs/csec_redteam/COST_RECORD_TEMPLATE.md`) are the planned source
  for refining them; none had been returned on 25 October 2026.
- AAF, DAW and DDEX costs for documents that are not checked against recorder
  metadata follow the stated rule; Project Rock has no such facts to measure.
- Report-only audio findings such as `AUDIO_TAKE_CONTENT_BEYOND_FINAL` do not
  raise any cost. That waits for the client's accuracy bar for audio
  diagnostics.
- Like the other profile policies, the scoring profile is identified by
  `profile_id` and `profile_version` in every response; it is not pinned by
  SHA-256.
