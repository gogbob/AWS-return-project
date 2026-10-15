# ALDO Returns Agent — Implementation Tasks

**Version:** 0.1 (Step 0 — Spec)

Tasks are ordered. A task may not begin until all listed dependencies are complete.
Each task's acceptance criteria are the minimum bar for "done".

---

## Step 0 — Specs (this step)

### TASK-00: Create project structure and spec documents
- **id:** TASK-00
- **description:** Create all required directories and write `requirements.md`, `design.md`, and `tasks.md` under `.kiro/specs/aldo-returns/`. No application code.
- **acceptance criteria:**
  - All directories listed in design.md §2 exist.
  - `requirements.md`, `design.md`, and `tasks.md` are present and complete.
  - No Python, TypeScript, or configuration files have been written yet.
- **dependencies:** none

---

## Step 1 — Data Profile

### TASK-01: Profile the four CSV files
- **id:** TASK-01
- **description:** Read the four raw CSV files from `data/raw/`, print real headers, apply the column normalization rules (trim, lowercase, replace spaces/punctuation with `_`, then apply the alias map), inspect distinct values for all categorical columns (`store_banner`, `last_return_status`, `return_channel`, `reason`, `Instore return location`, `Is item final sale?`, `Exchange_Confidence`), count nulls, and write `docs/data_profile.md`. Determine whether SKUs are style-level or size/colour-level and record the finding. Document which `last_return_status` values will be kept vs. excluded for insights evidence.
- **acceptance criteria:**
  - `docs/data_profile.md` exists and documents: real headers for each file, normalized column names, distinct values for every categorical column inspected, null counts for key columns, join key validation (order_number + sku overlap with returnable_flags), chosen `ITEM_KEY` value and rationale, list of `last_return_status` values included in evidence.
  - The profile was generated from the actual CSV files, not assumed.
- **dependencies:** TASK-00

---

## Step 2 — Project Scaffold

### TASK-02: Scaffold backend (Python)
- **id:** TASK-02
- **description:** Create `backend/app/main.py`, `config.py`, `db.py`, and empty `__init__.py` files for each subpackage. Set up FastAPI app with a health check endpoint (`GET /api/health`). Configure Pydantic BaseSettings to read all variables from `.env` with the defaults listed in design.md §9. Write `.env.example`.
- **acceptance criteria:**
  - `uvicorn app.main:app` starts without error.
  - `GET /api/health` returns `{"status": "ok"}`.
  - All config variables have correct defaults.
  - `.env.example` is present with every variable documented.
- **dependencies:** TASK-00

### TASK-03: Scaffold frontend (React + Vite + TypeScript + Tailwind)
- **id:** TASK-03
- **description:** Initialize a Vite + React + TypeScript project in `frontend/`. Add Tailwind CSS. Add React Router for `/` and `/admin` routes. Add TanStack Query. Create stub `CustomerPage` and `AdminPage` components. Configure a dev proxy to forward `/api` requests to `http://localhost:8000`.
- **acceptance criteria:**
  - `npm run dev` in `frontend/` starts without error.
  - `/` renders the `CustomerPage` stub without a console error.
  - `/admin` renders the `AdminPage` stub without a console error.
  - Tailwind utility classes apply correctly.
- **dependencies:** TASK-00

---

## Step 3 — Ingest Pipeline

### TASK-04: Column normalizer and CSV loaders
- **id:** TASK-04
- **description:** Implement `backend/app/ingest/loaders.py`. Load each CSV into a pandas DataFrame, apply the normalization rules (trim, lowercase, replace spaces/punctuation with `_`, alias map), and return a dict of DataFrames keyed by table name. Log any column names that are not in the expected set.
- **acceptance criteria:**
  - Loader runs without error on the real CSV files.
  - Output DataFrames have normalized column names matching those in data_profile.md.
  - Unexpected columns are logged as warnings, not exceptions.
- **dependencies:** TASK-01, TASK-02

### TASK-05: Transforms, joins, and category derivation
- **id:** TASK-05
- **description:** Implement `backend/app/ingest/transforms.py`. Apply joins (restocking fees on `return_order_number`; returnable flags on `order_number` + `sku`; exchanges on original order number). Derive `item_id` per `ITEM_KEY` config. Derive `category` from `category_keywords.yaml`. Populate `unmapped_reasons` for raw reason values not in `reason_taxonomy.yaml`. Compute `exchange_count` per item. Apply `item_return_qty` default of 1. Do not exclude any status values here — that is the insights engine's job.
- **acceptance criteria:**
  - Joins produce the correct number of matched rows (documented in data_profile.md).
  - Every OMS row has a `category` (at least `other`).
  - Unmapped reasons are collected and ready to insert.
  - `exchange_count` is available per item.
