# Dynamic Scoring Methodology

Status: **M1 implemented**. M2 (explainability surfacing) and the
contradiction-severity milestone are future work, described here for
context but **not** built yet. This document covers the `integrity` axis
only — the other three axes (`completeness`, `attestation_strength`,
`ai_disclosure`) are untouched by M1 and are described only as background.

---

## 1. Purpose and motivation

### Why configurable scoring is useful

Before M1, every number that shapes the `integrity` axis — how much each
evidence layer counts, how much evidence is required before a value is
reported at all, how confidence scales with coverage — was a Python
literal buried inside `assessment_passes.py`. Changing any of it meant
editing source and re-reading the surrounding logic to be sure nothing
else broke. That makes two ordinary, legitimate questions hard to answer:
*"does the current weighting match what our client expects?"* and
*"what would change if we weighted this differently?"* M1 answers both by
moving those numbers into a single external JSON file, validated on load,
with a documented default that reproduces today's behaviour exactly.

### Why equal weighting may not reflect evidence strength

Today, `integrity` is an **unweighted** average of however many of its
four contributing layers produced a value. A layer built from file-hash
binding and a layer built from independently reconstructing audio content
from declared parameters count exactly the same, even though they require
very different effort to satisfy honestly and very different effort to
fabricate. Equal weighting is a defensible *starting point* — it is
simple and avoids presuming an ordering nobody has justified — but it is
not obviously the *right* point, and until now there was no way to express
a different one without changing code.

### Why the system does not detect AI or prove human authorship

This remains unchanged by M1 and is a deliberate project boundary, not a
limitation to be engineered around. `ai_disclosure` measures whether a
submitter *declared* a creation method for each audio artefact — it is
disclosure-coverage bookkeeping, never a judgement about whether audio
content is AI-generated. Likewise, a `matched` result from a content-
reconstruction check means the submitted files are mathematically
consistent with a declared transformation; it does not establish who
performed that transformation, when, or whether a human was involved.
The project's own README states this directly: the system does not
produce an overall score, an accept/reject decision, or a human-versus-AI
classification. Dynamic scoring, as designed here, operates entirely
within that boundary — it changes how existing evidence layers are
combined, not what any individual check is allowed to claim.

---

## 2. Current scoring architecture

### Four independent axes

`completeness`, `integrity`, `attestation_strength`, `ai_disclosure` are
computed independently and never combined into one score. Each is shaped
`{availability, value, confidence, reasoning}`, where `availability` is
one of `available` / `partial` / `unavailable`, and `value`/`confidence`
are `null` whenever `availability` is `unavailable`.

### Which layers contribute to Integrity

`assess_axes()` (`music_target/assessment_passes.py`) builds six named
layers. Four are **score-eligible** (`contributes_to_integrity=True`); two
are reported for transparency but never move the integrity value:

| Layer | Contributes | What it checks |
|---|---|---|
| `file_integrity` | yes | SHA-256 binding and parser readability of submitted files |
| `declaration_consistency` | yes | Declared technical/relationship fields vs. measured file properties |
| `content_reconstruction` | yes | Declared edit/comp/stem/mix/master derivations, reconstructed and checked |
| `cross_evidence_support` | yes | Independent RIN/ERN/MIDI/C2PA/CompSheet/CueSheet claims vs. the graph |
| `structural_validity` | no | Graph-safety checks that already passed before scoring began |
| `cryptographic_attestation` | no | Mirrors the separate `attestation_strength` axis; must never inflate integrity |

Each contributing layer resolves to a `(status, value)` pair. For the
boolean-check layers this comes from `_boolean_status()`: the layer's
`value` is the *fraction* of its checks that passed, while its `status`
snaps to `"contradicted"` the moment that fraction drops below `1.0` —
i.e. status is binary even though the underlying value is a continuum.
For `content_reconstruction`, status comes from classifying each declared
derivation as `matched`/`contradicted`/`not_applicable`/`unavailable`, and
`value = matched / (matched + contradicted)` among the checks that were
actually assessed.

### Availability and confidence behaviour (unchanged by M1)

- A layer with no applicable checks reports `value: None` and is excluded
  from both the numerator and denominator of the integrity aggregate —
  missing evidence is never treated as zero.
- `integrity.availability` is `unavailable` if zero layers produced a
  value, `available` if all four did, otherwise `partial`.
- `integrity.confidence` scales only with *how many* layers produced a
  value, never with *how many checks* were inside each layer or where the
  evidence came from.

### The existing withholding rule

