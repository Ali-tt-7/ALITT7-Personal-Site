import logging
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("alitt7")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = (BASE_DIR / "static").resolve()
DB_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR.parent / "data" / "messages.db")))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SITE_NAME = os.getenv("SITE_NAME", "ALITT7")
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "1200"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "45"))

# Only trust X-Forwarded-For when the app is explicitly deployed behind a
# reverse proxy that overwrites/sets this header itself (e.g. the hosting
# platform's edge). Without this, any visitor can set their own
# X-Forwarded-For header and trivially bypass the rate limiter below.
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() == "true"

# Maximum accepted request body size, in bytes, for the anonymous-message
# endpoint. The message itself is capped at MAX_MESSAGE_LENGTH characters, so
# a JSON body should never legitimately need to be this large; anything
# bigger is rejected before it is parsed.
MAX_BODY_BYTES = 20_000

# Lightweight in-memory anti-spam limiter. For a single small personal site this is
# intentionally simple; for multiple instances, move rate limiting to a shared store.
_recent_requests: dict[str, float] = {}
_RATE_ENTRY_TTL = RATE_LIMIT_SECONDS * 20  # opportunistic cleanup horizon


def _prune_recent_requests(now: float) -> None:
    """Drop stale entries so this dict cannot grow without bound over a long
    process lifetime. Cheap and intentionally simple for a small single
    instance; see README for the multi-instance caveat."""
    stale_keys = [key for key, last in _recent_requests.items() if now - last > _RATE_ENTRY_TTL]
    for key in stale_keys:
        _recent_requests.pop(key, None)


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        # WAL mode lets a writer and readers proceed without blocking each
        # other and is the generally-recommended mode for a small SQLite
        # app like this one. It is a persistent, one-time setting stored in
        # the database file itself, so this only needs to run once here
        # (not on every connection) and is a no-op on subsequent startups.
        conn.execute("PRAGMA journal_mode=WAL")
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS anonymous_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
    finally:
        conn.close()


def _insert_message(message: str, created_at: str) -> None:
    """Blocking SQLite write, meant to be run off the event loop via
    run_in_threadpool. Always closes the connection explicitly: the sqlite3
    context manager only commits/rolls back a transaction, it does not close
    the connection, so relying on it alone leaks a connection (and a file
    descriptor) on every call."""
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            conn.execute(
                "INSERT INTO anonymous_messages(message, created_at) VALUES (?, ?)",
                (message, created_at),
            )
    finally:
        conn.close()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds a conservative set of security headers to every response.

    The CSP below was written by actually checking what index.html loads
    (grepped for inline <script>/style= attributes, external hosts, and
    data: URIs): there are no inline scripts or inline style attributes
    anywhere in the page, so no 'unsafe-inline' is needed. The only external
    origins are Google Fonts (stylesheet + font files); the only data: URI
    is the CSS noise-texture background image; the only fetch() call targets
    a same-origin relative path.
    """

    CSP = (
        "default-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = self.CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        # Browsers ignore HSTS on a plain-HTTP connection, so sending it
        # unconditionally is safe for local http:// development and becomes
        # effective once the site is actually served over HTTPS. No
        # includeSubDomains: we don't control what, if anything, gets put on
        # a future subdomain, and that directive is not safely reversible.
        response.headers["Strict-Transport-Security"] = "max-age=15552000"
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests whose body exceeds MAX_BODY_BYTES.

    Fast path: if the client sent a Content-Length header (true for every
    normal browser fetch() POST), we reject oversized requests immediately
    from the header alone, without reading the body.

    Fallback: a client using chunked transfer-encoding has no Content-Length
    header at all, so a header-only check would miss it entirely. In that
    case we read the body up front to measure its real size. This is safe
    and does not break downstream parsing: Starlette's Request caches the
    body the first time it is read, so FastAPI/Pydantic still see the full
    body normally afterwards. This is deliberately simple rather than a
    true chunk-by-chunk streaming limiter, which would be overkill for this
    site's traffic.
    """

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > MAX_BODY_BYTES:
                return JSONResponse(
                    {"detail": "Request body is too large."},
                    status_code=413,
                )
        else:
            body = await request.body()
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse(
                    {"detail": "Request body is too large."},
                    status_code=413,
                )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# No CORSMiddleware is added deliberately. The frontend is served by this
