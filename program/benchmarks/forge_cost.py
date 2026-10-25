"""Forge-cost derivation and confidence ranking check on a recorder-take production.

Part 1, derivation (development split). A claim is forged in the file that
holds it and, in turn, in every combination of the files that corroborate it;
the core evaluator runs on each variant. The measured cost is the size of the
smallest set of files that must change together for the forged claim to raise
no contradiction, plus one when that set includes a binary container (BWF/iXML
chunks, AAF) that needs specialist tools. Claims:

- a recorder take's take number: held in the WAV, corroborated by the sound
  report and the AAF;
- the same take's start timecode, moved by one second;
- one cue range in a cue sheet: held in the cue sheet alone.

Recorder metadata takes the cheaper of its two measured claims. A plain
document checked against nothing harder takes the cue-sheet cost. Binary
production documents (AAF, DAW session) not checked against recorder metadata
take the same rule by construction: one file plus the binary surcharge. A
trusted signature cannot be produced by editing files (an edited Lumina file
becomes invalid), so it takes the top of the scale.

Part 2, ranking (development split). Evidence mixes built from half of the
takes are evaluated with the derived costs under a grid of strength floors.
The orderings below are stated before any result is seen; a strict ordering
must hold by at least MARGIN, a non-decreasing one must not fall. Ranking can
only bound the floor to an interval; the pre-registered floor is kept if it
lies inside it.

Part 3, held out. The unmodified production is evaluated once, with the chosen
profile, after Parts 1 and 2. It is not used to choose anything.

    python -B benchmarks/forge_cost.py /path/to/bundle --take 10T07.WAV --out results.json

The bundle is a native request (submission.json) with its objects/ folder.
"""

import argparse
import copy
import datetime
import hashlib
import itertools
import json
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import run  # noqa: E402
from music_target import assessment_passes  # noqa: E402
from music_target.scoring_profile import load_default_scoring_profile  # noqa: E402

MARGIN = 0.02
FLOORS = (0.0, 0.25, 0.5, 0.75, 0.9)
PREREGISTERED_FLOOR = 0.5
TEXT, BINARY = "text", "binary"

# Orderings stated in advance: (higher, relation, lower, reason).
ORDERINGS = [
    ("sound_report", ">", "cue_sheet",
     "a sound report checked against recorder metadata beats a cue sheet checked only against file length"),
    ("aaf", ">", "cue_sheet",
     "an AAF checked against recorder metadata beats a cue sheet checked only against file length"),
    ("cue_sheet", ">", "no_documents", "checked cross-evidence beats none"),
    ("cue_sheet_and_sound_report", ">=", "sound_report",
     "adding a weaker corroborating document never lowers confidence"),
    ("aaf_and_sound_report", ">=", "aaf", "adding a corroborating document never lowers confidence"),
]


def load(bundle):
    request = json.loads((bundle / "submission.json").read_text())
    return request, {digest: bundle / path for digest, path in request["objects"].items()}


def evaluate(request, files, profile=None):
    folder = Path(tempfile.mkdtemp(prefix="forge-cost-"))
    try:
        (folder / "objects").mkdir()
        request = copy.deepcopy(request)
        for digest in list(request["objects"]):
            target = folder / "objects" / Path(files[digest]).name
            try:
                os.link(files[digest], target)
            except OSError:
                shutil.copyfile(files[digest], target)
            request["objects"][digest] = "objects/" + target.name
        with mock.patch.object(assessment_passes, "load_default_scoring_profile",
                               return_value=profile or load_default_scoring_profile()):
            return run.evaluate(request, folder, "native")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def add_object(request, files, data, suffix, folder):
    digest = hashlib.sha256(data).hexdigest()
    path = folder / f"{digest}{suffix}"
    path.write_bytes(data)
    request["objects"][digest] = path.name
    files[digest] = path
    return digest


def replace_object(request, files, old, data, suffix, folder):
    """Swap one object's bytes, re-hash it and update every reference."""
    new = add_object(request, files, data, suffix, folder)
    request["objects"].pop(old)
    for node in request["chain"]["artefacts"]:
        if node["artefact_hash"] == old:
            node["artefact_hash"] = new
        for edge in node["evidence"]:
            if edge["hash"] == old:
                edge["hash"] = new
    if request["chain"]["final_artefact_hash"] == old:
        request["chain"]["final_artefact_hash"] = new
    return new


def drop(request, digests):
    request["chain"]["artefacts"] = [node for node in request["chain"]["artefacts"]
                                     if node["artefact_hash"] not in digests]
    for node in request["chain"]["artefacts"]:
        node["evidence"] = [edge for edge in node["evidence"] if edge["hash"] not in digests]
    for digest in digests:
        request["objects"].pop(digest, None)