Even when some layers do produce values, `integrity.value` can still be
withheld (`null`). The rule, verified against the live code both before
and after M1: a numeric value is reported only if **both** (a) at least a
configured minimum number of layers produced a value, **and** (b) either
`content_reconstruction` or `cross_evidence_support` is among them — or,
regardless of (a)/(b), if any contributing layer is `contradicted`. This
exists specifically so that `file_integrity` and `declaration_consistency`
alone — both comparatively easy to satisfy — can never manufacture a
perfect-looking score with no corroborating or reconstructive evidence
behind it.

**M1 does not relax this rule.** It makes the rule's two thresholds
(`min_components_for_substantive_mean`, `require_substantive_support_or_
contradiction`) profile-configurable, with the shipped default set to
exactly today's values (`2` and `true`). Changing the default remains a
deliberate decision for the team, not something this change makes on its
own.

---

## 3. Proposed dynamic scoring methodology (implemented in M1)

### Weighted Integrity formula

Where the live code previously computed:

```
integrity_value = sum(layer.value for layer in scored_layers) / len(scored_layers)
```

it now computes a weighted mean over the same `scored_layers` (layers that
contribute to integrity and produced a non-null value):

```
integrity_value = Σ (layer.value × weight[layer.name])
                   ──────────────────────────────────
                   Σ weight[layer.name]
```

where `weight[layer.name]` comes from `integrity.layer_weights` in the
active scoring profile. **At the default profile, every weight is `1.0`,
so this formula reduces exactly to the old unweighted mean** — this was
verified directly, not merely argued: the project's 65-test suite and all
four documented example requests produce byte-identical output before and
after the change, and a dedicated check confirmed the weighted-mean
formula numerically equals the plain mean when all weights are equal.

### Assessed vs. unavailable layers

Nothing about *which* layers are eligible, or how a layer becomes
`unavailable`, changed in M1. Only `scored_layers` — the layers that are
both score-eligible and already produced a value under the existing rules
— ever enter the weighted sum; an `unavailable` layer contributes to
neither the numerator nor the denominator, exactly as before.

### How weights affect aggregation

Because the denominator is the *sum of weights of the layers actually
present*, not a fixed `4`, raising one layer's weight increases that
layer's influence on the mean only when it is populated — it has **no
effect at all** on a submission where that layer is `unavailable`. This
is a direct, intended consequence of weighting the mean rather than
weighting a fixed slot: a weight cannot inflate an axis that produced no
evidence to weight. The confidence formula is intentionally **not**
changed to depend on weights — confidence still reflects only how many
layers were populated, so a weighting change never retroactively
manufactures confidence that the evidence coverage doesn't support.

### Worked numerical example

Take four fully-populated layers with these fractional values (a
constructed illustration, not a real submission):

| Layer | value |
|---|---|
| `file_integrity` | 1.0 |
| `declaration_consistency` | 0.9 |
| `content_reconstruction` | 0.8 |
| `cross_evidence_support` | 0.6 |

**At the default profile** (all weights `1.0`):
```
(1.0 + 0.9 + 0.8 + 0.6) / 4 = 0.825
```

**At the illustrative weights from §4 below** (`file_integrity=1.00`,
`declaration_consistency=1.00`, `content_reconstruction=1.25`,
`cross_evidence_support=1.25`):
```
(1.0×1.00 + 0.9×1.00 + 0.8×1.25 + 0.6×1.25) / (1.00+1.00+1.25+1.25)
= (1.00 + 0.90 + 1.00 + 0.75) / 4.50
= 3.65 / 4.50
= 0.8111...
```

Both figures were computed by the actual implementation (not by hand
alone) and the hand-calculated values matched the code's output exactly.
A second, end-to-end check ran the real `run.evaluate → engine.
evaluate_chain → assess_axes` pipeline on a synthetic submission with a
deliberate declared-vs-measured sample-rate mismatch (one `declaration_
consistency` check failing among several) plus a correctly-declared edit
derivation: the default profile reproduced the pre-M1 unweighted result
(`0.9167`) exactly, and substituting the illustrative weights shifted it
to `0.9231` — matching the hand-computed weighted mean for that case
(`(1.0 + 0.75 + 1.0×1.25) / 3.25`) exactly.

---

## 4. Initial weighting rationale

These are **proposed illustrative weights**, used above only to
demonstrate that the mechanism works and to make §5's reasoning concrete.
**They are not calibrated, and the shipped default profile does not use
them** — the default sets every weight to `1.0`, preserving current
behaviour exactly.

