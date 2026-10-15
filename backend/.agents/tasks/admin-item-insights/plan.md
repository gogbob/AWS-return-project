# Implementation Plan — Admin "Item insights" page

Goal: add an admin-console page listing every product as an expandable dropdown row. Expanding a
row shows a horizontal bar chart of that item's return-reason shares vs. its category baseline,
an overshoot indicator, a red header + warning sign when the item overshoots its baseline too much
on any reason, and a recommendation/change status (change applied + when, or "needs admin input").

This feature COMPOSES the existing insights engine and admin routes. It does NOT re-derive
analytics. Both dev servers are already running with hot reload — do not start or restart them.

## Design decisions (grounded in the code I read)

1. New endpoint `GET /api/admin/item-insights` instead of overloading `/api/admin/insights`.
   Rationale: the existing list endpoint returns a flat ranked table (`n`, `n_eff`, `js_distance`,
   `flags`); this page needs per-item distribution + baseline + overshoot + recommendation status in
   one payload so the row can expand without a second fetch. A dedicated endpoint keeps the existing
   Insights tab untouched (`routes_admin.list_insights` stays as-is).

2. Endpoint reuses the exact engine calls already used by `get_insight_detail`
   (`compute_item_distribution`, `compute_category_baseline` / `compute_global_baseline`,
   `compute_anomaly`) and the same JSON rounding conventions (4 dp for shares, 2 dp for `n_eff`).
   Rationale: identical math and shapes to the proven detail endpoint; no new statistics.

3. "Overshoot" is defined off the existing `FlagResult`: a reason overshoots when
   `excess >= settings.flag_min_excess` (0.15, imported from `config.settings`, not hardcoded).
   "Overshoots too much" (→ red row + warning sign) means the item has at least one reason whose
   flag is BOTH `significant` AND `excess >= settings.flag_min_excess`. Rationale: `significant`
   already folds in the binomial + Bonferroni + ratio thresholds the engine owns; reusing it keeps
   the red state consistent with how `generate_recommendations` decides to act.

4. "Needs admin input" is the DEFECT-group signal, matching `generate_recommendations`
   (group `DEFECT` → `recommendation_type='quality_review'`, "escalate to QA"). An item
   `needs_input=true` when it has a significant overshooting flag whose `group == 'DEFECT'`.
   Rationale: the GOAL's "elevated DEFECT returns advise a production/quality change" maps exactly
   to the engine's existing DEFECT→quality_review path.

5. "Change made + when" comes from the `recommendations` table (columns confirmed in
   `ingest/models.py`: `status`, `updated_at`, `created_at`). An "applied" change = the most recent
   recommendation row for the item with `status='applied'`; its `updated_at` is the "when".
   `status` values: `proposed` → pending/needs review; `applied` → change made; `rejected`/
   `rolled_back` → no active change. Rationale: this is the only persisted record of an
   admin-approved change (`recommendation_decision` sets `status='applied'`); no new table needed.

6. Field-name contract: the backend will emit the EXACT keys the new TS interface declares. Note a
   pre-existing mismatch — `get_insight_detail` returns `name`/`distribution`/`baseline` while the
   old `InsightDetail` TS type reads `item_name`/`item_shares`/`baseline_shares`. I am NOT reusing
   that type; the new endpoint and new interface share one self-consistent contract (below) so the
   coder never has to reconcile them.

7. Sorting: rows returned most-concerning first — overshooting (red) items first, then by number of
   significant flags desc, then by max `excess` desc, then items with returns before items with
   `n==0`, then item name asc for stability. Rationale: an admin triaging quality problems should
   see red/needs-input items at the top without scrolling.

## Exact JSON shape for `GET /api/admin/item-insights`

Returns a JSON array; one object per `order_items` row (ALL products, including `n==0` and
category `other`). Each object:

