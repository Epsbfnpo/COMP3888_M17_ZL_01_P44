# Verification Record

## Version 0.8.2 and the new CSEC pin (2026-10-27)

- Pin: tag `csec-2026-10-27` (proposed), version 0.8.2, scoring profile `default-v7`, `source_revision` `sha256:90716ba4d277b654807a335106b054e2de7829c3fa3656f822fad0cad278b021`.
- Fresh environment: the files Git tracks were exported into an empty folder, a new virtual environment was created (CPython 3.13.7, macOS) and `requirements.txt`, `requirements-audio.txt` and `requirements-benchmark.txt` were installed. The exported code reported the pinned `source_revision`, and the valid-generated example returned `succeeded` with version 0.8.2.
- With `REQUIRE_AUDIO_BACKENDS=1` and `C2PA_SUNO_SAMPLE_DIR` set to the verified Suno downloads, **496 non-GUI tests passed in one process (59 s) with no skips**; the five `test_builder.GuiTests` cases passed in separate processes. Total: 501 tests, all passed.
- Example responses regenerated: every axis value, confidence and status is identical to 0.8.1; each audio file now carries a passing `audio_samples_valid` row.
- Real data: all 236 Project Rock WAVs (3.37 GiB) pass `audio_samples_valid` (1.2 s in total); all 233 recorder takes pass the bext/iXML timestamp check, and the forge-cost forgery of 10T07 (+1 s in bext only) fails it by 1.000 s.
- The 25 October archive received by CSEC lacked both `schemas/ddex/*/allowed-value-sets.xsd` files. Hashing the 25 October export without them gives `sha256:a3c0a5ed…`, the value CSEC reported; with them it gives the pinned `sha256:ac048707…`.
- Coverage re-measured on 28 October 2026 at commit `f148497` (same code as the pin), following the README procedure with `C2PA_SUNO_SAMPLE_DIR` set: 496 non-GUI tests passed in one process (52 s, no skips) and the five GUI tests passed in separate processes. 74.7% of lines and 65.0% of branches; 85% of lines without the policy-disabled `physical_audio_passes.py` and `audio_derivation.py`. Report: `reports/coverage/`.
- Test plan traceability (28 October 2026): `docs/testing/04_TEST_TRACEABILITY.md` maps the 86 numbered plan cases to tests. 77 are fully automated, 7 partly, 1 is manual (SI-10, Project Rock) and 1 has no test (AU-08, calibration profile digest mismatch).

## CSEC baseline pin and clean install (2026-10-25)

- Pinned baseline for CSEC: tag `csec-2026-10-25`, version 0.8.1, scoring profile `default-v7`, `source_revision` `sha256:ac048707f1bc29f53bc8ffa2ca27d8e121f6da0008fd397dddfc3bea4b9cbf5c`. Only documentation changed after the forge-cost merge, and documentation is not part of `source_revision`.
- Clean install: `git archive main program` into an empty folder, new virtual environment, `pip install -r requirements.txt -r requirements-audio.txt`. With `REQUIRE_AUDIO_BACKENDS=1`, 462 non-GUI tests ran: all passed except 3 of the 5 structure-benchmark tests, which need `mir_eval`; after `pip install -r requirements-benchmark.txt` those 5 passed. GUI tests passed in separate processes. The example response reported `source_revision` `sha256:ac048707…cbf5c`, matching the pin. The native input guide now lists all three requirement files.

