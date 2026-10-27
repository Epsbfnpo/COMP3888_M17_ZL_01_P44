"""Local file checks, libevchain passes, and explicitly scoped axis results."""

import dataclasses
import hashlib
import math
from pathlib import Path

from libevchain.evidence_chain import EvidenceChain
from libevchain.pipeline import ArtefactPass, EvidencePass, Pipeline
from libevchain.types import AudioType, ALL_RELATIONSHIPS

from music_target import domain
from music_target.assessment_passes import assess_axes, parse_bound_artefacts
from music_target.audio_formats import detect
from music_target.audio_observations import (MP3_RECORDER, flac_embedded, flac_observed, flac_recorder,
                                             limited_checks, mp3_embedded, mp3_observed)
from music_target.flac_io import FlacError, UnsupportedFlac, inspect_flac, verify_md5
from music_target.format_conversion import FORMAT_CONVERSION, conversion_checks
from music_target.mp3_io import Mp3Error, UnsupportedMp3, inspect_mp3
from music_target.wav_io import UnsupportedWav, WavError, inspect_wav
from music_target.recorder_metadata import recorder_fields, timestamp_checks
from music_target.sample_validation import from_flac_md5, sample_check, scan_decoded, scan_wav
from music_target.wav_parser import parse_wav_file
from music_target.workflow_policy import DEFAULT_POLICY_PATH, WorkflowCompletenessPass, load_workflow_policy
from music_target.time_ranges import content_range_checks

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


class InputError(ValueError):
    """A request-level failure; ``findings`` replaces the generic finding."""

    def __init__(self, message, findings=None):
        super().__init__(message)
        self.findings = list(findings or [])


class Unsupported(InputError):
    pass


class ObjectsChangedDuringEvaluation(Exception):
    """Non-final files changed while being evaluated; re-evaluate without them.

    Deliberately not a ValueError, so no check can swallow it.
    """

    def __init__(self, changed):
        super().__init__("Non-final objects changed during evaluation.")
        self.changed = changed  # digest -> finding


def finding(code, message, digest=None, severity="info"):
    result = {"code": code, "severity": severity, "message": message}
    if digest is not None:
        result["evidence_hash"] = digest
    return result


def axis(value=None, reason=None):
    return {"availability": "partial" if value is not None else "unavailable",
            "value": value, "confidence": None, "reasoning": reason}


def empty_axes():
    return {name: axis(reason="This evaluation has no result for this axis.")
            for name in ["completeness", "integrity", "attestation_strength", "ai_disclosure"]}


def _observed_wav(descriptor):
    codec = "PCM" if descriptor.format_kind == "int" else "IEEE_FLOAT"
    return {
        "duration_seconds": descriptor.duration_seconds,
        "sample_rate_hz": descriptor.sample_rate,
        "channels": descriptor.channels,
        "bit_depth": descriptor.container_bits,
        "format": "WAV",
        "codec_subtype": codec,
        "sample_format": descriptor.format_kind,
        "file_size_bytes": descriptor.file_size,
        "wav_format_tag": descriptor.format_tag,
        "valid_bits_per_sample": descriptor.valid_bits,
        "channel_mask": descriptor.channel_mask,
        "trailing_bytes": descriptor.trailing_bytes,
        "wav_warnings": list(descriptor.warnings),
    }


def _inspect_supported_wav(path):
    try:
        return inspect_wav(path)
    except UnsupportedWav as exc:
        raise Unsupported(str(exc)) from exc
    except WavError as exc:
        raise InputError(str(exc)) from exc


def strict_pcm_check(path):
    """Stream-check supported integer/float WAV framing before extraction."""
    return _observed_wav(_inspect_supported_wav(path))


def _inspect_other(path, detected, deadline):
    """FLAC or MP3 descriptor; format errors become request-level errors as for WAV."""
    try:
        if detected.container == "FLAC":
            return inspect_flac(path, detected)
        return inspect_mp3(path, detected, deadline)
    except (UnsupportedFlac, UnsupportedMp3) as exc:
        raise Unsupported(str(exc)) from exc
    except (FlacError, Mp3Error) as exc:
        raise InputError(str(exc)) from exc


