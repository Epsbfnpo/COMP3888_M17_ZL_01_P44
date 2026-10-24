# Future Work

Updated: 2026-10-24 (after merging scoring profile default-v6 with FLAC/MP3 support)

This file is the current consolidated roadmap for `v3/program`. It replaces the
shorter v0.4 task list and incorporates unresolved work from the legacy v3
prototype. The implemented baseline already includes workflow completeness,
MIDI/RIN/ERN/C2PA/CompSheet/CueSheet parsing and CrossEvidence. Since v0.7.0 (and still in v0.8.1) the
legacy `PhysicalSimilarityPass` is policy-disabled and is not imported or
invoked by assessment. Relationships receive non-scoring compatibility
placeholders only. The current priority is the claim ledger, deterministic
cross-evidence checks, hash-binding coverage, and evidence-chain reporting.

## Scope principles

- Keep completeness, integrity, attestation strength, and AI disclosure as
  separate dimensions.
- A content match means that submitted files are mathematically consistent; it
  does not prove historical use, creator identity, ownership, or authorship.
- Do not add an accepted/rejected decision policy unless the client explicitly
  requests and defines one.
- Keep the non-technical UI and proprietary DAW parsing behind evidence
  validity, calibration, and submission tooling.

## Completed P0: disable unreliable signal scoring

- `PhysicalSimilarityPass` is not executed by assessment.
- Physical diagnostics have `scoring_eligible: false` and never contribute to
  integrity.
- No signal-based contradiction, inconclusive, unavailable, or manual-review
  finding is emitted.
- Compatibility placeholders use `reason_code: disabled_by_policy` and retain
  `content_match_is_provenance: false`.
- Tests assert that the deprecated pass is never invoked.

## P3: Deferred optional audio research

The following historical calibration tasks do not block the evidence-chain
work. Revisit them only if the client later requests optional audio
fingerprinting or other non-scoring diagnostics.

1. Obtain licensed, provenance-explained real production datasets covering the
   full chain: raw takes, edited takes, CompSheets, stems, mixes, pre-masters,
   masters, and documented processing choices. Include honest-positive,
   incomplete, contradictory, and adversarial submissions.

2. Build a false-positive regression corpus for physical similarity. It must
   include repeated loops, identical copied regions, same-tempo/same-key but
   unrelated music, duplicate or highly correlated sources, source-separated
   stems reconstructed from a final mix, short partial matches, shuffled stems,
   phase inversion, silence, clipping, and AI-generated process evidence.

3. Calibrate each comparison mode with real labelled audio: pairwise continuity,
   excerpt search, CompSheet selection timeline, multi-source contribution, and
   master continuity. Measure the two endpoint thresholds separately and verify
   that uncertain transformations reliably remain `inconclusive`.

4. Calibrate master continuity with real mastered material. Expand labelled
   tests for stronger EQ, compression, limiting, dither, fades, silence
   insertion, clipping, and sample-rate conversion. Confirm that ordinary
   mastering remains corroborated or inconclusive, while only clearly unrelated
   content reaches contradicted.

5. Calibrate the separated integrity layers with labelled data and client input.
   File integrity, structural validity, declaration consistency, physical
   similarity, CrossEvidence, and cryptographic attestation are now visible
   separately; structural validity and cryptographic attestation do not inflate
   integrity. Confirm the remaining layer weights, and consider a confidence
   measure that also reflects check results and the amount of evidence, without
   adding an overall decision policy. Since default-v6 the Integrity confidence
   is described as "layers checked"; the Completeness confidence still counts
   rules rather than rule weights, the Attestation confidence uses fixed
   levels (0.9, 0.6, 0.3) and the Disclosure confidence is a constant 0.8.

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
   Partly done: `tests/test_c2pa_integrity.py` signs WAV fixtures at test time
   with a throwaway certificate and covers valid, edited-after-signing,
   corrupted-certificate and invalid supporting manifests. Real vendor-signed
   fixtures, expired/revoked credentials and a configured trust policy remain.

9. Confirm the DDEX Implementation Licence and schema redistribution rules
   before external deployment.