- **dependencies:** TASK-04

### TASK-06: SQLite write and idempotent ingest entry point
- **id:** TASK-06
- **description:** Implement `backend/app/ingest/run.py`. Create all 13 tables (DDL via SQLAlchemy models in `db.py`). Populate `order_items`, `returns`, `returnable_flags`, `restocking_fees`, `exchanges`, `customers` (loyalty via TASK-09), `stores` (from distinct `instore_return_location` values), and `unmapped_reasons`. Make the pipeline idempotent: drop and recreate tables, or use upsert logic. Write `data/stores.json` with `name, address, lat, lng, verified`. Generate `docs/data_profile.md` as a side-effect if it doesn't already exist.
- **acceptance criteria:**
  - `python -m app.ingest.run` runs without error on first and second invocation.
  - All 13 tables exist after ingest.
  - Row counts match those documented in data_profile.md.
  - `data/stores.json` is written.
- **dependencies:** TASK-05, TASK-02

---

## Step 4 — Knowledge Base Tools

### TASK-07: Implement all @tool functions
- **id:** TASK-07
- **description:** Implement `backend/app/kb/tools.py` with all 9 Strands `@tool` functions: `get_customer_orders`, `find_order_item` (fuzzy match using `rapidfuzz`), `check_eligibility`, `get_item_reason_context`, `get_loyalty` (internal only), `compute_triage`, `finalize_return`, `escalate_to_vision_review`, `escalate_to_human`. Each tool must query SQLite; no business logic beyond its defined scope.
- **acceptance criteria:**
  - All 9 functions are decorated with `@tool` and have docstrings.
  - `find_order_item` returns fuzzy matches from the customer's own orders only.
  - `get_loyalty` is not registered as an externally callable API tool; it is used internally by `compute_triage`.
  - `compute_triage` returns a structured dict including every component and the final outcome.
  - Unit tests for `check_eligibility` and `compute_triage` pass (see TASK-19).
- **dependencies:** TASK-06

---

## Step 5 — Loyalty

### TASK-08: Loyalty score generation
- **id:** TASK-08
- **description:** Implement `backend/app/loyalty/scores.py`. Export `get_loyalty(customer_id) -> int`. Use a seeded RNG keyed on `LOYALTY_SEED` + a deterministic hash of `customer_uid` to assign loyalty 0–100. Distribution: ~30% ≥ 70, ~40% 40–69, ~30% < 40. Customers with `fraud_flag=True` return effective loyalty 0. Add a docstring: "placeholder: random for demo".
- **acceptance criteria:**
  - Same `customer_uid` always returns the same loyalty value regardless of call order.
  - Distribution roughly matches 30/40/30 when tested on ≥ 100 customers.
  - `fraud_flag=True` customers return 0.
  - The function is the only entry point to loyalty data.
- **dependencies:** TASK-02

---

## Step 6 — Policy Engine

### TASK-09: Eligibility check
- **id:** TASK-09
- **description:** Implement `backend/app/policy/eligibility.py`. `check_eligibility(customer_id, order_number, sku) -> EligibilityResult`. Checks: item in customer's orders, not final-sale, no existing return for (order_number, sku). Returns structured result with `eligible: bool`, `reason: str | None`.
- **acceptance criteria:**
  - Returns ineligible for a final-sale item.
  - Returns ineligible if `return_order_number` already exists for the order line.
  - Returns ineligible if the item is not in the customer's orders.
  - Returns eligible otherwise.
- **dependencies:** TASK-06

### TASK-10: Triage and suspicion formula
- **id:** TASK-10
- **description:** Implement `backend/app/policy/triage.py`. `compute_triage(case_id) -> TriageResult`. Pull case data; call loyalty via `get_loyalty`; apply high-loyalty shortcut; otherwise compute suspicion with all four components (loyalty, implausibility, claim_risk, value_risk) using the formula in design.md §7. Return outcome + full breakdown JSON. Write breakdown to `cases.triage_breakdown_json`.
- **acceptance criteria:**
  - High-loyalty customer → always `APPROVED_NO_FEE`.
  - Low-loyalty + FIT_SIZE common reason → `APPROVED_WITH_FEE`.
  - Low-loyalty + DEFECT reason → `VISION_REVIEW`.
  - Triage breakdown JSON is stored in `cases`.
  - All suspicion component weights sum to 1.0.
