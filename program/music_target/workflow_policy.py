"""Workflow-specific completeness policy loading and evaluation.

This module evaluates evidence *presence*.  It deliberately does not turn a
submitter declaration or a graph edge into proof that the declared event took
place.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


class WorkflowPolicyError(ValueError):
    pass


def _no_duplicates(items):
    result = {}
    for key, value in items:
        if key in result:
            raise WorkflowPolicyError(f"Duplicate policy key: {key}")
        result[key] = value
    return result


def load_workflow_policy(path):
    path = Path(path)
    policy = json.loads(path.read_text(), object_pairs_hook=_no_duplicates)
    workflows = policy.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        raise WorkflowPolicyError("Workflow policy must contain workflows.")
    ids = [item.get("workflow_id") for item in workflows]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise WorkflowPolicyError("Workflow IDs must be non-empty and unique.")
    rule_ids = []
    common = policy.get("common_rules", {})
    groups = [common.get("required_for_every_workflow", []),
              common.get("optional_for_every_workflow", [])]
    for workflow in workflows:
        groups.extend([workflow.get("required", []), workflow.get("conditional", [])])
    for group in groups:
        for rule in group:
            if "rule_id" in rule:
                rule_ids.append(rule["rule_id"])
            weight = rule.get("weight", 0)
            if "weight" in rule and (type(weight) not in (int, float) or weight <= 0):
                raise WorkflowPolicyError("Rule weights must be positive numbers.")
    if len(rule_ids) != len(set(rule_ids)):
        raise WorkflowPolicyError("Rule IDs must be unique.")
    return policy


def _resolve_name(node, context):
    if isinstance(node, ast.Name):
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id not in context:
            raise WorkflowPolicyError(f"Unknown trigger name: {node.id}")
        return context[node.id]
    if isinstance(node, ast.Attribute):
        base = _resolve_name(node.value, context)
        return base.get(node.attr, False) if isinstance(base, dict) else False
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
        return node.value
    raise WorkflowPolicyError("Unsupported value in workflow trigger.")


def evaluate_trigger(expression, context):
    """Evaluate the policy's intentionally tiny expression language."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise WorkflowPolicyError("Invalid workflow trigger expression.") from exc

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [bool(visit(item)) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not bool(visit(node.operand))
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            left, right = _resolve_name(node.left, context), _resolve_name(node.comparators[0], context)
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if isinstance(node, (ast.Name, ast.Attribute, ast.Constant)):
            return bool(_resolve_name(node, context))
        raise WorkflowPolicyError("Unsupported operation in workflow trigger.")

    return bool(visit(tree))


def _node_role(node, final_hash):
    if node["artefact_hash"] == final_hash:
        return "final"
    attributes = node.get("attributes") or {}
    production = attributes.get("production") or {}
    for key in ("role", "declared_role", "source_role", "mix_stage", "stem_role"):
        if production.get(key):
            return production[key]
    return attributes.get("role")


class WorkflowCompletenessPass:
    """Evaluate every applicable workflow expectation and expose each result."""

    def __init__(self, policy):
        self.policy = policy
        self.workflows = {item["workflow_id"]: item for item in policy["workflows"]}
        self.allowed_modifiers = set(policy["workflow_selection"]["modifiers"])
        self.parser_observations = {}

    PARSER_REQUIRED_TYPES = {
        "project/midi", "metadata/ddex-rin", "metadata/ddex-ern",
        "provenance/c2pa", "text/comp-sheet", "text/cue-sheet",
    }

    def _parser_usable(self, node):
        if node["artefact_type"] not in self.PARSER_REQUIRED_TYPES:
            return True
        parsed = self.parser_observations.get(node["artefact_hash"])
        return parsed is None or parsed.get("status") in ("parsed", "partial")

    def _result(self, rule, level, status, reason, hashes=None, edges=None):
        return {
            "rule_id": rule.get("rule_id", "inline-option"),
            "level": level,
            "kind": rule["kind"],
            "status": status,
            "weight": rule.get("weight"),
            "reason": reason,
            "matched_artefact_hashes": hashes or [],
            "matched_relationships": edges or [],
        }

    def _evaluate_rule(self, rule, level, nodes, final_hash, bound_hashes, workflow_input):
        kind = rule["kind"]
        if kind == "artifact_presence":
            wanted = set(rule.get("artifact_types", []))
            candidates, matches = [], []
            for node in nodes:
                if node["artefact_hash"] not in bound_hashes or node["artefact_type"] not in wanted:
                    continue
                if rule.get("role") and _node_role(node, final_hash) != rule["role"]:
                    continue
                candidates.append(node["artefact_hash"])
                if self._parser_usable(node):
                    matches.append(node["artefact_hash"])
            minimum = rule.get("min_count", 1)
            status = ("satisfied" if len(matches) >= minimum else
                      "not_assessed" if len(candidates) >= minimum else "missing")
            parser_note = (" Matching files were present but their required parser did not produce a usable result."
                           if status == "not_assessed" else "")
            return self._result(rule, level, status,
                                f"Found {len(matches)} usable bound matching artefact(s); minimum is {minimum}."
                                + parser_note, matches)

        if kind == "relationship_presence":
            by_hash = {node["artefact_hash"]: node for node in nodes}
            matches = []
            for target in nodes:
                for edge in target.get("evidence", []):
                    source = by_hash[edge["hash"]]
                    if source["artefact_hash"] not in bound_hashes or target["artefact_hash"] not in bound_hashes:
                        continue
                    if edge["relationship_type"] not in set(rule.get("relationship_types", [])):
                        continue
                    if rule.get("source_types") and source["artefact_type"] not in rule["source_types"]:
                        continue
                    if rule.get("target_types") and target["artefact_type"] not in rule["target_types"]:
                        continue
                    if rule.get("source_role") and _node_role(source, final_hash) != rule["source_role"]:
                        continue
                    matches.append({"source_hash": source["artefact_hash"],
                                    "target_hash": target["artefact_hash"],
                                    "relationship_type": edge["relationship_type"]})
            minimum = rule.get("min_count", 1)
            status = "satisfied" if len(matches) >= minimum else "missing"
            return self._result(rule, level, status,
                                f"Found {len(matches)} matching bound relationship(s); minimum is {minimum}.",
                                edges=matches)

        if kind == "ancestry_path":
            by_hash = {node["artefact_hash"]: node for node in nodes}
            allowed = set(rule.get("allowed_relationship_types", []))
            sources, pending, seen = [], [final_hash], set()
            while pending:
                digest = pending.pop()
                if digest in seen:
                    continue
                seen.add(digest)
                node = by_hash[digest]
                if digest != final_hash and digest in bound_hashes and node["artefact_type"] in rule["source_types"]:
                    if not rule.get("source_role") or _node_role(node, final_hash) == rule["source_role"]:
                        sources.append(digest)
                for edge in node.get("evidence", []):
                    if edge["relationship_type"] in allowed:
                        pending.append(edge["hash"])
            minimum = rule.get("min_count", 1)
            status = "satisfied" if len(set(sources)) >= minimum else "missing"
            return self._result(rule, level, status,
                                f"Found {len(set(sources))} qualifying bound ancestor(s); minimum is {minimum}.",
                                sorted(set(sources)))

        if kind == "declaration_presence":
            declaration = rule["declaration"]
            if declaration == "submitter_confirmation":
                value = (workflow_input.get("declarations") or {}).get("submitter_confirmation")
                present = value is True
            elif declaration == "artefacts[].creation_method":
                audio = [node for node in nodes if node["artefact_type"].startswith("audio/")]
                present = bool(audio) and all((node.get("attributes") or {}).get("creation_method")
                                              not in (None, "", [], {}) for node in audio)
            elif declaration == "contributors[]":
                value = (workflow_input.get("declarations") or {}).get("contributors")
                present = isinstance(value, list) and bool(value)
            elif declaration == "workflow.description":
                present = bool(workflow_input.get("description"))
            else:
                return self._result(rule, level, "not_assessed",
                                    f"Declaration path {declaration!r} is not supported by this evaluator.")
            return self._result(rule, level, "satisfied" if present else "missing",
                                f"Declaration {declaration!r} is {'present' if present else 'missing or ambiguous'}.")

        if kind == "alternative_group":
            children = [self._evaluate_rule(option, level, nodes, final_hash, bound_hashes, workflow_input)
                        for option in rule.get("options", [])]
            satisfied = sum(item["status"] == "satisfied" for item in children)
            assessed = [item for item in children if item["status"] != "not_assessed"]
            minimum = rule.get("min_satisfied", 1)
            status = "satisfied" if satisfied >= minimum else ("missing" if assessed else "not_assessed")
            hashes = [digest for item in children for digest in item["matched_artefact_hashes"]]
            edges = [edge for item in children for edge in item["matched_relationships"]]
            result = self._result(rule, level, status,
                                  f"{satisfied}/{len(children)} alternatives satisfied; minimum is {minimum}.",
                                  hashes, edges)
            result["option_results"] = children
            return result

        return self._result(rule, level, "not_assessed", f"Unsupported rule kind: {kind}")

    def evaluate(self, native, workflow_input, bound_hashes, parser_observations=None):
        self.parser_observations = parser_observations or {}
        workflow_id = workflow_input.get("workflow_id")
        if workflow_id not in self.workflows:
            raise WorkflowPolicyError(f"Unknown workflow_id: {workflow_id}")
        workflow = self.workflows[workflow_id]
        submitted_modifiers = workflow_input.get("modifiers", [])
        unknown = set(submitted_modifiers) - self.allowed_modifiers
        if unknown:
            raise WorkflowPolicyError("Unknown workflow modifier(s): " + ", ".join(sorted(unknown)))
        effective_modifiers = sorted(set(workflow.get("default_modifiers", [])) | set(submitted_modifiers))
        final_hash = native["final_artefact_hash"]
        nodes = native["artefacts"]
        final = next(node for node in nodes if node["artefact_hash"] == final_hash)
        modifier_context = {name: name in effective_modifiers for name in self.allowed_modifiers}
        context = {"modifier": modifier_context, "final": {"type": final["artefact_type"]},
                   "workflow": workflow_input, "declaration": workflow_input.get("declarations", {})}

        results = []
        common = self.policy["common_rules"]
        for rule in common.get("required_for_every_workflow", []):
            results.append(self._evaluate_rule(rule, "required", nodes, final_hash, bound_hashes, workflow_input))
        for rule in workflow.get("required", []):
            results.append(self._evaluate_rule(rule, "required", nodes, final_hash, bound_hashes, workflow_input))
        for rule in workflow.get("conditional", []):
            if evaluate_trigger(rule["trigger"], context):
                results.append(self._evaluate_rule(rule, "conditional", nodes, final_hash, bound_hashes, workflow_input))
            else:
                results.append(self._result(rule, "conditional", "not_applicable",
                                            f"Trigger evaluated false: {rule['trigger']}"))
        optional_rules = list(common.get("optional_for_every_workflow", []))
        optional_rules.extend({"rule_id": f"{workflow_id}.optional.{index}", "kind": "artifact_presence",
                               "artifact_types": [artefact_type], "min_count": 1}
                              for index, artefact_type in enumerate(workflow.get("optional", []), 1))
        for rule in optional_rules:
            results.append(self._evaluate_rule(rule, "optional", nodes, final_hash, bound_hashes, workflow_input))

        applicable = [item for item in results if item["level"] != "optional" and item["status"] != "not_applicable"]
        assessed = [item for item in applicable if item["status"] != "not_assessed"]
        denominator = sum(item["weight"] for item in assessed)
        numerator = sum(item["weight"] for item in assessed if item["status"] == "satisfied")
        value = numerator / denominator if denominator else None
        confidence = len(assessed) / len(applicable) if applicable else None
        availability = ("unavailable" if value is None else
                        "available" if len(assessed) == len(applicable) else "partial")
        allowed_final = final["artefact_type"] in workflow.get("allowed_final_types", [])
        if not allowed_final:
            value = 0.0 if value is not None else None
        assessment = {
            "policy_id": self.policy["policy_id"],
            "policy_version": self.policy["policy_version"],
            "workflow_id": workflow_id,
            "workflow_title": workflow["title"],
            "modifiers": effective_modifiers,
            "allowed_final_type": allowed_final,
            "summary": {"applicable_rules": len(applicable), "assessed_rules": len(assessed),
                        "satisfied_rules": sum(item["status"] == "satisfied" for item in applicable),
                        "missing_rules": sum(item["status"] == "missing" for item in applicable)},
            "rule_results": results,
        }
        axis = {"availability": availability, "value": value, "confidence": confidence,
                "reasoning": (f"Workflow {workflow_id}: {numerator:g}/{denominator:g} assessed "
                              "required/conditional weight satisfied. Optional evidence is excluded.")}
        findings = []
        if not allowed_final:
            findings.append({"code": "WORKFLOW_FINAL_TYPE_MISMATCH", "severity": "medium",
                             "message": "The final artefact type is not allowed by the selected workflow.",
                             "evidence_hash": final_hash})
        for item in applicable:
            if item["status"] in ("missing", "not_assessed"):
                findings.append({"code": "WORKFLOW_EXPECTATION_" + item["status"].upper(),
                                 "severity": "medium" if item["status"] == "missing" else "low",
                                 "message": f"{item['rule_id']}: {item['reason']}"})
        return axis, assessment, findings
