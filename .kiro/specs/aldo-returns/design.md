# ALDO Returns Agent — Technical Design

**Version:** 0.1 (Step 0 — Spec)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Browser (React + Vite)                       │
│                                                                     │
│   /  (Customer Page)              /admin (Admin Console)            │
│   ┌───────────────────┐           ┌─────────────────────────────┐   │
│   │ Product Page      │           │ Review Queue                │   │
│   │ Return Modal      │           │ Insights Table              │   │
│   │  ├ Item Search    │           │ Recommendations             │   │
│   │  ├ Chat Window    │           │ Data Health                 │   │
│   │  └ Photo Upload   │           └─────────────────────────────┘   │
│   └───────────────────┘                                             │
└────────────────────────┬────────────────────────────────────────────┘
                         │ HTTP/JSON (Fetch API)
┌────────────────────────▼────────────────────────────────────────────┐
│                   FastAPI Backend  (Python 3.11+)                   │
│                                                                     │
│  app/api/          ← Route handlers, input validation (Pydantic)   │
│  app/policy/       ← Eligibility, triage, fee engine (pure Python) │
│  app/agents/       ← Strands Agents SDK wrappers                   │
│  │  ├ agent1_return_assistant.py  (text-only)                      │
│  │  └ agent2_vision_review.py     (multimodal)                     │
│  app/insights/     ← Insights engine, baseline, anomaly tests      │
│  app/loyalty/      ← Seeded loyalty generation                     │
│  app/ingest/       ← CSV → SQLite pipeline                         │
│  app/kb/           ← @tool functions (knowledge-base access)       │
│  app/config.py     ← Settings from .env (Pydantic BaseSettings)    │
│  app/db.py         ← SQLAlchemy engine + session factory           │
│  app/main.py       ← FastAPI app, router mounts                    │
│                                                                     │
│  backend/storage/uploads/<case_id>/  ← uploaded photos             │
└────────────────────────┬────────────────────────────────────────────┘
                         │ SQLAlchemy / sqlite3
┌────────────────────────▼────────────────────────────────────────────┐
│                     SQLite  (aldo_returns.db)                       │
│  13 tables — see Section 3                                          │
└────────────────────────┬────────────────────────────────────────────┘
                         │ @tool calls
┌────────────────────────▼────────────────────────────────────────────┐
│               Amazon Bedrock  (or mock, via LLM_MOCK)               │
│  Claude vision-capable model — configured via env vars              │
└─────────────────────────────────────────────────────────────────────┘

External data (read-only, git-ignored):
  data/raw/OMS Data_Feb24-Jan26_new.csv
  data/raw/McGill - Returns - Restocking Fees 2024 - 2025.csv
  data/raw/McGill - Returns - Returnable Flag 2024-2025.csv
  data/raw/McGill_Instore_return_exchanges.csv
