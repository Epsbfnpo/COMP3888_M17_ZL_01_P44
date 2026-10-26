"""FLAC and MP3 support: detection, parsing, core checks, C2PA, diagnostics, contract and builder.

Fixtures are generated at run time with soundfile (libsndfile writes FLAC with
an MD5 and MP3 with a Xing/LAME header); FLAC foreign-metadata blocks and
ID3v2 tags are assembled byte by byte, as ``flac --keep-foreign-metadata`` and
the C2PA SDK write them.
"""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np

BASE = Path(__file__).resolve().parents[1]
for path in (BASE, BASE / "vendor", Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))

import run  # noqa: E402
import soundfile  # noqa: E402
from music_target.audio_formats import detect, read_id3_tag  # noqa: E402
from music_target.flac_io import FlacError, UnsupportedFlac, inspect_flac, verify_md5  # noqa: E402
from music_target.mp3_io import Mp3Error, UnsupportedMp3, inspect_mp3  # noqa: E402
from music_target.assessment_passes import _reconcile_with_c2pa_chunk  # noqa: E402
from test_production_documents import TAKE, aaf_bytes, recorder_wav, sound_report, store  # noqa: E402

RATE = 48000


def tone(frames=RATE * 2 + 123, channels=2, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(frames) / RATE
    columns = [0.3 * np.sin(2 * np.pi * (330 + 110 * index) * t) + 0.01 * rng.normal(size=frames)
               for index in range(channels)]
    return np.stack(columns, axis=1)


def write_flac(path, samples=None, subtype="PCM_24"):
    soundfile.write(str(path), tone() if samples is None else samples, RATE, format="FLAC", subtype=subtype)
    return Path(path)


def write_mp3(path, samples=None):
    soundfile.write(str(path), tone() if samples is None else samples, RATE, format="MP3",
                    subtype="MPEG_LAYER_III")
    return Path(path)


def _flac_blocks(data):
    """(offset of first block, [(type, payload)]) of a FLAC byte string."""
    position, blocks = 4, []
    while True:
        last, kind = data[position] & 0x80, data[position] & 0x7F
        length = int.from_bytes(data[position + 1:position + 4], "big")
        blocks.append((kind, data[position + 4:position + 4 + length]))
        position += 4 + length
        if last:
            return blocks, data[position:]


def rebuild_flac(data, extra=(), streaminfo=None, prefix=b""):
    """Re-assemble a FLAC with extra metadata blocks after STREAMINFO (and optional bytes before fLaC)."""
    blocks, audio = _flac_blocks(data)
    if streaminfo is not None:
        blocks[0] = (0, streaminfo)
    blocks = blocks[:1] + list(extra) + blocks[1:]
    out = bytearray(prefix + b"fLaC")
    for index, (kind, payload) in enumerate(blocks):
        out.append(kind | (0x80 if index == len(blocks) - 1 else 0))
        out += len(payload).to_bytes(3, "big") + payload
    return bytes(out) + audio


def riff_blocks(wav_bytes):
    """APPLICATION 'riff' blocks as flac --keep-foreign-metadata writes them."""
    blocks, position = [(2, b"riff" + wav_bytes[:12])], 12
    while position + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[position:position + 4]
        size = struct.unpack("<I", wav_bytes[position + 4:position + 8])[0]
        if chunk_id == b"data":
            blocks.append((2, b"riff" + wav_bytes[position:position + 8]))
        else:
            end = position + 8 + size + (size & 1)
            blocks.append((2, b"riff" + wav_bytes[position:end]))
        position += 8 + size + (size & 1)
    return blocks


def _synchsafe(value):
    return bytes([(value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F])


def id3v24(frames, flags=0):
    body = b"".join(frame_id + _synchsafe(len(payload)) + struct.pack(">H", frame_flags) + payload
                    for frame_id, payload, frame_flags in frames)
    return b"ID3\x04\x00" + bytes([flags]) + _synchsafe(len(body)) + body


def geob(mime=b"application/c2pa", payload=b"c2pa manifest bytes"):
    return (b"GEOB", b"\x03" + mime + b"\x00c2pa\x00c2pa manifest store\x00" + payload, 0)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def write(self, name, data):
        path = self.folder / name
        path.write_bytes(data)
        return path

    def test_containers_are_decided_by_content(self):
        flac = write_flac(self.folder / "a.flac").read_bytes()
        mp3 = write_mp3(self.folder / "a.mp3").read_bytes()
        cases = {"WAV": b"RIFF" + struct.pack("<I", 4) + b"WAVE", "FLAC": flac, "MP3": mp3,
                 "Ogg": b"OggS" + bytes(40), "RF64": b"RF64" + bytes(40),
                 "AIFF": b"FORM" + bytes(4) + b"AIFF" + bytes(20)}
        for expected, data in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(detect(self.write(f"{expected}.wav", data)).container, expected)
        self.assertIsNone(detect(self.write("junk.bin", b"not audio at all" * 4)).container)

    def test_id3_tag_before_flac_marker_is_flac_not_mp3(self):
        # The C2PA SDK stores a FLAC manifest in an ID3v2 tag before fLaC.
        flac = write_flac(self.folder / "b.flac").read_bytes()
        path = self.write("signed.flac", id3v24([geob()]) + flac)
        detected = detect(path)
        self.assertEqual(detected.container, "FLAC")
        self.assertEqual(len(detected.leading_id3), 1)
        self.assertGreater(detected.audio_offset, 0)

    def test_junk_after_id3_before_first_mpeg_frame_is_reported(self):
        mp3 = write_mp3(self.folder / "c.mp3").read_bytes()
        path = self.write("junk.mp3", id3v24([(b"TIT2", b"\x03Title", 0)]) + b"\x00" * 37 + mp3)
        detected = detect(path)
        self.assertEqual(detected.container, "MP3")
        self.assertIn("junk_before_first_mpeg_frame:37", detected.warnings)

    def test_mpeg_layer_two_is_recognised_as_unsupported(self):
        frame = b"\xff\xfd\x90\x00" + bytes(400)
        path = self.write("layer2.mp3", frame)
        self.assertEqual(detect(path).container, "MPEG Layer I/II")
        with self.assertRaises(UnsupportedMp3):
            inspect_mp3(path)

    def test_id3_frames_are_read_with_bounds(self):
        frames = [(b"TIT2", b"\x03My title", 0), (b"TXXX", b"\x03SOURCE\x00Studio", 0), geob(),
                  (b"TPE1", b"\x00compressed", 0x0008)]
        path = self.write("tags.mp3", id3v24(frames) + write_mp3(self.folder / "d.mp3").read_bytes())
        tag = read_id3_tag(path, detect(path).leading_id3[0])
        by_id = {frame["id"]: frame for frame in tag["frames"]}
        self.assertEqual(by_id["TIT2"]["value"], "My title")
        self.assertEqual((by_id["TXXX"]["description"], by_id["TXXX"]["value"]), ("SOURCE", "Studio"))
        self.assertEqual(by_id["GEOB"]["mime_type"], "application/c2pa")
        self.assertTrue(tag["c2pa_manifest_present"])
        self.assertEqual(by_id["TPE1"]["not_decoded"], "compressed")
        self.assertTrue(any("compressed" in warning for warning in tag["warnings"]))


class FlacParserTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_streaminfo_and_md5_match_the_encoded_audio(self):
        samples = tone(channels=3)
        path = write_flac(self.folder / "x.flac", samples)
        descriptor = inspect_flac(path)
        self.assertEqual((descriptor.sample_rate, descriptor.channels, descriptor.bits_per_sample),
                         (RATE, 3, 24))
        self.assertEqual(descriptor.total_samples, len(samples))
        self.assertAlmostEqual(descriptor.duration_seconds, len(samples) / RATE)
        self.assertIsNotNone(descriptor.md5)
        result = verify_md5(path, descriptor)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["decoded_frames"], len(samples))

    def test_md5_is_computed_for_16_and_8_bit_audio(self):
        for subtype in ("PCM_16", "PCM_S8"):
            with self.subTest(subtype=subtype):
                path = write_flac(self.folder / f"{subtype}.flac", tone(frames=9000), subtype)
                self.assertEqual(verify_md5(path, inspect_flac(path))["status"], "matched")

    def test_changed_md5_is_a_mismatch_and_missing_md5_is_unavailable(self):
        data = write_flac(self.folder / "y.flac").read_bytes()
        blocks, _ = _flac_blocks(data)
        info = bytearray(blocks[0][1])
        changed = bytes(info[:18]) + bytes(16 * [0x11])
        missing = bytes(info[:18]) + bytes(16)
        for name, streaminfo, status in (("changed", changed, "mismatched"), ("missing", missing, "unavailable")):
            with self.subTest(name=name):
                path = self.folder / f"{name}.flac"
                path.write_bytes(rebuild_flac(data, streaminfo=streaminfo))
                self.assertEqual(verify_md5(path, inspect_flac(path))["status"], status)

    def test_foreign_riff_chunks_are_recovered(self):
        wav = Path(recorder_wav(self.folder, *TAKE))
        wav = self.folder / "objects" / f"{wav}.wav"
        samples, _ = soundfile.read(str(wav), dtype="int32")
        flac = write_flac(self.folder / "plain.flac", samples.astype(np.float64) / 2 ** 31).read_bytes()
        path = self.folder / "kept.flac"
        path.write_bytes(rebuild_flac(flac, riff_blocks(wav.read_bytes())))
        descriptor = inspect_flac(path)
        chunks = dict(descriptor.foreign_chunks)
        self.assertEqual(set(chunks), {b"fmt ", b"bext", b"iXML", b"data"})
        self.assertIn(b"<SCENE>2</SCENE>", chunks[b"iXML"])

    def test_malformed_and_out_of_range_flac(self):
        data = write_flac(self.folder / "z.flac").read_bytes()
        blocks, audio = _flac_blocks(data)
        cases = {
            "missing_streaminfo": (b"fLaC" + bytes([0x81]) + (4).to_bytes(3, "big") + b"pad!", FlacError),
            "truncated_metadata": (b"fLaC" + bytes([0x80]) + (34).to_bytes(3, "big") + bytes(10), FlacError),
            "invalid_metadata_block": (rebuild_flac(data, [(127, b"")]), FlacError),
        }
        info = bytearray(blocks[0][1])
        packed = int.from_bytes(info[10:18], "big")
        packed = (packed & ((1 << 44) - 1)) | (500000 << 44)
        info[10:18] = packed.to_bytes(8, "big")
        cases["unsupported_channel_or_rate"] = (rebuild_flac(data, streaminfo=bytes(info)), UnsupportedFlac)
        for reason, (raw, error) in cases.items():
            with self.subTest(reason=reason):
                path = self.folder / f"{reason}.flac"
                path.write_bytes(raw)
                with self.assertRaises(error) as caught:
                    inspect_flac(path)
                self.assertEqual(caught.exception.reason_code, reason)


class Mp3ParserTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_exact_length_from_xing_and_lame_headers(self):
        samples = tone()
        path = write_mp3(self.folder / "a.mp3", samples)
        descriptor = inspect_mp3(path)
        self.assertEqual(descriptor.total_samples, len(samples))
        self.assertEqual(descriptor.total_samples, soundfile.info(str(path)).frames)
        self.assertEqual((descriptor.mpeg_version, descriptor.sample_rate, descriptor.channels), ("1", RATE, 2))
        self.assertIn(descriptor.info_tag, ("Xing", "Info"))
        self.assertTrue(descriptor.encoder)

    def without_info_frame(self, name="b.mp3"):
        path = write_mp3(self.folder / name)
        data = path.read_bytes()
        descriptor = inspect_mp3(path)
        start = descriptor.audio_offset
        frame = int.from_bytes(data[start:start + 4], "big")
        from music_target.audio_formats import mpeg_header
        size = mpeg_header(data, start).frame_bytes
        stripped = self.folder / ("stripped-" + name)
        stripped.write_bytes(data[:start] + data[start + size:])
        return stripped, frame

    def test_without_gapless_information_the_duration_is_only_estimated(self):
        stripped, _ = self.without_info_frame()
        descriptor = inspect_mp3(stripped)
        self.assertIsNone(descriptor.info_tag)
        self.assertIsNone(descriptor.duration_seconds)
        self.assertIsNone(descriptor.total_samples)
        self.assertGreater(descriptor.estimated_duration_seconds, 2.0)
        self.assertIn("duration_estimated_without_gapless_information", descriptor.warnings)

    def test_lost_sync_and_truncation_are_reported_and_make_the_length_inexact(self):
        path = write_mp3(self.folder / "c.mp3")
        data = path.read_bytes()
        middle = len(data) // 2
        broken = self.folder / "broken.mp3"
        broken.write_bytes(data[:middle] + b"\x00" * 50 + data[middle:])
        descriptor = inspect_mp3(broken)
        self.assertTrue(any(item.startswith("sync_lost:") for item in descriptor.warnings))
        self.assertIsNone(descriptor.duration_seconds)
        truncated = self.folder / "truncated.mp3"
        truncated.write_bytes(data[:-100])
        descriptor = inspect_mp3(truncated)
        self.assertTrue(any(item.startswith("truncated_final_frame:") for item in descriptor.warnings))
        self.assertIsNone(descriptor.duration_seconds)

    def test_trailing_id3v1_and_apev2_tags_are_not_audio(self):
        data = write_mp3(self.folder / "d.mp3").read_bytes()
        ape_footer = b"APETAGEX" + struct.pack("<IIII", 2000, 32, 0, 0) + bytes(8)
        path = self.folder / "tagged.mp3"
        path.write_bytes(data + ape_footer + b"TAG" + b"Title".ljust(30, b"\x00") + bytes(95))
        descriptor = inspect_mp3(path)
        self.assertEqual(set(descriptor.trailing_tags), {"id3v1", "apev2"})
        self.assertEqual(descriptor.trailing_tags["id3v1"]["title"], "Title")
        self.assertEqual(descriptor.total_samples, inspect_mp3(self.folder / "d.mp3").total_samples)

    def test_not_mp3_is_rejected(self):
        path = self.folder / "e.mp3"
        path.write_bytes(b"\x00" * 200)
        with self.assertRaises(Mp3Error):
            inspect_mp3(path)


class EvaluatorTests(unittest.TestCase):
    """FLAC and MP3 through the real evaluator."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / "objects").mkdir()

    def put(self, path, suffix):
        return store(self.folder, Path(path).read_bytes(), suffix)

    def request(self, nodes, final, workflow="recorded_single_performance_to_final", modifiers=()):
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["workflow"].update(workflow_id=workflow, modifiers=list(modifiers))
        artefacts, objects = [], {}
        for digest, kind, suffix, attributes, evidence in nodes:
            artefacts.append({"artefact_hash": digest, "artefact_type": kind, "attributes": attributes,
                              "evidence": [{"hash": source, "relationship_type": relation, "attributes": {}}
                                           for source, relation in evidence]})
            objects[digest] = f"objects/{digest}.{suffix}"
        request["chain"].update(final_artefact_hash=final, artefacts=artefacts)
        request["objects"] = objects
        return request

    def evaluate(self, request):
        response = run.evaluate(copy.deepcopy(request), self.folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        return response

    @staticmethod
    def observation(response, digest):
        return next(row for row in response["findings"]
                    if row["code"] in ("AUDIO_OBSERVATION", "WAV_OBSERVATION") and row.get("evidence_hash") == digest)

    def flac_take(self, keep_metadata=True):
        wav_digest = recorder_wav(self.folder, *TAKE)
        wav = self.folder / "objects" / f"{wav_digest}.wav"
        samples, _ = soundfile.read(str(wav), dtype="int32")
        plain = write_flac(self.folder / "take.flac", samples.astype(np.float64) / 2 ** 31).read_bytes()
        data = rebuild_flac(plain, riff_blocks(wav.read_bytes())) if keep_metadata else plain
        wav.unlink()
        return store(self.folder, data, "flac"), wav_digest

    def test_flac_take_supports_recorder_checks_like_wav(self):
        take, _ = self.flac_take()
        master = self.put(write_flac(self.folder / "master.flac", tone(seed=3)), "flac")
        report = store(self.folder, sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:43:00", '"MixL"', '"MixR"', '"Boom"', '""']]), "csv")
        aaf = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), [
            ("24Y12M18", "2T01.WAV", "2", "01", TAKE[4], "2024-12-18", 31783.5, 25)]), "aaf")
        response = self.evaluate(self.request([
            (take, "audio/raw-take", "flac", {"technical": {"sample_rate_hz": RATE, "bit_depth": 24, "channels": 3,
                                                            "duration_seconds": 2.0}}, []),
            (report, "text/sound-report", "csv", {}, []), (aaf, "project/aaf", "aaf", {}, []),
            (master, "audio/master", "flac", {}, [(take, "derived_from"), (report, "input_to"), (aaf, "input_to")])],
            master))
        observation = self.observation(response, take)
        self.assertEqual(observation["code"], "AUDIO_OBSERVATION")
        self.assertEqual(observation["audio_format"], "FLAC")
        self.assertEqual(observation["recorder_metadata"]["file_name"], "2T01.WAV")
        self.assertEqual(observation["embedded_metadata"]["broadcast_extension"]["origination_date"], "2024-12-18")
        checks = {item["rule"]: item["passed"] for item in observation["checks"]}
        self.assertTrue(all(checks.values()), checks)
        self.assertIn("flac_md5_matches_decoded_audio", checks)
        recorder = [item for item in response["assessment_checks"]["cross_evidence"]
                    if item.get("evidence_tier") == "recorder_metadata"]
        self.assertTrue(recorder and all(item["passed"] for item in recorder), recorder)
        self.assertEqual(response["axes"]["integrity"]["status"], "consistent")
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["numeric_release"], "released")

    def test_flac_recording_without_kept_metadata_is_noted_not_contradicted(self):
        take, _ = self.flac_take(keep_metadata=False)
        master = self.put(write_flac(self.folder / "master.flac", tone(seed=3)), "flac")
        response = self.evaluate(self.request([
            (take, "audio/raw-take", "flac", {}, []),
            (master, "audio/master", "flac", {}, [(take, "derived_from")])], master))
        notes = [row for row in response["findings"] if row["code"] == "FLAC_RECORDER_METADATA_ABSENT"]
        self.assertEqual([row["evidence_hash"] for row in notes], [take])  # the master is not a recording
        self.assertIs(notes[0]["scoring_eligible"], False)
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["contradiction_count"], 0)

    def test_flac_md5_mismatch_is_a_file_integrity_contradiction(self):
        data = write_flac(self.folder / "m.flac").read_bytes()
        blocks, _ = _flac_blocks(data)
        changed = rebuild_flac(data, streaminfo=blocks[0][1][:18] + bytes(16 * [0x22]))
        master = store(self.folder, changed, "flac")
        response = self.evaluate(self.request([(master, "audio/master", "flac", {}, [])], master,
                                              workflow="mastering_service_only"))
        check = next(item for item in response["assessment_checks"]["bound_file_integrity"]
                     if item["rule"] == "flac_md5_matches_decoded_audio")
        self.assertIs(check["passed"], False)
        self.assertEqual(check["reason_code"], "md5_mismatch")
        self.assertIn("FILE_INTEGRITY_CONTRADICTION", {row["code"] for row in response["findings"]})
        self.assertIn("does not match the MD5", response["axes"]["integrity"]["reasoning"] or
                      json.dumps(response["assessment_checks"]["score_explanations"]))

    def test_wrong_streaminfo_sample_count_cannot_pass_the_md5_check(self):
        # libsndfile decodes exactly the STREAMINFO count, so a decoded-versus-
        # declared count is not an independent check with this decoder. A count
        # set too low truncates the decode and the MD5 fails; one set too high
        # makes the decode fail. Neither can pass as matched.
        data = write_flac(self.folder / "count.flac").read_bytes()
        blocks, _ = _flac_blocks(data)
        outcomes = {}
        for delta in (-1000, 1000):
            info = bytearray(blocks[0][1])
            field = int.from_bytes(info[13:18], "big")
            total = field & ((1 << 36) - 1)
            info[13:18] = ((field & ~((1 << 36) - 1)) | (total + delta)).to_bytes(5, "big")
            path = self.folder / f"count{delta}.flac"
            path.write_bytes(rebuild_flac(data, streaminfo=bytes(info)))
            outcomes[delta] = verify_md5(path, inspect_flac(path))["status"]
        self.assertEqual(outcomes, {-1000: "mismatched", 1000: "failed"})

    def test_undated_audio_edge_is_not_comparable_rather_than_absent(self):
        from music_target.assessment_passes import declared_chronology
        dated = {"observed": {"format": "WAV"},
                 "embedded_metadata": {"broadcast_extension": {"origination_date": "2024-12-18",
                                                               "origination_time": "10:00:00"}}}
        native = {"artefacts": [
            {"artefact_hash": "mp3", "evidence": []},
            {"artefact_hash": "flac", "evidence": []},
            {"artefact_hash": "wav", "evidence": [{"hash": "mp3", "relationship_type": "derived_from"},
                                                  {"hash": "flac", "relationship_type": "derived_from"},
                                                  {"hash": "doc", "relationship_type": "derived_from"}]}]}
        observations = {"wav": dated, "mp3": {"observed": {"format": "MP3"}},
                        "flac": {"observed": {"format": "FLAC"}, "embedded_metadata": {}}}
        rows, findings = declared_chronology(native, observations, {"wav", "mp3", "flac", "doc"})
        self.assertEqual([(row["source_hash"], row["status"], row["reason_code"], row["undated"]) for row in rows],
                         [("mp3", "not_comparable", "format_has_no_bwf", ["source"]),
                          ("flac", "not_comparable", "bwf_not_kept", ["source"])])
        self.assertTrue(all(row["source_newer_than_target"] is None and row["scoring_eligible"] is False
                            for row in rows))
        self.assertEqual(findings, [])

    def test_mp3_unmeasurable_fields_are_unavailable_not_contradicted(self):
        stripped, _ = Mp3ParserTests.without_info_frame(self, "final.mp3")
        final = self.put(stripped, "mp3")
        response = self.evaluate(self.request([
            (final, "audio/master", "mp3", {"creation_method": "recorded",
                                            "technical": {"bit_depth": 16, "duration_seconds": 2.0,
                                                          "sample_rate_hz": RATE, "channels": 2}}, [])],
            final, workflow="mastering_service_only"))
        observation = self.observation(response, final)
        checks = {item["rule"]: item for item in observation["checks"]}
        for rule in ("technical.bit_depth_matches_observed", "technical.duration_seconds_matches_observed"):
            self.assertIsNone(checks[rule]["passed"])
            self.assertEqual(checks[rule]["status"], "unavailable")
        self.assertTrue(checks["technical.sample_rate_hz_matches_observed"]["passed"])
        # Technical claims are scored in declaration consistency, as for WAV; the
        # unmeasurable ones stay unavailable facts and never count as votes.
        assessed = response["assessment_checks"]
        self.assertFalse(any(item["rule"].startswith("technical.") for item in assessed["bound_file_integrity"]))
        self.assertIn("technical.bit_depth_matches_observed",
                      {item["rule"] for item in assessed["declared_file_properties"]})
        facts = {fact["identity"].get("property"): fact for fact in assessed["declaration_scored_facts"]}
        self.assertEqual((facts["bit_depth"]["status"], facts["sample_rate_hz"]["passed"]), ("unavailable", True))
        self.assertEqual(observation["recorder_metadata"]["not_applicable"], True)
        limited = next(row for row in response["findings"] if row["code"] == "AUDIO_FORMAT_LIMITED_CHECKS")
        families = {item["check_family"]: item["limitation"] for item in limited["limited_checks"]}
        self.assertEqual(families, {"declared_bit_depth": "format", "declared_duration": "file",
                                    "decoded_sample_integrity": "program_scope", "recorder_metadata": "format",
                                    "bwf_chronology": "format", "tag_dates": "program_scope",
                                    "lossless_content": "format"})
        self.assertTrue(all(item["status"] == "unavailable" for item in limited["limited_checks"]))
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["contradiction_count"], 0)

    def test_mp3_with_gapless_header_compares_its_exact_duration(self):
        samples = tone()
        final = self.put(write_mp3(self.folder / "exact.mp3", samples), "mp3")
        response = self.evaluate(self.request([
            (final, "audio/master", "mp3", {"technical": {"duration_seconds": len(samples) / RATE}}, [])],
            final, workflow="mastering_service_only"))
        check = next(item for item in self.observation(response, final)["checks"]
                     if item["rule"] == "technical.duration_seconds_matches_observed")
        self.assertIs(check["passed"], True)

    def test_flac_named_wav_is_evaluated_as_flac(self):
        final = self.put(write_flac(self.folder / "named.flac"), "wav")
        response = self.evaluate(self.request([(final, "audio/master", "wav", {}, [])], final,
                                              workflow="mastering_service_only"))
        self.assertEqual(self.observation(response, final)["observed"]["format"], "FLAC")

    def test_unparseable_non_final_flac_is_isolated(self):
        broken = store(self.folder, b"fLaC" + bytes(100), "flac")
        master = self.put(write_flac(self.folder / "ok.flac"), "flac")
        response = self.evaluate(self.request([
            (broken, "audio/raw-track", "flac", {}, []),
            (master, "audio/master", "flac", {}, [(broken, "derived_from")])], master,
            workflow="mastering_service_only"))
        failed = next(row for row in response["findings"] if row["code"] == "AUDIO_PARSER_FAILED")
        self.assertEqual(failed["evidence_hash"], broken)
        self.assertEqual(failed["checks"][0]["rule"], "audio_parser_available")
        unreadable = next(item for item in response["assessment_checks"]["bound_file_integrity"]
                          if item["rule"] == "audio_file_readable")
        self.assertEqual((unreadable["passed"], unreadable["format"]), (False, "FLAC"))
        self.assertTrue(response["assessment_checks"]["integrity_decision"][0]["contradiction_present"])

    def test_mp3_with_lost_sync_fails_the_file_check_but_trailing_junk_does_not(self):
        data = write_mp3(self.folder / "source.mp3").read_bytes()
        middle = len(data) // 2
        for payload, damaged in ((data[:middle] + b"\x00" * 50 + data[middle:], True),
                                 (data + b"\x00" * 50, False)):
            with self.subTest(damaged=damaged):
                final = store(self.folder, payload, "mp3")
                response = self.evaluate(self.request([(final, "audio/master", "mp3", {}, [])], final,
                                                      workflow="mastering_service_only"))
                rows = [item for item in response["assessment_checks"]["bound_file_integrity"]
                        if item["rule"] == "audio_stream_complete"]
                if damaged:
                    self.assertIs(rows[0]["passed"], False)
                    self.assertTrue(rows[0]["damage"][0].startswith("sync_lost:"))
                else:
                    self.assertEqual(rows, [])

    def test_time_limit_and_unsupported_formats_are_not_file_damage(self):
        from music_target.engine import _readability_checks
        timeout = Mp3Error("The caller's time limit was reached.", "caller_deadline")
        self.assertEqual(_readability_checks("d" * 64, "MP3", "invalid", timeout, "timeout"), [])
        self.assertEqual(_readability_checks("d" * 64, "MP3", "unsupported",
                                             UnsupportedMp3("Layer II", "unsupported_mpeg_layer"), "x"), [])
        self.assertEqual(_readability_checks("d" * 64, "WAV", "invalid", OSError("read error"), "x"), [])
        [check] = _readability_checks("d" * 64, "MP3", "invalid", Mp3Error("No frame.", "no_mpeg_frames"), "x")
        self.assertIs(check["passed"], False)

    def test_unknown_mp3_duration_skips_range_checks_without_crashing(self):
        stripped, _ = Mp3ParserTests.without_info_frame(self, "cue.mp3")
        final = self.put(stripped, "mp3")
        cue = store(self.folder, json.dumps({"cues": [{"cue_id": "c1", "asset_hash": final, "start_seconds": 0.0,
                                                       "end_seconds": 99.0}]}).encode(), "json")
        response = self.evaluate(self.request([
            (cue, "text/cue-sheet", "json", {}, []),
            (final, "audio/master", "mp3", {}, [(cue, "input_to")])], final, workflow="mastering_service_only"))
        rules = {item["rule"] for item in response["assessment_checks"]["cross_evidence"]}
        self.assertNotIn("cue_sheet_range_within_audio", rules)


@unittest.skipUnless(importlib.util.find_spec("c2pa"), "c2pa-python is required")
class C2paInFlacAndMp3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / "objects").mkdir()

    def signed(self, writer, mime):
        from test_c2pa_integrity import _sign
        source = writer(self.folder / ("source." + mime.split("/")[1]))
        target = self.folder / ("signed." + mime.split("/")[1])
        _sign(str(source), str(target), mime_type=mime)
        return target

    def test_ai_label_in_flac_and_mp3_contradicts_a_recorded_declaration(self):
        for writer, mime, suffix in ((write_flac, "audio/flac", "flac"), (write_mp3, "audio/mpeg", "mp3")):
            with self.subTest(mime=mime):
                path = self.signed(writer, mime)
                self.assertEqual(detect(path).container, "FLAC" if suffix == "flac" else "MP3")
                digest = store(self.folder, path.read_bytes(), suffix)
                request = EvaluatorTests.request(self, [(digest, "audio/master", suffix,
                                                         {"creation_method": "recorded"}, [])], digest,
                                                 workflow="mastering_service_only")
                response = run.evaluate(request, self.folder, "native")
                self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
                self.assertEqual(response["validation"]["trust_status"], "untrusted")
                cross = {item["rule"]: item["passed"] for item in response["assessment_checks"]["cross_evidence"]}
                self.assertIs(cross["c2pa_ai_source_type_matches_creation_method"], False)
                record = response["assessment_checks"]["c2pa_attestation_records"][0]
                self.assertEqual((record["reader_succeeded"], record["manifest_present"]), (True, True))
                observation = next(row for row in response["findings"] if row["code"] == "AUDIO_OBSERVATION")
                self.assertIs(observation["embedded_metadata"]["c2pa_container_present"], True)

    def test_manifest_location_decides_absent_versus_unreadable(self):
        failed = {"sdk_available": True, "reader_succeeded": False, "present": False, "warnings": []}
        without = _reconcile_with_c2pa_chunk(dict(failed), {"embedded_metadata": {"c2pa_container_present": False}})
        self.assertEqual((without["reader_succeeded"], without["present"]), (True, False))
        empty = {"sdk_available": True, "reader_succeeded": True, "present": False, "warnings": []}
        damaged = _reconcile_with_c2pa_chunk(dict(empty), {"embedded_metadata": {"c2pa_container_present": True}})
        self.assertIs(damaged["reader_succeeded"], False)
        self.assertIn("manifest location", damaged["warnings"][-1])


REQUIRED_AUDIO = ("librosa", "scipy")


@unittest.skipIf(any(importlib.util.find_spec(name) is None for name in REQUIRED_AUDIO)
                 and os.environ.get("REQUIRE_AUDIO_BACKENDS") != "1", "audio diagnostics backends are optional")
class DiagnosticReadTests(unittest.TestCase):
    def test_flac_decodes_to_the_same_samples_as_wav_and_mp3_close_to_them(self):
        from music_target.audio_diagnostics.io import read_audio
        folder = Path(tempfile.mkdtemp())
        samples = tone()
        wav = folder / "a.wav"
        soundfile.write(str(wav), samples, RATE, subtype="PCM_24")
        flac = write_flac(folder / "a.flac", samples)
        mp3 = write_mp3(folder / "a.mp3", samples)
        reference = read_audio(wav, 0.5, 1.5, 22050)
        same = read_audio(flac, 0.5, 1.5, 22050)
        lossy = read_audio(mp3, 0.5, 1.5, 22050)
        # Same PCM; the WAV reader and libsndfile scale integers to float32 in a
        # different order, so values agree to float32 rounding.
        np.testing.assert_allclose(reference["samples"], same["samples"], rtol=0, atol=1e-6)
        self.assertEqual(lossy["range"], reference["range"])
        correlation = np.corrcoef(reference["samples"], lossy["samples"])[0, 1]
        self.assertGreater(correlation, 0.99)


class Mp3DecodedLengthTests(unittest.TestCase):
    """libsndfile estimates the length of an MP3 without a Xing header and stops decoding there."""

    @staticmethod
    def without_info_frame(path, prefix=b""):
        from music_target.audio_formats import mpeg_header
        data = Path(path).read_bytes()
        start = inspect_mp3(path).audio_offset
        stripped = Path(str(path) + ".stripped.mp3")
        stripped.write_bytes(prefix + data[start + mpeg_header(data, start).frame_bytes:])
        return stripped

    def test_large_id3_tag_on_constant_bitrate_mp3_decodes_in_full(self):
        # Lumina's MP3s: constant bitrate, a ~21 KB ID3 tag (the C2PA manifest), no Xing header.
        from music_target.audio_diagnostics.io import _open
        folder = Path(tempfile.mkdtemp())
        cbr = folder / "cbr.mp3"
        soundfile.write(str(cbr), tone(), RATE, format="MP3", bitrate_mode="CONSTANT", compression_level=0.5)
        path = self.without_info_frame(cbr, id3v24([geob(payload=bytes(range(256)) * 80)]))
        descriptor, blocks = _open(path)
        decoded = sum(len(block) for block in blocks(0, descriptor.frame_count))
        self.assertEqual(descriptor.frame_count, inspect_mp3(path).audio_frames * 1152)
        self.assertEqual(decoded, descriptor.frame_count)
        self.assertNotEqual(soundfile.info(str(path)).frames, descriptor.frame_count)

    def test_variable_bitrate_mp3_without_xing_header_is_not_analysed_truncated(self):
        from music_target.audio_diagnostics.io import AnalysisUnavailable, read_audio
        folder = Path(tempfile.mkdtemp())
        path = self.without_info_frame(write_mp3(folder / "vbr.mp3"))
        self.assertEqual(inspect_mp3(path).bitrate_mode, "VBR")
        with self.assertRaises(AnalysisUnavailable) as caught:
            read_audio(path, None, None, 22050)
        self.assertEqual(caught.exception.code, "decoder_length_mismatch")


class PublicContractTests(unittest.TestCase):
    def test_public_bundles_accept_flac_and_mpeg_audio(self):
        from music_target.contract import public_bundle_request
        folder = Path(tempfile.mkdtemp())
        (folder / "objects").mkdir()
        for writer, media_type, suffix in ((write_flac, "audio/flac", "flac"), (write_mp3, "audio/mpeg", "mp3")):
            with self.subTest(media_type=media_type):
                data = writer(folder / ("x." + suffix)).read_bytes()
                digest = store(folder, data, suffix)
                bundle = {"case_id": "PUBLIC-" + suffix, "final-artefact": digest,
                          "artefacts": [{"hash": digest, "hash-method": "sha256", "type": media_type,
                                         "evidence": []}]}
                request = public_bundle_request(bundle)
                self.assertEqual(request["objects"][digest], f"objects/{digest}.{suffix}")
                response = run.evaluate(request, folder, "csec")
                self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
                from music_target.contract import validate
                validate(response, "scoring-response.schema.json")


class BenchmarkEncodingTests(unittest.TestCase):
    def test_false_match_encoding_keeps_flac_samples_and_mp3_length(self):
        sys.path.insert(0, str(BASE / "benchmarks"))
        import false_match
        folder = Path(tempfile.mkdtemp())
        samples = tone()
        source = folder / "clip.wav"
        soundfile.write(str(source), samples, RATE, subtype="PCM_16")
        flac = false_match.encode(source, folder, "flac")
        mp3 = false_match.encode(source, folder, "mp3")
        np.testing.assert_array_equal(soundfile.read(str(flac))[0], soundfile.read(str(source))[0])
        self.assertEqual(detect(mp3).container, "MP3")
        self.assertEqual(inspect_mp3(mp3).total_samples, len(samples))


class BuilderProbeTests(unittest.TestCase):
    def test_probe_reports_only_what_each_format_carries(self):
        import wav_probe
        folder = Path(tempfile.mkdtemp())
        samples = tone()
        flac = wav_probe.audio_technical(write_flac(folder / "a.flac", samples))
        self.assertEqual(flac, {"sample_rate_hz": RATE, "channels": 2, "bit_depth": 24,
                                "duration_seconds": round(len(samples) / RATE, 6)})
        mp3 = wav_probe.audio_technical(write_mp3(folder / "a.mp3", samples))
        self.assertEqual(mp3, {"sample_rate_hz": RATE, "channels": 2,
                               "duration_seconds": round(len(samples) / RATE, 6)})
        (folder / "junk.wav").write_bytes(b"fLaC" + bytes(20))
        self.assertEqual(wav_probe.audio_technical(folder / "junk.wav"), {})

    def test_builder_inspection_reads_audio_by_content(self):
        import threading
        import builder_operations
        folder = Path(tempfile.mkdtemp())
        path = write_flac(folder / "named-as-audio.bin")
        result = builder_operations.inspect_files([str(path)], lambda message: None, threading.Event())[0]
        self.assertEqual(result["technical"]["bit_depth"], 24)
        self.assertEqual(result["hash"], hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
