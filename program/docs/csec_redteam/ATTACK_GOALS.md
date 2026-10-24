# Adversarial testing: attack goals and success criteria

Status: **draft for CSEC review, updated 24 October 2026.** The scoring
update (profile `default-v6`) has been merged into main together with FLAC
and MP3 support. Sections marked **[PENDING PIN]** still need the pinned
version and the thresholds the team agrees for G1 and G3. Goals G4, G5 and G7
do not depend on scoring and can be tested now.

## 1. What the evaluator claims, and what it does not

The evaluator reads a submitter-declared music-production evidence graph and
reports four separate axes: `completeness`, `integrity`,
`attestation_strength` and `ai_disclosure` (shown as "Creation-method
disclosure coverage"). Read these boundaries before choosing attacks, because
they define what counts as a failure:

- **No decision.** There is no overall score, no accept/reject outcome and no
  human-versus-AI classification. "The program did not reject my forgery" is
  not a finding by itself; a finding is a reported value or status that
  misrepresents the evidence (see the goals below).
- **`null` is not zero.** A `null` axis means "not assessed". Making an axis
  `null` is not an attack unless the reasoning or findings hide why.
- **Integrity measures consistency, not authenticity.** It checks whether the
  submitted files, declarations and documents agree with each other and with
  what can be measured in the files. Hash binding proves which bytes were
  submitted, not who made them or when.
- **`ai_disclosure` measures declaration coverage.** A complete "recorded"
  declaration and a complete "ai-generated" declaration both score 1.0. No
  content-based AI detection is performed.
- **Audio diagnostics are report-only.** With `--audio-analysis`, fingerprint
  and musical-similarity observations are added, but they never change an
  axis (`scoring_eligible: false`, `content_match_is_provenance: false`).

## 2. Attack goals

Each goal lists what the attacker tries to achieve and the observable result
that counts as success. Record every attempt, successful or not, with the cost
template ([COST_RECORD_TEMPLATE.md](COST_RECORD_TEMPLATE.md)); a defence that
held is a result too.

### G1. Fabricated provenance is reported as consistent evidence

The attacker builds an evidence package for audio they did not record (for
example AI-generated or copied audio) that claims a recorded production
history: recorder takes, a sound report, an AAF edit, mixes and a master.

Success: the response reports the package as consistent evidence of the
claimed history:

- `execution_status` is `succeeded`;
- the claimed workflow's required expectations are `met` in
  `workflow_assessment`;
- in `assessment_checks.integrity_decision`, `numeric_release` is not
  `withheld` and `contradiction_count` is 0, so `axes.integrity.value` is a
  number rather than `null`;
- the release rule is the `default-v6` withholding rule (two populated
  layers and an assessed qualifying cross-evidence fact; the optional
  `min_supported_asset_share` gate is off). The field names above are
  unchanged. **[PENDING PIN]** the integrity value that counts as "high".
- Note that in `default-v6` declared technical values form their own facts in
  declaration consistency, so a package with declared values usually has three
  populated layers (confidence 0.8).

Partial success is worth recording: a package that passes some checks and is
stopped by others shows which evidence raises the cost of forgery.

### G2. Creation method is misrepresented

- **G2a. AI audio declared as recorded.** Audio carrying a valid C2PA
  manifest with an AI-generation label is declared `creation_method:
  recorded`. Success: no contradiction under rule
  `c2pa_ai_source_type_matches_creation_method` and no
  `C2PA_KNOWN_AI_SIGNER` finding, so the declaration stands unchallenged.
- **G2b. Human authorship from an untrusted signer.** A manifest signed with
  a self-made certificate claims human authorship. Success: the signer is
  treated as trusted (`validation.trust_status` other than `untrusted`), or
  `attestation_strength` rises as if it were trusted.
- **G2c. Manifest edited after signing.** Success: the manifest is not
  reported as invalid (`C2PA_SIGNATURE_INVALID`,
  `C2PA_CONTENT_BINDING_FAILED` or `C2PA_VALIDATION_FAILED` missing).

Removing a manifest entirely is expected to give `absent`, which by design is
not negative evidence. Record it as a cost observation, not as a G2 success.

### G3. Honest evidence is reported as contradicted (false contradiction)

The attacker, or simply a careful tester, submits genuine production material
and looks for contradictions that the material does not deserve: timecode or
frame-rate edge cases, real recorder quirks, DAW-written BWF, library effects
inside an AAF, legitimately renamed files.

Success: a contradiction finding, a contradicted fact in
`assessment_checks.integrity_decision`, or a severe-rule cap on material
whose history is genuine. Under `default-v6` an ordinary contradiction
neither releases nor withholds a value by itself: with qualifying support the
value is still released, marked `contradicted`, and the contradiction only
lowers its layer mean (on Project Rock Ep01 one forged timecode among 96
facts gives 0.994); without support it stays withheld. **[PENDING PIN]** whether a single false contradiction
counts as success regardless of the value.

The Project Rock dataset (see Section 5) is the main genuine material for G3.

### G4. Robustness: crash, hang, escape or resource exhaustion

Independent of scoring; can be tested now.

Success, any of:

- exit code other than 0 for a request that parses as JSON and has enough
  identity fields to answer (exit code 2 is the documented result for
  unreadable or malformed JSON);
- stdout that is not a single schema-valid response
  (`schemas/scoring-response.schema.json`);
- wall-clock time beyond `options.timeout_seconds` plus a few seconds of
  process start-up, or a missing `timeout` response;
- memory or disk use far beyond the documented limits (512 MiB per object,
  1 GiB per request, 2 MiB of request JSON, 128 nodes);
- any file read outside the supplied `--root` (path traversal, symlinks,
  absolute paths, AAF or C2PA references);
- XML entity expansion or external entity resolution in RIN, ERN, iXML or any
  other XML input;
- parser abuse in FLAC or MP3: oversized or compressed ID3v2 frames, metadata
  blocks that overrun the file, lost MPEG sync, or a FLAC/MP3 disguised by its
  extension or an ID3 prefix;
- any network access while evaluating.

### G5. An input error is hidden or silently corrected

Independent of scoring; can be tested now.

Success: a tampered or malformed non-final input (wrong hash, upper-case hash
or extension, misspelled field, file content of another format under `.wav`,
a file changed during evaluation) is evaluated as if it were correct: no
finding names the problem, and the affected axes are not marked `partial`.

### G6. Audio diagnostics mislead (report-only)

Applies only with `--audio-analysis`. Success: unrelated audio is reported as
`supported_within_profile` correspondence, or audio really reused in the
target is hidden from every provider while the evaluator presents the result
as a completed non-match rather than `inconclusive` or insufficient support.
These results do not change any axis, so report them as observations about
the diagnostics, not as scoring failures.

### G7. Non-deterministic results

Independent of scoring. Success: the same request and files give different
`axes`, `findings` or `assessment_checks` on the same pinned version
(timings, `processing_ms` and `resource_usage` excluded).

## 3. Known weaknesses

These are documented limits. Testing them is still useful, because the brief
asks for the cost of exploiting them, but finding them again is not a new
defect.

| Weakness | What to measure |
|---|---|
| Recorder metadata (BWF `bext`, iXML) is written by the recorder but not authenticated: it can be rewritten together with the audio and the file rehashed, and a FLAC's stored MD5 can be rewritten with its audio. The response says so with `RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED`. | Time and skill needed to write convincing recorder metadata plus a matching sound report and AAF. |
| Sound reports and AAF files come from the submitter; their value is agreement with the recorder metadata, which a forger controls too. | Whether any cross-check catches a consistent forgery (expected: few or none). |
| BWF origination dates can be reset; a source newer than its target gives `DECLARED_SOURCE_NEWER_THAN_TARGET` (low, not scored). | Cost of making dates consistent. |
| Stripping a C2PA manifest gives `absent`, not a negative result. | Effort to strip or re-encode without leaving traces. |
| C2PA certificate revocation is not checked (`revocation_status: not_checked`, offline by design), and there is no time-stamp authority check. | Whether a revoked or expired signer is accepted. |
| Only two known AI signers are listed (BytePlus Lumina and Suno, matched by exact certificate fingerprint; neither confirmed by the vendor). | AI audio from other generators that is not recognised as AI-labelled; whether a certificate copying a listed signer's name or serial number is recognised (expected: no). |
| Submitting a recording as MP3, or as FLAC encoded without `--keep-foreign-metadata`, removes the recorder metadata, so sound-report and AAF recorder checks cannot run. This is reported (`AUDIO_FORMAT_LIMITED_CHECKS`, `FLAC_RECORDER_METADATA_ABSENT`), not counted as a contradiction. | Whether converting a fabricated package to MP3 or plain FLAC avoids contradictions a WAV version would have shown. |
| FLAC and MP3 receive only the checks implemented today; the rest are reported as not assessed (`limited_checks`, `limitation: program_scope`, `format` or `file`), and diagnostics thresholds are calibrated on WAV only. | Whether delivering FLAC or MP3 instead of WAV avoids a contradiction a WAV would show (expected: yes for recorder, chronology and bit-depth checks; that is the documented scope, not a defect). |
| A declared format conversion proves only that two submitted files hold the same samples (lossless) or the same parameters (lossy). Pairing a fabricated WAV with its own FLAC gives `identical`; it does not authenticate the WAV. | Whether a conversion pair adds apparent support without new evidence (expected: it adds declaration checks only). |
| Creation-method declarations are taken as declared; `ai_disclosure` measures coverage only. | Not an attack on its own; combine with G1 or G2. |
| Audio diagnostics can match shared material (a licensed loop or effect) between unrelated works. Five free-site MP3s that share short sounds at fixed times match at offset 0 in 6 of 10 pairs. | Expected behaviour; record cases. |
| The `bandwidth` diagnostic (report-only, uncalibrated) reads only where the audio stops. Restoring high frequencies (synthesised "air", noise above the old low-pass), or low-passing a genuine file, changes its reading; AI output is band-limited too. It never scores. | Effort to make a fake FLAC or upsampled file read `no_band_limit_found`; genuine files flagged. |

## 4. Out of scope for this round

- Attacks on the host, the operating system, the Python interpreter or the
  installed dependencies (supply chain).
- The desktop manifest builder (`builder_gui.py`); the target is `run.py`.
- Statistical false-accept or false-reject rates: there is no decision policy
  to compute them against.
- The public CSEC bundle profile beyond single audio files: it accepts
  `audio/wav`, `audio/flac` and `audio/mpeg` with `draft-of.audio` only, so
  most music evidence can be tested only through native requests
  ([NATIVE_INPUT_GUIDE.md](NATIVE_INPUT_GUIDE.md)).

## 5. Test material

- **Project Rock dataset.** CSEC already holds the Project Rock
  post-production material supplied by the client (Sound Devices poly BWF
  takes, sound reports, AAF edits, Pro Tools WIP mixes, Final Cut Pro XML and
  ProRes masters). We do not send it again. It is the genuine baseline for G3
  and the starting point for G1 forgeries. Our own run of it is summarised in
  `docs/PROJECT_ROCK_FINDINGS_2026-10-22.md`. The sound reports contain crew
  contact details: do not copy them into issue reports or shared results;
  refer to files by name or SHA-256.
- **Engineering fixtures** in `examples/`: synthetic, not genuine history.
- **AI-labelled audio**: Lumina output with an embedded BytePlus C2PA
  manifest, as used in `docs/FALSE_MATCH_BENCHMARK_2026-10-21.md`.

## 6. Rules of engagement

- Test the pinned version only, and record `system.version` and
  `system.source_revision` from each response. **[PENDING PIN]**
  pinned version and Git tag.
- Run locally. The evaluator needs no network, credentials or online
  services.
- Save every case as a reproducible bundle: the request JSON plus its
  `objects/` folder, or the script that generates them. Cases are rerun
  against later versions.
- Report each defect with [ISSUE_REPORT_TEMPLATE.md](ISSUE_REPORT_TEMPLATE.md)
  and each attempt's cost with
  [COST_RECORD_TEMPLATE.md](COST_RECORD_TEMPLATE.md).
- A G4 or G5 problem found on 0.8.1 can be reported at once. Report G1–G3
  results only against the pinned post-update version.
