"""Freeze candidate rules on development measurements, then apply them unchanged to held-out data.

    python -B benchmarks/separation_rules.py freeze dev/measurements.json --out frozen_rules.json

Exploratory and uncalibrated (Phase 2B, docs/SOURCE_SEPARATION_POC.md). A
rule flags a candidate stem whose metric crosses a threshold; it is a
measurement for research, not a label of any stem.
"""

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import separation_batch as batch  # noqa: E402
import separation_probe as probe  # noqa: E402

# Declared before any held-out song was measured. P is the primary hypothesis.
RULE_SPECS = [
    {"id": "P", "metric": "hf_excess_over_mix_db", "scope": "per_source", "primary": True},
    {"id": "S0", "metric": "hf_excess_over_mix_db", "scope": "fixed", "primary": False,
     "fixed": {"direction": batch.PREREGISTERED["direction"], "threshold": batch.PREREGISTERED["threshold"]}},
    {"id": "S1", "metric": "hf_excess_over_mix_db", "scope": "pooled", "primary": False},
    {"id": "S2", "metric": "hf_excess_mix_referenced_db", "scope": "pooled", "primary": False},
    {"id": "S3", "metric": "hf_excess_mix_referenced_db", "scope": "per_source", "primary": False},
]


def freeze(rows, source_file=None):
    usable = [row for row in rows if not row["excluded"]]
    rules = []
    for spec in RULE_SPECS:
        metric = spec["metric"]
        if spec["scope"] == "fixed":
            thresholds = {"all": spec["fixed"]}
        elif spec["scope"] == "pooled":
            thresholds = {"all": batch.choose_threshold(batch._values(usable, metric, "genuine"),
                                                        batch._values(usable, metric, "derived"))}
        else:
            thresholds = {source: batch.choose_threshold(batch._values(usable, metric, "genuine", source=source),
                                                         batch._values(usable, metric, "derived", source=source))
                          for source in probe.SOURCES}
        rules.append({**{k: v for k, v in spec.items() if k != "fixed"}, "thresholds": thresholds})
    return {"status": batch.STATUS, "frozen_at": datetime.date.today().isoformat(),
            "development_songs": sorted({row["song"] for row in usable}),
            "development_pairs": len({(row["song"], row["source"]) for row in usable}),
            "development_measurements": str(source_file) if source_file else None,
            "development_measurements_sha256": probe.sha256(source_file) if source_file else None,
            "selection": "Youden J on development data; ties go to the lower false-positive rate",
            "rules": rules}


def _rule_for(rule, source):
    return rule["thresholds"].get("all") or rule["thresholds"].get(source)


def apply_rules(frozen, rows):
    """Held-out rates of each frozen rule, overall and per source, with song-bootstrap intervals."""
    development = set(frozen["development_songs"])
    usable = [row for row in rows if not row["excluded"]]
    overlap = sorted({row["song"] for row in usable} & development)
    out = {"status": batch.STATUS, "development_songs_in_evaluation": overlap, "rules": {}}
    for rule in frozen["rules"]:
        decisions, by_source, missing = [], {source: [] for source in probe.SOURCES}, 0
        for row in usable:
            threshold = _rule_for(rule, row["source"])
            if row[rule["metric"]] is None or threshold is None:
                missing += 1
                continue
            decision = (row["song"], row["kind"], bool(batch.flags(row[rule["metric"]], threshold)))
            decisions.append(decision)
            by_source[row["source"]].append(decision)
        out["rules"][rule["id"]] = {
            "metric": rule["metric"], "scope": rule["scope"], "primary": rule["primary"],
            "thresholds": rule["thresholds"], "candidates_without_value": missing,
            "rates": batch._rates(decisions) if decisions else None,
            "rates_by_source": {source: batch._rates(items) for source, items in by_source.items() if items}}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Freeze exploratory separation rules on development data.")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze_cmd = sub.add_parser("freeze")
    freeze_cmd.add_argument("measurements", type=Path, help="measurements.json from a development batch")
    freeze_cmd.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f"separation_rules: {args.out} exists; frozen rules are never overwritten.", file=sys.stderr)
        return 2
    rows = json.loads(args.measurements.read_text())["rows"]
    frozen = freeze(rows, args.measurements)
    args.out.write_text(json.dumps(frozen, indent=2, allow_nan=False) + "\n")
    print(f"Frozen {len(frozen['rules'])} rules from {len(frozen['development_songs'])} songs: {args.out} "
          f"(sha256 {hashlib.sha256(args.out.read_bytes()).hexdigest()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
