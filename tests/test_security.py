"""
Security-focused tests. See conftest.py's module docstring for this suite's
execution status in the original audit sandbox (not run there; run it here).
"""
import sqlite3

import pytest


TRAVERSAL_PAYLOADS = [
    "/../main.py",
    "/../requirements.txt",
    "/../../data/.gitkeep",
    "/../../.env.example",
    "/assets/../../main.py",
    "/..%2fmain.py",
    "/....//....//etc/passwd",
    "/%2e%2e/%2e%2e/main.py",
    "/../../../../../../../../etc/passwd",
]


@pytest.mark.parametrize("path", TRAVERSAL_PAYLOADS)
def test_path_traversal_is_blocked(client, path):
    """No request should ever be able to read a file outside app/static/."""
    response = client.get(path)
    assert response.status_code in (200, 404)
    body = response.text
    # These strings only exist in real source/config files, never in the
    # index.html fallback page.
    assert "BASE_DIR = Path(__file__)" not in body
    assert "fastapi==" not in body
    assert "TELEGRAM_BOT_TOKEN=" not in body


def test_static_mount_blocks_traversal(client):
    response = client.get("/assets/../main.py")
    assert "BASE_DIR = Path(__file__)" not in response.text


def test_legit_static_asset_still_served(client):
    response = client.get("/assets/styles.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_xss_is_not_reflected(client):
    """The API never reflects visitor input back as HTML (it only ever
    returns small fixed JSON), so there is nothing here for a stored/
    reflected XSS payload to execute in. This test documents that
    expectation rather than exercising a template-rendering path that
    doesn't exist in this app."""
    payload = "<script>alert(1)</script>"
    response = client.post("/api/anonymous-message", json={"message": payload})
    assert response.status_code == 200
    assert "<script>" not in response.text


def test_sql_injection_shaped_message_is_stored_as_inert_text(client, app_module):
    payload = "'; DROP TABLE anonymous_messages; --"
    response = client.post("/api/anonymous-message", json={"message": payload})
    assert response.status_code == 200

    conn = sqlite3.connect(app_module.DB_PATH)
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        rows = conn.execute(
            "SELECT message FROM anonymous_messages WHERE message = ?", (payload,)
        ).fetchall()
    finally:
        conn.close()

    assert ("anonymous_messages",) in tables  # table was NOT dropped
    assert len(rows) == 1  # payload stored verbatim as inert text


def test_honeypot_field_is_silently_rejected(client, app_module):
    response = client.post(
        "/api/anonymous-message",
        json={"message": "hello", "website": "http://spam.example"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    conn = sqlite3.connect(app_module.DB_PATH)
    try:
        count = conn.execute("SELECT COUNT(*) FROM anonymous_messages").fetchone()[0]
    finally:
        conn.close()
    assert count == 0  # honeypot submissions must never be stored


def test_oversized_declared_body_rejected_before_parsing(client):
    huge_message = "y" * 30_000
    response = client.post(
        "/api/anonymous-message",
        content=('{"message": "%s"}' % huge_message).encode(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_oversized_message_field_rejected(client):
    response = client.post("/api/anonymous-message", json={"message": "x" * 5000})
    assert response.status_code == 422  # Pydantic max_length violation


def test_empty_message_rejected(client):
    response = client.post("/api/anonymous-message", json={"message": ""})
    assert response.status_code == 422


def test_malformed_json_returns_400_or_422_not_500(client):
    response = client.post(
        "/api/anonymous-message",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (400, 422)


def test_wrong_http_methods_rejected(client):
    assert client.post("/").status_code == 405
    assert client.delete("/api/anonymous-message").status_code == 405
    assert client.put("/health").status_code == 405


def test_forwarded_for_header_does_not_bypass_rate_limit(client):
    first = client.post("/api/anonymous-message", json={"message": "first message"})
    assert first.status_code == 200

    spoofed = client.post(
        "/api/anonymous-message",
        json={"message": "trying to bypass via spoofed header"},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    # TRUST_PROXY_HEADERS=false in the test fixture, so the spoofed header
    # must be ignored and the client still rate-limited.
    assert spoofed.status_code == 429


def test_rate_limit_blocks_rapid_second_submission(client):
    first = client.post("/api/anonymous-message", json={"message": "hello there"})
    assert first.status_code == 200
    second = client.post("/api/anonymous-message", json={"message": "again immediately"})
    assert second.status_code == 429


def test_security_headers_present(client):
    response = client.get("/health")
    assert response.headers.get("content-security-policy")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy")
    assert response.headers.get("permissions-policy")


def test_no_stack_trace_leaked_on_unexpected_error(client, app_module, monkeypatch):
    """Force the DB insert to fail and confirm the client never sees a
    traceback, file path, or internal exception message -- only the
    generic safe message."""

    def broken_insert(*args, **kwargs):
        raise RuntimeError("disk is on fire, /secret/internal/path exposed")

    monkeypatch.setattr(app_module, "_insert_message", broken_insert)
    monkeypatch.setattr(app_module, "notify_telegram", lambda message: False)

    response = client.post("/api/anonymous-message", json={"message": "will fail to store"})
    # Both storage paths failed -> 503, and nothing internal leaks either way.
    assert response.status_code == 503
    assert "/secret/internal/path" not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text


def test_message_survives_db_failure_if_telegram_succeeds(client, app_module, monkeypatch):
    def broken_insert(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(app_module, "_insert_message", broken_insert)
    monkeypatch.setattr(app_module, "notify_telegram", _async_true)

    response = client.post("/api/anonymous-message", json={"message": "telegram-only delivery"})
    assert response.status_code == 200
    body = response.json()
    assert body["stored"] is False
    assert body["telegram_sent"] is True


async def _async_true(message):
    return True
