"""
test_triage.py — Tests for acceptance criterion 5.

Criterion 5: Triage routing rules.
  - High-loyalty customer → APPROVED_NO_FEE regardless of reason.
  - Low-loyalty customer + common reason → APPROVED_WITH_FEE.
  - Low-loyalty customer + defect reason → VISION_REVIEW.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.policy.triage import TriageOutcome, compute_triage

from tests.conftest import make_case


# ---------------------------------------------------------------------------
# Criterion 5a — High loyalty → APPROVED_NO_FEE
# ---------------------------------------------------------------------------


def test_high_loyalty_always_approved_no_fee(test_db: Session) -> None:
    """
    CUST-HIGH has loyalty=85 (above high_loyalty_threshold=70).
    Any reason code must yield APPROVED_NO_FEE with zero fee.
    """
    case_id = make_case(
        test_db,
        customer_id="CUST-HIGH",
        order_number="ORD-HIGH-001",
        sku="SKU-001",
        item_id="ITEM-BOOTS",
        reason_code="too_small",
    )

    result = compute_triage(case_id, test_db)

    assert result.outcome == TriageOutcome.APPROVED_NO_FEE, (
        f"Expected APPROVED_NO_FEE for high-loyalty customer, got {result.outcome}"
    )
    assert result.fee == 0.0, (
        f"Expected fee=0.0 for high-loyalty fast-path, got fee={result.fee}"
    )


def test_high_loyalty_approved_no_fee_defect_reason(test_db: Session) -> None:
    """Even a DEFECT reason code for a high-loyalty customer yields APPROVED_NO_FEE."""
    case_id = make_case(
        test_db,
        customer_id="CUST-HIGH",
        reason_code="defect",
    )

    result = compute_triage(case_id, test_db)

    assert result.outcome == TriageOutcome.APPROVED_NO_FEE, (
        f"High-loyalty customer with DEFECT reason must still get APPROVED_NO_FEE, "
        f"got {result.outcome}"
    )


# ---------------------------------------------------------------------------
# Criterion 5b — Low loyalty + common reason → APPROVED_WITH_FEE
# ---------------------------------------------------------------------------


def test_low_loyalty_common_reason_approved_with_fee(test_db: Session) -> None:
    """
    CUST-LOW has loyalty=20.  too_small is a common reason for ITEM-BOOTS
    (the conftest seeds 30/40 returns as too_small) → implausibility is low →
    composite suspicion should fall below the 0.50 threshold.

    The triage formula:
      suspicion = 0.40*(1 - 20/100)       = 0.32   (loyalty component)
               + 0.30*implausibility       ≈ 0      (very common reason)
               + 0.20*0.3                  = 0.06   (FIT_SIZE claim_risk)
               + 0.10*value_risk           ≈ 0.03   (low refund)
             ≈ 0.41  < 0.50  → APPROVED_WITH_FEE
    """
    case_id = make_case(
        test_db,
        customer_id="CUST-LOW",
        order_number="ORD-LOW-001",
        sku="SKU-001",
        item_id="ITEM-BOOTS",
        reason_code="too_small",
    )

    result = compute_triage(case_id, test_db)

    assert result.outcome == TriageOutcome.APPROVED_WITH_FEE, (
        f"Expected APPROVED_WITH_FEE for low-loyalty / common reason, "
        f"got {result.outcome} (suspicion={result.suspicion})"
    )


# ---------------------------------------------------------------------------
# Criterion 5c — Low loyalty + defect reason → VISION_REVIEW
# ---------------------------------------------------------------------------


def test_low_loyalty_defect_vision_review(test_db: Session) -> None:
    """
    CUST-LOW has loyalty=20.  'defect' carries claim_risk=1.0 (highest) so:
      suspicion = 0.40*(1 - 20/100)       = 0.32
               + 0.30*implausibility       ~ 0.30   (no defect history for item)
               + 0.20*1.0                  = 0.20
               + 0.10*value_risk           ≈ 0.03
             ≈ 0.85  > 0.50  → VISION_REVIEW
    """
    case_id = make_case(
        test_db,
        customer_id="CUST-LOW",
        order_number="ORD-LOW-001",
        sku="SKU-001",
        item_id="ITEM-BOOTS",
        reason_code="defect",
    )

    result = compute_triage(case_id, test_db)

    assert result.outcome == TriageOutcome.VISION_REVIEW, (
        f"Expected VISION_REVIEW for low-loyalty + DEFECT reason, "
        f"got {result.outcome} (suspicion={result.suspicion})"
    )
