"""
routes_customer.py — Customer-facing API routes for the ALDO Returns Detective Agent.

All responses in this module MUST NEVER include fields named:
  loyalty, suspicion, fraud_flag, risk_score, triage_score, or any synonym.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..agents.assistant import build_assistant, run_mock_turn
from ..agents.vision import VisionSession
from ..config import settings
from ..db import get_db
from ..policy.eligibility import check_eligibility
from ..policy.triage import compute_triage

router = APIRouter()

# ---------------------------------------------------------------------------
# Demo customer list — 8 fixed entries with hidden loyalty bands.
# Loyalty bands: high=70-100, medium=40-69, low=0-39  (matches settings.high_loyalty_threshold=70)
# Sort order: high (1-3), medium (4-5), low (6-8)
# Loyalty is internal-only and NEVER sent to the customer UI.
# ---------------------------------------------------------------------------

# Two demo customers mapped to real customer_uid values in the dataset so the
# returnable-item list reflects actual order history. The public-facing "id"
# is a stable synthetic key; `uid` is what the data layer filters on.
_DEMO_CUSTOMERS: list[dict] = [
    {"id": "CUST-LOYAL", "uid": "SID:10005372135022", "label": "Loyal Customer", "_loyalty": 95},    # high loyalty
    {"id": "CUST-UNLOYAL", "uid": "SID:102909224525987", "label": "Unloyal Customer", "_loyalty": 8},  # low loyalty
]

# Map public demo id -> real customer_uid for the data layer.
_DEMO_UID_BY_ID: dict[str, str] = {c["id"]: c["uid"] for c in _DEMO_CUSTOMERS}

# Map label → customer index for fast admin lookup (used by admin routes too)
CUSTOMER_LABEL_MAP: dict[str, int] = {c["id"]: i + 1 for i, c in enumerate(_DEMO_CUSTOMERS)}


# ---------------------------------------------------------------------------
# 1. GET /api/demo/customers
# ---------------------------------------------------------------------------


@router.get("/api/demo/customers", tags=["Customer"])
def list_demo_customers(node_env: Optional[str] = Header(None, alias="X-Node-Env")):
    """
    Return the 8 demo customers.  Loyalty is included ONLY when the
    X-Node-Env: development header is present (dev tooltip).
    """
    is_dev = (
        node_env == "development"
        or os.environ.get("NODE_ENV", "") == "development"
    )

    result = []
    for c in _DEMO_CUSTOMERS:
        entry: dict = {"id": c["id"], "label": c["label"]}
        if is_dev:
            entry["loyalty_dev"] = c["_loyalty"]  # dev tooltip only
        result.append(entry)

    return result


# ---------------------------------------------------------------------------
# 2. GET /api/product
# ---------------------------------------------------------------------------


@router.get("/api/product", tags=["Customer"])
def get_product(db: Session = Depends(get_db)):
    """
    Return metadata for the featured product.

    item_id is taken from DEMO_ITEM_NAME config first; if empty, the item with
    the most evidence-eligible returns is used.

    NEVER includes loyalty or suspicion.
    """
    item_id: Optional[str] = settings.demo_item_name or None

    if not item_id:
        # Most evidence-eligible returns = most distinct customers with
        # completed/received/processed returns. Prefer clean style names
        # (skip internal variant codes like "..._mm" / "..._wm").
        row = db.execute(
            text(
                """
                SELECT r.item_id
                FROM returns r
                JOIN order_items oi ON oi.item_id = r.item_id
                WHERE r.last_return_status IN (
                    'RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED'
                )
                  AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
                  AND r.item_id IS NOT NULL
                  AND oi.orderitem_product_name IS NOT NULL
                  AND oi.orderitem_product_name NOT LIKE '%\\_mm' ESCAPE '\\'
                  AND oi.orderitem_product_name NOT LIKE '%\\_wm' ESCAPE '\\'
                  AND oi.orderitem_product_name NOT GLOB '*[0-9]*'
                GROUP BY r.item_id
                ORDER BY COUNT(DISTINCT r.customer_uid) DESC
                LIMIT 1
                """
            )
        ).fetchone()
        item_id = row[0] if row else None

    if not item_id:
        raise HTTPException(status_code=404, detail="No product data available")

    item_row = db.execute(
        text(
            "SELECT item_id, orderitem_product_name, category "
            "FROM order_items WHERE item_id = :iid"
        ),
        {"iid": item_id},
    ).fetchone()

    if not item_row:
        raise HTTPException(status_code=404, detail=f"Item {item_id!r} not found")

    # Check for an applied size-guidance recommendation → size_guidance_banner
    rec_row = db.execute(
        text(
            "SELECT draft_text FROM recommendations "
            "WHERE item_id = :iid "
            "  AND recommendation_type = 'size_guidance' "
            "  AND status = 'applied' "
            "ORDER BY updated_at DESC LIMIT 1"
        ),
        {"iid": item_id},
    ).fetchone()

    size_guidance_banner: Optional[str] = rec_row[0] if rec_row else None

    # Derive a rough price from the most recent refund_amount for this item
    price_row = db.execute(
        text(
            "SELECT refund_amount FROM returns "
            "WHERE item_id = :iid AND refund_amount IS NOT NULL "
            "ORDER BY return_creation_date DESC LIMIT 1"
        ),
        {"iid": item_id},
    ).fetchone()
    price: Optional[float] = float(price_row[0]) if price_row else None

    return {
        "item_id": item_row[0],
        "item_name": item_row[1],
        "price": price,
        "category": item_row[2],
        "size_guidance_banner": size_guidance_banner,
    }


# ---------------------------------------------------------------------------
# 3. GET /api/items/search
# ---------------------------------------------------------------------------


@router.get("/api/items/search", tags=["Customer"])
def search_items(customer_id: str, q: str = "", db: Session = Depends(get_db)):
    """
    List or fuzzy-search this customer's order items.

    When `q` is empty, returns ALL of the customer's returnable items so the
    UI can show them without requiring a search. When `q` is provided, results
    are filtered by fuzzy match on the product name.

    Returns [{order_number, sku, item_id, item_name, score}].
    NEVER includes loyalty.
    """
    from rapidfuzz import fuzz

    # Demo ids map to a real customer_uid; fall back to the raw value so a
    # real uid passed directly still works.
    resolved_uid = _DEMO_UID_BY_ID.get(customer_id, customer_id)

    rows = db.execute(
        text(
            """
            SELECT DISTINCT
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
        {"cid": resolved_uid},
    ).fetchall()

    query_lower = q.strip().lower()
    show_all = query_lower == ""
    results = []
    seen: set[tuple] = set()
    for row in rows:
        key = (row[0], row[1], row[2])
        if key in seen:
            continue
        seen.add(key)
        name = (row[3] or "").lower()
        score = 100.0 if show_all else float(fuzz.token_set_ratio(query_lower, name))
        if show_all or score >= 60:
            results.append(
                {
                    "order_number": row[0],
                    "sku": row[1],
                    "item_id": row[2],
                    "item_name": row[3],
                    "score": score,
                }
            )

    if show_all:
        results.sort(key=lambda x: (x["item_name"] or "").lower())
    else:
        results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# 3b. POST /api/checkout — record purchased items so they become returnable
