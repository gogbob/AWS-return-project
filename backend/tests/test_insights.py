"""
test_insights.py — Tests for acceptance criteria 2 and 3.

Criterion 2: Category baseline equals median of per-item shares, renormalised.
Criterion 3: Anomaly detection flags significant deviations; respects MIN_RETURNS_FLAG.
"""

from __future__ import annotations

import statistics
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.insights.engine import (
    compute_category_baseline,
    compute_item_distribution,
    compute_anomaly,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_item_with_returns(
    session: Session,
    item_id: str,
    category: str,
    returns: list[tuple[str, str]],  # [(customer_uid, reason_code), ...]
) -> None:
    """Insert an order_item and N completed returns into the test DB."""
    session.execute(
        text(
            "INSERT OR IGNORE INTO order_items "
            "(item_id, sku, orderitem_product_name, category, store_banner) "
            "VALUES (:iid, :sku, :name, :cat, 'ALDO')"
        ),
        {"iid": item_id, "sku": item_id, "name": item_id, "cat": category},
    )
    for idx, (cid, rc) in enumerate(returns):
        session.execute(
            text(
                "INSERT OR IGNORE INTO customers (customer_id, loyalty, fraud_flag) "
                "VALUES (:cid, 50, 0)"
            ),
            {"cid": cid},
        )
        session.execute(
            text(
                "INSERT INTO returns "
                "(order_number, sku, item_id, customer_uid, "
                " return_creation_date, last_return_status, reason, reason_code, "
                " source, fraud_flag) "
                "VALUES (:on, :sku, :iid, :cid, "
                "        '2025-01-01', 'RETURN_COMPLETED', :reason, :rc, 'csv', 0)"
            ),
            {
                "on": f"ORD-{item_id}-{idx}",
                "sku": item_id,
                "iid": item_id,
                "cid": cid,
                # The insights engine derives the granular code from the free-text
                # `reason` column via reason_to_code(); store a raw reason that
                # maps back to the intended code so the test's `rc` label resolves.
                "reason": _REASON_TEXT_FOR_CODE.get(rc, rc),
                "rc": rc,
            },
        )
    session.commit()


# Raw `reason` text whose taxonomy mapping resolves to the test's code label.
# Codes that are identity-mapped in reason_taxonomy.yaml (too_small, other, …)
# are stored as-is; 'defect' has no identity key so use a mapped synonym.
_REASON_TEXT_FOR_CODE = {
    "defect": "defective",
}


# ---------------------------------------------------------------------------
# Criterion 2 — Baseline equals median of per-item shares, renormalised
# ---------------------------------------------------------------------------


def test_baseline_equals_median_renormalized(test_db: Session) -> None:
    """
    Build 15 items in category 'boots-test', each with 35 completed returns
    spread across two reason codes.  Verify that compute_category_baseline
    returns shares that match the manually-computed median-then-renormalise.
    """
    category = "boots-test"
    codes = ("too_small", "other")
    # Vary the split across items so the median is meaningful
    splits = [
        (20, 15), (25, 10), (30, 5), (18, 17), (22, 13),
        (28, 7),  (15, 20), (10, 25), (35, 0),  (0, 35),
        (17, 18), (24, 11), (19, 16), (21, 14), (16, 19),
    ]
    assert len(splits) == 15

    per_item_shares: list[dict[str, float]] = []

    for i, (n_fit, n_other) in enumerate(splits):
        item_id = f"ITEM-BT-{i:02d}"
        returns: list[tuple[str, str]] = []
        for j in range(n_fit):
            returns.append((f"CUST-BT-{i:02d}-F{j}", "too_small"))
        for j in range(n_other):
            returns.append((f"CUST-BT-{i:02d}-O{j}", "other"))
        _insert_item_with_returns(test_db, item_id, category, returns)

        # Compute what the per-item share should be (unweighted shortcut:
        # since all customers have loyalty=50, weight is constant at 1.0)
        total = n_fit + n_other
        if total:
            per_item_shares.append(
                {"too_small": n_fit / total, "other": n_other / total}
            )

    # Manually compute expected baseline
    expected: dict[str, float] = {}
    for code in codes:
        vals = [s.get(code, 0.0) for s in per_item_shares]
        expected[code] = statistics.median(vals)
    total_exp = sum(expected.values())
    if total_exp > 0:
        expected = {k: v / total_exp for k, v in expected.items()}

    baseline = compute_category_baseline(category, test_db)

    assert baseline.get("scope") == "category", (
        "With 15 qualifying items scope must be 'category'"
    )

    for code in codes:
        got = float(baseline.get(code, 0.0))
        exp = expected[code]
        assert abs(got - exp) < 0.01, (
            f"Baseline for {code!r}: expected ≈{exp:.4f}, got {got:.4f}"
        )


def test_baseline_fallback_to_global(test_db: Session) -> None:
    """
    A category with fewer than 10 qualifying items must fall back to the
    global baseline (scope == 'global').
    """
    # Insert only 5 items with 30+ returns in a sparse category
    category = "sparse-cat"
    for i in range(5):
        iid = f"ITEM-SPARSE-{i}"
        returns = [(f"CUST-SP-{i}-{j}", "too_small") for j in range(35)]
        _insert_item_with_returns(test_db, iid, category, returns)

    baseline = compute_category_baseline(category, test_db)
    assert baseline.get("scope") == "global", (
        "Category with < 10 qualifying items must fall back to global scope"
    )


# ---------------------------------------------------------------------------
# Criterion 3 — Anomaly detection
# ---------------------------------------------------------------------------


def test_flagging_with_sufficient_n(test_db: Session) -> None:
    """
    Synthetic item with 80 % 'defect' returns in a category where the
    baseline for 'defect' is near zero (the conftest boots data has no
    defect returns).  With n=100 the binomial test should flag 'defect'
    as significant (excess ≫ FLAG_MIN_EXCESS=0.15, ratio ≫ 1.5).
    """
    item_id = "ITEM-FLAG-TEST"
    category = "boots"

    # Seed a realistic category baseline: 10+ qualifying peer items with NO
    # defect returns, so the per-item-median category baseline for 'defect' is
    # ~0 (the fallback global baseline over a near-empty test DB would instead
    # be dominated by this single high-volume item, which never happens against
    # the 650k-row production dataset the thresholds were tuned for).
    for i in range(12):
        peer = f"ITEM-FL-PEER-{i:02d}"
        peer_returns = [(f"CUST-FL-P{i:02d}-{j}", "too_small") for j in range(35)]
        _insert_item_with_returns(test_db, peer, category, peer_returns)

    # 100 unique customers; 80 defect, 20 other
    returns: list[tuple[str, str]] = []
    for j in range(80):
        returns.append((f"CUST-FL-D{j}", "defect"))
    for j in range(20):
        returns.append((f"CUST-FL-O{j}", "other"))

    _insert_item_with_returns(test_db, item_id, category, returns)

    flags = compute_anomaly(item_id, test_db)
    sig_codes = {f.reason_code for f in flags if f.significant}

    assert "defect" in sig_codes, (
        "Expected 'defect' to be a significant flag for item with 80% defect "
        f"returns vs near-zero baseline. "
        f"All flags: {[(f.reason_code, round(f.item_share,2), round(f.baseline_share,2), round(f.p_adj,4), f.significant) for f in flags]}"
    )


def test_no_flag_with_insufficient_n(test_db: Session) -> None:
    """
    Same distribution but only 10 returns — compute_anomaly must return an
    empty list (MIN_RETURNS_FLAG=30 and MIN_CUSTOMERS_FLAG=20).
    """
    item_id = "ITEM-SMALL-N"
    category = "boots"

    returns = [(f"CUST-SN-{j}", "defect") for j in range(6)]
    returns += [(f"CUST-SN-O{j}", "other") for j in range(4)]

    _insert_item_with_returns(test_db, item_id, category, returns)

    flags = compute_anomaly(item_id, test_db)
    assert flags == [], (
        f"Expected no flags for n=10, got: {flags}"
    )