- **dependencies:** TASK-07, TASK-08

---

## Step 7 — Insights Engine

### TASK-11: Evidence set, item stats, and baseline
- **id:** TASK-11
- **description:** Implement `backend/app/insights/engine.py`. `compute_item_stats(item_id)` returns weighted reason distribution, n, n_eff. `compute_baseline(category)` computes the per-reason median baseline (category scope if ≥ 10 qualifying items, otherwise global). Record which scope was used.
- **acceptance criteria:**
  - Dedup: a customer with 5 returns for the same item counts once (their most recent).
  - Baseline sums to 1.0 for every category tested.
  - Category with < 10 qualifying items uses global baseline.
  - On a small in-memory fixture, baseline equals the per-reason median renormalized to 1.
- **dependencies:** TASK-06, TASK-08

### TASK-12: Anomaly flagging and recommendations
- **id:** TASK-12
- **description:** Implement the flagging logic in `engine.py` and `backend/app/insights/recommendations.py`. Flag items per design.md §6 (excess, ratio, binomial test with Bonferroni). Compute JS distance. Map flags to recommendation types and draft texts. Upsert `recommendations` and write `agent_changes` audit rows.
- **acceptance criteria:**
  - Synthetic item: 60% `too_small` vs. 20% baseline, n=100 → flagged `SIZE_GUIDANCE`.
  - Item with n=10 → not flagged even at 60%.
  - JS distance ranking is consistent with flag severity.
  - `agent_changes` row written for every upserted recommendation.
- **dependencies:** TASK-11

---

## Step 8 — Agents

### TASK-13: Verify Strands Agents SDK API
- **id:** TASK-13
- **description:** Before writing agent code, verify the Strands Agents SDK current documentation for: multimodal (image) input, structured output (Pydantic), agents-as-tools pattern, and model provider configuration. Record findings and any deviations from the spec in `README.md` under "Assumptions".
- **acceptance criteria:**
  - A brief verification note is added to `README.md`.
  - If a concrete blocker is found in Strands, a LangChain fallback plan is documented.
- **dependencies:** TASK-02

### TASK-14: Return Assistant (Agent 1)
- **id:** TASK-14
- **description:** Implement `backend/app/agents/agent1_return_assistant.py`. Strands text-only agent with the hard-rules system prompt. Integrates tools from `kb/tools.py`. Persists turns to `case_messages`. Rehydrates history from DB on server restart. Implements mock-mode scripted replies.
- **acceptance criteria:**
  - Agent greets customer, confirms item and reason in first reply.
  - Agent asks at most one clarifying question.
  - Agent relays triage outcome correctly (APPROVED_NO_FEE, APPROVED_WITH_FEE, VISION_REVIEW).
  - In mock mode, no cloud credentials are required.
  - Agent message never contains "loyalty", "suspicion", "fraud", or "risk score" (tested in TASK-19).
- **dependencies:** TASK-07, TASK-10, TASK-13

### TASK-15: Vision Review Agent (Agent 2)
- **id:** TASK-15
- **description:** Implement `backend/app/agents/agent2_vision_review.py`. Multimodal Strands agent registered as a tool for Agent 1 (agents-as-tools). Returns a `VisionVerdict` Pydantic model. Implements 2-attempt retry logic. Stores image files and verdict in `case_evidence`. Implements mock mode (filename-based verdict).
- **acceptance criteria:**
  - Returns structured `VisionVerdict` with all five fields.
  - Mock mode: `ok` file → consistent, `bad` → inconsistent, other → unclear.
  - After 2 `unclear` results or any refusal → calls `escalate_to_human`.
  - Image files stored under `backend/storage/uploads/<case_id>/`.
  - Verdict stored in `case_evidence`.
- **dependencies:** TASK-14

---

## Step 9 — API Layer

### TASK-16: Customer-facing API endpoints
- **id:** TASK-16
- **description:** Implement all customer-facing routes in `backend/app/api/` (demo, items, stores, returns). Wire up agents. Validate all inputs with Pydantic. Ensure no `loyalty`, `suspicion`, `fraud_flag`, or triage component is returned.
- **acceptance criteria:**
  - All 9 customer-facing endpoints return correct responses.
  - OpenAPI docs at `/docs` list all endpoints.
  - `GET /api/returns/{case_id}` correctly reflects polling state.
  - A failed Pydantic validation returns HTTP 422.
- **dependencies:** TASK-15, TASK-09