# ---------------------------------------------------------------------------


class CheckoutItem(BaseModel):
    item_name: str
    size: str
    price: float


class CheckoutRequest(BaseModel):
    customer_id: str
    items: list[CheckoutItem]


@router.post("/api/checkout", tags=["Customer"])
def checkout(body: CheckoutRequest, db: Session = Depends(get_db)):
    """
    Record a purchase for the current demo customer.

    For each bag item we ensure an `order_items` row exists and insert a
    `returns` row with status 'PURCHASED' (source 'checkout'). This makes the
    item show up in the customer's returnable list and pass eligibility — the
    'PURCHASED' status is deliberately NOT one of the active-return statuses,
    so the return flow treats it as a fresh, un-returned purchase.
    """
    if not body.items:
        raise HTTPException(status_code=400, detail="Bag is empty.")

    resolved_uid = _DEMO_UID_BY_ID.get(body.customer_id, body.customer_id)

    order_number = f"WEB-{uuid.uuid4().hex[:10].upper()}"
    created = []

    for idx, item in enumerate(body.items):
        item_id = item.item_name
        # Unique sku per line so multiple sizes don't collide.
        sku = f"CO-{uuid.uuid4().hex[:8].upper()}"

        # Ensure an order_items row exists for this product.
        exists = db.execute(
            text("SELECT 1 FROM order_items WHERE item_id = :iid LIMIT 1"),
            {"iid": item_id},
        ).fetchone()
        if not exists:
            db.execute(
                text(
                    "INSERT INTO order_items "
                    "(item_id, sku, orderitem_product_name, category, store_banner) "
                    "VALUES (:iid, :sku, :name, 'other', 'ALDO')"
                ),
                {"iid": item_id, "sku": sku, "name": item.item_name},
            )

        # Insert a purchase row in `returns` (status PURCHASED => returnable).
        db.execute(
            text(
                "INSERT INTO returns "
                "(order_number, sku, item_id, customer_uid, return_creation_date, "
                " last_return_status, refund_amount, item_return_qty, source, fraud_flag) "
                "VALUES (:on, :sku, :iid, :cid, :now, 'PURCHASED', :price, 1, 'checkout', 0)"
            ),
            {
                "on": order_number,
                "sku": sku,
                "iid": item_id,
                "cid": resolved_uid,
                "now": datetime.utcnow(),
                "price": item.price,
            },
        )
        created.append(
            {
                "order_number": order_number,
                "sku": sku,
                "item_id": item_id,
                "item_name": item.item_name,
            }
        )

    db.commit()
    return {"order_number": order_number, "items": created}


