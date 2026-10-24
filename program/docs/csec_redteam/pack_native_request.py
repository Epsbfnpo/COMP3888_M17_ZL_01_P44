"""Pack local files into a native request for adversarial testing.

    python3 pack_native_request.py spec.json out_dir

The spec names each artefact by a short key, its file (relative to the spec),
its artefact type, optional attributes and its incoming evidence edges. The
script hashes every file, copies it to out_dir/objects/<sha256>.<ext> and writes
out_dir/request.json. Then run:

    python3 -B run.py --native out_dir/request.json --root out_dir

See NATIVE_INPUT_GUIDE.md for the spec format and a worked example.
"""

import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path


def pack(spec_path, out):
    spec = json.loads(spec_path.read_text())
    (out / "objects").mkdir(parents=True, exist_ok=True)
    digests, objects = {}, {}
    for name, node in spec["artefacts"].items():
        source = (spec_path.parent / node["file"]).resolve()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        relative = f"objects/{digest}{source.suffix.lower()}"
        shutil.copyfile(source, out / relative)
        digests[name], objects[digest] = digest, relative
    artefacts = [{"artefact_hash": digests[name], "artefact_type": node["type"],
                  "attributes": node.get("attributes", {}),
                  "evidence": [{"hash": digests[edge["from"]], "relationship_type": edge["relationship"],
                                "attributes": edge.get("attributes", {})}
                               for edge in node.get("evidence", [])]}
                 for name, node in spec["artefacts"].items()]
    request = {"run_id": str(uuid.uuid4()), "case_id": spec["case_id"], "profile": "music-native-v0.1",
               "options": {"include_reasoning": True, "offline_only": True,
                           "timeout_seconds": spec.get("timeout_seconds", 120)},
               "workflow": spec["workflow"],
               "chain": {"schema_version": "0.0.3", "hash_method": "sha256",
                         "final_artefact_hash": digests[spec["final"]], "artefacts": artefacts},
               "objects": objects}
    (out / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    return out / "request.json"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(pack(Path(sys.argv[1]), Path(sys.argv[2])))
