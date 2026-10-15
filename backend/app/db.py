"""
db.py — SQLAlchemy engine, session factory, declarative base, and FastAPI
dependency for the ALDO Returns Detective Agent.

The database file path is read from Settings.database_url so it can be
overridden via the DATABASE_URL environment variable without changing code.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    # SQLite-specific: allow the same connection to be used across threads
    # (necessary for FastAPI's thread-pool request handling) and wait up to
    # 30s for a lock instead of failing immediately with "database is locked".
    # The real LLM (Bedrock) turn holds a request open for several seconds, so
    # concurrent tool sessions can otherwise collide on SQLite's single writer.
    connect_args={"check_same_thread": False, "timeout": 30}
    if _is_sqlite
    else {},
    echo=False,  # set to True during local debugging to log all SQL
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        """Enable WAL mode and a busy timeout so readers and a writer can
        coexist and brief lock contention is retried rather than erroring."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

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
