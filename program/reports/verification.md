# Verification Record

Updated: 2026-10-06. Scope: `v3/program` v0.5.0.

- `python3 -m unittest discover -s tests -v`: **65 passed, 0 failures, 0 errors** in a clean temporary virtual environment using `requirements.txt`.
- Coverage includes graph safety, SHA-256/path boundaries, PCM/WAV, 16 workflows, MIDI, sheets, DDEX, C2PA/AI CrossEvidence, and the Edit/Comp/Stem/Mix/Master derivation suite.
- Exact derivation checks use only declared ranges and gains and cover partitioned validation, matched duration, target coverage, redundant sources, source identifiability, repeated-segment ambiguity, `matched`, `contradicted`, `not_applicable`, and missing-parameter `unavailable` results.
- Master checks use only declared ranges. Competing locations can expose ambiguity but cannot replace the declaration; coverage, mild processing, sample-rate conversion, `corroborated`, and manual-review output are covered.
- Eight physical-audio diagnostics are integrated: bounded landmark alignment, leave-one-out source contribution, per-selection CompSheet verification, processed-audio landmark/DTW comparison, source-channel residual modelling, mastering continuity, excerpt search, and decoy-source detection. Estimated parameters remain diagnostic and do not rewrite graph claims or count as exact provenance proof.
- Integrity exposes file, structural, declaration, content, CrossEvidence, and cryptographic layers; structural validity and attestation do not raise integrity.
- The RIN 2.1 and ERN 4.3 XSD sets compile offline. Invalid or synthetic XML reports `invalid` or `unsupported_version` while retaining best-effort extraction.
- SHA-256 audio binding is tested for RIN `FileReference` and ERN `DeliveryFile`.
- Every example response validates against `scoring-response.schema.json`.

Remaining work requires real XSD-valid positive RIN/ERN fixtures, real valid/tampered/untrusted C2PA fixtures, and threshold/weight calibration with real production and adversarial audio. The public CSEC adapter remains WAV-only. The four dimensions remain separate and do not include a decision policy, accepted/rejected outcome, or overall score.
