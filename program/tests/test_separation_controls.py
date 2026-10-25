"""Excerpt extraction and codec conditions for the Phase 2B controls, on generated audio (no network)."""

import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

import numpy as np
import soundfile as sf

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "benchmarks"))
import separation_controls as controls  # noqa: E402

RATE = 44100


def wav_bytes(samples, extra_chunk=True):
    buffer = io.BytesIO()
    sf.write(buffer, samples, RATE, format="WAV", subtype="PCM_16")
    data = buffer.getvalue()
    if extra_chunk:
        # A LIST chunk before 'data' must be skipped, not read as audio.
        fmt_end = 12 + 8 + int.from_bytes(data[16:20], "little")
        data = data[:fmt_end] + b"LIST" + (4).to_bytes(4, "little") + b"INFO" + data[fmt_end:]
        data = data[:4] + (len(data) - 8).to_bytes(4, "little") + data[8:]
    return data


class ExcerptTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(3)
        self.parts = {name: (rng.standard_normal((RATE * 4, 2)) * 0.1).clip(-1, 1) for name in controls.MEMBERS}
        self.archive = io.BytesIO()
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            for name, samples in self.parts.items():
                handle.writestr(f"test/Song/{name}.wav", wav_bytes(samples))
        self.archive.seek(0)

    def test_excerpt_matches_the_source_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = controls.fetch(["Song"], folder, 1.0, 2.0, source=self.archive)
            self.assertEqual(len(manifest["files"]), 5)
            excerpt, rate = sf.read(str(Path(folder) / "test" / "Song" / "vocals.wav"), dtype="int16")
            original, _ = sf.read(io.BytesIO(wav_bytes(self.parts["vocals"], extra_chunk=False)), dtype="int16")
            self.assertEqual(rate, RATE)
            self.assertEqual(len(excerpt), 2 * RATE)
            np.testing.assert_array_equal(excerpt, original[RATE:3 * RATE])

    def test_missing_member_and_overlong_excerpt_are_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(controls.probe.ProbeError):
                controls.fetch(["Other Song"], folder, 0.0, 1.0, source=self.archive)
            self.archive.seek(0)
            with self.assertRaises(controls.probe.ProbeError):
                controls.fetch(["Song"], folder, 3.5, 1.0, source=self.archive)


@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg required")
class ConditionTests(unittest.TestCase):
    def test_conditions_keep_stems_full_band_and_mixes_aligned(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            song = root / "hq" / "test" / "Song"
            song.mkdir(parents=True)
            t = np.arange(RATE * 3) / RATE
            rng = np.random.default_rng(5)
            parts = {"vocals": 0.2 * np.sin(2 * np.pi * 440 * t), "drums": 0.05 * rng.standard_normal(len(t)),
                     "bass": 0.2 * np.sin(2 * np.pi * 60 * t), "other": 0.02 * rng.standard_normal(len(t))}
            for name, signal in parts.items():
                sf.write(str(song / f"{name}.wav"), np.stack([signal, signal], 1), RATE, subtype="PCM_16")
            sf.write(str(song / "mixture.wav"), np.stack([sum(parts.values())] * 2, 1), RATE, subtype="PCM_16")
            report = controls.build(root / "hq", root / "out")
            self.assertEqual(set(report["conditions"]), set(controls.CONDITIONS))
            for name, item in report["conditions"].items():
                self.assertEqual(item["mix_checks"][0]["lag_samples"], 0, name)
            hq_vocals = (song / "vocals.wav").read_bytes()
            self.assertEqual((root / "out" / "C1C2_mp3_128" / "test" / "Song" / "vocals.wav").read_bytes(), hq_vocals)
            self.assertNotEqual((root / "out" / "C4_mp3_128_both" / "test" / "Song" / "vocals.wav").read_bytes(),
                                hq_vocals)
            edge = {name: item["mix_checks"][0]["mix_content_edge_hz"] for name, item in report["conditions"].items()}
            self.assertLess(edge["C1C2_mp3_128"], edge["C3_fullband"])


if __name__ == "__main__":
    unittest.main()
