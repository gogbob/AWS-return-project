"""
sources.py — Baseline data-source abstraction for the hybrid data layer.

ONLY the category and global reason baselines are routed through the sources
defined here. The per-item paths — compute_item_distribution, compute_anomaly,
_item_evidence and _build_item_insight — as well as the owner-return
auto-apply recompute stay on SQLite via engine.py so they always reflect the
live rows (live/checkout returns) the instant they are written. The Quincey /
owner "too_small" overshoot demo depends on that live per-item path, so it is
deliberately NOT touched here.

The Parquet / Athena datasets hold ONLY the historical source='csv' returns
(see app/ingest/to_parquet.py). That historical slice IS the intended baseline
reference distribution: the reason-code shares a "normal" item/category should
look like. Because the live rows are a handful of demo returns on top of
~650k historical ones, routing baselines through the historical Parquet gives
numerically equivalent baselines (verified: DuckDB global baseline matches the
SQLite global baseline within 1e-6) while letting Athena/DuckDB carry the heavy
scans off the local SQLite file.

Selection order under data_backend='auto': Athena (if AWS creds work and the
Glue tables are registered) -> DuckDB (if the local returns Parquet exists) ->
SQLite (always-correct reference). select_baseline_source() never raises; the
individual sources raise typed errors so _warm_insight_baselines() can fall
back down the chain.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from ..config import settings
from . import engine as _engine
from .engine import reason_to_code

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BaselineSourceError(RuntimeError):
    """Raised when a baseline source cannot produce a result.

    The selector/fallback in _warm_insight_baselines() catches this (and any
    other Exception) to fall back down the athena -> duckdb -> sqlite chain.
    """


# ---------------------------------------------------------------------------
# Shared Python aggregation (mirrors engine.compute_category_baseline logic)
# ---------------------------------------------------------------------------


def _shares_from_weighted_rows(rows: list[tuple[str, float]]) -> tuple[dict[str, float], int, float]:
    """Build a weighted reason-code share vector from (reason_code, weight) rows.

    Mirrors engine._weighted_distribution: shares sum to 1.0. Returns
    (shares, n, n_eff) where n is the raw row count and n_eff the summed weight.
    """
    weight_by_code: dict[str, float] = {}
    total_weight = 0.0
    for code, weight in rows:
        c = code or "other"
        w = float(weight)
        weight_by_code[c] = weight_by_code.get(c, 0.0) + w
        total_weight += w
    if total_weight == 0:
        return {}, 0, 0.0
    shares = {code: w / total_weight for code, w in weight_by_code.items()}
    return shares, len(rows), total_weight


def _category_medians_from_item_rows(
    item_rows: list[tuple[str, str, float]], qualifying: set[str]
) -> dict:
    """Build the per-category median-share baseline from (item_id, reason, weight) rows.

    This mirrors the Python aggregation in engine.compute_category_baseline: per
    qualifying item build its share vector, take the per-code median across
    items, renormalise to sum to 1, then attach n_items / scope.
    """
    totals_by_item: dict[str, dict[str, float]] = {}
    for item_id, reason, weight in item_rows:
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
        return {}

    all_codes: set[str] = set()
    for s in item_shares:
        all_codes.update(s.keys())

    medians: dict[str, float] = {}
    for code in all_codes:
        vals = [s.get(code, 0.0) for s in item_shares]
        medians[code] = statistics.median(vals)

    total = sum(medians.values())
    if total > 0:
        medians = {code: v / total for code, v in medians.items()}

    medians["n_items"] = len(item_shares)
    medians["scope"] = "category"
    return medians


# ---------------------------------------------------------------------------
# Source protocol
# ---------------------------------------------------------------------------


class BaselineSource(Protocol):
    """Two methods, both returning the exact shape engine.py produces."""

    name: str

    def global_baseline(self, db: Session) -> dict:
        """Return {reason_code: share, 'n', 'n_eff', 'scope': 'global'}."""
        ...

    def category_baseline(self, category: str, db: Session) -> dict:
        """Return {reason_code: share, 'n_items', 'scope': 'category'|'global'}."""
        ...


# ---------------------------------------------------------------------------
# SQLite — always-correct reference (delegates to engine.py unchanged)
# ---------------------------------------------------------------------------


class SqliteBaselineSource:
    """Thin wrapper over the existing engine.py SQLite baselines (unchanged).

    This is the always-correct reference used as the final fallback.
    """

    name = "sqlite"

    def global_baseline(self, db: Session) -> dict:
        return _engine.compute_global_baseline(db)

    def category_baseline(self, category: str, db: Session) -> dict:
        return _engine.compute_category_baseline(category, db)


# ---------------------------------------------------------------------------
# DuckDB — over local Parquet
# ---------------------------------------------------------------------------

# The evidence filter mirrors engine.py exactly: resolved statuses, no
# return/customer fraud, one row per (item_id, customer_uid) keeping the most
# recent return, loyalty weight = 0.5 + loyalty/100. SQL uses DuckDB-valid
# syntax (CAST AS DOUBLE, standard ROW_NUMBER, COALESCE); the Parquet `returns`
# dataset already contains ONLY the historical source='csv' rows.
#
# NOTE on determinism: ~10% of (item_id, customer_uid) groups have more than
# one return sharing the SAME return_creation_date. engine.py's ORDER BY
# return_creation_date DESC alone leaves the winner among those date-ties up to
# the engine, so SQLite and a naive DuckDB port can pick different reasons for
# ~10% of groups (a ~1e-4 share drift). We add `r.id DESC` as a stable
# secondary sort key here so the Parquet/Athena ports are reproducible and
# match a SQLite baseline computed with the same tiebreak within 1e-6. This is
# purely a tiebreak for already-tied dates; it never reorders distinct dates,
# so it does not change which return is "most recent".

_DUCKDB_GLOBAL_SQL = """
    WITH ranked AS (
        SELECT
            r.reason AS reason,
            COALESCE(c.loyalty, 0) AS loyalty,
            ROW_NUMBER() OVER (
                PARTITION BY r.item_id, r.customer_uid
                ORDER BY r.return_creation_date DESC, r.id DESC
            ) AS rn
        FROM read_parquet('{returns}') r
        LEFT JOIN read_parquet('{customers}') c
            ON c.customer_id = r.customer_uid
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
    )
    SELECT reason, (0.5 + CAST(loyalty AS DOUBLE) / 100.0) AS weight
    FROM ranked
    WHERE rn = 1
