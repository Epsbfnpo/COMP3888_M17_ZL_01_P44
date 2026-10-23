"""Choose declared audio edges from the retained graph and core observations."""

import hashlib
import json

AUDIO_RELATIONSHIPS = {"excerpted_from", "mastered_from", "edited_from", "comped_from",
                       "stemmed_from", "mixed_from", "derived_from", "draft-of.audio"}
RANGE_FIELDS = ("source_start_seconds", "source_end_seconds", "target_start_seconds", "target_end_seconds")


def is_audio(node):
    return node["artefact_type"] == "audio" or node["artefact_type"].startswith("audio/")


AAF_RANGE = "aaf_source_range"


def _norm(value):
    return str(value or "").strip().casefold()


def _aaf_ranges(response):
    """Source seconds each AAF-used recorder take contributes, keyed by the take's hash.

    AAF ranges are tape timecode; subtracting the take's recorder start gives
    seconds into the file. Duplicate ranges (one per channel slot) are merged.
    A range more than one frame outside the recording is left to the core
    aaf_source_range_within_recorded_timecode check and not analysed.
    """
    recorders = {}
    for row in response.get("findings", []):
        recorder = row.get("recorder_metadata") or {}
        if (row.get("code") in {"WAV_OBSERVATION", "AUDIO_OBSERVATION"} and recorder.get("present")
                and recorder.get("file_name")):
            recorders.setdefault((_norm(recorder.get("tape")), _norm(recorder["file_name"])), []).append(
                (row["evidence_hash"], recorder))
    ranges = {}
    for row in response.get("findings", []):
        if not (row.get("code") == "PARSER_OBSERVATION" and row.get("parser") == "aaf"
                and row.get("parser_status") in {"parsed", "partial"} and row.get("parser_result")):
            continue
        for reference in row["parser_result"].get("references", []):
            matches = recorders.get((_norm(reference.get("tape")), _norm(reference.get("file_name"))), [])
            if len(matches) != 1:
                continue  # unsubmitted or ambiguous take: the core reports it
            digest, recorder = matches[0]
            start, duration = recorder.get("start_timecode_seconds"), recorder.get("duration_seconds")
            if start is None or not duration:
                continue
            for item in reference.get("source_ranges", []):
                frame = 1 / item["edit_rate"]
                begin, end = item["start_seconds"] - start, item["end_seconds"] - start
                if begin < -frame or end > duration + frame or end - begin <= 0:
                    continue
                key = (round(max(begin, 0.0), 6), round(min(end, duration), 6))
                ranges.setdefault(digest, {}).setdefault(key, reference.get("master_mob"))
    return {digest: sorted(items.items()) for digest, items in ranges.items()}


def build_tasks(native, response):
    nodes = {node["artefact_hash"]: node for node in native["artefacts"]}
    bound = {row["evidence_hash"] for row in response.get("assessment_checks", {}).get("bound_file_integrity", [])
             if row.get("rule") == "object_hash_bound" and row.get("passed") is True}
    failed = {row.get("evidence_hash") for row in response["findings"]
              if row["code"] in {"WAV_PARSER_FAILED", "AUDIO_PARSER_FAILED", "OBJECT_CHANGED_DURING_EVALUATION"}}
    sheets = [row["parser_result"] for row in response["findings"]
              if row["code"] == "PARSER_OBSERVATION" and row.get("parser") == "comp-sheet"
              and row.get("parser_status") in {"parsed", "partial"} and row.get("parser_result")]
    issues = native.get("_input_issues", {})
    aaf_ranges = _aaf_ranges(response)
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
            aaf = []
            if (not task["unavailable_reason"] and task["relationship_type"] != "comped_from"
                    and all(value is None for value in task["attributes"].values())):
                # The edit names which seconds of this take it used: analyse those
                # seconds against the whole target instead of the whole take.
                aaf = aaf_ranges.get(task["source_hash"], [])
            variants = selections or aaf or [None]
            for number, selection in enumerate(variants):
                variant = {**task, "task_id": relation_id + ":" + str(number)}
                if selection and aaf:
                    (begin, end), mob = selection
                    variant["selection_id"] = f"aaf:{mob}:{number}"
                    variant["attributes"] = {"source_start_seconds": begin, "source_end_seconds": end,
                                             "target_start_seconds": None, "target_end_seconds": None}
                    variant["range_source"] = AAF_RANGE
                    tasks.append(variant)
                    continue
                if selection:
                    variant["selection_id"] = selection["selection_id"]
                    variant["attributes"] = {key: selection.get(key) for key in RANGE_FIELDS}
                variant["range_source"] = _range_source(variant["attributes"])
                if variant["range_source"] == DERIVED and selection:
                    attrs = variant["attributes"]
                    # A comp selection without an end copies its declared source
                    # span: the end is the submitter's own duration, flagged as
                    # derived, never estimated from the audio.
                    attrs["target_end_seconds"] = (attrs["target_start_seconds"] +
                                                   attrs["source_end_seconds"] - attrs["source_start_seconds"])
                elif variant["range_source"] == DERIVED:
                    variant["range_source"] = "declared"
                tasks.append(variant)
    return tasks


def file_tasks(native, response):
    """One whole-file task per bound, parseable audio artefact, for per-file providers."""
    bound = {row["evidence_hash"] for row in response.get("assessment_checks", {}).get("bound_file_integrity", [])
             if row.get("rule") == "object_hash_bound" and row.get("passed") is True}
    failed = {row.get("evidence_hash") for row in response["findings"]
              if row["code"] in {"WAV_PARSER_FAILED", "AUDIO_PARSER_FAILED", "OBJECT_CHANGED_DURING_EVALUATION"}}
    tasks = []
    for node in native["artefacts"]:
        digest = node["artefact_hash"]
        if is_audio(node) and digest in bound and digest not in failed:
            tasks.append({"task_id": "file:" + digest + ":0", "relation_id": "file:" + digest,
                          "source_hash": digest, "target_hash": digest, "relationship_type": "single_file",
                          "attributes": {key: None for key in RANGE_FIELDS}, "selection_id": None,
                          "range_source": "whole_file", "unavailable_reason": None})
    return tasks


DERIVED = "derived_from_declared_source_duration"


def _range_source(attrs):
    values = [attrs.get(key) for key in RANGE_FIELDS]
    if all(value is None for value in values):
        return "whole_file"
    numbers = all(type(value) in (int, float) for value in values[:3])
    if numbers and values[3] is None and values[1] > values[0]:
        return DERIVED
    return "declared"
