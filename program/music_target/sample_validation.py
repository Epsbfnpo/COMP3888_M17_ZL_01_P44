"""Full-length sample check of every bound audio file in a normal evaluation.

The structure checks (RIFF chunks, FLAC metadata, MP3 frame headers) do not
read the samples, so a float WAV holding NaN or Infinity, or audio a decoder
cannot get through, used to pass the file check. This module reads the whole
stream in bounded blocks and returns one of three outcomes:

- ``valid``: every frame was read and every sample is finite;
- ``invalid``: a non-finite sample was found or the decoder failed part-way;
- ``not_completed``: the scan stopped (caller's time limit, file changed, no
  decoder). This is never a pass.

WAV float samples are checked as stored (float32 or float64), before any
conversion, so a float64 value that only overflows float32 is not reported
as damage. Results are recorded with ``CHECK_VERSION`` and the decoder used.
"""

import time
from pathlib import Path

import numpy as np

from music_target.wav_io import MAX_DECODE_BLOCK_BYTES

CHECK_VERSION = "audio-sample-validation-v1"
RULE = "audio_samples_valid"


def _not_completed(record, reason_code, reason):
    return {**record, "status": "not_completed", "reason_code": reason_code, "reason": reason}


def _non_finite(block, first_frame, rate, record):
    """Count non-finite samples in a (frames, channels) block; remember the first."""
    bad = ~np.isfinite(block)
    count = int(bad.sum())
    if count:
        record["non_finite_samples"] += count
        if record["first_non_finite"] is None:
            frame, channel = (int(value) for value in np.argwhere(bad)[0])
            value = block[frame, channel]
            record["first_non_finite"] = {
                "kind": "NaN" if np.isnan(value) else "+Infinity" if value > 0 else "-Infinity",
                "frame": first_frame + frame, "seconds": round((first_frame + frame) / rate, 6),
                "channel": channel + 1}


def _finish(record):
    if record["non_finite_samples"]:
        # A non-finite sample is a definite failure even if the scan stopped early.
        first = record["first_non_finite"]
        return {**record, "status": "invalid", "reason_code": "non_finite_samples",
                "reason": (f"{record['non_finite_samples']} sample(s) are not finite numbers; the first is "
                           f"{first['kind']} at {first['seconds']:.3f} s, channel {first['channel']}.")}
    return record


def scan_wav(path, descriptor, deadline=None):
    """Read every PCM frame of an inspected WAV; check float samples as stored."""
    path = Path(path)
    float_dtype = ({32: "<f4", 64: "<f8"}.get(descriptor.container_bits)
                   if descriptor.format_kind == "float" else None)
    record = {"check_version": CHECK_VERSION, "decoder": "wav_io",
              "values_checked": f"stored_float{descriptor.container_bits}" if float_dtype else "integer_pcm",
              "frame_count": descriptor.frame_count, "frames_checked": 0,
              "non_finite_samples": 0, "first_non_finite": None}
    stat = path.stat()
    if (stat.st_size != descriptor.file_size
            or (descriptor.mtime_ns and stat.st_mtime_ns != descriptor.mtime_ns)):
        return _not_completed(record, "file_changed_after_inspection", "The file changed after it was inspected.")
    block_frames = max(1, MAX_DECODE_BLOCK_BYTES // descriptor.block_align)
    with path.open("rb") as handle:
        handle.seek(descriptor.data_offset)
        while record["frames_checked"] < descriptor.frame_count:
            if deadline is not None and time.time() >= deadline:
                return _finish(_not_completed(
                    record, "caller_deadline",
                    "The caller's time limit was reached before every sample was read."))
            count = min(block_frames, descriptor.frame_count - record["frames_checked"])
            raw = handle.read(count * descriptor.block_align)
            if len(raw) != count * descriptor.block_align:
                return _finish({**record, "status": "invalid", "reason_code": "truncated_pcm_frames",
                                "reason": "The PCM data ended before the declared number of frames."})
            if float_dtype:
                block = np.frombuffer(raw, dtype=float_dtype).reshape(-1, descriptor.channels)
                _non_finite(block, record["frames_checked"], descriptor.sample_rate, record)
            record["frames_checked"] += count
    return _finish({**record, "status": "valid"})


def scan_decoded(path, deadline=None, block_frames=65536):
    """Decode a FLAC or MP3 with soundfile in blocks and check every sample is finite."""
    record = {"check_version": CHECK_VERSION, "decoder": "soundfile", "values_checked": "decoded_float32",
              "frame_count": None, "frames_checked": 0, "non_finite_samples": 0, "first_non_finite": None}
    try:
        import soundfile
    except ImportError:
        return _not_completed(record, "decoder_unavailable", "soundfile is not installed, so the audio was not decoded.")
    record["decoder"] = f"libsndfile {soundfile.__libsndfile_version__}"
    try:
        audio = soundfile.SoundFile(str(path))
    except (RuntimeError, soundfile.LibsndfileError) as exc:
        # The decoder may not support this encoding; that is not damage.
        return _not_completed(record, "decoder_cannot_open", f"The decoder could not open this file: {exc}")
    try:
        with audio:
            record["frame_count"] = audio.frames if audio.frames >= 0 else None
            for block in audio.blocks(blocksize=block_frames, dtype="float32", always_2d=True):
                if deadline is not None and time.time() >= deadline:
                    return _finish(_not_completed(
                        record, "caller_deadline",
                        "The caller's time limit was reached before the audio was fully decoded."))
                _non_finite(block, record["frames_checked"], audio.samplerate, record)
                record["frames_checked"] += len(block)
    except (RuntimeError, soundfile.LibsndfileError) as exc:
        return _finish({**record, "status": "invalid", "reason_code": "audio_not_decodable",
                        "reason": f"The audio could not be decoded after frame {record['frames_checked']}: {exc}"})
    return _finish({**record, "status": "valid"})


def from_flac_md5(md5):
    """Sample-check outcome of the FLAC MD5 pass, which already decodes every frame.

    A decode failure is already a failed ``flac_md5_matches_decoded_audio``,
    so it returns None here rather than a second failure for the same fault.
    """
    record = {"check_version": CHECK_VERSION, "decoder": "soundfile (FLAC MD5 pass)",
              "values_checked": "decoded_int32", "frame_count": md5.get("declared_total_samples"),
              "frames_checked": md5.get("decoded_frames") or 0, "non_finite_samples": 0,
              "first_non_finite": None}
    if md5.get("status") in ("matched", "mismatched"):
        # Integer FLAC samples are always finite once decoded.
        return {**record, "status": "valid"}
    if md5.get("status") == "failed":
        return None
    return _not_completed(record, md5.get("reason_code") or "not_decoded",
                          md5.get("reason") or "The audio was not decoded.")


def sample_check(digest, container, result):
    """The ``audio_samples_valid`` check row for one file."""
    check = {"rule": RULE, "evidence_hash": digest, "source_pass": "WavMetadataPass", "format": container,
             **{key: value for key, value in result.items() if key != "status"}}
    if result["status"] == "valid":
        return {**check, "passed": True}
    if result["status"] == "invalid":
        return {**check, "passed": False}
    return {**check, "passed": None, "status": "not_completed"}