## First-demo feedback work: declared ranges, content beyond the final, forge cost (2026-10-25)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **466 tests; 463 passed, 3 skipped**. 462 non-GUI tests ran in one process (40 s); the 3 skips are the Suno real-sample tests, which need `C2PA_SUNO_SAMPLE_DIR`. The four `test_builder.GuiTests` cases passed in separate processes.
- Coverage of the same runs (coverage.py, branch measurement): 73.3% of lines and 63.5% of branches; 84% of lines without the policy-disabled `physical_audio_passes.py` and `audio_derivation.py`. Report: `reports/coverage/`.
- Declared ranges: on the repeated-excerpt fixture, declared 2-6 s and 10-14 s windows give `declared_range_supported`; a declared 7-11 s window gives `AUDIO_DECLARED_RANGE_NOT_SUPPORTED` (`content_found_elsewhere`, best source range 2.0-6.0 s, SpectroMap agrees). Integrity and every non-audio finding are identical with analysis on or off.
- Content beyond the final, Project Rock Ep01 (13 AAF-used takes, WIP mix): 26 unused pieces analysed; the 6 takes with a positive control report 10.1-32.8 s of unused material not found (119.9 s in total); 7 takes have no positive control; one unused piece has a 6-pair, 0.5 s SpectroMap candidate. Run time 35.5 s, 465 MB peak (previously about 19 s). No-AAF variant: a real take reports 15.1 s beyond the mix; an exact and a band-passed cut of the mix are both `contained_in_target`.
- Forge cost (`benchmarks/results/forge_cost_project_rock_2026-10-25.json`): smallest undetected forgeries are WAV + sound report + AAF for a take number (cost 4), WAV + sound report for a 1 s start-timecode shift (cost 3) and the cue sheet alone for a cue range (cost 1). All five stated orderings held for strength floors 0-0.9 under the derived costs; default-v6 failed two. Held-out Ep01: value 1.0 (unchanged), confidence 0.427 (default-v6: 0.533).
- Example responses regenerated: every axis value identical to the committed versions; Integrity confidence multiplied by the forge-cost factor (0.6 without cross-evidence, so 0.533 becomes 0.32 and 0.267 becomes 0.16).
- `reports/public-results/` was not regenerated; the public CSEC bundles are not stored in the repository.

## Suno as a known AI signer (2026-10-24, branch feature/c2pa-ai-signer-expansion)