## P1: Submission, relationship provenance, and reporting

10. Build the formal submission CLI. It should select a workflow, collect
    declarations, add files and relationships, calculate SHA-256 hashes, copy
    objects into the bundle, validate inputs, and generate `submission.json`
    plus `objects/`. The CLI should explain which required evidence is missing
    before evaluation.

11. Add the claim ledger and its versioned integrity policy. Deduplicate each
    material claim, attach measured/embedded/documented/attested evidence with
    binding, independence and credential dimensions, and compute consistency
    separately from assessment coverage. Do not introduce a decision policy.

12. Complete `corroborated`, `contradicted`, `not_assessed`, invalid-input,
    missing-evidence, and parser-failure claim-ledger cases for all 16 workflows
    and their modifiers. Physical placeholders remain `unavailable` and
    non-scoring.
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
    per-expectation workflow results, the claim ledger, contradictions,
    unassessed claims, hash-binding coverage, missing evidence and recommended
    additions. Show the disabled physical diagnostic separately and do not
    collapse the report into an unexplained overall score.

18. Synchronise examples, README, handoff documentation, schemas, release
    version, source revision, verification report, and test counts after each
    release. Records and examples were last regenerated on 24 October 2026
    for the merge of default-v6 with FLAC/MP3 support (403 tests);
    `reports/public-results/` still needs the public CSEC bundles to be
    regenerated, and the final report and both slide decks still quote 332
    tests.

## P2: Engineering quality and broader media support

19. Add CI for clean-environment installation, the full test suite, schema
    validation, dependency vulnerability checks, linting, and release artefact
    generation. Test supported Python versions and target platforms.

20. Expand performance and resource tests for large bundles, many
    relationships, high channel counts, parsers and cross-document comparisons.
    Retain the deprecated signal-helper limits only for direct legacy tests or
    a future optional diagnostic provider. File-size limits are fixed at
    512 MiB per object and 1 GiB per request; hashing time is not yet counted in
    the work budget.

20a. Completed: isolated non-final objects (hash mismatch, size limit, invalid
    path, hash or path format errors) are not assessed in
    `BoundFileIntegrityPass` (`object_hash_bound` with `passed: null`), so an
    input error does not lower the file-integrity layer.

20b. Detect documents that share an origin. `evidence_sources` currently
    records only the uploader as a known common source and lists the
    creator/software fields already parsed (WAV, DAW). Using collected real
    documents, also read the sound-report recorder line, the AAF
    identification, the RIN/ERN message sender and the C2PA claim generator;
    design name-matching rules from that data; then group documents that share
    an origin under `known_common_sources`. Independence stays unknown unless
    it can be shown.

20c. Choose whether to enable `withholding_rule.min_supported_asset_share`
    and at what value, using calibration submissions (see `support_breadth` in
    results). It currently covers recordings only; extend it to mixes, stems,
    masters or individual relationships once real data shows what qualifying
    evidence those assets carry. Since 24 October 2026 AAF-used takes that
    were not submitted join the denominator (`not_submitted`, or
    `unmatched_reference` when a submitted recording has no recorder identity),
    and `material_coverage` and `support_coverage` are reported separately; on
    Project Rock Ep01 removing a take now gives 12 of 13. Still open: matching
    renamed takes through aliases instead of tape and file name only, and
    whether a workflow requires every AAF-used take (item 34).

20d. Completed (24 October 2026): the workflow policy is loaded once per
    evaluation in `engine.py` from `workflow_policy.DEFAULT_POLICY_PATH` and
    passed to scoring, so completeness and `support_breadth` read the same
    policy.

21. Expand malicious-input tests for WAV chunk abuse, XML bombs, hostile PDFs,
    malformed MIDI, symlink/path attacks, oversized JSON, hash collisions in
    mappings, parser timeouts, and denial-of-service patterns.

22. FLAC and MP3 are implemented (24 October 2026; see
    `FLAC_MP3_SUPPORT_PLAN.md`). Prioritise AIFF, RF64/BW64, compressed WAV
    codecs and other formats from actual client submissions. Integer/float RIFF/WAVE and matching
    EXTENSIBLE SubFormats now share a streaming decoder. Define future
    conversion and resampling rules without hiding transformations from the
    report.

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