# Declared technical fields a format cannot carry, and why they are not compared.
_UNMEASURABLE = {
    "MP3": {"bit_depth": "MP3 is lossy and has no bit depth.",
            "duration_seconds": "The exact MP3 duration is unknown: there is no Xing/Info header "
                                "with encoder delay and padding."},
    "FLAC": {"duration_seconds": "STREAMINFO does not record the total number of samples."},
}


def _technical_checks(artefact, observed, unmeasurable=None):
    """Compare declared technical fields with measured ones (shared by every format)."""
    claim = artefact._attributes.get("technical", {})
    chain = getattr(artefact, "_evaluation_chain", None)
    declaration_errors = (getattr(chain, "_declaration_error_nodes", {})
                          if chain is not None else {})
    skipped_claims = artefact.artefact_hash in declaration_errors
    if skipped_claims:
        # The declaration contains fields this evaluator does not know;
        # it is not corrected, so its technical claims are not compared.
        claim = {}
    checks, mismatches = [], []
    for key, value in claim.items():
        if value is None:
            continue
        measured = observed[key]
        if measured is None:
            checks.append({"rule": f"technical.{key}_matches_observed",
                           "passed": None,
                           "status": "unavailable",
                           "evidence_hash": artefact.artefact_hash,
                           "declared": value,
                           "observed": None,
                           "reason": (unmeasurable or {}).get(key, "This property was not measurable."),
                           "source_pass": "WavMetadataPass"})
            continue
        same = (math.isclose(value, measured, rel_tol=0, abs_tol=0.002)
                if key == "duration_seconds" else value == measured)
        checks.append({"rule": f"technical.{key}_matches_observed",
                       "passed": same,
                       "evidence_hash": artefact.artefact_hash,
                       "declared": value,
                       "observed": measured,
                       "source_pass": "WavMetadataPass"})
        if not same:
            mismatches.append({"field": key, "declared": value, "observed": measured})
    return checks, mismatches, skipped_claims


# Parser warnings that mean audio samples are missing or the stream is broken.
# Damaged metadata after complete audio, unfinalised sizes (streaming writers
# leave them) and junk before the first MP3 frame are only warnings.
_STREAM_DAMAGE = {"WAV": ("partial_final_frame_ignored:",),
                  "MP3": ("sync_lost:", "truncated_final_frame:", "too_many_sync_losses")}

# Parse failures that say nothing about the file itself.
_NOT_FILE_DAMAGE = {"caller_deadline"}


def _stream_checks(digest, container, warnings):
    """A failed ``audio_stream_complete`` check when the parser had to skip audio; else none."""
    damage = [item for item in warnings if item.startswith(_STREAM_DAMAGE.get(container, ()))]
    if not damage:
        return []
    return [{"rule": "audio_stream_complete", "passed": False, "evidence_hash": digest,
             "source_pass": "WavMetadataPass", "format": container, "damage": damage}]


def _readability_checks(digest, label, failure_type, cause, error):
    """A failed ``audio_file_readable`` check when a file is malformed or not audio at all.

    An unsupported but valid format (AIFF, RF64, A-law), a read error and a
    time limit are not evidence about the file, so they add no check.
    """
    reason_code = getattr(cause, "reason_code", None)
    if (failure_type != "invalid" or not isinstance(cause, (WavError, FlacError, Mp3Error))
            or reason_code in _NOT_FILE_DAMAGE):
        return []
    return [{"rule": "audio_file_readable", "passed": False, "evidence_hash": digest,
             "source_pass": "WavMetadataPass", "format": label, "reason_code": reason_code,
             "reason": error}]


