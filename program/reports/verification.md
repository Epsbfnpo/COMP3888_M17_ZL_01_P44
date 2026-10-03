# Verification Record

Updated: 2026-10-03. Scope: `v3/program` v0.4.0.

- `python3 -m unittest discover -s tests -v`: **42 passed, 0 failures, 0 errors**.
- Coverage includes graph safety, SHA-256/path boundaries, PCM/WAV, 16 workflows, MIDI, sheets, DDEX, C2PA/AI CrossEvidence, and AudioDerivation.
- AudioDerivation covers `matched`, `contradicted`, `not_applicable`, and declared time offsets.
- The RIN 2.1 and ERN 4.3 XSD sets compile offline. Invalid or synthetic XML reports `invalid` or `unsupported_version` while retaining best-effort extraction.
- SHA-256 audio binding is tested for RIN `FileReference` and ERN `DeliveryFile`.
- Every example response validates against `scoring-response.schema.json`.

Remaining work requires real XSD-valid positive RIN/ERN fixtures, real valid/tampered/untrusted C2PA fixtures, and AudioDerivation threshold calibration with real stems and mixes. The public CSEC adapter remains WAV-only. The four dimensions remain separate and do not include a decision policy, accepted/rejected outcome, or overall score.
