"""
eligibility.py — Return eligibility checks for the ALDO Returns Detective Agent.

Validates whether a (customer, order, SKU) combination is eligible for return
before a case is created.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session


class EligibilityResult(BaseModel):
    eligible: bool
    reason: str          # human-readable message shown to the customer
    is_final_sale: bool


def check_eligibility(
    customer_id: str,
    order_number: str,
    sku: str,
    db: Session,
) -> EligibilityResult:
    """
    Determine whether a customer may return a specific SKU from an order.

    Rules (checked in order):
    1. The (order_number, sku) pair must appear in the customer's order history.
    2. The item must not be final-sale (is_final_sale = True is ineligible).
    3. No existing resolved return for (order_number, sku) may already exist.

    Returns an EligibilityResult with eligible=True on success, or
    eligible=False with a human-readable reason on failure.
    """

    # ── Rule 1: item belongs to the customer ─────────────────────────────────
    # The returns table holds OMS data where customer_uid == customer_id.
    # We look for any order-level row matching (customer_uid, order_number, sku).
    order_row = db.execute(
        text(
            "SELECT id FROM returns "
            "WHERE customer_uid = :cid "
            "  AND order_number = :order_number "
            "  AND sku = :sku "
            "LIMIT 1"
        ),
        {"cid": customer_id, "order_number": order_number, "sku": sku},
    ).fetchone()

    if order_row is None:
        return EligibilityResult(
            eligible=False,
            reason="We couldn't find that item in your order history. Please check your order number and try again.",
            is_final_sale=False,
        )

    # ── Rule 2: final-sale check ──────────────────────────────────────────────
    flag_row = db.execute(
        text(
            "SELECT is_final_sale FROM returnable_flags "
            "WHERE order_number = :order_number AND sku = :sku "
            "LIMIT 1"
        ),
        {"order_number": order_number, "sku": sku},
    ).fetchone()

    # Treat NULL / missing flag as not final-sale (conservative in customer's favour)
    is_final_sale = bool(flag_row[0]) if flag_row else False

    if is_final_sale:
        return EligibilityResult(
            eligible=False,
            reason=(
                "This item is final sale and can't be returned online. "
                "Contact customer service."
            ),
            is_final_sale=True,
        )

    # ── Rule 3: no duplicate return ───────────────────────────────────────────
    VALID_STATUSES = (
        "RETURN_COMPLETED",
        "RETURN_RECEIVED",
        "RETURN_PROCESSED",
        "RETURN_SHIPPING",
        "CREATED",
    )
    placeholders = ", ".join(f"'{s}'" for s in VALID_STATUSES)

    existing_return = db.execute(
        text(
            f"SELECT id FROM returns "
            f"WHERE order_number = :order_number "
            f"  AND sku = :sku "
            f"  AND last_return_status IN ({placeholders}) "
            f"  AND source != 'csv' "
            f"LIMIT 1"
        ),
        {"order_number": order_number, "sku": sku},
    ).fetchone()

    if existing_return is None:
        # Also check the cases table for an already-resolved return. We only
        # block on terminally-resolved cases (approved); a lingering PENDING
        # case is not a hard block, so a customer can restart an abandoned
        # return flow for the same item.
        existing_case = db.execute(
            text(
                "SELECT id FROM cases "
                "WHERE order_number = :order_number "
                "  AND sku = :sku "
                "  AND status IN ('APPROVED_NO_FEE', 'APPROVED_WITH_FEE') "
                "LIMIT 1"
            ),
            {"order_number": order_number, "sku": sku},
        ).fetchone()
        if existing_case:
            return EligibilityResult(
                eligible=False,
                reason="This item has already been returned.",
                is_final_sale=False,
            )

    return EligibilityResult(
        eligible=True,
        reason="This item is eligible for return.",
        is_final_sale=False,
    )