```
{
  "item_id": "ITEM-BOOTS",
  "item_name": "ALDO Grandbaia Boot",
  "category": "boot",                 // may be null
  "baseline_scope": "category",       // "category" | "global"
  "n": 42,                            // raw return count for the item
  "n_eff": 51.5,                      // effective (weighted) sample size, 2 dp
  "overshoot": true,                  // decision 3: >=1 significant flag with excess>=flag_min_excess
  "needs_input": true,                // decision 4: a significant DEFECT-group overshoot exists
  "reasons": [                        // one entry per reason code seen in item OR baseline
    {
      "reason_code": "too_small",
      "group": "FIT_SIZE",
      "item_share": 0.5231,           // 4 dp, 0 when absent
      "baseline_share": 0.2140,       // 4 dp, 0 when absent
      "excess": 0.3091,               // item_share - baseline_share, 4 dp
      "significant": true,            // FlagResult.significant
      "overshoot": true               // significant AND excess >= flag_min_excess
    }
  ],
  "recommendation": {                 // null when the item has no recommendation row
    "status": "applied",             // proposed|applied|rejected|rolled_back
    "recommendation_type": "quality_review",
    "reason_code": "defect",
    "change_applied": true,           // status == "applied"
    "changed_at": "2025-01-04T12:00:00"  // updated_at ISO string, null if never applied
  }
}
```

Rules:
- `reasons` is built from the union of item-distribution codes and baseline codes (same union logic
  as `get_insight_detail`), excluding the metadata keys `n`, `n_eff`, `scope`, `n_items`.
- A reason's `significant`/`excess` come from the matching `FlagResult` in `compute_anomaly`; when
  `compute_anomaly` returns `[]` (below `min_returns_flag`/`min_customers_flag`), all reasons get
  `significant=false`, `excess = item_share - baseline_share`, `overshoot=false`, and the item's
  top-level `overshoot`/`needs_input` are both `false`.
- `reasons` sorted by `excess` desc so the chart lists the biggest deviations first.
- Top-level `overshoot` = any `reason.overshoot`; `needs_input` = any `reason.overshoot` with
  `group=='DEFECT'`.
- `recommendation` = the most recent `recommendations` row for `item_id`
  (`ORDER BY updated_at DESC, id DESC LIMIT 1`); `null` if none. `changed_at` is `updated_at` only
  when `status=='applied'`, else `null`.
- Array sorted per decision 7.

## Plan items

- [ ] 1. Add `GET /api/admin/item-insights` to `routes_admin.py`.
      Query all `order_items (item_id, orderitem_product_name, category)`. For each item call
      `compute_item_distribution`, pick `compute_category_baseline(category)` (or
      `compute_global_baseline` when category is null) exactly as `get_insight_detail` does, and
      `compute_anomaly(item_id, db)`. Build the `reasons` list from the union of distribution +
      baseline codes (strip `_meta` keys), attaching each reason's `FlagResult`
      (`significant`, `excess`, `group`); compute per-reason `overshoot = significant and excess >=
      settings.flag_min_excess`. Import `settings` from `..config` (the module already imports from
      `..config` indirectly via engine — add an explicit `from ..config import settings`). Derive
      top-level `overshoot`/`needs_input`. Fetch the latest `recommendations` row per item for the
      `recommendation` block. Wrap each item's computation in try/except and skip on error (same
      defensive pattern as `list_insights`). Sort the array per decision 7. Return the array.
      Files: `backend/app/api/routes_admin.py`
      Verify: with the backend already running, `curl -s http://127.0.0.1:8000/api/admin/item-insights`
      returns a JSON array whose objects contain `item_id`, `reasons`, `overshoot`, `needs_input`,
      and `recommendation`; AND run the backend test suite (item 2) which asserts the shape.

- [ ] 2. Add a backend test for the new endpoint in `backend/tests/test_admin.py`.
      Follow the existing `test_db` / `test_client` fixtures and the `_insert_item_with_returns`
      pattern from `tests/test_insights.py` (import or replicate it) to seed one high-defect item and
      one normal item. Assert: `GET /api/admin/item-insights` returns 200 and a list; every object
      has keys `item_id,item_name,category,baseline_scope,n,n_eff,overshoot,needs_input,reasons,
      recommendation`; each `reasons[i]` has `reason_code,group,item_share,baseline_share,excess,
      significant,overshoot`; and the response passes the existing `_contains_forbidden` leak check
      (no loyalty/suspicion/fraud tokens in keys or string values).
      Files: `backend/tests/test_admin.py`
      Verify: from `backend/`, run `python -m pytest tests/test_admin.py -q` — all tests pass,
      including the new one.

