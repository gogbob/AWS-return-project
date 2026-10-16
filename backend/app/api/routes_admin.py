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

from .. import aws
from ..config import settings
from ..db import get_db
from ..insights.engine import (
    code_to_group,
    compute_anomaly,
    compute_category_baseline,
    compute_global_baseline,
    compute_item_distribution,
    compute_js_distance,
    recompute_category_baselines,
)
from ..insights.sources import last_athena_query_id, select_baseline_source

# Import the customer list so we can derive display labels for cases.
from .routes_customer import _DEMO_CUSTOMERS, CUSTOMER_LABEL_MAP

# Cases store the resolved customer_uid; map uid -> friendly label for admin.
_UID_TO_LABEL: dict[str, str] = {c["uid"]: c["label"] for c in _DEMO_CUSTOMERS}

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def _append_ai_log(case_id: str, content: str, db: Session) -> None:
    """Append a line to the AI activity log for a case (role='ai_log')."""
    db.execute(
        text(
            "INSERT INTO case_messages (case_id, role, content, created_at) "
            "VALUES (:cid, 'ai_log', :content, :now)"
        ),
        {"cid": case_id, "content": content, "now": datetime.utcnow()},
    )
    db.commit()


# ---------------------------------------------------------------------------
# Helper — map a customer_id to its "Customer #N" label
# ---------------------------------------------------------------------------


def _customer_label(customer_id: Optional[str]) -> str:
    if not customer_id:
        return "Unknown"
    # Cases now store the real customer_uid.
    if customer_id in _UID_TO_LABEL:
        return _UID_TO_LABEL[customer_id]
    # Backwards-compat: older cases may hold the synthetic id.
    if customer_id in CUSTOMER_LABEL_MAP:
        n = CUSTOMER_LABEL_MAP[customer_id]
        return f"Customer #{n}"
    return customer_id


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

    # Record the admin's instruction to the AI in the activity log.
    _ACTION_LOG_TEXT = {
        "approve_no_fee": "👤 Admin instructed the AI: APPROVE with FREE shipping.",
        "approve_with_fee": "👤 Admin instructed the AI: APPROVE with a reduced-cost return (shipping fee).",
        "reject": "👤 Admin instructed the AI: REJECT this return.",
        "mark_fraud": "👤 Admin instructed the AI: REJECT and flag as fraud.",
        "release": "👤 Admin released the case back to the AI for processing.",
    }
    log_line = _ACTION_LOG_TEXT.get(body.action, f"👤 Admin action: {body.action}.")
    if body.note:
        log_line += f' Note to customer: "{body.note}"'
    _append_ai_log(case_id, log_line, db)

    return {"success": True, "case_id": case_id, "new_status": new_status}


# ---------------------------------------------------------------------------
# 3b. POST /api/admin/cases/{id}/stop — halt the AI for this case
# ---------------------------------------------------------------------------


@router.post("/cases/{case_id}/stop")
def admin_stop_case(case_id: str, db: Session = Depends(get_db)):
    """
    Tell the AI to stop processing a case. The customer-facing assistant will
    no longer respond for this case. (No human-chat takeover is implemented —
    the AI simply halts.)
    """
    case_row = db.execute(
        text("SELECT id, status FROM cases WHERE id = :cid"),
        {"cid": case_id},
    ).fetchone()
    if not case_row:
        raise HTTPException(status_code=404, detail="Case not found")

    db.execute(
        text(
            "UPDATE cases SET status = 'STOPPED', decided_by = 'admin', "
            "decided_at = :now WHERE id = :cid"
        ),
        {"now": datetime.utcnow(), "cid": case_id},
    )
    db.commit()

    _append_ai_log(
        case_id,
        "🛑 Admin told the AI to STOP dealing with this customer. "
        "The automated assistant has halted all processing for this case.",
        db,
    )

    return {"success": True, "case_id": case_id, "new_status": "STOPPED"}


# ---------------------------------------------------------------------------
# 3c. GET /api/admin/activity — AI activity log feed across cases
# ---------------------------------------------------------------------------


