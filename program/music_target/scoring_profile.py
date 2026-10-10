"""Scoring profile loading and validation.

A scoring profile externalises the weights and thresholds the integrity
axis currently applies as hardcoded constants in ``assessment_passes.py``.
The default profile shipped at ``policies/scoring_profile.json`` is defined
so that *consuming* it changes no existing behaviour: every numeric value
matches today's hardcoded literal bit-for-bit. Changing a value away from
the default is therefore an explicit, reviewable configuration decision,
never a silent behaviour change.

This module intentionally covers only the sections consumed by the current
milestone (M1): integrity layer weights, the withholding-rule thresholds,
and the confidence formula's ceiling/denominator. It does not implement
forge-cost weighting or contradiction-severity caps -- those remain
documented future work, not configuration surfaces here.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from music_target.workflow_policy import _no_duplicates

#: Layer names the integrity aggregation in ``assess_axes`` can weight.
#: Must match the ``"layer"`` values ``_layer()`` assigns to every
#: ``contributes_to_integrity=True`` entry in ``assessment_passes.py``.
INTEGRITY_LAYER_NAMES = (
    "file_integrity",
    "declaration_consistency",
    "content_reconstruction",
    "cross_evidence_support",
)

#: The live integrity architecture has exactly this many contributing
#: layers (see INTEGRITY_LAYER_NAMES). The confidence formula's
#: denominator is not an independent tunable -- it describes a structural
#: fact about assess_axes(), so it is required to match rather than left
#: free, which would otherwise let confidence exceed the schema's [0, 1]
#: bound without any load-time warning.
EXPECTED_CONTRIBUTING_LAYER_COUNT = len(INTEGRITY_LAYER_NAMES)

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[1] / "policies" / "scoring_profile.json"

_PROFILE_KEYS = {"profile_id", "profile_version", "description", "integrity"}
_INTEGRITY_KEYS = {"layer_weights", "layer_weight_rationale", "withholding_rule", "confidence_formula"}
_WITHHOLDING_RULE_KEYS = {"min_components_for_substantive_mean", "require_substantive_support_or_contradiction"}
_CONFIDENCE_FORMULA_KEYS = {"ceiling", "denominator_layers"}


class ScoringProfileError(ValueError):
    pass


def _require_dict(value, where):
    if not isinstance(value, dict):
        raise ScoringProfileError(f"{where} must be an object.")
    return value


def _reject_unknown_keys(mapping, allowed, where):
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ScoringProfileError(f"{where} has unexpected key(s): {', '.join(unknown)}.")


def _reject_non_finite_json_constant(token):
    # json.loads accepts the non-standard tokens NaN/Infinity/-Infinity by
    # default; reject them at parse time, mirroring contract.read_json's
    # precedent for rejecting non-finite JSON numbers.
    raise ScoringProfileError(
        f"Scoring profile contains a non-finite JSON numeric constant ({token!r}); "
        "NaN, Infinity and -Infinity are not permitted.")


def _require_positive_number(value, where):
    if (type(value) not in (int, float) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0):
        raise ScoringProfileError(f"{where} must be a finite positive number.")
    return value


def _require_bounded_number(value, where, minimum=0, maximum=1):
    if (type(value) not in (int, float) or isinstance(value, bool)
            or not math.isfinite(value) or not (minimum <= value <= maximum)):
        raise ScoringProfileError(f"{where} must be a finite number in [{minimum}, {maximum}].")
    return value


def _require_positive_int(value, where):
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise ScoringProfileError(f"{where} must be a positive integer.")
    return value


def _validate_layer_weights(weights):
    _require_dict(weights, "integrity.layer_weights")
    missing = [name for name in INTEGRITY_LAYER_NAMES if name not in weights]
    if missing:
        raise ScoringProfileError(
            "integrity.layer_weights is missing required layer(s): " + ", ".join(missing))
    unknown = [name for name in weights if name not in INTEGRITY_LAYER_NAMES]
    if unknown:
        raise ScoringProfileError(
            "integrity.layer_weights has unknown layer(s): " + ", ".join(sorted(unknown)))
    for name in INTEGRITY_LAYER_NAMES:
        _require_positive_number(weights[name], f"integrity.layer_weights[{name!r}]")


def _validate_layer_weight_rationale(rationale):
    # Optional, documentation-only: a human-readable note per layer
    # explaining why a weight was (or was not) raised above the baseline
    # -- e.g. on forge-cost grounds. Never consumed by scoring logic.
    if rationale is None:
        return
    _require_dict(rationale, "integrity.layer_weight_rationale")
    for name, text in rationale.items():
        if name not in INTEGRITY_LAYER_NAMES:
            raise ScoringProfileError(
                f"integrity.layer_weight_rationale has unknown layer: {name!r}")
        if not isinstance(text, str) or not text:
            raise ScoringProfileError(
                f"integrity.layer_weight_rationale[{name!r}] must be a non-empty string.")


def _validate_withholding_rule(rule):
    _require_dict(rule, "integrity.withholding_rule")
    _reject_unknown_keys(rule, _WITHHOLDING_RULE_KEYS, "integrity.withholding_rule")
    if "min_components_for_substantive_mean" not in rule:
        raise ScoringProfileError(
            "integrity.withholding_rule.min_components_for_substantive_mean is required.")
    _require_positive_int(rule["min_components_for_substantive_mean"],
                          "integrity.withholding_rule.min_components_for_substantive_mean")
    if "require_substantive_support_or_contradiction" not in rule:
        raise ScoringProfileError(
            "integrity.withholding_rule.require_substantive_support_or_contradiction is required.")
    if not isinstance(rule["require_substantive_support_or_contradiction"], bool):
        raise ScoringProfileError(
            "integrity.withholding_rule.require_substantive_support_or_contradiction must be a boolean.")


def _validate_confidence_formula(formula):
    _require_dict(formula, "integrity.confidence_formula")
    _reject_unknown_keys(formula, _CONFIDENCE_FORMULA_KEYS, "integrity.confidence_formula")
    if "ceiling" not in formula:
        raise ScoringProfileError("integrity.confidence_formula.ceiling is required.")
    _require_bounded_number(formula["ceiling"], "integrity.confidence_formula.ceiling", 0, 1)
    if "denominator_layers" not in formula:
        raise ScoringProfileError("integrity.confidence_formula.denominator_layers is required.")
    _require_positive_int(formula["denominator_layers"], "integrity.confidence_formula.denominator_layers")
    # Not an independent tunable: the live architecture has exactly
    # EXPECTED_CONTRIBUTING_LAYER_COUNT contributing layers. A smaller
    # denominator would let confidence exceed the response schema's
    # [0, 1] bound as soon as more than one layer populates -- reject it
    # here, at load time, with a clear explanation, rather than let it
    # surface later as an opaque schema-validation failure.
    if formula["denominator_layers"] != EXPECTED_CONTRIBUTING_LAYER_COUNT:
        raise ScoringProfileError(
            "integrity.confidence_formula.denominator_layers must equal "
            f"{EXPECTED_CONTRIBUTING_LAYER_COUNT} (the live integrity architecture has exactly "
            f"{EXPECTED_CONTRIBUTING_LAYER_COUNT} contributing layers: "
            f"{', '.join(INTEGRITY_LAYER_NAMES)}); got {formula['denominator_layers']!r}.")


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
        profile = json.loads(raw, object_pairs_hook=_no_duplicates,
                             parse_constant=_reject_non_finite_json_constant)
    except ScoringProfileError:
        raise
    except ValueError as exc:
        raise ScoringProfileError(f"Scoring profile at {path} is not valid JSON: {exc}") from exc

    _require_dict(profile, "scoring profile")
    _reject_unknown_keys(profile, _PROFILE_KEYS, "scoring profile")

    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise ScoringProfileError("Scoring profile must have a non-empty string profile_id.")
    if type(profile.get("profile_version")) is not int:
        raise ScoringProfileError("Scoring profile must have an integer profile_version.")

    if "integrity" not in profile:
        raise ScoringProfileError("Scoring profile must contain an 'integrity' section.")
    integrity = _require_dict(profile["integrity"], "integrity")
    _reject_unknown_keys(integrity, _INTEGRITY_KEYS, "integrity")

    if "layer_weights" not in integrity:
        raise ScoringProfileError("integrity.layer_weights is required.")
    _validate_layer_weights(integrity["layer_weights"])
    _validate_layer_weight_rationale(integrity.get("layer_weight_rationale"))

    if "withholding_rule" not in integrity:
        raise ScoringProfileError("integrity.withholding_rule is required.")
    _validate_withholding_rule(integrity["withholding_rule"])

    if "confidence_formula" not in integrity:
        raise ScoringProfileError("integrity.confidence_formula is required.")
    _validate_confidence_formula(integrity["confidence_formula"])

    return profile


def load_default_scoring_profile():
    """Load the scoring profile shipped at ``policies/scoring_profile.json``."""
    return load_scoring_profile(DEFAULT_PROFILE_PATH)
