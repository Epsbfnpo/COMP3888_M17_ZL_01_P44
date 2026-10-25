"""Phase 2B encoding controls: MUSDB18-HQ excerpts and four codec conditions.

    python -B benchmarks/separation_controls.py fetch --songs-from heldout/measurements.json --count 10 \
        --out ~/datasets/musdb18hq-excerpts
    python -B benchmarks/separation_controls.py verify ~/datasets/musdb18hq-excerpts
    python -B benchmarks/separation_controls.py build ~/datasets/musdb18hq-excerpts --out controls/datasets

``fetch`` reads only the zip index and, per file, the compressed bytes up to
the end of the excerpt, by HTTP range requests (standard library only).
``build`` writes one MUSDB-style dataset per condition, which
separation_batch.py evaluates with the frozen rules. Exploratory and
uncalibrated (docs/SOURCE_SEPARATION_POC.md).
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import separation_probe as probe  # noqa: E402

HQ_URL = "https://zenodo.org/records/3338373/files/musdb18hq.zip?download=1"
HQ_SIZE = 22656664047
MEMBERS = ("mixture", *probe.SOURCES)
# condition: (mix codec, stem codec); None keeps the full-band WAV.
CONDITIONS = {
    "C1C2_mp3_128": ("mp3_128", None),
    "C1C2_aac_256": ("aac_256", None),
    "C3_fullband": (None, None),
    "C4_mp3_128_both": ("mp3_128", "mp3_128"),
}
CODECS = {"mp3_128": ("mp3", ["-c:a", "libmp3lame", "-b:a", "128k"]),
          "aac_256": ("m4a", ["-c:a", "aac", "-b:a", "256k"])}
RETRIES = 5
# Range reads from Zenodo are slow per connection, so files are fetched in parallel.
WORKERS = 5


class HTTPRange(io.RawIOBase):
    """A seekable read-only view of a remote file, fetched by byte ranges."""

    def __init__(self, url, size):
        self.url, self.size, self.pos, self.fetched = url, size, 0, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def readinto(self, buffer):
        count = min(len(buffer), self.size - self.pos)
        if count <= 0:
            return 0
        request = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + count - 1}"})
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    if response.status != 206:
                        raise probe.ProbeError(f"Range request returned HTTP {response.status}.")
                    data = response.read()
                break
            except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == RETRIES - 1:
                    raise
                time.sleep(2 ** attempt)
        buffer[:len(data)] = data
        self.pos += len(data)
        self.fetched += len(data)
        return len(data)


def _read_exact(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise probe.ProbeError("WAV stream ended early.")
    return data


def wav_excerpt(stream, start_seconds, duration_seconds):
    """Samples [start, start + duration) from a PCM WAV stream, reading no further than needed."""
    riff, _, wave = struct.unpack("<4sI4s", _read_exact(stream, 12))
    if riff != b"RIFF" or wave != b"WAVE":
        raise probe.ProbeError("Not a RIFF/WAVE stream.")
    fmt = None
    while True:
        chunk, size = struct.unpack("<4sI", _read_exact(stream, 8))
        if chunk == b"fmt ":
            body = _read_exact(stream, size + (size & 1))
            tag, channels, rate, _, align, bits = struct.unpack("<HHIIHH", body[:16])
            if tag != 1 or bits != 16:
                raise probe.ProbeError(f"Expected 16-bit PCM, found format {tag} with {bits} bits.")
            fmt = (channels, rate, align)
        elif chunk == b"data":
            if fmt is None:
                raise probe.ProbeError("WAV data chunk before fmt chunk.")
            channels, rate, align = fmt
            first, count = int(round(start_seconds * rate)), int(round(duration_seconds * rate))
            if (first + count) * align > size:
                raise probe.ProbeError("Excerpt extends beyond the audio.")
            _read_exact(stream, first * align)
            raw = _read_exact(stream, count * align)
            return np.frombuffer(raw, dtype="<i2").reshape(-1, channels), rate
        else:
            _read_exact(stream, size + (size & 1))


def _complete(path, duration):
    try:
        info = sf.info(str(path))
    except (RuntimeError, sf.LibsndfileError):
        return False
    return info.frames == int(round(duration * info.samplerate))


def fetch(songs, out_dir, start, duration, source=None):
    """Write MUSDB18-HQ excerpts as 16-bit WAV; returns a manifest. ``source`` overrides the remote zip (tests).

    Files already present with the expected length are kept, so an interrupted fetch can resume.
    """
    jobs = [(song, member) for song in songs for member in MEMBERS]
    fetched = []

    def open_archive():
        if source is not None:
            source.seek(0)
            return zipfile.ZipFile(source), None
        raw = HTTPRange(HQ_URL, HQ_SIZE)
        return zipfile.ZipFile(io.BufferedReader(raw, buffer_size=1 << 20)), raw

    def work(batch):
        archive, raw = open_archive()
        names, done = set(archive.namelist()), []
        for song, member in batch:
            path = Path(out_dir) / "test" / song / f"{member}.wav"
            name = f"test/{song}/{member}.wav"
            if name not in names:
                raise probe.ProbeError(f"{name} is not in the archive.")
            if not _complete(path, duration):
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as handle:
                    samples, rate = wav_excerpt(handle, start, duration)
                sf.write(str(path), samples, rate, subtype="PCM_16")
            done.append((song, member, path))
        fetched.append(raw.fetched if raw else 0)
        return done

    workers = 1 if source is not None else WORKERS
    batches = [jobs[index::workers] for index in range(workers)]
    with ThreadPoolExecutor(workers) as pool:
        done = [item for result in pool.map(work, batches) for item in result]
    rows = []
    for song, member, path in sorted(done):
        info = sf.info(str(path))
        rows.append({"song": song, "member": member, "path": str(path), "sha256": probe.sha256(path),
                     "sample_rate_hz": info.samplerate, "channels": info.channels, "frames": info.frames})
    return {"status": probe.STATUS, "source": HQ_URL if source is None else "local", "licence":
            "MUSDB18-HQ, non-commercial research use (Zenodo 3338373)", "start_seconds": start,
            "duration_seconds": duration, "bytes_fetched_this_run": sum(fetched), "files": rows}


def verify(dataset, max_lag=1.0):
    """Per song: do the stems sum to the mix, and does each stem align with it at zero lag?"""
    out = []
    for folder in sorted((Path(dataset) / "test").iterdir()):
        mix, rate = sf.read(str(folder / "mixture.wav"), dtype="float64", always_2d=True)
        stems = {name: sf.read(str(folder / f"{name}.wav"), dtype="float64", always_2d=True)[0]
                 for name in probe.SOURCES}
        residual = mix - sum(stems.values())
        lags = {name: probe.align(probe.mono(mix), probe.mono(audio), rate, max_lag)["lag_samples"]
                for name, audio in stems.items() if np.sqrt(np.mean(audio ** 2)) > 1e-4}
        out.append({"song": folder.name, "max_abs_mix_minus_stem_sum": probe._num(np.abs(residual).max()),
                    "mix_to_residual_db": probe._db(np.sum(mix ** 2) / np.sum(residual ** 2))
                    if np.sum(residual ** 2) > 0 else None,
                    "stem_lags_samples": lags,
                    "mix_content_edge_hz": probe.high_frequency_profile(probe.mono(mix), rate)["content_edge_hz"]})
    return out


def _codec_round_trip(path, codec, out_path):
    """Encode a WAV with ffmpeg and decode it back to 32-bit float WAV at the original rate."""
    suffix, options = CODECS[codec]
    with tempfile.TemporaryDirectory() as folder:
        encoded = Path(folder) / f"encoded.{suffix}"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path), *options, str(encoded)],
                       check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(encoded), "-c:a", "pcm_f32le",
                        str(out_path)], check=True)


def build(hq_dir, out_root):
    """One dataset per condition, plus the decoded mixes' alignment against the full-band mix."""
    report = {"status": probe.STATUS, "conditions": {}}
    songs = sorted(path.name for path in (Path(hq_dir) / "test").iterdir() if path.is_dir())
    for condition, (mix_codec, stem_codec) in CONDITIONS.items():
        checks = []
        for song in songs:
            source, target = Path(hq_dir) / "test" / song, Path(out_root) / condition / "test" / song
            target.mkdir(parents=True, exist_ok=True)
            for member in MEMBERS:
                codec = mix_codec if member == "mixture" else stem_codec
                if codec is None:
                    shutil.copyfile(source / f"{member}.wav", target / f"{member}.wav")
                else:
                    _codec_round_trip(source / f"{member}.wav", codec, target / f"{member}.wav")
            original, rate = sf.read(str(source / "mixture.wav"), dtype="float64", always_2d=True)
            decoded, decoded_rate = sf.read(str(target / "mixture.wav"), dtype="float64", always_2d=True)
            alignment = probe.align(probe.mono(original), probe.mono(decoded), rate, 0.5)
            checks.append({"song": song, "decoded_rate_hz": decoded_rate, "decoded_frames": len(decoded),
                           "original_frames": len(original), "lag_samples": alignment["lag_samples"],
                           "normalised_correlation": alignment["normalised_correlation_at_lag"],
                           "mix_content_edge_hz": probe.high_frequency_profile(probe.mono(decoded),
                                                                               decoded_rate)["content_edge_hz"]})
        report["conditions"][condition] = {"mix_codec": mix_codec or "wav_fullband",
                                           "stem_codec": stem_codec or "wav_fullband", "mix_checks": checks}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exploratory encoding controls for the separation indicator.")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch")
    fetch_cmd.add_argument("--songs-from", required=True, type=Path, help="measurements.json whose songs to use")
    fetch_cmd.add_argument("--count", type=int, default=10)
    fetch_cmd.add_argument("--start", type=float, default=30.0)
    fetch_cmd.add_argument("--duration", type=float, default=7.0)
    fetch_cmd.add_argument("--out", required=True, type=Path)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("dataset", type=Path)
    build_cmd = sub.add_parser("build")
    build_cmd.add_argument("hq_dir", type=Path)
    build_cmd.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            songs = sorted(row["song"] for row in json.loads(args.songs_from.read_text())["songs"])[:args.count]
            manifest = fetch(songs, args.out, args.start, args.duration)
            (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            print(f"{len(manifest['files'])} files, {manifest['bytes_fetched_this_run'] / 1e6:.1f} MB fetched")
        elif args.command == "verify":
            result = verify(args.dataset)
            (args.dataset / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=1))
        else:
            report = build(args.hq_dir, args.out)
            (args.out / "build_report.json").write_text(json.dumps(report, indent=2) + "\n")
            for name, item in report["conditions"].items():
                lags = sorted({check["lag_samples"] for check in item["mix_checks"]})
                edges = [check["mix_content_edge_hz"] for check in item["mix_checks"]]
                print(f"{name}: mix lags {lags}, mix content edge {min(edges)}-{max(edges)} Hz")
    except (probe.ProbeError, subprocess.CalledProcessError, OSError) as exc:
        print("separation_controls: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