| Layer | Illustrative weight |
|---|---|
| `file_integrity` | 1.00 |
| `declaration_consistency` | 1.00 |
| `content_reconstruction` | 1.25 |
| `cross_evidence_support` | 1.25 |

### Reasoning

**Reliability of the check.** `file_integrity` and `declaration_
consistency` compare a claim against a directly measurable property of
the submitted file (does the hash match; does the declared sample rate
match the file). These are reliable in the sense that the comparison
itself is exact, but satisfying them honestly is comparatively easy — a
submitter who controls the file controls both sides of the comparison
until the moment of hashing. `content_reconstruction` and `cross_
evidence_support` compare the submission against something the submitter
does not fully control after the fact: a derivation that must actually
reconstruct the target audio under the declared parameters, or an
independent document (RIN/ERN/CompSheet/C2PA) whose claims must agree
with the graph. The illustrative upweight reflects that these checks, when
they run at all, draw on a wider body of evidence than a single file
property.

**Independence and double-counting.** The four layers are designed not to
re-score the same underlying fact twice in a way that would justify
*discounting* any of them, so none of the illustrative weights are reduced
to correct for overlap — see the plan's analysis: a file's technical
mismatch appearing in both `file_integrity` (can the file be parsed) and
`declaration_consistency` (does it match the claim) are different
questions about the same file, not duplicated evidence. The one place
where double-counting *could* occur — a C2PA claim appearing in both
`cross_evidence_support` and the separate `attestation_strength` axis — is
already prevented structurally: `cryptographic_attestation` is a
non-contributing layer specifically so a C2PA signal can never move
`integrity_value` through two routes at once.

**Estimated forge cost.** See §5 — forge cost is part of why `content_
reconstruction` and `cross_evidence_support` are candidates for a higher
weight, but it is a documented *consideration*, not a computed input, in
M1.

**Scope and limitations of each check.** Every weight here operates
*after* a layer has already decided its own value using the existing,
unchanged pass logic. Weighting changes how much a layer's existing,
scoped result counts — it does not change what that result is allowed to
mean. In particular:

- **A matching SHA-256 does not prove human authorship.** `file_integrity`
  establishes that the submitted bytes are the bytes being assessed and
  that they parse as claimed — nothing about who produced those bytes or
  how.
- **Audio similarity does not prove derivation.** `content_reconstruction`
  only asserts that, under the submitter's own declared parameters, the
  target audio is mathematically consistent with the declared source —
  never that the declared production history is the one that actually
  occurred. Weighting this layer higher raises how much a *consistent
  reconstruction* counts; it does not strengthen what that reconstruction
  is permitted to claim.

---

## 5. Forge-cost considerations

### Definition, in this context

"Forge cost" is used here informally, to mean: *how much effort would it
take to produce evidence that passes a given check, for a submission that
did not actually follow the claimed creative process?* A check with high
forge cost requires an adversary to do real, hard-to-fake work (e.g.
constructing audio that genuinely satisfies a declared linear-derivation
model under independent holdout validation); a check with low forge cost
can be satisfied by asserting a claim that nothing else in the system
cross-checks.

### Why forge cost may inform weight selection

If two checks are otherwise comparable but one is measurably easier to
satisfy without the underlying process having happened, weighting the
harder-to-fake check more heavily is a defensible way to make the
aggregate value track genuine evidence strength rather than treating all
evidence as equally hard-won. This is the reasoning behind the
illustrative upweight on `content_reconstruction` and `cross_evidence_
support` in §4.

### Why forge cost is not a direct authenticity probability

Forge cost describes a *property of the check*, not a *property of the
specific submission being scored*. A check being hard to fake in general
does not mean a given passing instance of it is genuine, and a check
being easy to fake does not mean a given passing instance is forged. Forge
cost should never be read as "this evidence is N% likely to be real" —
it only justifies *how much a check should count when it is present*,
never *whether a particular result should be believed*. Conflating the
two was the specific failure mode the project's own prior design
iteration (an archived prototype, not part of the live system) risked by
combining forge cost directly into a per-unit confidence calculation;
this project treats that as a cautionary precedent, not a model to copy
as-is.

### Forge-cost weighting is not implemented in M1

No part of `scoring_profile.py` or `scoring_profile.json` computes,
stores, or applies a forge-cost multiplier. The default profile's
`integrity.layer_weight_rationale` section records forge-cost-style
*reasoning* for each layer's weight as a human-readable string — purely
documentation, never read by any scoring code — so the consideration is
visible to reviewers without being implemented as a mechanism. Should the
team later want forge cost as an actual computed input, that is a
distinct, separately-scoped design question (see §8).