# ---------------------------------------------------------------------------
# 4. GET /api/stores/nearest
# ---------------------------------------------------------------------------


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return great-circle distance in km between two lat/lng points."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * R * asin(sqrt(a))


@router.get("/api/stores/nearest", tags=["Customer"])
def nearest_stores(lat: float, lng: float, db: Session = Depends(get_db)):
    """
    Return 3 nearest stores by Haversine distance.
    Only stores with lat/lng != null are considered.
    """
    rows = db.execute(
        text(
            "SELECT name, address, lat, lng "
            "FROM stores "
            "WHERE lat IS NOT NULL AND lng IS NOT NULL"
        )
    ).fetchall()

    if not rows:
        return []

    scored = []
    for row in rows:
        dist = _haversine_km(lat, lng, float(row[2]), float(row[3]))
        maps_url = (
            "https://www.google.com/maps/search/?api=1&query="
            + quote(row[0] or "")
        )
        scored.append(
            {
                "name": row[0],
                "address": row[1],
                "lat": row[2],
                "lng": row[3],
                "distance_km": round(dist, 3),
                "maps_url": maps_url,
            }
        )

    scored.sort(key=lambda x: x["distance_km"])
    return scored[:3]


# ---------------------------------------------------------------------------
# AI activity-log helpers (admin console)
# ---------------------------------------------------------------------------


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


def _log_triage_decision(case_id: str, db: Session) -> None:
    """
    Run triage for a freshly-created case and record the AI's shipping-fee
    decision (and reasoning) to the admin activity log. On a review outcome,
    log that the AI is requesting human input on what to tell the customer.
    """
    try:
        result = compute_triage(case_id, db)
    except Exception as exc:  # pragma: no cover - defensive
        _append_ai_log(case_id, f"⚠️ Triage failed to run: {exc}", db)
        return

    bd = result.breakdown or {}
    loyalty_c = bd.get("loyalty_component", 0.0)
    implaus_c = bd.get("implausibility_component", 0.0)
    claim_c = bd.get("claim_risk_component", 0.0)

    # Pick the dominant reason driver for a plain-English explanation.
    drivers = {
        "customer loyalty": loyalty_c,
        "believability of the reason": implaus_c + claim_c,
    }
    main_driver = max(drivers, key=drivers.get) if any(drivers.values()) else "customer loyalty"

    outcome = result.outcome.value

    if outcome == "APPROVED_NO_FEE":
        _append_ai_log(
            case_id,
            "✅ Decision: APPROVE with FREE shipping (no fee). "
            f"Reason: strong {main_driver}. "
            "The customer qualifies for a no-cost return.",
            db,
        )
    elif outcome == "APPROVED_WITH_FEE":
        _append_ai_log(
            case_id,
            f"💲 Decision: APPROVE with a reduced-cost return "
            f"(${result.fee:.2f} shipping fee). "
            f"Reason: moderate risk based on {main_driver}. "
            "Free shipping not granted.",
            db,
        )
    else:
        # VISION_REVIEW / HUMAN_REVIEW — the AI needs a human to weigh in.
        _append_ai_log(
            case_id,
            f"🙋 Needs human input (suspicion {result.suspicion:.2f}). "
            f"The AI is unsure based on {main_driver} and is asking the admin: "
            "should the customer be offered free shipping, a reduced-cost return, "
            "or a rejection? Use the decision buttons below to tell the AI what to do.",
            db,
        )


