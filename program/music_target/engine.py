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
from music_target.wav_io import UnsupportedWav, WavError, inspect_wav
from music_target.wav_parser import parse_wav_file
from music_target.workflow_policy import WorkflowCompletenessPass, load_workflow_policy

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
class InputError(ValueError):
    pass


class Unsupported(InputError):
    pass


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


class WavMetadataPass(ArtefactPass):
    @staticmethod
    def evaluate(artefact):
        if not artefact.has_file():
            return None, None, ["No verified file is bound to this artefact."]
        path = Path(artefact.get_file())
        try:
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
            return None, {
                "status": "parser_failed",
                "failure_type": failure_type,
                "error": str(exc),
                "observed": {},
                "embedded_metadata": None,
                "checks": [{
                    "rule": "wav_parser_available",
                    "passed": None,
                    "status": "unavailable",
                    "evidence_hash": artefact.artefact_hash,
                    "source_pass": "WavMetadataPass",
                    "reason": str(exc),
                }],
                "claim_mismatches": [],
            }, [
                "This non-final WAV could not be decoded by the supported WAV parser; only checks that require this node are unavailable.",
            ]
        artefact._wav_parser_failure = None
        artefact._wav_parser_failure_reason_code = None
        artefact._wav_descriptor = descriptor
        claim = artefact._attributes.get("technical", {})
        checks, mismatches = [], []
        for key, value in claim.items():
            if value is None:
                continue
            measured = observed[key]
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
        return None, {"status": "parsed", "observed": observed,
                      "embedded_metadata": parsed,
                      "checks": checks, "claim_mismatches": mismatches}, [
            "WAV framing was checked; submission claims were compared with measured file properties.",
            "Embedded dates, credits and software tags are unauthenticated metadata.",
        ]


class RelationshipIntegrityPass(EvidencePass):
    """Check declared edge parameters against cached endpoint observations."""

    @staticmethod
    def evaluate(evidence):
        source, target = evidence.get_evidence_artefact(), evidence.get_result_artefact()
        source_result = target_result = None
        if source.artefact_type.artefact_type_name.startswith("audio"):
            _, source_result = source.get_pass_result(WavMetadataPass)
        if target.artefact_type.artefact_type_name.startswith("audio"):
            _, target_result = target.get_pass_result(WavMetadataPass)

        source_observed = (source_result or {}).get("observed", {})
        target_observed = (target_result or {}).get("observed", {})
        attrs = evidence._attributes
        checks = []

        if (attrs.get("source_start_seconds") is not None and
                attrs.get("source_end_seconds") is not None):
            checks.append({"rule": "source_time_order_valid",
                           "passed": attrs["source_end_seconds"] >= attrs["source_start_seconds"]})
        if (attrs.get("source_end_seconds") is not None and
                source_observed.get("duration_seconds") is not None):
            checks.append({"rule": "source_time_within_duration",
                           "passed": attrs["source_end_seconds"] <=
                                     source_observed["duration_seconds"]})
        if (attrs.get("target_end_seconds") is not None and
                target_observed.get("duration_seconds") is not None):
            checks.append({"rule": "target_time_within_duration",
                           "passed": attrs["target_end_seconds"] <=
                                     target_observed["duration_seconds"]})
        for attr, observed, key in (
                ("input_sample_rate_hz", source_observed, "sample_rate_hz"),
                ("output_sample_rate_hz", target_observed, "sample_rate_hz"),
                ("input_bit_depth", source_observed, "bit_depth"),
                ("output_bit_depth", target_observed, "bit_depth")):
            if attrs.get(attr) is not None and observed.get(key) is not None:
                checks.append({"rule": f"{attr}_matches_file",
                               "passed": attrs[attr] == observed[key],
                               "declared": attrs[attr], "observed": observed[key]})

        for check in checks:
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
                  "checks": checks}
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


def evaluate_chain(native, objects, root, workflow=None, allow_disconnected=False):
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
    bound, bound_hashes, findings, total = 0, set(), [], 0
    for digest in chain.artefacts:
        relative = objects.get(digest)
        if relative is None:
            findings.append(finding("MISSING_OBJECT", "No file mapping was supplied.", digest, "medium"))
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise InputError("Object path escapes the supplied bundle root.")
        if not path.exists():
            findings.append(finding("MISSING_OBJECT", "The declared object file is missing.", digest, "medium"))
            continue
        if not path.is_file():
            raise InputError("An object path must refer to a regular file.")
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise Unsupported("Object size exceeds the documented local evaluation limits.")
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != digest:
            raise InputError(f"SHA-256 mismatch for declared object {digest}.")
        # The digest was just verified above. Bind the known artefact directly
        # so libevchain does not hash the same file a second time.
        artefact = chain.artefacts[digest]
        artefact.bind_file(str(path))
        artefact._verified_file_hash = actual
        bound += 1
        bound_hashes.add(digest)
    if not chain.artefacts[chain.final_artefact_hash].has_file():
        raise InputError("The final artefact file is missing.")

    pipeline = Pipeline({AudioType: [WavMetadataPass]},
                        {ALL_RELATIONSHIPS: [RelationshipIntegrityPass]}, collect_results)
    observations, relationships = pipeline.eval_chain(chain)
    claim_mismatch = False
    for digest, observation in observations.items():
        if observation is not None:
            claim_mismatch |= bool(observation.get("claim_mismatches", []))
            if observation.get("status") == "parser_failed":
                findings.append({
                    **finding("WAV_PARSER_FAILED",
                              "A non-final WAV could not be parsed; dependent checks are unavailable.",
                              digest, "medium"),
                    **observation,
                })
            else:
                findings.append({
                    **finding("WAV_OBSERVATION",
                              "Measured WAV properties and extracted metadata.", digest),
                    **observation,
                })
    for result in relationships:
        findings.append({**finding("RELATIONSHIP_INTEGRITY_OBSERVATION", "Declared relationship parameters were checked by the cached EvidencePass; separate content analysis may corroborate or contradict the submitted transformation."),
                         **result})
    parser_observations, parser_findings = parse_bound_artefacts(
        native, chain, observations)
    findings.extend(parser_findings)

    integrity, attestation, ai_disclosure, validation, assessment_checks, assessment_findings = assess_axes(
        native, chain, bound_hashes, observations, relationships, parser_observations, workflow)
    findings.extend(assessment_findings)
    axes = empty_axes()
    workflow_assessment = None
    if workflow is not None:
        policy_path = Path(__file__).resolve().parents[1] / "policies" / "workflow_evidence_policy.json"
        completeness_pass = WorkflowCompletenessPass(load_workflow_policy(policy_path))
        axes["completeness"], workflow_assessment, policy_findings = completeness_pass.evaluate(
            native, workflow, bound_hashes, parser_observations)
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
    return axes, findings, workflow_assessment, validation, assessment_checks