def chunks(data):
    position = 12
    while position + 8 <= len(data):
        name, size = data[position:position + 4], struct.unpack("<I", data[position + 4:position + 8])[0]
        yield name, position + 8, size
        position += 8 + size + (size & 1)


def shift_bext_time_reference(data, seconds):
    rate = next(struct.unpack("<I", data[start + 4:start + 8])[0] for name, start, _ in chunks(data) if name == b"fmt ")
    out = bytearray(data)
    for name, start, _ in chunks(data):
        if name == b"bext":
            low, high = struct.unpack("<II", data[start + 338:start + 346])
            value = ((high << 32) | low) + round(seconds * rate)
            out[start + 338:start + 346] = struct.pack("<II", value & 0xFFFFFFFF, value >> 32)
            return bytes(out)
    raise ValueError("no bext chunk")


def edit_sound_report(data, file_name, column, old, new):
    lines = data.decode("utf-8").split("\n")
    for index, line in enumerate(lines):
        cells = line.split(",")
        if cells and cells[0].strip().upper() == file_name.upper():
            if cells[column].strip('"') != old:
                raise ValueError(f"unexpected sound-report value {cells[column]!r}, expected {old!r}")
            cells[column] = cells[column].replace(old, new)
            lines[index] = ",".join(cells)
            return "\n".join(lines).encode("utf-8")
    raise ValueError("take not found in sound report")


def edit_aaf(path, out, file_name, take=None, shift_frames=0):
    import aaf2
    shutil.copyfile(path, out)
    with aaf2.open(str(out), "rw") as handle:
        for master in handle.content.mastermobs():
            comments = {item.name: item for item in master["UserComments"].value} if "UserComments" in master else {}
            if str(getattr(comments.get("TapeID"), "value", "")).upper() != file_name.upper():
                continue
            if take is not None:
                comments["Take"].value = take
            for slot in master.slots:
                file_mob = getattr(slot.segment, "mob", None)
                for file_slot in (file_mob.slots if file_mob is not None else []):
                    if shift_frames and getattr(file_slot.segment, "mob", None) is not None:
                        file_slot.segment.start = file_slot.segment.start + shift_frames
        handle.save()
    return out.read_bytes()


def cue_sheet(takes):
    return json.dumps({"schema_version": "1.0", "sheet_type": "cue", "production_title": "forge-cost benchmark",
                       "cues": [{"cue_id": f"C{index + 1}", "asset_hash": digest, "start_seconds": 0.0,
                                 "end_seconds": round(recorder["duration_seconds"] - 1.0, 3)}
                                for index, (digest, recorder) in enumerate(takes)]}, indent=1).encode()


def add_document(request, files, data, suffix, kind, folder):
    digest = add_object(request, files, data, suffix, folder)
    request["chain"]["artefacts"].insert(0, {"artefact_hash": digest, "artefact_type": kind,
                                             "attributes": {}, "evidence": []})
    # Documents reach the final artefact as inputs, like the production's sound reports.
    final = next(node for node in request["chain"]["artefacts"]
                 if node["artefact_hash"] == request["chain"]["final_artefact_hash"])
    final["evidence"].append({"hash": digest, "relationship_type": "input_to", "attributes": {}})
    return digest


def summary(response):
    checks = response.get("assessment_checks") or {}
    decision = (checks.get("integrity_decision") or [{}])[0]
    failed = sorted({fact["rule"] for fact in checks.get("cross_evidence_scored_facts", [])
                     if fact.get("passed") is False})
    integrity = (response.get("axes") or {}).get("integrity") or {}
    return {"execution_status": response["execution_status"], "contradictions": decision.get("contradiction_count"),
            "failed_rules": failed, "integrity": integrity.get("value"), "confidence": integrity.get("confidence")}


def locate(request, files):
    response = evaluate(request, files)
    types = {node["artefact_hash"]: node["artefact_type"] for node in request["chain"]["artefacts"]}
    takes = {}
    for row in response["findings"]:
        recorder = row.get("recorder_metadata") or {}
        if row.get("code") == "WAV_OBSERVATION" and recorder.get("file_name"):
            takes[recorder["file_name"].upper()] = (row["evidence_hash"], recorder)
    reports = [digest for digest, kind in types.items() if kind == "text/sound-report"]
    aafs = [digest for digest, kind in types.items() if kind == "project/aaf"]
    return takes, reports, aafs