# ---------------------------------------------------------------------------
# 5. POST /api/returns/start
# ---------------------------------------------------------------------------


class StartReturnRequest(BaseModel):
    customer_id: str
    order_number: str
    sku: str
    item_id: str
    reason: str


@router.post("/api/returns/start", tags=["Customer"])
def start_return(body: StartReturnRequest, db: Session = Depends(get_db)):
    """
    Begin a return.  Checks eligibility, creates a case, starts assistant.
    NEVER returns loyalty or suspicion.
    """
    # Demo ids (CUST-00N) map to a real customer_uid for data-layer lookups.
    resolved_uid = _DEMO_UID_BY_ID.get(body.customer_id, body.customer_id)

    # 1. Check eligibility
    eligibility = check_eligibility(
        resolved_uid, body.order_number, body.sku, db
    )
    if not eligibility.eligible:
        raise HTTPException(status_code=400, detail=eligibility.reason)

    # 2. Cancel any lingering PENDING case for the same item (abandoned flow),
    #    then create a fresh case.
    db.execute(
        text(
            "UPDATE cases SET status = 'cancelled' "
            "WHERE order_number = :on AND sku = :sku AND status = 'PENDING'"
        ),
        {"on": body.order_number, "sku": body.sku},
    )

    case_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO cases "
            "(id, customer_id, order_number, sku, item_id, reason_code, status, created_at, source_channel) "
            "VALUES (:id, :cid, :on, :sku, :iid, :rc, 'PENDING', :now, 'online')"
        ),
        {
            "id": case_id,
            "cid": resolved_uid,
            "on": body.order_number,
            "sku": body.sku,
            "iid": body.item_id,
            "rc": body.reason,
            "now": datetime.utcnow(),
        },
    )
    db.commit()

    # 2b. Run triage now and write a human-readable AI decision to the
    #     admin activity log (case_messages role='ai_log').
    _log_triage_decision(case_id, db)

    # 3. Start assistant session — generate initial greeting
    if settings.llm_mock:
        agent = build_assistant()
        initial_message = run_mock_turn(
            agent,
            f"I'd like to return item {body.item_id} (order {body.order_number}) "
            f"because: {body.reason}",
        )
    else:
        agent = build_assistant()
        response = agent(
            f"I'd like to return item {body.item_id} (order {body.order_number}) "
            f"because: {body.reason}"
        )
        initial_message = str(response)

    # Persist initial assistant message
    db.execute(
        text(
            "INSERT INTO case_messages (case_id, role, content, created_at) "
            "VALUES (:cid, 'assistant', :content, :now)"
        ),
        {"cid": case_id, "content": initial_message, "now": datetime.utcnow()},
    )
    db.commit()

    # Read back state (may have been changed by agent tool calls)
    case_row = db.execute(
        text("SELECT status FROM cases WHERE id = :cid"),
        {"cid": case_id},
    ).fetchone()
    state = case_row[0] if case_row else "PENDING"

    return {"case_id": case_id, "initial_message": initial_message, "state": state}


# ---------------------------------------------------------------------------
# 6. POST /api/returns/{case_id}/message
# ---------------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    content: str


