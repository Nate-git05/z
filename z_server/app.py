"""Z auth + integrations web application (FastAPI)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from z_server.config import get_settings
from z_server.db import init_db
from z_server.routers import auth as auth_router
from z_server.routers import dashboard as dashboard_router
from z_server.routers import mcp as mcp_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name}",
        description=(
            "Z account, workspace, and MCP integrations API. "
            "User accounts live in PostgreSQL via SQLAlchemy. "
            "Model API keys remain bring-your-own and are not stored here."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router.router)
    app.include_router(mcp_router.router)
    app.include_router(dashboard_router.router)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    def health():
        return {"ok": True, "service": "z-server"}

    return app


app = create_app()
