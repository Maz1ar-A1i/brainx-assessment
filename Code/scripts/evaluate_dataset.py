"""Stratified exact-match benchmark for all controlled synthetic families."""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ocr import solve_arithmetic_image, solve_text_image, warmup_solver
from app.services.image_grid import solve_image_grid_challenge
from scripts.generate_synthetic_dataset import FAMILIES, generate_dataset

SOLVERS: dict[str, Callable] = {
    "synthetic_text": solve_text_image,
    "synthetic_arithmetic": solve_arithmetic_image,
}


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left_char != right_char)))
        previous = current
    return previous[-1]


def _percentile95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(0.95 * len(ordered) + 0.999999) - 1))
    return round(ordered[index], 2)


def _balanced_select(rows: list[dict[str, str]], maximum: int | None) -> list[dict[str, str]]:
    if maximum is None or maximum >= len(rows):
        return rows
    if maximum < len(FAMILIES):
        raise ValueError(f"max_samples must be at least {len(FAMILIES)} to test every challenge family")
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("challenge_type", "")].append(row)
    selected: list[dict[str, str]] = []
    indices = {family: 0 for family in grouped}
    while len(selected) < maximum:
        made_progress = False
        for family in FAMILIES:
            items = grouped.get(family, [])
            index = indices.get(family, 0)
            if index < len(items) and len(selected) < maximum:
                selected.append(items[index])
                indices[family] = index + 1
                made_progress = True
        if not made_progress:
            break
    return selected


def _solve(family: str, image_bytes: bytes, prompt: str) -> dict:
    if family == "synthetic_image_grid":
        return solve_image_grid_challenge(image_bytes, prompt)
    if family in SOLVERS:
        return SOLVERS[family](image_bytes)
    raise ValueError(f"unsupported_challenge_family:{family}")


