"""
run.py — Idempotent data ingestion script for the ALDO Returns Detective Agent.

Run from the backend/ directory:
    python -m app.ingest.run

What it does (in order):
 1.  Drop and recreate all 13 tables (idempotent).
 2.  Read OMS CSV (File 4) — normalise headers, parse dates, apply aliases.
 3.  Read Restocking Fees CSV (File 1) — normalise and join on return_order_number.
 4.  Read Returnable Flags CSV (File 2) — normalise and join on (order_number, sku).
 5.  Read Exchanges CSV (File 3) — normalise and join on order_number.
 6.  Derive item_id via ITEM_KEY config (default: orderitem_product_name).
 7.  Derive category via category_keywords.yaml.
 8.  Map reason → reason_code via reason_taxonomy.yaml. Record unmapped values.
 9.  Populate all 13 tables with upserts where applicable.
10.  Generate customers with seeded loyalty scores.
11.  Generate data/stores.json and populate the stores table.
12.  Print summary: row counts per table, unmapped reasons.

Column names follow docs/data_profile.md — the authoritative source of truth.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

# backend/app/ingest/ → backend/ → project root
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_DIR = PROJECT_ROOT / "data"
INGEST_DIR = Path(__file__).resolve().parent

# Raw CSV filenames (exact, as documented in data_profile.md)
OMS_CSV = DATA_RAW / "OMS Data_Feb24-Jan26_new.csv"
RESTOCKING_CSV = DATA_RAW / "McGill - Returns - Restocking Fees 2024 - 2025.csv"
RETURNABLE_CSV = DATA_RAW / "McGill - Returns - Returnable Flag 2024-2025.csv"
EXCHANGES_CSV = DATA_RAW / "McGill_Instore_return_exchanges.csv"

CATEGORY_YAML = INGEST_DIR / "category_keywords.yaml"
REASON_YAML = INGEST_DIR / "reason_taxonomy.yaml"
STORES_JSON = DATA_DIR / "stores.json"

# ---------------------------------------------------------------------------
# Import app modules (must be importable with `python -m app.ingest.run`
# from the backend/ directory, so sys.path is already correct).
# ---------------------------------------------------------------------------

from app.db import Base, engine  # noqa: E402  (after path setup)
from app.config import settings  # noqa: E402

from .models import (  # noqa: E402
    AgentChange,
    Case,
    CaseEvidence,
    CaseMessage,
    Customer,
    Exchange,
    OrderItem,
    Recommendation,
    RestockingFee,
    Return,
    ReturnableFlag,
    Store,
    UnmappedReason,
)

# ---------------------------------------------------------------------------
# Normalisation helpers (mirror docs/data_profile.md § Normalisation Rules)
# ---------------------------------------------------------------------------

# Characters to replace with underscore
_PUNCT_RE = re.compile(r"[\s\-/?(). ]+")
_MULTI_UNDER = re.compile(r"_+")

# Alias overrides from data_profile.md
HEADER_ALIASES: dict[str, str] = {
    "return_oder_number": "return_order_number",   # typo in older exports
    "return_creation_data": "return_creation_date",  # typo in older exports
    "is_item_final_sale_": "is_final_sale",          # trailing _ from '?' (older form)
    "is_item_final_sale": "is_final_sale",           # stripped trailing _ form
}


def _normalise_col(raw: str) -> str:
    """Apply the full normalisation pipeline to a single column name."""
    s = raw.strip().lower()
    s = _PUNCT_RE.sub("_", s)
    s = _MULTI_UNDER.sub("_", s)
    s = s.strip("_")
    return HEADER_ALIASES.get(s, s)


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename all columns of *df* using the normalisation pipeline."""
    df.columns = [_normalise_col(c) for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# Category keyword matching
# ---------------------------------------------------------------------------


def _load_category_keywords() -> list[tuple[str, list[str]]]:
    """Return ordered list of (category, [keywords]) from category_keywords.yaml."""
    with open(CATEGORY_YAML, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    # preserve YAML order; returns list of (cat, keywords)
    return [(cat, [kw.lower() for kw in kws]) for cat, kws in data.items()]


_CATEGORY_RULES: list[tuple[str, list[str]]] = []  # populated once at runtime


def derive_category(item_id: str | None) -> str:
    """Return first matching category keyword rule, or 'other'."""
    global _CATEGORY_RULES
    if not _CATEGORY_RULES:
        _CATEGORY_RULES = _load_category_keywords()
    if not item_id:
        return "other"
    name_lower = item_id.lower()
    for category, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if kw in name_lower:
                return category
    return "other"


# ---------------------------------------------------------------------------
# Reason taxonomy mapping
# ---------------------------------------------------------------------------


def _load_reason_taxonomy() -> dict[str, dict[str, str]]:
    """Return {normalised_reason: {code, canonical}} from reason_taxonomy.yaml."""
    with open(REASON_YAML, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    mappings = data.get("mappings", {})
    # normalise keys: strip, lowercase
    return {k.strip().lower(): v for k, v in mappings.items()}


_REASON_MAP: dict[str, dict[str, str]] = {}  # populated once at runtime


def map_reason(raw_reason: str | float | None) -> tuple[str, bool]:
    """
    Map a raw reason string to its reason_code.

    Returns (reason_code, was_mapped).
    Unmapped values receive code 'UNKNOWN'.
    Handles pandas NaN (float) gracefully.
    """
    global _REASON_MAP
    if not _REASON_MAP:
        _REASON_MAP = _load_reason_taxonomy()
    # NaN from pandas arrives as a float — treat as empty string
    if raw_reason is None or (isinstance(raw_reason, float) and raw_reason != raw_reason):
        key = ""
    else:
        key = str(raw_reason).strip().lower()
    entry = _REASON_MAP.get(key)
    if entry:
        return entry["code"], True
    return "UNKNOWN", False


# ---------------------------------------------------------------------------
# Loyalty score generation
# ---------------------------------------------------------------------------

LOYALTY_SEED = settings.loyalty_seed  # default 42


def _loyalty_score(customer_uid: str, seed: int = LOYALTY_SEED) -> int:
    """
    Generate a deterministic loyalty score (0–100) for *customer_uid*.

    Distribution target (per spec):
      ~30% high   (score ≥ 70)
      ~40% medium (score 30–69)
      ~30% low    (score < 30)

    Uses a seeded hash so the same UID always produces the same score.
    """
    digest = hashlib.md5(f"{seed}:{customer_uid}".encode()).hexdigest()
    # use first 8 hex digits → integer 0–4294967295
    raw = int(digest[:8], 16)
    percentile = (raw % 10000) / 100.0  # 0.0–99.99
    if percentile >= 70.0:
        # top 30% → score 70–100
        return int(70 + (percentile - 70.0) / 30.0 * 30)
    elif percentile >= 30.0:
        # middle 40% → score 30–69
        return int(30 + (percentile - 30.0) / 40.0 * 40)
    else:
        # bottom 30% → score 0–29
        return int(percentile / 30.0 * 30)


# ---------------------------------------------------------------------------
# Upsert helpers (SQLite INSERT OR REPLACE / INSERT OR IGNORE)
# SQLite has a max-variable limit (default 999); batch rows to stay under it.
# ---------------------------------------------------------------------------

# Maximum rows per upsert batch. With 5 columns × 190 rows = 950 variables
# (safely under the 999 limit). Adjust per model column count.
_SQLITE_VAR_LIMIT = 999


def _batch_upsert(session, model_class, rows: list[dict], index_elements: list[str], set_keys: list[str], batch_cols: int) -> int:
    """Generic batched upsert for SQLite. Returns total rows processed."""
    if not rows:
        return 0
    batch_size = max(1, _SQLITE_VAR_LIMIT // batch_cols)
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        stmt = sqlite_insert(model_class).values(batch)
        update_dict = {k: getattr(stmt.excluded, k) for k in set_keys}
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_=update_dict,
        )
        session.execute(stmt)
    return len(rows)


def _upsert_order_items(session, rows: list[dict]) -> int:
    """Upsert order_items by item_id (5 columns)."""
    return _batch_upsert(
        session, OrderItem, rows,
        index_elements=["item_id"],
        set_keys=["sku", "orderitem_product_name", "category", "store_banner"],
        batch_cols=5,
    )


def _upsert_customers(session, rows: list[dict]) -> int:
    """Upsert customers by customer_id (4 columns)."""
    return _batch_upsert(
        session, Customer, rows,
        index_elements=["customer_id"],
        set_keys=["cnsmr_prof_sid", "loyalty"],
        batch_cols=4,
    )


def _upsert_restocking_fees(session, rows: list[dict]) -> int:
    """Upsert restocking_fees by return_order_number (2 columns)."""
    return _batch_upsert(
        session, RestockingFee, rows,
        index_elements=["return_order_number"],
        set_keys=["restocking_revenue"],
        batch_cols=2,
    )


def _upsert_unmapped_reasons(session, counts: dict[str, dict]) -> None:
    """Insert / update unmapped reason counts (3 columns, one row at a time is fine)."""
    rows = [
        {
            "raw_value": raw_val,
            "count": info["count"],
            "first_seen": info["first_seen"],
        }
        for raw_val, info in counts.items()
    ]
    _batch_upsert(
        session, UnmappedReason, rows,
        index_elements=["raw_value"],
        set_keys=["count"],
        batch_cols=3,
    )


# ---------------------------------------------------------------------------
# Read helpers with chunk support for large files
# ---------------------------------------------------------------------------

CHUNK_SIZE = 100_000  # rows per chunk for large files


def _read_csv_chunked(path: Path, **kwargs) -> pd.DataFrame:
    """Read a CSV in chunks and return a single concatenated DataFrame."""
    print(f"  Reading {path.name} …", flush=True)
    chunks = []
    for chunk in pd.read_csv(path, chunksize=CHUNK_SIZE, dtype=str, **kwargs):
        chunk = normalise_columns(chunk)
        chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    print(f"    → {len(df):,} rows, columns: {list(df.columns)}")
    return df


# ---------------------------------------------------------------------------
# Main ingestion
# ---------------------------------------------------------------------------


def run() -> None:
    # ── Step 1: Drop and recreate all tables ─────────────────────────────────
    print("\n[1/12] Recreating database schema …")
    # Import models so their metadata is registered with Base
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("  Tables created:", sorted(Base.metadata.tables.keys()))

    # ── Step 2: Read OMS CSV ──────────────────────────────────────────────────
    print("\n[2/12] Reading OMS Returns data (File 4) …")
    oms = _read_csv_chunked(OMS_CSV)

    # Verify expected columns exist
    OMS_EXPECTED = {
        "store_banner", "order_number", "sku", "orderitem_product_name",
        "cnsmr_prof_sid", "customer_uid", "return_order_number",
        "return_channel", "return_creation_date", "return_creation_date_tmstmp",
        "last_return_status", "refund_amount", "item_return_qty",
        "reason", "instore_return_location",
    }
    missing = OMS_EXPECTED - set(oms.columns)
    if missing:
        print(f"  WARNING: Expected OMS columns not found: {missing}")
        print(f"  Actual columns: {list(oms.columns)}")

    # Parse return_creation_date (format varies; let pandas infer)
    oms["return_creation_date"] = pd.to_datetime(
        oms.get("return_creation_date"), errors="coerce"
    )

    # Numeric coercions
    oms["refund_amount"] = pd.to_numeric(oms.get("refund_amount"), errors="coerce")
    oms["item_return_qty"] = pd.to_numeric(oms.get("item_return_qty"), errors="coerce")

    # ── Step 3: Read Restocking Fees ──────────────────────────────────────────
    print("\n[3/12] Reading Restocking Fees (File 1) …")
    restocking = _read_csv_chunked(RESTOCKING_CSV)
    restocking["restocking_revenue"] = pd.to_numeric(
        restocking.get("restocking_revenue"), errors="coerce"
    )

    # ── Step 4: Read Returnable Flags ─────────────────────────────────────────
    print("\n[4/12] Reading Returnable Flags (File 2) …")
    returnable = _read_csv_chunked(RETURNABLE_CSV)

    # ── Step 5: Read Exchanges ────────────────────────────────────────────────
    print("\n[5/12] Reading Exchanges (File 3) …")
    exchanges = _read_csv_chunked(EXCHANGES_CSV)
    # File 3 uses 'original_order_number' → rename to 'order_number' for joins
    if "original_order_number" in exchanges.columns:
        exchanges = exchanges.rename(columns={"original_order_number": "order_number"})
    # File 3 uses 'transactions_businessdaydate' → 'business_day_date'
    if "transactions_businessdaydate" in exchanges.columns:
        exchanges = exchanges.rename(
            columns={"transactions_businessdaydate": "business_day_date"}
        )
    # File 3 uses 'transactions_transnumber' → 'trans_number'
    if "transactions_transnumber" in exchanges.columns:
        exchanges = exchanges.rename(
            columns={"transactions_transnumber": "trans_number"}
        )
    exchanges["exchange_qty"] = pd.to_numeric(
        exchanges.get("exchange_qty"), errors="coerce"
    )
    exchanges["sales"] = pd.to_numeric(exchanges.get("sales"), errors="coerce")

    # ── Step 6: Derive item_id ────────────────────────────────────────────────
    print("\n[6/12] Deriving item_id …")
    item_key = settings.item_key  # 'name' or 'sku'
    if item_key == "sku":
        oms["item_id"] = oms["sku"].fillna("").str.strip()
    else:
        # Default: use orderitem_product_name (style-level key per data profile)
        oms["item_id"] = oms["orderitem_product_name"].fillna("").str.strip()

    print(f"  ITEM_KEY={item_key!r}, unique item_ids: {oms['item_id'].nunique():,}")

    # ── Step 7: Derive category ───────────────────────────────────────────────
    print("\n[7/12] Deriving product category …")
    unique_items = oms[["item_id", "sku", "orderitem_product_name", "store_banner"]].drop_duplicates(
        subset=["item_id"]
    )
    unique_items = unique_items.copy()
    unique_items["category"] = unique_items["item_id"].apply(derive_category)
    cat_dist = unique_items["category"].value_counts().to_dict()
    print(f"  Category distribution: {cat_dist}")

    # ── Step 8: Reason mapping ────────────────────────────────────────────────
    print("\n[8/12] Mapping reason → reason_code …")
    unmapped_counts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "first_seen": None}
    )

    def _map_and_track(raw_reason: str | None) -> str:
        code, mapped = map_reason(raw_reason)
        if not mapped:
            key = (raw_reason or "").strip().lower()
            unmapped_counts[key]["count"] += 1
            if unmapped_counts[key]["first_seen"] is None:
                unmapped_counts[key]["first_seen"] = datetime.utcnow()
        return code

    oms["reason_code"] = oms["reason"].apply(_map_and_track)
    print(
        f"  Mapped: {(oms['reason_code'] != 'UNKNOWN').sum():,} | "
        f"Unmapped: {(oms['reason_code'] == 'UNKNOWN').sum():,}"
    )

    # ── Step 9: Populate tables ───────────────────────────────────────────────
    print("\n[9/12] Populating database tables …")

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        # 9a. order_items
        print("  → order_items …")
        oi_rows = unique_items.rename(
            columns={"sku": "sku", "orderitem_product_name": "orderitem_product_name"}
        ).to_dict("records")
        # keep only the columns the model expects
        oi_rows = [
            {
                "item_id": r["item_id"],
                "sku": r.get("sku"),
                "orderitem_product_name": r.get("orderitem_product_name"),
                "category": r.get("category"),
                "store_banner": r.get("store_banner"),
            }
            for r in oi_rows
        ]
        _upsert_order_items(session, oi_rows)
        session.flush()

        # 9b. returns
        print("  → returns …")
        returns_rows = []
        for _, row in oms.iterrows():
            returns_rows.append(
                {
                    "order_number": row.get("order_number"),
                    "sku": row.get("sku"),
                    "item_id": row.get("item_id") or None,
                    "customer_uid": row.get("customer_uid"),
                    "return_order_number": row.get("return_order_number"),
                    "return_channel": row.get("return_channel"),
                    "return_creation_date": row.get("return_creation_date")
                    if pd.notna(row.get("return_creation_date"))
                    else None,
                    "last_return_status": row.get("last_return_status"),
                    "refund_amount": row.get("refund_amount")
                    if pd.notna(row.get("refund_amount", float("nan")))
                    else None,
                    "item_return_qty": int(row["item_return_qty"])
                    if pd.notna(row.get("item_return_qty", float("nan")))
                    else None,
                    "reason": row.get("reason"),
                    "reason_code": row.get("reason_code"),
                    "source": "csv",
                    "fraud_flag": False,
                    "instore_return_location": row.get("instore_return_location"),
                }
            )
            # bulk insert in batches to keep memory manageable
            if len(returns_rows) >= CHUNK_SIZE:
                session.bulk_insert_mappings(Return, returns_rows)
                session.flush()
                returns_rows = []
        if returns_rows:
            session.bulk_insert_mappings(Return, returns_rows)
            session.flush()

        # 9c. returnable_flags
        print("  → returnable_flags …")
        rf_rows = []
        for _, row in returnable.iterrows():
            is_fs_raw = str(row.get("is_final_sale", "no")).strip().lower()
            rf_rows.append(
                {
                    "order_number": row.get("order_number"),
                    "sku": row.get("sku"),
                    "is_final_sale": is_fs_raw == "yes",
                }
            )
            if len(rf_rows) >= CHUNK_SIZE:
                session.bulk_insert_mappings(ReturnableFlag, rf_rows)
                session.flush()
                rf_rows = []
        if rf_rows:
            session.bulk_insert_mappings(ReturnableFlag, rf_rows)
            session.flush()

        # 9d. restocking_fees
        print("  → restocking_fees …")
        rst_rows = [
            {
                "return_order_number": row.get("return_order_number"),
                "restocking_revenue": row.get("restocking_revenue")
                if pd.notna(row.get("restocking_revenue", float("nan")))
                else None,
            }
            for _, row in restocking.iterrows()
            if row.get("return_order_number")
        ]
        _upsert_restocking_fees(session, rst_rows)
        session.flush()

        # 9e. exchanges
        print("  → exchanges …")
        ex_rows = []
        for _, row in exchanges.iterrows():
            ex_rows.append(
                {
                    "order_number": row.get("order_number"),
                    "exchange_sku": row.get("exchange_sku"),
                    "exchange_qty": int(row["exchange_qty"])
                    if pd.notna(row.get("exchange_qty", float("nan")))
                    else None,
                    "exchange_confidence": row.get("exchange_confidence"),
                    "business_day_date": row.get("business_day_date"),
                    "trans_number": row.get("trans_number"),
                    "return_store": row.get("return_store"),
                    "sales": row.get("sales")
                    if pd.notna(row.get("sales", float("nan")))
                    else None,
                }
            )
            if len(ex_rows) >= CHUNK_SIZE:
                session.bulk_insert_mappings(Exchange, ex_rows)
                session.flush()
                ex_rows = []
        if ex_rows:
            session.bulk_insert_mappings(Exchange, ex_rows)
            session.flush()

        # ── Step 10: customers with seeded loyalty ────────────────────────────
        print("\n[10/12] Building customers table …")
        customer_rows_oms = oms[["customer_uid", "cnsmr_prof_sid"]].dropna(
            subset=["customer_uid"]
        ).drop_duplicates(subset=["customer_uid"])
        cust_rows = []
        for _, row in customer_rows_oms.iterrows():
            uid = str(row["customer_uid"]).strip()
            cust_rows.append(
                {
                    "customer_id": uid,
                    "cnsmr_prof_sid": row.get("cnsmr_prof_sid"),
                    "loyalty": _loyalty_score(uid, LOYALTY_SEED),
                    "fraud_flag": False,
                }
            )
        print(f"  Unique customers: {len(cust_rows):,}")
        _upsert_customers(session, cust_rows)
        session.flush()

        # ── Step 11: stores ───────────────────────────────────────────────────
        print("\n[11/12] Building stores …")
        loc_col = "instore_return_location"
        store_col_ex = "return_store"

        store_ids: set[str] = set()
        if loc_col in oms.columns:
            store_ids.update(
                oms[loc_col].dropna().str.strip().unique().tolist()
            )
        if store_col_ex in exchanges.columns:
            store_ids.update(
                exchanges[store_col_ex].dropna().str.strip().unique().tolist()
            )
        # Remove empty strings
        store_ids.discard("")

        print(f"  Unique store IDs: {len(store_ids):,}")

        stores_list = [
            {
                "name": sid,
                "address": None,
                "lat": None,
                "lng": None,
                "verified": False,
            }
            for sid in sorted(store_ids, key=lambda x: x.zfill(10))
        ]

        # Write stores.json
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(STORES_JSON, "w", encoding="utf-8") as fh:
            json.dump(stores_list, fh, indent=2)
        print(f"  Written: {STORES_JSON}")

        # Insert into stores table
        session.bulk_insert_mappings(Store, stores_list)
        session.flush()

        # ── Unmapped reasons ──────────────────────────────────────────────────
        print("\n  → unmapped_reasons …")
        _upsert_unmapped_reasons(session, unmapped_counts)
        session.flush()

        session.commit()

    # ── Step 12: Summary ──────────────────────────────────────────────────────
    print("\n[12/12] Ingestion summary")
    print("=" * 60)

    table_models = [
        ("order_items", OrderItem),
        ("returns", Return),
        ("returnable_flags", ReturnableFlag),
        ("restocking_fees", RestockingFee),
        ("exchanges", Exchange),
        ("customers", Customer),
        ("stores", Store),
        ("cases", Case),
        ("case_messages", CaseMessage),
        ("case_evidence", CaseEvidence),
        ("recommendations", Recommendation),
        ("agent_changes", AgentChange),
        ("unmapped_reasons", UnmappedReason),
    ]

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        for tname, model in table_models:
            count = session.execute(
                text(f"SELECT COUNT(*) FROM {tname}")
            ).scalar()
            print(f"  {tname:<30} {count:>10,} rows")

        print()
        print("  Unmapped reason values:")
        unmapped_rows = session.execute(
            text("SELECT raw_value, count FROM unmapped_reasons ORDER BY count DESC LIMIT 20")
        ).fetchall()
        if unmapped_rows:
            for raw_val, cnt in unmapped_rows:
                print(f"    {cnt:>6,}x  {raw_val!r}")
        else:
            print("    (none)")

    print("\nDone.")


if __name__ == "__main__":
    run()
