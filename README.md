# ALITT7 — Personal Website

A dark, futuristic, mobile-first personal site for Ali, with a real FastAPI backend for anonymous messages.

## Overview

- Single-page frontend (`app/static/index.html` + `styles.css` + `app.js`), no build step, no framework.
- FastAPI + SQLite backend serving the page, a JSON API for anonymous messages, and the static assets.
- Optional Telegram notification when a new anonymous message arrives.
- Designed to run as one small process + one SQLite file — intentionally not a microservice architecture.

## Features

- Premium dark/glassmorphism UI with dark/light mode toggle
- Responsive mobile + desktop layout
- Home / Projects / About / Journey / Contact sections
- Project cards for Vaulty Pro and Study Flow
- "Things I've worked with" instead of exaggerated skill claims
- Timeline + certificate section
- Anonymous message form (no name/email required)
- SQLite message storage, WAL mode
- Telegram notification for new anonymous messages (optional, best-effort)
- Honeypot anti-bot field
- In-memory rate limiting (not spoofable via `X-Forwarded-For` by default)
- No visitor IP stored in the database
- Scroll reveal animations, active nav highlighting, mobile nav, back-to-top
- SEO metadata, `robots.txt`, `sitemap.xml`, canonical URL
- Security response headers (CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, HSTS)
- Dockerfile (non-root user, healthcheck)
- A pytest suite under `tests/`

## Architecture

```
Browser ── GET / , /assets/*, /{other paths}  →  FastAPI serves app/static files
        └─ POST /api/anonymous-message         →  validate → rate-limit → SQLite (+ optional Telegram)
```

- `app/main.py` — the entire backend: routing, validation, rate limiting, SQLite access, Telegram call, security headers, the SPA-fallback static-file router.
- `app/static/` — the entire frontend. `index.html` is served at `/`; anything else under `app/static/` is served by a path-containment-checked fallback route (see Security notes).
- `data/messages.db` — SQLite file holding anonymous messages (just `id`, `message`, `created_at` — nothing else).

## Local setup

Python 3.13+ is recommended (3.11+ should work).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r app/requirements.txt
cp .env.example .env              # fill in Telegram values if you want notifications
```

### Development run

```bash
RELOAD=true python run.py
# or directly:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://127.0.0.1:8000`.

### Production run (without Docker)

