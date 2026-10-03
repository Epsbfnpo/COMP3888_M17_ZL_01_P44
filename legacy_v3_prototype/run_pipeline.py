#!/usr/bin/env python3
"""
run_pipeline.py

One-shot driver for the music evidence bundle pipeline: builds the bundle
from a manifest, runs the four-axis assessment against it, then prints a
human-readable summary -- axis scores, findings, and any failed or
unassessed checks -- instead of leaving you to read raw JSON.

Usage
-----
    python3 run_pipeline.py bundle_manifest.json --objects-dir objects
    python3 run_pipeline.py bundle_manifest.json --objects-dir objects -o output
    python3 run_pipeline.py --skip-build --objects-dir objects -o output  # re-assess an existing bundle

This is a thin wrapper around the two existing steps and does not replace
them -- run bundle_builder.py / music_profiles.py directly if you need the
raw JSON files or any of their other flags (e.g. --self-test).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
AXIS_ORDER = ["completeness", "integrity", "attestation_strength", "ai_disclosure"]


def _run_step(description: str, cmd: List[str]) -> None:
    print(f"-> {description}")
    print(f"   $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        print(textwrap.indent(result.stdout.strip(), "   "))
    if result.returncode != 0:
        print(f"\n!! {description} failed (exit code {result.returncode})", file=sys.stderr)
        if result.stderr.strip():
            print(textwrap.indent(result.stderr.strip(), "   "), file=sys.stderr)
        sys.exit(result.returncode)
    if result.stderr.strip():
        # Some steps print warnings to stderr even on success.
        print(textwrap.indent(result.stderr.strip(), "   "))


def _short_hash(value: Any) -> str:
    if not value:
        return "?"
    value = str(value)
    return value[:12] + "..." if len(value) > 12 else value


def _bar(value: Any, width: int = 12) -> str:
    if value is None:
        return " " * width
    filled = max(0, min(width, int(round(value * width))))
    return "#" * filled + "-" * (width - filled)


def _print_axes(axes: Dict[str, Any]) -> None:
    print("\n=== AXIS SCORES ===")
    name_width = max((len(n) for n in AXIS_ORDER if n in axes), default=10)
    for name in AXIS_ORDER:
        axis = axes.get(name)
        if axis is None:
            continue
        avail = axis.get("availability", "unknown")
        value = axis.get("value")
        confidence = axis.get("confidence")

        value_str = f"{value:.3f}" if value is not None else "n/a  "
        conf_str = f"conf {confidence:.2f}" if confidence is not None else ""

        print(f"\n  {name:<{name_width}}  [{_bar(value)}]  {value_str}  {avail:<11} {conf_str}".rstrip())
        reasoning = axis.get("reasoning")
        if reasoning:
            print(textwrap.fill(
                reasoning, width=78, initial_indent="      ", subsequent_indent="      ",
            ))


def _print_findings(findings: List[Dict[str, Any]]) -> None:
    print("\n=== FINDINGS ===")
    if not findings:
        print("  none")
        return
    ordered = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "info"), 99))
    for f in ordered:
        sev = f.get("severity", "info").upper()
        code = f.get("code", "?")
        msg = f.get("message", "")
        print(f"  [{sev:<6}] {code}: {msg}")
        print(f"           evidence: {_short_hash(f.get('evidence_hash'))}")


def _iter_check_results(checks: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield (axis_name, result_dict) for every per-artefact/relationship result across all axes."""
    for axis_name, axis_checks in checks.items():
        if not isinstance(axis_checks, dict):
            continue
        for result in axis_checks.get("results", []):
            yield axis_name, result


def _stem_similarity_line(sim: Dict[str, Any]) -> str:
    if not sim.get("assessed", True):
        return f"stem_similarity: not assessed -- {sim.get('reason', 'no reason given')}"
    return (
        "stem_similarity: active_correlation="
        f"{sim.get('active_correlation', 0.0):.3f} (threshold {sim.get('threshold')}), "
        f"active_fraction={sim.get('active_fraction', 0.0):.3f}, "
        f"offset={sim.get('offset_seconds', 0.0):.3f}s"
    )


