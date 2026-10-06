#!/usr/bin/env python3
"""Ben-compatible four-axis music evidence profile.

The profile consumes bundles produced by ``bundle_builder.py`` (or bundles
normalized directly by ``relationship_extractor.py``) and uses the types
registered by ``music_evidence_types.py``.  It plugs concrete
passes and a score merger into Ben's ``Pipeline`` while preserving a crucial
distinction:

* ``value`` says what the checks found;
* ``confidence`` says how much evidence supported that result;
* ``unavailable`` means not assessed and always has null value/confidence.

Integrity is not creator honesty and it is not the number of files submitted.
It measures contradictions in the portions that can actually be checked.
Forge cost affects confidence, never truth by itself.

Run:
    python3 music_profiles.py bundle.json --objects-dir objects -o assessment.json
    python3 music_profiles.py --show-profile
    python3 music_profiles.py --self-test
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import wave

# Audio-content comparison (stem-vs-mix similarity) is an optional signal:
# most of this module works fine without it, so a missing install must not
# crash the whole pipeline -- it should just leave that one signal
# unassessed, the same way c2pa-python is optional for provenance parsing.
try:
    import numpy as _np
    import librosa as _librosa
    from scipy.signal import fftconvolve as _fftconvolve
    STEM_MATCH_AVAILABLE = True
    _STEM_MATCH_IMPORT_ERROR: Optional[str] = None
except ImportError as _exc:  # pragma: no cover - only hit without optional deps
    _np = None
    _librosa = None
    _fftconvolve = None
    STEM_MATCH_AVAILABLE = False
    _STEM_MATCH_IMPORT_ERROR = str(_exc)


class ProfileError(ValueError):
    """The profile or supplied evidence cannot be evaluated safely."""


def _support_candidates(filename: str) -> List[Path]:
    configured = os.environ.get("MUSIC_PROFILE_SUPPORT_DIR")
    result = []
    if configured:
        result.append(Path(configured).expanduser() / filename)
    here = Path(__file__).resolve().parent
    result.extend([
        here / filename,
        here / "COMP3888" / "COMP3888_M17_ZL_01_P44" / filename,
    ])
    return result


def _load_support_module(name: str):
    path = next((p for p in _support_candidates(name + ".py") if p.is_file()), None)
    if path is None:
        raise ProfileError(
            f"{name}.py must be beside this file, or MUSIC_PROFILE_SUPPORT_DIR "
            "must point to its directory")
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ProfileError(f"could not load support module: {path}")
    module = importlib.util.module_from_spec(spec)
    # Dynamic modules containing dataclasses must be registered before exec.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


MUSIC_TYPES = _load_support_module("music_evidence_types")
RELATIONSHIPS = _load_support_module("relationship_extractor")
WAV_PARSER = _load_support_module("wav_parser")
C2PA_PARSER = _load_support_module("c2pa_parser")
MUSIC_TYPES.register_all()

from libevchain.pipeline import ArtefactPass, EvidencePass, Pipeline  # noqa: E402
from libevchain.score import Score, Unassessed  # noqa: E402
from libevchain.types import ALL_ARTEFACTS, ALL_RELATIONSHIPS  # noqa: E402


def _read_json(path: Path) -> Any:
    def reject_duplicate_keys(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProfileError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=reject_duplicate_keys)
    except OSError as exc:
        raise ProfileError(f"could not read {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise ProfileError(
            f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None


def _load_profile() -> dict:
    configured = os.environ.get("MUSIC_PROFILE_PATH")
    here = Path(__file__).resolve().parent
    candidates = ([Path(configured).expanduser()] if configured else []) + [
        here / "music_profile.json",
        here / "COMP3888" / "COMP3888_M17_ZL_01_P44" / "music_profile.json",
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise ProfileError(
            "music_profile.json must be beside this file, or MUSIC_PROFILE_PATH "
            "must name it")
    value = _read_json(path)
    required = {
        "profile_id", "profile_version", "axis_semantics", "forge_cost_scale",
        "artefact_forge_cost", "relationship_origin", "expectations", "integrity",
        "attestation", "ai_disclosure",
    }
    if not isinstance(value, dict) or required - set(value):
        raise ProfileError(
            f"profile is missing fields: {sorted(required - set(value or {}))}")
    return value


PROFILE = _load_profile()


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _score(integrity: Any = Unassessed, completeness: Any = Unassessed,
           attestation: Any = Unassessed,
           ai_disclosure: Any = Unassessed) -> Score:
    """Build a Ben Score without turning an unassessed axis into zero."""
    def normalized(value: Any) -> Any:
        return Unassessed if value is Unassessed else _clamp(value)

    return Score(
        integrity=normalized(integrity),
        completeness=normalized(completeness),
        attestation_strength=normalized(attestation),
        ai_disclosure=normalized(ai_disclosure),
    )


def _type_name(artefact) -> str:
    return artefact.artefact_type.artefact_type_name


def _attributes(artefact) -> Mapping[str, Any]:
    value = getattr(artefact, "_attributes", {})
    return value if isinstance(value, dict) else {}


def _group(artefact, name: str) -> Mapping[str, Any]:
    value = _attributes(artefact).get(name, {})
    return value if isinstance(value, dict) else {}


def _technical(artefact) -> Mapping[str, Any]:
    return _group(artefact, "technical")


def _forge_cost_for(artefact) -> int:
    return int(PROFILE["artefact_forge_cost"].get(_type_name(artefact), 1))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact(values: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _bound_wav_attributes(path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Reparse a bound WAV so assessment does not trust stored observations."""
    parsed = WAV_PARSER.parse_wav_file(str(path))
    warnings = list(parsed.warnings)
    core = parsed.core_format
    if core is None:
        return None, warnings

    technical = _compact({
        "media_type": "audio/wav",
        "duration_seconds": core.duration_seconds,
        "sample_rate_hz": core.sample_rate_hz,
        "channels": core.num_channels,
        "bit_depth": core.bits_per_sample,
        "format": "WAV",
        "codec_subtype": core.audio_format,
        "file_size_bytes": path.stat().st_size,
    })
    source_metadata: Dict[str, Any] = {"original_filename": path.name}
    bext = parsed.broadcast_extension
    if bext is not None:
        source_metadata.update(_compact({
            "description": bext.description,
            "originator": bext.originator,
            "originator_reference": bext.originator_reference,
            "origination_date": bext.origination_date,
            "origination_time": bext.origination_time,
            "time_reference_samples": bext.time_reference,
            "umid": bext.umid,
            "coding_history": bext.coding_history,
        }))
    for key in ("software", "artist", "engineer", "comment"):
        value = parsed.info.get(key)
        if value is not None:
            source_metadata[key] = value
    return {
        "technical": technical,
        "source_metadata": source_metadata,
    }, warnings