"""

# Per-(item, reason) weighted totals for one category — mirrors
# engine._CATEGORY_WEIGHTED_SQL. Grouping/weighting is done in SQL; the median
# aggregation is done in Python via _category_medians_from_item_rows.
_DUCKDB_CATEGORY_SQL = """
    WITH ranked AS (
        SELECT
            r.item_id AS item_id,
            r.reason AS reason,
            COALESCE(c.loyalty, 0) AS loyalty,
            ROW_NUMBER() OVER (
                PARTITION BY r.item_id, r.customer_uid
                ORDER BY r.return_creation_date DESC, r.id DESC
            ) AS rn
        FROM read_parquet('{returns}') r
        LEFT JOIN read_parquet('{customers}') c
            ON c.customer_id = r.customer_uid
        LEFT JOIN read_parquet('{order_items}') oi
            ON oi.item_id = r.item_id
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
          AND oi.category = ?
    )
    SELECT item_id, reason, SUM(0.5 + CAST(loyalty AS DOUBLE) / 100.0) AS weight
    FROM ranked
    WHERE rn = 1
    GROUP BY item_id, reason
"""

# Qualifying items (>= min_returns_baseline unique customers) for one category —
# mirrors engine._qualifying_items_in_category.
_DUCKDB_QUALIFYING_SQL = """
    WITH joined AS (
        SELECT r.item_id AS item_id, r.customer_uid AS customer_uid
        FROM read_parquet('{returns}') r
        LEFT JOIN read_parquet('{customers}') c
            ON c.customer_id = r.customer_uid
        LEFT JOIN read_parquet('{order_items}') oi
            ON oi.item_id = r.item_id
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
          AND oi.category = ?
    )
    SELECT item_id
    FROM joined
    GROUP BY item_id
    HAVING COUNT(DISTINCT customer_uid) >= ?
"""


def _parquet_glob(table: str) -> str:
    """Return the read_parquet glob for a table's Parquet export directory."""
    return str(Path(settings.parquet_dir) / table / "*.parquet").replace("\\", "/")


def _returns_parquet_exists() -> bool:
    """True when the local returns Parquet export is present."""
    d = Path(settings.parquet_dir) / "returns"
    if not d.is_dir():
        return False
    return any(d.glob("*.parquet"))


