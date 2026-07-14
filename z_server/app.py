"""Z auth web application (FastAPI)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from z_server.config import get_settings
from z_server.db import init_db
from z_server.routers import auth as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} Auth",
        description=(
            "Z account authentication API for the CLI and web app. "
            "User accounts/workspaces live in PostgreSQL via SQLAlchemy. "
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

    @app.get("/health")
    def health():
        return {"ok": True, "service": "z-auth"}

    return app


app = create_app()
