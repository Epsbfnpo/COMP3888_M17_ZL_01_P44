"""Choose declared audio edges from the retained graph and core observations."""

import hashlib
import json

AUDIO_RELATIONSHIPS = {"excerpted_from", "mastered_from", "edited_from", "comped_from",
                       "stemmed_from", "mixed_from", "derived_from", "draft-of.audio"}
RANGE_FIELDS = ("source_start_seconds", "source_end_seconds", "target_start_seconds", "target_end_seconds")


def is_audio(node):
    return node["artefact_type"] == "audio" or node["artefact_type"].startswith("audio/")


def build_tasks(native, response):
    nodes = {node["artefact_hash"]: node for node in native["artefacts"]}
    bound = {row["evidence_hash"] for row in response.get("assessment_checks", {}).get("bound_file_integrity", [])
             if row.get("rule") == "object_hash_bound" and row.get("passed") is True}
    failed = {row.get("evidence_hash") for row in response["findings"]
              if row["code"] in {"WAV_PARSER_FAILED", "OBJECT_CHANGED_DURING_EVALUATION"}}
    sheets = [row["parser_result"] for row in response["findings"]
              if row["code"] == "PARSER_OBSERVATION" and row.get("parser") == "comp-sheet"
              and row.get("parser_status") in {"parsed", "partial"} and row.get("parser_result")]
    issues = native.get("_input_issues", {})
    tasks = []
    for target in native["artefacts"]:
        for index, edge in enumerate(target.get("evidence", [])):
            source = nodes[edge["hash"]]
            if not (is_audio(source) and is_audio(target)):
                continue
            relation_id = hashlib.sha256(json.dumps([source["artefact_hash"], target["artefact_hash"],
                edge["relationship_type"], index], separators=(",", ":")).encode()).hexdigest()
            task = {"relation_id": relation_id, "source_hash": source["artefact_hash"],
                    "target_hash": target["artefact_hash"], "relationship_type": edge["relationship_type"],
                    "attributes": {key: edge.get("attributes", {}).get(key) for key in RANGE_FIELDS},
                    "selection_id": None, "unavailable_reason": None}
            pair = (task["target_hash"], task["source_hash"])
            if task["relationship_type"] not in AUDIO_RELATIONSHIPS:
                task["unavailable_reason"] = "unsupported_relationship_semantics"
            elif any(digest not in bound or digest in failed for digest in (task["source_hash"], task["target_hash"])):
                task["unavailable_reason"] = "core_input_unavailable"
            elif (pair in issues.get("declaration_edges", {}) or
                  any(digest in issues.get("declaration_nodes", {}) for digest in pair)):
                task["unavailable_reason"] = "invalid_declaration"
            selections = []
            if task["relationship_type"] == "comped_from" and not task["unavailable_reason"]:
                for sheet in sheets:
                    for selection in sheet.get("selections", []):
                        target_hash = selection.get("target_hash") or sheet.get("metadata", {}).get("target_hash")
                        if selection.get("source_hash") == task["source_hash"] and target_hash == task["target_hash"]:
                            selections.append(selection)
                if not selections:
                    task["unavailable_reason"] = "comp_selection_unavailable"
            variants = selections or [None]
            for number, selection in enumerate(variants):
                variant = {**task, "task_id": relation_id + ":" + str(number)}
                if selection:
                    variant["selection_id"] = selection["selection_id"]
                    variant["attributes"] = {key: selection.get(key) for key in RANGE_FIELDS}
                tasks.append(variant)
    return tasks
