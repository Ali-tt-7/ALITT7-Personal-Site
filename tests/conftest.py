"""
Shared pytest fixtures for the ALITT7 test suite.

NOTE ON PROVENANCE: this file was written as part of a security/production
audit, but could NOT be executed in the sandbox that audit ran in --
outbound access to PyPI was blocked there (confirmed HTTP 403, a policy
denial, not a transient failure), and `fastapi`/`httpx`/`pytest` are not
preinstalled, so `pip install -r app/requirements.txt -r tests/requirements.txt`
could not run. The logic these tests check was instead verified in that
sandbox with a hand-built ASGI harness driving the real `app.main.app`
object directly (see the audit report for exactly what that covered). Run
this suite for real in any normal environment per the README's "Testing"
section -- it is expected to pass, but "expected" is not the same claim as
"observed," and this file is honest about which one applies here.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    """Imports app.main fresh, pointed at a throwaway SQLite file, with
    Telegram disabled (no token configured) so tests never make a real
    network call."""
    db_path = tmp_path / "messages.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("RATE_LIMIT_SECONDS", "45")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")

    # Ensure a clean import every time (module-level state like
    # _recent_requests must not leak between tests).
    for mod_name in list(sys.modules):
        if mod_name == "app.main" or mod_name == "app":
            del sys.modules[mod_name]

    import app.main as main  # noqa: PLC0415

    main.init_db()
    return main


@pytest.fixture
def client(app_module):
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as c:
        yield c
