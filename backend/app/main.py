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


def _warm_insight_baselines() -> None:
    """
    Precompute (and cache) the global + per-category reason baselines so the
    admin "Item insights" detail view is fast on the first click. Each baseline
    scans the returns table once and is then memoized in the engine; computing
    them here (in a background thread at startup) keeps that one-time cost off
    the user's first interaction. Failures are non-fatal.

    ONLY the category/global baselines are routed through the selected data
    backend (SQLite / DuckDB / Athena) via app.insights.sources. The per-item
    distribution/anomaly path and the owner-return auto-apply recompute stay on
    SQLite in engine.py, so the Quincey/owner "too_small" demo keeps reflecting
    live rows instantly. The warm walks the athena -> duckdb -> sqlite fallback
    chain and never crashes startup.
    """
    try:
        from .config import settings
        from .db import SessionLocal
        from .insights.engine import invalidate_baseline_cache
        from .insights import sources

        # Clear any memoized baselines first so a restart never serves stale
        # entries (e.g. baselines keyed by old coarse groups) — the warm below
        # then repopulates the cache with the current granular reason codes.
        invalidate_baseline_cache()

        # Build the fallback order starting from the selected source, de-duped,
        # always ending at SQLite (the always-correct reference).
        selected = sources.select_baseline_source(settings)
        chain: list = [selected]
        for cls in (
            sources.AthenaBaselineSource,
            sources.DuckDBBaselineSource,
            sources.SqliteBaselineSource,
        ):
            if not any(isinstance(s, cls) for s in chain):
                chain.append(cls())

        last_exc: Exception | None = None
        for source in chain:
            db = SessionLocal()
            try:
                # Re-clear so a partial previous attempt never leaves a mix of
                # backends in the cache.
                invalidate_baseline_cache()
                cats = sources.populate_baseline_cache(source, db)
                extra = ""
                if source.name == "athena":
                    qid = sources.last_athena_query_id()
                    extra = f" (athena query id {qid})" if qid else ""
                print(
                    f"[startup] warmed insight baselines for {len(cats)} "
                    f"categories via '{source.name}'{extra}"
                )
                return
            except Exception as exc:  # noqa: BLE001 - try the next backend
                last_exc = exc
                print(
                    f"[startup] baseline backend '{source.name}' failed "
                    f"({exc}); falling back"
                )
            finally:
                db.close()

        # Should be unreachable: SqliteBaselineSource is always last and
        # delegates to the engine SQL that already works.
        print(f"[startup] baseline warm skipped: {last_exc}")
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