```bash
python run.py
# or:
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`run.py` does **not** auto-reload unless `RELOAD=true` is set — auto-reload spawns a file-watcher process and should never run unattended in production.

## Environment variables

All variables are optional; the app runs with sane defaults if none are set. See `.env.example` for the canonical list:

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Bot token from BotFather. Leave empty to disable Telegram notifications entirely — the site still works and stores messages in SQLite. |
| `TELEGRAM_CHAT_ID` | *(empty)* | Chat ID that should receive the notification. |
| `SITE_NAME` | `ALITT7` | Used in the Telegram message text and the `/health` response. |
| `DATABASE_PATH` | `<project>/data/messages.db` | Where the SQLite file lives. |
| `MAX_MESSAGE_LENGTH` | `1200` | Max characters accepted for an anonymous message. |
| `RATE_LIMIT_SECONDS` | `45` | Minimum seconds between two submissions from the same client. |
| `TRUST_PROXY_HEADERS` | `false` | **Only** set to `true` if a reverse proxy in front of this app overwrites `X-Forwarded-For` itself (see Security notes below). |
| `PORT` | `8000` | Used by `run.py` only (not the Dockerfile, which hardcodes 8000). |
| `RELOAD` | `false` | Used by `run.py` only — enables uvicorn's dev auto-reload. |
| `LOG_LEVEL` | `INFO` | Python logging level for the app's own logger. |

## Telegram anonymous-message setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
3. Get the numeric chat ID that should receive messages and put it in `TELEGRAM_CHAT_ID`.
4. Never commit `.env` or a real token to Git — `.gitignore` and `.dockerignore` already exclude `.env`, and `.env.example` ships with empty placeholders only.

If Telegram delivery fails (bad token, network outage, Telegram API error) the request still succeeds as long as the message was stored in SQLite — Telegram is strictly best-effort and is never allowed to take down the whole endpoint. The JSON response includes `"stored"` and `"telegram_sent"` booleans so you can tell which channel actually got the message.

## Database

SQLite, WAL mode, a single table:

```sql
CREATE TABLE anonymous_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
)
```

That's it — no visitor IP, name, email, or user-agent is ever stored. Writes run off the asyncio event loop via a threadpool (SQLite's Python driver is blocking); each connection is opened and explicitly closed per write rather than left open.

**Persistence**: on a normal filesystem (local run, or Docker with the `data/` volume mounted), the database file persists across restarts. If you deploy to a platform with an ephemeral filesystem, `data/messages.db` will **not** survive a redeploy unless that platform gives you a persistent volume/disk — check your host's documentation; this project does not and cannot know that for you. Back up `data/messages.db` periodically if its contents matter to you.

**Concurrency**: this is a small personal site. SQLite's default busy-timeout (5s) and WAL mode are enough for its expected traffic. It is not tuned for high concurrent write volume, and it shouldn't need to be — if that ever changes, that's a sign to move to a real database, not to tune SQLite further.

## Security notes

- **Path traversal**: the SPA-fallback route that serves arbitrary files under `app/static/` resolves every candidate path and verifies it stays inside `STATIC_DIR` before serving it (`Path.relative_to()` containment check). `../` segments, percent-encoded variants, and similar are rejected (fall back to `index.html`), independent of how many segments or what encoding is used.
- **Rate limiting**: keyed off the real TCP peer address by default. `X-Forwarded-For` is only trusted if you explicitly set `TRUST_PROXY_HEADERS=true` — only do this if your hosting platform's edge/reverse proxy overwrites that header itself; otherwise any visitor can spoof it and bypass the limiter.
- **Request size**: a middleware rejects any `/api/*` request whose body exceeds 20 KB — checked from `Content-Length` when present, or by reading the body up front when it's absent (chunked requests). The anonymous message itself is capped at `MAX_MESSAGE_LENGTH` (1200) characters independently.
- **SQL injection**: every query is parameterized; nothing is ever string-interpolated into SQL.
- **XSS**: the frontend only ever uses `textContent`, never `innerHTML`/`outerHTML`/`insertAdjacentHTML`. There is no page in this app that renders visitor-submitted content back as HTML.
- **Telegram message injection**: the outgoing Telegram message is sent with no `parse_mode`, so Telegram renders it as plain text — a visitor cannot inject Markdown/HTML formatting, links, or mentions into the notification.
- **CORS**: intentionally not configured. The frontend only ever calls its own same-origin API; there's no reason to open this up cross-origin.
- **Security headers**: every response gets a Content-Security-Policy (`default-src 'self'`, Google Fonts allow-listed for style/font, no `unsafe-inline` since the page has no inline scripts/styles), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, a conservative `Permissions-Policy`, and `Strict-Transport-Security` (harmless over plain HTTP in local dev; effective once served over HTTPS).
- **Error handling**: unhandled exceptions are caught, logged server-side, and return a generic `500` — no stack trace, file path, or internal detail is ever sent to the client.
- **Secrets**: `.env` is gitignored and dockerignored. `.env.example` contains only empty placeholders. No token or credential is hardcoded anywhere in the source.

## Testing

```bash
pip install -r tests/requirements.txt
pytest tests/ -v
```

The suite covers: health/index endpoints, path-traversal payloads (plain and encoded), oversized requests, malformed JSON, validation failures, the honeypot field, rate limiting (including spoofed-header attempts), SQL-injection-shaped input, security headers, that no IP is ever stored, Telegram/database failure resilience, and static frontend checks (no unsafe DOM APIs, no dangling links/anchors, HTTPS-only external links, `rel=noopener` on `target=_blank` links, accessible form labeling).

> **Honesty note on provenance**: this suite was written during a security audit performed in a network-restricted sandbox where `pip install` to PyPI was blocked by policy (confirmed HTTP 403), so the `pytest`-based suite above could not be executed there. What *was* verified in that sandbox, using only already-installed packages (Starlette, Pydantic, the Python stdlib): a hand-built in-process ASGI harness drove the real `app.main.app` object (via a two-line import shim standing in only for the two missing packages, `fastapi` and `httpx`, not for any of their logic) through real HTTP-shaped requests — confirming the path-traversal fix, rate limiting, security headers, WAL mode, resilience logic, and 405/500 handling against the actual shipped code. The plain-stdlib frontend checks (no unsafe DOM APIs, broken links, etc.) were run directly and passed. Run `pytest tests/ -v` yourself in a normal environment for the full, standard confirmation.

## Docker

```bash
docker build -t alitt7 .
docker run -p 8000:8000 --env-file .env -v "$(pwd)/data:/app/data" alitt7
```

- Runs as a non-root user (`appuser`) inside the container.
- Includes a `HEALTHCHECK` against `/health`.
- `.dockerignore` keeps `.git`, `.env`, caches, and `*.db` files out of the build context.
- The `-v` volume mount is what makes `data/messages.db` survive `docker run`/`docker rm` cycles — without it, the database resets every time the container is recreated.

> This project's audit sandbox had no Docker daemon available (`docker` client present, but no daemon socket), so the build/run above could not be executed there. The Dockerfile was reviewed line-by-line instead. Build and run it yourself to confirm; if anything fails, it's most likely a base-image or registry-access issue in your environment, not application logic.

## Custom domain

The intended domain is `https://alitt7.dpdns.org`. Add it from your host's dashboard (e.g. Wasmer's **Settings → Domains** tab) and use the DNS target it gives you — don't guess an A/CNAME record.

## Tabriz hero image

The hero currently uses a CSS-generated cinematic scene so the page works immediately with no image file, and so nothing fake is ever presented as a real photograph. When you have a genuine photo of Tabriz, place it at `app/static/assets/tabriz-hero.jpg` and swap the hero to use it with a small, scoped CSS/HTML change — don't replace the placeholder with another placeholder dressed up as real.

## Known limitations / things to verify before relying on this in production

1. The rate limiter is in-memory and per-process. It's correct for one small instance; it resets on restart and isn't shared across multiple instances/workers. If you ever run more than one instance behind a load balancer, move this to a shared store (e.g. Redis) — not needed for this site's current scale.
2. "Anonymous" means the visitor doesn't submit a name/email and no IP is stored in the database. Hosting/network-level logs (reverse proxy access logs, etc.) can still exist outside this application's control.
3. If spam becomes a real problem, add Cloudflare Turnstile or another CAPTCHA in front of the endpoint rather than tightening the in-memory limiter further.
4. **Verify the Rubika (`rubika.ir/...`) and Bale (`ble.ir/...`) profile links actually resolve to the real accounts** before treating them as final — their exact current link format could not be independently confirmed during the audit that produced this version.
5. Confirm your hosting platform's filesystem persistence model for `data/messages.db` before relying on message history surviving a redeploy.

## Troubleshooting

- **"Address already in use" on startup** — something else is already listening on port 8000; set `PORT` (for `run.py`) or pass `--port` to uvicorn directly.
- **Anonymous messages aren't reaching Telegram** — check the server logs (`LOG_LEVEL=DEBUG` for more detail); a failed Telegram call is logged with `logger.warning(...)` but never shown to the visitor. Confirm the bot token and chat ID are correct and that the bot has been started/added to that chat.
- **"database is locked"** — very unlikely at this site's scale with WAL mode enabled, but if you see it under real concurrent load, that's the signal to move off SQLite rather than increase timeouts further.
- **Getting a 413 on a normal-looking message** — the request body exceeded the 20 KB cap; this should only happen with an abnormally large payload, not a normal 1200-character message.

## Folder structure

```text
ALITT7-Personal-Site/
├── app/
│   ├── main.py
│   ├── requirements.txt
│   └── static/
│       ├── index.html
│       ├── robots.txt
│       ├── sitemap.xml
│       └── assets/
│           ├── styles.css
│           ├── app.js
│           └── favicon.svg
├── data/
│   └── .gitkeep
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_security.py
│   ├── test_frontend_static.py
│   └── requirements.txt
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── run.py
└── README.md
```
