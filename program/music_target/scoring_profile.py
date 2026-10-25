"""Configurable Integrity layer weighting and severe-contradiction capping.

Externalises the per-layer weights, withholding rule, and confidence
formula that ``assess_axes()`` applies when combining the Integrity axis.
The shipped default-v6 profile retains the existing layer weights and
confidence formula. It aggregates cross-evidence by fact (one vote per
repeated claim) and keeps the release gate for ordinary contradictions; a
severe contradiction releases a value no higher than the cap. Other profiles are explicit,
reviewable configuration choices.

``physical_similarity`` is intentionally not a weightable layer here: it is
policy-disabled (``contributes_to_integrity=False`` in ``assess_axes()``),
and a weight it can never use would be a configuration value with no
effect. Re-admitting it to scoring is a separate decision.

The profile also optionally names a set of check ``rule`` strings that are
treated as severe contradictions: if any of them fails, the weighted mean is
capped at ``severe_contradiction_cap`` instead of being reported as-is. This
is a ceiling, not an override -- it only ever pulls the mean down toward the
cap, never below where it already was. The two keys are optional but must
be given together; a profile that defines neither leaves Integrity as the
plain weighted mean.

``one_vote_per_claim`` (optional, default false) makes the same claim about
one recording, repeated in several documents, a single cross-evidence fact.

``forge_cost`` (optional) grades how hard the checked cross-evidence would be
to forge and scales Integrity confidence by it, never the value (see
``music_target.forge_cost``). Tiers keep their role as the release gate, so
the costs must respect the tier order: signed attestation >= recorder
metadata > every submitter document. A profile without it keeps the
layer-count confidence formula.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

#: Layer names assess_axes() can weight -- must match the "layer" values
#: _layer() assigns to every contributes_to_integrity=True entry.
INTEGRITY_LAYER_NAMES = ("file_integrity", "declaration_consistency", "cross_evidence_support")

#: assess_axes() has exactly this many contributing layers today. The
#: confidence formula's denominator describes that structural fact rather
#: than an independent tunable, so it is checked against this constant
#: instead of accepted freely -- a smaller value would let confidence
#: exceed the response schema's [0, 1] bound as soon as more than one
#: layer populates.
EXPECTED_CONTRIBUTING_LAYER_COUNT = len(INTEGRITY_LAYER_NAMES)

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[1] / "policies" / "scoring_profile.json"

_PROFILE_KEYS = {"profile_id", "profile_version", "description", "integrity"}
_INTEGRITY_KEYS = {"layer_weights", "layer_weight_rationale", "withholding_rule", "confidence_formula",
                   "severe_contradiction_rules", "severe_contradiction_cap", "one_vote_per_claim",
                   "forge_cost"}
_WITHHOLDING_RULE_KEYS = {"min_components_for_substantive_mean", "require_substantive_support_or_contradiction",
                          "substantive_support_tiers", "require_substantive_support",
                          "release_capped_value_on_severe_contradiction", "min_supported_asset_share"}
_CONFIDENCE_FORMULA_KEYS = {"ceiling", "denominator_layers"}
_FORGE_COST_KEYS = {"scale_max", "tier_costs", "submitter_document_costs", "submitter_document_default",
                    "unbound_source_cost", "strength_floor", "derivation"}


class ScoringProfileError(ValueError):
    pass


def _no_duplicates(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ScoringProfileError(f"Duplicate scoring profile key: {key}")
        result[key] = value
    return result


def _positive_number(value, where):
    if (type(value) not in (int, float) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0):
        raise ScoringProfileError(f"{where} must be a finite positive number.")
    return value


def load_scoring_profile(path):
    """Load and validate a scoring profile JSON file.

    Raises ``ScoringProfileError`` on any structural problem -- a malformed
    or incomplete profile is never silently patched or defaulted around,
    since that would mask an intended configuration change.
    """
    path = Path(path)
    try:
        raw = path.read_text()
    except OSError as exc:
        raise ScoringProfileError(f"Could not read scoring profile at {path}: {exc}") from exc
    try:
        profile = json.loads(raw, object_pairs_hook=_no_duplicates)
    except ScoringProfileError:
        raise
    except ValueError as exc:
        raise ScoringProfileError(f"Scoring profile at {path} is not valid JSON: {exc}") from exc

    if not isinstance(profile, dict):
        raise ScoringProfileError("Scoring profile must be an object.")
    unknown = set(profile) - _PROFILE_KEYS
    if unknown:
        raise ScoringProfileError("Scoring profile has unexpected key(s): " + ", ".join(sorted(unknown)))
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"]:
        raise ScoringProfileError("Scoring profile must have a non-empty string profile_id.")
    if type(profile.get("profile_version")) is not int:
        raise ScoringProfileError("Scoring profile must have an integer profile_version.")

    integrity = profile.get("integrity")
    if not isinstance(integrity, dict):
        raise ScoringProfileError("Scoring profile must contain an 'integrity' object.")
    unknown = set(integrity) - _INTEGRITY_KEYS
    if unknown:
        raise ScoringProfileError("integrity has unexpected key(s): " + ", ".join(sorted(unknown)))

    weights = integrity.get("layer_weights")
    if not isinstance(weights, dict):
        raise ScoringProfileError("integrity.layer_weights must be an object.")
    missing = [name for name in INTEGRITY_LAYER_NAMES if name not in weights]
    if missing:
        raise ScoringProfileError(
            "integrity.layer_weights is missing required layer(s): " + ", ".join(missing))
    unknown = [name for name in weights if name not in INTEGRITY_LAYER_NAMES]
    if unknown:
        raise ScoringProfileError(
            "integrity.layer_weights has unknown layer(s): " + ", ".join(sorted(unknown)))
    for name in INTEGRITY_LAYER_NAMES:
        _positive_number(weights[name], f"integrity.layer_weights[{name!r}]")

    # Optional, documentation-only: a human-readable note per layer
    # explaining why a weight was (or was not) raised above the baseline --
    # e.g. on forge-cost grounds. Never consumed by scoring logic.
    rationale = integrity.get("layer_weight_rationale")
    if rationale is not None:
        if not isinstance(rationale, dict):
            raise ScoringProfileError("integrity.layer_weight_rationale must be an object.")
        unknown = set(rationale) - set(INTEGRITY_LAYER_NAMES)
        if unknown:
            raise ScoringProfileError(
                "integrity.layer_weight_rationale has unknown layer(s): " + ", ".join(sorted(unknown)))
        for name, text in rationale.items():
            if not isinstance(text, str) or not text:
                raise ScoringProfileError(
                    f"integrity.layer_weight_rationale[{name!r}] must be a non-empty string.")

    rule = integrity.get("withholding_rule")
    if not isinstance(rule, dict):
        raise ScoringProfileError("integrity.withholding_rule must be an object.")
    unknown = set(rule) - _WITHHOLDING_RULE_KEYS
    if unknown:
        raise ScoringProfileError(
            "integrity.withholding_rule has unexpected key(s): " + ", ".join(sorted(unknown)))
    minimum = rule.get("min_components_for_substantive_mean")
    if type(minimum) is not int or isinstance(minimum, bool) or minimum < 1:
        raise ScoringProfileError(
            "integrity.withholding_rule.min_components_for_substantive_mean must be a positive integer.")
    support_keys = [key for key in ("require_substantive_support", "require_substantive_support_or_contradiction")
                    if key in rule]
    if len(support_keys) != 1 or not isinstance(rule[support_keys[0]], bool):
        raise ScoringProfileError(
            "withholding_rule requires exactly one boolean require_substantive_support "
            "(legacy require_substantive_support_or_contradiction is accepted as an alias).")
    # Legacy key spelling is readable for existing profiles, but no longer
    # enables a contradiction-only exception to numeric release.
    # Optional and off when absent, so earlier profiles reproduce their results.
    if not isinstance(rule.get("release_capped_value_on_severe_contradiction", False), bool):
        raise ScoringProfileError(
            "integrity.withholding_rule.release_capped_value_on_severe_contradiction must be a boolean.")
    # Optional and off when absent: the share of the workflow's recordings that
    # must have qualifying evidence checked (supported or contradicted).
    share = rule.get("min_supported_asset_share")
    if share is not None and (type(share) not in (int, float) or isinstance(share, bool)
                              or not math.isfinite(share) or not (0 < share <= 1)):
        raise ScoringProfileError(
            "integrity.withholding_rule.min_supported_asset_share must be a number in (0, 1].")
    # Optional: which evidence tiers count as substantive support. Absent means
    # every assessed cross-evidence check counts (the original rule).
    tiers = rule.get("substantive_support_tiers")
    if tiers is not None:
        from music_target.evidence_tiers import TIERS
        if (not isinstance(tiers, list) or not tiers or len(set(tiers)) != len(tiers)
                or any(item not in TIERS for item in tiers)):
            raise ScoringProfileError(
                "integrity.withholding_rule.substantive_support_tiers must be a non-empty list of distinct "
                "tiers from: " + ", ".join(TIERS) + ".")

    formula = integrity.get("confidence_formula")
    if not isinstance(formula, dict):
        raise ScoringProfileError("integrity.confidence_formula must be an object.")
    unknown = set(formula) - _CONFIDENCE_FORMULA_KEYS
    if unknown:
        raise ScoringProfileError(
            "integrity.confidence_formula has unexpected key(s): " + ", ".join(sorted(unknown)))
    ceiling = formula.get("ceiling")
    if (type(ceiling) not in (int, float) or isinstance(ceiling, bool)
            or not math.isfinite(ceiling) or not (0 <= ceiling <= 1)):
        raise ScoringProfileError("integrity.confidence_formula.ceiling must be a finite number in [0, 1].")
    denominator = formula.get("denominator_layers")
    # Not an independent tunable: assess_axes() has exactly
    # EXPECTED_CONTRIBUTING_LAYER_COUNT contributing layers. Reject a
    # mismatched value here, at load time, rather than let it surface later
    # as an opaque schema-validation failure.
    if denominator != EXPECTED_CONTRIBUTING_LAYER_COUNT:
        raise ScoringProfileError(
            "integrity.confidence_formula.denominator_layers must equal "
            f"{EXPECTED_CONTRIBUTING_LAYER_COUNT} (assess_axes() has exactly "
            f"{EXPECTED_CONTRIBUTING_LAYER_COUNT} contributing layers: "
            f"{', '.join(INTEGRITY_LAYER_NAMES)}); got {denominator!r}.")

    rules = integrity.get("severe_contradiction_rules")
    cap = integrity.get("severe_contradiction_cap")
    if (rules is None) != (cap is None):
        raise ScoringProfileError(
            "integrity.severe_contradiction_rules and integrity.severe_contradiction_cap "
            "must be given together -- each is meaningless without the other.")
    if rules is not None:
        if not isinstance(rules, list) or not rules:
            raise ScoringProfileError(
                "integrity.severe_contradiction_rules must be a non-empty list of strings.")
        if any(not isinstance(item, str) or not item for item in rules):
            raise ScoringProfileError(
                "integrity.severe_contradiction_rules must contain only non-empty strings.")
        if len(rules) != len(set(rules)):
            raise ScoringProfileError("integrity.severe_contradiction_rules must not contain duplicates.")
        if (type(cap) not in (int, float) or isinstance(cap, bool)
                or not math.isfinite(cap) or not (0 <= cap <= 1)):
            raise ScoringProfileError(
                "integrity.severe_contradiction_cap must be a finite number in [0, 1].")

    if not isinstance(integrity.get("one_vote_per_claim", False), bool):
        raise ScoringProfileError("integrity.one_vote_per_claim must be a boolean.")

    if "forge_cost" in integrity:
        _check_forge_cost(integrity["forge_cost"])

    return profile


def _check_forge_cost(config):
    if not isinstance(config, dict):
        raise ScoringProfileError("integrity.forge_cost must be an object.")
    unknown = set(config) - _FORGE_COST_KEYS
    missing = _FORGE_COST_KEYS - {"derivation"} - set(config)
    if unknown or missing:
        raise ScoringProfileError("integrity.forge_cost has unexpected or missing key(s): "
                                  + ", ".join(sorted(unknown | missing)))
    scale = config["scale_max"]
    if type(scale) is not int or scale < 1:
        raise ScoringProfileError("integrity.forge_cost.scale_max must be a positive integer.")

    def cost(value, where):
        if type(value) is not int or not 1 <= value <= scale:
            raise ScoringProfileError(f"integrity.forge_cost.{where} must be an integer from 1 to {scale}.")
        return value

    from music_target.evidence_tiers import RECORDER_METADATA, SIGNED_ATTESTATION
    tiers = config["tier_costs"]
    if not isinstance(tiers, dict) or set(tiers) != {SIGNED_ATTESTATION, RECORDER_METADATA}:
        raise ScoringProfileError(
            f"integrity.forge_cost.tier_costs must name exactly {SIGNED_ATTESTATION} and {RECORDER_METADATA}.")
    documents = config["submitter_document_costs"]
    if not isinstance(documents, dict) or any(not isinstance(name, str) or not name for name in documents):
        raise ScoringProfileError("integrity.forge_cost.submitter_document_costs must map artefact types to costs.")
    submitter = [cost(value, f"submitter_document_costs[{name!r}]") for name, value in documents.items()]
    submitter.append(cost(config["submitter_document_default"], "submitter_document_default"))
    signed = cost(tiers[SIGNED_ATTESTATION], "tier_costs.signed_attestation")
    recorder = cost(tiers[RECORDER_METADATA], "tier_costs.recorder_metadata")
    # Forge cost grades evidence within the tiers; it must not reorder them.
    if not signed >= recorder > max(submitter):
        raise ScoringProfileError("integrity.forge_cost must keep the tier order: signed_attestation >= "
                                  "recorder_metadata > every submitter document cost.")
    if cost(config["unbound_source_cost"], "unbound_source_cost") > config["submitter_document_default"]:
        raise ScoringProfileError("integrity.forge_cost.unbound_source_cost must not exceed submitter_document_default.")
    floor = config["strength_floor"]
    if type(floor) not in (int, float) or isinstance(floor, bool) or not math.isfinite(floor) or not 0 <= floor < 1:
        raise ScoringProfileError("integrity.forge_cost.strength_floor must be a number in [0, 1).")
    if "derivation" in config and (not isinstance(config["derivation"], str) or not config["derivation"]):
        raise ScoringProfileError("integrity.forge_cost.derivation must be a non-empty string.")


def load_default_scoring_profile():
    """Load the scoring profile shipped at ``policies/scoring_profile.json``."""
    return load_scoring_profile(DEFAULT_PROFILE_PATH)
