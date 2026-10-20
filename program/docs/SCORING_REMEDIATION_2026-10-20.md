# S02 / S03 / S05 / S06 scoring remediation

Date: 2026-10-20. Applied after the initial V3 remediation, on the working tree
based on Git HEAD 588d7e5. The user authorised S02, S03 and S06, and explicitly
selected the S05 policy: the origin-not-authenticated finding must not score;
recorder-metadata consistency checks remain scored and release-eligible.

## Resulting behaviour

| Issue | Before | After |
|---|---|---|
| S02 | Many agreeing report copies diluted a conflicting report; repeated errors could also multiply penalties | One vote per rule and subject. Any assessed failure keeps that fact failed. Copies cannot add support or another penalty for that fact. |
| S03 | A contradiction could bypass the numeric release gate and yield a high score where the consistent baseline was null | Every numeric release meets the configured layer/support gate. Contradictions remain findings and a separate status. Without qualifying evidence the value stays null, including severe contradictions. |
| S05 | Recorder metadata could be read as authenticated recording origin | Consistency checks still score and qualify release. Every bound WAV with BWF/iXML fields receives an informational origin-not-authenticated finding, with no score effect. |
| S06 | AI disclosure could be mistaken for an AI-content detector | Displayed as Creation-method disclosure coverage. Counts, limitations and an informational finding explicitly state that no detector ran and declaration truth was not verified. The wire key and numeric definition remain compatible. |

## S02: fact units and conflicts

`assessment_checks.cross_evidence` retains every raw comparison and document
hash. `cross_evidence_scored_facts` is the scored input to the cross-evidence
layer. A fact has a stable ID, subject, contributing observation indices,
assessed tiers, document hashes, representative check and conflict status.

Identity uses the rule and stable subject fields: recording/asset/source/target
hashes, ingredient and MIDI hashes, source/target groups, and measured source
shape where applicable. Local selection IDs, cue labels, AAF mob names,
document hashes and declared answers do not create extra votes. Multiple
ranges on the same recording/property form one requirement: every assessed
range must pass. RIN/ERN format-conformance and workflow-contributor checks
are collection-level requirements. Unbound MIDI/C2PA properties are likewise
collection-level; unknown subjects otherwise stay separate rather than being
guessed equal.

For each fact: any false result => false; otherwise at least one true => true;
otherwise unavailable. Unavailable observations do not vote or promote an
assessed fact to an authenticated tier. A mixture of true and false results
sets `conflicting_outcomes: true`, and remains one failed fact. Explanations
name one representative failure per fact rather than repeating copied errors.

`integrity_layers.cross_evidence_support.check_count` / `scored_fact_count`
counts scored facts; `raw_check_count` and `evidence_tier_counts` preserve raw
observation counts. Document hashes are traceability, not independent-witness
counts. `cross_evidence_audit` remains diagnostic; it is not the scored input.

This aggregation removes extra votes for the same fact. It does not prevent
all manipulation by adding unrelated, distinct facts, or establish that
separate documents have independent authors. File-binding and parser checks
remain in their existing file-integrity layer.

## S03: release, status and severe caps

The default profile is now `default-v4`, version 4. Default release requires
at least two populated contributing layers and an assessed fact involving
`signed_attestation` or `recorder_metadata`. Qualifying means assessed, not
necessarily passed: a metadata contradiction can still yield a reduced
consistency value when the evidence needed to assess consistency exists.

An ordinary contradiction is not a release exception: an invalid declaration
cannot turn an unsupported baseline from null into a numeric value.

A severe contradiction is the one exception. Support needs independent evidence
because agreement can be fabricated; a severe failure such as a changed file hash
is computed by the evaluator itself and needs none. With
`release_capped_value_on_severe_contradiction: true` (default-v4), a severe rule
releases `min(weighted mean, 0.25)` even without qualifying evidence, with
`release_reason` `severe_contradiction_cap`. The cap still never raises a lower
mean. A tampered submission therefore shows at most 0.25, while one that only
lacks evidence stays null. Profiles without the flag (such as default-v3) keep
withholding the value, so earlier results reproduce.

