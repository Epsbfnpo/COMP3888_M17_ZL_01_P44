"""Who produced the evidence behind each cross-evidence check.

A check's tier records how hard its evidence is to fabricate, not whether it
passed. Submitter-provided documents (RIN, ERN, CompSheet, CueSheet, MIDI,
AAF, sound reports, unverified C2PA manifests) are declarations in another
format: they corroborate, and they can reveal a contradiction, but agreeing
with the submitted graph cannot by itself release an Integrity value. Two
tiers are independent of the submitter's own documents:

- ``signed_attestation``: a C2PA manifest whose signature validated;
- ``recorder_metadata``: comparisons against BWF/iXML metadata a recorder
  wrote into a hash-bound audio file. It can be forged, but only by also
  rewriting the bound audio file consistently.
"""

SIGNED_ATTESTATION = "signed_attestation"
RECORDER_METADATA = "recorder_metadata"
SUBMITTER_DOCUMENT = "submitter_document"
TIERS = (SIGNED_ATTESTATION, RECORDER_METADATA, SUBMITTER_DOCUMENT)

# C2PA SDK validation states that mean the manifest signature verified.
VERIFIED_C2PA_STATES = {"valid", "trusted"}


def c2pa_tier(manifest):
    state = str((manifest or {}).get("validation_state") or "").casefold()
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
