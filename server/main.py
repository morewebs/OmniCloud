"""FastAPI app: lifespan (DB init + sync tasks), REST API, MCP, SPA static serving."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.config
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import FileResponse
from starlette.routing import Route

from . import api, catalog, config, db, mcp_server, sync

STATIC = Path(__file__).resolve().parent / "static"

log = logging.getLogger("omnicloud.app")


def create_app() -> FastAPI:
    mcp_endpoint, mcp_manager = mcp_server.http_app()

    async def _startup() -> None:
        db.init()
        # crash/restart recovery: anything still in-flight died with the last
        # process - fail it visibly instead of leaving it stuck forever
        with db.connect() as conn:
            conn.execute("UPDATE actions SET status='failed', "
                         "detail='interrupted by restart' WHERE status='in_progress'")
            n_actions = conn.execute("SELECT changes()").fetchone()[0]
            stuck = conn.execute("SELECT id FROM orders WHERE status='executing'").fetchall()
            if stuck:
                ts = db.now()
                for row in stuck:
                    conn.execute("UPDATE orders SET status='failed', updated_at=? "
                                 "WHERE id=? AND status='executing'", (ts, row["id"]))
                    conn.execute("INSERT INTO order_events(order_id, status, "
                                  "detail, created_at) VALUES(?, 'failed', ?, ?)",
                                 (row["id"], "interrupted by restart", ts))
        if n_actions or stuck:
            log.warning("startup recovery: %d action(s) and %d order(s) "
                        "marked failed (interrupted by restart)",
                        n_actions, len(stuck))
        with db.connect() as conn:
            accounts_n = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            schema_v = conn.execute("PRAGMA user_version").fetchone()[0]
        log.info("OmniCloud starting: db=%s schema=%s accounts=%s master_key=%s",
                 config.DB_PATH, schema_v or db.SCHEMA_VERSION, accounts_n,
                 "set" if config.MASTER_KEY else "MISSING (credentials disabled)")
        sync.start_all()
        catalog.start()
        log.info("startup complete")

    async def _shutdown() -> None:
        log.info("shutting down: stopping background tasks")
        sync.stop_all()
        catalog.stop()
        # let cancelled tasks actually finish - a half-written sqlite tx on
        # shutdown is a corrupted row waiting to happen
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=5)
        log.info("shutdown complete")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await _startup()
        # the MCP session manager's task group must run for the app's lifetime
        async with mcp_manager.run():
            yield
        await _shutdown()

    # /api/docs: interactive API explorer. Endpoints still require auth
    # (session cookie or Bearer token) - the schema discloses nothing that
    # isn't already in the open-source codebase.
    app = FastAPI(title="OmniCloud", docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    app.include_router(api.router)
    app.middleware("http")(api.enforce_csrf)
    # MCP (Streamable HTTP): Bearer API tokens only, outside /api so the
    # cookie CSRF middleware never applies. Before the SPA catch-all.
    app.router.routes.append(Route("/mcp", endpoint=mcp_endpoint))

    # unhandled errors: JSON envelope (the SPA expects {detail}), never a stack
    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal error - see server logs"})

    # SPA: serve built frontend, fall back to index.html for client routes.
    if STATIC.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            file = (STATIC / path).resolve()
            # path-traversal guard: only files inside STATIC are served
            if file != STATIC.resolve() and STATIC.resolve() not in file.parents:
                return FileResponse(STATIC / "index.html")
            if file.is_file():
                return FileResponse(file)
            return FileResponse(STATIC / "index.html")

    return app


def _configure_logging() -> None:
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "iso": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "iso"},
        },
        "root": {"handlers": ["console"], "level": "INFO"},
    })


_configure_logging()
app = create_app()