# same app and only ever calls its own API with a same-origin relative fetch
# ("/api/anonymous-message" - confirmed by reading app.js). There is no
# cookie-based auth or other credential this API could leak cross-origin, but
# a public JSON POST endpoint still has no reason to invite cross-origin
# callers, so CORS is intentionally left unconfigured (same-origin only)
# rather than opened with allow_origins=["*"].
app = FastAPI(title=SITE_NAME, version="1.0.0", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Defense in depth: FastAPI/Starlette already avoid leaking tracebacks to
    # the client by default when not running in debug mode, but this makes
    # that explicit and guarantees we log server-side instead of losing the
    # error silently.
    logger.exception("Unhandled exception while processing %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error."}, status_code=500)


class AnonymousMessage(BaseModel):
    message: str = Field(min_length=1, max_length=1200)
    website: str = Field(default="", max_length=100)  # honeypot


def client_key(request: Request) -> str:
    # We do not store this value in the database. It exists only in memory for
    # a short anti-spam window.
    #
    # X-Forwarded-For is attacker-controlled input: any visitor can set it
    # themselves, so trusting it blindly lets anyone bypass the rate limiter
    # just by sending a different value on every request. It is only safe to
    # read when a trusted reverse proxy in front of this app overwrites the
    # header itself (set TRUST_PROXY_HEADERS=true in that case).
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "")
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop
    return request.client.host if request.client else "unknown"


def clean_message(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value


async def notify_telegram(message: str):
    if not BOT_TOKEN or not CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": (
            "📨 Anonymous message\n\n"
            f"{message}\n\n"
            f"🌐 {SITE_NAME}"
        ),
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return True
    except Exception:
        # Do not expose Telegram configuration/network details to visitors,
        # but do log server-side so a broken bot token or outage is
        # actually visible to whoever is operating the site.
        logger.warning("Telegram notification failed", exc_info=True)
        return False


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "site": SITE_NAME}


@app.post("/api/anonymous-message")
async def anonymous_message(payload: AnonymousMessage, request: Request):
    # Honeypot: bots that fill hidden fields are silently rejected.
    if payload.website:
        return {"ok": True}

    message = clean_message(payload.message)
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=400, detail="Message is too long.")

    key = client_key(request)
    now = time.time()
    _prune_recent_requests(now)
    last = _recent_requests.get(key, 0)
    if now - last < RATE_LIMIT_SECONDS:
        remaining = max(1, RATE_LIMIT_SECONDS - int(now - last))
        raise HTTPException(
            status_code=429,
            detail=f"Please wait about {remaining} seconds before sending another message.",
        )
    _recent_requests[key] = now

    created_at = datetime.now(timezone.utc).isoformat()

    # Store only the message and timestamp. No IP is stored in SQLite.
    # Run off the event loop: sqlite3 is blocking, and this endpoint is async.
    #
    # A storage failure (disk full, permissions, a locked/corrupted database
    # file) should not by itself make the whole submission fail if Telegram
    # delivery still works, and vice versa -- the message only needs to
    # reach one of its two destinations to count as delivered.
    stored = True
    try:
        await run_in_threadpool(_insert_message, message, created_at)
    except Exception:
        logger.exception("Failed to store anonymous message in SQLite")
        stored = False

    telegram_sent = await notify_telegram(message)

    if not stored and not telegram_sent:
        raise HTTPException(
            status_code=503,
            detail="Could not deliver your message right now. Please try again shortly.",
        )

    return {
        "ok": True,
        "stored": stored,
        "telegram_sent": telegram_sent,
        "message": "Your message was sent.",
    }


@app.get("/{path:path}")
async def spa_fallback(path: str):
    # SECURITY: `path` is attacker-controlled. Joining it onto STATIC_DIR and
    # calling .is_file() on the result, without resolving and checking
    # containment, allows "../" segments (e.g. "../main.py" or
    # "../../.env") to escape STATIC_DIR entirely and serve arbitrary files
    # readable by the process — including application source and secrets.
    # Resolving the candidate and requiring it to stay inside STATIC_DIR
    # closes that off regardless of how many "../" segments are used.
    candidate = (STATIC_DIR / path).resolve()
    try:
        candidate.relative_to(STATIC_DIR)
    except ValueError:
        return FileResponse(STATIC_DIR / "index.html")

    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(STATIC_DIR / "index.html")