@router.post("/api/returns/{case_id}/message", tags=["Customer"])
def send_message(case_id: str, body: SendMessageRequest, db: Session = Depends(get_db)):
    """
    Drive one turn of the Return Assistant.
    NEVER returns loyalty or suspicion.
    """
    # Load case
    case_row = db.execute(
        text("SELECT customer_id, order_number, sku, item_id, reason_code, status FROM cases WHERE id = :cid"),
        {"cid": case_id},
    ).fetchone()
    if not case_row:
        raise HTTPException(status_code=404, detail="Case not found")

    customer_id, order_number, sku, item_id, reason_code, status = case_row

    if status in ("APPROVED_NO_FEE", "APPROVED_WITH_FEE", "REJECTED"):
        raise HTTPException(status_code=400, detail=f"Case is already resolved: {status}")

    # Admin can halt the AI for this case; stop processing further turns.
    if status == "STOPPED":
        return {
            "message": (
                "An agent has taken over this request. The automated assistant "
                "has stopped and a team member will follow up if needed."
            ),
            "state": "STOPPED",
            "vision_requested": False,
        }

    # Persist user message
    db.execute(
        text(
            "INSERT INTO case_messages (case_id, role, content, created_at) "
            "VALUES (:cid, 'user', :content, :now)"
        ),
        {"cid": case_id, "content": body.content, "now": datetime.utcnow()},
    )
    db.commit()

    # Reload conversation history (exclude internal ai_log entries)
    msg_rows = db.execute(
        text(
            "SELECT role, content FROM case_messages "
            "WHERE case_id = :cid AND role != 'ai_log' ORDER BY id ASC"
        ),
        {"cid": case_id},
    ).fetchall()
    history = [
        {"role": row[0], "content": [{"text": row[1]}]} for row in msg_rows
    ]

    # Run one agent turn
    if settings.llm_mock:
        agent = build_assistant(messages=history)
        assistant_message = run_mock_turn(agent, body.content)
    else:
        agent = build_assistant(messages=history[:-1])  # last message is the new user turn
        response = agent(body.content)
        assistant_message = str(response)

    # Persist assistant reply
    db.execute(
        text(
            "INSERT INTO case_messages (case_id, role, content, created_at) "
            "VALUES (:cid, 'assistant', :content, :now)"
        ),
        {"cid": case_id, "content": assistant_message, "now": datetime.utcnow()},
    )
    db.commit()

    # Read back updated state & fee
    updated = db.execute(
        text(
            "SELECT status, triage_breakdown_json FROM cases WHERE id = :cid"
        ),
        {"cid": case_id},
    ).fetchone()
    state = updated[0] if updated else status
    fee: Optional[float] = None
    if updated and updated[1]:
        try:
            bd = json.loads(updated[1])
            raw_fee = bd.get("fee")
            if raw_fee is not None:
                fee = float(raw_fee)
        except (json.JSONDecodeError, TypeError):
            pass

    vision_requested = state == "VISION_REVIEW"

    return {
        "assistant_message": assistant_message,
        "state": state,
        "fee": fee,
        "vision_requested": vision_requested,
    }


# ---------------------------------------------------------------------------
# 7. POST /api/returns/{case_id}/photo
# ---------------------------------------------------------------------------

_UPLOAD_ROOT = Path(__file__).parents[3] / "storage" / "uploads"
_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


