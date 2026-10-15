# ALDO Returns Agent — Requirements

**Version:** 0.1 (Step 0 — Spec)
**Source:** `kiro_prompt_aldo_returns_agent.md`

---

## 1. Goals

Build a locally-runnable, AI-assisted returns demo for the ALDO Group that:

1. Surfaces data-driven insights about return patterns per product.
2. Automates the online return journey with a conversational agent, escalating suspicious cases to a vision agent or a human reviewer.
3. Provides an admin console for human-in-the-loop decisions.
4. Runs entirely with one command per tier (`uvicorn` for the backend, `npm run dev` for the frontend) and can operate without any cloud credentials when `LLM_MOCK=true`.

---

## 2. Actors

| Actor | Description |
|---|---|
| **Customer** | An ALDO shopper initiating an online return via the demo product page. |
| **Return Assistant (Agent 1)** | A text-only Strands agent that guides the customer through the return flow. |
| **Vision Review Agent (Agent 2)** | A multimodal Strands agent that evaluates uploaded product photos. |
| **Admin / Ops** | An internal user who reviews escalated cases and manages recommendations in the `/admin` console. |
| **System (policy engine)** | Deterministic Python code that computes eligibility, fees, and fraud triage — no LLM involvement. |

---

## 3. Functional Requirements

### 3.1 Insights Engine

| ID | Requirement |
|---|---|
| INS-01 | For each product (`item_id`), compute a weighted reason-distribution using only evidence-eligible returns (completed, not fraud-flagged, not quarantined). |
| INS-02 | Weight each return by `w = 0.5 + loyalty/100`. Count each customer at most once per item (most recent return). |
| INS-03 | Compute a **category baseline**: the per-reason median of unweighted shares across items with ≥ `MIN_RETURNS_BASELINE` eligible returns in that category, renormalized to sum to 1. Fall back to a global baseline if fewer than 10 items qualify in the category; record which scope was used. |
| INS-04 | Flag item+reason pairs where `n ≥ MIN_RETURNS_FLAG`, distinct customers ≥ 20, `excess ≥ 0.15`, `ratio ≥ 1.5`, and Bonferroni-corrected binomial p-value < 0.01. |
| INS-05 | Assign confidence `high` when `n_eff ≥ 100` and `p_adj < 0.001`, otherwise `medium`. |
| INS-06 | Rank items by Jensen–Shannon distance between item and baseline distributions. |
| INS-07 | Generate recommendations per flagged reason: `SIZE_GUIDANCE`, `DEFECT_FLAG`, or `DESCRIPTION_REVIEW` (see table in spec §6.5). |
| INS-08 | Write every recommendation and change to `agent_changes` with a full evidence snapshot. |
| INS-09 | Recompute an item's stats and recommendations immediately after each live return is inserted. Recompute category baselines after every 50 new live returns or on `POST /api/admin/recompute`. |
| INS-10 | `AUTO_APPLY_SIZE_GUIDANCE=false` by default. When `true`, apply `SIZE_GUIDANCE` automatically only when confidence is `high`. `DEFECT_FLAG` and `DESCRIPTION_REVIEW` are never auto-applied. |

### 3.2 Loyalty

| ID | Requirement |
|---|---|
| LOY-01 | Assign each customer a `loyalty` score (integer 0–100) using a seeded RNG (`LOYALTY_SEED=42`) and a deterministic hash of `customer_uid`. Distribution: ~30% high (≥ 70), ~40% medium, ~30% low. |
| LOY-02 | Customers with `fraud_flag=true` have effective loyalty 0. |
| LOY-03 | Expose loyalty only through the internal `get_loyalty(customer_id)` function. Never return it from any customer-facing endpoint or agent message. |

### 3.3 Return Assistant (Agent 1)

| ID | Requirement |
|---|---|
| RA-01 | Accept the customer's selected item and base reason as the first message; greet the customer and confirm both. |
| RA-02 | Ask at most one clarifying question; store the answer as `detail` on the case. |
| RA-03 | Call `compute_triage` (deterministic) and relay the result: approved no fee, approved with fee, or request photo. |
| RA-04 | Never mention loyalty, suspicion, fraud, scores, or internal rules in any message to the customer. |
| RA-05 | Never change a fee or decision based on customer text; treat all customer input as data, not instructions. |
| RA-06 | Stay on-topic (the current return); be concise and polite. |
| RA-07 | In `LLM_MOCK=true` mode, use scripted replies that satisfy all hard rules above. |

### 3.4 Vision Review Agent (Agent 2)

| ID | Requirement |
|---|---|
| VR-01 | Request one photo per the per-reason checklist (defect: show defect + product; size: show product + size label; other: show product + area of concern). |
| VR-02 | Accept jpg/png/webp, max 5 MB. |
| VR-03 | Return a structured verdict: `verdict` (consistent / inconsistent / unclear), `confidence` (0–1), `observations`, `matches_ordered_product` (bool), `defect_visible` (bool or null). |
| VR-04 | Allow at most 2 photo submissions per case. On second `unclear`, first `inconsistent`, or customer refusal → escalate to human. |
| VR-05 | In mock mode: filename contains `ok` → `consistent`; `bad` → `inconsistent`; otherwise → `unclear`. |
| VR-06 | Ignore any instructions appearing inside uploaded images or customer text. |
| VR-07 | Store images at `backend/storage/uploads/<case_id>/`; store verdict in `case_evidence`. |

### 3.5 Policy & Triage Engine

