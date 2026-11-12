"""CSV parsing/validation shared by the API and command-line importer."""
from __future__ import annotations

import csv
import io
from typing import Any


class DatasetImportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


ALIASES = {
    "company": {"company", "company name", "business", "business name"},
    "website": {"website", "website url", "site", "domain"},
    "city": {"city"},
    "state": {"state", "province"},
    "category": {"category", "business category"},
    "platform": {"platform", "ecommerce platform", "e-commerce platform"},
    "captcha": {"captcha", "captcha type", "captcha label"},
    "contact_page": {"contact page", "contact url", "contact page url", "contact"},
}
REQUIRED = {"company", "website", "platform", "captcha", "contact_page"}


def _normalize_header(value: str) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").strip().lower().replace("_", " ").split())


def _decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DatasetImportError("invalid_csv_encoding", "CSV must be UTF-8, UTF-16 or Windows-1252 encoded.")


def parse_dataset_csv(raw: bytes, *, max_rows: int = 10000) -> list[dict[str, Any]]:
    if not raw:
        raise DatasetImportError("empty_csv", "The uploaded CSV is empty.")

    text = _decode_csv(raw)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if not reader.fieldnames:
            raise DatasetImportError("missing_header", "The CSV does not contain a header row.")

        mapping: dict[str, str] = {}
        for actual in reader.fieldnames:
            normalized = _normalize_header(actual)
            for target, aliases in ALIASES.items():
                if normalized in aliases and target not in mapping:
                    mapping[target] = actual
                    break

        missing = sorted(REQUIRED - set(mapping))
        if missing:
            raise DatasetImportError(
                "missing_required_columns",
                "Missing required CSV columns: " + ", ".join(missing),
            )

        rows: list[dict[str, Any]] = []
        for line_number, source in enumerate(reader, start=2):
            if not source or not any(str(v or "").strip() for v in source.values()):
                continue
            if len(rows) >= max_rows:
                raise DatasetImportError("too_many_csv_rows", f"CSV exceeds the {max_rows} row limit.")

            row = {
                target: str(source.get(actual) or "").strip()
                for target, actual in mapping.items()
            }
            # Optional fields always exist for the DB insert statement.
            for key in ALIASES:
                row.setdefault(key, "")
            if not row["company"]:
                raise DatasetImportError("invalid_csv_row", f"Company is empty at CSV line {line_number}.")
            if not row["contact_page"]:
                raise DatasetImportError("invalid_csv_row", f"Contact Page is empty at CSV line {line_number}.")
            rows.append(row)

    except csv.Error as exc:
        raise DatasetImportError("invalid_csv", "The CSV contains malformed quoting or delimiters.") from exc

    if not rows:
        raise DatasetImportError("no_data_rows", "The CSV contains headers but no data rows.")
    return rows
