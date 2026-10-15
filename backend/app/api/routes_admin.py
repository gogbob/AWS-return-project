"""
routes_admin.py — Admin-facing API routes for the ALDO Returns Detective Agent.

Admin routes MAY include loyalty scores in case details (admin console only).
All responses still NEVER include raw suspicion/triage scores in list views.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..insights.engine import (
    compute_anomaly,
    compute_category_baseline,
    compute_global_baseline,
    compute_item_distribution,
    compute_js_distance,
    recompute_category_baselines,
)

# Import the customer list so we can derive "Customer #N" labels
from .routes_customer import _DEMO_CUSTOMERS, CUSTOMER_LABEL_MAP

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ---------------------------------------------------------------------------
# Helper — map a customer_id to its "Customer #N" label
# ---------------------------------------------------------------------------


def _customer_label(customer_id: Optional[str]) -> str:
    if customer_id and customer_id in CUSTOMER_LABEL_MAP:
        n = CUSTOMER_LABEL_MAP[customer_id]
        return f"Customer #{n}"
    return customer_id or "Unknown"


# ---------------------------------------------------------------------------
# 1. GET /api/admin/cases
# ---------------------------------------------------------------------------


@router.get("/cases")
def list_admin_cases(db: Session = Depends(get_db)):
    """
    Return cases in HUMAN_REVIEW or stuck VISION_REVIEW state.
    customer_label = "Customer #N"
    """
    rows = db.execute(
        text(
            """
            SELECT
                c.id,
                c.customer_id,
                oi.orderitem_product_name,
                c.reason_code,
                c.status,
                c.created_at
            FROM cases c
            LEFT JOIN order_items oi ON oi.item_id = c.item_id
            WHERE c.status IN ('HUMAN_REVIEW', 'VISION_REVIEW')
            ORDER BY c.created_at DESC
            """
        )
    ).fetchall()

    return [
        {
            "case_id": row[0],
            "customer_label": _customer_label(row[1]),
            "item_name": row[2],
            "reason_code": row[3],
            "status": row[4],
            "created_at": row[5],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 2. GET /api/admin/cases/{id}
# ---------------------------------------------------------------------------


@router.get("/cases/{case_id}")
def get_admin_case(case_id: str, db: Session = Depends(get_db)):
    """Full case detail including loyalty (admin console only)."""
    case_row = db.execute(
        text(
            """
            SELECT
                c.id,
                c.customer_id,
                c.order_number,
                c.sku,
                c.item_id,
                c.reason_code,
                c.detail,
                c.status,
                c.triage_outcome,
                c.triage_breakdown_json,
                c.suspicion_score,
                c.decision_note,
                c.created_at,
                oi.orderitem_product_name,
                cust.loyalty
            FROM cases c
            LEFT JOIN order_items oi ON oi.item_id = c.item_id
            LEFT JOIN customers cust ON cust.customer_id = c.customer_id
            WHERE c.id = :cid
            """
        ),
        {"cid": case_id},
    ).fetchone()

    if not case_row:
        raise HTTPException(status_code=404, detail="Case not found")

    (
        cid, customer_id, order_number, sku, item_id, reason_code, detail,
        status, triage_outcome, breakdown_json, suspicion_score, decision_note,
        created_at, item_name, loyalty,
    ) = case_row

    triage_breakdown = None
    if breakdown_json:
        try:
            triage_breakdown = json.loads(breakdown_json)
        except (json.JSONDecodeError, TypeError):
            pass

    # Messages (full transcript)
    msg_rows = db.execute(
        text(
            "SELECT role, content, created_at FROM case_messages "
            "WHERE case_id = :cid ORDER BY id ASC"
        ),
        {"cid": case_id},
    ).fetchall()
    messages = [
        {"role": r[0], "content": r[1], "created_at": r[2]} for r in msg_rows
    ]

    # Evidence (image paths + vision verdicts)
    ev_rows = db.execute(
        text(
            "SELECT file_path, vision_verdict_json, created_at "
            "FROM case_evidence WHERE case_id = :cid ORDER BY id ASC"
        ),
        {"cid": case_id},
    ).fetchall()
    evidence = []
    for ev in ev_rows:
        verdict = None
        if ev[1]:
            try:
                verdict = json.loads(ev[1])
            except (json.JSONDecodeError, TypeError):
                pass
        evidence.append(
            {"file_path": ev[0], "vision_verdict": verdict, "created_at": ev[2]}
        )

    return {
        "case_id": cid,
        "customer_label": _customer_label(customer_id),
        "item_name": item_name,
        "item_id": item_id,
        "order_number": order_number,
        "sku": sku,
        "reason_code": reason_code,
        "detail": detail,
        "status": status,
        "triage_outcome": triage_outcome,
        "triage_breakdown": triage_breakdown,
        "suspicion_score": suspicion_score,
        "decision_note": decision_note,
        "created_at": created_at,
        "loyalty": loyalty,  # admin-only field
        "messages": messages,
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# 3. POST /api/admin/cases/{id}/decision
# ---------------------------------------------------------------------------


_VALID_ACTIONS = {
    "approve_no_fee",
    "approve_with_fee",
    "reject",
    "mark_fraud",
    "release",
}

_ACTION_TO_STATUS: dict[str, str] = {
    "approve_no_fee": "APPROVED_NO_FEE",
    "approve_with_fee": "APPROVED_WITH_FEE",
    "reject": "REJECTED",
    "mark_fraud": "REJECTED",
    "release": "PENDING",
}


class AdminDecisionRequest(BaseModel):
    action: str
    note: Optional[str] = None


@router.post("/cases/{case_id}/decision")
def admin_decision(
    case_id: str, body: AdminDecisionRequest, db: Session = Depends(get_db)
):
    """
    Apply a human decision to a case.

    Spec §8.5 evidence rule updates:
      - mark_fraud: sets fraud_flag=True on the customer and on the return row.
      - reject: no evidence rule change.
      - approve_*: clear any fraud_flag the case introduced.
    """
    if body.action not in _VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action {body.action!r}. Must be one of: {sorted(_VALID_ACTIONS)}",
        )

    case_row = db.execute(
        text(
            "SELECT customer_id, order_number, sku, item_id FROM cases WHERE id = :cid"
        ),
        {"cid": case_id},
    ).fetchone()
    if not case_row:
        raise HTTPException(status_code=404, detail="Case not found")

    customer_id, order_number, sku, item_id = case_row
    new_status = _ACTION_TO_STATUS.get(body.action, "HUMAN_REVIEW")

    # Update case
    db.execute(
        text(
            "UPDATE cases SET status = :status, decision_note = :note, "
            "decided_by = 'admin', decided_at = :now WHERE id = :cid"
        ),
        {
            "status": new_status,
            "note": body.note,
            "now": datetime.utcnow(),
            "cid": case_id,
        },
    )

    # §8.5 evidence rule updates
    if body.action == "mark_fraud":
        # Flag the customer
        if customer_id:
            db.execute(
                text("UPDATE customers SET fraud_flag = 1 WHERE customer_id = :cid"),
                {"cid": customer_id},
            )
        # Flag the return row(s)
        if order_number and sku:
            db.execute(
                text(
                    "UPDATE returns SET fraud_flag = 1 "
                    "WHERE order_number = :on AND sku = :sku"
                ),
                {"on": order_number, "sku": sku},
            )

    elif body.action in ("approve_no_fee", "approve_with_fee"):
        # Ensure return record exists (may have been created by finalize_return tool)
        existing = db.execute(
            text(
                "SELECT id FROM returns "
                "WHERE order_number = :on AND sku = :sku AND source = 'live' "
                "LIMIT 1"
            ),
            {"on": order_number, "sku": sku},
        ).fetchone()
        if not existing and order_number and sku:
            import uuid as _uuid

            db.execute(
                text(
                    "INSERT INTO returns "
                    "(order_number, sku, item_id, customer_uid, return_order_number, "
                    " return_creation_date, last_return_status, reason_code, source, fraud_flag) "
                    "VALUES (:on, :sku, :iid, :cid, :ron, :now, 'RETURN_CREATED', "
                    "        NULL, 'live', 0)"
                ),
                {
                    "on": order_number,
                    "sku": sku,
                    "iid": item_id,
                    "cid": customer_id,
                    "ron": f"RTN-{_uuid.uuid4().hex[:10].upper()}",
                    "now": datetime.utcnow(),
                },
            )

    db.commit()

    return {"success": True, "case_id": case_id, "new_status": new_status}


# ---------------------------------------------------------------------------
# 4. GET /api/admin/insights
# ---------------------------------------------------------------------------


@router.get("/insights")
def list_insights(
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Return items ranked by Jensen-Shannon distance, optionally filtered by category.
    """
    if category:
        rows = db.execute(
            text(
                "SELECT item_id, orderitem_product_name, category "
                "FROM order_items WHERE category = :cat"
            ),
            {"cat": category},
        ).fetchall()
    else:
        rows = db.execute(
            text("SELECT item_id, orderitem_product_name, category FROM order_items")
        ).fetchall()

    results = []
    for row in rows:
        item_id, name, cat = row
        try:
            item_dist = compute_item_distribution(item_id, db)
            n = item_dist.get("n", 0)
            n_eff = item_dist.get("n_eff", 0.0)

            if n == 0:
                continue  # skip items with no returns

            if cat:
                baseline = compute_category_baseline(cat, db)
            else:
                baseline = compute_global_baseline(db)

            js_dist = compute_js_distance(item_dist, baseline)

            flags_raw = compute_anomaly(item_id, db)
            flags = [f.reason_code for f in flags_raw if f.significant]

            results.append(
                {
                    "item_id": item_id,
                    "name": name,
                    "category": cat,
                    "n": n,
                    "n_eff": round(n_eff, 2),
                    "js_distance": round(js_dist, 4),
                    "flags": flags,
                }
            )
        except Exception:
            continue

    results.sort(key=lambda x: x["js_distance"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# 5. GET /api/admin/insights/{item_id}
# ---------------------------------------------------------------------------


@router.get("/insights/{item_id}")
def get_insight_detail(item_id: str, db: Session = Depends(get_db)):
    """Full insight detail for one item."""
    item_row = db.execute(
        text(
            "SELECT item_id, orderitem_product_name, category FROM order_items WHERE item_id = :iid"
        ),
        {"iid": item_id},
    ).fetchone()
    if not item_row:
        raise HTTPException(status_code=404, detail="Item not found")

    _, name, category = item_row

    item_dist = compute_item_distribution(item_id, db)

    if category:
        baseline = compute_category_baseline(category, db)
        baseline_scope = baseline.get("scope", "category")
    else:
        baseline = compute_global_baseline(db)
        baseline_scope = "global"

    flags_raw = compute_anomaly(item_id, db)
    flags = [
        {
            "reason_code": f.reason_code,
            "group": f.group,
            "item_share": round(f.item_share, 4),
            "baseline_share": round(f.baseline_share, 4),
            "p_value": round(f.p_value, 6),
            "p_adj": round(f.p_adj, 6),
            "significant": f.significant,
            "excess": round(f.excess, 4),
            "ratio": round(f.ratio, 4),
        }
        for f in flags_raw
    ]

    # Separate p_values for summary
    p_values = {f["reason_code"]: f["p_adj"] for f in flags}

    # Strip metadata from distributions
    _meta = {"n", "n_eff", "scope", "n_items"}
    dist_clean = {k: round(v, 4) for k, v in item_dist.items() if k not in _meta}
    base_clean = {
        k: round(float(v), 4)
        for k, v in baseline.items()
        if k not in _meta and isinstance(v, (int, float))
    }

    return {
        "item_id": item_id,
        "name": name,
        "category": category,
        "n": item_dist.get("n", 0),
        "n_eff": round(float(item_dist.get("n_eff", 0.0)), 2),
        "distribution": dist_clean,
        "baseline": base_clean,
        "baseline_scope": baseline_scope,
        "flags": flags,
        "p_values": p_values,
    }


# ---------------------------------------------------------------------------
# 6. GET /api/admin/recommendations
# ---------------------------------------------------------------------------


@router.get("/recommendations")
def list_recommendations(db: Session = Depends(get_db)):
    """Return all recommendations."""
    rows = db.execute(
        text(
            """
            SELECT
                rec.id,
                rec.item_id,
                oi.orderitem_product_name,
                rec.recommendation_type,
                rec.reason_code,
                rec.draft_text,
                rec.confidence,
                rec.status
            FROM recommendations rec
            LEFT JOIN order_items oi ON oi.item_id = rec.item_id
            ORDER BY rec.confidence DESC, rec.created_at DESC
            """
        )
    ).fetchall()

    return [
        {
            "id": row[0],
            "item_id": row[1],
            "name": row[2],
            "recommendation_type": row[3],
            "reason_code": row[4],
            "draft_text": row[5],
            "confidence": row[6],
            "status": row[7],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 7. POST /api/admin/recommendations/{id}/decision
# ---------------------------------------------------------------------------


class RecommendationDecisionRequest(BaseModel):
    action: str  # "approve" | "reject" | "rollback"


_REC_VALID_ACTIONS = {"approve", "reject", "rollback"}

_REC_ACTION_TO_STATUS: dict[str, str] = {
    "approve": "applied",
    "reject": "rejected",
    "rollback": "rolled_back",
}


@router.post("/recommendations/{rec_id}/decision")
def recommendation_decision(
    rec_id: int,
    body: RecommendationDecisionRequest,
    db: Session = Depends(get_db),
):
    """
    Apply admin decision to a recommendation.
    - approve: sets status=applied (applies size guidance)
    - reject:  sets status=rejected
    - rollback: sets status=rolled_back
    """
    if body.action not in _REC_VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action {body.action!r}. Must be one of: {sorted(_REC_VALID_ACTIONS)}",
        )

    rec_row = db.execute(
        text("SELECT id, item_id, status FROM recommendations WHERE id = :rid"),
        {"rid": rec_id},
    ).fetchone()
    if not rec_row:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    new_status = _REC_ACTION_TO_STATUS[body.action]

    db.execute(
        text(
            "UPDATE recommendations SET status = :status, updated_at = :now WHERE id = :rid"
        ),
        {"status": new_status, "now": datetime.utcnow(), "rid": rec_id},
    )
    db.commit()

    return {"success": True, "id": rec_id, "new_status": new_status}


# ---------------------------------------------------------------------------
# 8. GET /api/admin/data-health
# ---------------------------------------------------------------------------


@router.get("/data-health")
def data_health(db: Session = Depends(get_db)):
    """Return last ingest time, row counts, and unmapped reasons."""
    # Row counts for all relevant tables
    table_names = [
        "returns",
        "order_items",
        "customers",
        "stores",
        "cases",
        "case_messages",
        "case_evidence",
        "recommendations",
        "agent_changes",
        "unmapped_reasons",
        "returnable_flags",
        "restocking_fees",
        "exchanges",
    ]
    row_counts: dict[str, int] = {}
    for table in table_names:
        try:
            count = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0  # noqa: S608
            row_counts[table] = int(count)
        except Exception:
            row_counts[table] = -1  # table may not exist yet

    # Last ingest time from returns table
    last_row = db.execute(
        text(
            "SELECT MAX(return_creation_date) FROM returns WHERE source = 'csv'"
        )
    ).fetchone()
    last_ingest_time = last_row[0] if last_row and last_row[0] else None

    # Unmapped reasons
    unmapped_rows = db.execute(
        text("SELECT raw_value, count FROM unmapped_reasons ORDER BY count DESC LIMIT 50")
    ).fetchall()
    unmapped_reasons = [{"raw_value": r[0], "count": r[1]} for r in unmapped_rows]

    return {
        "last_ingest_time": last_ingest_time,
        "row_counts": row_counts,
        "unmapped_reasons": unmapped_reasons,
    }


# ---------------------------------------------------------------------------
# 9. POST /api/admin/recompute
# ---------------------------------------------------------------------------


@router.post("/recompute")
def trigger_recompute(db: Session = Depends(get_db)):
    """Trigger recompute_category_baselines()."""
    try:
        recompute_category_baselines(db)
        return {"success": True, "message": "Category baselines recomputed successfully."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
