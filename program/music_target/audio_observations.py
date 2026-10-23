"""FLAC and MP3 observations in the same shape as the WAV ones.

``observed`` holds the measured technical properties that submitter claims are
compared with, and ``embedded_metadata`` the metadata found in the file. Keys
that WAV consumers already read (``broadcast_extension``, ``info``,
``recorder_metadata``) keep their meaning, so the chronology, sound-report and
AAF checks work on FLAC takes unchanged. A property a format cannot carry is
``None``, and the comparisons that need it are reported as unavailable.
"""

import dataclasses

from music_target.recorder_metadata import recorder_fields_from_chunks
from music_target.wav_parser import _parse_bext, _parse_list_info

# Check families a lossy format cannot support, and why (reported, not scored).
MP3_LIMITED_CHECKS = (
    ("bit_depth", "MP3 has no bit depth; declared bit depths are not compared."),
    ("recorder_metadata", "MP3 has no standard field for recorder metadata (bext/iXML), so sound-report "
                          "and AAF comparisons with recorder fields, and chronology checks on BWF origination "
                          "dates, are not possible."),
    ("lossless_content", "MP3 is lossy: decoded samples differ from the original recording, so audio "
                         "diagnostics compare an approximation of the source."),
)
MP3_DURATION_LIMIT = ("exact_duration", "The MP3 has no Xing/Info header with encoder delay and padding, "
                                        "so its exact length is unknown; duration comparisons are not made.")


def flac_observed(descriptor):
    return {
        "duration_seconds": descriptor.duration_seconds,
        "sample_rate_hz": descriptor.sample_rate,
        "channels": descriptor.channels,
        "bit_depth": descriptor.bits_per_sample,
        "format": "FLAC",
        "codec_subtype": "FLAC",
        "sample_format": "int",
        "file_size_bytes": descriptor.file_size,
        "lossless": True,
        "total_samples": descriptor.total_samples,
        "flac_warnings": list(descriptor.warnings),
    }


def flac_embedded(descriptor):
    """Metadata summary plus the WAV chunks a FLAC kept from its source."""
    chunks = dict(descriptor.foreign_chunks)
    bext = _parse_bext(chunks[b"bext"]) if b"bext" in chunks else None
    info = _parse_list_info(chunks[b"LIST"]) if b"LIST" in chunks else None
    return {
        "container": "FLAC",
        **descriptor.metadata,
        "broadcast_extension": dataclasses.asdict(bext) if bext else None,
        "info": info or {},
        "id3v2_tags": list(descriptor.id3_tags),
        "c2pa_container_present": descriptor.c2pa_container_present,
        "warnings": list(descriptor.warnings),
    }


def flac_recorder(descriptor, embedded):
    """Recorder metadata from kept bext/iXML chunks; absent when the encoder dropped them."""
    chunks = dict(descriptor.foreign_chunks)
    if b"bext" not in chunks and b"iXML" not in chunks:
        return {"present": False, "bwf_kind": None, "warnings": [],
                "foreign_metadata_kept": False}
    duration = descriptor.duration_seconds
    result = recorder_fields_from_chunks(embedded.get("broadcast_extension"), chunks.get(b"iXML"),
                                         descriptor.sample_rate, duration)
    return {**result, "foreign_metadata_kept": True}


def mp3_observed(descriptor):
    codec = "MPEG" + descriptor.mpeg_version.replace(".", "") + "_LAYER3"
    return {
        "duration_seconds": descriptor.duration_seconds,
        "duration_exact": descriptor.duration_seconds is not None,
        "estimated_duration_seconds": descriptor.estimated_duration_seconds,
        "sample_rate_hz": descriptor.sample_rate,
        "channels": descriptor.channels,
        "bit_depth": None,
        "format": "MP3",
        "codec_subtype": codec,
        "file_size_bytes": descriptor.file_size,
        "lossless": False,
        "total_samples": descriptor.total_samples,
        "channel_mode": descriptor.channel_mode,
        "bitrate_mode": descriptor.bitrate_mode,
        "nominal_bitrate_kbps": descriptor.nominal_bitrate_kbps,
        "average_bitrate_kbps": descriptor.average_bitrate_kbps,
        "mp3_warnings": list(descriptor.warnings),
    }


def mp3_embedded(descriptor):
    return {
        "container": "MP3",
        "mpeg_version": descriptor.mpeg_version,
        "layer": descriptor.layer,
        "audio_frames": descriptor.audio_frames,
        "samples_per_frame": descriptor.samples_per_frame,
        "info_tag": descriptor.info_tag,
        "info_tag_frames": descriptor.info_tag_frames,
        "encoder": descriptor.encoder,
        "encoder_delay_samples": descriptor.encoder_delay,
        "encoder_padding_samples": descriptor.encoder_padding,
        "lame_tag_crc_valid": descriptor.lame_tag_crc_valid,
        "id3v2_tags": list(descriptor.id3_tags),
        "trailing_tags": descriptor.trailing_tags,
        "c2pa_container_present": descriptor.c2pa_container_present,
        "broadcast_extension": None,
        "info": {},
        "warnings": list(descriptor.warnings),
    }


def mp3_limited_checks(descriptor):
    """Check families not run on this MP3, with reasons."""
    limits = list(MP3_LIMITED_CHECKS)
    if descriptor.duration_seconds is None:
        limits.append(MP3_DURATION_LIMIT)
    return [{"check_family": name, "reason": reason} for name, reason in limits]


MP3_RECORDER = {"present": False, "bwf_kind": None, "warnings": [], "not_applicable": True,
                "reason": "MP3 has no standard field for recorder metadata."}
