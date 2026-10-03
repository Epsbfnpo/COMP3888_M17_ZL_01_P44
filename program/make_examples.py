"""Create deterministic engineering fixtures; these are not human-authorship evidence."""

import base64
import hashlib
import io
import json
import math
import shutil
import struct
import wave
from pathlib import Path

BASE = Path(__file__).resolve().parent
OPTIONS = {"include_reasoning": True, "offline_only": True, "timeout_seconds": 60}


def wav_bytes(amplitude, frequency=220):
    samples = [round(amplitude * math.sin(2 * math.pi * frequency * n / 8000)) for n in range(4000)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return buf.getvalue()


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    folder = BASE / "examples" / "valid-generated"
    (folder / "objects").mkdir(parents=True, exist_ok=True)
    nodes, objects, previous = [], {}, None
    for role, amplitude, relationship in [
        ("raw-track", 1000, None), ("stem", 700, "stemmed_from"),
        ("mix", 600, "mixed_from"), ("master", 800, "mastered_from"),
    ]:
        data = wav_bytes(amplitude)
        digest = hashlib.sha256(data).hexdigest()
        relative = f"objects/{digest}.wav"
        (folder / relative).write_bytes(data)
        objects[digest] = relative
        production = {"role": "pre_master"} if role == "mix" else {}
        nodes.append({"artefact_hash": digest, "artefact_type": f"audio/{role}",
                      "attributes": {"creation_method": "synthesised", "production": production,
                                     "technical": {"duration_seconds": 0.5, "sample_rate_hz": 8000,
                                                   "channels": 1, "bit_depth": 16}},
                      "evidence": [] if previous is None else [
                          {"hash": previous, "relationship_type": relationship, "attributes": {}}]})
        previous = digest
    request = {"run_id": "842498bc-b128-4b49-a275-6ce319b4d67e", "case_id": "LOCAL-GENERATED-VALID",
               "profile": "music-native-v0.1", "options": OPTIONS,
               "workflow": {"workflow_id": "mastering_service_only", "modifiers": [],
                            "description": "A supplied pre-master mix is mastered to the final WAV.",
                            "declarations": {"submitter_confirmation": True,
                                             "contributors": ["Fixture Mastering Engineer"]}},
               "chain": {"schema_version": "0.0.3", "hash_method": "sha256",
                         "final_artefact_hash": previous, "artefacts": nodes}, "objects": objects}
    save_json(folder / "request.json", request)
    save_json(folder / "submission.json", request)
    (folder / "README.md").write_text(
        "These four PCM WAV files are deterministic software-generated sine waves.\n"
        "They exercise graph loading, metadata extraction and endpoint checks.\n"
        "They are NOT a real recording session, an authenticated production history,\n"
        "or an honest-human ground-truth sample for false-accept/false-reject evaluation.\n")
    csec_folder = BASE / "examples" / "csec-wav"
    (csec_folder / "objects").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(folder / objects[previous], csec_folder / objects[previous])
    save_json(csec_folder / "request.json", {
        "schema_version": "0.2-candidate", "run_id": "842498bc-b128-4b49-a275-6ce319b4d681",
        "case_id": "LOCAL-GENERATED-CSEC-WAV", "profile": "csec-public-bundle-v0.2-candidate",
        "bundle": {"case_id": "LOCAL-GENERATED-CSEC-WAV", "final-artefact": previous,
                   "artefacts": [{"hash": previous, "hash-method": "sha256", "type": "audio/wav", "evidence": []}]},
        "objects": {previous: objects[previous]}, "options": OPTIONS})
    broken = BASE / "examples" / "invalid-hash"
    shutil.copytree(folder / "objects", broken / "objects", dirs_exist_ok=True)
    bad_request = json.loads(json.dumps(request))
    bad_request["case_id"] = "LOCAL-INVALID-HASH"
    bad_request["run_id"] = "842498bc-b128-4b49-a275-6ce319b4d67f"
    save_json(broken / "request.json", bad_request)
    final_path = broken / objects[previous]
    modified = bytearray(final_path.read_bytes())
    modified[-1] ^= 1
    final_path.write_bytes(modified)

    # A separate candidate-protocol example with an actual PNG object.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=")
    digest = hashlib.sha256(png).hexdigest()
    unsupported = BASE / "examples" / "unsupported-png"
    (unsupported / "objects").mkdir(parents=True, exist_ok=True)
    (unsupported / f"objects/{digest}.png").write_bytes(png)
    save_json(unsupported / "request.json", {
        "schema_version": "0.2-candidate", "run_id": "842498bc-b128-4b49-a275-6ce319b4d680",
        "case_id": "LOCAL-UNSUPPORTED-PNG", "profile": "csec-public-bundle-v0.2-candidate",
        "bundle": {"case_id": "LOCAL-UNSUPPORTED-PNG", "final-artefact": digest,
                   "artefacts": [{"hash": digest, "hash-method": "sha256", "type": "image/png", "evidence": []}]},
        "objects": {digest: f"objects/{digest}.png"}, "options": OPTIONS})
    print("Created valid-generated, csec-wav, invalid-hash and unsupported-png fixtures.")


if __name__ == "__main__":
    main()
