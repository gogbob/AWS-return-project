"""
test_ingest.py — Tests for acceptance criteria 1, 4, and 9.

Criterion 1: Ingest pipeline runs without error and populates tables.
Criterion 4: Deduplication counts each customer once per item.
Criterion 9: A live return (source='live') increments the item count.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.insights.engine import compute_item_distribution


# ---------------------------------------------------------------------------
# Criterion 1 — Ingest runs without error, tables populated
# ---------------------------------------------------------------------------


def test_ingest_runs_without_error(test_db: Session) -> None:
    """
    The test DB fixture already creates all 13 tables and seeds data.
    Verify that the key tables have rows (proving the schema and seed work).
    """
    order_items_count = test_db.execute(
        text("SELECT COUNT(*) FROM order_items")
    ).scalar()
    assert order_items_count is not None and order_items_count > 0, (
        "order_items table must be non-empty after seeding"
    )

    returns_count = test_db.execute(
        text("SELECT COUNT(*) FROM returns")
    ).scalar()
    assert returns_count is not None and returns_count > 0, (
        "returns table must be non-empty after seeding"
    )

    customers_count = test_db.execute(
        text("SELECT COUNT(*) FROM customers")
    ).scalar()
    assert customers_count is not None and customers_count > 0, (
        "customers table must be non-empty after seeding"
    )


def test_all_thirteen_tables_exist(test_db: Session) -> None:
    """Every one of the 13 expected tables must exist and be queryable."""
    tables = [
        "order_items",
        "returns",
        "returnable_flags",
        "restocking_fees",
        "exchanges",
        "customers",
        "stores",
        "cases",
        "case_messages",
        "case_evidence",
        "recommendations",
        "agent_changes",
        "unmapped_reasons",
    ]
    for table in tables:
        count = test_db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        assert count is not None, f"Table {table!r} must exist"


# ---------------------------------------------------------------------------
# Criterion 4 — Deduplication: compute_item_distribution counts each
#               customer only once per item (most-recent return).
# ---------------------------------------------------------------------------


def test_dedup_customer_counts_once_per_item(test_db: Session) -> None:
    """
    Customer C1 has 5 return rows for ITEM-DEDUP (same item_id, different
    return_creation_date).  compute_item_distribution must count them as
    a single observation (n == 1).
    """
    now = datetime.utcnow()

    # Ensure the item exists in order_items
    test_db.execute(
        text(
            "INSERT OR IGNORE INTO order_items "
            "(item_id, sku, orderitem_product_name, category, store_banner) "
            "VALUES ('ITEM-DEDUP', 'SKU-DEDUP', 'Dedup Test Item', 'boots', 'ALDO')"
        )
    )

    # Customer DEDUP-C1 with loyalty 50
    test_db.execute(
        text(
            "INSERT OR IGNORE INTO customers (customer_id, loyalty, fraud_flag) "
            "VALUES ('DEDUP-C1', 50, 0)"
        )
    )

    # 5 return rows for the same (customer, item), different dates
    for i in range(5):
        test_db.execute(
            text(
                "INSERT INTO returns "
                "(order_number, sku, item_id, customer_uid, "
                " return_creation_date, last_return_status, reason_code, "
                " source, fraud_flag) "
                "VALUES (:on, 'SKU-DEDUP', 'ITEM-DEDUP', 'DEDUP-C1', "
                "        datetime('now', :delta), 'RETURN_COMPLETED', 'too_small', "
                "        'csv', 0)"
            ),
            {"on": f"ORD-DEDUP-{i}", "delta": f"-{i} days"},
        )
    test_db.commit()

    dist = compute_item_distribution("ITEM-DEDUP", test_db)
    n = dist.get("n", 0)
    assert n == 1, (
        f"Expected n=1 (one customer deduplicated), got n={n}"
    )


def test_dedup_two_customers_counted_separately(test_db: Session) -> None:
    """
    Two distinct customers for the same item must both be counted (n == 2).
    """
    test_db.execute(
        text(
            "INSERT OR IGNORE INTO order_items "
            "(item_id, sku, orderitem_product_name, category, store_banner) "
            "VALUES ('ITEM-TWO', 'SKU-TWO', 'Two Customers Item', 'sandals', 'ALDO')"
        )
    )
    for cid in ("TWO-C1", "TWO-C2"):
        test_db.execute(
            text(
                "INSERT OR IGNORE INTO customers (customer_id, loyalty, fraud_flag) "
                "VALUES (:cid, 50, 0)"
            ),
            {"cid": cid},
        )
        test_db.execute(
            text(
                "INSERT INTO returns "
                "(order_number, sku, item_id, customer_uid, "
                " return_creation_date, last_return_status, reason_code, "
                " source, fraud_flag) "
                "VALUES (:on, 'SKU-TWO', 'ITEM-TWO', :cid, "
                "        '2025-01-01', 'RETURN_COMPLETED', 'too_small', 'csv', 0)"
            ),
            {"on": f"ORD-{cid}", "cid": cid},
        )
    test_db.commit()

    dist = compute_item_distribution("ITEM-TWO", test_db)
    assert dist.get("n", 0) == 2, (
        "Expected n=2 (two distinct customers each counted once)"
    )


# ---------------------------------------------------------------------------
# Criterion 9 — Live return increments item count
# ---------------------------------------------------------------------------


def test_live_return_increments_item_counts(test_db: Session) -> None:
    """
    Inserting a live return (source='live') for ITEM-BOOTS increments
    compute_item_distribution('ITEM-BOOTS').n by exactly 1 for a new customer.
    """
    dist_before = compute_item_distribution("ITEM-BOOTS", test_db)
    n_before = dist_before.get("n", 0)

    # Insert a brand-new customer so we are guaranteed to add a new observation
    new_cust = f"LIVE-CUST-{uuid.uuid4().hex[:8]}"
    test_db.execute(
        text(
            "INSERT INTO customers (customer_id, loyalty, fraud_flag) "
            "VALUES (:cid, 50, 0)"
        ),
        {"cid": new_cust},
    )
    test_db.execute(
        text(
            "INSERT INTO returns "
            "(order_number, sku, item_id, customer_uid, "
            " return_creation_date, last_return_status, reason_code, "
            " source, fraud_flag) "
            "VALUES (:on, 'SKU-001', 'ITEM-BOOTS', :cid, "
            "        :now, 'RETURN_COMPLETED', 'too_small', 'live', 0)"
        ),
        {
            "on": f"ORD-LIVE-{uuid.uuid4().hex[:8]}",
            "cid": new_cust,
            "now": datetime.utcnow(),
        },
    )
    test_db.commit()

    dist_after = compute_item_distribution("ITEM-BOOTS", test_db)
    n_after = dist_after.get("n", 0)

    assert n_after == n_before + 1, (
        f"Expected item count to grow by 1 after live return "
        f"(before={n_before}, after={n_after})"
    )
