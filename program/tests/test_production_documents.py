"""Sound reports and AAF edits checked against recorder BWF/iXML metadata."""

import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "vendor"))
import run
from music_target.production_documents import (aaf_checks, parse_aaf_file, parse_sound_report_file,
                                               sound_report_checks)
from music_target.recorder_metadata import recorder_fields
from music_target.wav_io import inspect_wav
from music_target.wav_parser import parse_wav_file

RATE = 48000


def _chunk(chunk_id, payload):
    return chunk_id + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def _fixed(text, size):
    return text.encode("ascii")[:size].ljust(size, b"\0")


def recorder_wav(folder, file_name, tape, scene, take, tracks, start_seconds, date, seconds=2.0,
                 ixml_prefix=b"", seed=0):
    """A poly BWF like a field recorder writes: bext + iXML + 24-bit PCM."""
    description = "\r\n".join([f"sTAKE={take}", f"sSCENE={scene}", f"sFILENAME={file_name}", f"sTAPE={tape}"] +
                              [f"sTRK{index}={name}" for index, name in tracks.items()])
    reference = round(start_seconds * RATE)
    bext = (_fixed(description, 256) + _fixed("Recorder", 32) + _fixed("REF", 32) + _fixed(date, 10) +
            _fixed("08:00:00", 8) + struct.pack("<IIH", reference & 0xFFFFFFFF, reference >> 32, 1) +
            b"\0" * 64 + b"\0" * 10 + b"\0" * 180 + b"A=PCM,F=48000,W=24\r\n")
    track_xml = "".join(f"<TRACK><CHANNEL_INDEX>{index}</CHANNEL_INDEX><NAME>{name}</NAME></TRACK>"
                        for index, name in tracks.items())
    ixml = ixml_prefix + (f"<?xml version=\"1.0\"?><BWFXML><SCENE>{scene}</SCENE><TAKE>{take}</TAKE>"
                          f"<TAPE>{tape}</TAPE><SPEED><TIMECODE_RATE>25/1</TIMECODE_RATE>"
                          f"<TIMECODE_FLAG>NDF</TIMECODE_FLAG></SPEED><TRACK_LIST>{track_xml}</TRACK_LIST>"
                          "</BWFXML>").encode()
    channels = len(tracks)
    rng = np.random.default_rng(seed)
    samples = (rng.normal(scale=0.1, size=(round(seconds * RATE), channels)) * (2 ** 23 - 1)).astype("<i4")
    pcm = b"".join(int(value).to_bytes(3, "little", signed=True) for value in samples.ravel())
    fmt = struct.pack("<HHIIHH", 1, channels, RATE, RATE * channels * 3, channels * 3, 24)
    body = b"WAVE" + _chunk(b"fmt ", fmt) + _chunk(b"bext", bext) + _chunk(b"iXML", ixml) + _chunk(b"data", pcm)
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    digest = hashlib.sha256(data).hexdigest()
    (Path(folder) / "objects").mkdir(exist_ok=True)
    (Path(folder) / "objects" / f"{digest}.wav").write_bytes(data)
    return digest


def store(folder, data, suffix):
    digest = hashlib.sha256(data).hexdigest()
    (Path(folder) / "objects").mkdir(exist_ok=True)
    (Path(folder) / "objects" / f"{digest}.{suffix}").write_bytes(data)
    return digest


def sound_report(date, rows, frame_rate="25 ND"):
    lines = ["SOUND REPORT", "", 'Project:,"FIXTURE",', f'Date:,"{date}",', f'Frame Rate:,"{frame_rate}",', ",",
             "File Name,Scene,Take,Length,Start TC,Trk 1,Trk 2,Trk 3,Notes", ","]
    lines += [",".join(row) for row in rows]
    return ("\n".join(lines) + "\n").encode()


def aaf_bytes(folder, references):
    """Write an AAF whose master mobs cut pieces of recorder tapes."""
    import aaf2
    path = Path(folder) / "edit.aaf"
    with aaf2.open(str(path), "w") as handle:
        for tape_name, file_name, scene, take, tracks, date, start_seconds, frames in references:
            tape = handle.create.SourceMob(tape_name)
            tape.descriptor = handle.create.TapeDescriptor()
            tape.create_timecode_slot(25, 25)
            tape_slot = tape.create_empty_slot(25, media_kind="sound")
            tape_slot.segment.length = 25 * 86400
            handle.content.mobs.append(tape)
            file_mob = handle.create.SourceMob("")
            file_mob.descriptor = handle.create.ImportDescriptor()
            file_slot = file_mob.create_timeline_slot(25)
            file_slot.segment = tape.create_source_clip(slot_id=tape_slot.slot_id, start=round(start_seconds * 25),
                                                        length=frames, media_kind="sound")
            handle.content.mobs.append(file_mob)
            master = handle.create.MasterMob(f"{scene}/{take}")
            master.create_timeline_slot(25).segment = file_mob.create_source_clip(
                slot_id=file_slot.slot_id, start=0, length=frames, media_kind="sound")
            for key, value in {"TapeID": file_name, "Scene": scene, "Take": take, "Shoot Date": date,
                               **{f"TRK{index}": name for index, name in tracks.items()}}.items():
                master.comments[key] = value
            handle.content.mobs.append(master)
    return path.read_bytes()