## Open after the FLAC/MP3 and scoring merge (24 October 2026)

28. Scoring issues still open from `Scoring_System_v0.8.1`: passing checks
    still dilute a contradiction (one forged timecode among 96 facts gives
    0.994, released); the equal layer weights are not justified by data; the
    six severe rules share one 0.25 cap; and reported-but-unscored
    observations (unsubmitted AAF takes, chronology warnings) need a policy
    decision with the client.

29. Pin the CSEC version: Git tag, `source_revision`, the G1 and G3
    thresholds under default-v6, a fresh-environment install check, and the
    CSEC contact and testing window.

30. Calibrate the report-only `bandwidth` diagnostic on a larger labelled set
    (genuine lossless, lossy-derived, upsampled, AI-generated, mastered with a
    low-pass). AI output is band-limited too (25 of 25 Lumina benchmark WAVs),
    so a flag must stay a heuristic until false-positive rates are known.

31. Measure MP3 recall for excerpts and mixture components. The calibration
    status now lists the one tested configuration (libsndfile 1.2.2 with LAME
    3.100, VBR, about 140–163 kbps, false-match and shared-loop pairs only)
    under `encoded_configurations_tested`, and
    `mp3_encoded_queries_beyond_tested_configuration` under `not_tested`. Plan:
    licensed source recordings with known relationships, encoded as WAV, FLAC
    and MP3 at 64, 96, 128, 192 and 320 kbps plus a common VBR setting;
    positives (whole track, short excerpt, weak mix, known transposition and
    tempo change) and negatives (same key and tempo, same style, shared
    material); development and validation split by source family; report false
    matches, misses, abstentions, offset error, beat and transposition error,
    time, memory and decode failures. Keep current thresholds as the baseline
    and change them only for a systematic difference confirmed on held-out
    families.

32. Decide how to present offset-0 fingerprint matches spread over fixed
    times (five free-site MP3s share watermark-like bursts and match in 6 of
    10 unrelated pairs). The match is real shared audio; the report could name
    the pattern instead of leaving the reader to infer it.

33. Scoring for FLAC and MP3 follows the WAV rules, as the cross-format plan
    of 24 October recommends: shared analysis, format-specific reading, the
    FLAC MD5 as an internal check that never adds origin confidence, and
    missing material reported as insufficient evidence rather than a failure.
    A plain FLAC take is therefore withheld for lack of recorder evidence, and
    an MP3 mix now lists its edges as `not_comparable` (`format_has_no_bwf`)
    in `declared_chronology`. Confirm this treatment with the client.

34. Required-recording scope for `support_breadth` (client decision): which
    workflows require every take the AAF edit uses, which allow samples,
    which substitutes count, deadlines for late submissions, and whether
    `min_supported_asset_share` gates release. Library effects, music and
    renders stay classified separately (`aaf_non_recorder_media`).

35. Cross-file sample identity (deferred). Report, for declared edges between
    lossless files with the same rate, channels and bit depth, whether the
    decoded integer samples are identical, using a versioned digest over
    interleaved little-endian samples. Needs canonical rules for 8-bit WAV,
    WAVE_FORMAT_EXTENSIBLE padding and float, and stays report-only: identical
    samples describe an exact content relation, not provenance or order.

36. Chronology rules before any scoring (client decision): restrict the
    comparison to relationships with a real causal order (for example
    `derived_from` versions), separate recording, export, signing and receipt
    times, record time zone and tolerance, and require a verifiable time source.
    Until then `DECLARED_SOURCE_NEWER_THAN_TARGET` stays report-only.

37. FLAC sample count: libsndfile decodes exactly the STREAMINFO sample count,
    so a declared-versus-decoded comparison is not independent with this
    decoder (a count set too low fails the MD5; too high fails decoding, see
    `tests/test_audio_formats.py`). Revisit with a decoder that reports frames
    independently of STREAMINFO.

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