---

## 6. Implementation details

### Scoring profile JSON — `program/policies/scoring_profile.json`

```json
{
  "profile_id": "default-v1",
  "profile_version": 1,
  "description": "Reproduces pre-profile integrity scoring exactly. ...",
  "integrity": {
    "layer_weights": {
      "file_integrity": 1.0,
      "declaration_consistency": 1.0,
      "content_reconstruction": 1.0,
      "cross_evidence_support": 1.0
    },
    "layer_weight_rationale": { "...": "documentation-only strings per layer" },
    "withholding_rule": {
      "min_components_for_substantive_mean": 2,
      "require_substantive_support_or_contradiction": true
    },
    "confidence_formula": {
      "ceiling": 0.8,
      "denominator_layers": 4
    }
  }
}
```

Every numeric value above is the exact pre-M1 hardcoded literal. The
profile intentionally covers **only** what M1 consumes — no sections for
derivation thresholds, the C2PA ladder, AI-disclosure confidence, or
contradiction caps are present, because nothing yet reads them. Adding an
unused configuration section ahead of the code that consumes it would be
dead weight someone later has to decide is safe to delete.

### Loader and validation — `program/music_target/scoring_profile.py`

`load_scoring_profile(path)` parses the file with the same duplicate-key
rejection `workflow_policy.py` already uses (`_no_duplicates`, imported
directly to avoid drift between the two loaders), then validates:

- `profile_id` is a non-empty string; `profile_version` is an integer.
- `integrity.layer_weights` contains exactly the four known layer names,
  each mapped to a positive number (no unknown or missing layers).
- `integrity.layer_weight_rationale`, if present, maps known layer names
  to non-empty strings.
- `integrity.withholding_rule.min_components_for_substantive_mean` is a
  positive integer; `require_substantive_support_or_contradiction` is a
  boolean.
- `integrity.confidence_formula.ceiling` is a number in `[0, 1]`;
  `denominator_layers` is a positive integer.

Any failure raises `ScoringProfileError` — there is no silent fallback to
defaults for a profile that exists but is malformed, since that would
mask an intended configuration change. `load_default_scoring_profile()`
loads the shipped file at `policies/scoring_profile.json`.

### Integration into the scoring pipeline

`assess_axes()` in `assessment_passes.py` gained one optional trailing
parameter, `scoring_profile=None`, defaulting to `load_default_scoring_
profile()` when not supplied. Its only caller, `engine.py`, is unchanged —
it continues to call `assess_axes()` with its existing arguments, which
means it transparently receives the default profile. The aggregation step
was rewritten to:

1. Build `scored_layers` exactly as before (score-eligible layers with a
   non-null value).
2. Read `layer_weights`, `withholding_rule`, and `confidence_formula` from
   the active profile's `integrity` section.
3. Evaluate the withholding condition using the profile's thresholds
   instead of the literals `2` and the unconditional substantive-support
   requirement.
4. If eligible, compute the weighted mean over `scored_layers` using each
   layer's configured weight, looked up by `item["layer"]` (the name
   `_layer()` already attaches to every layer dict).
5. Compute confidence using the profile's `ceiling`/`denominator_layers`
   in place of the literals `0.8`/`4`.

No other function was touched. `_boolean_status()`, `_layer()`, the five
derivation passes, `C2PAAttestationPass`, and `AIDisclosurePass` are
unmodified — M1 only changes how already-computed layer values are
combined into `integrity.value`/`integrity.confidence`.

### Default compatibility

Verified, not assumed:

- All 65 existing unit tests pass unmodified, before and after the change.
- All four documented example requests (`valid-generated`, `invalid-hash`,
  `unsupported-png`, `csec-wav`) produce byte-identical JSON output before
  and after the change (ignoring timing fields and the source-code hash,
  which necessarily changes when new source files are added).
- A direct numerical check confirmed the weighted-mean formula reduces to
  the plain mean exactly when all weights equal `1.0`.
- An end-to-end run through the real evaluation pipeline, with a
  non-default profile substituted, confirmed `assess_axes()` actually
  consumes the supplied profile rather than it being an unused helper.
- Seven deliberately malformed profiles (duplicate key, missing
  `profile_id`, negative weight, missing layer, out-of-range confidence
  ceiling, non-boolean withholding flag, invalid JSON) were each confirmed
  to raise `ScoringProfileError`.

---

## 7. Explainability (planned M2 — not implemented)