```

---

## 2. Module Layout

```
aldo-returns/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, lifespan, router mounts
│   │   ├── config.py          # Pydantic BaseSettings, all env vars + defaults
│   │   ├── db.py              # SQLAlchemy engine, session factory, Base
│   │   ├── ingest/
│   │   │   ├── run.py         # Entry point: python -m app.ingest.run
│   │   │   ├── loaders.py     # CSV → DataFrame with normalization
│   │   │   ├── transforms.py  # Joins, cleaning, category derivation
│   │   │   └── category_keywords.yaml
│   │   ├── kb/
│   │   │   └── tools.py       # All @tool functions for Strands agents
│   │   ├── insights/
│   │   │   ├── engine.py      # compute_item_stats, compute_baseline, flag_items
│   │   │   ├── recommendations.py  # create/update recommendations + agent_changes
│   │   │   └── reason_taxonomy.yaml
│   │   ├── loyalty/
│   │   │   └── scores.py      # get_loyalty(), seeded generation
│   │   ├── policy/
│   │   │   ├── eligibility.py # check_eligibility logic
│   │   │   └── triage.py      # compute_triage, suspicion formula
│   │   ├── agents/
│   │   │   ├── agent1_return_assistant.py
│   │   │   └── agent2_vision_review.py
│   │   └── api/
│   │       ├── demo.py        # /api/demo/customers, /api/product
│   │       ├── items.py       # /api/items/search
│   │       ├── stores.py      # /api/stores/nearest
│   │       ├── returns.py     # /api/returns/*
│   │       └── admin.py       # /api/admin/*
│   ├── tests/
│   │   ├── test_ingest.py
│   │   ├── test_baseline.py
│   │   ├── test_flagging.py
│   │   ├── test_triage.py
│   │   ├── test_vision_mock.py
│   │   ├── test_admin_actions.py
│   │   └── test_leak.py
│   └── storage/
│       └── uploads/           # <case_id>/<filename>
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── CustomerPage.tsx
│   │   │   └── AdminPage.tsx
│   │   ├── components/
│   │   │   ├── ReturnModal/
│   │   │   │   ├── ItemSearch.tsx
│   │   │   │   ├── MethodSelector.tsx
│   │   │   │   ├── ReasonDropdown.tsx
│   │   │   │   ├── ChatWindow.tsx
│   │   │   │   └── PhotoUpload.tsx
│   │   │   └── admin/
│   │   │       ├── ReviewQueue.tsx
│   │   │       ├── CaseDetail.tsx
│   │   │       ├── InsightsTable.tsx
│   │   │       ├── RecommendationsList.tsx
│   │   │       └── DataHealth.tsx
│   │   ├── api/               # fetch wrappers
│   │   └── main.tsx
│   ├── index.html
│   ├── vite.config.ts
│   └── tailwind.config.ts
├── data/
│   └── raw/                   # CSV files (git-ignored)
├── docs/
│   └── data_profile.md        # written after Step 1 (data profiling)
├── .env.example
├── .gitignore
└── README.md
```

---

## 3. Data Model (SQLite — 13 tables)

### 3.1 `order_items`
Represents one line item from an OMS order.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `order_number` | TEXT | from OMS |
| `sku` | TEXT | style/size identifier |
| `item_id` | TEXT | normalized product name (or sku — see `ITEM_KEY`) |
| `orderitem_product_name` | TEXT | raw name |
| `customer_id` | TEXT FK → customers | = customer_uid |
| `store_banner` | TEXT | |
| `category` | TEXT | derived from keyword rules |
| `is_final_sale` | BOOLEAN | joined from returnable_flags |
| `created_at` | DATETIME | |

### 3.2 `returns`
One row per completed or live return.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `return_order_number` | TEXT UNIQUE | |
| `order_number` | TEXT FK → order_items | |
| `sku` | TEXT | |
| `item_id` | TEXT | |
| `customer_id` | TEXT FK → customers | |
| `reason_raw` | TEXT | original string from CSV or UI |
| `reason_code` | TEXT | canonical code |
| `reason_group` | TEXT | FIT_SIZE / DEFECT / etc. |
| `item_return_qty` | INTEGER | default 1 |
| `refund_amount` | REAL | |
| `restocking_revenue` | REAL | joined from restocking_fees |
| `return_channel` | TEXT | |
| `return_creation_date` | DATETIME | |
| `last_return_status` | TEXT | raw status value |
| `instore_return_location` | TEXT | |
| `source` | TEXT | 'csv' or 'live' |
| `fraud_flag` | BOOLEAN | default false |
| `quarantined` | BOOLEAN | default false — pending human case |
| `created_at` | DATETIME | |

### 3.3 `returnable_flags`
Final-sale status per order line.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `order_number` | TEXT | |
| `sku` | TEXT | |
| `is_final_sale` | BOOLEAN | |

### 3.4 `restocking_fees`
Per-return restocking revenue.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `return_order_number` | TEXT UNIQUE | |
| `restocking_revenue` | REAL | |

### 3.5 `exchanges`
In-store exchange events.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `original_order_number` | TEXT | joins to order_items.order_number |
| `exchange_sku` | TEXT | |
| `exchange_qty` | INTEGER | |
| `exchange_confidence` | TEXT | |
| `return_store` | TEXT | |
| `business_day_date` | DATETIME | |
| `trans_number` | TEXT | |
| `sales` | REAL | |

### 3.6 `customers`
One row per unique customer.

| Column | Type | Notes |
|---|---|---|
| `customer_id` | TEXT PK | = customer_uid |
| `csnmr_prof_id` | TEXT | |
| `loyalty` | INTEGER | 0–100, seeded RNG |
| `fraud_flag` | BOOLEAN | default false |
| `created_at` | DATETIME | |

### 3.7 `stores`
Distinct in-store return locations.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | from Instore return location |
| `address` | TEXT | nullable |
| `lat` | REAL | nullable |
| `lng` | REAL | nullable |
| `verified` | BOOLEAN | default false |

### 3.8 `cases`
One row per active or resolved return case (online only).

| Column | Type | Notes |
|---|---|---|
| `case_id` | TEXT PK | UUID |
| `customer_id` | TEXT FK → customers | |
| `order_number` | TEXT | |
| `sku` | TEXT | |
| `item_id` | TEXT | |
| `reason_code` | TEXT | |
| `reason_group` | TEXT | |
| `detail` | TEXT | clarifying answer |
| `status` | TEXT | OPEN / APPROVED_NO_FEE / APPROVED_WITH_FEE / VISION_REVIEW / HUMAN_REVIEW / REJECTED / FRAUD |
| `fee_charged` | REAL | 0 or SHIPPING_FEE_CAD |
| `triage_breakdown_json` | TEXT | full JSON of suspicion components |
| `decided_by` | TEXT | 'system', 'agent', or 'admin' |
| `decided_at` | DATETIME | |
| `created_at` | DATETIME | |

### 3.9 `case_messages`
Every turn in the return conversation.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `case_id` | TEXT FK → cases | |
| `role` | TEXT | 'user' or 'assistant' |
| `content` | TEXT | message text |
| `created_at` | DATETIME | |

### 3.10 `case_evidence`
Photo uploads and vision verdicts.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `case_id` | TEXT FK → cases | |
| `file_path` | TEXT | relative path under storage/uploads/ |
| `mime_type` | TEXT | |
| `verdict` | TEXT | consistent / inconsistent / unclear |
| `confidence` | REAL | |
| `observations` | TEXT | |
| `matches_ordered_product` | BOOLEAN | |
| `defect_visible` | BOOLEAN | nullable |
| `submitted_at` | DATETIME | |

### 3.11 `recommendations`
One active recommendation per (item_id, recommendation_type).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `item_id` | TEXT | |
| `category` | TEXT | |
| `recommendation_type` | TEXT | SIZE_GUIDANCE / DEFECT_FLAG / DESCRIPTION_REVIEW |
| `reason_code` | TEXT | the flagged reason that triggered this |
| `draft_text` | TEXT | human-readable suggestion |
| `status` | TEXT | proposed / approved / applied / rejected / rolled_back |
| `approved_by` | TEXT | nullable |
| `created_at` | DATETIME | |
| `applied_at` | DATETIME | nullable |

### 3.12 `agent_changes`
Audit log for every recommendation action.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `item_id` | TEXT | |
| `category` | TEXT | |
| `change_type` | TEXT | SIZE_GUIDANCE / DEFECT_FLAG / DESCRIPTION_REVIEW |
| `before_text` | TEXT | nullable |
| `after_text` | TEXT | |
| `evidence_snapshot_json` | TEXT | n, n_eff, shares, baseline, p_adj, confidence, baseline_scope |
| `status` | TEXT | proposed / approved / applied / rejected / rolled_back |
| `approved_by` | TEXT | nullable |
| `created_at` | DATETIME | |
| `applied_at` | DATETIME | nullable |

### 3.13 `unmapped_reasons`
Tracks raw reason strings that couldn't be mapped to a canonical code.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `raw_value` | TEXT UNIQUE | |
| `count` | INTEGER | |
| `first_seen` | DATETIME | |

---

## 4. API Surface

All endpoints under `/api`. Inputs validated with Pydantic. OpenAPI docs at `/docs`.

### Customer-facing

| Method | Path | Description |
|---|---|---|
| GET | `/api/demo/customers` | List 8 demo customers as "Customer #N" |
| GET | `/api/product` | Demo product info + any applied SIZE_GUIDANCE banner |
| GET | `/api/items/search?customer_id=&q=` | Fuzzy-match items in customer's orders |
| GET | `/api/stores/nearest?lat=&lng=` | 3 nearest stores by Haversine |
| POST | `/api/returns/start` | Create case, return `case_id` |
| POST | `/api/returns/{case_id}/message` | Send text turn; return assistant reply + state |
| POST | `/api/returns/{case_id}/photo` | Multipart upload; return vision verdict + state |
| POST | `/api/returns/{case_id}/refuse-photo` | Mark refusal; triggers human escalation |
| GET | `/api/returns/{case_id}` | Poll for case state |

**Never** return `loyalty`, `suspicion`, `fraud_flag`, or any triage component from the customer-facing endpoints above.

### Admin

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/cases` | List cases in HUMAN_REVIEW (+ stuck VISION_REVIEW) |
| GET | `/api/admin/cases/{id}` | Case detail including loyalty, triage, transcript, images |
| POST | `/api/admin/cases/{id}/decision` | approve_no_fee / approve_with_fee / reject / fraud / release |
| GET | `/api/admin/insights` | Items ranked by JS distance, filterable by category |
| GET | `/api/admin/insights/{item_id}` | Detail: reason distribution vs. baseline, stats |
| GET | `/api/admin/recommendations` | Proposed recommendations with evidence |
| POST | `/api/admin/recommendations/{id}/decision` | approve / reject / rollback |
| GET | `/api/admin/data-health` | Unmapped reasons, row counts, last ingest time |
| POST | `/api/admin/recompute` | Trigger baseline recomputation |

