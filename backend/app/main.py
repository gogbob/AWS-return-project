"""
main.py — FastAPI application factory for the ALDO Returns Detective Agent.

Responsibilities:
  - Create the FastAPI app instance with metadata.
  - Register CORS middleware (open for demo; tighten for production).
  - Mount API routers from the api/ subpackage.
  - Run a lifespan startup hook that ensures all ORM tables exist in the DB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Base, engine

# ---------------------------------------------------------------------------
# Router imports — add new routers here as submodules are implemented.
# Each router module should expose a `router` attribute (APIRouter instance).
# ---------------------------------------------------------------------------
# Example (uncomment as modules are built):
# from .api import ingest, returns, insights, flags, triage, agent

# ---------------------------------------------------------------------------
# Lifespan: create DB tables on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """
    Startup: ensure all tables defined via Base metadata exist.
    Teardown: nothing to clean up for SQLite; extend here for connection pools.
    """
    Base.metadata.create_all(bind=engine)
    yield


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ALDO Returns Detective Agent",
    description=(
        "AI-powered return triage and analytics for ALDO Group retail brands "
        "(ALDO, Call It Spring, Sperry)."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow all origins for demo / local development.
# Restrict origins, methods, and headers before deploying to production.
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Router registration — uncomment each line as the router module is created.
# ---------------------------------------------------------------------------
# app.include_router(ingest.router,   prefix="/api/ingest",   tags=["Ingest"])
# app.include_router(returns.router,  prefix="/api/returns",  tags=["Returns"])
# app.include_router(insights.router, prefix="/api/insights", tags=["Insights"])
# app.include_router(flags.router,    prefix="/api/flags",    tags=["Flags"])
# app.include_router(triage.router,   prefix="/api/triage",   tags=["Triage"])
# app.include_router(agent.router,    prefix="/api/agent",    tags=["Agent"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Meta"])
def health_check() -> dict[str, str]:
    """Liveness probe — returns 200 when the server is running."""
    return {"status": "ok"}
