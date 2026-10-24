"""Declared format conversions: the same audio stored in another file format.

A ``derived_from`` edge with ``transformation: "format_conversion"`` claims
that only the file format changed (for example a WAV take saved as FLAC, or a
WAV master encoded as MP3). The claim is checked against both files: sample
rate, channels, bit depth between lossless files, and length. These checks
compare a declaration with measured properties, so they are scored in
declaration consistency like other relationship claims. Between lossless
integer files the decoded samples are also compared exactly; that result is
report-only, because identical samples describe the content, not its origin.
"""

from music_target import sample_identity
from music_target.flac_io import flac_sample_digest

FORMAT_CONVERSION = "format_conversion"
# An MP3 with an exact (gapless) length can still differ by up to one MPEG
# frame from its source, depending on the encoder.
LOSSY_FRAME_SAMPLES = 1152


def _lossless(observed):
    return observed.get("format") in ("WAV", "FLAC")


def _integer_bits(observed):
    """Bit depth of the integer samples, or None (float WAV, MP3)."""
    if observed.get("format") == "WAV":
        return observed.get("valid_bits_per_sample") if observed.get("sample_format") == "int" else None
    if observed.get("format") == "FLAC":
        return observed.get("bit_depth")
    return None


def _direction(source, target):
    return f"{'lossless' if _lossless(source) else 'lossy'}_to_{'lossless' if _lossless(target) else 'lossy'}"


def _digest(artefact, observation, deadline):
    """Sample digest of one endpoint, computed once per artefact.

    A FLAC whose MD5 was verified already has the digest from that decode.
    """
    cached = getattr(artefact, "_sample_digest", None)
    if cached is not None:
        return cached
    verified = observation.get("flac_md5") or {}
    if verified.get("sample_sha256"):
        result = {"status": "computed", "sha256": verified["sample_sha256"],
                  "frames": verified["decoded_frames"], "bits": observation["observed"]["bit_depth"]}
    elif observation["observed"].get("format") == "FLAC":
        result = flac_sample_digest(artefact.get_file(), artefact._audio_descriptor, deadline)
    else:
        result = sample_identity.wav_sample_digest(artefact.get_file(), artefact._wav_descriptor, deadline)
    artefact._sample_digest = result
    return result


def conversion_checks(source, target, source_result, target_result, relationship_type, deadline=None):
    """(scored checks, report record) for one edge declared as a format conversion.

    ``source_result`` and ``target_result`` are the endpoints' WavMetadataPass results.
    """
    source_observed = (source_result or {}).get("observed") or {}
    target_observed = (target_result or {}).get("observed") or {}
    record = {"relationship_type": relationship_type, "source_format": source_observed.get("format"),
              "target_format": target_observed.get("format"), "scoring_eligible": False,
              "integrity_contribution": None}
    if relationship_type != "derived_from":
        return [], {**record, "status": "ignored", "reason_code": "requires_derived_from",
                    "reason": "A format conversion is declared on derived_from edges only."}
    if not source_observed or not target_observed:
        return [], {**record, "status": "unavailable", "reason_code": "endpoint_not_parsed",
                    "reason": "Both endpoints must be readable audio files."}
    record.update(status="assessed", direction=_direction(source_observed, target_observed))
    checks, unavailable = [], []
    for key in ("sample_rate_hz", "channels"):
        declared, observed = source_observed.get(key), target_observed.get(key)
        if declared is not None and observed is not None:
            checks.append({"rule": f"format_conversion_{key.removesuffix('_hz')}_preserved",
                           "passed": declared == observed, "declared": declared, "observed": observed})
    source_bits, target_bits = _integer_bits(source_observed), _integer_bits(target_observed)
    if source_bits is not None and target_bits is not None:
        checks.append({"rule": "format_conversion_bit_depth_preserved", "passed": source_bits == target_bits,
                       "declared": source_bits, "observed": target_bits})
    source_seconds, target_seconds = source_observed.get("duration_seconds"), target_observed.get("duration_seconds")
    rate = target_observed.get("sample_rate_hz") or source_observed.get("sample_rate_hz")
    if source_seconds is None or target_seconds is None or not rate:
        unavailable.append({"rule": "format_conversion_duration_preserved", "passed": None, "status": "unavailable",
                            "reason_code": "length_not_exact",
                            "reason": "An endpoint has no exact length (for example an MP3 without a Xing/LAME "
                                      "header), so the length was not compared."})
    else:
        lossless = _lossless(source_observed) and _lossless(target_observed)
        tolerance = 0.5 if lossless else LOSSY_FRAME_SAMPLES
        difference = abs(source_seconds - target_seconds) * rate
        checks.append({"rule": "format_conversion_duration_preserved", "passed": difference <= tolerance,
                       "declared": source_seconds, "observed": target_seconds,
                       "difference_samples": round(difference, 1), "tolerance_samples": tolerance})
    record["checks"] = [dict(item) for item in checks] + unavailable
    record["sample_identity"] = _sample_identity(source, target, source_result, target_result,
                                                 source_bits, target_bits, deadline)
    return checks, record


def _sample_identity(source, target, source_result, target_result, source_bits, target_bits, deadline):
    source_observed, target_observed = source_result["observed"], target_result["observed"]
    if not (_lossless(source_observed) and _lossless(target_observed)):
        return {"status": "not_comparable", "reason_code": "lossy_endpoint",
                "reason": "A lossy file cannot hold the exact samples of its source."}
    if source_bits is None or target_bits is None:
        return {"status": "not_comparable", "reason_code": "float_samples",
                "reason": "Float samples have no integer representation to compare."}
    if (source_observed.get("sample_rate_hz"), source_observed.get("channels"), source_bits) != (
            target_observed.get("sample_rate_hz"), target_observed.get("channels"), target_bits):
        return {"status": "not_comparable", "reason_code": "parameters_differ",
                "reason": "Sample rate, channels or bit depth differ, so the samples cannot be equal."}
    return sample_identity.compare(_digest(source, source_result, deadline),
                                   _digest(target, target_result, deadline))