Contradictions and their findings remain visible even when no value is released.
`axes.integrity.status` repeats the decision status, so a reader of the axes alone
can tell a contradicted submission from one that lacks evidence.

`assessment_checks.integrity_decision[0]` records:

- `status`: consistent, contradicted or insufficient_evidence;
- `contradiction_present`;
- `numeric_release`: released or withheld;
- `release_reason`: qualifying_cross_evidence, configured_layer_coverage,
  severe_contradiction_cap, insufficient_assessed_layers,
  insufficient_qualifying_cross_evidence or no_assessed_layers;
- `severe_contradiction_present`;
- `ordinary_contradiction_release_allowed: false`;
- `severe_contradiction_release`: capped_value or disabled;
- explicit consistency / non-authentication score semantics.

The canonical profile flag is `require_substantive_support`. The old
`require_substantive_support_or_contradiction` spelling remains a readable
alias but does not restore its old bypass; providing both is rejected.
A custom profile can explicitly set the support requirement false, but still
must meet its configured layer count. Null remains unavailable as a numeric
score; consumers must not turn it into zero for ranking.

## S05: unchanged eligibility, non-scoring limitation

`RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED` is emitted once per bound WAV
with parsed BWF/iXML metadata. It has severity info, `scoring_eligible: false`,
`integrity_contribution: null`, and `recorder_origin_authenticated: null`.
Unknown origin is not reported as an invalid signature or a failed check.

A synthetic but internally consistent BWF/report pair can still release
Integrity 1.0. This means checked fields agree; it does not authenticate the
recorder, recording event, author or historical origin. A wrong timecode still
lowers consistency through its actual failed fact. The informational finding
itself neither increases nor lowers the value and cannot release it.

## S06: compatible coverage semantics

The shared response keeps `axes.ai_disclosure`. Its display name is
**Creation-method disclosure coverage**. `ai_disclosure_coverage` records
applicable, assessed, declared and excluded counts, plus:

- `content_ai_detection_performed: false`;
- `ai_generated_probability: null`;
- `declaration_truth_verified: false`.

`AI_DISCLOSURE_IS_DECLARATION_COVERAGE` explicitly reports this limit without
an Integrity contribution. Fully declared recorded material and fully declared
AI-generated material both have coverage 1.0. This is not the share of AI audio
or the probability of AI use. Missing declarations retain the old unavailable
numeric behaviour and the existing missing-disclosure finding; structured
counts show how many declarations are missing. Input-error exclusions are
preserved. No content AI detector was added.

## Unchanged policy and validation

Layer weights, all confidence formulas, the severe ceiling value, optional
audio non-scoring isolation, and authentication/attestation implementation are
unchanged. Versioned profile semantics and documentation reflect the authorised
release and aggregation changes. Previous example responses and verification
reports remain historical snapshots rather than current expected outputs.

Thirteen new regression tests cover bidirectional copy invariance at 1/3/10/30
copies, renamed local labels, distinct recordings, unavailable-tier handling,
real BWF/report parsing, non-scoring origin findings, ordinary and severe
release gating, a real file-hash failure with other qualifying evidence still
capped at 0.25, legacy flag compatibility, and declaration coverage semantics.
The previous weighted-mean/cap arithmetic fixtures now explicitly qualify their
cross-evidence in the test profile, while new tests enforce the stricter
shipped default gate. Existing profile-version/text assertions are updated.

The 55 previous scoring fixtures are replayed against the new policy with
unchanged execution status, confidence numbers and non-Integrity axis values.
In the recorded dilution cases, 1/3/10/30 agreeing copies all produce 0.875.
The unsupported wrong-sample-rate case changes from 0.9 to null while retaining
contradicted status. The consistent metadata case remains 1.0 with the
non-scoring origin finding. Two full optional-audio replays produce ten
completed observations and preserve all core axes. No Lumina generation,
credit use or subscription changes are involved.

The final full-suite result and replay artifacts are recorded in the delivery
verification folder. This is policy/engineering validation, not statistical
confidence calibration or authentication of a real production event.
