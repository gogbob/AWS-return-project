"""
engine.py — Insights engine for the ALDO Returns Detective Agent.

Computes reason-code distributions, detects anomalies with Bonferroni-
corrected binomial tests, calculates Jensen-Shannon distances, and writes
recommendations to the database.

Evidence set (applies everywhere):
  - last_return_status IN ('RETURN_COMPLETED', 'RETURN_RECEIVED', 'RETURN_PROCESSED')
  - NOT fraud_flag
  - NOT quarantined (cases table status != 'quarantined' for matching order/sku)
  - Count each customer once per item (most recent return by return_creation_date)
  - Weight: w = 0.5 + loyalty / 100  (range 0.5 – 1.5)
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
try:
    from scipy.stats import binom_test  # type: ignore[import-untyped]  # scipy < 1.11
except ImportError:  # scipy >= 1.11 renamed binom_test → binomtest
    from scipy.stats import binomtest as _binomtest  # type: ignore[import-untyped]

    def binom_test(k: int, n: int, p: float, alternative: str = "greater") -> float:  # type: ignore[misc]
        """Shim: delegate to scipy.stats.binomtest (scipy >= 1.11)."""
        return _binomtest(k, n, p, alternative=alternative).pvalue
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings, CLAIM_RISK_BY_GROUP

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = ("RETURN_COMPLETED", "RETURN_RECEIVED", "RETURN_PROCESSED")

_TAXONOMY_PATH = Path(__file__).parent / "reason_taxonomy.yaml"
_taxonomy_cache: dict | None = None


def _load_taxonomy() -> dict:
    global _taxonomy_cache
    if _taxonomy_cache is None:
        with open(_TAXONOMY_PATH, encoding="utf-8") as fh:
            _taxonomy_cache = yaml.safe_load(fh)
    return _taxonomy_cache


# Normalise a raw reason string for taxonomy lookup
_PUNCT_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _PUNCT_RE.sub(" ", s.strip().lower())


def reason_to_code(raw: str | None) -> str:
    """Map a raw reason string to a canonical code; unmapped → 'other'."""
    taxonomy = _load_taxonomy()
    mappings: dict[str, str] = taxonomy.get("mappings", {})
    key = _norm(raw or "")
    return mappings.get(key, "other")


def code_to_group(code: str) -> str:
    """Return the canonical group for a reason code (e.g. FIT_SIZE)."""
    taxonomy = _load_taxonomy()
    groups: dict[str, list[str]] = taxonomy.get("canonical_groups", {})
    for group, codes in groups.items():
        if code in codes:
            return group
    return "OTHER"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FlagResult:
    reason_code: str
    group: str
    item_share: float          # weighted share in item distribution
    baseline_share: float      # baseline rate for this reason code
    p_value: float             # binomial p-value (before correction)
    p_adj: float               # Bonferroni-corrected p-value
    significant: bool          # p_adj < FLAG_P_ADJ
    excess: float              # item_share - baseline_share
    ratio: float               # item_share / max(baseline_share, 1e-9)
    n: int                     # total observations for the item
    n_eff: float               # effective sample size (sum of weights)
    baseline_scope: str        # 'category' or 'global'


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------

_WEIGHT_SQL = """
    WITH ranked AS (
        SELECT
            r.id,
            r.item_id,
            r.customer_uid,
            r.reason,
            r.refund_amount,
            r.return_creation_date,
            COALESCE(c.loyalty, 0) AS loyalty,
            CASE WHEN c.fraud_flag = 1 THEN 1 ELSE 0 END AS cust_fraud,
            ROW_NUMBER() OVER (
                PARTITION BY r.item_id, r.customer_uid
                ORDER BY r.return_creation_date DESC
            ) AS rn
        FROM returns r
        LEFT JOIN customers c ON c.customer_id = r.customer_uid
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
          AND r.item_id = :item_id
    )
    SELECT
        reason,
        loyalty,
        (0.5 + CAST(loyalty AS REAL) / 100.0) AS weight
    FROM ranked
    WHERE rn = 1