def forge(request, files, holder, corroborators, folder):
    """Forge the claim in its holder plus each subset of corroborators.

    ``holder`` and every corroborator are (digest, suffix, kind, edit), where
    edit(path, folder) returns the forged bytes.
    """
    sources = {"holder": holder, **corroborators}
    rows = []
    for size in range(len(corroborators) + 1):
        for subset in itertools.combinations(corroborators, size):
            variant, variant_files = copy.deepcopy(request), dict(files)
            edited = ["holder", *subset]
            for name in edited:
                digest, suffix, _, edit = sources[name]
                replace_object(variant, variant_files, digest, edit(variant_files[digest], folder), suffix, folder)
            rows.append({"forged_in": edited, "binary_edit": any(sources[name][2] == BINARY for name in edited),
                         **summary(evaluate(variant, variant_files))})
    undetected = [row for row in rows if row["contradictions"] == 0 and row["execution_status"] == "succeeded"]
    smallest = min(undetected, key=lambda row: (len(row["forged_in"]), row["binary_edit"])) if undetected else None
    return {"rows": rows, "smallest_undetected_forgery": smallest and smallest["forged_in"],
            "measured_cost": None if smallest is None else len(smallest["forged_in"]) + int(smallest["binary_edit"])}


def derive(request, files, take_name, folder):
    takes, reports, aafs = locate(request, files)
    digest, recorder = takes[take_name.upper()]
    report = next(item for item in reports if take_name.upper().encode() in files[item].read_bytes().upper())
    line = next(row for row in files[report].read_text().splitlines() if row.upper().startswith(take_name.upper()))
    old_take, old_tc = line.split(",")[2].strip('"'), line.split(",")[4].strip('"')
    new_take = str(int(old_take) + 1).zfill(len(old_take))
    hh, mm, ss, ff = (int(part) for part in old_tc.split(":"))
    new_tc = f"{hh:02d}:{mm:02d}:{ss + 1:02d}:{ff:02d}"

    def take_in_wav(path, _):
        return (path.read_bytes().replace(f"<TAKE>{old_take}</TAKE>".encode(), f"<TAKE>{new_take}</TAKE>".encode())
                .replace(f"sTAKE={old_take}".encode(), f"sTAKE={new_take}".encode()))

    claims = {
        "take_number": forge(request, files, (digest, ".wav", BINARY, take_in_wav), {
            "sound_report": (report, ".csv", TEXT, lambda path, _: edit_sound_report(
                path.read_bytes(), take_name, 2, old_take, new_take)),
            "aaf": (aafs[0], ".aaf", BINARY, lambda path, work: edit_aaf(
                path, work / "edit.aaf", take_name, take=new_take))}, folder),
        "start_timecode_plus_1s": forge(request, files, (digest, ".wav", BINARY, lambda path, _:
                                                         shift_bext_time_reference(path.read_bytes(), 1.0)), {
            "sound_report": (report, ".csv", TEXT, lambda path, _: edit_sound_report(
                path.read_bytes(), take_name, 4, old_tc, new_tc)),
            "aaf": (aafs[0], ".aaf", BINARY, lambda path, work: edit_aaf(
                path, work / "edit.aaf", take_name, shift_frames=25))}, folder),
    }
    claims["take_number"]["edit"] = [old_take, new_take]
    claims["start_timecode_plus_1s"]["edit"] = [old_tc, new_tc]
    with_cues, cue_files = copy.deepcopy(request), dict(files)
    cue = add_document(with_cues, cue_files, cue_sheet([(digest, recorder)]), ".json", "text/cue-sheet", folder)
    claims["cue_range"] = forge(with_cues, cue_files, (cue, ".json", TEXT, lambda path, _: path.read_bytes().replace(
        b'"start_seconds": 0.0', b'"start_seconds": 2.0')), {}, folder)
    claims["cue_range"]["baseline"] = summary(evaluate(with_cues, cue_files))
    claims["cue_range"]["edit"] = ["start 0.0 s", "start 2.0 s"]
    measured = {name: item["measured_cost"] for name, item in claims.items()}
    document_cost = measured["cue_range"]
    return claims, {"recorder_metadata": min(measured["take_number"], measured["start_timecode_plus_1s"]),
                    "plain_document": document_cost, "binary_document": document_cost + 1}


def derived_profile(base, costs, floor):
    profile = copy.deepcopy(base)
    forge_cost = profile["integrity"]["forge_cost"]
    forge_cost["tier_costs"] = {"signed_attestation": forge_cost["scale_max"],
                                "recorder_metadata": costs["recorder_metadata"]}
    forge_cost["submitter_document_costs"] = {"project/aaf": costs["binary_document"],
                                              "project/daw-session": costs["binary_document"]}
    forge_cost["submitter_document_default"] = costs["plain_document"]
    forge_cost["unbound_source_cost"] = costs["plain_document"]
    forge_cost["strength_floor"] = floor
    return profile