def _stem_load_audio(path: Path, sample_rate_hz: int) -> "_np.ndarray":
    y, _ = _librosa.load(str(path), sr=sample_rate_hz, mono=True)
    return y


def _stem_mel_db(y: "_np.ndarray", sample_rate_hz: int, hop_length: int) -> "_np.ndarray":
    S = _librosa.feature.melspectrogram(y=y, sr=sample_rate_hz, n_mels=64, hop_length=hop_length)
    return _librosa.power_to_db(S, ref=1.0)


def _stem_find_best_offset(song_y: "_np.ndarray", stem_y: "_np.ndarray",
                           sample_rate_hz: int, max_offset_seconds: float) -> float:
    """Small-offset cross-correlation of amplitude envelopes.

    Finds lead-in/lead-out padding differences between a raw stem export and
    the final bounce. Deliberately does not search pitch shifts or tempo
    ratios: a stem from the same session as the mix should already share its
    pitch and tempo, unlike an excerpted/pitched sample.
    """
    hop = 256
    song_env = _librosa.feature.rms(y=song_y, hop_length=hop)[0]
    stem_env = _librosa.feature.rms(y=stem_y, hop_length=hop)[0]
    song_env = song_env - song_env.mean()
    stem_env = stem_env - stem_env.mean()

    corr = _fftconvolve(song_env, stem_env[::-1], mode="full")
    zero_idx = len(stem_env) - 1
    max_shift_frames = int(max_offset_seconds * sample_rate_hz / hop)
    lo = max(0, zero_idx - max_shift_frames)
    hi = min(len(corr), zero_idx + max_shift_frames + 1)
    window = corr[lo:hi]
    best_rel = int(_np.argmax(window)) + lo - zero_idx
    return best_rel * hop / sample_rate_hz


def _stem_similarity(stem_path: Path, mix_path: Path, config: Mapping[str, Any]) -> Dict[str, Any]:
    """Check how much of a stem's audible content correlates with a mix.

    Adapted from the standalone stem_match.py prototype. Returns the raw
    diagnostics (offset, correlations, active fraction); the caller decides
    pass/fail against its own threshold.
    """
    sample_rate_hz = int(config.get("sample_rate_hz", 22050))
    hop_length = int(config.get("hop_length", 1024))
    max_offset_seconds = float(config.get("max_offset_seconds", 5.0))
    silence_db = float(config.get("silence_db", -45.0))

    song_y = _stem_load_audio(mix_path, sample_rate_hz)
    stem_y = _stem_load_audio(stem_path, sample_rate_hz)

    offset_sec = _stem_find_best_offset(song_y, stem_y, sample_rate_hz, max_offset_seconds)
    offset_samples = int(round(offset_sec * sample_rate_hz))

    if offset_samples >= 0:
        song_aligned = song_y[offset_samples:offset_samples + len(stem_y)]
        stem_aligned = stem_y[:len(song_aligned)]
    else:
        stem_aligned = stem_y[-offset_samples:]
        song_aligned = song_y[:len(stem_aligned)]
        stem_aligned = stem_aligned[:len(song_aligned)]

    n = min(len(song_aligned), len(stem_aligned))
    song_aligned, stem_aligned = song_aligned[:n], stem_aligned[:n]
    if n < sample_rate_hz:  # less than 1 second of overlap -- nothing meaningful to say
        return {
            "offset_seconds": offset_sec,
            "overall_correlation": 0.0,
            "active_correlation": 0.0,
            "active_fraction": 0.0,
            "insufficient_overlap": True,
        }

    mel_song = _stem_mel_db(song_aligned, sample_rate_hz, hop_length)
    mel_stem = _stem_mel_db(stem_aligned, sample_rate_hz, hop_length)
    m = min(mel_song.shape[1], mel_stem.shape[1])
    mel_song, mel_stem = mel_song[:, :m], mel_stem[:, :m]

    stem_rms_db = _librosa.power_to_db(
        _librosa.feature.rms(y=stem_aligned, hop_length=hop_length)[0] ** 2, ref=1.0
    )[:m]
    active_mask = stem_rms_db > silence_db
    active_fraction = float(active_mask.mean()) if m > 0 else 0.0

    a = mel_song - mel_song.mean(axis=0, keepdims=True)
    b = mel_stem - mel_stem.mean(axis=0, keepdims=True)
    num = (a * b).sum(axis=0)
    den = _np.linalg.norm(a, axis=0) * _np.linalg.norm(b, axis=0) + 1e-9
    frame_sims01 = (_np.clip(num / den, -1.0, 1.0) + 1.0) / 2.0

    overall_correlation = float(frame_sims01.mean())
    active_correlation = (
        float(frame_sims01[active_mask].mean()) if active_mask.any() else 0.0
    )

    return {
        "offset_seconds": offset_sec,
        "overall_correlation": overall_correlation,
        "active_correlation": active_correlation,
        "active_fraction": active_fraction,
        "insufficient_overlap": False,
    }


def _values_match(field: str, left: Any, right: Any,
                  sample_rate_hz: Optional[int] = None) -> bool:
    if type(left) is int and type(right) is int:
        return left == right
    if type(left) in (int, float) and type(right) in (int, float):
        if field == "duration_seconds" and sample_rate_hz:
            tolerance = max(1e-6, 1.0 / sample_rate_hz)
        else:
            scale = max(1.0, abs(float(left)), abs(float(right)))
            tolerance = 1e-6 * scale
        return abs(float(left) - float(right)) <= tolerance
    return left == right