@router.get("/activity")
def admin_activity(
    case_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Return the AI activity log (role='ai_log'), newest first. Optionally filter
    to a single case. Each entry carries the case id, customer label, item, and
    the AI/admin log line.
    """
    params: dict = {}
    where = "cm.role = 'ai_log'"
    if case_id:
        where += " AND cm.case_id = :cid"
        params["cid"] = case_id

    rows = db.execute(
        text(
            f"""
            SELECT
                cm.case_id,
                cm.content,
                cm.created_at,
                c.customer_id,
                oi.orderitem_product_name,
                c.status
            FROM case_messages cm
            LEFT JOIN cases c ON c.id = cm.case_id
            LEFT JOIN order_items oi ON oi.item_id = c.item_id
            WHERE {where}
            ORDER BY cm.id DESC
            LIMIT 200
            """  # noqa: S608 - where clause is built from constants only
        ),
        params,
    ).fetchall()

    return [
        {
            "case_id": r[0],
            "content": r[1],
            "created_at": r[2],
            "customer_label": _customer_label(r[3]),
            "item_name": r[4],
            "status": r[5],
        }
        for r in rows
    ]


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
# 4b. GET /api/admin/item-insights
# ---------------------------------------------------------------------------

_INSIGHT_META_KEYS = {"n", "n_eff", "scope", "n_items"}


# Status values that count as a qualifying (resolved) return — must match the
# filter used by the insights engine (_WEIGHT_SQL in insights/engine.py).
_RESOLVED_STATUSES = ("RETURN_COMPLETED", "RETURN_RECEIVED", "RETURN_PROCESSED")


def _build_item_insight(item_id: str, name, category, db: Session) -> dict | None:
    """
    Compute the FULL return-reason analytics for a SINGLE item: item vs.
    category/global baseline distribution, anomaly flags, overshoot, the latest
    recommendation, and whether admin input is needed. Returns None if the item
    has no qualifying returns. This is the only place the heavy engine calls run,
    and it runs for one item at a time (lazily, when a row is expanded).
    """
    item_dist = compute_item_distribution(item_id, db)
    n = item_dist.get("n", 0)
    if n == 0:
        return None

    n_eff = item_dist.get("n_eff", 0.0)

    if category:
        baseline = compute_category_baseline(category, db)
    else:
        baseline = compute_global_baseline(db)

    item_shares = {
        k: float(v) for k, v in item_dist.items() if k not in _INSIGHT_META_KEYS
    }
    baseline_shares = {
        k: float(v)
        for k, v in baseline.items()
        if k not in _INSIGHT_META_KEYS and isinstance(v, (int, float))
    }
    codes = set(item_shares) | set(baseline_shares)

    flags_raw = compute_anomaly(item_id, db)
    flag_by_code = {f.reason_code: f for f in flags_raw}

    reasons = []
    for code in codes:
        item_share = item_shares.get(code, 0.0)
        baseline_share = baseline_shares.get(code, 0.0)
        flag = flag_by_code.get(code)
        if flag is not None:
            excess = flag.excess
            ratio = flag.ratio
            significant = flag.significant
        else:
            excess = item_share - baseline_share
            ratio = item_share / max(baseline_share, 1e-9)
            significant = False
        reasons.append(
            {
                "reason_code": code,
                # Coerce to native Python types — anomaly flags come back as
                # numpy scalars (numpy.bool_/float64) which FastAPI can't encode.
                "item_share": round(float(item_share), 4),
                "baseline_share": round(float(baseline_share), 4),
                "excess": round(float(excess), 4),
                "ratio": round(float(ratio), 4),
                "significant": bool(significant),
            }
        )

    overshooting = [
        r for r in reasons if r["significant"] and r["excess"] >= settings.flag_min_excess
    ]
    overshoot = len(overshooting) > 0

    # Multi-dimension defect detection: if the item's return rate for opposing
    # fit dimensions (too small AND too big, etc.) all exceed the flag floor,
    # the problem is likely the product itself rather than one sizing direction.
    share_by_code = {r["reason_code"]: r["item_share"] for r in reasons}
    _defect_dims = ("too_small", "too_big", "too_narrow", "too_wide")
    qualifying = {
        c
        for c in _defect_dims
        if share_by_code.get(c, 0.0) >= settings.flag_min_excess
    }
    defect_dimensions = sorted(qualifying)
    defect_combined_share = round(
        sum(share_by_code.get(c, 0.0) for c in qualifying), 4
    )
    defect_suspected = (
        len(qualifying) >= 2 and "too_small" in qualifying and "too_big" in qualifying
    )

    positive_excess = [r["excess"] for r in reasons if r["excess"] > 0]
    max_excess = round(max(positive_excess), 4) if positive_excess else 0.0

    top_flag = None
    if overshoot:
        worst = max(overshooting, key=lambda r: r["excess"])
        top_flag = {
            "reason_code": worst["reason_code"],
            "group": code_to_group(worst["reason_code"]),
            "excess": worst["excess"],
            "ratio": worst["ratio"],
        }

    rec_row = db.execute(
        text(
            "SELECT id, recommendation_type, reason_code, draft_text, "
            "confidence, status, created_at, updated_at "
            "FROM recommendations WHERE item_id = :iid "
            "ORDER BY updated_at DESC, id DESC LIMIT 1"
        ),
        {"iid": item_id},
    ).fetchone()
    recommendation = None
    if rec_row is not None:
        recommendation = {
            "id": rec_row[0],
            "recommendation_type": rec_row[1],
            "reason_code": rec_row[2],
            "draft_text": rec_row[3],
            "confidence": rec_row[4],
            "status": rec_row[5],
            "created_at": rec_row[6],
            "updated_at": rec_row[7],
        }

    has_proposed = recommendation is not None and recommendation["status"] == "proposed"
    has_applied = recommendation is not None and recommendation["status"] == "applied"
    defect_flag = any(
        r["significant"] and code_to_group(r["reason_code"]) == "DEFECT" for r in reasons
    )
    needs_input = has_proposed or (defect_flag and not has_applied)

    reasons.sort(key=lambda r: r["excess"], reverse=True)

    return {
        "item_id": item_id,
        "item_name": name,
        "category": category,
        "n": n,
        "n_eff": round(float(n_eff), 2),
        "reasons": reasons,
        "overshoot": overshoot,
        "max_excess": max_excess,
        "top_flag": top_flag,
        "recommendation": recommendation,
        "needs_input": needs_input,
        "defect_suspected": defect_suspected,
        "defect_dimensions": defect_dimensions,
        "defect_combined_share": defect_combined_share,
    }


@router.get("/item-insights")
def list_item_insights(db: Session = Depends(get_db)):
    """
    CHEAP list for the admin "Item insights" page. Returns one lightweight row
    per item that has qualifying returns — NO distribution / baseline / anomaly
    computation. The expensive per-item analytics are computed lazily by
    GET /item-insights/{item_id} only when the admin expands a row.

    Each row: {item_id, item_name, category, n}. A single GROUP BY over the
    returns table (joined to order_items for the name) powers the whole list,
    so the page loads instantly regardless of catalog size.
    """
    placeholders = ", ".join(f"'{s}'" for s in _RESOLVED_STATUSES)
    # Aggregate on the returns table alone (grouping only by item_id so the
    # planner can use ix_returns_status + ix_returns_item_id), take the top
    # items by volume, then resolve names/categories in one small follow-up
    # query. This avoids a 3-column GROUP BY join over the full 650k-row table.
    count_rows = db.execute(
        text(
            f"""
            SELECT r.item_id, COUNT(*) AS n
            FROM returns r
            WHERE r.item_id IS NOT NULL
              AND r.last_return_status IN ({placeholders})
              AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
            GROUP BY r.item_id
            ORDER BY n DESC
            LIMIT 1000
            """  # noqa: S608 - placeholders built from a constant tuple only
        )
    ).fetchall()

    if not count_rows:
        return []

    counts = {row[0]: int(row[1]) for row in count_rows}
    ids = list(counts.keys())
    id_placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": iid for i, iid in enumerate(ids)}
    name_rows = db.execute(
        text(
            f"""
            SELECT item_id, orderitem_product_name, category
            FROM order_items
            WHERE item_id IN ({id_placeholders})
              AND orderitem_product_name IS NOT NULL
            """  # noqa: S608 - placeholders are bound parameters
        ),
        params,
    ).fetchall()
    names = {r[0]: (r[1], r[2]) for r in name_rows}

    results = [
        {
            "item_id": iid,
            "item_name": names[iid][0],
            "category": names[iid][1],
            "n": counts[iid],
        }
        for iid in ids
        if iid in names
    ]
    results.sort(key=lambda x: x["n"], reverse=True)
    return results


@router.get("/item-insights/{item_id}")
def get_item_insight(item_id: str, db: Session = Depends(get_db)):
    """
    FULL return-reason analytics for ONE item (computed on demand when the admin
    expands its row). Returns the same per-item shape the list used to embed:
    reasons[], overshoot, max_excess, top_flag, recommendation, needs_input.
    """
    item_row = db.execute(
        text(
            "SELECT item_id, orderitem_product_name, category "
            "FROM order_items WHERE item_id = :iid"
        ),
        {"iid": item_id},
    ).fetchone()
    if not item_row:
        raise HTTPException(status_code=404, detail="Item not found")

    insight = _build_item_insight(item_row[0], item_row[1], item_row[2], db)
    if insight is None:
        raise HTTPException(status_code=404, detail="Item has no returns to analyse")
    return insight


# ---------------------------------------------------------------------------
# 4c. POST /api/admin/items/{item_id}/delist — remove a defective product
# ---------------------------------------------------------------------------


@router.post("/items/{item_id}/delist")
def delist_item(item_id: str, db: Session = Depends(get_db)):
    """
    Admin confirms removal of a product from the site (e.g. when it is
    defect-suspected across all sizes). Records an 'applied' delist
    recommendation and appends an AI activity-log note to the item's most
    recent related case, if one exists.
    """
    item_row = db.execute(
        text(
            "SELECT item_id, orderitem_product_name, category "
            "FROM order_items WHERE item_id = :iid"
        ),
        {"iid": item_id},
    ).fetchone()
    if not item_row:
        raise HTTPException(status_code=404, detail="Item not found")

    # Pull the latest analysis to explain the removal and derive confidence.
    insight = _build_item_insight(item_row[0], item_row[1], item_row[2], db)
    defect_dimensions = insight.get("defect_dimensions", []) if insight else []
    defect_combined_share = (
        insight.get("defect_combined_share", 0.0) if insight else 0.0
    )
    confidence = float(defect_combined_share) if defect_combined_share else 0.5

    if defect_dimensions:
        dims_text = ", ".join(defect_dimensions)
        draft_text = (
            f"Remove {item_row[1]!r} from the site. The item is failing across "
            f"multiple fit dimensions ({dims_text}), which points to a product "
            f"defect rather than a one-off sizing issue. Combined share of the "
            f"flagged dimensions is {defect_combined_share:.0%}."
        )
    else:
        draft_text = (
            f"Remove {item_row[1]!r} from the site based on its return profile."
        )

    now = datetime.utcnow()
    db.execute(
        text(
            "INSERT INTO recommendations "
            "(item_id, category, recommendation_type, reason_code, draft_text, "
            " confidence, status, created_at, updated_at) "
            "VALUES (:iid, :cat, 'delist', 'defect', :draft, :conf, 'applied', "
            "        :now, :now)"
        ),
        {
            "iid": item_id,
            "cat": item_row[2],
            "draft": draft_text,
            "conf": round(confidence, 4),
            "now": now,
        },
    )
    db.commit()

    # Append an AI log line to the item's most recent related case, if any.
    case_row = db.execute(
        text(
            "SELECT id FROM cases WHERE item_id = :iid "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"iid": item_id},
    ).fetchone()
    if case_row is not None:
        _append_ai_log(
            case_row[0],
            f"🗑️ Admin confirmed removal of {item_row[1]!r} from the site. "
            "Logged as an applied delist recommendation (product flagged as "
            "defective across multiple sizes).",
            db,
        )

    return {"success": True, "item_id": item_id, "applied_at": now.isoformat()}


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
# 8b. GET /api/admin/data-source — which analytics backend serves baselines
# ---------------------------------------------------------------------------


@router.get("/data-source")
def data_source(db: Session = Depends(get_db)):
    """
    Report which analytics backend (Athena / DuckDB / SQLite) currently serves
    the category/global baselines, plus the S3 / Glue / Athena wiring for the
    admin console. Cheap and non-blocking: it runs NO new Athena query — only an
    optional, guarded Glue get_tables lookup (returns [] if Glue is denied or
    unavailable).
    """
    backend_in_use = getattr(select_baseline_source(settings), "name", "sqlite")

    # Optional, guarded: list registered Glue tables without raising.
    tables_registered: list[str] = []
    try:
        resp = aws.glue_client().get_tables(DatabaseName=settings.glue_database)
        tables_registered = [t["Name"] for t in resp.get("TableList", [])]
    except Exception:
        tables_registered = []

    return {
        "backend_in_use": backend_in_use,
        "aws_available": aws.aws_available(),
        "s3_bucket": settings.s3_bucket,
        "glue_database": settings.glue_database,
        "athena_results": settings.athena_results_s3,
        "tables_registered": tables_registered,
        "last_athena_query_id": last_athena_query_id(),
        "row_counts_source": backend_in_use,
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