def _md5_check(digest, md5):
    passed = {"matched": True, "mismatched": False, "failed": False}.get(md5["status"])
    check = {"rule": "flac_md5_matches_decoded_audio", "passed": passed, "evidence_hash": digest,
             "source_pass": "WavMetadataPass",
             **{key: md5.get(key) for key in ("stored_md5", "decoded_md5", "decoded_frames",
                                              "declared_total_samples", "reason_code", "reason")
                if md5.get(key) is not None}}
    if passed is None:
        check["status"] = "unavailable"
    return check


def _evaluate_other(artefact, path, detected):
    """FLAC and MP3 observations, in the WAV result shape plus ``audio_format``."""
    chain = getattr(artefact, "_evaluation_chain", None)
    deadline = getattr(chain, "_deadline", None)
    descriptor = _inspect_other(path, detected, deadline)
    container = descriptor.format
    artefact._wav_parser_failure = None
    artefact._wav_parser_failure_reason_code = None
    artefact._wav_descriptor = None
    artefact._audio_descriptor = descriptor
    if container == "FLAC":
        observed, embedded = flac_observed(descriptor), flac_embedded(descriptor)
        try:
            recorder = flac_recorder(descriptor, embedded)
        except Exception as exc:  # metadata never decides whether audio is usable
            recorder = {"present": False, "warnings": ["Recorder metadata could not be read: " + str(exc)]}
    else:
        observed, embedded, recorder = mp3_observed(descriptor), mp3_embedded(descriptor), dict(MP3_RECORDER)
    checks, mismatches, skipped_claims = _technical_checks(artefact, observed, _UNMEASURABLE[container])
    checks += _stream_checks(artefact.artefact_hash, container, descriptor.warnings)
    checks += timestamp_checks(artefact.artefact_hash, recorder, descriptor.sample_rate)
    result = {"status": "parsed", "audio_format": container, "observed": observed,
              "embedded_metadata": embedded, "recorder_metadata": recorder,
              "checks": checks, "claim_mismatches": mismatches}
    notes = [f"{container} structure was checked; submission claims were compared with measured file properties.",
             "Embedded dates, credits and software tags are unauthenticated metadata."]
    md5 = {}
    if container == "FLAC":
        md5 = verify_md5(path, descriptor, deadline)
        result["checks"] = checks + [_md5_check(artefact.artefact_hash, md5)]
        result["flac_md5"] = md5
        result["limited_checks"] = limited_checks(
            "FLAC", exact_duration=descriptor.duration_seconds is not None, md5_stored=descriptor.md5 is not None,
            recorder_kept=recorder.get("foreign_metadata_kept") is not False)
    else:
        result["limited_checks"] = limited_checks("MP3", exact_duration=descriptor.duration_seconds is not None)
    # Every sample is read once: FLAC through its MD5 pass (or a plain decode
    # when no MD5 is stored), MP3 through the decoder.
    if container == "FLAC" and md5.get("reason_code") != "md5_not_stored":
        samples = from_flac_md5(md5)
    else:
        samples = scan_decoded(path, deadline)
    if samples is not None:
        result["checks"].append(sample_check(artefact.artefact_hash, container, samples))
    notes.append("The evaluator is designed for WAV; checks not made on this file are listed in "
                 "limited_checks and reported as unavailable.")
    if skipped_claims:
        result["technical_claims_skipped"] = "input_error"
    return None, result, notes