def _print_failed_and_unassessed(checks: Dict[str, Any]) -> None:
    failures: List[Tuple[str, Dict[str, Any]]] = []
    unassessed_sims: List[Tuple[str, Dict[str, Any]]] = []

    for axis_name, result in _iter_check_results(checks):
        is_failure = bool(result.get("contradiction")) or result.get("assessed") is False
        if not is_failure:
            for rule in result.get("checks", []) or []:
                if not rule.get("passed", True):
                    is_failure = True
                    break
        if is_failure:
            failures.append((axis_name, result))

        sim = result.get("stem_similarity")
        if sim and not sim.get("assessed", True):
            unassessed_sims.append((axis_name, result))

    print(f"\n=== FAILED CHECKS ({len(failures)}) ===")
    if not failures:
        print("  none")
    for axis_name, result in failures:
        kind = result.get("kind", "?")
        art = result.get("artefact_hash") or result.get("target_hash")
        print(f"  [{axis_name}] {kind}  ({_short_hash(art)})")
        print(f"      {result.get('reason', '')}")
        for rule in result.get("checks", []) or []:
            if not rule.get("passed", True):
                print(f"        x failed rule: {rule.get('rule')}")
        sim = result.get("stem_similarity")
        if sim and sim.get("assessed", True) and not sim.get("passed", True):
            print(f"        {_stem_similarity_line(sim)}")

    if unassessed_sims:
        print(f"\n=== UNASSESSED SIGNALS ({len(unassessed_sims)}) ===")
        print("  (not a failure -- a signal that could not be computed at all)")
        for axis_name, result in unassessed_sims:
            art = result.get("target_hash") or result.get("artefact_hash")
            print(f"  [{axis_name}] {result.get('kind', '?')}  ({_short_hash(art)})")
            print(f"        {_stem_similarity_line(result['stem_similarity'])}")


def summarize(assessment_path: Path) -> None:
    data = json.loads(assessment_path.read_text())
    print(f"\nProfile: {data.get('profile_id')} v{data.get('profile_version')}")
    _print_axes(data.get("axes", {}))
    _print_findings(data.get("findings", []))
    _print_failed_and_unassessed(data.get("checks", {}))
    print(
        "\nNote: this profile does not combine axes into a single overall "
        "score by design -- see README.md's 'Scores and output semantics'."
    )
    print(f"Full detail: {assessment_path}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a music evidence bundle, assess it, and print a summary."
    )
    parser.add_argument("manifest", nargs="?", help="Path to bundle_manifest.json (omit with --skip-build)")
    parser.add_argument("--objects-dir", required=True, help="Directory containing the source files")
    parser.add_argument(
        "-o", "--output-dir", default="output",
        help="Directory for evidence_bundle.json / assessment.json (default: output)",
    )
    parser.add_argument(
        "--python", default=sys.executable,
        help="Python interpreter to invoke the pipeline scripts with (default: current interpreter)",
    )
    parser.add_argument(
        "--skip-build", action="store_true",
        help="Skip bundle_builder.py and assess an existing evidence_bundle.json in --output-dir",
    )
    args = parser.parse_args()

    if not args.skip_build and not args.manifest:
        parser.error("manifest is required unless --skip-build is given")

    script_dir = Path(__file__).resolve().parent
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "evidence_bundle.json"
    assessment_path = output_dir / "assessment.json"

    if not args.skip_build:
        _run_step(
            "Building evidence bundle",
            [args.python, str(script_dir / "bundle_builder.py"), args.manifest, "-o", str(bundle_path)],
        )
    elif not bundle_path.exists():
        print(f"!! --skip-build given but {bundle_path} does not exist", file=sys.stderr)
        return 1

    _run_step(
        "Running four-axis assessment",
        [
            args.python, str(script_dir / "music_profiles.py"), str(bundle_path),
            "--objects-dir", args.objects_dir, "-o", str(assessment_path),
        ],
    )

    summarize(assessment_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
