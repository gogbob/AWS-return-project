"""
vision_result.py — Process vision-model verdicts for the ALDO Returns Detective Agent.

After a customer uploads photos and the vision model produces a verdict,
this module maps that verdict to a final triage outcome.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..insights.engine import code_to_group
from .triage import TriageOutcome


class VisionVerdict(BaseModel):
    verdict: Literal["consistent", "inconsistent", "unclear"]
    confidence: float
    observations: str
    matches_ordered_product: bool
    defect_visible: Optional[bool] = None


def process_vision_result(
    verdict: VisionVerdict,
    reason_code: str,
    case_id: str,
    db: Session,
) -> TriageOutcome:
    """
    Map a vision verdict to a final triage outcome and update the case.

    Rules:
      consistent AND confidence >= 0.7  → approve
          fee waived  if reason group is DEFECT
          fee applied if any other group
      inconsistent, unclear, or confidence < 0.7 → HUMAN_REVIEW

    The case triage_outcome is updated in place.
    """
    group = code_to_group(reason_code)

    if verdict.verdict == "consistent" and verdict.confidence >= 0.7:
        # Approve; waive fee for confirmed defects
        if group == "DEFECT":
            outcome = TriageOutcome.APPROVED_NO_FEE
            fee = 0.0
        else:
            outcome = TriageOutcome.APPROVED_WITH_FEE
            fee = settings.shipping_fee_cad
    else:
        # Insufficient evidence or contradiction → human review
        outcome = TriageOutcome.HUMAN_REVIEW
        fee = 0.0

    # Persist the updated outcome
    db.execute(
        text(
            "UPDATE cases SET triage_outcome = :outcome WHERE id = :cid"
        ),
        {"outcome": outcome.value, "cid": case_id},
    )
    db.commit()

    return outcome