class WavMetadataPass(ArtefactPass):
    """Per-file audio observations: WAV, FLAC and MP3, decided by file content."""

    @staticmethod
    def evaluate(artefact):
        if not artefact.has_file():
            return None, None, ["No verified file is bound to this artefact."]
        path = Path(artefact.get_file())
        container = None
        try:
            detected = detect(path)
            container = detected.container
            if container in ("FLAC", "MP3", "MPEG Layer I/II"):
                return _evaluate_other(artefact, path, detected)
            descriptor = _inspect_supported_wav(path)
            observed = _observed_wav(descriptor)
            parsed = dataclasses.asdict(parse_wav_file(str(path), descriptor))
            parsed.pop("source_path", None)
        except (InputError, OSError, ValueError) as exc:
            chain = getattr(artefact, "_evaluation_chain", None)
            if chain is not None and artefact.artefact_hash == chain.final_artefact_hash:
                raise
            failure_type = "unsupported" if isinstance(exc, Unsupported) else "invalid"
            artefact._wav_parser_failure = str(exc)
            cause = exc.__cause__
            artefact._wav_parser_failure_reason_code = getattr(
                cause, "reason_code", "unsupported_wav_format"
                if failure_type == "unsupported" else "invalid_wav")
            artefact._wav_descriptor = None
            other = container in ("FLAC", "MP3", "MPEG Layer I/II")
            label = ("MP3" if container == "MPEG Layer I/II" else container) if other else "WAV"
            failure = {
                "status": "parser_failed",
                "failure_type": failure_type,
                "error": str(exc),
                "observed": {},
                "embedded_metadata": None,
                "checks": [{
                    "rule": "audio_parser_available" if other else "wav_parser_available",
                    "passed": None,
                    "status": "unavailable",
                    "evidence_hash": artefact.artefact_hash,
                    "source_pass": "WavMetadataPass",
                    "reason": str(exc),
                }] + _readability_checks(artefact.artefact_hash, label, failure_type, cause, str(exc)),
                "claim_mismatches": [],
            }
            if other:
                failure["audio_format"] = label
            return None, failure, [
                f"This non-final {label} could not be decoded by the supported {label} parser; only checks that require this node are unavailable.",
            ]
        artefact._wav_parser_failure = None
        artefact._wav_parser_failure_reason_code = None
        artefact._wav_descriptor = descriptor
        checks, mismatches, skipped_claims = _technical_checks(artefact, observed)
        checks += _stream_checks(artefact.artefact_hash, "WAV", descriptor.warnings)
        try:
            recorder = recorder_fields(path, descriptor, parsed)
        except Exception as exc:  # metadata never decides whether audio is usable
            recorder = {"present": False, "warnings": ["Recorder metadata could not be read: " + str(exc)]}
        checks += timestamp_checks(artefact.artefact_hash, recorder, descriptor.sample_rate)
        chain = getattr(artefact, "_evaluation_chain", None)
        checks.append(sample_check(artefact.artefact_hash, "WAV",
                                   scan_wav(path, descriptor, getattr(chain, "_deadline", None))))
        result = {"status": "parsed", "observed": observed,
                  "embedded_metadata": parsed, "recorder_metadata": recorder,
                  "checks": checks, "claim_mismatches": mismatches}
        if skipped_claims:
            result["technical_claims_skipped"] = "input_error"
        return None, result, [
            "WAV framing was checked; submission claims were compared with measured file properties.",
            "Embedded dates, credits and software tags are unauthenticated metadata.",
        ]


