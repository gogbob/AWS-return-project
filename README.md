# ALDO Returns Agent

An AI-assisted returns system for the ALDO Group. It ingests historical returns data,
computes per-item return-reason insights against a median baseline, runs a conversational
return assistant with a deterministic fraud-triage policy, and escalates suspicious cases
to a vision-capable agent and, if needed, a human admin console.

The backend runs **fully local by default** (SQLite + a mock LLM) and transparently
upgrades to a **hybrid AWS analytics layer** (S3 + Glue + Athena, with DuckDB over local
Parquet as a middle tier) and **Amazon Bedrock** models when AWS credentials are present.
Every AWS path degrades gracefully back to local when credentials are absent or denied.

---

## Major libraries and where they are used

### Agents and LLM

| Library | Role in the framework | Where it's used |
|---|---|---|
| **Strands Agents SDK** (`strands-agents`) | Agent runtime. `Agent`, `@tool`, `BedrockModel`, and structured output power both agents and the knowledge-base tools the agents call. | `app/agents/assistant.py` (Return Assistant, `Agent` + `BedrockModel`), `app/agents/vision.py` (Vision Review `Agent` + `structured_output`), `app/agents/memory.py` (one `Agent` per case), `app/kb/tools.py` (`@tool` functions) |
| **Amazon Bedrock** (via `strands.models.BedrockModel`) | Default model provider. Text model for the assistant, vision-capable Claude model for photo review. Model IDs/region come from env vars; never hardcoded credentials. | `app/agents/assistant.py`, `app/agents/vision.py` |
| **Pydantic** (`pydantic`) | Structured output schema for the vision verdict, and typed triage results. | `app/agents/vision.py` (`VisionVerdict`), `app/policy/triage.py` (`TriageResult`), `app/config.py` |

### Hybrid data / analytics layer (AWS)

| Library | Role in the framework | Where it's used |
|---|---|---|
| **boto3** | AWS SDK. Lazy, cached clients for S3, Glue, Athena, and STS (credential probe). | `app/aws.py` (client factories + `aws_available()`), consumed by `app/ingest/to_parquet.py` and `app/insights/sources.py` |
| **Amazon S3** | Stores the exported Parquet datasets and Athena query results. | `app/ingest/to_parquet.py` (`upload_s3`), `app/aws.py` (`s3_client`) |
| **AWS Glue** | Data catalog. Registers the Parquet datasets as external tables for Athena. | `app/ingest/to_parquet.py` (`register_athena` → `create_database`, `CREATE EXTERNAL TABLE`), `app/insights/sources.py` (`_glue_tables_registered`) |
| **Amazon Athena** | Serverless SQL over the S3/Glue Parquet datasets. Runs the heavy baseline scans off the local SQLite file. | `app/insights/sources.py` (`AthenaBaselineSource`), `app/ingest/to_parquet.py` (`run_athena`) |
| **DuckDB** (`duckdb`) | Local Parquet query engine — the middle tier between Athena and SQLite in the `auto` fallback chain. | `app/insights/sources.py` (`DuckDBBaselineSource`, `read_parquet(...)`) |
| **pandas** (`pandas`) | CSV ingestion, cleaning/normalisation, joins, and reading tables out to Parquet. | `app/ingest/run.py` (CSV read/clean/join), `app/ingest/to_parquet.py` (`read_sql` → `to_parquet`) |
| **PyArrow** (`pyarrow`) | Parquet writer and schema reader; maps Parquet types to Athena/Hive column types. | `app/ingest/to_parquet.py` (`to_parquet(engine="pyarrow")`, `pq.read_schema`, `_athena_type`) |

### Web / API, storage, and stats

