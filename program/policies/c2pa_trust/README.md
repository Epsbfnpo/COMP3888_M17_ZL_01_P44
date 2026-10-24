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

## Known AI signers

`known_ai_signers.json` matches a manifest's signing certificate by its exact
SHA-256 fingerprint (base64, as the SDK's allowed list requires). Names,
subjects, issuers and serial numbers are recorded for people; the serial
number only chooses the name to report, and a certificate that copies a listed
name or serial number is not recognised.

| Signer | Certificate | Valid | Evidence |
|---|---|---|---|
| BytePlus Lumina (Seed Audio) | `CN=certificate@byteplus.com, O=Byteplus Pte. Ltd.`, issued by GlobalSign GCC R45 SMIME CA 2025 | until 2028-03-16 | Lumina WAV and MP3 output, 2026-10-21 and 2026-10-24 |
| Suno | `CN=Suno Content Credentials, O=Suno Inc`, issued by Suno's private `CN=Suno C2PA Root CA, O=Suno Inc` | 2026-07-23 to 2028-10-25 | Two Suno MP3 downloads of separate generations (chirp-goose-t1), 2026-10-24: both valid, `trainedAlgorithmicMedia`, DigiCert time-stamp, same signing certificate. Their SHA-256 hashes are in `verified_samples`; the audio is not committed. |

Recognition is not trust. A listed signer is not on the C2PA Trust List or the
legacy ITL, so its manifests stay `untrusted` (attestation strength 0.3), and
listing never changes AI disclosure coverage. What it changes: the manifest's
AI label is accepted as `signed_attestation` even without an AI source type or
when the manifest is invalid, and `C2PA_KNOWN_AI_SIGNER` names the generator
(medium if the artefact was not declared `ai-generated`). Neither the Suno root
nor any other listed signer is added to the pinned trust lists.

To add a signer: take the certificate from real output (at least two files
from separate generations, signed by the same certificate), record each file's
SHA-256 and the extraction date, and confirm with
`_known_ai_signer` that the SDK matches the fingerprint. Certificates expire and
are replaced, so a new certificate needs a new entry. To recheck the Suno
downloads, run `C2PA_SUNO_SAMPLE_DIR=<folder> python -B -m unittest
tests.test_c2pa_integrity.SunoSampleTests` from `program/`.

## Updating a list

1. Pick the new commit of the source repository and download each file from that commit (not from `main`).
2. Replace the files, then update `commit`, `commit_date`, `retrieved` and every `sha256` in `trust_lists.json` (`shasum -a 256 <file>`).
3. Run `python -B -m unittest tests.test_c2pa_integrity` from `program/`; `test_shipped_trust_lists_match_their_pins` confirms the pins.
4. Record the change in the commit message, because results produced before and after the update can differ.
