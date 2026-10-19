"""Ableton Live sessions checked against the submitted graph and MIDI."""

import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run
from music_target.daw_session import DawSessionError, parse_ableton_session
from test_music_benchmark import midi_bytes


def store(folder, data, suffix):
    digest = hashlib.sha256(data).hexdigest()
    (Path(folder) / "objects").mkdir(exist_ok=True)
    (Path(folder) / "objects" / f"{digest}.{suffix}").write_bytes(data)
    return digest


def wav_bytes(seconds, seed, rate=22050):
    import io
    samples = np.random.default_rng(seed).normal(scale=0.1, size=int(seconds * rate))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes((samples * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def sample_ref(name, data, rate=22050, live10=False):
    frames = (len(data) - 44) // 2
    if live10:
        path = f'<RelativePath><RelativePathElement Dir="Samples"/></RelativePath><Name Value="{name}"/>'
    else:
        path = f'<RelativePath Value="Samples/{name}"/><Path Value="/Users/x/Song Project/Samples/{name}"/>'
    return (f'<SampleRef><FileRef>{path}<OriginalFileSize Value="{len(data)}"/><OriginalCrc Value="0"/></FileRef>'
            f'<DefaultDuration Value="{frames}"/><DefaultSampleRate Value="{rate}"/></SampleRef>')


def als_bytes(samples, tempo=100, signature=(4, 4), live10=False, prefix=""):
    clips = "".join(f'<AudioTrack Id="{index}"><Name><EffectiveName Value="Track {index}"/></Name><DeviceChain>'
                    f'<MainSequencer><Sample><ArrangerAutomation><Events><AudioClip Id="0" Time="0">'
                    f'<CurrentStart Value="0"/><CurrentEnd Value="16"/><Name Value="{name}"/>'
                    f'{sample_ref(name, data, live10=live10)}</AudioClip></Events></ArrangerAutomation></Sample>'
                    f'</MainSequencer></DeviceChain></AudioTrack>' for index, (name, data) in enumerate(samples))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>{prefix}<Ableton MajorVersion="5" Creator="Ableton Live 11.0">'
           f'<LiveSet><Tracks>{clips}</Tracks><MasterTrack><DeviceChain><Mixer><Tempo><Manual Value="{tempo}"/>'
           f'</Tempo></Mixer></DeviceChain><TimeSignature><Numerator Value="{signature[0]}"/>'
           f'<Denominator Value="{signature[1]}"/></TimeSignature></MasterTrack></LiveSet></Ableton>')
    return gzip.compress(xml.encode())


class DawSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        # Exported stems usually share one length, as here.
        self.stems = [("drums.wav", wav_bytes(2, 1)), ("bass.wav", wav_bytes(2, 2))]
        self.mix = wav_bytes(2, 3)

    def request(self, session, declared_extra=None):
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["workflow"].update(workflow_id="midi_virtual_instrument_production", modifiers=[])
        midi = store(self.folder, midi_bytes([60, 64, 67], tempo_bpm=100, time_signature=(4, 4)), "mid")
        stems = [store(self.folder, data, "wav") for _, data in self.stems + ([declared_extra] if declared_extra else [])]
        mix = store(self.folder, self.mix, "wav")
        als = store(self.folder, session, "als")
        artefacts = [{"artefact_hash": midi, "artefact_type": "project/midi", "attributes": {}, "evidence": []},
                     {"artefact_hash": als, "artefact_type": "project/daw-session", "attributes": {}, "evidence": []}]
        artefacts += [{"artefact_hash": digest, "artefact_type": "audio/stem", "attributes": {},
                       "evidence": [{"hash": midi, "relationship_type": "rendered_from", "attributes": {}}]}
                      for digest in stems]
        artefacts.append({"artefact_hash": mix, "artefact_type": "audio/mix", "attributes": {},
                          "evidence": [{"hash": digest, "relationship_type": "mixed_from", "attributes": {}}
                                       for digest in stems] +
                                      [{"hash": als, "relationship_type": "input_to", "attributes": {}}]})
        request["chain"].update(final_artefact_hash=mix, artefacts=artefacts)
        request["objects"] = {midi: f"objects/{midi}.mid", als: f"objects/{als}.als", mix: f"objects/{mix}.wav",
                              **{digest: f"objects/{digest}.wav" for digest in stems}}
        return request

    def evaluate(self, request):
        response = run.evaluate(request, self.folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        return response, [item for item in response["assessment_checks"].get("cross_evidence", [])
                          if item["rule"].startswith("daw_session_")]

    def test_honest_session_corroborates_but_cannot_release_integrity_alone(self):
        response, checks = self.evaluate(self.request(als_bytes(self.stems)))
        rules = sorted(item["rule"] for item in checks)
        self.assertEqual(rules, ["daw_session_tempo_matches_midi", "daw_session_time_signature_matches_midi",
                                 "daw_session_uses_declared_source_audio"])
        group = next(item for item in checks if item["rule"] == "daw_session_uses_declared_source_audio")
        self.assertEqual((group["declared_sources"], group["session_samples"]), (2, 2))
        self.assertTrue(all(item["passed"] and item["evidence_tier"] == "submitter_document" for item in checks))
        self.assertIsNone(response["axes"]["integrity"]["value"])

    def test_declared_stem_missing_from_the_session_is_a_named_contradiction(self):
        extra = ("vocals.wav", wav_bytes(2, 4))
        response, checks = self.evaluate(self.request(als_bytes(self.stems), declared_extra=extra))
        failed = [item for item in checks if item["passed"] is False]
        self.assertEqual([item["rule"] for item in failed], ["daw_session_uses_declared_source_audio"])
        explanation = next(row for row in response["assessment_checks"]["score_explanations"]
                           if row["axis"] == "integrity")
        self.assertEqual((failed[0]["declared_sources"], failed[0]["session_samples"]), (3, 2))
        self.assertTrue(any("declares 3 source(s)" in statement and "session uses 2 such sample(s)" in statement
                            for statement in explanation["statements"]))

    def test_tempo_mismatch_is_reported(self):
        _, checks = self.evaluate(self.request(als_bytes(self.stems, tempo=128)))
        tempo = next(item for item in checks if item["rule"] == "daw_session_tempo_matches_midi")
        self.assertFalse(tempo["passed"])
        self.assertEqual((tempo["declared"], tempo["observed"]), (128.0, 100.0))

    def test_live_10_file_references_are_read(self):
        path = self.folder / "set.als"
        path.write_bytes(als_bytes(self.stems, live10=True))
        samples = parse_ableton_session(path)["samples"]
        self.assertEqual([item["file_name"] for item in samples], ["drums.wav", "bass.wav"])
        self.assertEqual(samples[0]["relative_path"], "Samples")

    def test_non_ableton_session_keeps_not_assessed(self):
        request = self.request(b"<project/>")
        response, checks = self.evaluate(request)
        self.assertEqual(checks, [])
        observation = next(item for item in response["findings"] if item["code"] == "PARSER_OBSERVATION"
                           and item["message"].startswith("project/daw-session"))
        self.assertEqual(observation["parser_status"], "not_assessed")

    def test_session_with_a_dtd_is_refused(self):
        path = self.folder / "set.als"
        path.write_bytes(als_bytes(self.stems, prefix='<!DOCTYPE a [<!ENTITY b "c">]>'))
        with self.assertRaises(DawSessionError):
            parse_ableton_session(path)


if __name__ == "__main__":
    unittest.main()
