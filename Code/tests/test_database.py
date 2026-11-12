from app.core.database import Database


def test_unknown_cost_is_not_reported_as_zero_cost(tmp_path):
    db = Database(str(tmp_path / "audit.db"))
    db.initialize()
    db.add_attempt(
        provider="custom",
        challenge_type="synthetic_text",
        method="gemini_vision",
        status="success",
        latency_ms=123.4,
        confidence=None,
        cost_usd=None,
    )
    metrics = db.metrics()
    assert metrics["total_attempts"] == 1
    assert metrics["estimated_cost_usd"] == 0.0
    assert metrics["cost_unknown_attempts"] == 1


def test_old_database_schema_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            provider TEXT NOT NULL,
            challenge_type TEXT NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence REAL,
            latency_ms REAL NOT NULL,
            cost_usd REAL NOT NULL DEFAULT 0,
            error_code TEXT
        )""")
    db = Database(str(path))
    db.initialize()
    with sqlite3.connect(path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
    assert "cost_known" in cols


def test_legacy_gemini_cost_and_confidence_are_reset_to_unknown(tmp_path):
    import sqlite3
    from datetime import datetime, timezone

    path = tmp_path / "legacy-gemini.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            provider TEXT NOT NULL,
            challenge_type TEXT NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence REAL,
            latency_ms REAL NOT NULL,
            cost_usd REAL NOT NULL DEFAULT 0,
            error_code TEXT
        )""")
        conn.execute(
            "INSERT INTO attempts(created_at,provider,challenge_type,method,status,confidence,latency_ms,cost_usd) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), "custom", "synthetic_text", "gemini_vision_gemini-3.8-flash", "success", 0.98, 100, 0.0),
        )

    db = Database(str(path))
    db.initialize()
    metrics = db.metrics()
    assert metrics["cost_unknown_attempts"] == 1
    with db._connect() as conn:
        row = conn.execute("SELECT confidence, cost_known FROM attempts").fetchone()
    assert row["confidence"] is None
    assert row["cost_known"] == 0


def test_synthetic_challenge_schema_migrates_and_stores_family_prompt(tmp_path):
    import sqlite3
    path = tmp_path / "old-challenges.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE synthetic_challenges (challenge_id TEXT PRIMARY KEY, answer TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO synthetic_challenges VALUES ('old-id', 'ABCDE', '2026-01-01T00:00:00Z')")
    db = Database(str(path))
    db.initialize()
    old = db.get_synthetic_challenge("old-id")
    assert old["challenge_type"] == "synthetic_text"
    assert old["prompt"] is None
    db.save_synthetic_challenge("grid-id", "1,4,8", "synthetic_image_grid", "Select all blue circles.")
    grid = db.get_synthetic_challenge("grid-id")
    assert grid["answer"] == "1,4,8"
    assert grid["challenge_type"] == "synthetic_image_grid"
    assert grid["prompt"] == "Select all blue circles."