class DuckDBBaselineSource:
    """Baselines computed by DuckDB over the local Parquet exports."""

    name = "duckdb"

    def _connect(self):
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - duckdb is pinned
            raise BaselineSourceError(f"duckdb import failed: {exc}") from exc
        try:
            return duckdb.connect(database=":memory:")
        except Exception as exc:  # noqa: BLE001
            raise BaselineSourceError(f"duckdb connect failed: {exc}") from exc

    def global_baseline(self, db: Session) -> dict:  # noqa: ARG002 - db unused (Parquet source)
        if not _returns_parquet_exists():
            raise BaselineSourceError(
                f"returns Parquet not found under {settings.parquet_dir}"
            )
        sql = _DUCKDB_GLOBAL_SQL.format(
            returns=_parquet_glob("returns"),
            customers=_parquet_glob("customers"),
        )
        con = self._connect()
        try:
            raw = con.execute(sql).fetchall()
        except Exception as exc:  # noqa: BLE001
            raise BaselineSourceError(f"duckdb global query failed: {exc}") from exc
        finally:
            con.close()
        rows = [(reason_to_code(r[0]), r[1]) for r in raw]
        shares, n, n_eff = _shares_from_weighted_rows(rows)
        result = dict(shares)
        result["n"] = n
        result["n_eff"] = n_eff
        result["scope"] = "global"
        return result

    def category_baseline(self, category: str, db: Session) -> dict:
        if not _returns_parquet_exists():
            raise BaselineSourceError(
                f"returns Parquet not found under {settings.parquet_dir}"
            )
        qual_sql = _DUCKDB_QUALIFYING_SQL.format(
            returns=_parquet_glob("returns"),
            customers=_parquet_glob("customers"),
            order_items=_parquet_glob("order_items"),
        )
        cat_sql = _DUCKDB_CATEGORY_SQL.format(
            returns=_parquet_glob("returns"),
            customers=_parquet_glob("customers"),
            order_items=_parquet_glob("order_items"),
        )
        con = self._connect()
        try:
            qualifying = {
                r[0]
                for r in con.execute(
                    qual_sql, [category, settings.min_returns_baseline]
                ).fetchall()
            }
            # Fewer than 10 qualifying items -> fall back to the global baseline,
            # mirroring engine.compute_category_baseline.
            if len(qualifying) < 10:
                result = self.global_baseline(db)
                result["scope"] = "global"
                result["n_items"] = len(qualifying)
                return result
            item_rows = [
                (r[0], r[1], r[2])
                for r in con.execute(cat_sql, [category]).fetchall()
            ]
        except BaselineSourceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BaselineSourceError(f"duckdb category query failed: {exc}") from exc
        finally:
            con.close()

        medians = _category_medians_from_item_rows(item_rows, qualifying)
        if not medians:
            result = self.global_baseline(db)
            result["scope"] = "global"
            result["n_items"] = 0
            return result
        return medians


# ---------------------------------------------------------------------------
# Athena — over S3-hosted Parquet via the Glue catalog
# ---------------------------------------------------------------------------

# Module-level record of the last Athena query execution id (consumed by
# FEAT-004 / the /data-source endpoint).
_last_athena_query_id: str | None = None


def last_athena_query_id() -> str | None:
    """Return the id of the most recent Athena query execution, if any."""
    return _last_athena_query_id


# Same aggregation as DuckDB but against the Glue tables. CAST AS DOUBLE and the
# 0/1 boolean comparisons are Athena-valid; the fully-qualified table names use
# settings.glue_database.
_ATHENA_GLOBAL_SQL = """
    WITH ranked AS (
        SELECT
            r.reason AS reason,
            COALESCE(c.loyalty, 0) AS loyalty,
            ROW_NUMBER() OVER (
                PARTITION BY r.item_id, r.customer_uid
                ORDER BY r.return_creation_date DESC, r.id DESC
            ) AS rn
        FROM {db}.returns r
        LEFT JOIN {db}.customers c
            ON c.customer_id = r.customer_uid
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
    )
    SELECT reason, (0.5 + CAST(loyalty AS DOUBLE) / 100.0) AS weight
    FROM ranked
    WHERE rn = 1
"""

