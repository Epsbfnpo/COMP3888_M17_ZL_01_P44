"""False-match benchmark for unrelated-but-similar audio pairs.

Each pair runs through the real evaluator (core assessment plus the optional
audio diagnostics) as a derived_from relationship. Per category it counts how
often each provider reports a match. Whether a match is false depends on the
category: covers share a composition but no audio, and shared-loop pairs share
real audio but are otherwise different songs. Counts are case observations on a
small sample, never population rates.

    python -B benchmarks/false_match.py benchmarks/false_match_lumina_2026-10-21.json \
        --audio /path/to/clips --out /tmp/false_match.json [--write-status]
"""

import argparse
import copy
import datetime
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

PROGRAM = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROGRAM), str(PROGRAM / "vendor")]

import run  # noqa: E402
from music_target.audio_diagnostics.policy import DEFAULT_PATH  # noqa: E402
from music_target.audio_diagnostics.runner import augment_response  # noqa: E402

STATUS_PATH = PROGRAM / "policies" / "audio_calibration_status.json"
# A match per provider; the five-vote floor is audfprint's own candidate floor.
DEFINITIONS = {
    "audfprint": "a candidate offset whose support_assessment is supported_within_profile",
    "fingerprint_agreement": "an offset where audfprint and SpectroMap each cast at least 5 votes",
    "music_semantics": "correspondence_assessment.status is supported_within_profile",
}
MIN_AGREEMENT_VOTES = 5


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_shared_loop(spec, audio_dir, out_dir):
    """Mix a loop into a base song at a fixed offset and gain (deterministic)."""
    base, rate = sf.read(str(audio_dir / spec["base"]), dtype="float64", always_2d=True)
    loop, loop_rate = sf.read(str(audio_dir / spec["loop"]), dtype="float64", always_2d=True)
    if rate != loop_rate or base.shape[1] != loop.shape[1]:
        raise ValueError(f"{spec['id']}: base and loop must share sample rate and channel count")
    start = int(round(spec["offset_seconds"] * rate))
    end = min(len(base), start + len(loop))
    mixed = base.copy()
    mixed[start:end] += spec["loop_gain"] * loop[:end - start]
    mixed /= max(1.0, float(np.max(np.abs(mixed))) / 0.98)
    path = out_dir / f"{spec['id']}.wav"
    sf.write(str(path), mixed, rate, subtype="PCM_16")
    return path


def evaluate_pair(source, target, work):
    folder = Path(tempfile.mkdtemp(dir=work))
    (folder / "objects").mkdir()
    hashes = []
    for path in (source, target):
        digest = sha256(path)
        (folder / "objects" / f"{digest}.wav").write_bytes(Path(path).read_bytes())
        hashes.append(digest)
    if hashes[0] == hashes[1]:
        raise ValueError(f"{source} and {target} are byte-identical")
    request = json.loads((PROGRAM / "examples/valid-generated/request.json").read_text())
    request["workflow"]["workflow_id"] = "ai_assisted_or_generated_production"
    request["workflow"]["modifiers"] = ["includes_ai_generated_audio"]
    request["options"]["timeout_seconds"] = 600
    request["chain"]["final_artefact_hash"] = hashes[1]
    request["chain"]["artefacts"] = [
        {"artefact_hash": hashes[0], "artefact_type": "audio/ai-source",
         "attributes": {"creation_method": "ai-generated"}, "evidence": []},
        {"artefact_hash": hashes[1], "artefact_type": "audio/master",
         "attributes": {"creation_method": "ai-generated"},
         "evidence": [{"hash": hashes[0], "relationship_type": "derived_from", "attributes": {}}]}]
    request["objects"] = {digest: f"objects/{digest}.wav" for digest in hashes}
    core = run.evaluate(copy.deepcopy(request), folder, "native")
    if core["execution_status"] != "succeeded":
        raise RuntimeError(f"core evaluation failed: {core.get('error')}")
    return augment_response(core, request, folder, "native", DEFAULT_PATH, time.monotonic() + 600)