---

## 5. Agent Orchestration Pattern (Strands Agents SDK)

```
Customer sends message
        │
        ▼
┌───────────────────┐
│  Agent 1           │  ← text-only Strands Agent
│  Return Assistant  │     System prompt with hard rules
│                   │     Tools: get_customer_orders, find_order_item,
│                   │            check_eligibility, compute_triage,
│                   │            finalize_return, escalate_to_vision_review,
│                   │            escalate_to_human
└────────┬──────────┘
         │  compute_triage → VISION_REVIEW
         │  calls escalate_to_vision_review tool
         ▼
┌───────────────────┐
│  Agent 2           │  ← multimodal Strands Agent (agents-as-tools pattern)
│  Vision Review     │     Invoked as a tool by Agent 1
│                   │     Tools: (none — image is direct input)
│                   │     Returns: VisionVerdict (Pydantic structured output)
└────────┬──────────┘
         │  verdict → policy engine (Python)
         ▼
  Policy engine: finalize or escalate_to_human
```

**Agents-as-tools:** Agent 2 is registered as a Strands tool callable by Agent 1. The SDK pattern for this must be verified against current Strands docs before implementation (spec §0 rule 4).

**Conversation persistence:** each case holds one Agent 1 instance in memory. On server restart, `case_messages` is used to rehydrate the agent's message history before the next turn.