class RelationshipIntegrityPass(EvidencePass):
    """Check declared edge parameters against cached endpoint observations."""

    @staticmethod
    def evaluate(evidence):
        source, target = evidence.get_evidence_artefact(), evidence.get_result_artefact()
        chain = getattr(target, "_evaluation_chain", None)
        edge_errors = (getattr(chain, "_declaration_error_edges", {})
                       if chain is not None else {})
        if (target.artefact_hash, source.artefact_hash) in edge_errors:
            return None, {
                "pass": "RelationshipIntegrityPass",
                "source_hash": source.artefact_hash,
                "target_hash": target.artefact_hash,
                "relationship": evidence.relationship_type.relationship_type_name,
                "endpoint_roles_validated": True,
                "content_relationship_verified": None,
                "status": "unavailable",
                "reason_code": "input_error",
                "reason": "The relationship declaration contains unrecognised fields; "
                          "its declared parameters were not checked.",
                "value": None,
                "checks": [],
            }, ["Declared parameters were not compared because the declaration has input errors."]
        source_result = target_result = None
        if source.artefact_type.artefact_type_name.startswith("audio"):
            _, source_result = source.get_pass_result(WavMetadataPass)
        if target.artefact_type.artefact_type_name.startswith("audio"):
            _, target_result = target.get_pass_result(WavMetadataPass)

        source_observed = (source_result or {}).get("observed", {})
        target_observed = (target_result or {}).get("observed", {})
        attrs = evidence._attributes
        checks = []
        range_details = []
        for role, observed in (("source", source_observed), ("target", target_observed)):
            range_details.extend(content_range_checks(
                attrs.get(role + "_start_seconds"), attrs.get(role + "_end_seconds"),
                observed.get("duration_seconds"), observed.get("sample_rate_hz"), role))
        # Keep the existing scoring inputs for already-valid declarations.
        # Extra successful validation details are not additional support and
        # must not dilute an existing failure. Newly detected failures still
        # enter the existing contradiction path; policy weights are unchanged.
        legacy_range_rules = {"source_time_order_valid", "source_time_within_duration",
                              "target_time_within_duration"}
        checks.extend(check for check in range_details
                      if check["rule"] in legacy_range_rules or check["passed"] is False)
        for attr, observed, key in (
                ("input_sample_rate_hz", source_observed, "sample_rate_hz"),
                ("output_sample_rate_hz", target_observed, "sample_rate_hz"),
                ("input_bit_depth", source_observed, "bit_depth"),
                ("output_bit_depth", target_observed, "bit_depth")):
            if attrs.get(attr) is not None and observed.get(key) is not None:
                checks.append({"rule": f"{attr}_matches_file",
                               "passed": attrs[attr] == observed[key],
                               "declared": attrs[attr], "observed": observed[key]})

        conversion = None
        if attrs.get("transformation") == FORMAT_CONVERSION:
            conversion_scored, conversion = conversion_checks(
                source, target, source_result, target_result,
                evidence.relationship_type.relationship_type_name, getattr(chain, "_deadline", None))
            checks.extend(conversion_scored)
            for check in conversion.get("checks", []):
                check.update({"source_hash": source.artefact_hash, "target_hash": target.artefact_hash})

        for check in checks + range_details:
            check.update({"source_hash": source.artefact_hash,
                          "target_hash": target.artefact_hash,
                          "relationship_type":
                              evidence.relationship_type.relationship_type_name,
                          "source_pass": "RelationshipIntegrityPass"})

        result = {"pass": "RelationshipIntegrityPass",
                  "source_hash": source.artefact_hash,
                  "target_hash": target.artefact_hash,
                  "relationship": evidence.relationship_type.relationship_type_name,
                  "endpoint_roles_validated": True,
                  "content_relationship_verified": None,
                  "value": (sum(item["passed"] for item in checks) / len(checks)
                            if checks else None),
                  "checks": checks,
                  "range_validation_details": range_details}
        if conversion is not None:
            result["format_conversion"] = {"source_hash": source.artefact_hash,
                                           "target_hash": target.artefact_hash, **conversion}
        if (source_observed.get("duration_seconds") is not None and
                target_observed.get("duration_seconds") is not None):
            result["duration_delta_seconds"] = (
                target_observed["duration_seconds"]
                - source_observed["duration_seconds"])
        return None, result, [
            "Endpoint roles were validated before the Pass ran; declared edge parameters were compared with cached endpoint observations.",
            "Duration difference is diagnostic only.",
            "Scoped edit, comp, stem and mix claims receive separate content checks; mastering receives continuity corroboration only.",
        ]


_RECORDING_TYPES = {"audio/raw-take", "audio/raw-track"}