- [ ] 3. Add the `ItemInsight` TypeScript interfaces and `getItemInsights()` to the API client.
      Add interfaces `ItemInsightReason` (`reason_code,group,item_share,baseline_share,excess,
      significant,overshoot`), `ItemInsightRecommendation` (`status,recommendation_type,reason_code,
      change_applied,changed_at` with `changed_at: string | null`), and `ItemInsight`
      (`item_id,item_name,category(string|null),baseline_scope,n,n_eff,overshoot,needs_input,
      reasons: ItemInsightReason[], recommendation: ItemInsightRecommendation | null`). Add
      `getItemInsights(): Promise<ItemInsight[]> { return request('/admin/item-insights'); }` to the
      `api` object, following the existing `getInsights`/`getActivity` style (BASE='/api', `request`
      helper). These names mirror the backend contract exactly (decision 6).
      Files: `frontend/src/api/client.ts`
      Verify: from `frontend/`, run `npx tsc --noEmit` — no type errors.

- [ ] 4. Create the `ItemInsights.tsx` admin component.
      Build a list of collapsible rows. Each row is a header + a collapsible panel:
      - Header is a real `<button type="button">` spanning the row with `aria-expanded={open}` and
        `aria-controls` pointing to the panel `id`; it toggles open state (useState per row, e.g. an
        `openId` string|null). Show item name, category, and `n`.
      - When `item.overshoot` is true, render the header RED (e.g. `bg-red-50 border-red-300
        text-red-800`, reuse the red palette already used by the Stop button / status badges in
        `ActivityLog.tsx`) and show a warning sign (⚠) with an accessible label:
        `<span role="img" aria-label="Overshoots category baseline">⚠</span>`.
      - When `item.needs_input` is true, show a "Needs admin input" badge (reuse the yellow badge
        style from `ActivityLog.statusBadge` / `InsightsTable` flag chips).
      - Recommendation status text: if `recommendation?.change_applied`, show
        "Change applied · {new Date(changed_at).toLocaleString()}"; else if
        `recommendation?.status==='proposed'`, show "Change proposed — awaiting review"; else if
        `needs_input`, show "Needs admin input: advise production/quality change"; else "No change".
      - Expanded panel: horizontal (sideways) bar chart over `reasons`, reusing the two-bar
        item-vs-baseline pattern and `maxVal` scaling from `InsightDetail.tsx` (black bar = item,
        gray bar = baseline, percentage labels). Use FRONTEND friendly labels via a local
        `REASON_LABELS` map: too_small→'Too small', too_big→'Too big',
        too_narrow→'Too narrow/uncomfortable', too_wide→'Too wide', defect→'Defective / quality',
        not_as_described→'Not as described', changed_mind→'Changed mind / preference', other→'Other';
        fall back to the raw code when unknown. Mark overshooting reasons visibly (e.g. red text or a
        small ⚠ with `aria-label` on that reason row).
      - Loading state (`text-sm text-gray-400`) and empty state, matching the other admin components.
      Fetch data in `useEffect` via `api.getItemInsights()`.
      Files: `frontend/src/components/admin/ItemInsights.tsx`
      Verify: from `frontend/`, run `npx tsc --noEmit` — compiles with no errors (full UI check in
      item 5's verify).

- [ ] 5. Wire the new "Item insights" tab into `AdminPage.tsx`.
      Add `'item-insights'` to the `Tab` union, add `{ key: 'item-insights', label: 'Item insights' }`
      to the `TABS` array (place it right after the existing `insights` entry), import
      `ItemInsights`, and render `{activeTab === 'item-insights' && <ItemInsights />}` in the tab
      content block. Follow the exact existing tab pattern.
      Files: `frontend/src/pages/AdminPage.tsx`
      Verify: from `frontend/`, run `npm run build` (tsc + vite build) — build succeeds with no type
      errors; then load the admin console in the browser (already-running vite on :5173), click the
      "Item insights" tab, confirm rows render, a row expands/collapses on click (and via keyboard),
      an overshooting item shows a red header with the ⚠ sign, and the bar chart shows friendly
      reason labels.

## Notes / assumptions

- The engine's `min_returns_flag` (30) and `min_customers_flag` (20) mean most demo items will have
  `compute_anomaly == []` and thus `overshoot=false`. That is correct behavior, not a bug — only
  items with enough evidence can turn red. The seeded test item in item 2 must exceed those
  thresholds to exercise the red/needs_input path.
- Tests run from the `backend/` directory (conftest sets up a shared-cache in-memory SQLite and
  overrides `get_db`); there is no repo-level pytest config, so always `cd backend` first.
- No new dependencies are introduced on either side.
