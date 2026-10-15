"""
conftest.py — Shared pytest fixtures for the ALDO Returns Detective test suite.

Provides:
  - test_db:     in-memory SQLite session; all tables created + minimal seed data
  - test_client: FastAPI TestClient wired to test_db via dependency override
  - mock_llm:    sets LLM_MOCK=true in environment (also set globally at module level)

Implementation note
-------------------
SQLite `:memory:` databases are connection-scoped.  FastAPI routes run in
anyio worker threads, which can acquire a different connection from the pool.
To share one in-memory database across connections we use the SQLite
*named shared-cache* URI `file:testdb?mode=memory&cache=shared`.
All connections that reference the same URI name see the same database.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Force mock mode before any app module is imported so Settings picks it up.
# LLM_MOCK=true prevents any boto3 / AWS SDK initialisation during tests.
# ---------------------------------------------------------------------------
os.environ.setdefault("LLM_MOCK", "true")
# Use a named shared-cache in-memory SQLite so all connections (including
# those spawned by anyio worker threads) share the same dataset.
_TEST_DB_URI = "file:aldo_test_db?mode=memory&cache=shared"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB_URI}")

# ---------------------------------------------------------------------------
# Now import app modules (after env overrides are in place)
# ---------------------------------------------------------------------------
from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

# ---------------------------------------------------------------------------
# Shared-cache in-memory engine — all connections see the same data
# ---------------------------------------------------------------------------
_TEST_ENGINE = create_engine(
    f"sqlite:///{_TEST_DB_URI}",
    connect_args={"check_same_thread": False, "uri": True},
    echo=False,
)
_TestSessionLocal = sessionmaker(bind=_TEST_ENGINE, autocommit=False, autoflush=False)


def _create_all_tables() -> None:
    """Create all ORM-defined tables in the shared-cache in-memory engine."""
    # Import all models to register them with Base.metadata
    from app.ingest.models import (  # noqa: F401
        AgentChange,
        Case,
        CaseEvidence,
        CaseMessage,
        Customer,
        Exchange,
        OrderItem,
        Recommendation,
        RestockingFee,
        Return,
        ReturnableFlag,
        Store,
        UnmappedReason,
    )
    Base.metadata.create_all(bind=_TEST_ENGINE)


def _seed_minimal_data(session: Session) -> None:
    """Insert just enough rows for the test suite to exercise each code path."""
    now = datetime.utcnow()

    # ── Customers ────────────────────────────────────────────────────────────
    session.execute(
        text(
            "INSERT OR IGNORE INTO customers (customer_id, loyalty, fraud_flag) VALUES "
            "('CUST-HIGH', 85, 0)"
        )
    )
    session.execute(
        text(
            "INSERT OR IGNORE INTO customers (customer_id, loyalty, fraud_flag) VALUES "
            "('CUST-LOW', 20, 0)"
        )
    )
    session.execute(
        text(
            "INSERT OR IGNORE INTO customers (customer_id, loyalty, fraud_flag) VALUES "
            "('CUST-DEMO', 50, 0)"
        )
    )
    session.execute(
        text(
            "INSERT OR IGNORE INTO customers (customer_id, loyalty, fraud_flag) VALUES "
            "('CUST-FRAUD', 0, 1)"
        )
    )

    # ── Order items ──────────────────────────────────────────────────────────
    session.execute(
        text(
            "INSERT OR IGNORE INTO order_items "
            "(item_id, sku, orderitem_product_name, category, store_banner) VALUES "
            "('ITEM-BOOTS', 'SKU-001', 'Demo Ankle Boot', 'boots', 'ALDO')"
        )
    )
    session.execute(
        text(
            "INSERT OR IGNORE INTO order_items "
            "(item_id, sku, orderitem_product_name, category, store_banner) VALUES "
            "('ITEM-SANDALS', 'SKU-002', 'Demo Sandal', 'sandals', 'ALDO')"
        )
    )
    session.execute(
        text(
            "INSERT OR IGNORE INTO order_items "
            "(item_id, sku, orderitem_product_name, category, store_banner) VALUES "
            "('ITEM-SNEAKERS', 'SKU-003', 'Demo Sneaker', 'sneakers', 'ALDO')"
        )
    )

    # ── Historical returns (CSV source) ──────────────────────────────────────
    # 40 completed returns for ITEM-BOOTS from 40 distinct customers.
    # 30 use reason_code='too_small' (FIT_SIZE group), 10 use 'other' (OTHER).
    # This satisfies MIN_RETURNS_FLAG=30 and MIN_CUSTOMERS_FLAG=20.
    for i in range(40):
        reason_code = "too_small" if i < 30 else "other"
        reason = "too small" if i < 30 else "changed my mind / other"
        cust = f"C{i:04d}"
        session.execute(
            text(
                "INSERT OR IGNORE INTO customers (customer_id, loyalty, fraud_flag) "
                "VALUES (:cid, 50, 0)"
            ),
            {"cid": cust},
        )
        session.execute(
            text(
                "INSERT INTO returns "
                "(order_number, sku, item_id, customer_uid, return_order_number, "
                " return_creation_date, last_return_status, reason, reason_code, "
                " source, fraud_flag) "
                "VALUES (:on, 'SKU-001', 'ITEM-BOOTS', :cid, :ron, :now, "
                "        'RETURN_COMPLETED', :reason, :rc, 'csv', 0)"
            ),
            {
                "on": f"ORD-BOOT-{i:04d}",
                "cid": cust,
                "ron": f"RTN-BOOT-{i:04d}",
                "now": now,
                "reason": reason,
                "rc": reason_code,
            },
        )

    # ── One CUST-HIGH return (for eligibility checks to pass) ─────────────────
    session.execute(
        text(
            "INSERT OR IGNORE INTO returns "
            "(order_number, sku, item_id, customer_uid, return_order_number, "
            " return_creation_date, last_return_status, reason, reason_code, "
            " source, fraud_flag) "
            "VALUES ('ORD-HIGH-001', 'SKU-001', 'ITEM-BOOTS', 'CUST-HIGH', "
            "        'RTN-HIGH-001', :now, 'RETURN_COMPLETED', "
            "        'too small', 'too_small', 'csv', 0)"
        ),
        {"now": now},
    )

    # ── One CUST-LOW return ───────────────────────────────────────────────────
    session.execute(
        text(
            "INSERT OR IGNORE INTO returns "
            "(order_number, sku, item_id, customer_uid, return_order_number, "
            " return_creation_date, last_return_status, reason, reason_code, "
            " source, fraud_flag) "
            "VALUES ('ORD-LOW-001', 'SKU-001', 'ITEM-BOOTS', 'CUST-LOW', "
            "        'RTN-LOW-001', :now, 'RETURN_COMPLETED', "
            "        'too small', 'too_small', 'csv', 0)"
        ),
        {"now": now},
    )

    # ── One returnable-flag row (not final sale) ──────────────────────────────
    session.execute(
        text(
            "INSERT OR IGNORE INTO returnable_flags "
            "(order_number, sku, is_final_sale) "
            "VALUES ('ORD-HIGH-001', 'SKU-001', 0)"
        )
    )
    session.execute(
        text(
            "INSERT OR IGNORE INTO returnable_flags "
            "(order_number, sku, is_final_sale) "
            "VALUES ('ORD-LOW-001', 'SKU-001', 0)"
        )
    )

    # ── Stores ───────────────────────────────────────────────────────────────
    session.execute(
        text(
            "INSERT OR IGNORE INTO stores (name, address, lat, lng, verified) "
            "VALUES ('ALDO McGill', '1307 Saint-Catherine St W', 45.5017, -73.5718, 1)"
        )
    )

    session.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def test_db() -> Generator[Session, None, None]:
    """
    Yield a fresh in-memory SQLite session for each test function.

    Tables are recreated and seed data is inserted on every call so
    tests are fully isolated.
    """
    # Drop and recreate tables for clean state
    Base.metadata.drop_all(bind=_TEST_ENGINE)
    _create_all_tables()

    session = _TestSessionLocal()
    _seed_minimal_data(session)

    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def test_client(test_db: Session) -> TestClient:
    """
    Return a FastAPI TestClient that uses the in-memory test_db session.

    The `get_db` dependency is overridden so every request uses the same
    shared-cache in-memory session, keeping the test state in sync.
    """

    def _override_get_db():
        # Open a fresh connection to the shared-cache DB for each request
        # so the worker thread gets a valid connection without thread issues.
        request_session = _TestSessionLocal()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch) -> None:
    """
    Ensure LLM_MOCK=true is set for every test.

    Patching both the env var and the live `settings` object so no boto3
    calls are ever attempted during test runs.
    """
    monkeypatch.setenv("LLM_MOCK", "true")
    # Patch the settings singleton used by all app modules
    from app import config as _cfg
    monkeypatch.setattr(_cfg.settings, "llm_mock", True)


# ---------------------------------------------------------------------------
# Helpers — used by multiple test modules
# ---------------------------------------------------------------------------


def make_case(
    session: Session,
    customer_id: str = "CUST-HIGH",
    order_number: str = "ORD-HIGH-001",
    sku: str = "SKU-001",
    item_id: str = "ITEM-BOOTS",
    reason_code: str = "too_small",
    status: str = "PENDING",
) -> str:
    """Insert a minimal case row and return its UUID string."""
    case_id = str(uuid.uuid4())
    session.execute(
        text(
            "INSERT INTO cases "
            "(id, customer_id, order_number, sku, item_id, reason_code, "
            " status, source_channel, created_at) "
            "VALUES (:id, :cid, :on, :sku, :iid, :rc, :status, 'online', :now)"
        ),
        {
            "id": case_id,
            "cid": customer_id,
            "on": order_number,
            "sku": sku,
            "iid": item_id,
            "rc": reason_code,
            "status": status,
            "now": datetime.utcnow(),
        },
    )
    session.commit()
    return case_id