**Mock mode (`LLM_MOCK=true`):**
- Agent 1 uses a scripted response map keyed on conversation stage.
- Agent 2 evaluates the uploaded filename only.

---

## 6. Insights Engine Algorithm

```
Input: evidence-eligible returns (not fraud_flagged, not quarantined,
       final status approved/completed)

Step 1 — Dedup + weight
  For each (customer_id, item_id): keep only the most recent return.
  w = 0.5 + loyalty / 100        (loyalty via get_loyalty, never exposed)

Step 2 — Item distribution
  s_ir = Σw(reason=r) / Σw        (weighted share per reason)
  n    = distinct customers
  n_eff = (Σw)² / Σw²             (effective sample size)

Step 3 — Category baseline
  Collect items with distinct-customer count ≥ MIN_RETURNS_BASELINE.
  If ≥ 10 such items in category:
      b_r = median(unweighted s_ir) across those items
      renormalize so Σ b_r = 1
      scope = 'category'
  Else:
      use global baseline (same formula across all qualifying items)
      scope = 'global'

Step 4 — Anomaly test (per item, per reason)
  Precondition: n ≥ MIN_RETURNS_FLAG AND distinct customers ≥ 20
  excess   = s_ir − b_r
  ratio    = s_ir / max(b_r, 0.01)
  k        = round(s_ir * n_eff)
  N        = round(n_eff)
  p_one    = binom.sf(k-1, N, b_r)          (scipy.stats.binom.sf)
  p_adj    = p_one * num_reasons             (Bonferroni)
  FLAG if: excess ≥ FLAG_MIN_EXCESS (0.15)
       AND ratio  ≥ FLAG_MIN_RATIO  (1.5)
       AND p_adj  < FLAG_P_ADJ      (0.01)
  confidence = 'high'   if n_eff ≥ 100 AND p_adj < 0.001
             = 'medium'  otherwise

Step 5 — Ranking
  JS distance = sqrt(JSD(item_dist || baseline_dist))
  Items ranked descending by JS distance.

Step 6 — Recommendations
  Map flagged reason → recommendation type → draft text (see spec §6.5).
  Upsert into `recommendations`; write audit row to `agent_changes`.
```

---

## 7. Triage Formula