TRACKS = {1: "MixL", 2: "MixR", 3: "Boom"}
# 2T01 recorded 18 Dec 2024 from 08:49:43:00 for two seconds.
TAKE = ("2T01.WAV", "24Y12M18", "2", "01", TRACKS, 31783.0, "2024-12-18")


class RecorderMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def fields(self, digest):
        path = self.folder / "objects" / f"{digest}.wav"
        descriptor = inspect_wav(path)
        import dataclasses
        return recorder_fields(path, descriptor, dataclasses.asdict(parse_wav_file(str(path), descriptor)))

    def test_bext_and_ixml_fields_are_read_from_the_bound_file(self):
        fields = self.fields(recorder_wav(self.folder, *TAKE))
        self.assertTrue(fields["present"])
        self.assertEqual((fields["file_name"], fields["tape"], fields["scene"], fields["take"]),
                         ("2T01.WAV", "24Y12M18", "2", "01"))
        self.assertEqual(fields["tracks"], {"1": "MixL", "2": "MixR", "3": "Boom"})
        self.assertAlmostEqual(fields["start_timecode_seconds"], 31783.0)
        self.assertEqual(fields["origination_date"], "2024-12-18")
        self.assertEqual(fields["sources"], ["bext", "iXML"])

    def test_ixml_with_a_dtd_is_not_parsed(self):
        doctype = b'<!DOCTYPE x [<!ENTITY a "aaaa">]>'
        fields = self.fields(recorder_wav(self.folder, *TAKE, ixml_prefix=doctype))
        self.assertIn("DTD", fields["warnings"][0])
        self.assertEqual(fields["scene"], "2")  # still available from bext


class ProductionDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.take = recorder_wav(self.folder, *TAKE)

    def observations(self, *digests):
        import dataclasses
        result = {}
        for digest in digests:
            path = self.folder / "objects" / f"{digest}.wav"
            descriptor = inspect_wav(path)
            parsed = dataclasses.asdict(parse_wav_file(str(path), descriptor))
            result[digest] = {"recorder_metadata": recorder_fields(path, descriptor, parsed),
                              "observed": {"duration_seconds": descriptor.duration_seconds}}
        return result

    def report_checks(self, data, *digests):
        path = self.folder / "report.csv"
        path.write_bytes(data)
        return {item["rule"]: item for item in
                sound_report_checks(parse_sound_report_file(path), "r" * 64, self.observations(*digests))}

    def test_honest_report_agrees_with_recorder_metadata(self):
        checks = self.report_checks(sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:43:00", '"MixL"', '"MixR"', '"Boom"', '""']]), self.take)
        for rule in ("sound_report_start_timecode_matches_recorder", "sound_report_scene_take_matches_recorder",
                     "sound_report_track_names_match_recorder"):
            self.assertTrue(checks[rule]["passed"], checks[rule])
            self.assertEqual(checks[rule]["evidence_tier"], "recorder_metadata")
        # Declared length vs measured duration is cheap to fake: not independent.
        self.assertEqual(checks["sound_report_length_matches_audio"]["evidence_tier"], "submitter_document")

    def test_shifted_report_timecode_is_a_recorder_contradiction(self):
        checks = self.report_checks(sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:45:00", '"MixL"', '"MixR"', '"Boom"', '""']]), self.take)
        self.assertFalse(checks["sound_report_start_timecode_matches_recorder"]["passed"])

    def test_same_file_name_from_another_day_is_a_different_take(self):
        checks = self.report_checks(sound_report("17/12/24", [
            ["2T01.WAV", "2", "01", "00:00:30", "14:00:00:00", '"MixL"', '"MixR"', '"Boom"', '""']]), self.take)
        self.assertEqual(checks, {})

    def test_drop_frame_report_timecode_is_not_compared(self):
        checks = self.report_checks(sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:43:00", '"MixL"', '"MixR"', '"Boom"', '""']],
            frame_rate="29.97 DF"), self.take)
        self.assertEqual(checks["sound_report_start_timecode_matches_recorder"]["status"], "unavailable")

    def test_aaf_source_ranges_are_checked_against_the_recorded_timecode(self):
        inside = ("24Y12M18", "2T01.WAV", "2", "01", TRACKS, "2024-12-18", 31783.5, 25)
        outside = ("24Y12M18", "2T01.WAV", "2", "01", TRACKS, "2024-12-18", 31790.0, 25)
        for reference, expected in ((inside, True), (outside, False)):
            folder = Path(tempfile.mkdtemp(dir=self.folder))
            (folder / "edit.aaf").write_bytes(aaf_bytes(folder, [reference]))
            parsed = parse_aaf_file(folder / "edit.aaf")
            checks = {item["rule"]: item for item in aaf_checks(parsed, "a" * 64, self.observations(self.take))}
            self.assertIs(checks["aaf_source_range_within_recorded_timecode"]["passed"], expected)
            self.assertTrue(checks["aaf_scene_take_matches_recorder"]["passed"])
            self.assertTrue(checks["aaf_shoot_date_matches_recording"]["passed"])
            self.assertEqual(checks["aaf_source_range_within_recorded_timecode"]["evidence_tier"],
                             "recorder_metadata")

    def request(self, documents):
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["case_id"] = "PRODUCTION-DOCUMENTS"
        request["workflow"]["workflow_id"] = "recorded_single_performance_to_final"
        request["workflow"]["modifiers"] = []
        master = recorder_wav(self.folder, "MASTER.WAV", "MASTER", "0", "1", {1: "L", 2: "R"}, 0.0,
                              "2025-01-30", seed=1)
        artefacts = [{"artefact_hash": self.take, "artefact_type": "audio/raw-take", "attributes": {},
                      "evidence": []}]
        objects = {self.take: f"objects/{self.take}.wav", master: f"objects/{master}.wav"}
        for digest, kind, suffix in documents:
            artefacts.append({"artefact_hash": digest, "artefact_type": kind, "attributes": {}, "evidence": []})
            objects[digest] = f"objects/{digest}.{suffix}"
        artefacts.append({"artefact_hash": master, "artefact_type": "audio/master", "attributes": {},
                          "evidence": [{"hash": self.take, "relationship_type": "derived_from", "attributes": {}}] +
                                      [{"hash": digest, "relationship_type": "input_to", "attributes": {}}
                                       for digest, _, _ in documents]})
        request["chain"]["final_artefact_hash"] = master
        request["chain"]["artefacts"] = artefacts
        request["objects"] = objects
        return request

    def test_recorder_corroboration_releases_integrity_end_to_end(self):
        report = store(self.folder, sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:43:00", '"MixL"', '"MixR"', '"Boom"', '""']]), "csv")
        aaf = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), [
            ("24Y12M18", "2T01.WAV", "2", "01", TRACKS, "2024-12-18", 31783.5, 25)]), "aaf")
        response = run.evaluate(self.request([(report, "text/sound-report", "csv"),
                                              (aaf, "project/aaf", "aaf")]), self.folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        cross = response["assessment_checks"]["cross_evidence"]
        recorder = [item for item in cross if item["evidence_tier"] == "recorder_metadata"]
        self.assertTrue(recorder and all(item["passed"] for item in recorder), recorder)
        self.assertIsNotNone(response["axes"]["integrity"]["value"])
        self.assertIn("Qualifying cross-evidence was assessed", response["axes"]["integrity"]["reasoning"])

    def test_timecode_mismatch_is_explained_not_capped(self):
        report = store(self.folder, sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:45:00", '"MixL"', '"MixR"', '"Boom"', '""']]), "csv")
        response = run.evaluate(self.request([(report, "text/sound-report", "csv")]), self.folder, "native")
        statement = ("Sound report start timecode for 2T01.WAV is 08:49:45:00, but the recorder wrote "
                     "08:49:43:00 into the file (2.00 s apart).")
        explanation = next(row for row in response["assessment_checks"]["score_explanations"]
                           if row["axis"] == "integrity")
        self.assertIn(statement, explanation["statements"])
        self.assertIn(statement, response["axes"]["integrity"]["reasoning"])
        self.assertFalse(explanation["cap_applied"])  # an ordinary contradiction, not a severe one
        self.assertIsNotNone(response["axes"]["integrity"]["value"])
        self.assertGreater(response["axes"]["integrity"]["value"], 0.25)

    def test_timecode_formatting(self):
        from music_target.score_explanations import timecode
        self.assertEqual(timecode(31783.0, 25), "08:49:43:00")
        self.assertEqual(timecode(50699.36, 25), "14:04:59:09")

    def test_documents_without_recorder_match_keep_integrity_withheld(self):
        # The report names a take the submission does not include: nothing
        # independent was compared, so the value stays withheld.
        report = store(self.folder, sound_report("18/12/24", [
            ["9T09.WAV", "9", "09", "00:00:02", "10:00:00:00", '"MixL"', '"MixR"', '"Boom"', '""']]), "csv")
        response = run.evaluate(self.request([(report, "text/sound-report", "csv")]), self.folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        self.assertIsNone(response["axes"]["integrity"]["value"])


if __name__ == "__main__":
    unittest.main()