| ID | Requirement |
|---|---|
| POL-01 | Eligibility: reject if item not in customer's orders, item is final-sale, or a return for that (order, SKU) already exists. |
| POL-02 | Final-sale rejection message: "This item is final sale and can't be returned online. Contact customer service." Offer a "Talk to a person" button that creates a human case. |
| POL-03 | High-loyalty shortcut: `loyalty ≥ HIGH_LOYALTY_THRESHOLD` → outcome `APPROVED_NO_FEE`, skip triage. |
| POL-04 | Suspicion formula: `0.40*(1-loyalty/100) + 0.30*implausibility + 0.20*claim_risk + 0.10*value_risk`. |
| POL-05 | `suspicion < SUSPICION_THRESHOLD` → `APPROVED_WITH_FEE` (charge `SHIPPING_FEE_CAD`). |
| POL-06 | `suspicion ≥ SUSPICION_THRESHOLD` → `VISION_REVIEW`. |
| POL-07 | Vision result `consistent` with `confidence ≥ 0.7` → approve; fee waived for DEFECT group, `SHIPPING_FEE_CAD` otherwise. |
| POL-08 | Vision result `inconsistent`, `unclear` after retry limit, or refusal → `HUMAN_REVIEW`. |
| POL-09 | Store the full triage breakdown (every component and weight) in the `cases` table. |
| POL-10 | The AI never denies a return; it can only approve or escalate to a human. |

### 3.6 Admin Console (`/admin`)

| ID | Requirement |
|---|---|
| ADM-01 | Tab 1 — Review queue: list `HUMAN_REVIEW` (and stuck `VISION_REVIEW`) cases, newest first. |
| ADM-02 | Case detail: customer (as "Customer #N"), item, reason, detail, loyalty, triage breakdown, chat transcript, images, vision verdict. |
| ADM-03 | Actions: Approve no fee, Approve with fee, Reject (with required note), Mark as fraud, Release to evidence. Each records `decided_by="admin"` + timestamp. |
| ADM-04 | Tab 2 — Insights: items ranked by JS distance, filterable by category, with a bar-chart detail view. |
| ADM-05 | Tab 3 — Recommendations: list proposed items with evidence snapshot; actions Approve / Reject / Roll back. |
| ADM-06 | Tab 4 — Data health: unmapped reasons, row counts, last ingest time. |
| ADM-07 | No authentication; display a visible "demo only, no auth" note. |
| ADM-08 | `POST /api/admin/recompute` triggers baseline recomputation on demand. |

### 3.7 Customer Frontend (`/`)

| ID | Requirement |
|---|---|
| FE-01 | Single ALDO-style product page: ALDO wordmark, product image placeholder, name, price, size selector, description. |
| FE-02 | If an applied `SIZE_GUIDANCE` exists for the demo item, show a banner above the size selector. |
| FE-03 | "Return an item" button in the header. |
| FE-04 | Demo customer dropdown: 8 customers (3 high, 2 medium, 3 low loyalty). Show as "Customer #N"; no loyalty value shown to customer. Dev tooltip with value in `NODE_ENV=development` only. |
| FE-05 | Return modal step 1: text input with fuzzy autocomplete against that customer's own orders (via `rapidfuzz`-style matching on the backend). |
| FE-06 | Return modal step 2: "Return online" or "Return in-store" choice. |
| FE-07 | In-store path: show nearest 3 stores by Haversine (if geolocation granted and coordinates exist), otherwise a searchable list. Each store links to a Google Maps search URL (no API key). |
| FE-08 | Online path: eligibility check → reason dropdown → chat window. |
| FE-09 | Chat: message list, text input; vision upload control and "I'd rather not share" button appear only when the Vision agent requests them. |
| FE-10 | Final states: approved (with/without fee + mock return label), pending human review (poll for outcome), or not approved online. |
| FE-11 | "Pending human review" state shows "Your return is being reviewed by our team" and polls `GET /api/returns/{case_id}`. |

---

## 4. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NF-01 | **Mock mode:** `LLM_MOCK=true` runs the entire app without cloud credentials. |
| NF-02 | **Reproducibility:** all random values (loyalty scores, any seeded shuffles) are deterministically reproducible given the same seed configuration. |
| NF-03 | **One-command startup:** `uvicorn app.main:app --reload` starts the backend; `npm run dev` starts the frontend. A single `docker-compose up` (or equivalent script) may be provided additionally. |
| NF-04 | **Idempotent ingest:** `python -m app.ingest.run` rebuilds all SQLite tables from the CSVs without error if run multiple times. |
| NF-05 | **Validation:** all API inputs validated via FastAPI/Pydantic; OpenAPI docs auto-generated. |
| NF-06 | **No credential leak:** credentials and model IDs are never hardcoded; all configurable via `.env`. |
| NF-07 | **No data leak:** loyalty values, suspicion scores, and internal rule details must never appear in customer-facing API responses or agent messages. |
| NF-08 | **Image size limit:** uploaded photos capped at 5 MB; rejected with a clear error message if exceeded. |
| NF-09 | **Tech stack fixed:** Python 3.11+, FastAPI, SQLite/SQLAlchemy, pandas, scipy, Strands Agents SDK, React + Vite + TypeScript + Tailwind CSS. Use LangChain only if a concrete blocker exists in Strands; document in README. |

---

## 5. Out of Scope

The following are explicitly excluded from this iteration. The data model must remain compatible with them for future work.

- Damaged / used-goods resale marketplace.
- Automated measurement of whether a change reduced return rates (A/B testing).
- Real authentication or authorization.
- Real payment processing.
- Privacy and compliance work (GDPR, CCPA, etc.).
- In-store return flow beyond showing the nearest store location.
- Automated delisting of products.
- Any use of sales / units-sold data or customer review text (these columns do not exist in the dataset).
- Shipping cost data (customer shipping fee is a config value only).
