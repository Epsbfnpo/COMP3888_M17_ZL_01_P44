# C2PA trust configuration

| File | Purpose |
|---|---|
| `trust_lists.json` | Pins every trust file by source commit SHA and SHA-256. The evaluator refuses to run if a file differs. |
| `official/C2PA-TRUST-LIST.pem` | Official C2PA Trust List (claim-signing anchors), from `c2pa-org/conformance-public`. |
| `official/C2PA-TSA-TRUST-LIST.pem` | Official time-stamp authority list. Pinned and recorded only: c2pa-python 0.37.10 has no separate TSA anchor setting. |
| `itl/anchors.pem`, `itl/allowed.pem`, `itl/store.cfg` | Frozen legacy Interim Trust List (anchors, end-entity certificates, accepted EKUs), from `contentauth/verify-site` `static/trust/`. |
| `known_ai_signers.json` | Signing certificates of known AI generation tools. Not a trust list: it only identifies signed AI labels, which are accepted as evidence that lowers the human share. |

## How trust is decided

1. The SDK validates the manifest against the official list. `Trusted` gives `trusted_c2pa`; `Invalid` gives `invalid`.
2. Otherwise the manifest is validated against the ITL. A match counts as `trusted_legacy_itl` only when the SDK reports a signing time before the freeze date (`frozen_on`). Without that time the signer stays `untrusted` and the finding explains why.
3. Everything else is `untrusted`. Revocation is never checked (offline validation).

A trusted signature identifies who signed a manifest; it does not verify that the manifest's claims are true.

## Updating a list

1. Pick the new commit of the source repository and download each file from that commit (not from `main`).
2. Replace the files, then update `commit`, `commit_date`, `retrieved` and every `sha256` in `trust_lists.json` (`shasum -a 256 <file>`).
3. Run `python -B -m unittest tests.test_c2pa_integrity` from `program/`; `test_shipped_trust_lists_match_their_pins` confirms the pins.
4. Record the change in the commit message, because results produced before and after the update can differ.
