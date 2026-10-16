"""
to_parquet.py — Offline data-prep pipeline for the hybrid data layer.

Exports the 8 GROUP-1 tables from the existing aldo_returns.db to local
Parquet, uploads each file to S3 (guarded), then registers them as Glue /
Athena external tables (guarded). Reads the existing DB — it does NOT re-run
the full CSV ingest (app.ingest.run) unless ``--rebuild`` is passed AND the DB
is missing.

Run from the backend/ directory::

    python -m app.ingest.to_parquet

Everything AWS-facing is wrapped so the script still exits 0 (and keeps the
local Parquet copies) when S3 / Glue / Athena are denied or credentials are
absent or expired — the whole design degrades gracefully to local.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from app.config import settings
from app.db import engine

# ---------------------------------------------------------------------------
# GROUP-1 tables and the SQL used to export each one.
# `returns` is historical-only (source = 'csv'); the rest are full-table reads.
# ---------------------------------------------------------------------------

GROUP1_TABLES: list[str] = [
    "returns",
    "order_items",
    "customers",
    "returnable_flags",
    "restocking_fees",
    "exchanges",
    "stores",
    "unmapped_reasons",
]


def _table_sql(table: str) -> str:
    """Return the export SQL for *table* (returns is CSV-history only)."""
    if table == "returns":
        return "SELECT * FROM returns WHERE source = 'csv'"
    return f"SELECT * FROM {table}"


def _parquet_path(table: str) -> Path:
    """Local path <parquet_dir>/<table>/<table>.parquet for *table*."""
    return Path(settings.parquet_dir) / table / f"{table}.parquet"


# ---------------------------------------------------------------------------
# 1. Local Parquet export
# ---------------------------------------------------------------------------


def export_parquet() -> dict[str, int]:
    """
    Read each GROUP-1 table from the live DB and write it to local Parquet.

    Returns a {table: row_count} dict and prints the count per table.
    """
    counts: dict[str, int] = {}
    for table in GROUP1_TABLES:
        df = pd.read_sql(_table_sql(table), engine)
        path = _parquet_path(table)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, engine="pyarrow", index=False)
        counts[table] = len(df)
        print(f"  exported {table:<20} {len(df):>10,} rows -> {path}", flush=True)
    return counts


# ---------------------------------------------------------------------------
# 2. Guarded S3 upload
# ---------------------------------------------------------------------------


def upload_s3() -> dict[str, str]:
    """
    Upload each local Parquet file to S3 under the datasets prefix.

    Each upload is guarded individually: on any failure the skip is logged and
    the loop continues (the local copy is kept). Returns {table: s3_uri} for
    the uploads that succeeded.
    """
    from app.aws import s3_client

    uploaded: dict[str, str] = {}
    for table in GROUP1_TABLES:
        local = _parquet_path(table)
        key = f"{settings.s3_datasets_prefix}{table}/{table}.parquet"
        try:
            s3_client().upload_file(str(local), settings.s3_bucket, key)
        except Exception as err:  # noqa: BLE001 — degrade gracefully on any denial
            print(f"  S3 upload skipped for {table}: {err}", flush=True)
            continue
        uri = f"s3://{settings.s3_bucket}/{key}"
        uploaded[table] = uri
        print(f"  uploaded {table:<20} -> {uri}", flush=True)
    return uploaded


# ---------------------------------------------------------------------------
# 3. Guarded Glue / Athena external-table registration
# ---------------------------------------------------------------------------

# pyarrow logical type → Athena/Hive column type.
_PA_TYPE_ORDER = (
    ("is_boolean", "boolean"),
    ("is_integer", "bigint"),
    ("is_floating", "double"),
    ("is_timestamp", "timestamp"),
    ("is_string", "string"),
    ("is_large_string", "string"),
)


def _athena_type(pa_type) -> str:  # noqa: ANN001 — pyarrow DataType
    """Map a pyarrow column type to an Athena/Hive column type."""
    import pyarrow.types as pat

    checks = {
        "is_boolean": pat.is_boolean,
        "is_integer": pat.is_integer,
        "is_floating": pat.is_floating,
        "is_timestamp": pat.is_timestamp,
        "is_string": pat.is_string,
        "is_large_string": pat.is_large_string,
    }
    for name, _ in _PA_TYPE_ORDER:
        if checks[name](pa_type):
            return dict(_PA_TYPE_ORDER)[name]
    # Fall back to string for anything unrecognised (safest for Athena).
    return "string"


def _columns_for(table: str) -> list[tuple[str, str]]:
    """Derive (column_name, athena_type) pairs from the Parquet schema."""
    schema = pq.read_schema(_parquet_path(table))
    return [(field.name, _athena_type(field.type)) for field in schema]


def run_athena(sql: str) -> str:
    """
    Run *sql* on Athena and block until it reaches a terminal state.

    Returns the query execution id. Raises RuntimeError if the query does not
    finish SUCCEEDED (the caller guards this).
    """
    from app.aws import athena_client

    client = athena_client()
    resp = client.start_query_execution(
        QueryString=sql,
        ResultConfiguration={"OutputLocation": settings.athena_results_s3},
        WorkGroup=settings.athena_workgroup,
    )
    qid = resp["QueryExecutionId"]
    deadline = time.monotonic() + 60
    while True:
        info = client.get_query_execution(QueryExecutionId=qid)
        state = info["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            return qid
        if state in ("FAILED", "CANCELLED"):
            reason = (
                info["QueryExecution"]["Status"].get("StateChangeReason", "")
            )
            raise RuntimeError(f"Athena query {qid} {state}: {reason}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"Athena query {qid} timed out (last state {state})")
        time.sleep(1)


def register_athena() -> dict[str, object]:
    """
    Register the exported Parquet datasets as Glue/Athena external tables.

    Creates the Glue database (swallowing AlreadyExistsException) then issues a
    CREATE EXTERNAL TABLE IF NOT EXISTS per table. The whole operation is
    guarded: any Glue/Athena denial logs a skip and the script still exits 0.

    Returns a status dict: {"registered": bool, "query_ids": {table: qid}}.
    """
    from botocore.exceptions import ClientError

    from app.aws import glue_client

    result: dict[str, object] = {"registered": False, "query_ids": {}}

    try:
        try:
            glue_client().create_database(
                DatabaseInput={"Name": settings.glue_database}
            )
            print(f"  created Glue database {settings.glue_database}", flush=True)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code == "AlreadyExistsException":
                print(
                    f"  Glue database {settings.glue_database} already exists",
                    flush=True,
                )
            else:
                raise

        query_ids: dict[str, str] = {}
        for table in GROUP1_TABLES:
            cols = ",\n  ".join(
                f"`{name}` {atype}" for name, atype in _columns_for(table)
            )
            location = (
                f"s3://{settings.s3_bucket}/{settings.s3_datasets_prefix}{table}/"
            )
            sql = (
                f"CREATE EXTERNAL TABLE IF NOT EXISTS "
                f"{settings.glue_database}.{table} (\n  {cols}\n) "
                f"STORED AS PARQUET\nLOCATION '{location}'"
            )
            qid = run_athena(sql)
            query_ids[table] = qid
            print(f"  registered {table:<20} (query {qid})", flush=True)

        result["registered"] = True
        result["query_ids"] = query_ids
    except Exception as err:  # noqa: BLE001 — degrade gracefully on any denial
        print(f"  Athena/Glue registration skipped: {err}", flush=True)

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    """Resolve the local SQLite DB path from the configured database_url."""
    url = settings.database_url
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return Path(url[len(prefix):])
    # Non-sqlite URL: fall back to the conventional backend/aldo_returns.db.
    return Path(__file__).resolve().parent.parent.parent / "aldo_returns.db"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="to_parquet",
        description="Export GROUP-1 tables to Parquet, S3, and Glue/Athena.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="If the DB is missing, run the full CSV ingest first.",
    )
    args = parser.parse_args(argv)

    db_path = _db_path()
    if not db_path.exists():
        if args.rebuild:
            print(f"DB {db_path} missing; running full CSV ingest (--rebuild) …")
            from app.ingest.run import run as ingest_run

            ingest_run()
        else:
            print(
                f"ERROR: database not found at {db_path}. "
                f"Re-run with --rebuild to build it from the CSVs first.",
                file=sys.stderr,
            )
            return 1

    print("Exporting GROUP-1 tables to local Parquet …")
    counts = export_parquet()

    print("\nUploading Parquet datasets to S3 …")
    uploaded = upload_s3()

    print("\nRegistering Glue/Athena external tables …")
    reg = register_athena()

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("to_parquet summary")
    print("=" * 60)
    for table in GROUP1_TABLES:
        print(f"  {table:<20} {counts[table]:>10,} rows")
    dataset_prefix = f"s3://{settings.s3_bucket}/{settings.s3_datasets_prefix}"
    print(f"\n  local parquet dir : {settings.parquet_dir}")
    print(f"  s3 dataset prefix : {dataset_prefix}")
    print(f"  s3 uploads        : {len(uploaded)}/{len(GROUP1_TABLES)} tables")
    print(f"  glue/athena       : {'registered' if reg['registered'] else 'skipped'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
