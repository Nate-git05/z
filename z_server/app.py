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
from z_server.routers import gateway as gateway_router
from z_server.routers import mcp as mcp_router
from z_server.routers import skills as skills_router
from z_server.routers import uncertainty as uncertainty_router
from z_server.routers import waitlist as waitlist_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    import sys

    log = logging.getLogger("z_server")
    try:
        init_db()
        log.info("Database initialized")
    except Exception as err:
        settings = get_settings()
        hint = (
            "For local early testing, unset DATABASE_URL (defaults to sqlite ./z_server.db) "
            "or run: export DATABASE_URL='sqlite+pysqlite:///./z_server.db'"
            if settings.dev_mode
            else (
                "Check DATABASE_URL on Cloud Run. For Supabase use the URI with "
                "sslmode=require (auto-added if missing) and the psycopg driver, e.g. "
                "postgresql+psycopg://postgres:PASS@db.PROJECT.supabase.co:5432/postgres"
            )
        )
        # Print to stderr so Cloud Logging always surfaces the root cause.
        print(
            f"FATAL: Z server failed to initialize the database: {err}\n"
            f"DATABASE_URL host/driver check — {hint}",
            file=sys.stderr,
            flush=True,
        )
        raise RuntimeError(
            f"Z server failed to initialize the database ({settings.database_url!r}): {err}\n{hint}"
        ) from err
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name}",
        description=(
            "Z account, workspace, MCP, and routing-gateway API. "
            "User accounts live in PostgreSQL via SQLAlchemy. "
            "Provider model keys stay on the server (gateway); clients use Z auth."
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
    app.include_router(gateway_router.router)
    app.include_router(mcp_router.router)
    app.include_router(uncertainty_router.router)
    app.include_router(waitlist_router.router)
    app.include_router(skills_router.router)
    app.include_router(dashboard_router.router)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    def health():
        return {"ok": True, "service": "z-server"}

    return app


app = create_app()
