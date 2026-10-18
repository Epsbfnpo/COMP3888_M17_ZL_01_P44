"""Create a reproducible teaching fixture with a repeated source and excerpt."""

import hashlib
import json
from pathlib import Path
import wave
import numpy as np


def create_example(folder=None):
    base = Path(__file__).resolve().parent
    folder = Path(folder) if folder else base / "examples/audio-diagnostics"
    (folder / "objects").mkdir(parents=True, exist_ok=True)
    rate = 22050
    rng = np.random.default_rng(42)
    t = np.arange(rate * 8) / rate
    motif = np.zeros(len(t))
    for index in range(16):
        a, b = index * rate // 2, (index + 1) * rate // 2
        frequency = rng.uniform(180, 1800)
        motif[a:b] = (.2 * np.sin(2 * np.pi * frequency * t[a:b])
                      + .12 * np.sin(2 * np.pi * frequency * 1.49 * t[a:b])
                      + .03 * rng.normal(size=b - a))
    motif *= .6 + .4 * np.sin(2 * np.pi * 2 * t) ** 2
    hashes, objects = [], {}
    for samples in (np.r_[motif, motif], motif[2 * rate:6 * rate]):
        import io
        data = io.BytesIO()
        with wave.open(data, "wb") as stream:
            stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            stream.writeframes((samples * 32767).astype("<i2").tobytes())
        digest = hashlib.sha256(data.getvalue()).hexdigest()
        relative = "objects/" + digest + ".wav"
        path = folder / relative
        if path.exists() and path.read_bytes() != data.getvalue():
            raise ValueError("Existing fixture object differs; refusing to overwrite it.")
        path.write_bytes(data.getvalue())
        hashes.append(digest)
        objects[digest] = relative
    request = json.loads((base / "examples/valid-generated/request.json").read_text())
    request["case_id"] = "AUDIO-OBSERVATION-FIXTURE"
    request["workflow"]["workflow_id"] = "sample_and_loop_based_production"
    request["workflow"]["description"] = "Synthetic repeated recording and four-second excerpt; not provenance ground truth."
    request["options"]["timeout_seconds"] = 180
    request["objects"] = objects
    request["chain"]["final_artefact_hash"] = hashes[1]
    request["chain"]["artefacts"] = [
        {"artefact_hash": hashes[0], "artefact_type": "audio/raw-track", "attributes": {}, "evidence": []},
        {"artefact_hash": hashes[1], "artefact_type": "audio/sample", "attributes": {},
         "evidence": [{"hash": hashes[0], "relationship_type": "excerpted_from", "attributes": {}}]},
    ]
    (folder / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    (folder / "README.md").write_text(
        "# Synthetic audio diagnostic example\n\n"
        "The 16-second source repeats an eight-second synthetic motif. The four-second sample "
        "occurs near source 2-6 seconds and 10-14 seconds. Both are valid content candidates; "
        "neither proves historical provenance. All audio observations remain non-scoring.\n")
    return folder


if __name__ == "__main__":
    print(create_example())
