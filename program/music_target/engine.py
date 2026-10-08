"""Local file checks, libevchain passes, and explicitly scoped axis results."""

import dataclasses
import hashlib
import math
import struct
from pathlib import Path

from libevchain.evidence_chain import EvidenceChain
from libevchain.pipeline import ArtefactPass, EvidencePass, Pipeline
from libevchain.types import AudioType, ALL_RELATIONSHIPS

from music_target import domain
from music_target.assessment_passes import assess_axes, parse_bound_artefacts
from music_target.wav_parser import parse_wav_file
from music_target.workflow_policy import WorkflowCompletenessPass, load_workflow_policy

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


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


def strict_pcm_check(path):
    """Check RIFF bounds and uncompressed integer PCM framing before extraction.

    Optional metadata is not authenticated. No claim about who created the audio.
    """
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise InputError("Expected a RIFF/WAVE file.")
    if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise InputError("RIFF declared size differs from actual file length.")
    pos, fmt, payload, seen = 12, None, None, set()
    while pos < len(data):
        if pos + 8 > len(data):
            raise InputError("Truncated WAV chunk header.")
        name = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        start, end = pos + 8, pos + 8 + size
        if end + size % 2 > len(data):
            raise InputError("WAV chunk exceeds the RIFF container.")
        if name in (b"fmt ", b"data"):
            if name in seen:
                raise InputError("Duplicate mandatory WAV chunk.")
            seen.add(name)
        if name == b"fmt ":
            if size < 16:
                raise InputError("Truncated WAV fmt chunk.")
            fmt = struct.unpack_from("<HHIIHH", data, start)
        elif name == b"data":
            if fmt is None:
                raise InputError("The data chunk must follow fmt in this baseline.")
            payload = size
        pos = end + size % 2
    if fmt is None or payload is None:
        raise InputError("A fmt chunk and a data chunk are required.")
    code, channels, rate, byte_rate, align, bits = fmt
    if code != 1 or bits not in (8, 16, 24, 32):
        raise Unsupported("Only uncompressed integer PCM WAV (8/16/24/32-bit) is supported.")
    if not (1 <= channels <= 32 and 1 <= rate <= 384000):
        raise Unsupported("Channel count or sample rate is outside the baseline limits.")
    if align != channels * (bits // 8) or byte_rate != rate * align:
        raise InputError("Inconsistent PCM block alignment or byte rate.")
    if payload == 0 or payload % align:
        raise InputError("Empty or truncated PCM sample data.")
    return {"duration_seconds": payload / byte_rate, "sample_rate_hz": rate,
            "channels": channels, "bit_depth": bits, "format": "WAV",
            "codec_subtype": "PCM", "file_size_bytes": len(data)}


class WavMetadataPass(ArtefactPass):
    @staticmethod
    def evaluate(artefact):
        if not artefact.has_file():
            return None, None, ["No verified file is bound to this artefact."]
        path = Path(artefact.get_file())
        observed = strict_pcm_check(path)
        parsed = dataclasses.asdict(parse_wav_file(str(path)))
        parsed.pop("source_path", None)
        claim = artefact._attributes.get("technical", {})
        mismatches = []
        for key, value in claim.items():
            if value is None:
                continue
            measured = observed[key]
            same = (math.isclose(value, measured, rel_tol=0, abs_tol=0.002)
                    if key == "duration_seconds" else value == measured)
            if not same:
                mismatches.append({"field": key, "declared": value, "observed": measured})
        return None, {"observed": observed, "embedded_metadata": parsed,
                      "claim_mismatches": mismatches}, [
            "PCM framing was checked; submission claims were compared with measured file properties.",
            "Embedded dates, credits and software tags are unauthenticated metadata.",
        ]


class MusicRelationshipPass(EvidencePass):
    @staticmethod
    def evaluate(evidence):
        parent, child = evidence.get_evidence_artefact(), evidence.get_result_artefact()
        source_result = target_result = None
        if parent.artefact_type.artefact_type_name.startswith("audio"):
            _, source_result = parent.get_pass_result(WavMetadataPass)
        if child.artefact_type.artefact_type_name.startswith("audio"):
            _, target_result = child.get_pass_result(WavMetadataPass)
        result = {"source_hash": parent.artefact_hash, "target_hash": child.artefact_hash,
                  "relationship": evidence.relationship_type.relationship_type_name,
                  "content_relationship_verified": None}
        if source_result and target_result:
            result["duration_delta_seconds"] = (
                target_result["observed"]["duration_seconds"]
                - source_result["observed"]["duration_seconds"])
        return None, result, [
            "Endpoint roles were validated. Duration difference is diagnostic only.",
            "Scoped edit, comp, stem and mix claims receive separate content checks; mastering receives continuity corroboration only.",
        ]


def collect_results(final_artefact):
    # Follow evidence once per node: shared ancestors must not be counted twice.
    visited, observations, relationships = set(), {}, []
    pending = [final_artefact]
    while pending:
        artefact = pending.pop()
        if artefact.artefact_hash in visited:
            continue
        visited.add(artefact.artefact_hash)
        if artefact.artefact_type.artefact_type_name.startswith("audio"):
            _, observations[artefact.artefact_hash] = artefact.get_pass_result(WavMetadataPass)
        for edge in artefact.get_evidence():
            _, result = edge.get_pass_result(MusicRelationshipPass)
            relationships.append(result)
            pending.append(edge.get_evidence_artefact())
    return observations, relationships


def evaluate_chain(native, objects, root, workflow=None):
    chain = EvidenceChain.from_dict(native)
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
    if reachable != set(chain.artefacts):
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
        chain.bind_file(str(path))
        bound += 1
        bound_hashes.add(digest)
    if not chain.artefacts[chain.final_artefact_hash].has_file():
        raise InputError("The final artefact file is missing.")

    pipeline = Pipeline({AudioType: [WavMetadataPass]},
                        {ALL_RELATIONSHIPS: [MusicRelationshipPass]}, collect_results)
    observations, relationships = pipeline.eval_chain(chain)
    claim_mismatch = False
    for digest, observation in observations.items():
        if observation is not None:
            claim_mismatch |= bool(observation["claim_mismatches"])
            findings.append({**finding("WAV_OBSERVATION", "Measured WAV properties and extracted metadata.", digest),
                             **observation})
            if observation["claim_mismatches"]:
                findings.append(finding("TECHNICAL_CLAIM_MISMATCH",
                                        "Submitted technical attributes disagree with the actual file.", digest, "medium"))
    for result in relationships:
        findings.append({**finding("RELATIONSHIP_OBSERVATION", "Relationship diagnostics; scoped edit, comp, stem and mix claims receive content analysis, while mastering receives continuity corroboration."),
                         **result})
    parser_observations, parser_findings = parse_bound_artefacts(
        native, chain, observations)
    findings.extend(parser_findings)

    integrity, attestation, ai_disclosure, validation, assessment_checks, assessment_findings = assess_axes(
        native, chain, bound_hashes, observations, parser_observations, workflow)
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
