"""
db.py — SQLAlchemy engine, session factory, declarative base, and FastAPI
dependency for the ALDO Returns Detective Agent.

The database file path is read from Settings.database_url so it can be
overridden via the DATABASE_URL environment variable without changing code.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

engine = create_engine(
    settings.database_url,
    # SQLite-specific: allow the same connection to be used across threads
    # (necessary for FastAPI's thread-pool request handling).
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
    echo=False,  # set to True during local debugging to log all SQL
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

# ---------------------------------------------------------------------------
# Declarative base — all ORM models inherit from this
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_db() -> Generator[Session, None, None]:
    """
    Yield a SQLAlchemy session and ensure it is closed after the request,
    even if an exception is raised.

    Usage in a route::

        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
