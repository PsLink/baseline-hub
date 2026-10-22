from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from sqlalchemy import text

from baseline_hub import api, auth, github, publication_api, sessions, ui_api
from baseline_hub.config import Settings
from baseline_hub.db import database


def create_app(config=None, *, engine=None, factory=None):
    config = config or Settings()
    if factory is None:
        engine, factory = database(config.database_url)
    app = FastAPI(title="Baseline Hub", version="0.1.0")
    app.state.settings, app.state.engine, app.state.factory = config, engine, factory

    @app.exception_handler(HTTPException)
    async def platform_error(request, exc):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "code": exc.detail["code"],
                    "detail": exc.detail["message"],
                },
                headers=exc.headers,
            )
        return await http_exception_handler(request, exc)

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        if not sessions.check_origin(request, config):
            return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    for router in (auth.router, github.router, publication_api.router, api.router, ui_api.router):
        app.include_router(router, prefix="/api")

    @app.get("/api/health")
    def health():
        with factory() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app