"""


def _item_evidence(item_id: str, db: Session) -> list[dict]:
    """Return one row per customer (most recent return) for the given item.

    The raw ``reason`` text is mapped through ``reason_to_code`` so that
    distributions use fine-grained codes (too_small, too_big, …) rather than
    the coarse GROUP names stored in ``returns.reason_code``.
    """
    rows = db.execute(text(_WEIGHT_SQL), {"item_id": item_id}).fetchall()
    return [{"reason_code": reason_to_code(r[0]), "loyalty": r[1], "weight": r[2]} for r in rows]


_CATEGORY_EVIDENCE_SQL = """
    WITH ranked AS (
        SELECT
            r.id,
            r.item_id,
            r.customer_uid,
            r.reason_code,
            COALESCE(c.loyalty, 0) AS loyalty,
            CASE WHEN c.fraud_flag = 1 THEN 1 ELSE 0 END AS cust_fraud,
            ROW_NUMBER() OVER (
                PARTITION BY r.item_id, r.customer_uid
                ORDER BY r.return_creation_date DESC
            ) AS rn
        FROM returns r
        LEFT JOIN customers c ON c.customer_id = r.customer_uid
        LEFT JOIN order_items oi ON oi.item_id = r.item_id
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
          AND oi.category = :category
    )
    SELECT item_id, reason_code, loyalty, (0.5 + CAST(loyalty AS REAL) / 100.0) AS weight
    FROM ranked
    WHERE rn = 1
"""


def _global_evidence(db: Session) -> list[dict]:
    sql = """
        WITH ranked AS (
            SELECT
                r.item_id,
                r.customer_uid,
                r.reason,
                COALESCE(c.loyalty, 0) AS loyalty,
                ROW_NUMBER() OVER (
                    PARTITION BY r.item_id, r.customer_uid
                    ORDER BY r.return_creation_date DESC
                ) AS rn
            FROM returns r
            LEFT JOIN customers c ON c.customer_id = r.customer_uid
            WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
              AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
              AND COALESCE(c.fraud_flag, 0) = 0
        )
        SELECT reason, loyalty, (0.5 + CAST(loyalty AS REAL) / 100.0) AS weight
        FROM ranked WHERE rn = 1
    """
    rows = db.execute(text(sql)).fetchall()
    return [{"reason_code": reason_to_code(r[0]), "loyalty": r[1], "weight": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Distribution helpers
# ---------------------------------------------------------------------------


def _weighted_distribution(evidence: list[dict]) -> tuple[dict[str, float], int, float]:
    """
    Compute weighted reason-code share from evidence rows.

    Returns:
        shares   {reason_code: weighted_share}  (sums to 1.0)
        n        raw count
        n_eff    sum of weights
    """
    weight_by_code: dict[str, float] = {}
    total_weight = 0.0
    for row in evidence:
        code = row["reason_code"] or "other"
        w = float(row["weight"])
        weight_by_code[code] = weight_by_code.get(code, 0.0) + w
        total_weight += w

    if total_weight == 0:
        return {}, 0, 0.0

    shares = {code: w / total_weight for code, w in weight_by_code.items()}
    return shares, len(evidence), total_weight


# ---------------------------------------------------------------------------
# A. Item distribution
# ---------------------------------------------------------------------------


def compute_item_distribution(item_id: str, db: Session) -> dict:
    """
    Compute weighted reason-code distribution for a single item.

    Returns: {reason_code: share, 'n': count, 'n_eff': effective_n}
    """
    evidence = _item_evidence(item_id, db)
    shares, n, n_eff = _weighted_distribution(evidence)
    result = dict(shares)
    result["n"] = n
    result["n_eff"] = n_eff
    return result


# ---------------------------------------------------------------------------
# B. Category baseline
# ---------------------------------------------------------------------------


def _qualifying_items_in_category(category: str, db: Session) -> list[str]:
    """Return item_ids in this category that meet MIN_RETURNS_BASELINE."""
    sql = """
        SELECT r.item_id, COUNT(DISTINCT r.customer_uid) AS n_customers
        FROM returns r
        LEFT JOIN customers c ON c.customer_id = r.customer_uid
        LEFT JOIN order_items oi ON oi.item_id = r.item_id
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
          AND oi.category = :category
        GROUP BY r.item_id
        HAVING n_customers >= :min_baseline
    """
    rows = db.execute(
        text(sql),
        {"category": category, "min_baseline": settings.min_returns_baseline},
    ).fetchall()
    return [r[0] for r in rows]


# Per-category baseline cache (baselines are static for a given dataset; the
# admin page only reads them). Keyed by category name; cleared when returns
# change via invalidate_baseline_cache().
_BASELINE_CACHE: dict[str, dict] = {}


def invalidate_baseline_cache() -> None:
    """Clear memoized category/global baselines (call after returns change)."""
    _BASELINE_CACHE.clear()


# One query returns the per-(item, reason) weighted totals for an ENTIRE
# category, so we never issue one query per item (the old N+1 that made the
# large "other" category take minutes). The customer-loyalty weight
# (0.5 + loyalty/100) is applied in SQL, matching _weighted_distribution.
_CATEGORY_WEIGHTED_SQL = """
    WITH ranked AS (
        SELECT
            r.item_id,
            r.customer_uid,
            r.reason,
            COALESCE(c.loyalty, 0) AS loyalty,
            ROW_NUMBER() OVER (
                PARTITION BY r.item_id, r.customer_uid
                ORDER BY r.return_creation_date DESC
            ) AS rn
        FROM returns r
        LEFT JOIN customers c ON c.customer_id = r.customer_uid
        LEFT JOIN order_items oi ON oi.item_id = r.item_id
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
          AND oi.category = :category
    )
    SELECT
        item_id,
        reason,
        SUM(0.5 + CAST(loyalty AS REAL) / 100.0) AS weight
    FROM ranked
    WHERE rn = 1
    GROUP BY item_id, reason