- CPython 3.12.2 on macOS, c2pa-python 0.37.10 (`.venv312`): `tests.test_c2pa_integrity` **33 tests, OK (30 passed, 3 skipped without the Suno downloads)**; with `C2PA_SUNO_SAMPLE_DIR` set, the module plus `test_audio_formats.C2paInFlacAndMp3Tests` ran **35 tests, all passed, no skips**.
- Full non-GUI run in this environment: 378 tests, 2 failures and 10 errors, all from the optional audio packages that are not installed in `.venv312` (scipy, librosa, spectromap). The same run on an unmodified export of `HEAD` gives the same 12 failing tests (369 tests), so none is caused by this change. The 425-test record below used the full audio environment.
- Samples: two Suno MP3 downloads of separate generations (SHA-256 `b5ea01a4…9b47555` and `22565ec8…5c72f910`). Both manifests, in an ID3v2.4 `GEOB` frame, are valid with no failure codes, declare `trainedAlgorithmicMedia`, carry a DigiCert time-stamp (06:30:04Z and 06:42:55Z) and are signed by the same certificate: `CN=Suno Content Credentials, O=Suno Inc`, issued by `CN=Suno C2PA Root CA, O=Suno Inc`, valid 2026-07-23 to 2028-10-25, fingerprint `Q60v1k3DH0FLokCC25J/xRvIMOGEFTfKG7FAZWzH+2A=`. The SDK allowed-list match accepts that fingerprint and rejects a different one.
- New: `SunoKnownSignerTests` (6 tests: the shipped entry, a certificate copying Suno's subject and serial number not recognised, attestation strength and AI disclosure identical with and without the entry, `recorded` contradicted, `ai-generated` consistent) and `SunoSampleTests` (3 tests on the real downloads, matched by SHA-256; skipped unless `C2PA_SUNO_SAMPLE_DIR` is set). The audio is not committed.
- Scoring logic and pinned trust lists unchanged; Suno stays `untrusted` (0.3).

## Other AI music generators (2026-10-24, branch feature/c2pa-ai-signer-expansion)

Original output from four more generators was checked against the evaluator. No
fingerprint was added; the details are in `policies/c2pa_trust/README.md`.

- **Stability AI** (WAV): valid manifest, trusted certificate, explicit
  AI-generation declaration. Handled correctly by trust validation.
- **Google Lyria** (MP3): valid, trusted Google manifest with explicit
  AI-generation metadata. Handled correctly by trust validation.
- **ElevenLabs** (WAV): no C2PA manifest, so the file reads as `absent`.
  ElevenLabs documents signing only for opt-in MP3 output from its API, so no
  current certificate could be verified. Recorded as a limitation.
- **Adobe Firefly** (WAV): references a valid remotely hosted manifest. The
  evaluator validates offline with remote manifest fetching off, so the file
  reads as `absent`. Recorded as a limitation; a fingerprint would not change
  this.
- Not yet recorded: the samples' SHA-256 hashes and signing certificate
  details, and automated tests on these samples (the audio is not committed).

## WAV focus and FLAC/MP3 scope (2026-10-24, branch feature/merge-main-flac-mp3)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **425 tests, all passed**. 421 non-GUI tests ran in one process (34 s, no skips); the four `test_builder.GuiTests` cases ran in separate processes.
- Decision: WAV is the assessed format; FLAC and MP3 keep the checks implemented today, and every other check is reported as not assessed. `FORMAT_SCOPE` in `music_target/audio_observations.py` is the single list; new tests require every check family and every declarable technical field to have a status for FLAC and MP3, check the per-file `limited_checks` (a plain FLAC lists recorder metadata, BWF chronology and tag dates; WAV lists none) and the WAV-only calibration note on diagnostics rows. The MP3 test now expects seven families with their limitation.
- Example responses: every `axes` object identical to main.

## Mixed-format evidence (2026-10-24, branch feature/merge-main-flac-mp3)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **422 tests, all passed**. 418 non-GUI tests ran in one process (35 s, no skips); the four `test_builder.GuiTests` cases ran in separate processes.
- New: `tests/test_format_conversion.py` (13 tests): one canonical sample digest for WAV and FLAC of the same integer samples (16-bit, 24-bit, unsigned 8-bit WAV against signed 8-bit FLAC), a changed sample, float WAV not digested; conversion checks for WAV→FLAC, a resampled target, WAV→MP3 and a non-`derived_from` edge; an unknown `transformation` value rejected; capability rows and advice, removed once the original is declared; the diagnostics alignment summary; the builder probe, advice and conversion checks.
- Writing the same float signal with libsndfile gives different integer samples in WAV and in FLAC (95 951 of 96 123 frames differ at 16 bits), so the identity tests write integer samples.
- Real files: the SoniqTools Bach WAV and FLAC are sample-identical at 16/44.1 and 24/96; WAV→320 kbps MP3 and 128 kbps MP3→fake FLAC pass the parameter checks and are not sample-compared; the fake 24/96 FLAC upsampled from CD audio, declared as a conversion of the CD WAV, fails the sample-rate and bit-depth checks.
- Example responses: every `axes` object identical to main; they gain `format_capabilities` rows.

## Cross-format review changes (2026-10-24, branch feature/merge-main-flac-mp3)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **409 tests, all passed**. 405 non-GUI tests ran in one process (33 s, no skips); the four `test_builder.GuiTests` cases ran in separate processes.
- New tests: AAF-used takes in the support-breadth denominator (counted once across AAFs), provisional coverage when a submitted take has no recorder identity, not applicable without required recordings, the workflow policy loaded once, undated MP3 and FLAC edges as `not_comparable`, a wrong STREAMINFO sample count never passing the MD5 check, and the decoder versions in the audio summary. The AAF missing-take test now checks that only the reasoning changes, not the scores.
- Example responses: axes and Integrity reasoning identical to main for `valid-generated`, `csec-wav` and `wav-match`.
- Project Rock Ep01: Integrity unchanged in every variant. With one AAF-used take removed, `support_breadth` reads 12 of 13 (`not_submitted: 1`) instead of 12/12; plain FLAC takes give 13 `unmatched_reference` with provisional coverage; the MP3 mix lists 13 `not_comparable` chronology rows (`format_has_no_bwf`).

## Merge of main (scoring default-v6) with FLAC/MP3 support (2026-10-24, branch feature/merge-main-flac-mp3)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **403 tests, all passed**. 399 non-GUI tests ran in one process (32 s, no skips); the four `test_builder.GuiTests` cases ran in separate processes.
- Conflicts: `music_target/assessment_passes.py` (both branches added a helper in the same place; both kept) and five example responses (regenerated). No other file conflicted.
- FLAC and MP3 technical claims use the same `technical.*` rules as WAV, so after the merge they are scored in declaration consistency (`declared_file_properties`, one fact per declared property); claims MP3 cannot measure stay unavailable facts and are not counted. The FLAC MD5 check carries no submitter claim and stays in file integrity. A new assertion in `tests/test_audio_formats.py` checks this.
- WAV examples: every `axes` object is identical to main. Testing_music pairs: every fingerprint, semantics and bandwidth row and every axis is identical to the FLAC/MP3 branch.
- Project Rock Ep01 format variants rerun on the merged code: same conclusions as on 23 October; assessed facts are 96 instead of 122 because `one_vote_per_claim` merges the sound-report and AAF scene/take and track-name claims for each of the 13 takes. `support_breadth` reads 13/13 for the WAV, kept-metadata FLAC and MP3 variants and 0/13 for plain FLAC takes, whose recorder metadata is lost.

## Testing_music run and content bandwidth (2026-10-24, branch feature/flac-mp3-support)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **386 tests, all passed**. 382 non-GUI tests ran in one process (32 s, no skips); the four `test_builder.GuiTests` cases ran in separate processes.
- New: `tests/test_bandwidth.py` (9 tests: assessment rules, detector on synthetic low-passed, floor-masked and upsampled noise, block-size invariance, insufficient signal, evaluator axes unchanged) and one builder test for the per-file bandwidth lines.
- Material: 11 downloaded files (6 free-site MP3s, Bach as 16/44.1 and 24/96 WAV and FLAC, one Lumina 16 kHz MP3) and 7 locally made files (genuine 128 and 320 kbps MP3s, a fake 320 kbps MP3 from 128, a fake FLAC from 128, a fake 24/96 FLAC and WAV from CD audio). Every single-file and pair case completed with audio analysis.
- Problems found and fixed: (1) fake formats passed unnoticed; the new report-only `bandwidth` provider flags all four trick files and none of the four genuine lossless files, and both genuine encodes read `consistent_with_bitrate`; (2) a CBR MP3 without gapless information (Jingle Bells) was refused by the decoder length guard for a 439-sample shortfall; the guard now allows two frames when no gapless length exists.
- Observed, not changed: the free-site MP3s share watermark-like bursts at fixed times, so audfprint reports offset-0 supported matches in 6 of 10 unrelated pairs; this is real shared audio, and the frozen gates are unchanged (`docs/FALSE_MATCH_BENCHMARK_2026-10-21.md`).
- Real material: Project Rock 1 of 15 files flagged (the 48 kHz edit reference, upsampled from 44.1 kHz); all 25 Lumina WAVs are band-limited (16.9–20.1 kHz), so a flag is never a verdict. Axes are identical with and without the provider.

## FLAC and MP3 support (2026-10-23, branch feature/flac-mp3-support)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **376 tests, all passed**. 372 non-GUI tests ran in one process (31 s, no skips); the four `test_builder.GuiTests` cases ran in separate processes.
- New: `tests/test_audio_formats.py` (29 tests) for detection by content (including an ID3v2 tag before `fLaC`), bounded ID3v2, FLAC blocks, kept WAV chunks and MD5, MP3 exact and estimated length, lost sync and tags, evaluator results, C2PA in FLAC and MP3, diagnostics decoding, the public profile and the builder probe. A malformed FLAC named `.wav` is now an input error (read as FLAC) instead of `unsupported`; an Ogg file named `.wav` is still `unsupported`.
- Checked by hand against reference tools: the FLAC MD5 recomputed from soundfile equals `metaflac --show-md5sum` for 8-, 16- and 24-bit files with 1–3 channels; the MP3 exact sample count equals soundfile's gapless length for 16 LAME and FFmpeg encodes (CBR, VBR, 11.025–48 kHz, MPEG-1, 2 and 2.5); a 20-minute MP3 is walked in 0.07 s and a 299 MiB FLAC MD5 is verified in 1.6 s.
- Real material: Project Rock Ep01 rerun as FLAC (kept and plain metadata), MP3 320 and 128 kbps and all-FLAC; see `docs/PROJECT_ROCK_FINDINGS_2026-10-22.md` (FLAC with kept metadata identical to WAV; plain FLAC withholds Integrity; MP3 loses only chronology).
- Real Lumina MP3 (Seed Speech 2.0, 24 and 48 kHz): C2PA in an ID3v2.4 `GEOB` frame, valid, known AI signer (same BytePlus certificate as the WAV output), untrusted; `ai-generated` consistent, `recorded` contradicted. These files have no Xing header; libsndfile mis-estimates such lengths, so the diagnostics reader now decodes only the MPEG frames and refuses truncated output (`decoder_length_mismatch`).
- WAV unchanged: every example regenerates identically apart from `source_revision` and the `mp3_encoded_queries` calibration note. On the repeated-excerpt example, WAV, FLAC and MP3 versions give the same audfprint offsets (2.0 s and 10.01 s).

## Version 0.8.1, calibration intervals (2026-10-23)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **342 tests, all passed**. 338 non-GUI tests ran in one process (30 s, no skips); the four `test_builder.GuiTests` cases ran in separate processes.
- New: `music_target/case_intervals.py` (95% Wilson score intervals) and `tests/test_case_intervals.py` (10 tests). Every count in `policies/audio_calibration_status.json` now carries `wilson_95` and sits under `development`, `held_out` or `real_material`; a test recomputes every stored interval from its count. `benchmarks/audio_calibration.py` and `benchmarks/false_match.py` output the intervals; the builder shows them beside the benchmark counts.
- No score, gate or finding changed. The status file is hashed into `source_revision`, so all example responses were regenerated; in `examples/audio-diagnostics/response.json` only the attached `calibration` objects, the status hash, `source_revision` and timings differ.

## Version 0.8.1 (2026-10-22)

- `REQUIRE_AUDIO_BACKENDS=1`, CPython 3.13.7 on macOS: **332 tests, all passed**. 328 non-GUI tests ran in one process (31 s, no skips); the four `test_builder.GuiTests` cases ran in separate processes. Details: `reports/test-results.txt` and `reports/release.json`.
- New regression coverage: DAW and library BWF are not recorder metadata; raw takes may be mixed directly; AAF-used takes missing from a submission and the AAF's non-recorder media are reported without changing a score; contradiction counts live in `integrity_decision` with a per-layer breakdown and singular/plural wording; AAF-declared source ranges drive audio diagnostic tasks, and only those tasks may leave the target range open; a source originated after its target is reported, not scored. The desktop builder's preflight now accepts exactly the evaluator's size limits (512 MiB per object, 1 GiB per request); before, it refused files over 32 MiB.
- Real material (Project Rock Ep01, outside the repository): core axes unchanged by every reporting change; audio on/off parity holds including the new records; 6 of 14 AAF-declared take ranges were found in the WIP mix. See `docs/PROJECT_ROCK_FINDINGS_2026-10-22.md`.
- Example responses under `examples/` were regenerated with 0.8.1. Since the 0.7.0 snapshots, `attestation_strength` reads 0.0 rather than null for files without a C2PA manifest, as defined in 0.8.0. `examples/wav-match/assessment-result.json` (last produced by 0.4.0) now withholds Integrity, as default-v4 requires qualifying cross-evidence. `reports/public-results/` was not regenerated, because the public CSEC bundles are not stored in the repository; its README explains how to refresh it.

## Historical record (v0.7.0 phase 0)

Updated: 2026-10-14. Scope: `V3/program` v0.7.0 phase 0.

- `.venv/bin/python -B -m unittest discover -s tests -v`: **102 passed, 0 failures, 0 errors** on CPython 3.13.7 with the pinned dependencies in `requirements.txt`, including `numpy==2.2.6`. The phase-0 change has not yet been rerun across every supported Python/platform combination.
- Input validation never corrects input. Tests cover final-first stopping (hash mismatch, mapping case, upper-case extension, size limit, FLAC content named `.wav`, a final file changed during evaluation), re-evaluation with a non-final file isolated when it changes during evaluation (and `error` if files change again), non-final isolation (hash mismatch, upper-case hash and extension, size limits, symlink escape), `UNKNOWN_FIELD` suggestions, a misspelled fade that no longer produces a false contradiction, `not_assessed` workflow rules, unused mappings, and the case-mismatch hint for dangling references.
- Coverage includes graph safety, SHA-256/path boundaries, PCM/WAV, 16 workflows, MIDI, sheets, DDEX, C2PA/AI CrossEvidence, and policy-disabled physical diagnostics. A regression test patches `PhysicalSimilarityPass.evaluate` to raise and confirms that assessment never invokes it. Deprecated signal helpers remain directly testable while isolated from production assessment.
- The shared streaming WAV reader supports 8/16/24/32-bit integer PCM, 32/64-bit IEEE float, and both corresponding `WAVE_FORMAT_EXTENSIBLE` SubFormats. Decoding produces C-contiguous `(frames, channels)` NumPy `float32` arrays without whole-file reads. Tests cover signed 24-bit expansion, finite float values outside `[-1, 1]`, float64 conversion, EXTENSIBLE float, NaN/Infinity rejection, trailing-byte warnings, window seeking, ndarray caching and active-memory rejection.
- Complete-file proxies are generated in phase-aligned overlapping blocks with Kaiser-windowed sinc filtering. Tests cover a cached 48 kHz to 2000 Hz stereo proxy across the ten-second block boundary, non-integer 2100 Hz to 400 Hz anti-alias filtering, and full-file excerpt discovery at 65 seconds.
- Every retained relationship receives one compatibility placeholder with `comparison_mode: disabled`, `status: unavailable`, `reason_code: disabled_by_policy`, `scoring_eligible: false`, `integrity_contribution: null`, and `content_match_is_provenance: false`. No signal measurement is executed and no physical-similarity finding is emitted.
- `docs/PHYSICAL_SIMILARITY.md` records the active disabled policy and preserves the former v0.6 algorithm details only as an explicitly inactive historical appendix.
- Deprecated signal helpers retain active-window, proxy-cache, and weighted-work guards, but assessment does not call them. The caller's requested wall-clock timeout still has no evaluator-side 60-second cap. The worker deadline, byte-capped decoding helpers, RIFF chunk boundaries, and changed-file protections remain tested.
- Integrity exposes file, structural, declaration, CrossEvidence, cryptographic, and disabled physical layers. The physical layer has a null value and `contributes_to_integrity: false`; structural validity and attestation also do not raise integrity.
- The RIN 2.1 and ERN 4.3 XSD sets compile offline. Invalid or synthetic XML reports `invalid` or `unsupported_version` while retaining best-effort extraction.
- SHA-256 audio binding is tested for RIN `FileReference` and ERN `DeliveryFile`.
- Every example response validates against `scoring-response.schema.json`.

Remaining work requires the claim-ledger scoring redesign, deterministic metadata/timeline checks, hash-binding coverage, real XSD-valid positive RIN/ERN fixtures, and real valid/tampered/untrusted C2PA fixtures. Optional audio fingerprinting is deferred and must remain non-scoring if later introduced. The public CSEC adapter remains WAV-only. The four dimensions remain separate and do not include a decision policy, accepted/rejected outcome, or overall score.
