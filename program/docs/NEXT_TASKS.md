# Future Work

Updated: 2026-10-03

This file is the current consolidated roadmap for `v3/program`. It replaces the
shorter v0.4 task list and incorporates unresolved work from the legacy v3
prototype. The implemented baseline already includes workflow completeness,
MIDI/RIN/ERN/C2PA/CompSheet/CueSheet parsing, CrossEvidence, and scoped Edit,
Comp, Stem, Mix, and Master content checks. Version 0.5 also exposes bounded
automatic alignment, leave-one-out source contribution, per-selection CompSheet
verification, processed-audio matching, channel-matrix residual, excerpt search,
mastering continuity, and decoy-source diagnostics. These additions still need
calibration against licensed real production material.

## Scope principles

- Keep completeness, integrity, attestation strength, and AI disclosure as
  separate dimensions.
- A content match means that submitted files are mathematically consistent; it
  does not prove historical use, creator identity, ownership, or authorship.
- Do not add an accepted/rejected decision policy unless the client explicitly
  requests and defines one.
- Keep the non-technical UI and proprietary DAW parsing behind evidence
  validity, calibration, and submission tooling.

## P0: Evidence validity and false-positive control

1. Obtain licensed, provenance-explained real production datasets covering the
   full chain: raw takes, edited takes, CompSheets, stems, mixes, pre-masters,
   masters, and documented processing choices. Include honest-positive,
   incomplete, contradictory, and adversarial submissions.

2. Build a false-positive regression corpus for content derivation. It must
   include repeated loops, identical copied regions, same-tempo/same-key but
   unrelated music, duplicate or highly correlated sources, source-separated
   stems reconstructed from a final mix, short partial matches, shuffled stems,
   phase inversion, silence, clipping, and AI-generated process evidence.

3. Calibrate the hardened Edit, Comp, Stem, and Mix checks with real labelled
   audio. Checks now require submitter-declared ranges and gains and never fit
   missing transformation parameters. Partitioned validation, coverage
   reporting, correlated-source detection, source-identifiability checks,
   repeated-segment ambiguity, and provenance disclaimers are implemented;
   their thresholds still require real-world false-positive/false-negative
   measurement.

4. Calibrate the hardened Master continuity assessment with real mastered
   material. Master assessment now uses declared source/target ranges; competing
   locations may expose ambiguity but are never substituted for the declared
   alignment. Coverage, manual-review reasons, and `corroborated` separation are implemented. Expand labelled tests
   for stronger EQ, compression, limiting, dither, fades, silence insertion,
   clipping, and sample-rate conversion.

5. Calibrate the separated integrity layers with labelled data and client input.
   File integrity, structural validity, declaration consistency, content
   reconstruction, CrossEvidence, and cryptographic attestation are now visible
   separately; structural validity and cryptographic attestation do not inflate
   integrity. Confirm the remaining score-eligible layer weights and confidence
   model without adding an overall decision policy.

6. Decide whether to restore the legacy prototype's `forge_cost` concept. If it
   is retained, use it only as a separately reported confidence/fabrication-
   effort estimate, never as evidence that a claim is true. Calibrate values
   empirically rather than assigning them only by artefact type.

7. Add clearly licensed, XSD-valid real RIN and ERN fixtures. Cover valid,
   invalid, unsupported-version, namespace variation, missing field, incorrect
   identifier, and audio-hash mismatch cases. Add version detection/routing so
   unsupported DDEX versions are reported precisely rather than silently
   treated as the currently supported RIN 2.1 or ERN 4.3.

8. Add real C2PA fixtures for valid signed media, tampered media, untrusted or
   expired/revoked credentials, missing manifests, malformed manifests, and
   ingredient relationship/hash mismatch. Confirm behaviour against the
   production C2PA SDK and the selected trust policy.

9. Confirm the DDEX Implementation Licence and schema redistribution rules
   before external deployment.

## P1: Submission, relationship provenance, and reporting

10. Build the formal submission CLI. It should select a workflow, collect
    declarations, add files and relationships, calculate SHA-256 hashes, copy
    objects into the bundle, validate inputs, and generate `submission.json`
    plus `objects/`. The CLI should explain which required evidence is missing
    before evaluation.

