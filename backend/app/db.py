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

if _is_sqlite:
    # Local SQLite demo under a threaded server. WAL mode (set in the connect
    # pragma below) lets multiple readers run concurrently with one writer, so
    # a long-running read (e.g. computing a category baseline, or a slow LLM
    # turn holding its session) does NOT block cheap reads on other
    # connections. We use a generously-sized QueuePool with a long checkout
    # timeout; pool_recycle avoids stale handles. (A single-connection
    # StaticPool would serialize every request behind the slowest one.)
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_size=20,
        max_overflow=40,
        pool_timeout=60,
        pool_recycle=1800,
        echo=False,
    )
else:
    engine = create_engine(settings.database_url, echo=False)


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
