"""Exact sample identity between lossless audio files.

Two files hold the same audio when their decoded integer samples are equal at
the same sample rate, channel count and bit depth. The digest is SHA-256 over
the samples as FLAC defines them for its MD5: signed little-endian integers of
ceil(bits/8) bytes, channels interleaved (8-bit WAV is unsigned and is centred
first; EXTENSIBLE samples are left-justified in their container and are shifted
back to their valid bits). Float WAV has no integer representation and is not
compared. Identical samples describe an exact content relation; they do not
show who made either file or which came first.
"""

import hashlib
import time

import numpy as np

SCHEME = "pcm-int-le-interleaved-sha256-v1"


def _unavailable(reason_code, reason):
    return {"status": "unavailable", "reason_code": reason_code, "reason": reason}


def _container_ints(raw, container_bits):
    if container_bits == 8:
        return np.frombuffer(raw, dtype=np.uint8).astype(np.int32) - 128
    if container_bits == 16:
        return np.frombuffer(raw, dtype="<i2").astype(np.int32)
    if container_bits == 24:
        triples = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
        return np.where(values & 0x800000, values - (1 << 24), values)
    return np.frombuffer(raw, dtype="<i4").astype(np.int32)


def wav_sample_digest(path, descriptor, deadline=None, block_frames=65536):
    """Canonical sample SHA-256 of an integer-PCM WAV; ``deadline`` is a ``time.time()`` value."""
    if descriptor.format_kind != "int":
        return _unavailable("float_samples", "Float samples have no integer representation to compare.")
    bits, container = descriptor.valid_bits, descriptor.container_bits
    width = (bits + 7) // 8
    remaining = descriptor.frame_count * descriptor.block_align
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        handle.seek(descriptor.data_offset)
        while remaining:
            if deadline is not None and time.time() >= deadline:
                return _unavailable("caller_deadline",
                                    "The caller's time limit was reached before every sample was read.")
            raw = handle.read(min(remaining, block_frames * descriptor.block_align))
            if not raw:
                return _unavailable("truncated_data", "The data chunk ended before its declared size.")
            remaining -= len(raw)
            if container == bits and bits > 8:
                digest.update(raw)  # already the canonical bytes
                continue
            values = _container_ints(raw, container) >> (container - bits)
            digest.update(values.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :width].tobytes())
    return {"status": "computed", "sha256": digest.hexdigest(), "frames": descriptor.frame_count, "bits": bits}


def compare(source, target):
    """Compare two digest records from ``wav_sample_digest`` or ``flac_sample_digest``."""
    for side, record in (("source", source), ("target", target)):
        if record.get("status") != "computed":
            return {"status": "unavailable", "reason_code": record.get("reason_code"),
                    "reason": f"The {side} samples could not be digested: {record.get('reason')}"}
    identical = (source["sha256"], source["frames"]) == (target["sha256"], target["frames"])
    return {"status": "identical" if identical else "different", "scheme": SCHEME,
            "source_sha256": source["sha256"], "target_sha256": target["sha256"],
            "source_frames": source["frames"], "target_frames": target["frames"]}
