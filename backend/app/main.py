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
from sqlalchemy import text

from .db import Base, engine

# ---------------------------------------------------------------------------
# Router imports
# ---------------------------------------------------------------------------

from .api.routes_customer import router as customer_router
from .api.routes_admin import router as admin_router

# ---------------------------------------------------------------------------
# Lifespan: create DB tables on startup
# ---------------------------------------------------------------------------


def _warm_insight_baselines() -> None:
    """
    Precompute (and cache) the global + per-category reason baselines so the
    admin "Item insights" detail view is fast on the first click. Each baseline
    scans the returns table once and is then memoized in the engine; computing
    them here (in a background thread at startup) keeps that one-time cost off
    the user's first interaction. Failures are non-fatal.
    """
    try:
        from .db import SessionLocal
        from .insights.engine import (
            compute_category_baseline,
            compute_global_baseline,
            invalidate_baseline_cache,
        )

        # Clear any memoized baselines first so a restart never serves stale
        # entries (e.g. baselines keyed by old coarse groups) — the warm below
        # then repopulates the cache with the current granular reason codes.
        invalidate_baseline_cache()

        db = SessionLocal()
        try:
            compute_global_baseline(db)
            cats = [
                r[0]
                for r in db.execute(
                    text(
                        "SELECT DISTINCT category FROM order_items "
                        "WHERE category IS NOT NULL"
                    )
                ).fetchall()
            ]
            for cat in cats:
                compute_category_baseline(cat, db)
            print(f"[startup] warmed insight baselines for {len(cats)} categories")
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - warming must never crash startup
        print(f"[startup] baseline warm skipped: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """
    Startup: ensure all tables exist, then warm insight baselines in a daemon
    thread so the server starts responding immediately while the (one-time)
    baseline computation runs in the background.
    Teardown: nothing to clean up for SQLite.
    """
    Base.metadata.create_all(bind=engine)

    import threading

    threading.Thread(target=_warm_insight_baselines, daemon=True).start()
    yield


# (Warming runs in a daemon thread so it never blocks startup. With a
# multi-connection pool + WAL, concurrent reads proceed while it computes.)


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
