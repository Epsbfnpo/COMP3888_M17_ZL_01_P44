"""Run the evaluator on public music datasets that carry production ground truth.

Datasets are not included; see benchmarks/README.md for sources and licences.
Each track becomes one native request whose relationships are known to be
true (the dataset says which stems make the mix), plus one negative control:
a stem from another track declared as a source of this mix. Rows record what
each audio observation reported for true and control relationships, and the
summary gives, per metric, how often a true relationship outranks a control
(ROC AUC). Every run is a separate process with a memory limit.

    python -B benchmarks/music_benchmark.py slakh /data/babyslakh_16k --out /tmp/bench
    python -B benchmarks/music_benchmark.py musdb /data/musdb18hq/test --out /tmp/bench
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import uuid

PROGRAM = Path(__file__).resolve().parents[1]
TEMPLATE = PROGRAM / "examples/valid-generated/request.json"
MEMORY_LIMIT_BYTES = 2 * 1024 ** 3


# --- dataset adapters --------------------------------------------------------

def _wav(path, objects):
    """Store a WAV (or FLAC converted to WAV) under objects/<sha256>.wav."""
    path = Path(path)
    if path.suffix.lower() == ".flac":
        import soundfile
        data, rate = soundfile.read(str(path), dtype="int16", always_2d=True)
        temporary = objects / "converting.wav"
        soundfile.write(str(temporary), data, rate, subtype="PCM_16")
        path = temporary
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    destination = objects / f"{digest}.wav"
    if not destination.exists():
        shutil.copyfile(path, destination)
    if path.name == "converting.wav":
        path.unlink()
    return digest


def _store(path, objects, suffix):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    destination = objects / f"{digest}.{suffix}"
    if not destination.exists():
        shutil.copyfile(path, destination)
    return digest


def _first(folder, stem):
    for suffix in (".wav", ".flac"):
        if (folder / (stem + suffix)).exists():
            return folder / (stem + suffix)
    return None


def slakh_tracks(root):
    """Slakh2100/BabySlakh: MIDI/Sxx.mid renders stems/Sxx.*, which mix into mix.*."""
    for track in sorted(path for path in Path(root).iterdir() if (path / "stems").is_dir()):
        mix = _first(track, "mix")
        stems = []
        for audio in sorted((track / "stems").iterdir()):
            midi = track / "MIDI" / (audio.stem + ".mid")
            if audio.suffix.lower() in (".wav", ".flac"):
                stems.append({"audio": audio, "midi": midi if midi.exists() else None})
        if mix and stems:
            yield {"track": track.name, "mix": mix, "stems": stems}


def musdb_tracks(root):
    """MUSDB18-HQ: drums/bass/other/vocals.wav mix into mixture.wav."""
    for track in sorted(path for path in Path(root).iterdir() if (path / "mixture.wav").exists()):
        stems = [{"audio": track / f"{name}.wav", "midi": None}
                 for name in ("drums", "bass", "other", "vocals") if (track / f"{name}.wav").exists()]
        if stems:
            yield {"track": track.name, "mix": track / "mixture.wav", "stems": stems}


ADAPTERS = {"slakh": (slakh_tracks, "midi_virtual_instrument_production"),
            "musdb": (musdb_tracks, "multitrack_recording_mix_master")}


# --- requests ----------------------------------------------------------------

def build_requests(kind, root, bundle, seed=0):
    """Native requests with true stems plus one control stem from another track."""
    adapter, workflow = ADAPTERS[kind]
    objects = bundle / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    tracks = list(adapter(root))
    rng = random.Random(seed)
    template = json.loads(TEMPLATE.read_text())
    requests, labels = [], {}
    for index, track in enumerate(tracks):
        nodes, edges, refs = [], [], {}
        for stem in track["stems"]:
            audio = _wav(stem["audio"], objects)
            refs[audio] = "wav"
            stem_edges = []
            if stem["midi"]:
                midi = _store(stem["midi"], objects, "mid")
                refs[midi] = "mid"
                nodes.append({"artefact_hash": midi, "artefact_type": "project/midi", "attributes": {}, "evidence": []})
                stem_edges.append({"hash": midi, "relationship_type": "rendered_from", "attributes": {}})
            nodes.append({"artefact_hash": audio, "artefact_type": "audio/stem", "attributes": {},
                          "evidence": stem_edges})
            edges.append(audio)
            labels[(track["track"], audio)] = "true_stem"
        others = [other for position, other in enumerate(tracks) if position != index]
        if others:
            donor = rng.choice(others)
            control = _wav(rng.choice(donor["stems"])["audio"], objects)
            if control not in refs:
                refs[control] = "wav"
                nodes.append({"artefact_hash": control, "artefact_type": "audio/stem", "attributes": {},
                              "evidence": []})
                edges.append(control)
                labels[(track["track"], control)] = "control_other_track"
        mix = _wav(track["mix"], objects)
        refs[mix] = "wav"
        nodes.append({"artefact_hash": mix, "artefact_type": "audio/mix", "attributes": {},
                      "evidence": [{"hash": digest, "relationship_type": "mixed_from", "attributes": {}}
                                   for digest in edges]})
        request = json.loads(json.dumps(template))
        request.update(run_id=str(uuid.uuid4()), case_id=f"BENCH-{kind}-{track['track']}"[:120])
        request["workflow"].update(workflow_id=workflow, modifiers=[],
                                   description="Public dataset benchmark; dataset metadata is the ground truth.")
        request["options"]["timeout_seconds"] = 3600
        request["chain"].update(final_artefact_hash=mix, artefacts=nodes)
        request["objects"] = {digest: f"objects/{digest}.{suffix}" for digest, suffix in refs.items()}
        requests.append((track["track"], request))
    return requests, labels


# --- execution ---------------------------------------------------------------

def run_guarded(request_path, bundle, output_path, python=sys.executable):
    command = [python, "-B", str(PROGRAM / "run.py"), "--native", str(request_path), "--root", str(bundle),
               "--output", str(output_path), "--audio-analysis"]
    started, peak, killed = time.time(), 0, False
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=PROGRAM)
    try:
        import psutil
        parent = psutil.Process(process.pid)
        while process.poll() is None:
            try:
                tree = [parent] + parent.children(recursive=True)
                rss = sum(item.memory_info().rss for item in tree)
            except psutil.Error:
                rss = 0
            peak = max(peak, rss)
            if rss > MEMORY_LIMIT_BYTES:
                killed = True
                for item in reversed(tree):
                    try:
                        item.kill()
                    except psutil.Error:
                        pass
                break
            time.sleep(0.2)
    except ImportError:
        pass  # without psutil the run is unguarded; the summary says so
    _, stderr = process.communicate()
    return {"exit": process.returncode, "killed_over_memory_limit": killed, "peak_rss_mb": round(peak / 1e6),
            "wall_seconds": round(time.time() - started, 2), "stderr_tail": stderr.decode()[-800:]}


def _rows(track, response, labels):
    checks = response.get("assessment_checks") or {}
    rows = []
    for key in ("audio_fingerprint", "audio_fingerprint_agreement", "audio_semantics"):
        for item in checks.get(key) or []:
            label = labels.get((track, item["source_hash"]))
            if label is None:
                continue
            metrics, matches = item.get("metrics") or {}, item.get("matches") or []
            best = matches[0] if matches else {}
            rows.append({"track": track, "label": label, "provider": item["provider"], "status": item["status"],
                         "reason_code": item["reason_code"], "candidates": len(matches),
                         "top_votes": best.get("matched_token_pairs") or min(
                             best.get("audfprint_votes", 0), best.get("spectromap_votes", 0)) or 0,
                         "supported_seconds": best.get("target_supported_window_duration_seconds"),
                         "harmonic_similarity_z": metrics.get("harmonic_similarity_z"),
                         "chord_sequence_similarity": metrics.get("chord_sequence_similarity")})
    return rows


def auc(positives, negatives):
    """Probability that a random true relationship outranks a random control."""
    pairs = [(p, n) for p in positives for n in negatives]
    if not pairs:
        return None
    return sum(1.0 if p > n else 0.5 if p == n else 0.0 for p, n in pairs) / len(pairs)


def summarise(rows):
    summary = {}
    for provider, metric in (("audfprint", "top_votes"), ("spectromap", "top_votes"),
                             ("fingerprint_agreement", "top_votes"),
                             ("music_semantics", "harmonic_similarity_z"),
                             ("music_semantics", "chord_sequence_similarity")):
        chosen = [row for row in rows if row["provider"] == provider and row["status"] != "unavailable"]
        values = lambda label: [row[metric] or 0 for row in chosen if row["label"] == label]
        summary[f"{provider}.{metric}"] = {
            "true_relationships": len(values("true_stem")), "controls": len(values("control_other_track")),
            "auc": auc(values("true_stem"), values("control_other_track"))}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=sorted(ADAPTERS))
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Only the first N tracks (for a quick check)")
    args = parser.parse_args()
    bundle = args.out / "bundle"
    requests, labels = build_requests(args.kind, args.dataset, bundle)
    if args.limit:
        requests = requests[:args.limit]
    rows, runs = [], []
    for track, request in requests:
        request_path = args.out / "requests" / f"{track}.json"
        response_path = args.out / "responses" / f"{track}.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(request))
        meta = run_guarded(request_path, bundle, response_path)
        runs.append({"track": track, **meta})
        if response_path.exists() and not meta["killed_over_memory_limit"]:
            rows.extend(_rows(track, json.loads(response_path.read_text()), labels))
        print(f"{track}: exit={meta['exit']} wall={meta['wall_seconds']}s peak={meta['peak_rss_mb']}MB", flush=True)
    result = {"dataset": args.kind, "tracks": len(requests), "runs": runs, "summary": summarise(rows)}
    (args.out / "rows.json").write_text(json.dumps(rows, indent=1))
    (args.out / "summary.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result["summary"], indent=1))


if __name__ == "__main__":
    main()