| Library | Role in the framework | Where it's used |
|---|---|---|
| **FastAPI** | HTTP API, OpenAPI docs, CORS, lifespan startup (table creation + baseline warm-up). | `app/main.py` (app factory, lifespan), `app/api/routes_customer.py`, `app/api/routes_admin.py` |
| **Uvicorn** | ASGI server that runs the FastAPI app. | run command (below) |
| **SQLAlchemy** | ORM + engine. Local system-of-record (SQLite with WAL, a `QueuePool`), 13-table schema, all live reads/writes. | `app/db.py` (engine, `Base`, `SessionLocal`), `app/ingest/models.py` (models), used throughout `app/kb`, `app/policy`, `app/insights` |
| **scipy** (`scipy.stats`) | One-sided binomial test for the anomaly flag (with a `binom_test`/`binomtest` version shim). | `app/insights/engine.py` |
| **RapidFuzz** (`rapidfuzz`) | Fuzzy item-name matching against a customer's own order lines. | `app/kb/tools.py` (`find_order_item`, `fuzz.token_set_ratio`) |
| **python-dotenv** + **pydantic-settings** | Loads `.env` into the environment (so boto3's credential chain sees `AWS_*`) and into a typed `Settings` object. | `app/config.py` |
| **PyYAML** (`yaml`) | Category keyword rules and the reason taxonomy. | `app/ingest/run.py`, `app/ingest/category_keywords.yaml`, `app/insights/reason_taxonomy.yaml` |
| **python-multipart** | Multipart parsing for customer photo uploads. | FastAPI photo-upload endpoint (`app/api/routes_customer.py`) |

### Frontend

| Library | Role in the framework | Where it's used |
|---|---|---|
| **React + React Router** | Single-page app with `/` (customer demo) and `/admin` (human console) routes. | `frontend/` |
| **Vite** | Dev server and build tool. | `frontend/package.json` |
| **TypeScript** | Typed frontend source. | `frontend/` |
| **Tailwind CSS** (+ PostCSS, Autoprefixer) | Styling for the retail product page and admin console. | `frontend/` |

---

## System framework, step by step

Each step below lists the `.py` files that implement it. All paths are relative to `backend/`.

### 1. Configuration and infrastructure
- **`app/config.py`** — Typed `Settings` (pydantic-settings). Loads `.env`, defines LLM/model, triage thresholds, flagging parameters, and the hybrid data-layer settings (`s3_bucket`, `glue_database`, `athena_workgroup`, `data_backend`, `parquet_dir`). Also holds `TRIAGE_WEIGHTS` and `CLAIM_RISK_BY_GROUP`.
- **`app/aws.py`** — Lazy, `lru_cache`'d boto3 clients for S3 / Glue / Athena, plus `aws_available()` (STS probe). Safe to import with no credentials.
- **`app/db.py`** — SQLAlchemy engine (SQLite + WAL + `QueuePool`), `SessionLocal`, declarative `Base`, and the `get_db` FastAPI dependency.

### 2. Ingestion (CSV → SQLite)
- **`app/ingest/run.py`** — Idempotent `python -m app.ingest.run`. Reads the four raw CSVs (pandas, chunked), normalises headers, applies alias maps, derives `item_id` and `category` (PyYAML rules), maps reasons to canonical codes, generates seeded loyalty scores, builds `stores.json`, and populates the 13 tables.
- **`app/ingest/models.py`** — SQLAlchemy ORM models for all 13 tables (`order_items`, `returns`, `customers`, `cases`, `recommendations`, etc.).

### 3. Data-prep for the hybrid layer (SQLite → Parquet → S3 → Glue/Athena)
- **`app/ingest/to_parquet.py`** — `python -m app.ingest.to_parquet`. Exports the 8 analytics tables to local Parquet (pandas + PyArrow), uploads to S3 (boto3, guarded), and registers them as Glue/Athena external tables (guarded). Exits 0 even when AWS is denied, keeping the local Parquet copies.

### 4. Insights engine (baseline + anomaly flagging)
- **`app/insights/engine.py`** — The always-correct SQLite reference: weighted per-item reason distributions, median category/global baselines, the scipy binomial anomaly test, Jensen–Shannon ranking, recommendations, and live recompute.
- **`app/insights/sources.py`** — The hybrid baseline abstraction. `select_baseline_source()` picks **Athena → DuckDB → SQLite** under `data_backend='auto'`. `AthenaBaselineSource` (boto3/Athena over Glue), `DuckDBBaselineSource` (DuckDB over local Parquet), and `SqliteBaselineSource` all return the exact shape `engine.py` produces.

### 5. Loyalty and return policy (deterministic Python, not the LLM)
- **`app/loyalty/loyalty.py`** — `get_loyalty(customer_id)` placeholder (seeded, demo-only).
- **`app/policy/eligibility.py`** — Eligibility checks (final-sale, duplicate return, item-in-orders).
- **`app/policy/triage.py`** — Composite suspicion score → `APPROVED_NO_FEE | APPROVED_WITH_FEE | VISION_REVIEW | HUMAN_REVIEW`, persisted to `cases`. Uses `TRIAGE_WEIGHTS`/`CLAIM_RISK_BY_GROUP` from config.
- **`app/policy/vision_result.py`** — Maps a structured vision verdict to the approve/escalate/fee decision.

### 6. Knowledge-base tools (the agents' only data access)
- **`app/kb/tools.py`** — Strands `@tool` functions: `get_customer_orders`, `find_order_item` (RapidFuzz), `check_eligibility`, `get_item_reason_context`, `get_loyalty`, `compute_triage`, `finalize_return`, `escalate_to_vision_review`, `escalate_to_human`. These wrap the SQLite tables and the policy/insights modules.

### 7. Agents
- **`app/agents/assistant.py`** — Return Assistant (text-only Strands `Agent` on a Bedrock model; scripted replies in mock mode). Enforces the one-clarifying-question rule, calls `compute_triage`, and relays the deterministic outcome.
- **`app/agents/vision.py`** — Vision Review (multimodal Strands `Agent` with `structured_output` → `VisionVerdict`). Per-reason photo checklists, 2-photo retry limit, and a filename-based mock verdict.
- **`app/agents/memory.py`** — One `Agent` per case in memory, with DB rehydration of conversation history after a restart.

### 8. API layer
- **`app/main.py`** — FastAPI app factory: CORS, router registration, lifespan that creates tables and warms the insight baselines through the selected data backend in a daemon thread.
- **`app/api/routes_customer.py`** — Customer flow endpoints (demo customers, product, item search, stores, start return, message, photo/refuse, poll).
- **`app/api/routes_admin.py`** — Admin console endpoints (review queue, decisions, insights, recommendations, data health, recompute).

---

## Running it

### Backend (one command)

```powershell
cd backend
pip install -r requirements.txt
# Ingest the four CSVs (place them in data/raw/ first):
python -m app.ingest.run
# (optional) Export to Parquet + S3 + Glue/Athena when AWS creds are present:
python -m app.ingest.to_parquet
# Run the API:
uvicorn app.main:app --reload
```

OpenAPI docs are served at `http://localhost:8000/docs`; health at `/health`.

### Frontend (one command)

```powershell
cd frontend
npm install
npm run dev
```

---

## Mock mode

Set `LLM_MOCK=true` to run the whole app without any cloud credentials:
- The Return Assistant uses scripted replies (`app/agents/assistant.py`).
- The Vision agent returns a filename-based verdict: `consistent` if the filename contains `ok`, `inconsistent` if it contains `bad`, `unclear` otherwise (`app/agents/vision.py`).

## Data-backend selection

`DATA_BACKEND` controls the analytics source for baselines:
- `auto` (default) — Athena if AWS creds work **and** the Glue tables are registered, else DuckDB if the local returns Parquet exists, else SQLite.
- `athena` / `duckdb` / `sqlite` — force a specific backend.

The per-item live paths (distributions, anomaly recompute) always stay on SQLite so live returns are reflected instantly; only the category/global baselines are routed through the selected backend.

## Configuration

See `.env.example` for all settings. Key ones: `LLM_MOCK`, `MODEL_PROVIDER`, `MODEL_ID`,
`AWS_REGION`, `DATA_BACKEND`, `S3_BUCKET`, `GLUE_DATABASE`, `ATHENA_WORKGROUP`,
`SUSPICION_THRESHOLD`, `HIGH_LOYALTY_THRESHOLD`, `SHIPPING_FEE_CAD`. Never hardcode
credentials or model IDs; boto3 reads `AWS_*` from the environment / `.env`.

## Assumptions

- Local-first: with no AWS credentials, everything runs on SQLite + mock LLM. AWS (S3/Glue/Athena) and Bedrock are optional upgrades that degrade gracefully.
- The Parquet/Athena datasets hold only the historical `source='csv'` returns; live returns live in SQLite and feed the per-item live paths.
- Loyalty scores are seeded placeholders for the demo (`LOYALTY_SEED`, default 42), stable across rebuilds.
