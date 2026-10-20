"""Offline calibration-manifest validation; no generation or account access."""

import argparse
import json
import math
from pathlib import Path
import re

PARTITIONS = {"development", "validation", "test", "unassigned"}
LABEL_METHODS = {"manual_review", "deterministic_transform", "independent_measurement", "dataset_annotation"}
REFUND_STATES = {"unknown", "not_applicable", "pending", "refunded", "not_refunded"}


def validate_manifest(document):
    """Reject known leakage and prompt-as-label mistakes; report missing labels.

    The manifest records annotation provenance; it cannot establish that the
    annotation is correct or the annotator/tool is actually independent.
    """
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    require(isinstance(document, dict) and document.get("schema_version") == "1.0",
            "Expected manifest schema_version 1.0")
    cases = document.get("cases")
    require(isinstance(cases, list), "cases must be a list")
    by_id, partitions, hashes = {}, {}, {}
    for case in cases:
        require(isinstance(case, dict), "Each case must be an object")
        case_id = case.get("case_id")
        require(isinstance(case_id, str) and case_id and case_id not in by_id, "case_id must be nonempty and unique")
        by_id[case_id] = case
        split = case.get("partition")
        require(isinstance(split, str) and split in PARTITIONS, f"{case_id}: invalid partition")
        families = case.get("family_ids")
        require(isinstance(families, list) and families and
                all(isinstance(item, str) and item for item in families), f"{case_id}: family_ids are required")
        for family in families:
            if split != "unassigned":
                require(family not in partitions or partitions[family] == split,
                        f"{case_id}: source family {family} leaks across partitions")
                partitions[family] = split
        generation = case.get("generation")
        if generation is not None:
            require(isinstance(generation, dict), f"{case_id}: generation must be an object")
            require(generation.get("status") in ("succeeded", "failed"), f"{case_id}: invalid generation status")
            require(isinstance(generation.get("submitted_prompt_text"), str) and
                    bool(generation["submitted_prompt_text"].strip()), f"{case_id}: actual submitted_prompt_text is required")
            require(isinstance(generation.get("refund_status"), str) and generation["refund_status"] in REFUND_STATES,
                    f"{case_id}: refund_status is required; use unknown when not verified")
            credits = generation.get("charged_credits")
            require(credits is None or (type(credits) in (int, float) and math.isfinite(credits) and credits >= 0),
                    f"{case_id}: charged_credits must be finite and nonnegative or null")
        failed = generation is not None and generation["status"] == "failed"
        digest = case.get("audio_sha256")
        require((failed and digest is None) or (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)),
                f"{case_id}: successful/local audio requires audio_sha256")
        if digest and split != "unassigned":
            require(digest not in hashes or hashes[digest] == split, f"{case_id}: identical audio leaks across partitions")
            hashes[digest] = split
        require(isinstance(case.get("requested_properties", {}), dict), f"{case_id}: requested_properties must be an object")
        labels = case.get("verified_labels", [])
        require(isinstance(labels, list), f"{case_id}: verified_labels must be a list")
        for label in labels:
            require(isinstance(label, dict) and isinstance(label.get("property"), str) and label["property"] and
                    "value" in label and isinstance(label.get("method"), str) and label["method"] in LABEL_METHODS and
                    isinstance(label.get("annotator_or_tool"), str) and label["annotator_or_tool"].strip(),
                    f"{case_id}: labels require property, value, independent method and annotator_or_tool; prompts are not labels")
    for case_id, case in by_id.items():
        parents = case.get("parent_case_ids", [])
        require(isinstance(parents, list) and all(isinstance(parent, str) and parent in by_id for parent in parents),
                f"{case_id}: unknown parent_case_ids")
        for parent in parents:
            require(parent != case_id and set(by_id[parent]["family_ids"]) <= set(case["family_ids"]),
                    f"{case_id}: derivatives must inherit all parent families")
    # A cycle would make the claimed transform lineage unusable as ground truth.
    visiting, visited = set(), set()
    def visit(case_id):
        require(case_id not in visiting, f"{case_id}: cyclic parent lineage")
        if case_id in visited:
            return
        visiting.add(case_id)
        for parent in by_id[case_id].get("parent_case_ids", []):
            visit(parent)
        visiting.remove(case_id)
        visited.add(case_id)
    for case_id in by_id:
        visit(case_id)
    successful = [case for case in cases if not (case.get("generation") or {}).get("status") == "failed"]
    return {"case_count": len(cases), "source_family_count": len({family for case in cases for family in case["family_ids"]}),
            "failed_generation_count": len(cases) - len(successful),
            "successful_cases_without_verified_labels": [case["case_id"] for case in successful if not case.get("verified_labels")],
            "refund_status_unknown_cases": [case["case_id"] for case in cases
                                            if (case.get("generation") or {}).get("refund_status") == "unknown"],
            "ground_truth_correctness_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        def invalid(value):
            raise ValueError(f"Non-finite number: {value}")
        document = json.loads(args.manifest.read_text(), parse_constant=invalid)
        # Also catches floating point overflow such as 1e309.
        json.dumps(document, allow_nan=False)
        print(json.dumps(validate_manifest(document), indent=2, allow_nan=False))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Invalid calibration manifest: {exc}\n")


if __name__ == "__main__":
    main()