def _format_findings(digest, observation, artefact_type):
    """Report-only notes on what a FLAC or MP3 file allows the evaluator to check."""
    container = observation.get("audio_format")
    notes = []
    if container in ("FLAC", "MP3") and observation.get("limited_checks"):
        notes.append({**finding("AUDIO_FORMAT_LIMITED_CHECKS",
                                f"The evaluator is designed and calibrated for WAV. Some checks were not made on "
                                f"this {container} file; they are listed in limited_checks with the reason "
                                "(program_scope: implemented for WAV only; format: the format cannot carry it; "
                                "file: this file does not carry it) and reported as unavailable, never as "
                                "contradictions.", digest),
                      "audio_format": container, "limited_checks": observation["limited_checks"],
                      "scoring_eligible": False, "integrity_contribution": None})
    recorder = observation.get("recorder_metadata") or {}
    if (container == "FLAC" and artefact_type in _RECORDING_TYPES
            and recorder.get("foreign_metadata_kept") is False):
        return notes + [{**finding("FLAC_RECORDER_METADATA_ABSENT",
                           "This FLAC recording carries no WAV chunks (bext/iXML). A recorder writes them "
                           "into the WAV; plain FLAC encoding drops them unless the encoder keeps foreign "
                           "metadata (flac --keep-foreign-metadata). Recorder comparisons are not possible; "
                           "this is not a contradiction.", digest),
                 "audio_format": "FLAC", "scoring_eligible": False, "integrity_contribution": None}]
    return notes


def collect_results(final_artefact):
    # Include retained nodes that became disconnected only because an unknown
    # node or relationship was quarantined.
    chain = final_artefact._evaluation_chain
    observations, relationships = {}, []
    for artefact in chain.artefacts.values():
        if artefact.artefact_type.artefact_type_name.startswith("audio"):
            _, observations[artefact.artefact_hash] = artefact.get_pass_result(WavMetadataPass)
        for edge in artefact.get_evidence():
            _, result = edge.get_pass_result(RelationshipIntegrityPass)
            relationships.append(result)
    return observations, relationships