### TASK-17: Admin API endpoints
- **id:** TASK-17
- **description:** Implement all admin routes in `backend/app/api/admin.py`. Case list, case detail (with loyalty and triage), decision actions, insights, recommendations, data-health, recompute. Each decision action must record `decided_by="admin"` and apply the evidence rules from spec §8.5.
- **acceptance criteria:**
  - All 9 admin endpoints return correct responses.
  - Approve/reject/fraud actions update `cases`, `returns.fraud_flag`, and `customers.fraud_flag` appropriately.
  - "Release to evidence" marks `returns.quarantined=false` without setting fraud.
  - `POST /api/admin/recompute` triggers baseline recomputation and returns HTTP 202.
- **dependencies:** TASK-12, TASK-16

---

## Step 10 — Frontend Implementation

### TASK-18: Customer page and return modal
- **id:** TASK-18
- **description:** Implement the full customer-facing UI: ALDO-style product page, header with demo customer dropdown (8 customers, labeled "Customer #N"), "Return an item" modal with all 5 steps (item search, method, in-store/online, reason, chat + photo). Integrate all customer API endpoints. Implement polling for human-review state. Show SIZE_GUIDANCE banner when applied.
- **acceptance criteria:**
  - All 5 modal steps render and transition correctly.
  - Fuzzy item search works against the customer's own orders.
  - In-store path shows nearest stores (or searchable list); never creates a case.
  - Chat window shows agent replies; photo upload appears only when Vision agent requests it.
  - "I'd rather not share" button triggers `POST /api/returns/{case_id}/refuse-photo`.
  - Approved states show mock return label and fee line.
  - Pending state polls every 3 seconds.
  - Loyalty values are never shown in the UI (dev tooltip only in `NODE_ENV=development`).
- **dependencies:** TASK-16, TASK-03

### TASK-19: Admin console UI
- **id:** TASK-19
- **description:** Implement the 4-tab admin console: Review Queue, Insights, Recommendations, Data Health. Implement case detail with full transcript, images, triage breakdown, and action buttons. Implement insights bar chart (item vs. baseline distributions). Implement unmapped reasons list with "add to taxonomy" affordance note.
- **acceptance criteria:**
  - Review Queue lists HUMAN_REVIEW cases newest first.
  - Case detail shows all required fields; actions trigger the correct admin API calls.
  - Insights table sortable by JS distance; detail view shows bar chart.
  - Recommendations list shows evidence snapshot; approve/reject/rollback work.
  - Data Health shows unmapped reasons and row counts.
  - "Demo only, no auth" note is visible.
- **dependencies:** TASK-17, TASK-03

---

## Step 11 — Tests

### TASK-20: Automated test suite (pytest)
- **id:** TASK-20
- **description:** Write pytest tests covering all 10 acceptance criteria in spec §14. Key tests: ingest (TASK-01 criteria), baseline fixture test, flagging synthetic items, dedup/weighting, triage paths, vision mock paths, admin actions + evidence rules, leak test (scan transcripts for forbidden words), live update increments counts, in-store path never creates a case.
- **acceptance criteria:**
  - `pytest backend/tests/` passes with zero failures.
  - Leak test scans all agent transcripts from the mock-mode scenarios and fails if "loyalty", "suspicion", "fraud", or "risk score" appear.
  - Flagging test: n=100, 60% `too_small` vs. 20% baseline → flagged; n=10 same ratio → not flagged.
  - Triage tests cover all three outcome paths.
  - Vision mock tests cover ok/bad/unclear/refuse flows.
- **dependencies:** TASK-15, TASK-17

---

## Step 12 — README

### TASK-21: Final README and .env.example
- **id:** TASK-21
- **description:** Write the final `README.md` with: project overview, prerequisites, setup instructions (clone, place CSVs, copy .env.example, configure MODEL_ID/AWS_REGION or set LLM_MOCK=true, run ingest, start backend, start frontend), mock mode instructions, demo script (which of the 8 demo customers triggers which path — approve no fee / approve with fee / vision review / human review), assumptions section (ITEM_KEY choice rationale, Strands SDK verification notes, any deviations from spec, store coordinate verification status). Ensure `.env.example` has every variable with defaults and inline comments.
- **acceptance criteria:**
  - A developer unfamiliar with the project can run the full demo following the README alone.
  - Mock mode section explains how to run with `LLM_MOCK=true` and which demo customers exercise which path.
  - Assumptions section lists every non-trivial choice made silently during implementation.
  - `.env.example` has all variables from design.md §9.
- **dependencies:** TASK-20
