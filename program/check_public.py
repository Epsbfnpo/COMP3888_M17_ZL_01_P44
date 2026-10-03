"""Run public bundles locally and save execution/axis results, without oracle labels."""

import argparse
import collections
import json
from pathlib import Path

import run
from music_target.contract import public_bundle_request, read_json, source_revision, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_root", type=Path)
    args = parser.parse_args()
    root = args.public_root.resolve(strict=True)
    output = Path(__file__).resolve().parent / "reports/public-results"
    output.mkdir(parents=True, exist_ok=True)
    manifest = read_json(root / "manifest.json")
    results = []
    for case in manifest["cases"]:
        path = (root / case["bundle"]).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Bundle path escapes the public corpus root")
        bundle = read_json(path)
        request = public_bundle_request(bundle)
        response = run.run_isolated(request, path.parent, "csec")
        validate(response, "scoring-response.schema.json")
        # Output names use the observed safe directory name, not arbitrary case_id text.
        destination = output / f"{path.parent.name}.response.json"
        destination.write_text(json.dumps(response, indent=2) + "\n")
        if case["media_type"] == "audio/wav" and response["execution_status"] == "succeeded":
            (output / f"{path.parent.name}.request.json").write_text(json.dumps(request, indent=2) + "\n")
        results.append({"case_id": case["case_id"], "media_type": case["media_type"],
                        "execution_status": response["execution_status"],
                        "axes": response["axes"], "response": str(destination.relative_to(output.parent)),
                        "error": response["error"]})
    summary = {"source_revision": source_revision(), "case_count": len(results),
               "execution_counts": dict(collections.Counter(r["execution_status"] for r in results)),
               "interpretation": "Execution compatibility only. No private ground truth, decision policy or false-accept/false-reject rates.",
               "results": results}
    (output.parent / "public-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["execution_counts"]))


if __name__ == "__main__":
    main()
