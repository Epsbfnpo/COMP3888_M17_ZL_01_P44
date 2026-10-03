# Schema changelog

## 0.0.4

- Reserves audio `provenance` and `parser_diagnostics` for parser/verifier
  output even when `parse_c2pa` is false, closing the self-attestation path.
- Adds C2PA `read_status` so dependency absence, read failure, no active
  manifest, and a successfully read manifest remain distinct.
- Requires machine-origin relationships to provide `asserted_by` plus an RFC
  6901 `reference_pointer`.
- Requires the declaring artefact type, pointed value, endpoint hashes, and
  declaring file SHA-256 binding to be verified before machine-origin
  confidence or forge cost is used.
- Retains the complete raw C2PA ingredient object so its pointer can be checked.

The profile version is **0.1.3**. Attestation is now computed only from a live
C2PA read of a SHA-256-bound file. An invalid signature or content binding
forces the attestation axis to zero and cannot be hidden by a stronger result
on another artefact. The maximum level requires explicit signature,
certificate-chain, revocation, and trust-list passes; unknown results do not
silently count as trusted.
