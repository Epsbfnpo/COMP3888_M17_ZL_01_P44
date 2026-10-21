"""Evidence categories for consistency scoring, not authenticated origin.

The default policy permits assessed ``signed_attestation`` and
``recorder_metadata`` checks to qualify numeric Integrity release.
``recorder_metadata`` means fields read from a hash-bound WAV; it does not
establish who wrote them. A non-scoring finding records that limitation.
Submitter documents alone do not qualify release. Neither a successful
comparison nor signature validity establishes authorship or historical origin.
"""

SIGNED_ATTESTATION = "signed_attestation"
RECORDER_METADATA = "recorder_metadata"
SUBMITTER_DOCUMENT = "submitter_document"
TIERS = (SIGNED_ATTESTATION, RECORDER_METADATA, SUBMITTER_DOCUMENT)

# A signature that merely validates can come from a self-made certificate, so
# only a credential the SDK reports as trusted identifies an accountable signer.
VERIFIED_C2PA_STATES = {"trusted"}
# The signature itself verified, whoever the signer is.
SIGNATURE_VALID_C2PA_STATES = {"valid", "trusted"}


def c2pa_tier(manifest, reduces_human_share=False):
    """Trust is asymmetric.

    Evidence that lowers the human share of a work (an AI-generation label) is
    accepted from any valid signature or from a known AI generation signer:
    nobody gains by forging a claim that only hurts their own assessment.
    Evidence that supports human authorship needs a trusted signer.
    """
    manifest = manifest or {}
    state = str(manifest.get("validation_state") or "").casefold()
    if reduces_human_share and (state in SIGNATURE_VALID_C2PA_STATES or manifest.get("known_ai_signer")):
        return SIGNED_ATTESTATION
    return SIGNED_ATTESTATION if state in VERIFIED_C2PA_STATES else SUBMITTER_DOCUMENT


def assign_default_tiers(checks):
    """Checks that did not state a tier compare submitter documents."""
    for item in checks:
        item.setdefault("evidence_tier", SUBMITTER_DOCUMENT)
    return checks


def tier_counts(checks):
    counts = {tier: {"assessed": 0, "passed": 0} for tier in TIERS}
    for item in checks:
        if isinstance(item.get("passed"), bool):
            counts[item["evidence_tier"]]["assessed"] += 1
            counts[item["evidence_tier"]]["passed"] += item["passed"]
    return counts
