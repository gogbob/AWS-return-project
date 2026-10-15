"""
loyalty.py — Loyalty scoring for the ALDO Returns Detective Agent.

The loyalty scores are seeded and pre-computed during data ingestion
(app/ingest/run.py) using a deterministic hash of the customer UID.
This module simply reads the stored value from the customers table.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_loyalty(customer_id: str, db: Session) -> int:
    """
    Return the pre-computed loyalty score (0–100) for *customer_id*.

    If the customer has fraud_flag = True, returns 0.
    If the customer is not found, returns 0 (conservative default).
    """
    row = db.execute(
        text(
            "SELECT loyalty, fraud_flag FROM customers WHERE customer_id = :cid"
        ),
        {"cid": customer_id},
    ).fetchone()

    if row is None:
        return 0

    loyalty, fraud_flag = row

    if fraud_flag:
        return 0

    return int(loyalty) if loyalty is not None else 0