def observe(response):
    checks = response["assessment_checks"]
    rows = {row["provider"]: row for key in ("audio_fingerprint", "audio_fingerprint_agreement", "audio_semantics")
            for row in checks.get(key) or []}
    audfprint = rows.get("audfprint") or {}
    agreement = rows.get("fingerprint_agreement") or {}
    semantics = rows.get("music_semantics") or {}
    supported = [match for match in audfprint.get("matches") or []
                 if (match.get("support_assessment") or {}).get("status") == "supported_within_profile"]
    agreed = [match for match in agreement.get("matches") or []
              if (match.get("audfprint_votes") or 0) >= MIN_AGREEMENT_VOTES
              and (match.get("spectromap_votes") or 0) >= MIN_AGREEMENT_VOTES]
    metrics = semantics.get("metrics") or {}
    assessment = metrics.get("correspondence_assessment") or {}
    return {
        "statuses": {name: row.get("status") for name, row in rows.items()},
        "audfprint": {"match": bool(supported),
                      "offsets": [round(match["source_minus_target_offset_seconds"], 3) for match in supported]},
        "fingerprint_agreement": {"match": bool(agreed),
                                  "offsets": [round(match["source_minus_target_offset_seconds"], 3) for match in agreed]},
        "music_semantics": {"match": assessment.get("status") == "supported_within_profile",
                            "status": assessment.get("status"),
                            "rejection_reasons": assessment.get("rejection_reasons"),
                            "transposition_semitones": metrics.get("transposition_semitones"),
                            "tempo_ratio": metrics.get("tempo_ratio"),
                            "harmonic_similarity_z": metrics.get("harmonic_similarity_z"),
                            "source_key": (metrics.get("source") or {}).get("key"),
                            "target_key": (metrics.get("target") or {}).get("key"),
                            "source_tempo_bpm": (metrics.get("source") or {}).get("tempo_bpm"),
                            "target_tempo_bpm": (metrics.get("target") or {}).get("tempo_bpm")},
    }


def summarize(manifest, results):
    """Per provider and category: pairs, matches, and whether a match is a false match."""
    summary = {}
    for provider in DEFINITIONS:
        categories = {}
        for name, category in manifest["categories"].items():
            rows = [row for row in results if row["category"] == name and not row.get("error")]
            matches = sum(row["observation"][provider]["match"] for row in rows)
            if provider in category.get("unclassified", []):
                entry = {"pairs": len(rows), "supported": matches, "note": category["unclassified_reason"]}
            elif provider in category.get("expected_match", []):
                entry = {"pairs": len(rows), "detected": matches, "note": category["expected_reason"]}
            else:
                entry = {"pairs": len(rows), "false_matches": matches}
            categories[name] = entry
        summary[provider] = {"benchmark": manifest["benchmark_id"], "definition": DEFINITIONS[provider],
                             "date": manifest["date"], "categories": categories,
                             "scope": "Case counts on a small Lumina sample; not a population false-positive rate."}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--audio", type=Path, required=True, help="Directory holding the manifest's clips")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--write-status", action="store_true",
                        help="Record the counts as false_match_rates in policies/audio_calibration_status.json")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        files = {}
        for clip in manifest["clips"]:
            path = args.audio / clip["file"]
            if sha256(path) != clip["sha256"]:
                raise SystemExit(f"{clip['file']} does not match its recorded SHA-256")
            files[clip["id"]] = path
        for spec in manifest.get("derived", []):
            files[spec["id"]] = make_shared_loop(spec, args.audio, work)
        results = []
        for pair in manifest["pairs"]:
            row = {"pair_id": pair["pair_id"], "category": pair["category"],
                   "source": pair["source"], "target": pair["target"]}
            try:
                response = evaluate_pair(files[pair["source"]], files[pair["target"]], work)
                row["observation"] = observe(response)
            except Exception as exc:  # recorded per pair; never silently dropped
                row["error"] = f"{type(exc).__name__}: {exc}"
            results.append(row)
            print(f"{pair['pair_id']:<28} {pair['category']:<26} " + (row.get("error") or " ".join(
                f"{name}={'Y' if row['observation'][name]['match'] else '-'}" for name in DEFINITIONS)), flush=True)
    summary = summarize(manifest, results)
    args.out.write_text(json.dumps({"manifest": manifest["benchmark_id"], "results": results,
                                    "summary": summary}, indent=2) + "\n")
    if args.write_status:
        status = json.loads(STATUS_PATH.read_text())
        for provider, record in summary.items():
            status["providers"][provider]["false_match_rates"] = record
        status["false_match_updated"] = datetime.date.today().isoformat()
        STATUS_PATH.write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
