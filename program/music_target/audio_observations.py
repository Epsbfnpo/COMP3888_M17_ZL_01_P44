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

# The evaluator is designed and calibrated for WAV/BWF. FLAC and MP3 receive
# the checks implemented today; every other check family is reported as not
# assessed, never as a contradiction and never in a score. Each entry says,
# per format, whether the family is implemented ("implemented"), implemented
# only when the file carries the information ("if_in_file"), not implemented
# by this program ("not_implemented_for_format", a program limitation), or
# impossible for the format ("format_cannot_carry").
FORMAT_SCOPE = {
    "hash_binding": {"WAV": "implemented", "FLAC": "implemented", "MP3": "implemented"},
    "container_structure": {"WAV": "implemented", "FLAC": "implemented", "MP3": "implemented"},
    "declared_technical_values": {"WAV": "implemented", "FLAC": "implemented", "MP3": "implemented"},
    "declared_bit_depth": {"WAV": "implemented", "FLAC": "implemented", "MP3": "format_cannot_carry"},
    "declared_duration": {"WAV": "implemented", "FLAC": "if_in_file", "MP3": "if_in_file"},
    "decoded_sample_integrity": {"WAV": "implemented", "FLAC": "if_in_file", "MP3": "not_implemented_for_format"},
    "recorder_metadata": {"WAV": "implemented", "FLAC": "if_in_file", "MP3": "format_cannot_carry"},
    "bwf_chronology": {"WAV": "implemented", "FLAC": "if_in_file", "MP3": "format_cannot_carry"},
    "tag_dates": {"WAV": "not_applicable", "FLAC": "not_implemented_for_format",
                  "MP3": "not_implemented_for_format"},
    "embedded_c2pa": {"WAV": "implemented", "FLAC": "implemented", "MP3": "implemented"},
    "lossless_content": {"WAV": "implemented", "FLAC": "implemented", "MP3": "format_cannot_carry"},
}
# Which family each declarable technical field belongs to.
TECHNICAL_FAMILY = {"sample_rate_hz": "declared_technical_values", "channels": "declared_technical_values",
                    "file_size_bytes": "declared_technical_values", "format": "declared_technical_values",
                    "codec_subtype": "declared_technical_values", "bit_depth": "declared_bit_depth",
                    "duration_seconds": "declared_duration"}
LIMITATION_OF = {"not_implemented_for_format": "program_scope", "format_cannot_carry": "format",
                 "not_in_file": "file"}
_REASONS = {
    ("declared_bit_depth", "MP3"): "MP3 has no bit depth; declared bit depths are not compared.",
    ("declared_duration", "FLAC"): "STREAMINFO does not record the total number of samples, so the length "
                                   "is unknown and duration claims are not compared.",
    ("declared_duration", "MP3"): "The MP3 has no Xing/Info header with encoder delay and padding, so its "
                                  "exact length is unknown; duration comparisons are not made.",
    ("decoded_sample_integrity", "FLAC"): "The encoder stored no MD5 of the audio in STREAMINFO, so the decoded "
                                          "samples cannot be checked against it.",
    ("decoded_sample_integrity", "MP3"): "This evaluator walks the MP3 frame headers but does not decode or "
                                         "verify the audio samples; that check is implemented for WAV and "
                                         "FLAC only.",
    ("recorder_metadata", "FLAC"): "This FLAC carries no WAV chunks (bext/iXML), so sound-report and AAF "
                                   "comparisons with recorder fields are not possible.",
    ("recorder_metadata", "MP3"): "MP3 has no standard field for recorder metadata (bext/iXML), so "
                                  "sound-report and AAF comparisons with recorder fields are not possible.",
    ("bwf_chronology", "FLAC"): "Without kept BWF chunks there is no origination date to compare.",
    ("bwf_chronology", "MP3"): "MP3 carries no BWF origination date to compare.",
    ("tag_dates", "FLAC"): "Dates in Vorbis comments are not read; this evaluator compares BWF dates only.",
    ("tag_dates", "MP3"): "Dates in ID3 tags (TDRC) are not read; this evaluator compares BWF dates only.",
    ("lossless_content", "MP3"): "MP3 is lossy: decoded samples differ from the original recording, so samples "
                                 "are never compared exactly and audio diagnostics compare an approximation.",
}


def limited_checks(container, *, exact_duration=True, md5_stored=True, recorder_kept=True):
    """Check families not assessed on this FLAC or MP3 file, each as an unavailable row with its reason.

    The keyword arguments say whether the file carries what an ``if_in_file``
    family needs. Families implemented for the format are not listed.
    """
    present = {"declared_duration": exact_duration, "decoded_sample_integrity": md5_stored,
               "recorder_metadata": recorder_kept, "bwf_chronology": recorder_kept}
    rows = []
    for family, scope in FORMAT_SCOPE.items():
        status = scope[container]
        if status == "if_in_file":
            if present.get(family, True):
                continue
            status = "not_in_file"
        if status in ("implemented", "not_applicable"):
            continue
        rows.append({"check_family": family, "status": "unavailable", "reason_code": status,
                     "limitation": LIMITATION_OF[status], "reason": _REASONS[(family, container)]})
    return rows


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


MP3_RECORDER = {"present": False, "bwf_kind": None, "warnings": [], "not_applicable": True,
                "reason": "MP3 has no standard field for recorder metadata."}
