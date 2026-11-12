from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class Database:
    """Small SQLite repository used by the local assessment application."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured SQLite connection and always close it.

        sqlite3.Connection's built-in context manager commits/rolls back but does
        not close the connection. This wrapper ensures API requests do not leak
        one connection per query.
        """
        path = Path(self.db_path).expanduser()
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    challenge_type TEXT NOT NULL,
                    method TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL,
                    latency_ms REAL NOT NULL,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    error_code TEXT,
                    cost_known INTEGER NOT NULL DEFAULT 1
                )"""
            )
            # Migrate databases from the earlier schema without deleting history.
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(attempts)").fetchall()
            }
            if "cost_known" not in columns:
                conn.execute(
                    "ALTER TABLE attempts ADD COLUMN cost_known INTEGER NOT NULL DEFAULT 1"
                )

            # Earlier builds recorded Gemini's unknown per-request cost as $0 and
            # fabricated confidence values. Correct those legacy metrics rather
            # than continuing to present them as measured values.
            conn.execute(
                "UPDATE attempts SET cost_known = 0, confidence = NULL "
                "WHERE LOWER(method) LIKE 'gemini%' OR LOWER(method) LIKE '%gemini_vision%'"
            )

            conn.execute(
                """CREATE TABLE IF NOT EXISTS imported_sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company TEXT, website TEXT, city TEXT, state TEXT,
                    category TEXT, platform TEXT, captcha_label TEXT, contact_page TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS synthetic_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    answer TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    challenge_type TEXT NOT NULL DEFAULT 'synthetic_text',
                    prompt TEXT
                )"""
            )
            challenge_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(synthetic_challenges)").fetchall()
            }
            if "challenge_type" not in challenge_columns:
                conn.execute(
                    "ALTER TABLE synthetic_challenges ADD COLUMN challenge_type TEXT NOT NULL DEFAULT 'synthetic_text'"
                )
            if "prompt" not in challenge_columns:
                conn.execute("ALTER TABLE synthetic_challenges ADD COLUMN prompt TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_attempts_created_at ON attempts(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_imported_sites_company ON imported_sites(company)"
            )

    def add_attempt(
        self,
        *,
        provider: str,
        challenge_type: str,
        method: str,
        status: str,
        latency_ms: float,
        confidence: float | None = None,
        cost_usd: float | None = 0.0,
        error_code: str | None = None,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        cost_known = cost_usd is not None
        stored_cost = max(0.0, float(cost_usd)) if cost_known else 0.0
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO attempts
                (created_at, provider, challenge_type, method, status, confidence,
                 latency_ms, cost_usd, error_code, cost_known)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    created_at,
                    str(provider)[:80],
                    str(challenge_type)[:120],
                    str(method)[:120],
                    str(status)[:30],
                    confidence,
                    max(0.0, float(latency_ms)),
                    stored_cost,
                    str(error_code)[:120] if error_code else None,
                    1 if cost_known else 0,
                ),
            )

    def replace_imported_sites(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            raise ValueError("cannot_replace_dataset_with_zero_rows")

        values = []
        for row in rows:
            values.append(
                {
                    "company": str(row.get("company", "")).strip(),
                    "website": str(row.get("website", "")).strip(),
                    "city": str(row.get("city", "")).strip(),
                    "state": str(row.get("state", "")).strip(),
                    "category": str(row.get("category", "")).strip(),
                    "platform": str(row.get("platform", "")).strip(),
                    "captcha": str(row.get("captcha", "")).strip(),
                    "contact_page": str(row.get("contact_page", "")).strip(),
                }
            )

        with self._connect() as conn:
            # DELETE and INSERT are one transaction. A failed insert rolls back the DELETE.
            conn.execute("DELETE FROM imported_sites")
            conn.executemany(
                """INSERT INTO imported_sites
                (company, website, city, state, category, platform, captcha_label, contact_page)
                VALUES (:company, :website, :city, :state, :category, :platform, :captcha, :contact_page)""",
                values,
            )
        return len(values)

    def save_synthetic_challenge(
        self,
        challenge_id: str,
        answer: str,
        challenge_type: str = "synthetic_text",
        prompt: str | None = None,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO synthetic_challenges
                   (challenge_id, answer, created_at, challenge_type, prompt)
                   VALUES (?, ?, ?, ?, ?)""",
                (challenge_id, answer, created_at, challenge_type, prompt),
            )

    def get_synthetic_challenge(self, challenge_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT challenge_id, answer, challenge_type, prompt, created_at
                   FROM synthetic_challenges WHERE challenge_id = ?""",
                (challenge_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_synthetic_answer(self, challenge_id: str) -> str | None:
        """Backwards-compatible accessor used by older callers/tests."""
        challenge = self.get_synthetic_challenge(challenge_id)
        return str(challenge["answer"]) if challenge else None

    def dataset_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM imported_sites").fetchone()[0]
            rows = conn.execute(
                """SELECT COALESCE(NULLIF(TRIM(captcha_label), ''), 'Unknown') AS label,
                          COUNT(*) AS count
                   FROM imported_sites
                   GROUP BY COALESCE(NULLIF(TRIM(captcha_label), ''), 'Unknown')
                   ORDER BY count DESC, label ASC"""
            ).fetchall()
        return {
            "total_records": int(total),
            "captcha_distribution": [dict(row) for row in rows],
        }

    def count_imported_sites(self, query: str = "") -> int:
        with self._connect() as conn:
            if query.strip():
                q = f"%{query.strip()}%"
                row = conn.execute(
                    """SELECT COUNT(*) FROM imported_sites
                       WHERE company LIKE ? OR website LIKE ? OR platform LIKE ?
                          OR captcha_label LIKE ? OR category LIKE ? OR contact_page LIKE ?""",
                    (q, q, q, q, q, q),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM imported_sites").fetchone()
        return int(row[0])

    def get_imported_sites(
        self, limit: int = 15, offset: int = 0, query: str = ""
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self._connect() as conn:
            if query.strip():
                q = f"%{query.strip()}%"
                rows = conn.execute(
                    """SELECT company, website, platform, captcha_label, category, contact_page
                       FROM imported_sites
                       WHERE company LIKE ? OR website LIKE ? OR platform LIKE ?
                          OR captcha_label LIKE ? OR category LIKE ? OR contact_page LIKE ?
                       ORDER BY id LIMIT ? OFFSET ?""",
                    (q, q, q, q, q, q, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT company, website, platform, captcha_label, category, contact_page
                       FROM imported_sites ORDER BY id LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
        return [dict(row) for row in rows]

    def metrics(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, latency_ms, cost_usd, cost_known FROM attempts ORDER BY latency_ms"
            ).fetchall()

        total = len(rows)
        success = sum(row["status"] == "success" for row in rows)
        failed = sum(row["status"] == "failed" for row in rows)
        unverified = sum(row["status"] == "unverified" for row in rows)
        inconclusive = sum(row["status"] == "inconclusive" for row in rows)
        latencies = [float(row["latency_ms"]) for row in rows]
        known_costs = [
            float(row["cost_usd"]) for row in rows if int(row["cost_known"])
        ]
        unknown_cost_attempts = sum(not int(row["cost_known"]) for row in rows)
        p95_index = (
            min(total - 1, max(0, int(0.95 * total + 0.999999) - 1)) if total else 0
        )
        return {
            "total_attempts": total,
            "successful_attempts": success,
            "failed_attempts": failed,
            "unverified_attempts": unverified,
            "inconclusive_attempts": inconclusive,
            "success_rate": round(success / total, 4) if total else 0.0,
            "average_latency_ms": round(sum(latencies) / total, 2) if total else 0.0,
            "p95_latency_ms": round(latencies[p95_index], 2) if total else 0.0,
            "estimated_cost_usd": round(sum(known_costs), 6),
            "cost_unknown_attempts": unknown_cost_attempts,
        }