def mixes(request, files, folder):
    takes, reports, aafs = locate(request, files)
    ordered = sorted(takes.items())
    half = [value for _, value in ordered[: len(ordered) // 2]]
    base = copy.deepcopy(request)
    drop(base, {digest for _, (digest, _) in ordered[len(ordered) // 2:]})
    cases = {}
    for name, keep_aaf, keep_sr, add_cues in (("aaf_and_sound_report", True, True, False), ("aaf", True, False, False),
                                              ("sound_report", False, True, False),
                                              ("no_documents", False, False, False),
                                              ("cue_sheet", False, False, True),
                                              ("cue_sheet_and_sound_report", False, True, True)):
        variant, variant_files = copy.deepcopy(base), dict(files)
        drop(variant, set(([] if keep_aaf else aafs) + ([] if keep_sr else reports)))
        if add_cues:
            add_document(variant, variant_files, cue_sheet(half), ".json", "text/cue-sheet", folder)
        cases[name] = (variant, variant_files)
    return cases


def rank(cases, base, costs):
    grid = []
    for floor in (None, *FLOORS):
        if floor is None:
            profile = copy.deepcopy(base)
            profile["integrity"].pop("forge_cost")
        else:
            profile = derived_profile(base, costs, floor)
        values = {name: evaluate(variant, files, profile)["axes"]["integrity"]["confidence"]
                  for name, (variant, files) in cases.items()}
        checks = []
        for high, relation, low, reason in ORDERINGS:
            difference = round(values[high] - values[low], 3)
            held = difference >= MARGIN if relation == ">" else difference >= 0
            checks.append({"higher": high, "relation": relation, "lower": low, "reason": reason,
                           "difference": difference, "held": held})
        grid.append({"strength_floor": floor, "confidence": values, "orderings": checks,
                     "all_held": all(item["held"] for item in checks),
                     **({"label": "default-v6 (no forge cost)"} if floor is None else {})})
    passing = sorted(row["strength_floor"] for row in grid if row["all_held"] and row["strength_floor"] is not None)
    chosen = (PREREGISTERED_FLOOR if PREREGISTERED_FLOOR in passing else
              min(passing, key=lambda floor: abs(floor - PREREGISTERED_FLOOR)) if passing else None)
    return grid, passing, chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--take", required=True, help="recorder file name of the take to forge, e.g. 10T07.WAV")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    request, files = load(args.bundle)
    base = load_default_scoring_profile()
    with tempfile.TemporaryDirectory(prefix="forge-cost-edits-") as directory:
        folder = Path(directory)
        claims, costs = derive(request, files, args.take, folder)
        grid, passing, chosen = rank(mixes(request, files, folder), base, costs)
    held_out = None
    if chosen is not None:
        response = evaluate(request, files, derived_profile(base, costs, chosen))
        strength = response["assessment_checks"]["evidence_strength"][0]
        held_out = {"split": "held_out", "integrity": response["axes"]["integrity"]["value"],
                    "confidence": response["axes"]["integrity"]["confidence"],
                    "evidence_strength": {key: value for key, value in strength.items() if key != "subjects"}}
    result = {"benchmark": "forge_cost", "date": datetime.date.today().isoformat(),
              "case_id": request.get("case_id"), "take": args.take, "base_profile_id": base["profile_id"],
              "derivation": {"split": "development", "claims": claims, "derived_costs": costs,
                             "signed_attestation": "top of the scale: a trusted signature cannot be produced by "
                                                   "editing files; an edited signed file becomes invalid"},
              "ranking": {"split": "development", "margin": MARGIN, "preregistered_floor": PREREGISTERED_FLOOR,
                          "orderings_stated_in_advance": ORDERINGS, "grid": grid,
                          "passing_floors": passing, "chosen_floor": chosen},
              "held_out": held_out}
    args.out.write_text(json.dumps(result, indent=1, default=str) + "\n")
    for name, item in claims.items():
        print(f"{name}: smallest undetected forgery {item['smallest_undetected_forgery']}, cost {item['measured_cost']}")
    print("derived costs:", costs)
    for row in grid:
        print("floor", row["strength_floor"], "held:", row["all_held"], row["confidence"])
    print("passing floors:", passing, "chosen:", chosen)
    print("held-out:", held_out)


if __name__ == "__main__":
    main()
