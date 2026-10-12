# Verification Record

Updated: 2026-10-12. Scope: `V3/program` v0.5.0.

- `.venv/bin/python -m unittest discover -s tests -v`: **97 passed, 0 failures, 0 errors** using the pinned dependencies in `requirements.txt`, including `numpy==2.2.6` on CPython 3.13.7.
- Coverage includes graph safety, SHA-256/path boundaries, PCM/WAV, 16 workflows, MIDI, sheets, DDEX, C2PA/AI CrossEvidence, and the Edit/Comp/Stem/Mix/Master derivation suite.
- Exact derivation checks use only declared ranges and gains. Original-rate PCM is processed in approximately ten-second blocks with vectorised placement, gain/fade envelopes, alternating holdout statistics, Gram accumulation, coverage and cross-block local-mismatch state. Tests cover ranges beyond 60 seconds, repeated locations, source identifiability and local tampering.
- PCM decoding produces C-contiguous `(frames, channels)` NumPy `float32` arrays for 8/16/24/32-bit integer PCM. Tests cover signed 24-bit expansion, window seeking, ndarray caching and active-memory rejection.
- Complete-file proxies are generated in phase-aligned overlapping blocks with Kaiser-windowed sinc filtering. Tests cover a cached 48 kHz to 2000 Hz stereo proxy across the ten-second block boundary, non-integer 2100 Hz to 400 Hz anti-alias filtering, and full-file excerpt discovery at 65 seconds.
- Eight physical-audio diagnostics are integrated: FFT-coarse/full-file landmark alignment, leave-one-out source contribution, per-selection CompSheet verification, processed-audio landmark/DTW comparison, source-channel residual modelling, mastering continuity, excerpt search, and decoy-source detection. Estimated parameters remain diagnostic and do not rewrite graph claims, count as exact provenance proof, or override exact declared contradictions.
- Resource guards include a 128 MiB active-window limit, 256 MiB proxy-cache limit, weighted request work limit and the existing wall-clock timeout. Tests confirm that an incomplete budget-limited search returns `unavailable` with `resource_budget_exhausted` and never `contradicted`.
- Audio outputs report actual analysed/searched ranges, completion state and proxy sample rate.
- Integrity exposes file, structural, declaration, content, CrossEvidence, and cryptographic layers; structural validity and attestation do not raise integrity.
- The RIN 2.1 and ERN 4.3 XSD sets compile offline. Invalid or synthetic XML reports `invalid` or `unsupported_version` while retaining best-effort extraction.
- SHA-256 audio binding is tested for RIN `FileReference` and ERN `DeliveryFile`.
- Every example response validates against `scoring-response.schema.json`.

Remaining work requires real XSD-valid positive RIN/ERN fixtures, real valid/tampered/untrusted C2PA fixtures, and threshold/weight calibration with real production and adversarial audio. The public CSEC adapter remains WAV-only. The four dimensions remain separate and do not include a decision policy, accepted/rejected outcome, or overall score.