class BoundFileIntegrityPass(ArtefactPass):
    """Check that a bound object matches its content-addressed graph node.

    This proves file-to-bundle consistency.  It does not authenticate the
    creator because an attacker controlling both bytes and bundle can recompute
    the hash.
    """

    @staticmethod
    def evaluate(artefact):
        path_value = artefact.get_file()
        if not path_value:
            return _score(), {
                "kind": "bound-file-integrity",
                "assessed": False,
                "reason": f"{artefact.artefact_hash}: object file was not bound",
            }, ["Object file unavailable; hash consistency was not assessed"]
        path = Path(path_value)
        try:
            observed = _hash_file(path)
        except OSError as exc:
            return _score(), {
                "kind": "bound-file-integrity",
                "assessed": False,
                "reason": f"could not read {path}: {exc}",
            }, [f"Object file could not be read: {exc}"]
        matched = observed == artefact.artefact_hash
        checks = [{
            "rule": "bound_file_sha256_matches",
            "passed": matched,
            "declared": artefact.artefact_hash,
            "observed": observed,
        }]

        # Reparse audio during assessment. This prevents a hand-written bundle
        # from presenting invented ``technical`` values as parser observations.
        if _type_name(artefact).startswith("audio/"):
            try:
                live_attributes, parser_warnings = _bound_wav_attributes(path)
            except OSError as exc:
                live_attributes, parser_warnings = None, [str(exc)]
            parseable = live_attributes is not None and not parser_warnings
            checks.append({
                "rule": "bound_audio_wav_parses_without_warnings",
                "passed": parseable,
                "declared": "valid WAV without parser warnings",
                "observed": ("valid WAV" if parseable else
                             parser_warnings or "missing WAV fmt chunk"),
            })

            if live_attributes is not None:
                sample_rate = live_attributes["technical"].get("sample_rate_hz")
                comparisons = (
                    ("technical", "technical", "stored"),
                    ("source_metadata", "source_metadata", "stored"),
                    ("declared_technical", "technical", "declared"),
                    ("declared_source_metadata", "source_metadata", "declared"),
                )
                for left_name, live_name, meaning in comparisons:
                    left_group = _group(artefact, left_name)
                    live_group = live_attributes[live_name]
                    for field, left_value in left_group.items():
                        # An upload basename may legitimately change in transit;
                        # it is not an embedded WAV fact.
                        if live_name == "source_metadata" and field == "original_filename":
                            continue
                        if left_value is None or field not in live_group:
                            continue
                        live_value = live_group[field]
                        if live_value is None:
                            continue
                        passed = _values_match(
                            field, left_value, live_value, sample_rate)
                        rule_suffix = ("matches_bound_wav" if meaning == "stored"
                                       else "matches_observed")
                        checks.append({
                            "rule": f"{left_name}.{field}_{rule_suffix}",
                            "passed": passed,
                            meaning: left_value,
                            "observed": live_value,
                        })

        failures = [item for item in checks if not item["passed"]]
        value = sum(1 for item in checks if item["passed"]) / len(checks)
        forge_cost = _forge_cost_for(artefact)
        # Hash binding is a precise consistency test but weak authentication.
        confidence = _clamp(0.2 + 0.08 * forge_cost)
        if failures:
            reasoning = (
                f"{len(checks) - len(failures)}/{len(checks)} file, parser and "
                "declaration consistency checks passed; contradictions: "
                + ", ".join(item["rule"] for item in failures)
            )
        else:
            reasoning = (
                f"{len(checks)}/{len(checks)} file, parser and declaration "
                f"consistency checks passed for {path.name}"
            )
        return _score(integrity=value), {
            "kind": "bound-file-integrity",
            "assessed": True,
            "value": _clamp(value),
            "confidence": confidence,
            "forge_cost": forge_cost,
            "contradiction": bool(failures),
            "checks": checks,
            "artefact_hash": artefact.artefact_hash,
            "reason": reasoning,
        }, [reasoning]


def _append_check(checks: List[Tuple[str, bool]], name: str,
                  left: Any, right: Any) -> None:
    if left is not None and right is not None:
        checks.append((name, left == right))


class RelationshipIntegrityPass(EvidencePass):
    """Check endpoint legality and concrete, cross-artefact claim parameters."""

    @staticmethod
    def evaluate(evidence):
        source = evidence.get_evidence_artefact()
        target = evidence.get_result_artefact()
        relationship = evidence.relationship_type
        relationship_name = relationship.relationship_type_name
        attrs = getattr(evidence, "_attributes", {})
        checks: List[Tuple[str, bool]] = []

        checks.append((
            "relationship_endpoints_valid",
            bool(relationship.validate_types(target.artefact_type, source.artefact_type)),
        ))
        try:
            relationship.validate_attributes(attrs)
            checks.append(("relationship_attributes_valid", True))
        except Exception:
            checks.append(("relationship_attributes_valid", False))

        origin = attrs.get("assertion_origin", "submitter")
        machine_binding = getattr(evidence, "_machine_origin_binding", None)
        if origin in RELATIONSHIPS.MACHINE_READABLE_ORIGINS:
            checks.append((
                "machine_origin_asserting_file_hash_bound",
                bool(machine_binding and machine_binding.get("verified")),
            ))

        source_start = attrs.get("source_start_seconds")
        source_end = attrs.get("source_end_seconds")
        target_start = attrs.get("target_start_seconds")
        target_end = attrs.get("target_end_seconds")
        if source_start is not None and source_end is not None:
            checks.append(("source_time_order_valid", source_end >= source_start))
        if target_start is not None and target_end is not None:
            checks.append(("target_time_order_valid", target_end >= target_start))
        source_duration = _technical(source).get("duration_seconds")
        target_duration = _technical(target).get("duration_seconds")
        if source_end is not None and source_duration is not None:
            checks.append(("source_time_within_duration", source_end <= source_duration))
        if target_end is not None and target_duration is not None:
            checks.append(("target_time_within_duration", target_end <= target_duration))
        if (source_start is not None and source_end is not None
                and target_start is not None and target_duration is not None):
            excerpt_length = source_end - source_start
            checks.append(("excerpt_fits_target", target_start + excerpt_length <= target_duration))

        source_tech, target_tech = _technical(source), _technical(target)
        _append_check(checks, "input_sample_rate_matches_source",
                      attrs.get("input_sample_rate_hz"), source_tech.get("sample_rate_hz"))
        _append_check(checks, "output_sample_rate_matches_target",
                      attrs.get("output_sample_rate_hz"), target_tech.get("sample_rate_hz"))
        _append_check(checks, "input_bit_depth_matches_source",
                      attrs.get("input_bit_depth"), source_tech.get("bit_depth"))
        _append_check(checks, "output_bit_depth_matches_target",
                      attrs.get("output_bit_depth"), target_tech.get("bit_depth"))
        _append_check(checks, "source_format_matches_source",
                      attrs.get("source_format"), source_tech.get("format"))
        _append_check(checks, "target_format_matches_target",
                      attrs.get("target_format"), target_tech.get("format"))

        # Audio-content correlation: is the stem's audible content actually
        # present in the target mix? Unlike the checks above, this compares
        # waveforms rather than declared/observed metadata, so it is kept out
        # of `checks` unless it can actually be computed, and its raw
        # diagnostics are always reported separately even when a pass/fail
        # verdict isn't possible (e.g. missing dependency or unbound files).
        stem_similarity: Optional[Dict[str, Any]] = None
        stem_config = PROFILE.get("stem_similarity")
        if (stem_config and relationship_name in stem_config.get("applicable_relationship_types", ())
                and _type_name(source).startswith("audio/")
                and _type_name(target).startswith("audio/")):
            source_path, target_path = source.get_file(), target.get_file()
            if not source_path or not target_path:
                stem_similarity = {
                    "assessed": False,
                    "reason": "stem or target audio file was not bound; content was not compared",
                }
            elif not STEM_MATCH_AVAILABLE:
                stem_similarity = {
                    "assessed": False,
                    "reason": ("librosa/numpy/scipy not installed "
                               f"({_STEM_MATCH_IMPORT_ERROR}); audio content was not compared"),
                }
            else:
                try:
                    diagnostics = _stem_similarity(Path(source_path), Path(target_path), stem_config)
                except Exception as exc:
                    stem_similarity = {
                        "assessed": False,
                        "reason": f"stem similarity check failed: {exc}",
                    }
                else:
                    threshold = float(stem_config.get("active_correlation_threshold", 0.65))
                    passed = (not diagnostics["insufficient_overlap"]
                              and diagnostics["active_correlation"] >= threshold)
                    stem_similarity = {
                        "assessed": True,
                        "threshold": threshold,
                        "passed": passed,
                        **diagnostics,
                    }
                    checks.append(("stem_audio_content_present_in_target", passed))

        failures = [name for name, passed in checks if not passed]
        value = sum(1 for _, passed in checks if passed) / len(checks)
        machine_origin_verified = bool(
            machine_binding and machine_binding.get("verified"))
        effective_origin = (
            origin if (origin not in RELATIONSHIPS.MACHINE_READABLE_ORIGINS
                       or machine_origin_verified)
            else "submitter"
        )
        origin_spec = PROFILE["relationship_origin"][effective_origin]
        confidence = float(origin_spec["base_confidence"])
        # Concrete cross-file parameters make a claim more checkable. A creator
        # confirmation is provenance for the claim, but never verification.
        if len(checks) > 2:
            confidence += float(PROFILE["integrity"]["relationship_parameter_bonus"])
        if attrs.get("confirmed_by_submitter"):
            confidence += float(PROFILE["integrity"]["submitter_confirmation_bonus"])
        confidence = min(
            confidence,
            float(PROFILE["integrity"]["maximum_claim_only_confidence"]),
        )
        confidence = _clamp(confidence)
        reason = (f"{relationship_name}: {len(checks) - len(failures)}/{len(checks)} "
                  f"checkable consistency rules passed; claim origin={origin}")
        if origin != effective_origin:
            binding_reason = (
                machine_binding.get("reason") if machine_binding
                else "asserting artefact file was not verified"
            )
            reason += (
                "; machine-origin confidence/forge cost downgraded to submitter: "
                f"{binding_reason}"
            )
        if failures:
            reason += "; contradictions: " + ", ".join(failures)
        elif len(checks) == 2:
            reason += "; no relationship-specific parameter could be cross-checked"
        result = {
            "kind": "relationship-integrity",
            "assessed": True,
            "value": _clamp(value),
            "confidence": confidence,
            "forge_cost": int(origin_spec["forge_cost"]),
            "declared_origin": origin,
            "effective_origin": effective_origin,
            "machine_origin_binding": machine_binding,
            "contradiction": bool(failures),
            "checks": [{"rule": name, "passed": passed} for name, passed in checks],
            "reason": reason,
            "target_hash": target.artefact_hash,
            "source_hash": source.artefact_hash,
            "relationship_type": relationship_name,
        }
        if stem_similarity is not None:
            result["stem_similarity"] = stem_similarity
        return _score(integrity=value), result, [reason]


