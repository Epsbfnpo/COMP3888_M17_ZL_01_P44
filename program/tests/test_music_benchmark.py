"""The public-dataset benchmark harness, on a tiny generated Slakh-style dataset."""

import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "vendor"))
sys.path.insert(0, str(BASE / "benchmarks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import music_benchmark
from music_target.contract import validate
from test_audio_diagnostics import OTHER, POP, synthetic_song


def midi_bytes(notes, tempo_bpm=100, time_signature=None):
    """Format-0 MIDI: one quarter note per pitch."""
    def varlen(value):
        out = [value & 0x7F]
        while value > 0x7F:
            value >>= 7
            out.insert(0, (value & 0x7F) | 0x80)
        return bytes(out)
    events = b"\x00\xff\x51\x03" + int(60_000_000 / tempo_bpm).to_bytes(3, "big")
    if time_signature:
        numerator, denominator = time_signature
        events += b"\x00\xff\x58\x04" + bytes([numerator, denominator.bit_length() - 1, 24, 8])
    for pitch in notes:
        events += varlen(0) + bytes([0x90, pitch, 90]) + varlen(480) + bytes([0x80, pitch, 0])
    events += b"\x00\xff\x2f\x00"
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(events)) + events


def write_wav(path, samples, rate=22050):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def slakh_fixture(root):
    for name, sections, seed in (("Track00001", POP, 0), ("Track00002", OTHER, 5)):
        track = Path(root) / name
        (track / "stems").mkdir(parents=True)
        (track / "MIDI").mkdir()
        stems = [synthetic_song(sections, "AB", seed=seed), synthetic_song(sections, "BA", bright=True, seed=seed + 1)]
        for index, audio in enumerate(stems):
            write_wav(track / "stems" / f"S0{index}.wav", audio)
            (track / "MIDI" / f"S0{index}.mid").write_bytes(midi_bytes([60 + index, 64, 67]))
        length = min(len(item) for item in stems)
        write_wav(track / "mix.wav", sum(item[:length] for item in stems) / 2)


class MusicBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        slakh_fixture(self.root / "dataset")

    def test_requests_are_valid_and_label_true_and_control_stems(self):
        requests, labels = music_benchmark.build_requests("slakh", self.root / "dataset", self.root / "bundle")
        self.assertEqual(len(requests), 2)
        for track, request in requests:
            validate(request, "native-request.schema.json")
            mix = next(node for node in request["chain"]["artefacts"]
                       if node["artefact_hash"] == request["chain"]["final_artefact_hash"])
            declared = [(track, edge["hash"]) for edge in mix["evidence"]]
            self.assertEqual(sorted(labels[key] for key in declared),
                             ["control_other_track", "true_stem", "true_stem"])
            kinds = {node["artefact_type"] for node in request["chain"]["artefacts"]}
            self.assertEqual(kinds, {"project/midi", "audio/stem", "audio/mix"})

    def test_auc(self):
        self.assertEqual(music_benchmark.auc([3, 4], [1, 2]), 1.0)
        self.assertEqual(music_benchmark.auc([1], [1]), 0.5)
        self.assertIsNone(music_benchmark.auc([], [1]))

    def test_one_track_runs_end_to_end_under_the_memory_guard(self):
        if importlib.util.find_spec("librosa") is None and os.environ.get("REQUIRE_AUDIO_BACKENDS") != "1":
            self.skipTest("Optional audio dependencies are not installed")
        requests, labels = music_benchmark.build_requests("slakh", self.root / "dataset", self.root / "bundle")
        track, request = requests[0]
        request_path, response_path = self.root / "request.json", self.root / "response.json"
        request_path.write_text(json.dumps(request))
        meta = music_benchmark.run_guarded(request_path, self.root / "bundle", response_path)
        self.assertEqual(meta["exit"], 0, meta["stderr_tail"])
        self.assertFalse(meta["killed_over_memory_limit"])
        rows = music_benchmark._rows(track, json.loads(response_path.read_text()), labels)
        self.assertEqual({row["label"] for row in rows}, {"true_stem", "control_other_track"})
        summary = music_benchmark.summarise(rows)
        self.assertIn("music_semantics.harmonic_similarity_z", summary)


if __name__ == "__main__":
    unittest.main()