11. Add explicit manual-review output without introducing a decision policy.
    Report machine-readable review reasons for ambiguous alignment, insufficient
    coverage, parser limitations, unsupported processing, conflicting evidence,
    custom workflows, and unavailable cryptographic validation.

12. Complete positive, missing, `not_assessed`, `not_applicable`, contradiction,
    and parser-failure end-to-end cases for all 16 workflows and their modifiers.
    Confirm required/conditional/optional classifications and weights with the
    client.

13. Restore or replace the legacy machine-origin binding controls. A
    relationship claiming origin from C2PA, RIN, ERN, or a DAW export should be
    bound to the declaring artefact, a stable field/pointer, and the relevant
    endpoint hashes. An unbound machine-origin label must receive no confidence
    advantage over a submitter declaration.

14. Expand RIN/ERN extraction and CrossEvidence for contributors, roles,
    sessions, recording components, ISRCs, resource/release identifiers, and
    technical delivery data. Add a documented generation-record and AI-
    disclosure schema. Implement ERN AI-extension extraction only from an
    authoritative mapping and real examples.

15. Expand secure C2PA ingredient URI, identifier, and hash binding. Preserve
    the distinction between manifest presence, successful parsing, signature
    validity, content binding, certificate chain, revocation, trust, timestamp,
    and signer identity. Do not automatically create graph edges from unbound
    ingredients.

16. Decide and document how CSEC/public requests carry `workflow_id`, modifiers,
    non-WAV evidence, relationships, and declarations. Re-run the target in the
    other team's runner after the contract is agreed.

17. Produce a user-facing final report that shows the four dimensions,
    per-expectation workflow results, relationship evidence levels, content-test
    coverage, contradictions, unavailable checks, and manual-review reasons. Do
    not collapse these into an unexplained overall score.

18. Synchronise examples, README, handoff documentation, schemas, release
    version, source revision, verification report, and test counts after each
    release. The current release records must be regenerated after the new
    derivation suite.

## P2: Engineering quality and broader media support

19. Add CI for clean-environment installation, the full test suite, schema
    validation, dependency vulnerability checks, linting, and release artefact
    generation. Test supported Python versions and target platforms.

20. Add performance and resource tests for long audio, large bundles, many
    relationships, high channel counts, and worst-case alignment inputs. Review
    the current 60-second analysis cap, 16-source limit, and file-size limits
    using measured runtime and memory data.

21. Expand malicious-input tests for WAV chunk abuse, XML bombs, hostile PDFs,
    malformed MIDI, symlink/path attacks, oversized JSON, hash collisions in
    mappings, parser timeouts, and denial-of-service patterns.

22. Prioritise FLAC, AIFF, BWF, RF64, floating-point WAV, and other formats from
    actual client submissions. Define conversion and resampling rules without
    hiding transformations from the report.

23. Expand CompSheet and CueSheet aliases, frame/timecode formats, validation,
    and crossfade fields. Add scanned-document OCR only if client data shows it
    is required.

24. Evaluate real AI-generated and hybrid human/AI media, including correctly
    attributed production outputs and convincing fabricated process evidence.
    Measure false-positive and false-negative behaviour; do not relabel AI-
    disclosure coverage as AI detection.

## P3: Deferred product features

25. Build the non-technical submission UI and final report presentation after
    the CLI and contracts are stable.

26. Add DAW evidence through official exports and interchange formats first.
    Parse proprietary DAW project formats only when client workflows require it
    and maintainable specifications or APIs are available.

27. Consider a named and versioned decision policy only if the client later
    requests accepted/rejected/needs-review outcomes and supplies thresholds,
    costs, and representative evaluation ground truth. Keep it outside the four
    assessment dimensions.

## External inputs and decisions still required

- Client datasets containing real RIN, ERN, C2PA, raw/edited/stem/mix/master,
  CompSheet, and relevant AI-assisted workflows.
- Client confirmation of workflow evidence requirements, conditional rules,
  weights, confidence ceilings, and legal/rights-document scope.
- CSEC technical contact, collaboration mode, request contract, and runner
  integration plan.
- The upstream `libevchain` revision that should replace or validate the pinned
  local snapshot.
- A trust policy for C2PA credentials and a decision on whether forge cost is
  useful as a separately labelled confidence concept.