def _recursive_lookup(value: Any, wanted: Iterable[str]) -> Optional[bool]:
    wanted_normalized = {item.lower().replace("-", "_") for item in wanted}
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in wanted_normalized and isinstance(item, bool):
                return item
            nested = _recursive_lookup(item, wanted)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _recursive_lookup(item, wanted)
            if nested is not None:
                return nested
    return None


def _check_status(value: Optional[bool], *, present: Optional[bool] = None) -> str:
    """Render a verifier result without turning missing data into a pass."""
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    if present is True:
        return "present_unverified"
    if present is False:
        return "absent"
    return "unknown"


def _c2pa_verification_checks(
        *, observed_hash: str, declared_hash: str, signature_info: Any,
        validation_status: Any) -> Tuple[dict, Optional[bool], Optional[bool]]:
    """Expose individual C2PA checks instead of one ambiguous trust flag.

    Only explicit verifier-owned boolean fields are interpreted.  Generic
    ``valid``/``trusted`` fields are deliberately ignored because they can be
    submitter-shaped data and do not identify what was actually validated.
    """
    crypto_valid = _recursive_lookup(
        validation_status, ("cryptographically_valid", "signature_valid"))
    certificate_chain_valid = _recursive_lookup(
        validation_status,
        ("certificate_chain_valid", "credential_chain_valid", "certificate_valid"),
    )
    not_revoked = _recursive_lookup(
        validation_status, ("not_revoked", "revocation_valid"))
    if not_revoked is None:
        revoked = _recursive_lookup(validation_status, ("revoked",))
        if revoked is not None:
            not_revoked = not revoked
    credential_trusted = _recursive_lookup(
        validation_status, ("credential_trusted", "trust_valid"))
    timestamp_valid = _recursive_lookup(
        validation_status, ("timestamp_valid", "time_stamp_valid"))
    signer_identity_valid = _recursive_lookup(
        validation_status,
        ("signer_identity_valid", "signer_verified", "identity_verified"),
    )

    signature_present = signature_info is not None
    timestamp_present = bool(getattr(signature_info, "time", None))
    identity_present = bool(getattr(signature_info, "issuer", None))
    return ({
        "content_binding": {
            "status": "pass",
            "declared_sha256": declared_hash,
            "observed_sha256": observed_hash,
        },
        "signature": {
            "status": _check_status(crypto_valid, present=signature_present),
            "present": signature_present,
        },
        "certificate_chain": {
            "status": _check_status(certificate_chain_valid),
        },
        "revocation": {
            "status": _check_status(not_revoked),
        },
        "trust_list": {
            "status": _check_status(credential_trusted),
        },
        "timestamp": {
            "status": _check_status(timestamp_valid, present=timestamp_present),
            "present": timestamp_present,
        },
        "signer_identity": {
            "status": _check_status(signer_identity_valid, present=identity_present),
            "present": identity_present,
        },
    }, crypto_valid, credential_trusted)


