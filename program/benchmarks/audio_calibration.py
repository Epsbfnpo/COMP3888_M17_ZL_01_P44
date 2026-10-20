"""Replay frozen audio-profile measurements without making account requests.

python -B benchmarks/audio_calibration.py manifest.json --partition test --out /tmp/replay.json

Manifest: {"families": [{"id": "N02", "partition": "test", "path": "/data/N02.wav",
                         "sha256": "..."}]}. Paths may be relative to the manifest.
All derivatives and mixture parents remain inside the requested partition.
Known DSP relationships are ground truth; distinct recordings are controls,
not a claim about authorship or absence of shared musical material.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys

import numpy as np
PROGRAM = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROGRAM), str(PROGRAM / "music_target/audio_diagnostics/vendor/audfprint")]
from music_target.audio_diagnostics.policy import DEFAULTS
from music_target.audio_diagnostics.providers.music_semantics import _describe, _comparison
from music_target.audio_diagnostics.providers.calibration import qualify_comparison, fingerprint_support, profile
from music_target.audio_diagnostics.providers.alignment import align_tokens


def load_families(manifest, partition):
    path = Path(manifest).resolve()
    families = json.loads(path.read_text())["families"]
    ids, hashes = set(), {}
    for row in families:
        if row["id"] in ids:
            raise ValueError("Duplicate family id")
        ids.add(row["id"])
        file = Path(row["path"])
        file = file if file.is_absolute() else path.parent / file
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        if digest != row["sha256"]:
            raise ValueError("Audio hash mismatch: " + row["id"])
        if digest in hashes:
            raise ValueError("Duplicate audio assigned to multiple families/partitions")
        hashes[digest] = row["partition"]
        row["path"] = str(file)
    selected = [row for row in families if row["partition"] == partition]
    if len(selected) < 2:
        raise ValueError("At least two families required in the selected partition")
    return selected


def semantic_rows(families):
    import librosa
    parameters = DEFAULTS["music_semantics"]
    rate = parameters["sample_rate_hz"]
    descriptions, rows = {}, []
    def describe(y):
        return _describe({"samples": y, "range": [0., len(y) / rate]}, parameters, librosa)
    def compare(name, source, target, label, truth=None):
        raw = _comparison(source, target, parameters, librosa)
        decision = qualify_comparison(raw, parameters)
        if any(d["beat_tracking_status"] != "tracked" for d in (source, target)):
            decision["status"] = "abstained"
            decision["rejection_reasons"].append("insufficient_tracked_beats")
        best = max(raw["candidates"], key=lambda item: item["harmonic_similarity"])
        rows.append({"case": name, "label": label, "truth": truth,
                     "assessment": decision, "best_pitch": best["transposition_semitones"],
                     "time_grid_tempo": raw["time_grid_tempo"], "best_beat_candidate": best})
    for family in families:
        name = family["id"]
        y, _ = librosa.load(family["path"], sr=rate, duration=24)
        source = descriptions[name] = describe(y)
        for variant, speed, pitch in [("identity", 1., 0), ("slow", .8, 0), ("fast", 1.12, 0),
                                       ("faster", 1.35, 0), ("up", 1., 2), ("down", 1., -3), ("combined", 1.12, 2)]:
            target = y if speed == 1 else librosa.effects.time_stretch(y, rate=speed)
            if pitch:
                target = librosa.effects.pitch_shift(target, sr=rate, n_steps=pitch)
            compare(name + "_" + variant, source, describe(target), "known_dsp_derivative", {"tempo_ratio": speed, "transposition": pitch})
        compare(name + "_crop", source, describe(y[5 * rate:17 * rate]), "known_dsp_derivative", {"tempo_ratio": 1., "transposition": 0})
        noisy = y + np.random.default_rng(3888).normal(size=len(y)) * np.sqrt(np.mean(y * y) / 10 ** 1.5)
        compare(name + "_noise", source, describe(noisy), "known_dsp_derivative", {"tempo_ratio": 1., "transposition": 0})
    for a, b in itertools.combinations(descriptions, 2):
        compare(a + "__" + b, descriptions[a], descriptions[b], "distinct_recording_control")
    y = (.1 * np.sin(2 * np.pi * 440 * np.arange(rate * 24) / rate)).astype(np.float32)
    tone = describe(y)
    compare("sustained_tone", tone, tone, "low_information_control")
    return rows


def fingerprint_rows(families):
    import librosa
    import audfprint_analyze as upstream
    parameters = DEFAULTS["audfprint"]
    rate = parameters["sample_rate_hz"]
    sounds = {row["id"]: librosa.load(row["path"], sr=rate, duration=24)[0] for row in families}
    rows, cache = [], {}
    def tokens(name, y, density):
        key = (name, density)
        if key not in cache:
            analyzer = upstream.Analyzer(density=density)
            analyzer.target_sr = rate
            hashes = upstream.landmarks2hashes(analyzer.peaks2landmarks(analyzer.find_peaks(y, rate)))
            cache[key] = [(int(row[0]), int(row[1])) for row in hashes]
        return cache[key]
    def compare(name, first, second, y, z, offset):
        for density in (20, 40):
            p = {**parameters, "density": density}
            analyzer = upstream.Analyzer(density=density)
            matches, metrics, _ = align_tokens(tokens(first, y, density), tokens(second, z, density),
                analyzer.n_hop / rate, {"range": [0., len(y) / rate]}, {"range": [0., len(z) / rate]}, p)
            for match in matches:
                match["support_assessment"] = fingerprint_support(match, metrics, p)
            rows.append({"case": name, "density": density, "expected_offset": offset, "matches": matches, "metrics": metrics})
    names = list(sounds)
    for index, name in enumerate(names):
        y = sounds[name]
        other = sounds[names[(index + 1) % len(names)]]
        other = np.pad(other, (0, max(0, len(y) + 2 * rate - len(other))))[:len(y) + 2 * rate]
        for gain in (1., .5, .25):
            z = other.copy(); z[2 * rate:2 * rate + len(y)] += gain * y
            variant = name + f"_mix{gain}"
            compare(variant, name, variant, y, z, -2.)
        compare(name + "_crop", name, name + "_crop", y, y[5 * rate:17 * rate], 5.)
    for a, b in itertools.combinations(names, 2):
        compare(a + "__" + b, a, b, sounds[a], sounds[b], None)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--mode", choices=("semantics", "fingerprint"), default="semantics")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    families = load_families(args.manifest, args.partition)
    rows = semantic_rows(families) if args.mode == "semantics" else fingerprint_rows(families)
    output = {"profile_sha256": profile()["freeze_sha256"], "partition": args.partition,
              "mode": args.mode, "families": [row["id"] for row in families], "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(f"{len(rows)} observations written to {args.out}")


if __name__ == "__main__":
    main()