"""


def compute_category_baseline(category: str, db: Session) -> dict:
    """
    Per-reason-code median share across all qualifying items in the category,
    renormalised to sum to 1. Result is memoized per category.

    Returns: {reason_code: median_share, 'n_items': count, 'scope': 'category'}
    """
    if category in _BASELINE_CACHE:
        return dict(_BASELINE_CACHE[category])

    qualifying = set(_qualifying_items_in_category(category, db))

    if len(qualifying) < 10:
        result = compute_global_baseline(db)
        result["scope"] = "global"
        result["n_items"] = len(qualifying)
        _BASELINE_CACHE[category] = dict(result)
        return result

    # Pull the whole category's per-item weighted reason totals in ONE query,
    # then build each qualifying item's share vector in Python (no per-item SQL).
    rows = db.execute(text(_CATEGORY_WEIGHTED_SQL), {"category": category}).fetchall()

    # Accumulate weights per (item_id, granular_code) in Python — the raw
    # reason is mapped through reason_to_code() so share vectors use the
    # fine-grained codes rather than the coarse GROUP names.
    totals_by_item: dict[str, dict[str, float]] = {}
    for item_id, reason, weight in rows:
        if item_id not in qualifying:
            continue
        code = reason_to_code(reason)
        item_totals = totals_by_item.setdefault(item_id, {})
        item_totals[code] = item_totals.get(code, 0.0) + float(weight)

    item_shares: list[dict[str, float]] = []
    for totals in totals_by_item.values():
        total_w = sum(totals.values())
        if total_w > 0:
            item_shares.append({code: w / total_w for code, w in totals.items()})

    if not item_shares:
        result = compute_global_baseline(db)
        result["scope"] = "global"
        result["n_items"] = 0
        _BASELINE_CACHE[category] = dict(result)
        return result

    all_codes = set()
    for s in item_shares:
        all_codes.update(s.keys())

    import statistics

    medians: dict[str, float] = {}
    for code in all_codes:
        vals = [s.get(code, 0.0) for s in item_shares]
        medians[code] = statistics.median(vals)

    total = sum(medians.values())
    if total > 0:
        medians = {code: v / total for code, v in medians.items()}

    medians["n_items"] = len(item_shares)
    medians["scope"] = "category"
    _BASELINE_CACHE[category] = dict(medians)
    return medians


# ---------------------------------------------------------------------------
# C. Global baseline
# ---------------------------------------------------------------------------


def compute_global_baseline(db: Session) -> dict:
    """
    Compute weighted reason-code distribution across all qualifying evidence.
    Memoized (keyed under "__global__" in the baseline cache).

    Returns: {reason_code: share, 'n': count, 'n_eff': n_eff, 'scope': 'global'}
    """
    cached = _BASELINE_CACHE.get("__global__")
    if cached is not None:
        return dict(cached)

    evidence = _global_evidence(db)
    shares, n, n_eff = _weighted_distribution(evidence)
    result = dict(shares)
    result["n"] = n
    result["n_eff"] = n_eff
    result["scope"] = "global"
    _BASELINE_CACHE["__global__"] = dict(result)
    return result


# ---------------------------------------------------------------------------
# D. Anomaly detection — binomial + Bonferroni
# ---------------------------------------------------------------------------


def compute_anomaly(item_id: str, db: Session) -> list[FlagResult]:
    """
    For each reason code in the item distribution, test whether its share
    is significantly higher than the baseline using a one-sided binomial test.
    Apply Bonferroni correction across all tested codes.

    Flags are only raised when:
      - n >= MIN_RETURNS_FLAG
      - unique customers >= MIN_CUSTOMERS_FLAG
      - excess >= FLAG_MIN_EXCESS
      - ratio >= FLAG_MIN_RATIO
      - p_adj < FLAG_P_ADJ

    Returns a list of FlagResult (significant or not) for all tested codes.
    """
    # Check minimum evidence thresholds
    n_cust_sql = """
        SELECT COUNT(DISTINCT customer_uid)
        FROM returns
        WHERE item_id = :item_id
          AND last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (fraud_flag = 0 OR fraud_flag IS NULL)
    """
    n_customers = db.execute(text(n_cust_sql), {"item_id": item_id}).scalar() or 0

    evidence = _item_evidence(item_id, db)
    n = len(evidence)

    if n < settings.min_returns_flag or n_customers < settings.min_customers_flag:
        return []

    item_dist = _weighted_distribution(evidence)
    shares, _, n_eff = item_dist

    # Determine baseline
    # Get category for this item
    cat_row = db.execute(
        text("SELECT category FROM order_items WHERE item_id = :iid"),
        {"iid": item_id},
    ).fetchone()
    category = cat_row[0] if cat_row else None

    if category:
        baseline = compute_category_baseline(category, db)
        baseline_scope = baseline.get("scope", "category")
    else:
        baseline = compute_global_baseline(db)
        baseline_scope = "global"

    all_codes = set(shares.keys()) | {
        k for k in baseline if isinstance(k, str) and k not in ("n", "n_eff", "scope", "n_items")
    }

    # Compute raw p-values
    raw_results: list[tuple[str, float, float, float, float, float]] = []
    for code in all_codes:
        item_share = shares.get(code, 0.0)
        b_r = float(baseline.get(code, 0.0))

        # Effective integer count for the binomial test
        k = round(item_share * n)

        # One-sided binomial test: H1 = item rate > baseline rate
        p_val = binom_test(k, n, max(b_r, 1e-9), alternative="greater")

        excess = item_share - b_r
        ratio = item_share / max(b_r, 1e-9)
        raw_results.append((code, item_share, b_r, p_val, excess, ratio))

    # Bonferroni correction
    m = len(raw_results)
    flags: list[FlagResult] = []
    for code, item_share, b_r, p_val, excess, ratio in raw_results:
        p_adj = min(p_val * m, 1.0)
        significant = (
            p_adj < settings.flag_p_adj
            and excess >= settings.flag_min_excess
            and ratio >= settings.flag_min_ratio
        )
        flags.append(
            FlagResult(
                reason_code=code,
                group=code_to_group(code),
                item_share=item_share,
                baseline_share=b_r,
                p_value=p_val,
                p_adj=p_adj,
                significant=significant,
                excess=excess,
                ratio=ratio,
                n=n,
                n_eff=n_eff,
                baseline_scope=baseline_scope,
            )
        )

    return flags


# ---------------------------------------------------------------------------
# E. Jensen-Shannon distance
# ---------------------------------------------------------------------------


def compute_js_distance(dist_a: dict, dist_b: dict) -> float:
    """
    Compute the Jensen-Shannon distance between two reason-code distributions.

    Both dicts map reason_code → share (floats, should sum ≈ 1).
    Metadata keys ('n', 'n_eff', 'scope', 'n_items') are ignored.

    Returns a float in [0, 1].
    """
    _meta = {"n", "n_eff", "scope", "n_items"}
    codes = (set(dist_a) | set(dist_b)) - _meta
    if not codes:
        return 0.0

    def _share(d: dict, code: str) -> float:
        v = d.get(code, 0.0)
        return float(v) if isinstance(v, (int, float)) else 0.0

    # Build normalised probability vectors
    sum_a = sum(_share(dist_a, c) for c in codes)
    sum_b = sum(_share(dist_b, c) for c in codes)
    if sum_a == 0 or sum_b == 0:
        return 1.0 if (sum_a == 0) != (sum_b == 0) else 0.0

    p = [_share(dist_a, c) / sum_a for c in codes]
    q = [_share(dist_b, c) / sum_b for c in codes]

    # M = (P + Q) / 2
    m = [(pi + qi) / 2.0 for pi, qi in zip(p, q)]

    def _kl(a: list[float], b: list[float]) -> float:
        total = 0.0
        for ai, bi in zip(a, b):
            if ai > 0 and bi > 0:
                total += ai * math.log2(ai / bi)
        return total

    jsd = (_kl(p, m) + _kl(q, m)) / 2.0
    # JSD is in [0, 1] in bits; sqrt gives distance in [0, 1]
    return math.sqrt(max(0.0, jsd))


# ---------------------------------------------------------------------------
# F. Recommendations
# ---------------------------------------------------------------------------


def generate_recommendations(item_id: str, db: Session) -> None:
    """
    Generate size-guidance or description-fix recommendations for *item_id*
    based on the anomaly results and write them to the recommendations table.

    Only generates a recommendation if there is at least one significant flag.
    Skips if a 'proposed' recommendation for the same item/reason already exists.
    """
    flags = compute_anomaly(item_id, db)
    significant_flags = [f for f in flags if f.significant]
    if not significant_flags:
        return

    # Pick the highest-excess significant flag as the primary signal
    top = max(significant_flags, key=lambda f: f.excess)

    # Check for duplicate
    existing = db.execute(
        text(
            "SELECT id FROM recommendations "
            "WHERE item_id = :iid AND reason_code = :rc AND status = 'proposed'"
        ),
        {"iid": item_id, "rc": top.reason_code},
    ).fetchone()
    if existing:
        return

    # Determine recommendation type and draft text
    group = top.group
    if group == "FIT_SIZE":
        rec_type = "size_guidance"
        draft = (
            f"This item has an elevated return rate for fit/size issues "
            f"({top.item_share:.0%} vs baseline {top.baseline_share:.0%}). "
            f"Consider adding a size guide or reviewing the size chart."
        )
    elif group == "DESCRIPTION":
        rec_type = "description_update"
        draft = (
            f"This item has an elevated return rate for description mismatch "
            f"({top.item_share:.0%} vs baseline {top.baseline_share:.0%}). "
            f"Review product images and description for accuracy."
        )
    elif group == "DEFECT":
        rec_type = "quality_review"
        draft = (
            f"This item has an elevated defect return rate "
            f"({top.item_share:.0%} vs baseline {top.baseline_share:.0%}). "
            f"Escalate to quality assurance."
        )
    else:
        rec_type = "review"
        draft = (
            f"This item has an elevated return rate for '{top.reason_code}' "
            f"({top.item_share:.0%} vs baseline {top.baseline_share:.0%}). "
            f"Manual review recommended."
        )

    # Get category
    cat_row = db.execute(
        text("SELECT category FROM order_items WHERE item_id = :iid"),
        {"iid": item_id},
    ).fetchone()
    category = cat_row[0] if cat_row else None

    db.execute(
        text(
            "INSERT INTO recommendations "
            "(item_id, category, recommendation_type, reason_code, draft_text, confidence, status, created_at, updated_at) "
            "VALUES (:item_id, :category, :rec_type, :reason_code, :draft_text, :confidence, 'proposed', :now, :now)"
        ),
        {
            "item_id": item_id,
            "category": category,
            "rec_type": rec_type,
            "reason_code": top.reason_code,
            "draft_text": draft,
            "confidence": 1.0 - top.p_adj,
            "now": datetime.utcnow(),
        },
    )
    db.commit()


# ---------------------------------------------------------------------------
# G. Recompute helpers
# ---------------------------------------------------------------------------

# Counter for triggering category baseline recomputation
_live_return_counter = 0
_RECOMPUTE_EVERY = 50


def recompute_item(item_id: str, db: Session) -> None:
    """
    Called after each live return is ingested.

    Recomputes the item distribution, runs anomaly detection, and stores
    an evidence snapshot in agent_changes. Also triggers category-level
    recomputation every _RECOMPUTE_EVERY live returns.
    """
    global _live_return_counter

    flags = compute_anomaly(item_id, db)
    item_dist = compute_item_distribution(item_id, db)

    # Determine baseline scope
    cat_row = db.execute(
        text("SELECT category FROM order_items WHERE item_id = :iid"),
        {"iid": item_id},
    ).fetchone()
    category = cat_row[0] if cat_row else None

    if category:
        baseline = compute_category_baseline(category, db)
    else:
        baseline = compute_global_baseline(db)

    baseline_scope = baseline.get("scope", "global")

    # Compute adjusted p_adj for the snapshot (use min of flags or 1.0)
    min_p_adj = min((f.p_adj for f in flags), default=1.0)
    confidence = 1.0 - min_p_adj

    shares = {k: v for k, v in item_dist.items() if k not in ("n", "n_eff")}

    snapshot = {
        "n": item_dist.get("n", 0),
        "n_eff": item_dist.get("n_eff", 0.0),
        "shares": shares,
        "baseline": {k: v for k, v in baseline.items() if k not in ("n", "n_eff", "scope", "n_items")},
        "p_adj": min_p_adj,
        "confidence": confidence,
        "baseline_scope": baseline_scope,
    }

    db.execute(
        text(
            "INSERT INTO agent_changes "
            "(item_id, category, change_type, evidence_snapshot_json, status, created_at) "
            "VALUES (:item_id, :category, 'insight_recompute', :snapshot, 'pending', :now)"
        ),
        {
            "item_id": item_id,
            "category": category,
            "snapshot": json.dumps(snapshot),
            "now": datetime.utcnow(),
        },
    )
    db.commit()

    generate_recommendations(item_id, db)

    _live_return_counter += 1
    if _live_return_counter % _RECOMPUTE_EVERY == 0:
        recompute_category_baselines(db)


def recompute_category_baselines(db: Session) -> None:
    """
    Recompute baselines for all categories and update agent_changes.
    Called every _RECOMPUTE_EVERY live returns.
    """
    cat_rows = db.execute(
        text("SELECT DISTINCT category FROM order_items WHERE category IS NOT NULL")
    ).fetchall()

    for (category,) in cat_rows:
        baseline = compute_category_baseline(category, db)
        snapshot = {
            "baseline": {k: v for k, v in baseline.items() if k not in ("n_items", "scope")},
            "scope": baseline.get("scope", "category"),
            "n_items": baseline.get("n_items", 0),
        }
        db.execute(
            text(
                "INSERT INTO agent_changes "
                "(category, change_type, evidence_snapshot_json, status, created_at) "
                "VALUES (:category, 'baseline_recompute', :snapshot, 'pending', :now)"
            ),
            {
                "category": category,
                "snapshot": json.dumps(snapshot),
                "now": datetime.utcnow(),
            },
        )
    db.commit()