class AttestationSignalPass(ArtefactPass):
    """Re-read C2PA from the hash-bound file; never score stored claims alone."""

    @staticmethod
    def evaluate(artefact):
        stored_provenance = _group(artefact, "provenance")
        if not stored_provenance:
            return _score(), {"kind": "attestation", "assessed": False}, []

        path_value = artefact.get_file()
        if not path_value:
            reason = (
                "Stored C2PA metadata was ignored because the corresponding "
                "content-addressed file was not bound"
            )
            return _score(), {
                "kind": "attestation",
                "assessed": False,
                "verification_attempted": False,
                "verification_state": "file_unbound",
                "reason": reason,
            }, [reason]

        path = Path(path_value)
        try:
            observed_hash = _hash_file(path)
        except OSError as exc:
            reason = f"Could not read the attested file: {exc}"
            return _score(), {
                "kind": "attestation",
                "assessed": False,
                "verification_attempted": True,
                "verification_state": "file_read_error",
                "error": True,
                "reason": reason,
            }, [reason]
        if observed_hash != artefact.artefact_hash:
            reason = "C2PA verification was blocked because the bound file hash mismatched"
            return _score(attestation=0.0), {
                "kind": "attestation",
                "assessed": True,
                "verification_attempted": True,
                "verification_state": "content_binding_failed",
                "critical_failure": True,
                "value": 0.0,
                "confidence": 1.0,
                "declared_hash": artefact.artefact_hash,
                "observed_hash": observed_hash,
                "verification_checks": {
                    "content_binding": {
                        "status": "fail",
                        "declared_sha256": artefact.artefact_hash,
                        "observed_sha256": observed_hash,
                    },
                    **{name: {"status": "not_run"} for name in (
                        "signature", "certificate_chain", "revocation",
                        "trust_list", "timestamp", "signer_identity",
                    )},
                },
                "reason": reason,
            }, [reason]

        parsed = C2PA_PARSER.parse_c2pa_file(str(path))
        read_status = getattr(parsed, "read_status", "not_attempted")
        if read_status == "dependency_unavailable":
            reason = "C2PA verification dependency is unavailable; stored claims were ignored"
            return _score(), {
                "kind": "attestation",
                "assessed": False,
                "verification_attempted": False,
                "verification_state": read_status,
                "verification_checks": {
                    "content_binding": {"status": "pass"},
                    **{name: {"status": "not_run"} for name in (
                        "signature", "certificate_chain", "revocation",
                        "trust_list", "timestamp", "signer_identity",
                    )},
                },
                "reason": reason,
            }, [reason]
        if read_status == "read_error":
            reason = "; ".join(parsed.warnings) or "C2PA reader failed"
            return _score(), {
                "kind": "attestation",
                "assessed": False,
                "verification_attempted": True,
                "verification_state": read_status,
                "error": True,
                "verification_checks": {
                    "content_binding": {"status": "pass"},
                    **{name: {"status": "unknown"} for name in (
                        "signature", "certificate_chain", "revocation",
                        "trust_list", "timestamp", "signer_identity",
                    )},
                },
                "reason": reason,
            }, [reason]

        present = parsed.present is True
        signature_present = parsed.signature_info is not None
        status = parsed.validation_status
        if not present:
            reason = "The bound file has no active C2PA manifest"
            return _score(), {
                "kind": "attestation",
                "assessed": False,
                "verification_attempted": True,
                "verification_state": "no_active_manifest",
                "manifest_present": False,
                "verification_checks": {
                    "content_binding": {"status": "pass"},
                    "signature": {"status": "absent", "present": False},
                    **{name: {"status": "unknown"} for name in (
                        "certificate_chain", "revocation", "trust_list",
                        "timestamp", "signer_identity",
                    )},
                },
                "reason": reason,
            }, [reason]

        verification_checks, crypto_valid, trusted = _c2pa_verification_checks(
            observed_hash=observed_hash,
            declared_hash=artefact.artefact_hash,
            signature_info=parsed.signature_info,
            validation_status=status,
        )
        explicitly_failed_trust_checks = any(
            verification_checks[name]["status"] == "fail"
            for name in (
                "certificate_chain", "revocation", "trust_list",
                "timestamp", "signer_identity",
            )
        )
        essential_trust_checks_pass = all(
            verification_checks[name]["status"] == "pass"
            for name in ("certificate_chain", "revocation", "trust_list")
        )
        fully_trusted = crypto_valid is True and essential_trust_checks_pass
        levels = PROFILE["attestation"]["levels"]
        value = float(levels["manifest_present"])
        if signature_present:
            value = max(value, float(levels["signature_present"]))
        if crypto_valid is True:
            value = max(value, float(levels["cryptographically_valid"]))
        if fully_trusted:
            value = max(value, float(levels["credential_trusted"]))
        critical_failure = crypto_valid is False
        if critical_failure:
            value = 0.0
            verification_state = "invalid_signature"
        elif fully_trusted:
            verification_state = "trusted"
        elif crypto_valid is True and (
                trusted is False or explicitly_failed_trust_checks):
            verification_state = "cryptographically_valid_untrusted"
        elif crypto_valid is True and trusted is True:
            verification_state = "cryptographically_valid_trust_incomplete"
        elif crypto_valid is True:
            verification_state = "cryptographically_valid_trust_unknown"
        elif signature_present:
            verification_state = "signature_present_unverified"
        else:
            verification_state = "manifest_present_unverified"
        reason = (
            "C2PA was re-read from the SHA-256-bound file; validation fields "
            "come from that live parser result, not from submitter metadata"
        )
        return _score(attestation=value), {
            "kind": "attestation",
            "assessed": True,
            "verification_attempted": True,
            "verification_state": verification_state,
            "critical_failure": critical_failure,
            "value": _clamp(value),
            "confidence": float(PROFILE["attestation"][
                "metadata_only_confidence_ceiling"]),
            "manifest_present": present,
            "signature_present": signature_present,
            "cryptographically_valid": crypto_valid,
            "credential_trusted": trusted,
            "verification_checks": verification_checks,
            "artefact_hash": artefact.artefact_hash,
            "reason": reason,
        }, [reason]


class AIDisclosurePass(ArtefactPass):
    """Detect explicit disclosure fields; never classify the media itself."""

    @staticmethod
    def evaluate(artefact):
        attrs = _attributes(artefact)
        declarations: List[Any] = []
        if "creation_method" in attrs:
            declarations.append(attrs["creation_method"])
        ai_group = attrs.get("AI-declaration")
        if isinstance(ai_group, dict) and "value" in ai_group:
            declarations.append(ai_group["value"])
        nonempty = [value for value in declarations if value not in (None, "", [], {})]
        if not nonempty:
            return _score(), {
                "kind": "ai-disclosure",
                "assessed": False,
                "reason": f"{_type_name(artefact)} has no explicit AI-use declaration",
            }, []
        clear = all(isinstance(value, (str, bool, int, float, list, dict))
                    for value in nonempty)
        value = 1.0 if clear else 0.5
        reason = (f"explicit AI-use/creation-method disclosure found on "
                  f"{artefact.artefact_hash}")
        return _score(ai_disclosure=value), {
            "kind": "ai-disclosure",
            "assessed": True,
            "value": value,
            "confidence": 0.7,
            "reason": reason,
        }, [reason]


class ProfileScore(Score):
    """Ben Score with availability, confidence, reasoning and findings."""

    def __init__(self, axes: Dict[str, Dict[str, Any]], findings: List[dict],
                 checks: Dict[str, Any]):
        self.axes = axes
        self.findings = findings
        self.checks = checks

        def score_value(axis_name: str) -> Any:
            value = axes[axis_name]["value"]
            return Unassessed if value is None else value

        super().__init__(
            integrity=score_value("integrity"),
            completeness=score_value("completeness"),
            attestation_strength=score_value("attestation_strength"),
            ai_disclosure=score_value("ai_disclosure"),
        )

    def to_dict(self) -> dict:
        return {
            "profile_id": PROFILE["profile_id"],
            "profile_version": PROFILE["profile_version"],
            "axes": copy.deepcopy(self.axes),
            "findings": copy.deepcopy(self.findings),
            "checks": copy.deepcopy(self.checks),
        }


