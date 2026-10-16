"""
tools.py — Strands @tool functions for the ALDO Returns Detective Agent.

Exposes the dataset to the agent via well-typed tool functions. Internal
tools (triage, suspicion, loyalty) are for agent reasoning only and must
never surface their raw scores to the customer.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from rapidfuzz import fuzz
from sqlalchemy import text
from strands import tool

from ..db import SessionLocal
from ..insights.engine import compute_item_distribution, compute_category_baseline, compute_global_baseline
from ..loyalty.loyalty import get_loyalty as _get_loyalty
from ..policy.eligibility import check_eligibility as _check_eligibility
from ..policy.triage import compute_triage as _compute_triage


# ---------------------------------------------------------------------------
# 1. get_customer_orders
# ---------------------------------------------------------------------------


@tool
def get_customer_orders(customer_id: str) -> list[dict]:
    """Return all order items for a customer with their return status."""
    try:
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    """
                    SELECT
                        r.order_number,
                        r.sku,
                        oi.item_id,
                        oi.orderitem_product_name,
                        oi.category,
                        r.last_return_status,
                        COALESCE(rf.is_final_sale, 0) AS is_final_sale
                    FROM returns r
                    LEFT JOIN order_items oi ON oi.item_id = r.item_id
                    LEFT JOIN returnable_flags rf
                           ON rf.order_number = r.order_number AND rf.sku = r.sku
                    WHERE r.customer_uid = :cid
                    ORDER BY r.order_number, r.sku
                    """
                ),
                {"cid": customer_id},
            ).fetchall()

            return [
                {
                    "order_number": row[0],
                    "sku": row[1],
                    "item_id": row[2],
                    "orderitem_product_name": row[3],
                    "category": row[4],
                    "return_status": row[5],
                    "is_final_sale": bool(row[6]),
                }
                for row in rows
            ]
    except Exception as exc:
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# 2. find_order_item
# ---------------------------------------------------------------------------


@tool
def find_order_item(customer_id: str, item_name_query: str) -> list[dict]:
    """Fuzzy-match an item name against the customer's own order lines.
    Uses rapidfuzz for case-insensitive substring + typo tolerance.
    Returns up to 5 matches sorted by score."""
    try:
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    """
                    SELECT
                        r.order_number,
                        r.sku,
                        oi.item_id,
                        oi.orderitem_product_name
                    FROM returns r
                    LEFT JOIN order_items oi ON oi.item_id = r.item_id
                    WHERE r.customer_uid = :cid
                      AND oi.orderitem_product_name IS NOT NULL
                    """
                ),
                {"cid": customer_id},
            ).fetchall()

        # Deduplicate by (order_number, sku, item_id) before scoring
        seen: set[tuple] = set()
        candidates: list[dict] = []
        for row in rows:
            key = (row[0], row[1], row[2])
            if key not in seen:
                seen.add(key)
                candidates.append(
                    {
                        "order_number": row[0],
                        "sku": row[1],
                        "item_id": row[2],
                        "orderitem_product_name": row[3],
                    }
                )

        # Score each candidate
        query_lower = item_name_query.lower()
        scored: list[dict] = []
        for c in candidates:
            name = (c["orderitem_product_name"] or "").lower()
            score = fuzz.token_set_ratio(query_lower, name)
            if score >= 60:
                scored.append({**c, "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:5]

    except Exception as exc:
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# 3. check_eligibility
# ---------------------------------------------------------------------------


@tool
def check_eligibility(customer_id: str, order_number: str, sku: str) -> dict:
    """Check return eligibility. Returns {eligible, reason, is_final_sale}."""
    try:
        with SessionLocal() as db:
            result = _check_eligibility(customer_id, order_number, sku, db)
        return result.model_dump()
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# 4. get_item_reason_context
# ---------------------------------------------------------------------------


@tool
def get_item_reason_context(item_id: str, reason_code: str) -> dict:
    """Return the item's reason distribution, baseline distribution, and plausibility score.
    Used internally to inform triage — never shown to customer."""
    try:
        with SessionLocal() as db:
            item_dist = compute_item_distribution(item_id, db)

            # Determine category for this item
            cat_row = db.execute(
                text("SELECT category FROM order_items WHERE item_id = :iid"),
                {"iid": item_id},
            ).fetchone()
            category = cat_row[0] if cat_row else None

            if category:
                baseline = compute_category_baseline(category, db)
            else:
                baseline = compute_global_baseline(db)

            # Plausibility score: smoothed Laplace estimate of how common
            # this reason code is for the item relative to baseline.
            # smoothed = (count_ir + 20 * b_r) / (n_i + 20)
            # plausibility = clamp(smoothed / max(b_r, 0.05), 0, 1)
            n_i = item_dist.get("n", 0)
            b_r = float(baseline.get(reason_code, 0.0))
            item_share = float(item_dist.get(reason_code, 0.0))

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
            plausibility = max(0.0, min(1.0, smoothed / max(b_r, 0.05)))

        # Strip metadata keys from distributions for a clean response
        _meta = {"n", "n_eff", "scope", "n_items"}
        item_dist_clean = {k: v for k, v in item_dist.items() if k not in _meta}
        baseline_clean = {k: v for k, v in baseline.items() if k not in _meta}

        return {
            "item_distribution": item_dist_clean,
            "baseline_distribution": baseline_clean,
            "plausibility_score": round(plausibility, 4),
            "n": n_i,
            "baseline_scope": baseline.get("scope", "category"),
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# 5. get_loyalty
# ---------------------------------------------------------------------------


@tool
def get_loyalty(customer_id: str) -> int:
    """Placeholder: random for demo. Returns loyalty score 0-100 for internal triage use only.
    NEVER expose this value to the customer or include it in any customer-facing message."""
    try:
        with SessionLocal() as db:
            return _get_loyalty(customer_id, db)
    except Exception as exc:
        return {"error": str(exc)}  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 6. compute_triage
# ---------------------------------------------------------------------------


@tool
def compute_triage(case_id: str) -> dict:
    """Compute the triage outcome for a case. Returns {outcome, suspicion, breakdown, fee}.
    For internal use only — do not share suspicion, fraud, or risk scores with the customer."""
    try:
        with SessionLocal() as db:
            result = _compute_triage(case_id, db)
            # Persist triage result in cases table (already done inside _compute_triage via _persist)
        return result.model_dump()
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# 7. finalize_return
# ---------------------------------------------------------------------------

_VALID_DECISIONS = {"approved_no_fee", "approved_with_fee", "rejected"}


@tool
def finalize_return(case_id: str, decision: str) -> dict:
    """Finalize a return case. decision must be one of: approved_no_fee, approved_with_fee, rejected.
    Inserts the return into the returns table with source='live' and triggers insight recompute.
    Returns {success, return_order_number, fee, message}."""
    if decision not in _VALID_DECISIONS:
        return {
            "error": f"Invalid decision {decision!r}. Must be one of: {', '.join(sorted(_VALID_DECISIONS))}",
            "success": False,
        }

    try:
        from ..insights.engine import recompute_item  # local import avoids circular at module load

        with SessionLocal() as db:
            # Load case details
            case_row = db.execute(
                text(
                    "SELECT customer_id, order_number, sku, item_id, reason_code, triage_breakdown_json "
                    "FROM cases WHERE id = :cid"
                ),
                {"cid": case_id},
            ).fetchone()

            if case_row is None:
                return {"error": f"Case {case_id!r} not found", "success": False}

            customer_id, order_number, sku, item_id, reason_code, breakdown_json = case_row

            # Determine fee from triage breakdown
            fee = 0.0
            if breakdown_json:
                try:
                    bd = json.loads(breakdown_json)
                    fee = float(bd.get("fee", 0.0))
                except (json.JSONDecodeError, TypeError, ValueError):
                    fee = 0.0

            if decision == "rejected":
                fee = 0.0

            # Generate return order number
            return_order_number = f"RTN-{uuid.uuid4().hex[:10].upper()}"

            # Resolve refund amount
            refund_row = db.execute(
                text(
                    "SELECT refund_amount FROM returns "
                    "WHERE order_number = :on AND sku = :sku "
                    "ORDER BY return_creation_date DESC LIMIT 1"
                ),
                {"on": order_number, "sku": sku},
            ).fetchone()
            refund_amount = float(refund_row[0]) if refund_row and refund_row[0] is not None else None

            if decision != "rejected":
                # Insert live return record
                db.execute(
                    text(
                        "INSERT INTO returns "
                        "(order_number, sku, item_id, customer_uid, return_order_number, "
                        " return_creation_date, last_return_status, refund_amount, "
                        " reason_code, source, fraud_flag) "
                        "VALUES (:order_number, :sku, :item_id, :customer_uid, :ron, "
                        " :created_at, 'RETURN_CREATED', :refund_amount, "
                        " :reason_code, 'live', 0)"
                    ),
                    {
                        "order_number": order_number,
                        "sku": sku,
                        "item_id": item_id,
                        "customer_uid": customer_id,
                        "ron": return_order_number,
                        "created_at": datetime.utcnow(),
                        "refund_amount": refund_amount,
                        "reason_code": reason_code,
                    },
                )

            # Update case status
            final_status = decision  # 'approved_no_fee' | 'approved_with_fee' | 'rejected'
            db.execute(
                text(
                    "UPDATE cases SET status = :status, decided_at = :now "
                    "WHERE id = :cid"
                ),
                {"status": final_status, "cid": case_id, "now": datetime.utcnow()},
            )
            db.commit()

            # Trigger insight recompute for the item
            if item_id and decision != "rejected":
                recompute_item(item_id, db)

        if decision == "approved_no_fee":
            message = f"Your return has been approved. Return order {return_order_number} created with no restocking fee."
        elif decision == "approved_with_fee":
            message = f"Your return has been approved. Return order {return_order_number} created. A ${fee:.2f} CAD restocking fee will apply."
        else:
            message = "Your return request has been reviewed and could not be approved at this time. Please contact customer service if you have questions."
            return_order_number = None

        return {
            "success": True,
            "return_order_number": return_order_number,
            "fee": fee if decision != "rejected" else 0.0,
            "message": message,
        }

    except Exception as exc:
        return {"error": str(exc), "success": False}


# ---------------------------------------------------------------------------
# 8. escalate_to_vision_review
# ---------------------------------------------------------------------------


@tool
def escalate_to_vision_review(case_id: str) -> dict:
    """Escalate a case to vision review. Updates case status to VISION_REVIEW.
    Returns {success, message} — message is safe to relay to customer."""
    try:
        with SessionLocal() as db:
            result = db.execute(
                text("UPDATE cases SET status = 'VISION_REVIEW' WHERE id = :cid"),
                {"cid": case_id},
            )
            if result.rowcount == 0:
                return {"success": False, "error": f"Case {case_id!r} not found"}
            db.commit()

        return {
            "success": True,
            "message": (
                "To complete your return, please upload a photo of the shoe "
                "against a plain background so we can evaluate the size and fit. "
                "You can use the upload button below."
            ),
        }
    except Exception as exc:
        return {"error": str(exc), "success": False}


# ---------------------------------------------------------------------------
# 9. escalate_to_human
# ---------------------------------------------------------------------------


@tool
def escalate_to_human(case_id: str, reason: str) -> dict:
    """Escalate a case to human review. Updates case status to HUMAN_REVIEW.
    Returns {success, message} — message is safe to relay to customer."""
    try:
        with SessionLocal() as db:
            result = db.execute(
                text(
                    "UPDATE cases SET status = 'HUMAN_REVIEW', decision_note = :reason "
                    "WHERE id = :cid"
                ),
                {"cid": case_id, "reason": reason},
            )
            if result.rowcount == 0:
                return {"success": False, "error": f"Case {case_id!r} not found"}
            db.commit()

        return {
            "success": True,
            "message": (
                "Your return has been accepted and you can send the item back. "
                "Because of your recent return activity, a team member will review "
                "this return before your refund is finalized — this helps us keep "
                "returns fair for everyone. You'll get an update within 1–2 business days."
            ),
        }
    except Exception as exc:
        return {"error": str(exc), "success": False}
