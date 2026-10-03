"""FastAPI app: lifespan (DB init + sync tasks), REST API, SPA static serving."""
from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import FastAPI

from . import api, catalog, db, sync

STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="OmniCloud", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    app.include_router(api.router)
    app.middleware("http")(api.enforce_csrf)

    @app.on_event("startup")
    async def _startup() -> None:
        db.init()
        sync.start_all()
        catalog.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        sync.stop_all()
        catalog.stop()

    # SPA: serve built frontend, fall back to index.html for client routes.
    if STATIC.exists():
        from fastapi.staticfiles import StaticFiles
        from starlette.responses import FileResponse

        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            file = STATIC / path
            if file.is_file():
                return FileResponse(file)
            return FileResponse(STATIC / "index.html")

    return app


# Deprecated-style lifespan below kept minimal; on_event is fine for v1.
app = create_app()