```
# Computed in app/policy/triage.py — pure Python, no LLM

def compute_suspicion(loyalty, item_id, reason_code, reason_group, refund_amount):

    # Component 1: loyalty (40%)
    loyalty_component = 1 - loyalty / 100

    # Component 2: implausibility (30%)
    count_ir = returns for (item_id, reason_code), evidence-eligible
    n_i      = total evidence-eligible returns for item_id
    b_r      = baseline share for reason_code in item's category
    smoothed = (count_ir + 20 * b_r) / (n_i + 20)
    implausibility = clamp(1 - smoothed / max(b_r, 0.05), 0, 1)

    # Component 3: claim risk by group (20%)
    claim_risk = {DEFECT: 1.0, DESCRIPTION: 0.5, OTHER: 0.5,
                  FIT_SIZE: 0.3, PREFERENCE: 0.2}[reason_group]

    # Component 4: value risk (10%)
    median_refund = median refund_amount for item_id from returns table
    if median_refund is None: median_refund = item_median * 0.3  # fallback
    value_risk = min(1, refund_amount / HIGH_VALUE_CAD)

    suspicion = (0.40 * loyalty_component
               + 0.30 * implausibility
               + 0.20 * claim_risk
               + 0.10 * value_risk)

    return suspicion, {breakdown dict for triage_breakdown_json}
```

Outcome routing:
- `loyalty ≥ HIGH_LOYALTY_THRESHOLD` → `APPROVED_NO_FEE` (skip formula)
- `suspicion < SUSPICION_THRESHOLD` → `APPROVED_WITH_FEE`
- `suspicion ≥ SUSPICION_THRESHOLD` → `VISION_REVIEW`

---

## 8. Frontend Routes and Components

### Routes (React Router)

| Path | Component | Description |
|---|---|---|
| `/` | `CustomerPage` | ALDO demo product page + return modal |
| `/admin` | `AdminPage` | Internal admin console (4 tabs) |

### Key Components

**CustomerPage**
- `ProductCard` — product image placeholder, name, price, description, size selector, SIZE_GUIDANCE banner (conditional)
- `Header` — ALDO wordmark, "Return an item" button, demo customer dropdown
- `ReturnModal` — multi-step modal orchestrating the sub-components below

**ReturnModal steps**
1. `ItemSearch` — text input + autocomplete, calls `GET /api/items/search`
2. `MethodSelector` — "Return online" / "Return in-store" radio
3. `StoreLocator` — Haversine nearest stores or searchable list (in-store path)
4. `ReasonDropdown` — canonical reason codes with friendly labels
5. `ChatWindow` — message list, text input, state-driven rendering
   - `PhotoUpload` — upload control + "I'd rather not share" (conditionally rendered)
   - `PendingReview` — polls `GET /api/returns/{case_id}`

**AdminPage (4 tabs)**
- `ReviewQueue` — paginated case list with status badges
- `CaseDetail` — customer info, transcript, images, triage, vision verdict, action buttons
- `InsightsTable` — sortable table + bar chart via `recharts` (or similar)
- `RecommendationsList` — evidence snapshot + approve/reject/rollback
- `DataHealth` — unmapped reasons table, row counts, recompute button

### State Management
- React Query (TanStack Query) for server state and polling.
- Local component state for modal wizard steps.
- No global store needed at this scale.

---

## 9. Configuration Reference

All values read from environment (`.env` file, with defaults in `config.py`).

| Variable | Default | Used by |
|---|---|---|
| `LLM_MOCK` | `false` | agents |
| `MODEL_PROVIDER` | `bedrock` | agents |
| `MODEL_ID` | _(required when not mock)_ | agents |
| `AWS_REGION` | _(required when not mock)_ | agents |
| `ITEM_KEY` | `name` | ingest, kb tools |
| `LOYALTY_SEED` | `42` | loyalty |
| `HIGH_LOYALTY_THRESHOLD` | `70` | policy |
| `SUSPICION_THRESHOLD` | `0.50` | policy |
| `HIGH_VALUE_CAD` | `150` | policy |
| `SHIPPING_FEE_CAD` | `7.99` | policy |
| `MIN_RETURNS_BASELINE` | `30` | insights |
| `MIN_RETURNS_FLAG` | `30` | insights |
| `MIN_CUSTOMERS_FLAG` | `20` | insights |
| `FLAG_MIN_EXCESS` | `0.15` | insights |
| `FLAG_MIN_RATIO` | `1.5` | insights |
| `FLAG_P_ADJ` | `0.01` | insights |
| `AUTO_APPLY_SIZE_GUIDANCE` | `false` | insights |
| `DEMO_ITEM_NAME` | _(auto: top item by return count)_ | frontend |