def run_evaluation(labels_path: Path | None = None, max_samples: int | None = None) -> dict:
    default_csv = ROOT / "data" / "synthetic_dataset" / "labels.csv"
    using_default = labels_path is None or Path(labels_path).resolve() == default_csv.resolve()
    if labels_path is None:
        labels_path = default_csv

    if not Path(labels_path).is_file():
        if not using_default:
            raise FileNotFoundError(f"Labels CSV not found: {labels_path}")
        print("Dataset manifest missing. Generating the default 300-sample dataset…")
        generate_dataset(count=300)

    labels_path = Path(labels_path).expanduser().resolve()
    with labels_path.open("r", encoding="utf-8-sig", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    if not all_rows:
        raise ValueError(f"No labelled samples found in {labels_path}")

    # Older manifests did not have challenge_type and represented only text. Replace
    # only the project's own default generated dataset with the current 3-family set.
    actual_families = {row.get("challenge_type", "").strip() for row in all_rows}
    required_families = set(FAMILIES)
    if using_default and not required_families.issubset(actual_families):
        print("Legacy text-only manifest detected. Regenerating three-family corpus…")
        generate_dataset(count=300)
        with default_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            all_rows = list(csv.DictReader(handle))
    elif not required_families.issubset(actual_families):
        raise ValueError("The supplied manifest must include synthetic_text, synthetic_arithmetic and synthetic_image_grid rows")

    images_dir = labels_path.parent / "images"
    expected_names = []
    for row in all_rows:
        filename = (row.get("filename") or "").strip()
        if not filename or Path(filename).name != filename:
            raise ValueError("Invalid image filename in labels manifest")
        expected_names.append(filename)
    if using_default and any(not (images_dir / name).is_file() for name in expected_names):
        print("Some generated images are missing. Recreating the reproducible three-family corpus…")
        generate_dataset(count=len(all_rows))
        with default_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            all_rows = list(csv.DictReader(handle))

    rows = _balanced_select(all_rows, max_samples)
    if not rows:
        raise ValueError("No samples selected for evaluation")

    for index, row in enumerate(rows, start=2):
        family = row.get("challenge_type", "").strip()
        if family not in FAMILIES:
            raise ValueError(f"Unsupported challenge_type at manifest row {index}: {family}")
        if not row.get("ground_truth", "").strip():
            raise ValueError(f"Missing ground_truth at manifest row {index}")
        path = images_dir / row["filename"]
        if not path.is_file():
            raise FileNotFoundError(f"Missing image at manifest row {index}: {path}")

    warmup_started = time.perf_counter()
    warmup_solver()
    warmup_ms = (time.perf_counter() - warmup_started) * 1000

    family_stats: dict[str, dict] = {}
    mismatches: list[dict] = []
    all_latencies: list[float] = []
    overall_exact = 0
    text_char_errors = 0
    text_total_chars = 0
    per_family_rows: dict[str, list[float]] = defaultdict(list)
    per_family_exact: Counter[str] = Counter()
    per_family_total: Counter[str] = Counter()

    for sample_number, row in enumerate(rows, start=1):
        family = row["challenge_type"]
        expected = row["ground_truth"].strip()
        image_path = images_dir / row["filename"]
        image_bytes = image_path.read_bytes()
        started = time.perf_counter()
        result = _solve(family, image_bytes, row.get("prompt", ""))
        latency = (time.perf_counter() - started) * 1000
        predicted = str(result.get("text", ""))
        correct = predicted == expected
        all_latencies.append(latency)
        per_family_rows[family].append(latency)
        per_family_total[family] += 1
        if correct:
            overall_exact += 1
            per_family_exact[family] += 1
        else:
            mismatches.append({
                "family": family,
                "filename": row["filename"],
                "expected": expected,
                "predicted": predicted,
                "confidence": result.get("confidence"),
                "prompt": row.get("prompt", ""),
            })
        if family == "synthetic_text":
            text_total_chars += len(expected)
            text_char_errors += _edit_distance(predicted, expected)
        if sample_number % 50 == 0 or sample_number == len(rows):
            print(f"  Evaluated {sample_number}/{len(rows)}")

    for family in FAMILIES:
        family_total = per_family_total[family]
        family_correct = per_family_exact[family]
        family_latencies = per_family_rows[family]
        family_stats[family] = {
            "total": family_total,
            "exact": family_correct,
            "accuracy_pct": round(100 * family_correct / family_total, 2) if family_total else None,
            "average_latency_ms": round(statistics.mean(family_latencies), 2) if family_latencies else None,
            "p95_latency_ms": _percentile95(family_latencies),
        }

    total = len(rows)
    report = {
        "total_samples": total,
        "corpus_samples": len(all_rows),
        "exact_matches": overall_exact,
        "exact_match_accuracy_pct": round(100 * overall_exact / total, 2),
        "family_distribution_in_corpus": dict(Counter(row["challenge_type"] for row in all_rows)),
        "family_metrics": family_stats,
        "text_character_accuracy_pct": round(100 * (1 - text_char_errors / text_total_chars), 2) if text_total_chars else None,
        "text_character_metric_scope": "synthetic_text only; not reported for arithmetic or image-grid selection",
        "warmup_ms": round(warmup_ms, 2),
        "average_latency_ms": round(statistics.mean(all_latencies), 2),
        "p50_latency_ms": round(statistics.median(all_latencies), 2),
        "p95_latency_ms": _percentile95(all_latencies),
        "cost_usd": 0.0,
        "solver_stack": "OpenCV + NumPy + Pillow; CPU only",
        "scope": "Only the bundled controlled candidate-owned synthetic generators. Does not measure provider CAPTCHA solving.",
        "sample_mismatches": mismatches[:50],
    }

    print("\n================== RESULTS SUMMARY ==================")
    print(f"Exact match: {overall_exact}/{total} ({report['exact_match_accuracy_pct']:.2f}%)")
    for family, metrics in family_stats.items():
        print(f"{family}: {metrics['exact']}/{metrics['total']} ({metrics['accuracy_pct']}%), avg={metrics['average_latency_ms']} ms, p95={metrics['p95_latency_ms']} ms")
    print(f"Overall avg / p50 / p95: {report['average_latency_ms']} / {report['p50_latency_ms']} / {report['p95_latency_ms']} ms")
    print("External solver cost: $0.00 (local CPU CV)")
    print("Scope: controlled synthetic families only; not production provider challenges")
    print("=====================================================\n")

    artifact_dir = ROOT / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    (artifact_dir / "dataset_evaluation_latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate all three controlled synthetic CAPTCHA families.")
    parser.add_argument("labels", nargs="?", default="", help="Optional labels.csv path")
    parser.add_argument("--labels", dest="labels_flag", default="", help="Optional labels.csv path")
    parser.add_argument("--samples", type=int, default=None, help="Maximum total samples; stratified across families")
    args = parser.parse_args()
    selected = args.labels_flag or args.labels
    run_evaluation(Path(selected) if selected else None, max_samples=args.samples)