_ATHENA_CATEGORY_SQL = """
    WITH ranked AS (
        SELECT
            r.item_id AS item_id,
            r.reason AS reason,
            COALESCE(c.loyalty, 0) AS loyalty,
            ROW_NUMBER() OVER (
                PARTITION BY r.item_id, r.customer_uid
                ORDER BY r.return_creation_date DESC, r.id DESC
            ) AS rn
        FROM {db}.returns r
        LEFT JOIN {db}.customers c
            ON c.customer_id = r.customer_uid
        LEFT JOIN {db}.order_items oi
            ON oi.item_id = r.item_id
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
          AND oi.category = '{category}'
    )
    SELECT item_id, reason, SUM(0.5 + CAST(loyalty AS DOUBLE) / 100.0) AS weight
    FROM ranked
    WHERE rn = 1
    GROUP BY item_id, reason
"""

_ATHENA_QUALIFYING_SQL = """
    WITH joined AS (
        SELECT r.item_id AS item_id, r.customer_uid AS customer_uid
        FROM {db}.returns r
        LEFT JOIN {db}.customers c
            ON c.customer_id = r.customer_uid
        LEFT JOIN {db}.order_items oi
            ON oi.item_id = r.item_id
        WHERE r.last_return_status IN ('RETURN_COMPLETED','RETURN_RECEIVED','RETURN_PROCESSED')
          AND (r.fraud_flag = 0 OR r.fraud_flag IS NULL)
          AND COALESCE(c.fraud_flag, 0) = 0
          AND oi.category = '{category}'
    )
    SELECT item_id
    FROM joined
    GROUP BY item_id
    HAVING COUNT(DISTINCT customer_uid) >= {min_baseline}
"""


def _athena_escape(value: str) -> str:
    """Escape a single-quoted string literal for inlining into Athena SQL."""
    return value.replace("'", "''")


class AthenaBaselineSource:
    """Baselines computed by Athena over the S3 Parquet / Glue catalog."""

    name = "athena"

    def _run_query(self, sql: str) -> list[list]:
        """Run one Athena query, poll to SUCCEEDED, return parsed result rows.

        Records the execution id in the module-level _last_athena_query_id.
        Raises BaselineSourceError on any boto3/ClientError or query failure.
        """
        global _last_athena_query_id
        try:
            from .. import aws
        except Exception as exc:  # noqa: BLE001
            raise BaselineSourceError(f"aws module import failed: {exc}") from exc
        try:
            client = aws.athena_client()
            resp = client.start_query_execution(
                QueryString=sql,
                ResultConfiguration={"OutputLocation": settings.athena_results_s3},
                WorkGroup=settings.athena_workgroup,
            )
            qid = resp["QueryExecutionId"]
            _last_athena_query_id = qid

            # Poll until terminal (cap ~60s).
            deadline = time.monotonic() + 60.0
            state = "QUEUED"
            reason = ""
            while time.monotonic() < deadline:
                info = client.get_query_execution(QueryExecutionId=qid)
                status = info["QueryExecution"]["Status"]
                state = status["State"]
                reason = status.get("StateChangeReason", "")
                if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    break
                time.sleep(1.0)
            if state != "SUCCEEDED":
                raise BaselineSourceError(
                    f"athena query {qid} ended in state {state}: {reason}"
                )

            # Page through results; first row of the first page is the header.
            rows: list[list] = []
            header_skipped = False
            paginator = client.get_paginator("get_query_results")
            for page in paginator.paginate(QueryExecutionId=qid):
                for rec in page["ResultSet"]["Rows"]:
                    values = [col.get("VarCharValue") for col in rec["Data"]]
                    if not header_skipped:
                        header_skipped = True
                        continue
                    rows.append(values)
            return rows
        except BaselineSourceError:
            raise
        except Exception as exc:  # noqa: BLE001 - boto3/ClientError etc.
            raise BaselineSourceError(f"athena query failed: {exc}") from exc

    def global_baseline(self, db: Session) -> dict:  # noqa: ARG002 - db unused (Athena source)
        sql = _ATHENA_GLOBAL_SQL.format(db=settings.glue_database)
        raw = self._run_query(sql)
        rows: list[tuple[str, float]] = []
        for rec in raw:
            reason, weight = rec[0], rec[1]
            rows.append((reason_to_code(reason), float(weight) if weight is not None else 0.0))
        shares, n, n_eff = _shares_from_weighted_rows(rows)
        result = dict(shares)
        result["n"] = n
        result["n_eff"] = n_eff
        result["scope"] = "global"
        return result

    def category_baseline(self, category: str, db: Session) -> dict:
        qual_sql = _ATHENA_QUALIFYING_SQL.format(
            db=settings.glue_database,
            category=_athena_escape(category),
            min_baseline=int(settings.min_returns_baseline),
        )
        qualifying = {rec[0] for rec in self._run_query(qual_sql) if rec and rec[0] is not None}
        if len(qualifying) < 10:
            result = self.global_baseline(db)
            result["scope"] = "global"
            result["n_items"] = len(qualifying)
            return result

        cat_sql = _ATHENA_CATEGORY_SQL.format(
            db=settings.glue_database, category=_athena_escape(category)
        )
        item_rows: list[tuple[str, str, float]] = []
        for rec in self._run_query(cat_sql):
            item_id, reason, weight = rec[0], rec[1], rec[2]
            item_rows.append((item_id, reason, float(weight) if weight is not None else 0.0))

        medians = _category_medians_from_item_rows(item_rows, qualifying)
        if not medians:
            result = self.global_baseline(db)
            result["scope"] = "global"
            result["n_items"] = 0
            return result
        return medians


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

