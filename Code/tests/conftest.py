"""Isolate pytest state from the developer's real local database and artifacts."""
from __future__ import annotations

import os
import shutil
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TEST_ROOT = Path(tempfile.gettempdir()) / f"captchalab-pytest-{os.getpid()}"
_TEST_ROOT.mkdir(parents=True, exist_ok=True)

# Set before tests import app.main (which creates Settings/Database at module load).
os.environ["DATABASE_PATH"] = str(_TEST_ROOT / "test.db")
os.environ["ARTIFACT_DIR"] = str(_TEST_ROOT / "artifacts")
os.environ["ALLOWED_TARGET_HOSTS"] = "localhost,127.0.0.1,::1,captchalab.test"
os.environ["APP_ENV"] = "test"


def pytest_sessionfinish(session, exitstatus):
    # TestClient closes its lifespan before session finish; remove only this test's temp dir.
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