def _axis(availability: str, value: Optional[float], confidence: Optional[float],
          reasoning: str) -> dict:
    if availability == "unavailable":
        value, confidence = None, None
    return {
        "availability": availability,
        "value": None if value is None else _clamp(value),
        "confidence": None if confidence is None else _clamp(confidence),
        "reasoning": reasoning,
    }


def _walk(final_artefact) -> Tuple[List[Any], List[Any]]:
    artefacts, edges, pending, seen = [], [], [final_artefact], set()
    while pending:
        artefact = pending.pop()
        if artefact.artefact_hash in seen:
            continue
        seen.add(artefact.artefact_hash)
        artefacts.append(artefact)
        current_edges = list(artefact.get_evidence())
        edges.extend(current_edges)
        pending.extend(edge.get_evidence_artefact() for edge in current_edges)
    return artefacts, edges


def _pass_aux(owner, pass_type) -> Optional[Mapping[str, Any]]:
    result = owner.get_all_pass_results().get(pass_type)
    if result is None:
        return None
    return result[1] if isinstance(result[1], dict) else None


def _completeness_axis(final_artefact, artefacts: Sequence[Any]) -> Tuple[dict, List[dict]]:
    expectations = PROFILE["expectations"]
    required = expectations["incoming_relationships"]
    findings, checks = [], []
    for artefact in artefacts:
        artefact_type = _type_name(artefact)
        allowed = required.get(artefact_type)
        if not allowed:
            continue
        present = {edge.relationship_type.relationship_type_name
                   for edge in artefact.get_evidence()}
        passed = bool(present & set(allowed))
        checks.append({
            "expectation": f"{artefact_type} has an incoming production relationship",
            "artefact_hash": artefact.artefact_hash,
            "allowed_relationships": allowed,
            "passed": passed,
        })
        if not passed:
            findings.append({
                "code": "EXPECTED_RELATIONSHIP_ABSENT",
                "severity": "info",
                "message": (f"{artefact_type} has no expected incoming relationship; "
                            "this lowers completeness but does not imply forgery"),
                "evidence_hash": artefact.artefact_hash,
            })

    final_type = _type_name(final_artefact)
    if final_type in expectations["primary_ancestor_required_for_final_types"]:
        primary = set(expectations["primary_ancestor_types"])
        passed = any(_type_name(item) in primary for item in artefacts
                     if item is not final_artefact)
        checks.append({
            "expectation": "final production output reaches primary/source evidence",
            "artefact_hash": final_artefact.artefact_hash,
            "allowed_artefact_types": sorted(primary),
            "passed": passed,
        })
        if not passed:
            findings.append({
                "code": "PRIMARY_EVIDENCE_ABSENT",
                "severity": "info",
                "message": ("No raw recording or DAW session is reachable from the final "
                            "artefact; this is incomplete, not a contradiction"),
                "evidence_hash": final_artefact.artefact_hash,
            })

    if not checks:
        return _axis(
            "unavailable", None, None,
            "the visible workflow triggers no profile expectation; completeness was not assessed",
        ), findings
    passed = sum(1 for item in checks if item["passed"])
    return _axis(
        "available", passed / len(checks), 0.9,
        f"{passed} of {len(checks)} workflow-triggered expectations were satisfied; "
        "optional evidence is excluded from the denominator",
    ), findings


def _integrity_axis(artefacts: Sequence[Any], edges: Sequence[Any]) -> Tuple[dict, List[dict], dict]:
    assessed, findings = [], []
    expected_count = len(artefacts) + len(edges)
    for artefact in artefacts:
        aux = _pass_aux(artefact, BoundFileIntegrityPass)
        if aux and aux.get("assessed"):
            assessed.append(aux)
    for edge in edges:
        aux = _pass_aux(edge, RelationshipIntegrityPass)
        if aux and aux.get("assessed"):
            assessed.append(aux)

    if not assessed:
        return (_axis(
            "unavailable", None, None,
            "no object file or relationship supplied an integrity check; no adverse inference",
        ), findings, {"expected": expected_count, "assessed": 0, "results": []})

    weights = [max(1, int(item.get("forge_cost", 1))) for item in assessed]
    value = sum(float(item["value"]) * weight
                for item, weight in zip(assessed, weights)) / sum(weights)
    contradictions = [item for item in assessed if item.get("contradiction")]
    if contradictions:
        value = min(value, float(PROFILE["integrity"]["contradiction_value_ceiling"]))
        for item in contradictions:
            findings.append({
                "code": "INTEGRITY_CONTRADICTION",
                "severity": "high",
                "message": item["reason"],
                **({"evidence_hash": item.get("target_hash", item.get("artefact_hash"))}
                   if item.get("target_hash") or item.get("artefact_hash") else {}),
            })

    unassessed_signals = [
        {
            "signal": "stem_similarity",
            "reason": item["stem_similarity"].get("reason", "signal was not assessed"),
            "target_hash": item.get("target_hash"),
            "source_hash": item.get("source_hash"),
            "relationship_type": item.get("relationship_type"),
        }
        for item in assessed
        if isinstance(item.get("stem_similarity"), dict)
        and item["stem_similarity"].get("assessed") is False
    ]

    coverage = len(assessed) / expected_count if expected_count else 0.0
    confidence = coverage * (
        sum(float(item.get("confidence", 0.0)) for item in assessed) / len(assessed))
    availability = (
        "available"
        if len(assessed) == expected_count and not unassessed_signals
        else "partial"
    )
    reasoning = (
        f"{len(assessed)} of {expected_count} file/relationship integrity units assessed; "
        f"{len(contradictions)} contradiction(s); "
        f"{len(unassessed_signals)} required signal(s) unassessed. "
    )
    if unassessed_signals and not contradictions:
        # Do not publish a perfect-looking numeric result when a configured,
        # applicable content check did not run. Unknown is not treated as a
        # failure: the completed unit details remain available, while the axis
        # is explicitly partial and its aggregate value is withheld.
        reported_value: Optional[float] = None
        reported_confidence: Optional[float] = None
        reasoning += (
            "The numeric integrity value is withheld until the required signals "
            "can be completed."
        )
    else:
        reported_value = value
        reported_confidence = confidence
        reasoning += (
            "Forge cost weights consistency and assessment confidence reports "
            "coverage; neither proves authorship."
        )
    return (_axis(availability, reported_value, reported_confidence, reasoning), findings,
            {"expected": expected_count, "assessed": len(assessed),
             "unassessed_signals": unassessed_signals, "results": assessed})