# The 8 GROUP-1 tables FEAT-002 registers in the Glue catalog.
_EXPECTED_GLUE_TABLES = {
    "returns",
    "order_items",
    "customers",
    "returnable_flags",
    "restocking_fees",
    "exchanges",
    "stores",
    "unmapped_reasons",
}


def _glue_tables_registered() -> bool:
    """True when the Glue database holds the expected GROUP-1 tables.

    Guarded: any boto3/ClientError (denied, missing db, no creds) returns False
    rather than raising, so the selector can skip Athena cleanly.
    """
    try:
        from .. import aws

        client = aws.glue_client()
        found: set[str] = set()
        next_token: str | None = None
        while True:
            kwargs = {"DatabaseName": settings.glue_database, "MaxResults": 100}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = client.get_tables(**kwargs)
            for t in resp.get("TableList", []):
                found.add(t.get("Name", ""))
            next_token = resp.get("NextToken")
            if not next_token:
                break
        return _EXPECTED_GLUE_TABLES.issubset(found)
    except Exception:  # noqa: BLE001 - any failure => Athena not usable
        return False


def select_baseline_source(settings_obj=settings) -> BaselineSource:
    """Pick a baseline source per settings.data_backend. Never raises.

    - 'sqlite'  -> SqliteBaselineSource
    - 'duckdb'  -> DuckDBBaselineSource
    - 'athena'  -> AthenaBaselineSource
    - 'auto'    -> Athena if AWS is available AND the Glue tables exist;
                   else DuckDB if the local returns Parquet exists;
                   else SQLite.
    """
    backend = (getattr(settings_obj, "data_backend", "auto") or "auto").lower()

    if backend == "sqlite":
        return SqliteBaselineSource()
    if backend == "duckdb":
        return DuckDBBaselineSource()
    if backend == "athena":
        return AthenaBaselineSource()

    # auto
    try:
        from .. import aws

        if aws.aws_available() and _glue_tables_registered():
            return AthenaBaselineSource()
    except Exception:  # noqa: BLE001 - never let selection raise
        pass

    try:
        if _returns_parquet_exists():
            return DuckDBBaselineSource()
    except Exception:  # noqa: BLE001
        pass

    return SqliteBaselineSource()


# ---------------------------------------------------------------------------
# Cache population helper (used by main._warm_insight_baselines)
# ---------------------------------------------------------------------------


def populate_baseline_cache(source: BaselineSource, db: Session) -> list[str]:
    """Compute global + per-category baselines through *source* and write them
    into engine._BASELINE_CACHE under '__global__' and each category key,
    exactly as engine.compute_* would. Returns the list of categories warmed.

    Raises whatever the source raises (so the caller can fall back); callers
    should invalidate_baseline_cache() first.
    """
    from sqlalchemy import text as _text

    global_baseline = source.global_baseline(db)
    _engine._BASELINE_CACHE["__global__"] = dict(global_baseline)

    cats = [
        r[0]
        for r in db.execute(
            _text(
                "SELECT DISTINCT category FROM order_items WHERE category IS NOT NULL"
            )
        ).fetchall()
    ]
    for cat in cats:
        cat_baseline = source.category_baseline(cat, db)
        _engine._BASELINE_CACHE[cat] = dict(cat_baseline)
    return cats
