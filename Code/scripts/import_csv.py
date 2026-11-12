"""Import the assessment CSV (or another compatible CSV) into SQLite."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.core.database import Database
from app.services.dataset_import import DatasetImportError, parse_dataset_csv


def main() -> int:
    parser = argparse.ArgumentParser(description="Import CAPTCHA metadata CSV into CaptchaLab SQLite.")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=str(ROOT / "data" / "USA_Ecommerce_Platforms_200_Contact_Pages.csv"),
        help="CSV file to import (defaults to the supplied assessment CSV).",
    )
    parser.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024)
    args = parser.parse_args()

    csv_path = Path(args.csv_path).expanduser().resolve()
    if not csv_path.is_file():
        parser.error(f"CSV file not found: {csv_path}")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    raw = csv_path.read_bytes()
    if len(raw) > args.max_bytes:
        parser.error(f"CSV exceeds configured size limit ({args.max_bytes} bytes)")

    try:
        rows = parse_dataset_csv(raw)
    except DatasetImportError as exc:
        print(f"Import failed [{exc.code}]: {exc}", file=sys.stderr)
        return 2

    settings = get_settings()
    db = Database(settings.database_path)
    db.initialize()
    count = db.replace_imported_sites(rows)
    print(f"Imported {count} metadata records from: {csv_path}")
    print(db.dataset_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
