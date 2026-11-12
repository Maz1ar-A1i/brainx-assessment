"""Convenience CLI for the stratified three-family synthetic benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_dataset import run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark text, arithmetic and image-grid solvers.")
    parser.add_argument("--samples", type=int, default=300, help="Total samples to evaluate, stratified across all families")
    parser.add_argument("--labels", default="", help="Optional labels.csv path")
    parser.add_argument("--seed", type=int, default=20261112, help="Seed used when regenerating the default corpus")
    parser.add_argument("--regenerate", action="store_true", help="Regenerate the default 3-family corpus before evaluating it")
    args = parser.parse_args()
    if args.samples < 3:
        parser.error("--samples must be at least 3 so every family is evaluated")
    if args.regenerate and args.labels:
        parser.error("--regenerate applies only to the bundled default corpus; do not combine it with --labels")
    if args.regenerate:
        from scripts.generate_synthetic_dataset import generate_dataset
        generate_dataset(count=args.samples, seed=args.seed)
    report = run_evaluation(Path(args.labels) if args.labels else None, max_samples=args.samples)
    print(json.dumps({
        "total_samples": report["total_samples"],
        "exact_match_accuracy_pct": report["exact_match_accuracy_pct"],
        "family_metrics": report["family_metrics"],
        "average_latency_ms": report["average_latency_ms"],
        "p95_latency_ms": report["p95_latency_ms"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
