"""How hard the checked cross-evidence would be to forge; scales Integrity confidence only.

A forge cost (1-5) says how much consistent work it would take to fake the
evidence behind a cross-evidence fact. It never changes the Integrity value:
the value says what the checks found, the confidence how strongly the checked
evidence supports it. Tiers keep their role as the release gate; forge cost
grades the evidence within and below them, and the profile enforces that no
submitter document outranks recorder metadata.

Each subject (one recording, one document collection) takes the cost of its
strongest assessed fact, and the strength is the mean over subjects. A plain
mean over facts would let an extra, weaker corroborating document lower
confidence; per-subject maxima cannot. A fact whose documents or recording are
not hash-bound gets the unbound cost, whatever its tier.
"""

from music_target.evidence_tiers import RECORDER_METADATA, SIGNED_ATTESTATION

# Identity keys that name the check rather than the subject it is about.
_RULE_KEYS = ("rule", "claim")


def _subject_key(identity):
    """The artefacts a fact is about, whatever the check calls them.

    A cue sheet names a take ``asset_hash`` and a sound report ``audio_hash``;
    both are facts about the same recording and must share its subject.
    """
    hashes, other = set(), []
    for name, value in identity.items():
        if name in _RULE_KEYS:
            continue
        if name.endswith("_hash"):
            hashes.add(value)
        elif name.endswith("_hashes") and isinstance(value, list):
            hashes.update(value)
        else:
            other.append((name, str(value)))
    return tuple(sorted(hashes)), tuple(sorted(other))


def fact_cost(fact, artefact_types, bound_hashes, config):
    """(cost, basis) for one assessed cross-evidence fact."""
    identity = fact.get("identity") or {}
    documents = fact.get("document_hashes") or []
    audio = identity.get("audio_hash")
    if any(digest not in bound_hashes for digest in documents) or (audio and audio not in bound_hashes):
        return config["unbound_source_cost"], "unbound_source"
    tiers = set(fact.get("assessed_evidence_tiers") or [])
    for tier in (SIGNED_ATTESTATION, RECORDER_METADATA):
        if tier in tiers:
            return config["tier_costs"][tier], tier
    costs = config["submitter_document_costs"]
    typed = [(costs[artefact_types[digest]], "submitter_document:" + artefact_types[digest])
             for digest in documents if artefact_types.get(digest) in costs]
    if typed:
        return max(typed)
    return config["submitter_document_default"], "submitter_document"


def evidence_strength(cross_facts, artefact_types, bound_hashes, config):
    """Mean over subjects of the strongest assessed fact's forge cost, and the confidence factor."""
    subjects = {}
    for fact in cross_facts:
        if not isinstance(fact.get("passed"), bool):
            continue
        cost, basis = fact_cost(fact, artefact_types, bound_hashes, config)
        key = _subject_key(fact.get("identity") or {})
        if key not in subjects or cost > subjects[key]["forge_cost"]:
            subjects[key] = {"subject": {name: value for name, value in (fact.get("identity") or {}).items()
                                         if name not in _RULE_KEYS},
                             "forge_cost": cost, "basis": basis, "rule": fact.get("rule")}
    scale, floor = config["scale_max"], config["strength_floor"]
    if subjects:
        mean = sum(item["forge_cost"] for item in subjects.values()) / len(subjects)
        basis = "assessed_cross_evidence"
    else:
        # Only the submission's own claims were checked: submitter-level evidence.
        mean, basis = config["submitter_document_default"], "no_cross_evidence"
    by_basis = {}
    for item in subjects.values():
        by_basis[item["basis"]] = by_basis.get(item["basis"], 0) + 1
    return {"applies_to": "integrity_confidence_only", "basis": basis,
            "subject_count": len(subjects), "mean_subject_forge_cost": round(mean, 3),
            "scale_max": scale, "strength_floor": floor,
            "confidence_factor": round(floor + (1 - floor) * mean / scale, 3),
            "subjects_by_basis": dict(sorted(by_basis.items())),
            "subjects": sorted(subjects.values(), key=lambda item: (-item["forge_cost"], str(item["subject"]))),
            "derivation": config.get("derivation")}


def describe(strength, layers, denominator):
    """One sentence for the Integrity reasoning."""
    if strength["basis"] == "no_cross_evidence":
        source = ("no cross-evidence was assessed, so it rests on the submission's own claims "
                  f"(forge cost {strength['mean_subject_forge_cost']:g} of {strength['scale_max']})")
    else:
        parts = ", ".join(f"{count} {name.replace('_', ' ')}"
                          for name, count in strength["subjects_by_basis"].items())
        source = (f"the strongest checked evidence per subject has an average forge cost of "
                  f"{strength['mean_subject_forge_cost']:.2f} of {strength['scale_max']} ({parts})")
    return (f"Integrity confidence reflects how many of the three evidence layers were checked "
            f"({layers}/{denominator}) and how hard the checked evidence would be to forge: {source}, "
            f"a factor of {strength['confidence_factor']:.2f}. Forge cost changes confidence only, never the value. ")
