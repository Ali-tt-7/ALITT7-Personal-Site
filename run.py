import os

import uvicorn

if __name__ == "__main__":
    # reload=True is a development convenience (it spawns a file-watcher
    # process) and should not run unattended in production. Set RELOAD=true
    # locally if you want auto-reload; it defaults to off.
    reload = os.getenv("RELOAD", "false").strip().lower() == "true"
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=reload)
