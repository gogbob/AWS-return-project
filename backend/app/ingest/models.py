"""
models.py — SQLAlchemy ORM models for all 13 tables in the ALDO Returns
Detective Agent database.

All models inherit from the shared DeclarativeBase defined in db.py.
Column names match the normalised headers documented in docs/data_profile.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


# ---------------------------------------------------------------------------
# 1. order_items
# ---------------------------------------------------------------------------


class OrderItem(Base):
    """Style-level product catalogue derived from OMS orderitem_product_name."""

    __tablename__ = "order_items"

    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True)
    orderitem_product_name: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    store_banner: Mapped[str | None] = mapped_column(String, nullable=True)

    # relationships
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="order_item"
    )
    agent_changes: Mapped[list["AgentChange"]] = relationship(
        back_populates="order_item"
    )


# ---------------------------------------------------------------------------
# 2. returns
# ---------------------------------------------------------------------------


class Return(Base):
    """One row per resolved return transaction from the OMS export."""

    __tablename__ = "returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # item_id derived from orderitem_product_name via ITEM_KEY config
    item_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("order_items.item_id"), nullable=True, index=True
    )
    customer_uid: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    return_order_number: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    return_channel: Mapped[str | None] = mapped_column(String, nullable=True)
    return_creation_date: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    last_return_status: Mapped[str | None] = mapped_column(String, nullable=True)
    refund_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    item_return_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String, nullable=True)
    # source distinguishes live API rows from historical CSV rows
    source: Mapped[str] = mapped_column(String, default="csv")
    fraud_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    # instore_return_location: numeric store ID from OMS data; nullable for online returns
    instore_return_location: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
# 3. returnable_flags
# ---------------------------------------------------------------------------


class ReturnableFlag(Base):
    """Final-sale / non-returnable flags sourced from File 2."""

    __tablename__ = "returnable_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # True means final-sale (non-returnable)
    is_final_sale: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# 4. restocking_fees
# ---------------------------------------------------------------------------


class RestockingFee(Base):
    """One restocking fee record per return order number (File 1)."""

    __tablename__ = "restocking_fees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    return_order_number: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    restocking_revenue: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------------------
# 5. exchanges
# ---------------------------------------------------------------------------


class Exchange(Base):
    """In-store exchange transactions from File 3."""

    __tablename__ = "exchanges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # original_order_number in source CSV → mapped to order_number for consistency
    order_number: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    exchange_sku: Mapped[str | None] = mapped_column(String, nullable=True)
    exchange_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exchange_confidence: Mapped[str | None] = mapped_column(String, nullable=True)
    # transactions_businessdaydate in source CSV → business_day_date (alias for clarity)
    business_day_date: Mapped[str | None] = mapped_column(String, nullable=True)
    # transactions_transnumber in source CSV → trans_number
    trans_number: Mapped[str | None] = mapped_column(String, nullable=True)
    return_store: Mapped[str | None] = mapped_column(String, nullable=True)
    sales: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------------------
# 6. customers
# ---------------------------------------------------------------------------


class Customer(Base):
    """Customer profile table; customer_id == customer_uid from OMS data."""

    __tablename__ = "customers"

    # customer_id is the primary key; equals customer_uid from the OMS CSV
    customer_id: Mapped[str] = mapped_column(String, primary_key=True)
    # cnsmr_prof_sid in OMS CSV (spec calls it csnmr_prof_id — real name documented)
    # NOTE: Spec uses 'csnmr_prof_id'; real OMS column is 'cnsmr_prof_sid'.
    #       Stored under the real column name.
    cnsmr_prof_sid: Mapped[str | None] = mapped_column(String, nullable=True)
    loyalty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fraud_flag: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# 7. stores
# ---------------------------------------------------------------------------


class Store(Base):
    """Store lookup table seeded from instore_return_location values."""

    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# 8. cases
# ---------------------------------------------------------------------------


class Case(Base):
    """Agent-generated fraud / triage cases."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    customer_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("customers.customer_id"), nullable=True, index=True
    )
    order_number: Mapped[str | None] = mapped_column(String, nullable=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True)
    item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    triage_outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    triage_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    suspicion_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )
    source_channel: Mapped[str] = mapped_column(String, default="online")

    # relationships
    messages: Mapped[list["CaseMessage"]] = relationship(back_populates="case")
    evidence: Mapped[list["CaseEvidence"]] = relationship(back_populates="case")


# ---------------------------------------------------------------------------
# 9. case_messages
# ---------------------------------------------------------------------------


class CaseMessage(Base):
    """Chat messages attached to a case (user / assistant turns)."""

    __tablename__ = "case_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String, ForeignKey("cases.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )

    case: Mapped["Case"] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# 10. case_evidence
# ---------------------------------------------------------------------------


class CaseEvidence(Base):
    """Files / images attached to a case with optional vision analysis."""

    __tablename__ = "case_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String, ForeignKey("cases.id"), nullable=False, index=True
    )
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    vision_verdict_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )

    case: Mapped["Case"] = relationship(back_populates="evidence")


# ---------------------------------------------------------------------------
# 11. recommendations
# ---------------------------------------------------------------------------


class Recommendation(Base):
    """Agent-proposed product-level recommendations (e.g. size guidance)."""

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("order_items.item_id"), nullable=True, index=True
    )
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    recommendation_type: Mapped[str | None] = mapped_column(String, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String, nullable=True)
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String, default="proposed"
    )  # 'proposed'|'approved'|'applied'|'rejected'|'rolled_back'
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    order_item: Mapped["OrderItem | None"] = relationship(
        back_populates="recommendations"
    )


# ---------------------------------------------------------------------------
# 12. agent_changes
# ---------------------------------------------------------------------------


class AgentChange(Base):
    """Audit log of every automated change the agent proposes or applies."""

    __tablename__ = "agent_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("order_items.item_id"), nullable=True, index=True
    )
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    change_type: Mapped[str | None] = mapped_column(String, nullable=True)
    before_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    order_item: Mapped["OrderItem | None"] = relationship(
        back_populates="agent_changes"
    )


# ---------------------------------------------------------------------------
# 13. unmapped_reasons
# ---------------------------------------------------------------------------


class UnmappedReason(Base):
    """Tracks raw reason values that could not be matched in reason_taxonomy."""

    __tablename__ = "unmapped_reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_value: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
