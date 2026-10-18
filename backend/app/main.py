"""FastAPI application factory & lifespan (DB init + migrations)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agents import engine  # noqa: F401 (registers controls)
from .agents.tools import bootstrap_tools, set_app_context
from .config import get_settings
import os
from .db import get_connection, run_migrations
from .openrouter import OpenRouterAdapter, ProviderError
from .routers import agents, chat, conversations, media, models, platform, projects
from .sandbox import PROJECTS_ROOT, WORKSPACE_ROOT
from .schemas import HealthOut

VERSION = "0.2.0"


def _db_path(database_url: str) -> str:
    # sqlite:///./nova.db -> ./nova.db ; tests override DATABASE_URL with tmp path
    assert database_url.startswith("sqlite"), \
        "Phase 1 supports SQLite only; PostgreSQL migration path documented in docs/"
    return database_url.removeprefix("sqlite:///")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    conn = await get_connection(_db_path(settings.database_url))
    applied = await run_migrations(conn)
    app.state.conn = conn
    app.state.openrouter = OpenRouterAdapter(settings)
    app.state.settings = settings
    import app.sandbox as sandbox_mod
    if os.environ.get("NOVA_WORKSPACE_ROOT"):
        root = Path(os.environ["NOVA_WORKSPACE_ROOT"])
        sandbox_mod.WORKSPACE_ROOT = root
        sandbox_mod.PROJECTS_ROOT = root / "projects"
    sandbox_mod.PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    app.state.tools = bootstrap_tools()
    set_app_context(conn)
    app.state.bg_tasks = set()
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    assets = WORKSPACE_ROOT / "assets"
    assets.mkdir(exist_ok=True)
    app.state.assets_dir = assets
    if applied:
        import logging
        logging.getLogger("nova").info("Applied migrations: %s", applied)
    yield
    # Cancel any in-flight agent tasks on shutdown.
    for t in list(app.state.bg_tasks):
        t.cancel()
    await conn.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="NOVA AI Backend", version=VERSION, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @app.exception_handler(ProviderError)
    async def provider_error_handler(_: Request, exc: ProviderError):
        return JSONResponse(exc.to_dict(), status_code=exc.status or 502)

    @app.get("/api/health", response_model=HealthOut, tags=["system"])
    async def health():
        s = app.state.settings
        return HealthOut(
            status="ok",
            provider_configured=bool(s.openrouter_api_key),
            default_model=s.openrouter_model,
            db="sqlite",
            version=VERSION,
        )

    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(models.router)
    app.include_router(agents.router)
    app.include_router(projects.router)
    app.include_router(projects.preview_router)
    app.include_router(media.router)
    app.include_router(platform.router)
    return app


app = create_app()
