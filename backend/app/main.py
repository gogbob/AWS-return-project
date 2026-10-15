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
# Router imports
# ---------------------------------------------------------------------------

from .api.routes_customer import router as customer_router
from .api.routes_admin import router as admin_router

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
    title="ALDO Returns Agent",
    description="Demo returns system.",
    version="1.0.0",
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
# Router registration
# ---------------------------------------------------------------------------

app.include_router(customer_router)
app.include_router(admin_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Meta"])
def health_check() -> dict[str, str]:
    """Liveness probe — returns 200 when the server is running."""
    return {"status": "ok"}
