"""
test_instore.py — Tests for acceptance criterion 10.

Criterion 10: In-store path (GET /api/stores/nearest) does not create a case
              and does not invoke AI agents.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import make_case


# ---------------------------------------------------------------------------
# Criterion 10 — In-store nearest-store lookup creates no case and no agent call
# ---------------------------------------------------------------------------


def test_instore_no_case_created(
    test_db: Session, test_client: TestClient
) -> None:
    """
    GET /api/stores/nearest must not create any new case in the cases table.
    """
    # Baseline case count
    count_before = test_db.execute(
        text("SELECT COUNT(*) FROM cases")
    ).scalar() or 0

    resp = test_client.get("/api/stores/nearest?lat=45.5017&lng=-73.5718")
    # 200 (stores found) or 200 with empty list (no geocoded stores in seed) are both valid
    assert resp.status_code == 200, (
        f"GET /api/stores/nearest expected 200, got {resp.status_code}: {resp.text}"
    )

    count_after = test_db.execute(
        text("SELECT COUNT(*) FROM cases")
    ).scalar() or 0

    assert count_after == count_before, (
        f"cases table grew during /api/stores/nearest "
        f"(before={count_before}, after={count_after})"
    )


def test_instore_no_case_messages_created(
    test_db: Session, test_client: TestClient
) -> None:
    """
    GET /api/stores/nearest must not create any case_messages rows.
    """
    msg_before = test_db.execute(
        text("SELECT COUNT(*) FROM case_messages")
    ).scalar() or 0

    test_client.get("/api/stores/nearest?lat=45.5017&lng=-73.5718")

    msg_after = test_db.execute(
        text("SELECT COUNT(*) FROM case_messages")
    ).scalar() or 0

    assert msg_after == msg_before, (
        f"case_messages grew during /api/stores/nearest (before={msg_before}, after={msg_after})"
    )


def test_instore_agents_not_invoked(
    test_db: Session, test_client: TestClient, monkeypatch
) -> None:
    """
    GET /api/stores/nearest must not call build_assistant or run_mock_turn.
    """
    invoked: list[str] = []

    import app.agents.assistant as _assistant_mod

    def _fake_build(*args, **kwargs):
        invoked.append("build_assistant")
        return _assistant_mod.build_assistant.__wrapped__(*args, **kwargs) if hasattr(
            _assistant_mod.build_assistant, "__wrapped__"
        ) else None

    def _fake_run(*args, **kwargs):
        invoked.append("run_mock_turn")
        return "mock reply"

    monkeypatch.setattr(_assistant_mod, "build_assistant", _fake_build)
    monkeypatch.setattr(_assistant_mod, "run_mock_turn", _fake_run)

    test_client.get("/api/stores/nearest?lat=45.5017&lng=-73.5718")

    assert invoked == [], (
        f"Agent functions were invoked during /api/stores/nearest: {invoked}"
    )


def test_instore_returns_list_format(
    test_db: Session, test_client: TestClient
) -> None:
    """
    Response is always a JSON list (empty or populated), never a case object.
    """
    resp = test_client.get("/api/stores/nearest?lat=45.5017&lng=-73.5718")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list), (
        f"Expected list response from /api/stores/nearest, got {type(body).__name__}"
    )
