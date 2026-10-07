"""
Functional API tests. See conftest.py's module docstring for this suite's
execution status in the original audit sandbox (not run there; run it here).
"""
import sqlite3


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "site" in body


def test_index_page_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<html" in response.text.lower()


def test_anonymous_message_success_stores_and_responds(client, app_module):
    response = client.post(
        "/api/anonymous-message", json={"message": "A genuinely normal anonymous note."}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["stored"] is True

    conn = sqlite3.connect(app_module.DB_PATH)
    try:
        rows = conn.execute("SELECT message FROM anonymous_messages").fetchall()
    finally:
        conn.close()
    assert rows == [("A genuinely normal anonymous note.",)]


def test_anonymous_message_collapses_whitespace(client, app_module):
    response = client.post(
        "/api/anonymous-message",
        json={"message": "line one\n\n\nline two   with   spaces"},
    )
    assert response.status_code == 200

    conn = sqlite3.connect(app_module.DB_PATH)
    try:
        row = conn.execute("SELECT message FROM anonymous_messages").fetchone()
    finally:
        conn.close()
    # Documents current behavior: newlines/extra whitespace are collapsed to
    # single spaces before storage. See README for the product-decision note
    # on this -- it was flagged, not silently changed, in the audit.
    assert row[0] == "line one line two with spaces"


def test_no_ip_address_is_ever_stored(client, app_module):
    response = client.post(
        "/api/anonymous-message",
        json={"message": "checking that my IP is not stored anywhere"},
        headers={"X-Forwarded-For": "198.51.100.42"},
    )
    assert response.status_code == 200

    conn = sqlite3.connect(app_module.DB_PATH)
    try:
        row = conn.execute(
            "SELECT * FROM anonymous_messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        columns = [d[0] for d in conn.execute("SELECT * FROM anonymous_messages").description]
    finally:
        conn.close()

    assert "ip" not in [c.lower() for c in columns]
    for cell in row:
        assert "198.51.100.42" not in str(cell)


def test_database_schema_is_minimal(app_module):
    """Confirms exactly what is stored, matching the README's privacy claim:
    only the message text and a timestamp -- nothing else."""
    conn = sqlite3.connect(app_module.DB_PATH)
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(anonymous_messages)")]
    finally:
        conn.close()
    assert set(columns) == {"id", "message", "created_at"}
