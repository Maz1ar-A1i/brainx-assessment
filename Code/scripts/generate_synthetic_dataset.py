"""Generate a reproducible, three-family synthetic CAPTCHA dataset.

Families:
  * synthetic_text: transcribe a five-character visual string.
  * synthetic_arithmetic: read an expression and calculate its result.
  * synthetic_image_grid: select 1-based cells matching a colour/shape prompt.

These are controlled candidate-owned fixtures. They are not representations of
Google reCAPTCHA, hCaptcha, Cloudflare Turnstile, or arbitrary photo-selection tasks.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.synthetic import generate_arithmetic_challenge, generate_text_challenge
from app.services.image_grid import generate_image_grid_challenge

FAMILIES = ("synthetic_text", "synthetic_arithmetic", "synthetic_image_grid")


def _family_counts(total: int) -> dict[str, int]:
    if total < len(FAMILIES):
        raise ValueError(f"At least {len(FAMILIES)} samples are required to include all families")
    base, remainder = divmod(total, len(FAMILIES))
    return {family: base + (1 if index < remainder else 0) for index, family in enumerate(FAMILIES)}


def _remove_managed_images(images_dir: Path) -> None:
    """Remove only filename patterns created by this generator, not arbitrary files."""
    for pattern in ("captcha_*.png", "text_*.png", "math_*.png", "grid_*.png"):
        for path in images_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def generate_dataset(
    count: int = 300,
    seed: int = 20261112,
    output_dir: Path | None = None,
    *,
    clean: bool = True,
) -> dict:
    if output_dir is None:
        output_dir = ROOT / "data" / "synthetic_dataset"
    output_dir = Path(output_dir).expanduser().resolve()
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_csv = output_dir / "labels.csv"
    counts = _family_counts(int(count))
    if clean:
        _remove_managed_images(images_dir)

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows: list[dict[str, object]] = []
    image_id = 0
    family_seed_offsets = {
        "synthetic_text": 0,
        "synthetic_arithmetic": 100_000,
        "synthetic_image_grid": 200_000,
    }

    print("=" * 58)
    print("CaptchaLab AI — Three-Family Synthetic Dataset Generator")
    print(f"Total samples : {count}")
    print(f"Seed          : {seed}")
    print(f"Output        : {output_dir}")
    print("Family split  : " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print("=" * 58)

    for family in FAMILIES:
        for family_index in range(counts[family]):
            sample_seed = int(seed) + family_seed_offsets[family] + family_index
            image_id += 1
            if family == "synthetic_text":
                image_bytes, answer, challenge_id = generate_text_challenge(sample_seed)
                prompt = "Transcribe the five characters shown in the image."
                prefix = "text"
            elif family == "synthetic_arithmetic":
                image_bytes, answer, challenge_id, prompt = generate_arithmetic_challenge(sample_seed)
                prefix = "math"
            else:
                image_bytes, answer, challenge_id, prompt = generate_image_grid_challenge(sample_seed)
                prefix = "grid"

            filename = f"{prefix}_{family_index + 1:04d}.png"
            (images_dir / filename).write_bytes(image_bytes)
            rows.append({
                "image_id": image_id,
                "challenge_type": family,
                "filename": filename,
                "ground_truth": answer,
                "prompt": prompt,
                "length": len(answer),
                "challenge_id": challenge_id,
                "seed": sample_seed,
                "created_at": now,
            })

        print(f"  Generated {counts[family]:4d} {family} samples")

    # Interleave families in the manifest. Evaluation still stratifies explicitly,
    # but a quick manual inspection of the CSV no longer shows text-only grouping.
    random.Random(seed).shuffle(rows)
    fieldnames = [
        "image_id", "challenge_type", "filename", "ground_truth", "prompt",
        "length", "challenge_id", "seed", "created_at",
    ]
    with labels_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    distribution = Counter(str(row["challenge_type"]) for row in rows)
    readme = f"""# CaptchaLab AI Synthetic Dataset

Generated at: {now}  
Base seed: `{seed}`  
Total samples: **{count}**  

## Challenge families

| Family | Samples | What the solver must do |
|---|---:|---|
| `synthetic_text` | {distribution['synthetic_text']} | Transcribe a coloured, rotated five-character string |
| `synthetic_arithmetic` | {distribution['synthetic_arithmetic']} | Read a digit/operator/digit expression and calculate it |
| `synthetic_image_grid` | {distribution['synthetic_image_grid']} | Classify controlled geometric shapes by colour and shape, then return 1-based cell positions |

The image-grid family uses a controlled vocabulary of six colours and three geometric shapes. It is a computer-vision benchmark, not a claim of general photograph recognition. None of these families reproduces a provider's production challenge or bypasses a provider widget.

Images are stored in `images/`. The ground-truth index is `labels.csv`.
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")

    print(f"\nDataset created: {len(rows)} rows")
    print(f"Manifest: {labels_csv}")
    return {
        "total": len(rows),
        "family_counts": dict(distribution),
        "output_dir": str(output_dir),
        "labels_file": str(labels_csv),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a balanced three-family CAPTCHA dataset.")
    parser.add_argument("--samples", type=int, default=300, help="Total samples, balanced across three families (default: 300)")
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--keep-existing", action="store_true", help="Do not remove old generator-managed PNG images")
    args = parser.parse_args()
    out_dir = Path(args.output).resolve() if args.output else None
    generate_dataset(count=args.samples, seed=args.seed, output_dir=out_dir, clean=not args.keep_existing)