def _attestation_axis(artefacts: Sequence[Any]) -> Tuple[dict, dict, List[dict]]:
    attempts, findings = [], []
    for artefact in artefacts:
        aux = _pass_aux(artefact, AttestationSignalPass)
        if aux and (aux.get("assessed") or aux.get("verification_state")):
            attempts.append(aux)

    critical = [item for item in attempts if item.get("critical_failure")]
    if critical:
        for item in critical:
            findings.append({
                "code": "ATTESTATION_CRITICAL_FAILURE",
                "severity": "critical",
                "message": item["reason"],
                **({"evidence_hash": item["artefact_hash"]}
                   if item.get("artefact_hash") else {}),
            })
        confidence = max(float(item.get("confidence", 0.0)) for item in critical)
        return (_axis(
            "available", 0.0, confidence,
            f"{len(critical)} critical C2PA verification failure(s); a valid "
            "attestation on another artefact cannot mask an invalid signature "
            "or content binding",
        ), {"results": attempts, "critical_failures": len(critical)}, findings)

    errors = [item for item in attempts if item.get("error")]
    for item in errors:
        findings.append({
            "code": "ATTESTATION_VERIFICATION_ERROR",
            "severity": "medium",
            "message": item["reason"],
        })
    results = [item for item in attempts if item.get("assessed")]
    if not results:
        return _axis(
            "unavailable", None, None,
            "no SHA-256-bound file produced an assessable C2PA verification result; "
            "stored or submitter-provided provenance fields were not scored",
        ), {"results": attempts, "critical_failures": 0}, findings
    value = max(float(item["value"]) for item in results)
    confidence = max(float(item["confidence"]) for item in results)
    availability = "partial" if errors or len(results) < len(attempts) else "available"
    return _axis(
        availability, value, confidence,
        "C2PA was re-read from bound files; the value reports the strongest "
        "non-invalid attestation while every per-file result remains visible",
    ), {"results": attempts, "critical_failures": 0}, findings


def _ai_disclosure_axis(artefacts: Sequence[Any]) -> Tuple[dict, dict]:
    results = []
    applicable = [item for item in artefacts
                  if _type_name(item).startswith("audio/")
                  or _type_name(item) == "metadata/ddex-ern"]
    for artefact in applicable:
        aux = _pass_aux(artefact, AIDisclosurePass)
        if aux and aux.get("assessed"):
            results.append(aux)
    if not results:
        return _axis(
            "unavailable", None, None,
            "no explicit creation_method or DDEX AI-declaration was supplied; AI use was not inferred",
        ), {"applicable": len(applicable), "assessed": 0, "results": []}
    coverage = len(results) / len(applicable) if applicable else 1.0
    value = sum(float(item["value"]) for item in results) / len(results)
    confidence = coverage * (
        sum(float(item["confidence"]) for item in results) / len(results))
    availability = "available" if len(results) == len(applicable) else "partial"
    return _axis(
        availability, value, confidence,
        f"explicit AI disclosure found on {len(results)} of {len(applicable)} applicable artefact(s); "
        "this score measures disclosure coverage, not likelihood of human authorship",
    ), {"applicable": len(applicable), "assessed": len(results), "results": results}


def score_merger(final_artefact) -> ProfileScore:
    artefacts, edges = _walk(final_artefact)
    completeness, completeness_findings = _completeness_axis(final_artefact, artefacts)
    integrity, integrity_findings, integrity_checks = _integrity_axis(artefacts, edges)
    attestation, attestation_checks, attestation_findings = _attestation_axis(artefacts)
    ai_disclosure, ai_checks = _ai_disclosure_axis(artefacts)
    return ProfileScore(
        axes={
            "completeness": completeness,
            "integrity": integrity,
            "attestation_strength": attestation,
            "ai_disclosure": ai_disclosure,
        },
        findings=(completeness_findings + integrity_findings
                  + attestation_findings),
        checks={
            "integrity": integrity_checks,
            "attestation_strength": attestation_checks,
            "ai_disclosure": ai_checks,
        },
    )


def build_pipeline() -> Pipeline:
    """Create a real Ben Pipeline configured by this profile."""
    return Pipeline(
        artefact_passes={
            ALL_ARTEFACTS: [
                BoundFileIntegrityPass,
                AttestationSignalPass,
                AIDisclosurePass,
            ],
        },
        evidence_passes={
            ALL_RELATIONSHIPS: [RelationshipIntegrityPass],
        },
        score_merger=score_merger,
    )


def _bind_object_files(chain: Any, objects_dir: Path) -> List[Path]:
    """Bind ordinary filenames through Ben's content-hash matcher."""
    if not objects_dir.is_dir():
        raise ProfileError(f"objects directory does not exist: {objects_dir}")
    paths = sorted(
        item for item in objects_dir.rglob("*")
        if item.is_file() and not item.name.startswith(".")
    )
    if not paths:
        raise ProfileError(f"objects directory contains no files: {objects_dir}")
    for path in paths:
        try:
            chain.bind_file(str(path))
        except Exception as exc:
            raise ProfileError(
                f"Ben could not bind object file {path}: {exc}") from None
    return paths


def _prepare_machine_origin_bindings(chain: Any) -> None:
    """Record whether each machine-origin claim's asserting file is bound.

    Pointer/type/endpoint checks already run in ``apply_relationships``.  This
    final check prevents those stored attributes from receiving a machine
    confidence or forge-cost boost unless the asserting artefact's actual file
    is present and still matches its declared SHA-256 identity.
    """
    for evidence in chain.get_evidence_relationships():
        attrs = getattr(evidence, "_attributes", {})
        origin = attrs.get("assertion_origin", "submitter")
        if origin not in RELATIONSHIPS.MACHINE_READABLE_ORIGINS:
            continue
        asserted_by = attrs.get("asserted_by")
        artefact = chain.artefacts.get(asserted_by)
        if artefact is None:
            result = {
                "verified": False,
                "asserted_by": asserted_by,
                "reason": "asserted_by does not resolve to an artefact",
            }
        elif not artefact.get_file():
            result = {
                "verified": False,
                "asserted_by": asserted_by,
                "reason": "asserting artefact file is not bound",
            }
        else:
            try:
                observed_hash = _hash_file(Path(artefact.get_file()))
            except OSError as exc:
                result = {
                    "verified": False,
                    "asserted_by": asserted_by,
                    "reason": f"asserting artefact file could not be read: {exc}",
                }
            else:
                verified = observed_hash == asserted_by
                result = {
                    "verified": verified,
                    "asserted_by": asserted_by,
                    "observed_hash": observed_hash,
                    "reason": (
                        "asserting artefact file hash matches asserted_by"
                        if verified else
                        "asserting artefact file hash no longer matches asserted_by"
                    ),
                }
        evidence._machine_origin_binding = result


