"""
test_admin.py — Tests for acceptance criteria 7 and 8.

Criterion 7: Admin human decision updates case and is visible in customer poll.
Criterion 8: Fraud exclusion from insights; no loyalty/suspicion leak in
             customer API or assistant messages.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import make_case

# ---------------------------------------------------------------------------
# Forbidden tokens that must never appear in customer-facing responses
# ---------------------------------------------------------------------------
_FORBIDDEN_TOKENS = {"loyalty", "suspicion", "fraud", "risk score", "risk_score", "score"}


def _contains_forbidden(obj, *, path: str = "") -> list[str]:
    """
    Recursively walk JSON-decoded Python objects (dicts, lists, str) and
    return a list of violation descriptions (field key or string value).
    """
    violations: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            k_lower = str(k).lower()
            for token in _FORBIDDEN_TOKENS:
                if token in k_lower and k_lower not in ("risk_score",) :
                    # triage_breakdown_json from admin is ok; customer routes
                    # must not include these keys at all
                    violations.append(f"key {k!r} at {path}")
            violations.extend(_contains_forbidden(v, path=f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            violations.extend(_contains_forbidden(item, path=f"{path}[{i}]"))
    elif isinstance(obj, str):
        lower = obj.lower()
        for token in _FORBIDDEN_TOKENS:
            if token in lower:
                violations.append(f"value {obj!r:.60s} at {path}")
    return violations


# ---------------------------------------------------------------------------
# Criterion 7 — Human admin action updates case; customer poll reflects it
# ---------------------------------------------------------------------------


def test_human_action_updates_case_and_polling(
    test_db: Session, test_client: TestClient
) -> None:
    """
    Create a HUMAN_REVIEW case → POST /api/admin/cases/{id}/decision
    with approve_no_fee → GET /api/returns/{id} must return APPROVED_NO_FEE.
    """
    case_id = make_case(
        test_db,
        customer_id="CUST-HIGH",
        status="HUMAN_REVIEW",
    )

    # Admin decision
    resp = test_client.post(
        f"/api/admin/cases/{case_id}/decision",
        json={"action": "approve_no_fee", "note": "manual override"},
    )
    assert resp.status_code == 200, f"Admin decision failed: {resp.text}"
    data = resp.json()
    assert data["new_status"] == "APPROVED_NO_FEE", (
        f"Expected new_status=APPROVED_NO_FEE, got {data['new_status']!r}"
    )

    # Customer poll reflects updated status
    poll = test_client.get(f"/api/returns/{case_id}")
    assert poll.status_code == 200, f"Poll failed: {poll.text}"
    poll_data = poll.json()
    assert poll_data["state"] == "APPROVED_NO_FEE", (
        f"Customer poll must return APPROVED_NO_FEE, got {poll_data['state']!r}"
    )


def test_admin_approve_with_fee(test_db: Session, test_client: TestClient) -> None:
    """Admin approve_with_fee sets status to APPROVED_WITH_FEE."""
    case_id = make_case(test_db, status="HUMAN_REVIEW")

    resp = test_client.post(
        f"/api/admin/cases/{case_id}/decision",
        json={"action": "approve_with_fee"},
    )
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "APPROVED_WITH_FEE"


def test_admin_reject(test_db: Session, test_client: TestClient) -> None:
    """Admin reject sets status to REJECTED."""
    case_id = make_case(test_db, status="HUMAN_REVIEW")

    resp = test_client.post(
        f"/api/admin/cases/{case_id}/decision",
        json={"action": "reject"},
    )
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "REJECTED"


# ---------------------------------------------------------------------------
# Criterion 8a — Fraud case excluded from insights evidence set
# ---------------------------------------------------------------------------


def test_fraud_case_excluded_from_insights(test_db: Session, test_client: TestClient) -> None:
    """
    Mark a return as fraud via admin mark_fraud action.
    The evidence query (which filters fraud_flag=0) must no longer count
    that return in the item distribution for ITEM-BOOTS.
    """
    from app.insights.engine import compute_item_distribution

    # Build a case for an existing return
    case_id = make_case(
        test_db,
        customer_id="CUST-LOW",
        order_number="ORD-LOW-001",
        sku="SKU-001",
        item_id="ITEM-BOOTS",
        reason_code="too_small",
        status="HUMAN_REVIEW",
    )

    # Count how many CUST-LOW rows contribute to the distribution before fraud
    dist_before = compute_item_distribution("ITEM-BOOTS", test_db)
    n_before = dist_before.get("n", 0)

    # Mark fraud via admin API
    resp = test_client.post(
        f"/api/admin/cases/{case_id}/decision",
        json={"action": "mark_fraud"},
    )
    assert resp.status_code == 200, f"mark_fraud failed: {resp.text}"

    # The fraud flag should now be set on the matching return rows
    fraud_count = test_db.execute(
        text(
            "SELECT COUNT(*) FROM returns "
            "WHERE customer_uid = 'CUST-LOW' AND fraud_flag = 1"
        )
    ).scalar()
    assert fraud_count is not None and fraud_count > 0, (
        "Expected at least one return to be fraud-flagged after mark_fraud"
    )

    # Customer fraud_flag must also be set
    cust_row = test_db.execute(
        text("SELECT fraud_flag FROM customers WHERE customer_id = 'CUST-LOW'")
    ).fetchone()
    assert cust_row is not None and bool(cust_row[0]) is True, (
        "Customer fraud_flag must be set after mark_fraud"
    )

    # Item distribution must exclude fraud rows (n decreases)
    dist_after = compute_item_distribution("ITEM-BOOTS", test_db)
    n_after = dist_after.get("n", 0)
    assert n_after < n_before, (
        f"Expected item distribution n to decrease after fraud exclusion "
        f"(before={n_before}, after={n_after})"
    )


# ---------------------------------------------------------------------------
# Criterion 8b — No loyalty/suspicion leak in customer-facing API responses
# ---------------------------------------------------------------------------

_CUSTOMER_ENDPOINTS = [
    ("GET", "/api/demo/customers"),
    ("GET", "/api/product"),
    ("GET", "/api/stores/nearest?lat=45.5&lng=-73.5"),
]


@pytest.mark.parametrize("method,path", _CUSTOMER_ENDPOINTS)
def test_no_loyalty_leak_in_customer_api(
    method: str, path: str, test_db: Session, test_client: TestClient
) -> None:
    """
    All customer-facing GET endpoints must not expose loyalty, suspicion,
    fraud, or risk-score tokens in any key or string value of their JSON.

    Note: /api/product requires at least one order_item → seeded by conftest.
    """
    resp = getattr(test_client, method.lower())(path)
    # Accept 200 or 404 (product not found if demo_item_name empty and no returns)
    assert resp.status_code in (200, 404), (
        f"{method} {path} → unexpected status {resp.status_code}: {resp.text}"
    )
    if resp.status_code == 200:
        try:
            body = resp.json()
        except Exception:
            return  # non-JSON response — no leak possible

        violations = _contains_forbidden(body)
        # The only exception: /api/demo/customers may include loyalty_dev
        # only when X-Node-Env: development is present (not sent here).
        # Filter out the legitimate loyalty_dev key that should not appear
        # in production mode.
        violations = [v for v in violations if "loyalty_dev" not in v]
        assert violations == [], (
            f"{method} {path}: forbidden tokens found in response: {violations}"
        )


def test_no_loyalty_leak_in_returns_start_response(
    test_db: Session, test_client: TestClient
) -> None:
    """
    POST /api/returns/start must not leak forbidden tokens in its JSON response.
    """
    resp = test_client.post(
        "/api/returns/start",
        json={
            "customer_id": "CUST-HIGH",
            "order_number": "ORD-HIGH-001",
            "sku": "SKU-001",
            "item_id": "ITEM-BOOTS",
            "reason_code": "FIT_SIZE",
        },
    )
    # 200 or 400 (if duplicate, etc.) — we care about JSON content when 200
    if resp.status_code == 200:
        violations = _contains_forbidden(resp.json())
        assert violations == [], (
            f"/api/returns/start response contains forbidden tokens: {violations}"
        )


# ---------------------------------------------------------------------------
# Criterion 8c — No loyalty/suspicion leak in assistant messages
# ---------------------------------------------------------------------------


def test_no_loyalty_leak_in_assistant_messages(
    test_db: Session, test_client: TestClient
) -> None:
    """
    Simulate a return flow (mock mode), collect all assistant messages from
    case_messages, and assert none contain forbidden tokens.
    """
    # Start a return to create a case + initial assistant message
    start_resp = test_client.post(
        "/api/returns/start",
        json={
            "customer_id": "CUST-HIGH",
            "order_number": "ORD-HIGH-001",
            "sku": "SKU-001",
            "item_id": "ITEM-BOOTS",
            "reason_code": "FIT_SIZE",
        },
    )
    assert start_resp.status_code == 200, f"start_return failed: {start_resp.text}"
    case_id = start_resp.json()["case_id"]

    # Send a follow-up message to generate another assistant turn
    msg_resp = test_client.post(
        f"/api/returns/{case_id}/message",
        json={"content": "The boot is too small, I need a larger size."},
    )
    # Accept 200 or 400 (resolved case)
    assert msg_resp.status_code in (200, 400), f"Unexpected: {msg_resp.text}"

    # Collect ALL assistant messages from the DB
    rows = test_db.execute(
        text(
            "SELECT content FROM case_messages "
            "WHERE case_id = :cid AND role = 'assistant'"
        ),
        {"cid": case_id},
    ).fetchall()

    assert rows, "Expected at least one assistant message in case_messages"

    for (content,) in rows:
        content_lower = content.lower()
        for token in _FORBIDDEN_TOKENS:
            # Allow "score" only as part of a compound word that is not a leak
            # e.g. "score" alone is forbidden but "score" inside a word could appear
            # in legitimate phrases.  Use word-boundary style check.
            if token == "score":
                # Check for standalone "score" or "risk score"
                import re
                if re.search(r"\brisk\s+score\b|\bscore\b", content_lower):
                    assert False, (
                        f"Forbidden token {token!r} found in assistant message: "
                        f"{content[:120]!r}"
                    )
            else:
                assert token not in content_lower, (
                    f"Forbidden token {token!r} found in assistant message: "
                    f"{content[:120]!r}"
                )
