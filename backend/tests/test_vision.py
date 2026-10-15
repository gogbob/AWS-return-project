"""
test_vision.py — Tests for acceptance criterion 6.

Criterion 6: Vision review routing (mock mode only — LLM_MOCK=true).

Mock rules (from agents/vision.py::mock_vision_verdict):
  - filename contains 'ok'  → consistent/high-confidence → approved
  - filename contains 'bad' → inconsistent               → HUMAN_REVIEW
  - anything else           → unclear                    → ask_again (first),
                                                           HUMAN_REVIEW (second)

Tests use the VisionSession directly rather than the HTTP layer so they
are not coupled to file storage / multipart upload plumbing.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.vision import VisionSession
from tests.conftest import make_case


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_filename(name: str, tmp_path) -> str:
    """Create an empty temp file and return its path string."""
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # minimal JPEG-like bytes
    return str(p)


# ---------------------------------------------------------------------------
# Criterion 6a — 'ok' photo + DEFECT reason → approved_no_fee
# ---------------------------------------------------------------------------


def test_ok_photo_approves_defect_no_fee(tmp_path) -> None:
    """
    A filename containing 'ok' with a DEFECT reason group must yield
    action='approved_no_fee' and a consistent verdict.
    Reason code 'defect' maps to group DEFECT via the insights taxonomy.
    """
    session = VisionSession(case_id="fake-case-001", reason_code="defect")
    filename = _tmp_filename("ok_photo.jpg", tmp_path)

    action, verdict = session.evaluate(filename)

    assert verdict.verdict == "consistent", (
        f"Expected verdict='consistent' for 'ok' photo, got {verdict.verdict!r}"
    )
    assert action == "approved_no_fee", (
        f"Expected action='approved_no_fee' for DEFECT + consistent, got {action!r}"
    )
    assert verdict.confidence >= 0.7, (
        f"Expected high confidence for 'ok' photo, got {verdict.confidence}"
    )


def test_ok_photo_non_defect_approved_with_fee(tmp_path) -> None:
    """
    A filename containing 'ok' with a non-DEFECT reason yields
    action='approved_with_fee'.
    """
    session = VisionSession(case_id="fake-case-002", reason_code="too_small")
    filename = _tmp_filename("ok_return.jpg", tmp_path)

    action, verdict = session.evaluate(filename)

    assert action == "approved_with_fee", (
        f"Expected action='approved_with_fee' for FIT_SIZE + consistent, got {action!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 6b — 'bad' photo → HUMAN_REVIEW
# ---------------------------------------------------------------------------


def test_bad_photo_human_review(tmp_path) -> None:
    """
    A filename containing 'bad' yields inconsistent verdict → human_review.
    """
    session = VisionSession(case_id="fake-case-003", reason_code="defect")
    filename = _tmp_filename("bad_photo.jpg", tmp_path)

    action, verdict = session.evaluate(filename)

    assert verdict.verdict == "inconsistent", (
        f"Expected verdict='inconsistent' for 'bad' photo, got {verdict.verdict!r}"
    )
    assert action == "human_review", (
        f"Expected action='human_review' for inconsistent verdict, got {action!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 6c — two unclear photos → HUMAN_REVIEW after second
# ---------------------------------------------------------------------------


def test_first_unclear_photo_asks_again(tmp_path) -> None:
    """
    First unclear photo (no 'ok' or 'bad' in name) → ask_again.
    """
    session = VisionSession(case_id="fake-case-004", reason_code="FIT_SIZE")
    filename = _tmp_filename("photo_unclear.jpg", tmp_path)

    action, verdict = session.evaluate(filename)

    assert verdict.verdict == "unclear", (
        f"Expected verdict='unclear' for generic filename, got {verdict.verdict!r}"
    )
    assert action == "ask_again", (
        f"Expected action='ask_again' for first unclear photo, got {action!r}"
    )


def test_two_unclear_photos_human_review(tmp_path) -> None:
    """
    Two consecutive unclear photos → human_review after the second.
    """
    session = VisionSession(case_id="fake-case-005", reason_code="FIT_SIZE")
    f1 = _tmp_filename("photo_one.jpg", tmp_path)
    f2 = _tmp_filename("photo_two.png", tmp_path)

    action1, _ = session.evaluate(f1)
    action2, _ = session.evaluate(f2)

    assert action1 == "ask_again", (
        f"Expected 'ask_again' on first unclear, got {action1!r}"
    )
    assert action2 == "human_review", (
        f"Expected 'human_review' on second unclear, got {action2!r}"
    )
    assert session.concluded is True


# ---------------------------------------------------------------------------
# Criterion 6d — customer refuses photo → HUMAN_REVIEW
# ---------------------------------------------------------------------------


def test_refuse_photo_human_review(test_db: Session, test_client: TestClient) -> None:
    """
    POST /api/returns/{case_id}/refuse-photo → case status becomes HUMAN_REVIEW.
    """
    case_id = make_case(test_db, status="VISION_REVIEW")

    resp = test_client.post(f"/api/returns/{case_id}/refuse-photo")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()
    assert data["state"] == "HUMAN_REVIEW", (
        f"Expected state='HUMAN_REVIEW' after refuse-photo, got {data['state']!r}"
    )

    # Also verify the DB was updated
    row = test_db.execute(
        text("SELECT status FROM cases WHERE id = :cid"),
        {"cid": case_id},
    ).fetchone()
    assert row is not None
    assert row[0] == "HUMAN_REVIEW", (
        f"DB case status must be HUMAN_REVIEW, got {row[0]!r}"
    )


def test_vision_session_refuses_after_conclusion(tmp_path) -> None:
    """
    Calling evaluate() on an already-concluded VisionSession raises RuntimeError.
    """
    session = VisionSession(case_id="fake-case-006", reason_code="defect")
    filename = _tmp_filename("bad_concluded.jpg", tmp_path)
    session.evaluate(filename)  # concludes because verdict is inconsistent

    with pytest.raises(RuntimeError, match="already concluded"):
        session.evaluate(filename)