def evaluate_bundle(bundle: Mapping[str, Any],
                    objects_dir: Optional[Path] = None) -> ProfileScore:
    """Load a bundle with Ben, bind optional objects, and run the profile."""
    # With no declarations this validates the existing nested evidence shape,
    # resolves all source hashes and rejects graph cycles.
    normalized = RELATIONSHIPS.apply_relationships(bundle, [])
    chain = RELATIONSHIPS.EvidenceChain.from_dict(normalized)
    if chain.final_artefact_hash not in chain.artefacts:
        raise ProfileError("final_artefact_hash is not present in artefacts")
    if objects_dir is not None:
        _bind_object_files(chain, objects_dir)
    _prepare_machine_origin_bindings(chain)
    result = build_pipeline().eval_chain(chain)
    if not isinstance(result, ProfileScore):
        raise ProfileError("profile pipeline returned an unexpected score type")
    return result


def self_test() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        # Ordinary filenames prove that matching is performed by Ben's
        # chain.bind_file(), not by a hash-based filename convention.
        source_path = root / "original-vocal.wav"
        target_path = root / "vocal-phrase.wav"
        for path, frame_count in ((source_path, 48000), (target_path, 24000)):
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(48000)
                handle.writeframes(b"\x00\x00" * frame_count)
        source_hash = _hash_file(source_path)
        target_hash = _hash_file(target_path)
        bundle = {
            "schema_version": "0.0.4",
            "hash_method": "sha256",
            "final_artefact_hash": target_hash,
            "artefacts": [
                {
                    "artefact_hash": source_hash,
                    "artefact_type": "audio/raw-take",
                    "attributes": {
                        "technical": {
                            "duration_seconds": 1.0,
                            "sample_rate_hz": 48000,
                        },
                        "production": {"track_name": "Original vocal"},
                        "creation_method": "recorded",
                    },
                    "evidence": [],
                },
                {
                    "artefact_hash": target_hash,
                    "artefact_type": "audio/sample",
                    "attributes": {
                        "technical": {
                            "duration_seconds": 0.5,
                            "sample_rate_hz": 48000,
                        },
                        "production": {"sample_name": "Vocal phrase"},
                        "creation_method": "recorded",
                    },
                    "evidence": [],
                },
            ],
        }
        declarations = {"relationships": [{
            "target": target_hash,
            "source": source_hash,
            "relationship_type": "excerpted_from",
            "attributes": {
                "assertion_origin": "submitter",
                "confirmed_by_submitter": True,
                "source_start_seconds": 0.25,
                "source_end_seconds": 0.75,
                "target_start_seconds": 0.0,
                "claim_rationale": "Declared by the submitter.",
            },
        }]}
        complete = RELATIONSHIPS.apply_relationships(bundle, declarations)
        assessed_score = evaluate_bundle(complete, root)
        assessment = assessed_score.to_dict()
        if "overall_score" in assessment:
            raise RuntimeError("assessment must not emit an overall score")
        if assessment["axes"]["integrity"]["value"] != 1.0:
            raise RuntimeError("consistent files and relationship did not pass integrity")
        if assessment["axes"]["completeness"]["value"] != 1.0:
            raise RuntimeError("complete sample lineage did not pass completeness")
        if assessment["axes"]["attestation_strength"]["availability"] != "unavailable":
            raise RuntimeError("missing optional C2PA was treated as a failure")
        if assessed_score.attestation_strength is not Unassessed:
            raise RuntimeError("unavailable attestation was not represented as Ben Unassessed")
        if assessment["axes"]["ai_disclosure"]["value"] != 1.0:
            raise RuntimeError("explicit creation-method disclosure was not recognized")

        metadata_contradicted = copy.deepcopy(complete)
        metadata_contradicted["artefacts"][0]["attributes"][
            "declared_technical"
        ] = {"sample_rate_hz": 44100}
        metadata_bad = evaluate_bundle(metadata_contradicted, root).to_dict()
        if metadata_bad["axes"]["integrity"]["value"] > float(
                PROFILE["integrity"]["contradiction_value_ceiling"]):
            raise RuntimeError("declared/parser metadata contradiction was not scored")
        metadata_units = metadata_bad["checks"]["integrity"]["results"]
        if not any(
            unit.get("contradiction")
            and any(
                check.get("rule") ==
                "declared_technical.sample_rate_hz_matches_observed"
                and check.get("passed") is False
                for check in unit.get("checks", [])
            )
            for unit in metadata_units
        ):
            raise RuntimeError("metadata contradiction detail was not retained")

        contradicted = copy.deepcopy(complete)
        contradicted["artefacts"][1]["evidence"][0]["attributes"][
            "source_end_seconds"] = 75.0
        bad = evaluate_bundle(contradicted, root).to_dict()
        if bad["axes"]["integrity"]["value"] > float(
                PROFILE["integrity"]["contradiction_value_ceiling"]):
            raise RuntimeError("contradiction did not cap integrity")
        if not any(item["code"] == "INTEGRITY_CONTRADICTION"
                   for item in bad["findings"]):
            raise RuntimeError("contradiction finding was not emitted")

        no_files = evaluate_bundle(complete).to_dict()
        if no_files["axes"]["integrity"]["availability"] != "partial":
            raise RuntimeError("missing bound objects should make integrity partial")

    return {
        "status": "ok",
        "ben_pipeline_used": True,
        "ben_chain_bind_file_used": True,
        "ordinary_filenames_bound_by_content_hash": True,
        "profile_id": PROFILE["profile_id"],
        "consistent_integrity": assessment["axes"]["integrity"],
        "contradiction_integrity": bad["axes"]["integrity"],
        "missing_c2pa_is_not_failure": True,
        "missing_objects_produce_partial_integrity": True,
        "declared_parser_conflict_is_scored": True,
        "overall_score_invented": False,
    }


def _write_json(path: Optional[Path], value: Any) -> None:
    encoded = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if path is None:
        print(encoded, end="")
    else:
        path.write_text(encoded, encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", type=Path,
                        help="Ben-compatible evidence bundle JSON")
    parser.add_argument("--objects-dir", type=Path,
                        help="directory of artefact files matched by content SHA-256")
    parser.add_argument("-o", "--output", type=Path,
                        help="assessment output path; stdout when omitted")
    parser.add_argument("--show-profile", action="store_true",
                        help="print the machine-readable profile")
    parser.add_argument("--self-test", action="store_true",
                        help="run a Ben Pipeline integration test")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            if args.bundle or args.objects_dir:
                parser.error("--self-test cannot be combined with bundle inputs")
            _write_json(args.output, self_test())
            return 0
        if args.show_profile:
            if args.bundle or args.objects_dir:
                parser.error("--show-profile cannot be combined with bundle inputs")
            _write_json(args.output, PROFILE)
            return 0
        if args.bundle is None:
            parser.error("bundle is required unless --self-test or --show-profile is used")
        bundle = _read_json(args.bundle)
        result = evaluate_bundle(bundle, args.objects_dir)
        _write_json(args.output, result.to_dict())
        return 0
    except (ProfileError, RELATIONSHIPS.RelationshipExtractionError) as exc:
        print(f"profile evaluation failed: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"profile evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
