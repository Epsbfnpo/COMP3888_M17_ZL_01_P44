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
                 ixml_prefix=b"", seed=0, ixml_timestamp=None):
    """A poly BWF like a field recorder writes: bext + iXML + 24-bit PCM.

    ``ixml_timestamp`` is (samples since midnight, sample rate) for the iXML
    SPEED block; real recorders write it equal to bext TimeReference.
    """
    description = "\r\n".join([f"sTAKE={take}", f"sSCENE={scene}", f"sFILENAME={file_name}", f"sTAPE={tape}"] +
                              [f"sTRK{index}={name}" for index, name in tracks.items()])
    reference = round(start_seconds * RATE)
    bext = (_fixed(description, 256) + _fixed("Recorder", 32) + _fixed("REF", 32) + _fixed(date, 10) +
            _fixed("08:00:00", 8) + struct.pack("<IIH", reference & 0xFFFFFFFF, reference >> 32, 1) +
            b"\0" * 64 + b"\0" * 10 + b"\0" * 180 + b"A=PCM,F=48000,W=24\r\n")
    track_xml = "".join(f"<TRACK><CHANNEL_INDEX>{index}</CHANNEL_INDEX><NAME>{name}</NAME></TRACK>"
                        for index, name in tracks.items())
    stamp = ""
    if ixml_timestamp is not None:
        samples, stamp_rate = ixml_timestamp
        stamp = (f"<TIMESTAMP_SAMPLE_RATE>{stamp_rate}</TIMESTAMP_SAMPLE_RATE>"
                 f"<TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI>{samples >> 32:010d}</TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI>"
                 f"<TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO>{samples & 0xFFFFFFFF:010d}"
                 "</TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO>")
    ixml = ixml_prefix + (f"<?xml version=\"1.0\"?><BWFXML><SCENE>{scene}</SCENE><TAKE>{take}</TAKE>"
                          f"<TAPE>{tape}</TAPE><SPEED><TIMECODE_RATE>25/1</TIMECODE_RATE>"
                          f"<TIMECODE_FLAG>NDF</TIMECODE_FLAG>{stamp}</SPEED><TRACK_LIST>{track_xml}</TRACK_LIST>"
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


def bwf_wav(folder, description, originator, start_seconds, date, ixml=b"", seconds=1.0, seed=0):
    """A stereo BWF like a DAW bounce or library effect: bext (and maybe iXML), no recording identity."""
    reference = round(start_seconds * RATE)
    bext = (_fixed(description, 256) + _fixed(originator, 32) + _fixed("REF", 32) + _fixed(date, 10) +
            _fixed("08:00:00", 8) + struct.pack("<IIH", reference & 0xFFFFFFFF, reference >> 32, 1) +
            b"\0" * 64 + b"\0" * 10 + b"\0" * 180)
    rng = np.random.default_rng(seed)
    samples = (rng.normal(scale=0.1, size=(round(seconds * RATE), 2)) * (2 ** 23 - 1)).astype("<i4")
    pcm = b"".join(int(value).to_bytes(3, "little", signed=True) for value in samples.ravel())
    fmt = struct.pack("<HHIIHH", 1, 2, RATE, RATE * 6, 6, 24)
    body = b"WAVE" + _chunk(b"fmt ", fmt) + _chunk(b"bext", bext)
    if ixml:
        body += _chunk(b"iXML", ixml)
    body += _chunk(b"data", pcm)
    return store(folder, b"RIFF" + struct.pack("<I", len(body)) + body, "wav")


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


def aaf_bytes(folder, references, library=()):
    """Write an AAF whose master mobs cut pieces of recorder tapes (and optional library files)."""
    import aaf2
    path = Path(folder) / "edit.aaf"
    with aaf2.open(str(path), "w") as handle:
        for name in library:
            # A library effect: a file source with a locator and no recorder tape behind it.
            file_mob = handle.create.SourceMob("")
            file_mob.descriptor = handle.create.ImportDescriptor()
            locator = handle.create.NetworkLocator()
            locator["URLString"].value = "file:///library/" + name.replace(" ", "%20")
            file_mob.descriptor["Locator"].append(locator)
            file_slot = file_mob.create_empty_slot(25, media_kind="sound")
            file_slot.segment.length = 25
            handle.content.mobs.append(file_mob)
            master = handle.create.MasterMob(name + ".new.01")
            master.create_timeline_slot(25).segment = file_mob.create_source_clip(
                slot_id=file_slot.slot_id, start=0, length=25, media_kind="sound")
            handle.content.mobs.append(master)
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


class ContradictionSummaryTests(unittest.TestCase):
    def test_counts_and_wording_name_each_contradicted_layer(self):
        from music_target.score_explanations import contradiction_summary, fact_counts
        counts = fact_counts({
            "file_integrity": [{"passed": False}, {"passed": True}],
            "declaration_consistency": [{"passed": None}],
            "cross_evidence_support": [{"passed": False}, {"passed": True}, {"passed": True}],
        })
        self.assertEqual((counts["contradiction_count"], counts["assessed_fact_count"]), (2, 5))
        self.assertEqual(contradiction_summary(counts),
                         "2 of 5 assessed facts are contradicted (file_integrity 1 of 2, cross_evidence_support 1 of 3)")
        self.assertIsNone(contradiction_summary(fact_counts({"file_integrity": [{"passed": True}]})))


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

    def test_daw_bounce_bwf_is_not_recorder_metadata(self):
        # A Pro Tools bounce: bext with a session timeline position, no recording identity.
        fields = self.fields(bwf_wav(self.folder, "", "Pro Tools", 360.84, "2025-02-01"))
        self.assertFalse(fields["present"])
        self.assertEqual(fields["bwf_kind"], "daw_or_library")
        self.assertEqual(fields["originator"], "Pro Tools")
        self.assertAlmostEqual(fields["timeline_reference_seconds"], 360.84)
        self.assertNotIn("start_timecode_seconds", fields)

    def test_library_effect_keywords_are_not_recorder_metadata(self):
        # Library effects describe content, not a recording event, even with an iXML chunk.
        ixml = b"<?xml version=\"1.0\"?><BWFXML><NOTE>ambience</NOTE></BWFXML>"
        fields = self.fields(bwf_wav(self.folder, "interior, ambience, parking, airvent,", "", 0.0, "",
                                     ixml=ixml))
        self.assertFalse(fields["present"])
        self.assertEqual(fields["bwf_kind"], "daw_or_library")
        self.assertEqual(fields["sources"], ["bext", "iXML"])

    def test_recorder_take_is_classified_as_recorder(self):
        fields = self.fields(recorder_wav(self.folder, *TAKE))
        self.assertEqual(fields["bwf_kind"], "recorder")

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

    def request(self, documents, master=None):
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["case_id"] = "PRODUCTION-DOCUMENTS"
        request["workflow"]["workflow_id"] = "recorded_single_performance_to_final"
        request["workflow"]["modifiers"] = []
        master = master or recorder_wav(self.folder, "MASTER.WAV", "MASTER", "0", "1", {1: "L", 2: "R"}, 0.0,
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

    def test_aaf_take_missing_from_the_submission_is_reported_not_scored(self):
        # The edit uses 2T01 (submitted) and 3T02 (not submitted).
        other = ("3T02.WAV", "24Y12M18", "3", "02", TRACKS, 32000.0, "2024-12-18")
        refs = [("24Y12M18", "2T01.WAV", "2", "01", TRACKS, "2024-12-18", 31783.5, 25),
                (other[1], other[0], other[2], other[3], TRACKS, other[6], 32000.5, 25)]
        aaf = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), refs), "aaf")
        baseline = run.evaluate(self.request([(aaf, "project/aaf", "aaf")]), self.folder, "native")
        coverage = baseline["assessment_checks"]["aaf_reference_coverage"][0]
        self.assertEqual((coverage["referenced_recordings"], coverage["bound_recordings"]), (2, 1))
        self.assertEqual([(item["tape"], item["file_name"]) for item in coverage["missing_recordings"]],
                         [("24Y12M18", "3T02.WAV")])
        finding = next(row for row in baseline["findings"] if row["code"] == "AAF_REFERENCED_TAKES_NOT_SUBMITTED")
        self.assertEqual(finding["severity"], "low")
        self.assertIs(finding["scoring_eligible"], False)
        # Adding the missing take clears the finding; scores of the shared evidence are unchanged.
        complete_refs = [refs[0]]
        aaf_complete = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), complete_refs), "aaf")
        complete = run.evaluate(self.request([(aaf_complete, "project/aaf", "aaf")]), self.folder, "native")
        self.assertFalse(any(row["code"] == "AAF_REFERENCED_TAKES_NOT_SUBMITTED" for row in complete["findings"]))
        # Scores are unchanged; only the reasoning names the missing material.
        strip = lambda axes: {name: {k: v for k, v in axis.items() if k != "reasoning"} for name, axis in axes.items()}
        self.assertEqual(strip(baseline["axes"]), strip(complete["axes"]))
        # The unsubmitted take joins the required recordings, so it cannot be
        # left out to raise the share; it is missing material, not a contradiction.
        breadth = baseline["assessment_checks"]["support_breadth"][0]
        self.assertEqual((breadth["required_recording_count"], breadth["not_submitted"], breadth["contradicted"]),
                         (2, 1, 0))
        self.assertEqual((breadth["material_coverage"], breadth["support_coverage"], breadth["coverage_scope"]),
                         (0.5, 0.5, "confirmed"))
        self.assertEqual(complete["assessment_checks"]["support_breadth"][0]["support_coverage"], 1.0)
        self.assertIn("1 recording the AAF edit uses was not submitted", baseline["axes"]["integrity"]["reasoning"])

    def test_aaf_library_media_is_listed_without_scoring(self):
        library = ["BOOM Library - horse trotting.wav"]
        refs = [("24Y12M18", "2T01.WAV", "2", "01", TRACKS, "2024-12-18", 31783.5, 25)]
        aaf = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), refs, library), "aaf")
        parsed = parse_aaf_file(self.folder / "objects" / f"{aaf}.aaf")
        self.assertEqual(len(parsed["references"]), 1)
        self.assertEqual(parsed["other_media"], [{"master_mob": library[0] + ".new.01",
                                                  "locator_file_names": library}])
        plain = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), refs), "aaf")
        with_media = run.evaluate(self.request([(aaf, "project/aaf", "aaf")]), self.folder, "native")
        without = run.evaluate(self.request([(plain, "project/aaf", "aaf")]), self.folder, "native")
        media = with_media["assessment_checks"]["aaf_non_recorder_media"][0]
        self.assertEqual(media["media_count"], 1)
        self.assertTrue(any(row["code"] == "AAF_REFERENCES_NON_RECORDER_MEDIA" for row in with_media["findings"]))
        self.assertIn("1 media items that are not part of this submission",
                      with_media["axes"]["ai_disclosure"]["reasoning"])
        for name in ("completeness", "integrity", "attestation_strength", "ai_disclosure"):
            self.assertEqual(with_media["axes"][name]["value"], without["axes"][name]["value"])

    def tasks_for(self, documents, master=None):
        from music_target.audio_diagnostics.tasks import build_tasks
        from music_target.contract import to_native
        request = self.request(documents, master=master)
        response = run.evaluate(copy.deepcopy(request), self.folder, "native")
        native, _ = to_native(copy.deepcopy(request), "native", include_report=True)
        return [task for task in build_tasks(native, response) if task["source_hash"] == self.take]

    def test_aaf_used_range_limits_the_diagnostic_task(self):
        # The AAF uses 1 s of 2T01 starting 0.5 s into the recording (08:49:43:12.5 at 25 fps).
        aaf = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), [
            ("24Y12M18", "2T01.WAV", "2", "01", TRACKS, "2024-12-18", 31783.5, 25)]), "aaf")
        (task,) = self.tasks_for([(aaf, "project/aaf", "aaf")])
        self.assertEqual(task["range_source"], "aaf_source_range")
        self.assertTrue(task["selection_id"].startswith("aaf:2/01:"))
        # The fixture rounds the AAF start to whole 25 fps frames, so allow one frame.
        self.assertAlmostEqual(task["attributes"]["source_start_seconds"], 0.5, delta=1 / 25)
        self.assertAlmostEqual(task["attributes"]["source_end_seconds"], 1.5, delta=1 / 25)
        self.assertIsNone(task["attributes"]["target_start_seconds"])
        self.assertIsNone(task["attributes"]["target_end_seconds"])

    def test_without_a_usable_aaf_range_the_whole_take_is_analysed(self):
        (plain,) = self.tasks_for([])
        self.assertEqual(plain["range_source"], "whole_file")
        # A range outside the recording is left to the core check, not analysed.
        outside = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), [
            ("24Y12M18", "2T01.WAV", "2", "01", TRACKS, "2024-12-18", 31790.0, 25)]), "aaf")
        (task,) = self.tasks_for([(outside, "project/aaf", "aaf")])
        self.assertEqual(task["range_source"], "whole_file")

    def test_only_aaf_tasks_may_leave_the_target_range_open(self):
        from music_target.audio_diagnostics.io import AnalysisUnavailable, read_pair
        path = str(self.folder / "objects" / f"{self.take}.wav")
        attributes = {"source_start_seconds": 0.5, "source_end_seconds": 1.5,
                      "target_start_seconds": None, "target_end_seconds": None}
        task = {"source_path": path, "target_path": path, "attributes": attributes,
                "range_source": "aaf_source_range"}
        source, target = read_pair(task, 8000)
        self.assertEqual(source["range"], [0.5, 1.5])
        self.assertEqual(target["range"], [0.0, 2.0])
        with self.assertRaises(AnalysisUnavailable) as caught:
            read_pair({**task, "range_source": "declared"}, 8000)
        self.assertEqual(caught.exception.code, "incomplete_declared_ranges")

    def test_aaf_range_match_is_reported_as_a_content_observation(self):
        from music_target.audio_diagnostics.contracts import result
        from music_target.audio_diagnostics.runner import _attach
        task = {"task_id": "t:0", "relation_id": "t", "relationship_type": "derived_from",
                "source_hash": "a" * 64, "target_hash": "b" * 64, "selection_id": "aaf:2/01:0",
                "range_source": "aaf_source_range"}
        rows = []
        for provider, matched in (("audfprint", False), ("spectromap", True)):
            row = result(task, provider)
            row.update(analysis_complete=True, analysed_ranges={"source": [0.5, 1.5], "target": [0.0, 2.0]},
                       matches=[{"source_minus_target_offset_seconds": 0.0}] if matched else [])
            rows.append(row)
        combined = _attach({"findings": [], "assessment_checks": {}}, rows, {"task_count": 1})
        summary = combined["assessment_checks"]["audio_diagnostic_summary"][0]
        self.assertEqual((summary["aaf_range_tasks"], summary["aaf_range_tasks_with_correspondence"]), (1, 1))
        (finding,) = [row for row in combined["findings"] if row["code"] == "AUDIO_AAF_RANGE_CORRESPONDENCE"]
        self.assertEqual(finding["providers"], ["spectromap"])
        self.assertEqual(finding["source_range_seconds"], [0.5, 1.5])
        self.assertIs(finding["scoring_eligible"], False)
        self.assertIs(finding["content_match_is_provenance"], False)

    def test_source_originated_after_its_target_is_reported_not_scored(self):
        # The take says 18 Dec 2024; a master bounced "earlier" (2024-01-01) is out of order.
        early = bwf_wav(self.folder, "", "Pro Tools", 0.0, "2024-01-01", seed=3)
        late = bwf_wav(self.folder, "", "Pro Tools", 0.0, "2025-02-01", seed=4)
        reversed_ = run.evaluate(self.request([], master=early), self.folder, "native")
        ordered = run.evaluate(self.request([], master=late), self.folder, "native")
        (finding,) = [row for row in reversed_["findings"] if row["code"] == "DECLARED_SOURCE_NEWER_THAN_TARGET"]
        self.assertEqual((finding["severity"], finding["source_hash"]), ("low", self.take))
        self.assertIs(finding["scoring_eligible"], False)
        rows = reversed_["assessment_checks"]["declared_chronology"]
        self.assertEqual([row["source_newer_than_target"] for row in rows], [True])
        self.assertFalse(any(row["code"] == "DECLARED_SOURCE_NEWER_THAN_TARGET" for row in ordered["findings"]))
        self.assertEqual([row["source_newer_than_target"] for row in ordered["assessment_checks"]["declared_chronology"]],
                         [False])
        for name in ("completeness", "integrity", "attestation_strength", "ai_disclosure"):
            self.assertEqual(reversed_["axes"][name]["value"], ordered["axes"][name]["value"])

    def test_raw_take_can_be_mixed_directly(self):
        # Dialogue post mixes recorder takes straight after the AAF edit.
        responses = {}
        for relationship in ("derived_from", "mixed_from"):
            request = self.request([])
            final = request["chain"]["artefacts"][-1]
            final["artefact_type"] = "audio/mix"
            final["evidence"][0]["relationship_type"] = relationship
            responses[relationship] = run.evaluate(request, self.folder, "native")
        response = responses["mixed_from"]
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        self.assertEqual(response["coverage"]["skipped_relationships"], 0)
        self.assertFalse(any(row["code"] == "RELATIONSHIP_ENDPOINTS_SKIPPED" for row in response["findings"]))
        self.assertEqual(response["axes"]["completeness"], responses["derived_from"]["axes"]["completeness"])

    def test_daw_bounce_gets_no_recorder_origin_finding(self):
        master = bwf_wav(self.folder, "", "Pro Tools", 360.84, "2025-02-01", seed=2)
        response = run.evaluate(self.request([], master=master), self.folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        flagged = {row["evidence_hash"] for row in response["findings"]
                   if row["code"] == "RECORDER_METADATA_ORIGIN_NOT_AUTHENTICATED"}
        self.assertEqual(flagged, {self.take})

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

    def shifted_report(self):
        return store(self.folder, sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:45:00", '"MixL"', '"MixR"', '"Boom"', '""']]), "csv")

    def test_contradiction_count_leads_the_integrity_reasoning(self):
        response = run.evaluate(self.request([(self.shifted_report(), "text/sound-report", "csv")]),
                                self.folder, "native")
        decision = response["assessment_checks"]["integrity_decision"][0]
        explanation = next(row for row in response["assessment_checks"]["score_explanations"]
                           if row["axis"] == "integrity")
        self.assertEqual(decision["contradiction_count"], 1)
        by_layer = {row["layer"]: row for row in decision["fact_counts_by_layer"]}
        self.assertEqual(set(by_layer), {"file_integrity", "declaration_consistency", "cross_evidence_support"})
        self.assertEqual(by_layer["cross_evidence_support"]["contradicted"], 1)
        self.assertEqual(decision["assessed_fact_count"], sum(row["assessed"] for row in by_layer.values()))
        cross = by_layer["cross_evidence_support"]
        lead = (f"1 of {decision['assessed_fact_count']} assessed facts is contradicted "
                f"(cross_evidence_support 1 of {cross['assessed']})")
        self.assertTrue(response["axes"]["integrity"]["reasoning"].startswith(lead))
        self.assertEqual(explanation["statements"][0], lead + ".")
        for key in ("contradiction_count", "assessed_fact_count", "fact_counts_by_layer"):
            self.assertEqual(explanation[key], decision[key])
        honest = run.evaluate(self.request([]), self.folder, "native")
        self.assertEqual(honest["assessment_checks"]["integrity_decision"][0]["contradiction_count"], 0)
        self.assertNotIn("assessed facts", honest["axes"]["integrity"]["reasoning"])

    def test_several_contradictions_use_the_plural(self):
        # The report shifts the start timecode and the AAF names the wrong scene: two facts fail.
        aaf = store(self.folder, aaf_bytes(Path(tempfile.mkdtemp(dir=self.folder)), [
            ("24Y12M18", "2T01.WAV", "9", "01", TRACKS, "2024-12-18", 31783.5, 25)]), "aaf")
        response = run.evaluate(self.request([(self.shifted_report(), "text/sound-report", "csv"),
                                              (aaf, "project/aaf", "aaf")]), self.folder, "native")
        decision = response["assessment_checks"]["integrity_decision"][0]
        self.assertEqual(decision["contradiction_count"], 2)
        self.assertTrue(response["axes"]["integrity"]["reasoning"].startswith(
            f"2 of {decision['assessed_fact_count']} assessed facts are contradicted"))

    def test_counts_survive_without_reasoning(self):
        request = self.request([(self.shifted_report(), "text/sound-report", "csv")])
        request["options"]["include_reasoning"] = False
        response = run.evaluate(request, self.folder, "native")
        self.assertNotIn("score_explanations", response["assessment_checks"])
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["contradiction_count"], 1)

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


class RecorderTimestampTests(unittest.TestCase):
    """bext TimeReference against the iXML timestamp the recorder wrote with it."""

    setUp = RecorderMetadataTests.setUp
    fields = RecorderMetadataTests.fields
    request = ProductionDocumentTests.request

    def check(self, digest):
        from music_target.recorder_metadata import timestamp_checks
        return timestamp_checks(digest, self.fields(digest), RATE)

    def recorder_take(self, bext_seconds, ixml_samples, ixml_rate=RATE):
        return recorder_wav(self.folder, "2T01.WAV", "24Y12M18", "2", "01", TRACKS, bext_seconds, "2024-12-18",
                            ixml_timestamp=(ixml_samples, ixml_rate))

    def test_matching_timestamps_pass(self):
        [check] = self.check(self.recorder_take(31783.0, 31783 * RATE))
        self.assertEqual(check["rule"], "recorder_bext_time_reference_matches_ixml_timestamp")
        self.assertIs(check["passed"], True)
        self.assertEqual(check["difference_seconds"], 0.0)

    def test_bext_shifted_by_one_second_fails(self):
        [check] = self.check(self.recorder_take(31784.0, 31783 * RATE))
        self.assertIs(check["passed"], False)
        self.assertAlmostEqual(check["difference_seconds"], 1.0)

    def test_one_sample_apart_at_the_same_rate_fails(self):
        [check] = self.check(self.recorder_take(31783.0, 31783 * RATE + 1))
        self.assertIs(check["passed"], False)

    def test_timestamp_at_another_rate_is_compared_in_seconds(self):
        [check] = self.check(self.recorder_take(31783.0, 31783 * 96000, ixml_rate=96000))
        self.assertIs(check["passed"], True)
        [check] = self.check(self.recorder_take(31783.0, 31784 * 96000, ixml_rate=96000))
        self.assertIs(check["passed"], False)

    def test_without_an_ixml_timestamp_nothing_is_checked(self):
        self.assertEqual(self.check(recorder_wav(self.folder, *TAKE)), [])

    def test_daw_or_library_bwf_is_not_checked(self):
        from music_target.recorder_metadata import timestamp_checks
        recorder = {"bwf_kind": "daw_or_library", "time_reference_samples": 1, "ixml_timestamp_samples": 2}
        self.assertEqual(timestamp_checks("d" * 64, recorder, RATE), [])

    def test_timestamp_shift_matched_in_the_sound_report_is_a_contradiction(self):
        # The CSEC forgery: bext and the sound report both moved by one
        # second, so the report check passes; the iXML timestamp still says
        # 08:49:43:00.
        self.take = self.recorder_take(31784.0, 31783 * RATE)
        report = store(self.folder, sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:44:00", '"MixL"', '"MixR"', '"Boom"', '""']]), "csv")
        response = run.evaluate(self.request([(report, "text/sound-report", "csv")]), self.folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        checks = response["assessment_checks"]
        report_check = next(item for item in checks["cross_evidence"]
                            if item["rule"] == "sound_report_start_timecode_matches_recorder")
        self.assertIs(report_check["passed"], True)
        stamp = next(item for item in checks["bound_file_integrity"]
                     if item["rule"] == "recorder_bext_time_reference_matches_ixml_timestamp")
        self.assertIs(stamp["passed"], False)
        layer = next(item for item in checks["integrity_layers"] if item["layer"] == "file_integrity")
        self.assertEqual(layer["status"], "contradicted")
        self.assertTrue(checks["integrity_decision"][0]["contradiction_present"])
        explanation = next(row for row in checks["score_explanations"] if row["axis"] == "integrity")
        self.assertIn("differs from its iXML timestamp (31783.000 s) by 1.000 s", " ".join(explanation["statements"]))

    def test_honest_take_with_timestamp_still_releases_integrity(self):
        self.take = self.recorder_take(31783.0, 31783 * RATE)
        report = store(self.folder, sound_report("18/12/24", [
            ["2T01.WAV", "2", "01", "00:00:02", "08:49:43:00", '"MixL"', '"MixR"', '"Boom"', '""']]), "csv")
        response = run.evaluate(self.request([(report, "text/sound-report", "csv")]), self.folder, "native")
        self.assertEqual(response["assessment_checks"]["integrity_decision"][0]["contradiction_count"], 0)
        self.assertIsNotNone(response["axes"]["integrity"]["value"])


if __name__ == "__main__":
    unittest.main()