M1 changes how `integrity.value` is computed; it does **not** change what
the response body reports about *why*. No response field currently states
which profile was active or what weight each layer carried — a response
scored under the default profile and one scored under a hypothetical
alternative profile are indistinguishable from the output alone, other
than the resulting `value` itself.

The planned M2 addition, not built in this milestone: surface the active
profile's identity and the weight actually applied to each layer inside
the existing `assessment_checks` object (which already reports an
`integrity_layers` array per response) — for example, an added
`assessment_checks["scoring_profile"]` entry naming `profile_id`/
`profile_version`, and a `"weight"` field added to each existing `
integrity_layers` entry. Both would be purely additive to `assessment_
checks`; neither requires any change to the public response schema, since
`assessment_checks` is already an open, schema-permitted extension point.
This is scoped as M2 specifically so that M1's behavioural change and
M2's visibility change can be reviewed and landed separately.

---

## 8. Limitations and future work

- **Weights are not empirically calibrated.** The §4 illustrative weights
  were chosen to make the mechanism's reasoning concrete, not derived from
  any labelled corpus. No calibration work has been done, and none is
  proposed as part of M1.
- **Evidence dependence/correlation is not formally modelled.** §4 argues
  informally that the four layers ask sufficiently different questions to
  avoid double-counting, but this has not been tested against adversarial
  submissions designed to satisfy multiple layers from a single underlying
  fabrication.
- **Integrity can still be `unavailable`/`null` under the existing rules**,
  and M1 does not change that. On the project's own flagship example and
  on real CSEC submissions examined during development, `content_
  reconstruction` and `cross_evidence_support` are both `unavailable`,
  which caps the populated layers at two and (at the default profile)
  withholds a numeric value regardless of how those two layers are
  weighted. **Dynamic weighting does not resolve this by itself** — a
  weighted mean of two layers at any weights is not reachable until a
  submission supplies enough evidence to populate a third or fourth layer,
  or until the withholding-rule thresholds themselves are changed from
  their default (a decision this milestone deliberately leaves to the
  team).
- **Contradiction severity and score caps are deferred.** Nothing in M1
  distinguishes a trivial contradiction from a severe one; every
  contradicted layer is treated exactly as it was before. This is an
  explicitly separate, later milestone.
- **Workflow-specific weighting is future work.** All weights in this
  design are global; nothing lets a workflow (e.g. `mastering_service_
  only` vs. `take_comping_and_editing`) carry its own weight profile.
- **Broader calibration against real production and adversarial material**
  — comparing equal vs. reliability-based weighting, testing severity-cap
  designs, measuring sensitivity across workflows — remains entirely
  future work requiring datasets not yet collected.

---

## 9. References

**Verified from this repository:**

- Coalition for Content Provenance and Authenticity (C2PA) — referenced as
  the standard `legacy_parsers/c2pa_parser.py` implements against
  (docstring: *"Baseline reader for C2PA (Coalition for Content Provenance
  and Authenticity)"*). This project treats a C2PA manifest's presence,
  signature, and validation state as evidence inputs to the separate
  `attestation_strength` axis, not as an integrity input.
- DDEX Recording Information Notification (RIN) 2.1 and Electronic Release
  Notification (ERN) 4.3 — pinned XSD schemas with recorded source URLs
  and checksums at `program/schemas/ddex/SOURCES.json`, used by `cross_
  evidence_support` checks.
- The project's own archived prior design (`legacy_v3_prototype/
  music_profile.json`, `legacy_v3_prototype/music_profiles.py`) — not part
  of the live system, but reviewed this session as a documented precedent
  for externalising weights (`artefact_forge_cost`, `relationship_origin.
  base_confidence`) and for a forge-cost-weighted integrity mean with a
  flat contradiction ceiling. §5 above explicitly treats this prior
  design's conflation of forge cost with per-unit confidence as a
  cautionary precedent rather than a pattern to repeat.

**General field principle, not a specific citation:** weighting evidence
by its reliability and by how independent it is from other evidence
already counted is a standard idea in multi-source evidence aggregation
and intelligence-analysis practice generally. No specific paper, author,
or formal citation for this general principle is asserted here, and none
should be inferred — it is stated as general background, not as a
reference this design was built from.

**Explicitly not cited:** no academic paper, standard, or technical
reference for a *forge-cost-weighted evidence aggregation formula*
specifically was found in this repository or otherwise available to
verify during this work. If the team wants a citation-backed treatment of
forge-cost-style weighting for a future milestone, that literature search
has not yet been done and should not be assumed to exist from this
document.