@router.post("/api/returns/{case_id}/photo", tags=["Customer"])
async def upload_photo(
    case_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Accept a photo, run the Vision agent, persist verdict.
    NEVER returns loyalty or suspicion.
    """
    # Validate content type
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Must be jpg, png, or webp.",
        )

    # Read and size-check
    data = await file.read()
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 5 MB limit.")

    # Load case
    case_row = db.execute(
        text("SELECT reason_code, status FROM cases WHERE id = :cid"),
        {"cid": case_id},
    ).fetchone()
    if not case_row:
        raise HTTPException(status_code=404, detail="Case not found")

    reason_code, current_status = case_row

    # Save file
    upload_dir = _UPLOAD_ROOT / case_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload").suffix or ".jpg"
    filename = f"{uuid.uuid4().hex}{suffix}"
    file_path = upload_dir / filename
    file_path.write_bytes(data)

    # Count prior vision evidence to drive retry logic
    prior_count = db.execute(
        text("SELECT COUNT(*) FROM case_evidence WHERE case_id = :cid"),
        {"cid": case_id},
    ).scalar() or 0

    # Run vision session
    session = VisionSession(case_id=case_id, reason_code=reason_code or "other")
    session.attempt = prior_count  # resume retry counter
    action, verdict = session.evaluate(str(file_path))

    verdict_json = verdict.model_dump_json()

    # Persist evidence
    db.execute(
        text(
            "INSERT INTO case_evidence (case_id, file_path, vision_verdict_json, created_at) "
            "VALUES (:cid, :fp, :vj, :now)"
        ),
        {
            "cid": case_id,
            "fp": str(file_path),
            "vj": verdict_json,
            "now": datetime.utcnow(),
        },
    )

    # Map action → state and update case
    _ACTION_STATE: dict[str, str] = {
        "approved_no_fee": "APPROVED_NO_FEE",
        "approved_with_fee": "APPROVED_WITH_FEE",
        "human_review": "HUMAN_REVIEW",
        "ask_again": "VISION_REVIEW",
    }
    new_state = _ACTION_STATE.get(action, "VISION_REVIEW")

    db.execute(
        text("UPDATE cases SET status = :status WHERE id = :cid"),
        {"status": new_state, "cid": case_id},
    )
    db.commit()

    # Build a user-facing message
    _ACTION_MESSAGES: dict[str, str] = {
        "approved_no_fee": "Your return has been approved with no restocking fee.",
        "approved_with_fee": f"Your return has been approved. A ${settings.shipping_fee_cad:.2f} CAD restocking fee applies.",
        "human_review": (
            "Your return has been escalated to our customer service team. "
            "A representative will follow up within 1–2 business days."
        ),
        "ask_again": (
            "The photo wasn't quite clear enough. "
            "Could you submit another photo showing the item from a different angle?"
        ),
    }
    assistant_message = _ACTION_MESSAGES.get(
        action,
        "Photo received. We'll be in touch shortly.",
    )

    return {
        "verdict": verdict.verdict,
        "state": new_state,
        "assistant_message": assistant_message,
    }


# ---------------------------------------------------------------------------
# 8. POST /api/returns/{case_id}/refuse-photo
# ---------------------------------------------------------------------------


@router.post("/api/returns/{case_id}/refuse-photo", tags=["Customer"])
def refuse_photo(case_id: str, db: Session = Depends(get_db)):
    """
    Customer refuses to submit a photo → escalate to HUMAN_REVIEW.
    NEVER returns loyalty or suspicion.
    """
    case_row = db.execute(
        text("SELECT id FROM cases WHERE id = :cid"),
        {"cid": case_id},
    ).fetchone()
    if not case_row:
        raise HTTPException(status_code=404, detail="Case not found")

    db.execute(
        text("UPDATE cases SET status = 'HUMAN_REVIEW' WHERE id = :cid"),
        {"cid": case_id},
    )
    db.commit()

    return {
        "state": "HUMAN_REVIEW",
        "assistant_message": (
            "No problem. Your return has been escalated to our customer service team. "
            "A representative will follow up within 1–2 business days."
        ),
    }


# ---------------------------------------------------------------------------
# 9. GET /api/returns/{case_id}
# ---------------------------------------------------------------------------


@router.get("/api/returns/{case_id}", tags=["Customer"])
def get_return_case(case_id: str, db: Session = Depends(get_db)):
    """
    Poll case state.
    NEVER returns loyalty or suspicion.
    """
    row = db.execute(
        text(
            "SELECT status, triage_outcome, triage_breakdown_json "
            "FROM cases WHERE id = :cid"
        ),
        {"cid": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")

    status, triage_outcome, breakdown_json = row

    fee: Optional[float] = None
    if breakdown_json:
        try:
            bd = json.loads(breakdown_json)
            raw_fee = bd.get("fee")
            if raw_fee is not None:
                fee = float(raw_fee)
        except (json.JSONDecodeError, TypeError):
            pass

    # Last assistant message
    msg_row = db.execute(
        text(
            "SELECT content FROM case_messages "
            "WHERE case_id = :cid AND role = 'assistant' "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"cid": case_id},
    ).fetchone()
    message = msg_row[0] if msg_row else None

    return {
        "state": status,
        "outcome": triage_outcome,
        "fee": fee,
        "message": message,
    }
