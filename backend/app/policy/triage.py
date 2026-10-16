"""
triage.py — Triage scoring engine for the ALDO Returns Detective Agent.

Implements spec section 8.2: composite suspicion score → triage outcome.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings, CLAIM_RISK_BY_GROUP, TRIAGE_WEIGHTS
from ..loyalty.loyalty import get_loyalty
from ..insights.engine import compute_item_distribution, code_to_group


class TriageOutcome(str, Enum):
    APPROVED_NO_FEE = "APPROVED_NO_FEE"
    APPROVED_WITH_FEE = "APPROVED_WITH_FEE"
    VISION_REVIEW = "VISION_REVIEW"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class TriageResult(BaseModel):
    outcome: TriageOutcome
    suspicion: float
    breakdown: dict   # {loyalty_component, implausibility_component, claim_risk_component, value_risk_component}
    fee: float        # 0.0 or settings.shipping_fee_cad


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def compute_triage(case_id: str, db: Session) -> TriageResult:
    """
    Compute the triage outcome for an open case.

    Formula (spec §8.2):
      suspicion = 0.40*(1 - loyalty/100)
                + 0.30*implausibility
                + 0.20*claim_risk
                + 0.10*value_risk

    Outcomes:
      loyalty >= HIGH_LOYALTY_THRESHOLD          → APPROVED_NO_FEE
      suspicion < SUSPICION_THRESHOLD            → APPROVED_WITH_FEE
      suspicion >= SUSPICION_THRESHOLD           → VISION_REVIEW

    The triage breakdown and suspicion score are persisted to cases.
    """

    # ── Load case ─────────────────────────────────────────────────────────────
    # Note: the `cases` table has no refund_amount column; it is resolved from
    # the `returns` table below.
    case_row = db.execute(
        text(
            "SELECT customer_id, order_number, sku, item_id, reason_code "
            "FROM cases WHERE id = :cid"
        ),
        {"cid": case_id},
    ).fetchone()

    if case_row is None:
        raise ValueError(f"Case {case_id!r} not found")

    customer_id, order_number, sku, item_id, reason_code = case_row
    refund_amount = None

    # ── Step 0: resolve refund_amount from the returns table ──────────────────
    if refund_amount is None and order_number and sku:
        amt_row = db.execute(
            text(
                "SELECT refund_amount FROM returns "
                "WHERE order_number = :on AND sku = :sku "
                "ORDER BY return_creation_date DESC LIMIT 1"
            ),
            {"on": order_number, "sku": sku},
        ).fetchone()
        refund_amount = float(amt_row[0]) if amt_row and amt_row[0] is not None else None

    # ── Step 1: loyalty fast-path ──────────────────────────────────────────────
    loyalty = get_loyalty(customer_id, db) if customer_id else 0

    if loyalty >= settings.high_loyalty_threshold:
        breakdown = {
            "loyalty_component": 0.0,
            "implausibility_component": 0.0,
            "claim_risk_component": 0.0,
            "value_risk_component": 0.0,
        }
        _persist(case_id, TriageOutcome.APPROVED_NO_FEE, 0.0, breakdown, 0.0, db)
        return TriageResult(
            outcome=TriageOutcome.APPROVED_NO_FEE,
            suspicion=0.0,
            breakdown=breakdown,
            fee=0.0,
        )

    # ── Step 2: implausibility ─────────────────────────────────────────────────
    # smoothed = (count_ir + 20 * b_r) / (n_i + 20)
    # implausibility = clamp(1 - smoothed / max(b_r, 0.05), 0, 1)

    if item_id and reason_code:
        item_dist = compute_item_distribution(item_id, db)
        n_i = item_dist.get("n", 0)
        b_r = float(item_dist.get(reason_code, 0.0))

        # count_ir: number of (item, reason_code) observations in evidence
        count_ir_row = db.execute(
            text(
                "SELECT COUNT(*) FROM returns "
                "WHERE item_id = :iid AND reason_code = :rc "
                "  AND last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED') "
                "  AND (fraud_flag = 0 OR fraud_flag IS NULL)"
            ),
            {"iid": item_id, "rc": reason_code},
        ).fetchone()
        count_ir = int(count_ir_row[0]) if count_ir_row else 0

        smoothed = (count_ir + 20 * b_r) / (n_i + 20) if (n_i + 20) > 0 else b_r
        implausibility = _clamp(1.0 - smoothed / max(b_r, 0.05))
    else:
        implausibility = 0.5   # neutral fallback

    # ── Step 3: claim risk ────────────────────────────────────────────────────
    if reason_code:
        group = code_to_group(reason_code)
        claim_risk = CLAIM_RISK_BY_GROUP.get(group, CLAIM_RISK_BY_GROUP.get("OTHER", 0.5))
    else:
        claim_risk = 0.3   # conservative neutral fallback

    # ── Step 4: value risk ────────────────────────────────────────────────────
    if refund_amount is not None:
        value_risk = _clamp(float(refund_amount) / settings.high_value_cad)
    else:
        value_risk = 0.3   # fallback when refund amount unknown

    # ── Composite suspicion score ─────────────────────────────────────────────
    loyalty_component = TRIAGE_WEIGHTS["loyalty"] * (1.0 - loyalty / 100.0)
    implausibility_component = TRIAGE_WEIGHTS["implausibility"] * implausibility
    claim_risk_component = TRIAGE_WEIGHTS["claim_risk"] * claim_risk
    value_risk_component = TRIAGE_WEIGHTS["value_risk"] * value_risk

    suspicion = (
        loyalty_component
        + implausibility_component
        + claim_risk_component
        + value_risk_component
    )

    breakdown = {
        "loyalty_component": round(loyalty_component, 4),
        "implausibility_component": round(implausibility_component, 4),
        "claim_risk_component": round(claim_risk_component, 4),
        "value_risk_component": round(value_risk_component, 4),
    }

    # ── Step 5: routing ───────────────────────────────────────────────────────
    # Weak purchase history (below low_history_threshold) always goes through
    # the photo/vision flow as friction — the customer is asked for photos of
    # the shoe to "evaluate size" before the return is accepted (with a fee)
    # and queued for human review. Otherwise, low suspicion auto-approves.
    if loyalty < settings.low_history_threshold:
        outcome = TriageOutcome.VISION_REVIEW
        fee = 0.0  # fee decided after the photo-friction flow completes
    elif suspicion < settings.suspicion_threshold:
        outcome = TriageOutcome.APPROVED_WITH_FEE
        fee = settings.shipping_fee_cad
    else:
        outcome = TriageOutcome.VISION_REVIEW
        fee = 0.0   # fee decision deferred to vision / human review

    _persist(case_id, outcome, suspicion, breakdown, fee, db)

    return TriageResult(
        outcome=outcome,
        suspicion=round(suspicion, 4),
        breakdown=breakdown,
        fee=fee,
    )


def _persist(
    case_id: str,
    outcome: TriageOutcome,
    suspicion: float,
    breakdown: dict,
    fee: float,
    db: Session,
) -> None:
    """Persist triage result to the cases table."""
    db.execute(
        text(
            "UPDATE cases SET "
            "triage_outcome = :outcome, "
            "triage_breakdown_json = :breakdown, "
            "suspicion_score = :suspicion "
            "WHERE id = :cid"
        ),
        {
            "outcome": outcome.value,
            "breakdown": json.dumps({**breakdown, "fee": fee}),
            "suspicion": suspicion,
            "cid": case_id,
        },
    )
    db.commit()