def _file_identity(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def _bind_verified(chain, digest, path):
    # The digest was verified by _check_object. Bind the known artefact
    # directly so libevchain does not hash the same file a second time.
    artefact = chain.artefacts[digest]
    artefact.bind_file(str(path))
    artefact._verified_file_hash = digest


def _check_object(root, relative, digest, *, final, total_before=0,
                  allow_external=False):
    """Return ``(problem_finding_or_None, size, resolved_path)`` for one object."""
    label = "final artefact" if final else "object"
    if relative is None:
        return (finding("MISSING_OBJECT", "The final artefact file is missing."
                        if final else "No file mapping was supplied.", digest, "medium"),
                0, None)
    submitted_path = Path(relative)
    path = (submitted_path if submitted_path.is_absolute()
            else root / submitted_path).resolve()
    if not allow_external and not path.is_relative_to(root):
        return (finding("OBJECT_PATH_INVALID",
                        f"Object path escapes the supplied bundle root ({label}); "
                        "the file was not opened.", digest, "high"), 0, None)
    if not path.exists():
        return (finding("MISSING_OBJECT", "The final artefact file is missing."
                        if final else "The declared object file is missing.",
                        digest, "medium"), 0, None)
    if not path.is_file():
        return (finding("OBJECT_PATH_INVALID",
                        f"An object path must refer to a regular file ({label}).",
                        digest, "high"), 0, None)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES or total_before + size > MAX_TOTAL_BYTES:
        limits = (f"{MAX_FILE_BYTES // (1024 * 1024)} MiB per file and "
                  f"{MAX_TOTAL_BYTES // (1024 * 1024)} MiB per request")
        if final:
            return (finding("OBJECT_SIZE_LIMIT",
                            f"Object size exceeds the documented local evaluation limits ({limits}).",
                            digest, "medium"), size, path)
        return ({**finding("OBJECT_SKIPPED_SIZE_LIMIT",
                           f"This non-final object exceeds the evaluation size limits ({limits}) "
                           "and was not bound; checks that need it are unavailable.",
                           digest, "medium"),
                 "size_bytes": size, "affected_checks": "checks that use this file"},
                size, path)
    with path.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != digest:
        if final:
            return (finding("OBJECT_HASH_MISMATCH",
                            f"SHA-256 mismatch for declared object {digest}.", digest, "high"),
                    size, path)
        return ({**finding("OBJECT_HASH_MISMATCH",
                           f"SHA-256 mismatch for declared object {digest}: the file content "
                           "differs from the manifest (for example it was re-tagged or re-exported "
                           "after the manifest was made). It was not bound; checks that need it "
                           "are unavailable.", digest, "high"),
                 "observed_sha256": actual, "affected_checks": "checks that use this file"},
                size, path)
    return None, size, path


def evaluate_chain(native, objects, root, workflow=None, allow_disconnected=False,
                   deadline=None, changed_objects=None, allow_external_objects=False):
    chain = EvidenceChain.from_dict(native)
    for artefact in chain.artefacts.values():
        artefact._evaluation_chain = chain
    for edge in chain.get_evidence_relationships():
        if not edge.relationship_type.validate_types(
                edge.get_result_artefact().artefact_type,
                edge.get_evidence_artefact().artefact_type):
            raise InputError("Relationship endpoints do not match the registered role contract.")

    # Reject disconnected nodes so they cannot inflate a final asset's evidence metrics.
    reachable, pending = set(), [chain.final_artefact_hash]
    while pending:
        digest = pending.pop()
        if digest not in reachable:
            reachable.add(digest)
            pending.extend(chain.artefacts[digest].evidence)
    if reachable != set(chain.artefacts) and not allow_disconnected:
        raise InputError("All declared artefacts must be connected to the final artefact.")

    root = Path(root).resolve(strict=True)
    input_issues = native.get("_input_issues") or {}
    chain._declaration_error_nodes = dict(input_issues.get("declaration_nodes") or {})
    chain._declaration_error_edges = dict(input_issues.get("declaration_edges") or {})
    chain._deadline = deadline
    unbound_issues = dict(input_issues.get("unbound_nodes") or {})
    final_hash = chain.final_artefact_hash
    bound, bound_hashes, findings, total = 0, set(), [], 0
    # Keep absence separate from other input errors. A missing object supplies no
    # bytes that could contradict its declared hash; it reduces evidence
    # completeness instead of file integrity.
    missing_hashes = set()
    # Files that changed during an earlier attempt are isolated from the start,
    # so the result equals one where they were never bound.
    for digest, problem in (changed_objects or {}).items():
        unbound_issues[digest] = [problem["code"]]
        findings.append(problem)
    binding_stats = {}

    # The final artefact is checked first. If its identity cannot be
    # confirmed, the whole assessment stops and only its problem is reported.
    problem, size, path = _check_object(root, objects.get(final_hash), final_hash,
                                        final=True,
                                        allow_external=allow_external_objects)
    if problem is not None:
        exception = Unsupported if problem["code"] == "OBJECT_SIZE_LIMIT" else InputError
        raise exception(problem["message"], [problem])
    total += size
    _bind_verified(chain, final_hash, path)
    binding_stats[final_hash] = _file_identity(path)
    bound += 1
    bound_hashes.add(final_hash)

    for digest in chain.artefacts:
        if digest == final_hash or digest in unbound_issues:
            continue
        relative = objects.get(digest)
        if relative is None:
            missing_hashes.add(digest)
            findings.append(finding("MISSING_OBJECT", "No file mapping was supplied.", digest, "medium"))
            continue
        problem, size, path = _check_object(root, relative, digest, final=False,
                                            total_before=total,
                                            allow_external=allow_external_objects)
        if problem is not None:
            findings.append(problem)
            if problem["code"] == "MISSING_OBJECT":
                missing_hashes.add(digest)
            else:
                unbound_issues[digest] = [problem["code"]]
            continue
        total += size
        _bind_verified(chain, digest, path)
        binding_stats[digest] = _file_identity(path)
        bound += 1
        bound_hashes.add(digest)
    chain._input_error_nodes = unbound_issues
    input_error_hashes = set(unbound_issues)

    pipeline = Pipeline({AudioType: [WavMetadataPass]},
                        {ALL_RELATIONSHIPS: [RelationshipIntegrityPass]}, collect_results)
    observations, relationships = pipeline.eval_chain(chain)
    claim_mismatch = False
    node_types = {node["artefact_hash"]: node["artefact_type"] for node in native["artefacts"]}
    for digest, observation in observations.items():
        if observation is not None:
            claim_mismatch |= bool(observation.get("claim_mismatches", []))
            other = observation.get("audio_format")
            if observation.get("status") == "parser_failed":
                findings.append({
                    **(finding("AUDIO_PARSER_FAILED",
                               f"A non-final {other} file could not be parsed; dependent checks are unavailable.",
                               digest, "medium") if other else
                       finding("WAV_PARSER_FAILED",
                               "A non-final WAV could not be parsed; dependent checks are unavailable.",
                               digest, "medium")),
                    **observation,
                })
            else:
                findings.append({
                    **(finding("AUDIO_OBSERVATION",
                               f"Measured {other} properties and extracted metadata.", digest) if other else
                       finding("WAV_OBSERVATION",
                               "Measured WAV properties and extracted metadata.", digest)),
                    **observation,
                })
                findings.extend(_format_findings(digest, observation, node_types.get(digest)))
    for result in relationships:
        findings.append({**finding("RELATIONSHIP_INTEGRITY_OBSERVATION", "Declared relationship parameters were checked by the cached EvidencePass; separate content analysis may corroborate or contradict the submitted transformation."),
                         **result})
    parser_observations, parser_findings = parse_bound_artefacts(
        native, chain, observations)
    findings.extend(parser_findings)

    # Loaded once: completeness and the support-breadth summary must read the
    # same workflow policy.
    workflow_policy = load_workflow_policy(DEFAULT_POLICY_PATH) if workflow is not None else None
    integrity, attestation, ai_disclosure, validation, assessment_checks, assessment_findings = assess_axes(
        native, chain, bound_hashes, missing_hashes, observations, relationships,
        parser_observations, workflow, workflow_policy=workflow_policy)
    findings.extend(assessment_findings)
    axes = empty_axes()
    workflow_assessment = None
    if workflow is not None:
        completeness_pass = WorkflowCompletenessPass(workflow_policy)
        axes["completeness"], workflow_assessment, policy_findings = completeness_pass.evaluate(
            native, workflow, bound_hashes, parser_observations, input_error_hashes)
        findings.extend(policy_findings)
    else:
        axes["completeness"] = axis(bound / len(chain.artefacts),
            f"{bound}/{len(chain.artefacts)} declared reachable objects were available and hash-matched. "
            "No workflow was selected, so workflow expectations were not applied.")
        findings.append(finding("WORKFLOW_POLICY_NOT_APPLIED",
                                "No workflow was supplied; completeness is legacy declared-object availability only.", severity="low"))
    axes["integrity"] = integrity
    axes["attestation_strength"] = attestation
    axes["ai_disclosure"] = ai_disclosure
    if claim_mismatch:
        axes["integrity"]["reasoning"] += " Technical claim contradictions were found."

    # A file replaced while it was being analysed makes every result that read
    # it unreliable. For the final artefact the assessment stops; changed
    # non-final files make the caller re-evaluate once with them isolated.
    changed = {}
    for digest, identity in binding_stats.items():
        try:
            unchanged = _file_identity(Path(chain.artefacts[digest].get_file())) == identity
        except OSError:
            unchanged = False
        if unchanged:
            continue
        if digest == final_hash:
            problem = finding("FINAL_ARTEFACT_CHANGED",
                              "The final artefact file changed during evaluation; "
                              "all results were discarded.", digest, "high")
            raise InputError(problem["message"], [problem])
        changed[digest] = {**finding(
            "OBJECT_CHANGED_DURING_EVALUATION",
            "This file changed during evaluation; the request was re-evaluated with it "
            "isolated, so checks that need it are unavailable.", digest, "high"),
            "affected_checks": "checks that use this file"}
    if changed:
        raise ObjectsChangedDuringEvaluation(changed)
    return axes, findings, workflow_assessment, validation, assessment_checks
