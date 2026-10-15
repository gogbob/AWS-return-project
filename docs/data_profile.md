# Data Profile — ALDO Returns Detective Agent

**Generated from:** `c:\AWS\AWS-return-project\data\raw\`  
**Files profiled:** 4  
**Purpose:** Authoritative record of real CSV structure, normalization mapping, categorical values, and data quality decisions for the return detective pipeline.

---

## Table of Contents

1. [File Inventory](#file-inventory)
2. [Normalization Rules](#normalization-rules)
3. [File 1 — Restocking Fees](#file-1--restocking-fees)
4. [File 2 — Returnable Flag (Final Sale)](#file-2--returnable-flag-final-sale)
5. [File 3 — Instore Return & Exchanges](#file-3--instore-return--exchanges)
6. [File 4 — OMS Returns Data](#file-4--oms-returns-data)
7. [`last_return_status` Keep / Exclude Decision](#last_return_status-keep--exclude-decision)
8. [SKU Granularity & ITEM_KEY Recommendation](#sku-granularity--item_key-recommendation)
9. [Store Location List (`instore_return_location`)](#store-location-list-instore_return_location)

---

## File Inventory

| # | Filename | Size | Data Rows |
|---|----------|------|-----------|
| 1 | `McGill - Returns - Restocking Fees 2024 - 2025.csv` | 12.2 MB | **521,660** |
| 2 | `McGill - Returns - Returnable Flag 2024-2025.csv` | 47.1 MB | **1,597,921** |
| 3 | `McGill_Instore_return_exchanges.csv` | 3.7 MB | **61,934** |
| 4 | `OMS Data_Feb24-Jan26_new.csv` | 137.2 MB | **649,745** |

---

## Normalization Rules

The following transformation pipeline is applied to every raw header before use in the pipeline:

1. **Trim** leading/trailing whitespace.
2. **Lowercase** all characters.
3. **Replace** spaces and punctuation (`-`, `/`, `?`, `(`, `)`, `.`) with `_`.
4. **Collapse** consecutive underscores to a single `_`.
5. Apply **alias overrides**:

| Raw (after trim + lower + replace) | Normalized alias |
|------------------------------------|-----------------|
| `return_oder_number` | `return_order_number` |
| `return_creation_data` | `return_creation_date` |
| `is_item_final_sale_` | `is_final_sale` |

> **Note:** The alias `is_item_final_sale_` is the normalized form of the raw header `Is item final sale?`. The trailing `_` (from the `?`) is collapsed into the alias `is_final_sale`.

---

## File 1 — Restocking Fees

**Filename:** `McGill - Returns - Restocking Fees 2024 - 2025.csv`  
**Row count:** 521,660  
**Encoding:** UTF-8 (no BOM)

### Real Headers (as read from file)

```
"return_order_number","restocking revenue"
```

### Normalized Headers

| Position | Raw Header | Normalized | Notes |
|----------|-----------|------------|-------|
| 0 | `return_order_number` | `return_order_number` | Already normalized — matches spec exactly |
| 1 | `restocking revenue` | `restocking_revenue` | Space replaced with `_` |

### Deviations from Spec

None. The spec alias `return_oder_number → return_order_number` does **not** apply here — this file's header is already correctly spelled `return_order_number`.

### Column Values

**`restocking_revenue`** — numeric, 3 distinct values:

| Value | Meaning |
|-------|---------|
| `0.00` | No fee charged |
| `4.95` | Standard restocking fee tier 1 |
| `5.95` | Standard restocking fee tier 2 |

> This column is effectively a categorical fee tier despite being numeric. A zero value means no restocking fee was levied.

### Join Key

`return_order_number` — links to File 4 (`OMS Data`) on `return_order_number`.

---

## File 2 — Returnable Flag (Final Sale)

**Filename:** `McGill - Returns - Returnable Flag 2024-2025.csv`  
**Row count:** 1,597,921  
**Encoding:** UTF-8 (no BOM)

### Real Headers (as read from file)

```
"order_number","sku","Is item final sale?"
```

### Normalized Headers

| Position | Raw Header | Normalized | Notes |
|----------|-----------|------------|-------|
| 0 | `order_number` | `order_number` | No change |
| 1 | `sku` | `sku` | No change |
| 2 | `Is item final sale?` | `is_final_sale` | Lowercased, spaces → `_`, `?` dropped → alias applied |

### Alias Applied

`is_item_final_sale_` → `is_final_sale`  
(Raw `Is item final sale?` normalizes to `is_item_final_sale_`; the alias strips the trailing `_` and renames to `is_final_sale`.)

### Deviations from Spec

The spec listed the alias source as `is_item_final_sale?` — the actual raw header is `Is item final sale?`. Both normalize to the same form before aliasing; no functional deviation.

### Distinct Values

**`is_final_sale`:**

| Value | Count |
|-------|-------|
| `no` | 1,201,672 |
| `yes` | 396,249 |

> `yes` = item is final-sale / non-returnable. These rows should be flagged in the pipeline as ineligible for return credit.

### Join Keys

- `order_number` — links to File 4 (`OMS Data`) on `order_number`
- `sku` — links on `sku` within the same order

---

## File 3 — Instore Return & Exchanges

**Filename:** `McGill_Instore_return_exchanges.csv`  
**Row count:** 61,934  
**Encoding:** UTF-8 (no BOM)  
**Date range:** 2024 – January 2026 (M/D/YYYY format)

### Real Headers (as read from file)

```
Exchange_Confidence,transactions_businessdaydate,transactions_transnumber,Original Order Number,Exchange SKU,Exchange QTY,Return store,Sales
```

> Note: This file uses **no quoting** (unquoted CSV), unlike the other three files which quote all fields.

### Normalized Headers

| Position | Raw Header | Normalized | Notes |
|----------|-----------|------------|-------|
| 0 | `Exchange_Confidence` | `exchange_confidence` | Lowercased |
| 1 | `transactions_businessdaydate` | `transactions_businessdaydate` | Already snake_case |
| 2 | `transactions_transnumber` | `transactions_transnumber` | Already snake_case |
| 3 | `Original Order Number` | `original_order_number` | Spaces → `_`, lowercased |
| 4 | `Exchange SKU` | `exchange_sku` | Space → `_`, lowercased |
| 5 | `Exchange QTY` | `exchange_qty` | Space → `_`, lowercased |
| 6 | `Return store` | `return_store` | Space → `_`, lowercased |
| 7 | `Sales` | `sales` | Lowercased |

### Deviations from Spec

This file's headers differ significantly from the OMS-centric spec headers. It is a separate exchange-transaction table, not a direct subset of the OMS schema. The join point is `original_order_number` ↔ OMS `order_number`.

### Distinct Values

**`exchange_confidence`:**

| Value | Meaning |
|-------|---------|
| `CONFIRMED` | Exchange transaction explicitly linked to a return |
| `ASSUMED` | Exchange inferred / probabilistically matched |

> The `ASSUMED` category should be treated with lower confidence in insight calculations. Consider filtering to `CONFIRMED` only for high-precision analyses.

**`return_store`:** 395 distinct store IDs — see [Store Location List](#store-location-list-instore_return_location) below.

**`sales`:** Numeric (revenue amount per exchange line). No categorical analysis applicable.

### Join Key

`original_order_number` — links to File 4 (`OMS Data`) on `order_number`.  
`exchange_sku` — links to File 2 on `sku` for final-sale check on the exchanged item.

---

## File 4 — OMS Returns Data

**Filename:** `OMS Data_Feb24-Jan26_new.csv`  
**Row count:** 649,745  
**Encoding:** UTF-8 (no BOM)  
**Date range:** 2024-02-04 to 2026-01-31

### Real Headers (as read from file)

```
"store_banner","order_number","sku","orderitem_product_name","cnsmr_prof_sid","customer_uid",
"return_order_number","return_channel","return_creation_date","return_creation_date_tmstmp",
"last return status","Refund amount","item return qty","reason","Instore return location"
```

### Normalized Headers

| Position | Raw Header | Normalized | Alias Applied? | Notes |
|----------|-----------|------------|---------------|-------|
| 0 | `store_banner` | `store_banner` | — | No change |
| 1 | `order_number` | `order_number` | — | No change |
| 2 | `sku` | `sku` | — | No change |
| 3 | `orderitem_product_name` | `orderitem_product_name` | — | Already snake_case |
| 4 | `cnsmr_prof_sid` | `cnsmr_prof_sid` | — | Already snake_case |
| 5 | `customer_uid` | `customer_uid` | — | No change |
| 6 | `return_order_number` | `return_order_number` | — | Already normalized |
| 7 | `return_channel` | `return_channel` | — | No change |
| 8 | `return_creation_date` | `return_creation_date` | — | Already normalized |
| 9 | `return_creation_date_tmstmp` | `return_creation_date_tmstmp` | — | Already snake_case |
| 10 | `last return status` | `last_return_status` | — | Spaces → `_`, lowercased |
| 11 | `Refund amount` | `refund_amount` | — | Space → `_`, lowercased |
| 12 | `item return qty` | `item_return_qty` | — | Spaces → `_`, lowercased |
| 13 | `reason` | `reason` | — | No change |
| 14 | `Instore return location` | `instore_return_location` | — | Spaces → `_`, lowercased |

### Deviations from Spec

| Spec Expected Header | Real Header | Status |
|---------------------|------------|--------|
| `return_oder_number` (alias source) | `return_order_number` | **No deviation** — file already has the correct spelling; alias not needed |
| `return_creation_data` (alias source) | `return_creation_date` | **No deviation** — file already has correct spelling |
| `is_item_final_sale?` | Not present (lives in File 2) | Correct — this column belongs in File 2, not File 4 |

> All three spec aliases target corrections for *expected* raw typos. The actual data in File 4 does not have those typos — the headers are already correct. The aliases remain defined for robustness in case older data exports reintroduce them.

### Distinct Values — Categorical Columns

**`store_banner`** (5 values):

| Value | Description |
|-------|-------------|
| `ALDO_CA` | ALDO Canada |
| `ALDO_US` | ALDO United States |
| `CIS_CA` | Call It Spring Canada |
| `SPERRY_CA` | Sperry Canada |
| `SPERRY_US` | Sperry United States |

**`return_channel`** (5 values):

| Value | Description |
|-------|-------------|
| `CCC` | Customer Care Centre (agent-initiated) |
| `HappyReturnBar` | Happy Returns drop-off bar |
| `HappyReturnMail` | Happy Returns mail-in |
| `InStore` | In-store return |
| `Web` | Self-serve web / portal return |

**`last_return_status`** — see dedicated section below.

**`reason`** — 44 distinct values observed (including empty string and `return.page.reasons.*` prefixed variants which are truncated API codes):

| Canonical reason | Variants / Notes |
|-----------------|-----------------|
| `Arrived in poor condition` | `return.page.reasons.Arrived in poor cond` (truncated) |
| `Arrived too late` | `return.page.reasons.Arrived too late` |
| `Changed my mind / Other` | `Changed my mind / other`, `Changed idea`, `return.page.reasons.Changed my mind / Ot`, `return.page.reasons.J'ai changé d'idée /` (French) |
| `Defective` | — |
| `Found something else` | — |
| `Gift return` | — |
| `Lost in transit` | `Lost In Transit` (case variant) |
| `Manufacturer defect` | `return.page.reasons.Manufacturer defect` |
| `Missing Item` | — |
| `Not as advertised` | — |
| `Not satisfied with the colour` | — |
| `Not what I expected` | `return.page.reasons.Not what I expected`, `return.page.reasons.Ne répond pas aux at` (French, truncated) |
| `Poor quality` | — |
| `Return Extension` | `Return extension` (case variant) |
| `Return To Sender` | `Return to sender` (case variant) |
| `Return Without Merchandise` | `Return without merch` (abbreviated), `return.page.reasons.Inconfortable` (mismapped) |
| `Too big` | `return.page.reasons.Too big`, `return.page.reasons.Trop grand` (French) |
| `Too small` | `return.page.reasons.Too small` |
| `Uncomfortable` | `return.page.reasons.Uncomfortable` |
| `Warranty` | — |
| `Wrong Item` | — |
| `Wrong item delivered` | `return.page.reasons.Wrong item delivered` |
| `Wrong size delivered` | `return.page.reasons.Wrong size delivered` |
| *(empty)* | No reason recorded; treat as unknown |

> **Data quality note:** The `return.page.reasons.*` prefix values are API slugs with hard truncation at ~40 characters. They should be mapped to their canonical English equivalents during ETL. French variants indicate Canadian bilingual data.

**`instore_return_location`** — numeric store IDs. See [Store Location List](#store-location-list-instore_return_location).

---

## `last_return_status` Keep / Exclude Decision

### All Distinct Values (full dataset)

| Status Value | Sample Count (300k rows) | Proportion | Decision |
|-------------|--------------------------|------------|----------|
| `RETURN_COMPLETED` | 277,436 | 92.5% | ✅ **KEEP** |
| `RETURN_SHIPPING` | 13,396 | 4.5% | ⚠️ **EXCLUDE** |
| `RECEIVED_AT_BAR` | 2,392 | 0.8% | ⚠️ **EXCLUDE** |
| `REFUND_FAILED` | 2,079 | 0.7% | ⚠️ **EXCLUDE** |
| `RETURN_RECEIVED` | 1,505 | 0.5% | ✅ **KEEP** |
| `CREATED` | 1,429 | 0.5% | ⚠️ **EXCLUDE** |
| `RETURN_RCV_IN_PROGRESS` | 1,292 | 0.4% | ⚠️ **EXCLUDE** |
| `SHOPIFY_RETURN_CREATION_FAILED` | 287 | 0.1% | ⚠️ **EXCLUDE** |
| `RETURN_PROCESSED` | 150 | 0.1% | ✅ **KEEP** |
| `LABEL_BOOKING_FAILED` | 24 | <0.1% | ⚠️ **EXCLUDE** |
| `RETURN_TAX_FAILED` | 10 | <0.1% | ⚠️ **EXCLUDE** |

### Decision Rationale

**KEEP** (evidence-grade, return resolved):

| Status | Rationale |
|--------|-----------|
| `RETURN_COMPLETED` | Terminal success state — refund issued, return cycle finished. Primary analysis target (92.5% of data). |
| `RETURN_RECEIVED` | Item physically received at warehouse; financially settled or in final settlement. Appropriate for refund-amount and reason analysis. |
| `RETURN_PROCESSED` | Processing confirmed complete. Treat as equivalent to `RETURN_COMPLETED` for insight purposes. |

**EXCLUDE** (in-flight, failed, or incomplete — not evidence of a resolved return):

| Status | Rationale |
|--------|-----------|
| `RETURN_SHIPPING` | Item in transit; return not confirmed received. Outcome unknown. |
| `RECEIVED_AT_BAR` | Happy Returns bar accepted the item but warehouse has not received it yet. Intermediate state. |
| `RETURN_RCV_IN_PROGRESS` | Receiving in progress at warehouse; not yet finalized. |
| `CREATED` | Return request created but not acted upon. May be abandoned. |
| `REFUND_FAILED` | Technical failure — refund not issued. Cannot treat as completed return. |
| `RETURN_TAX_FAILED` | Tax calculation failed; return financially unresolved. |
| `LABEL_BOOKING_FAILED` | Shipping label never generated; return likely never shipped. |
| `SHOPIFY_RETURN_CREATION_FAILED` | Downstream system sync failed; return may not exist in Shopify. |

> **Pipeline filter:** Apply `WHERE last_return_status IN ('RETURN_COMPLETED', 'RETURN_RECEIVED', 'RETURN_PROCESSED')` before any insight calculation. This captures ~93% of rows and represents only fully resolved returns.

---

## SKU Granularity & ITEM_KEY Recommendation

### Analysis

SKU-to-product-name mapping was analyzed across 50,000 OMS rows:

| Metric | Count |
|--------|-------|
| Unique SKUs in sample | 28,906 |
| SKUs mapping to exactly 1 product name | 28,757 (99.5%) |
| SKUs mapping to multiple product names | 149 (0.5%) |
| Unique product names in sample | ~25,000+ |
| Product names mapping to multiple SKUs | 3,182 |

### Interpretation

- **One product name → many SKUs** is the dominant pattern (e.g., `Aneka` → SKUs `16453664`, `16453686`, `16453688`, `16453690`, `16453692`, …). These SKUs represent different **size and/or color variants** of the same style.
- The 149 SKUs that appear under 2 product names are edge cases — likely data entry inconsistencies (e.g., internal short-name vs. full display name, as seen with `Salt water_wm` / `Saltwater™ Duck Boot`), not true duplication.
- **Conclusion: SKUs are size/color-level (variant-level) identifiers.** A single style maps to multiple SKUs.

### ITEM_KEY Recommendation

**`ITEM_KEY=name`** — use `orderitem_product_name` as the grouping key for style-level aggregations (return rate by style, reason analysis by product).

Use `sku` only for variant-level analysis (e.g., "which specific size/color drives returns?") or for joining to File 2 (final-sale flag), where the join must be on `sku` not name.

> If a canonical style ID exists in ALDO's product catalog (e.g., a style code separate from the variant SKU), that would be the preferred `ITEM_KEY` — but it is not present in these CSVs. `orderitem_product_name` is the best available proxy.

---

## Store Location List (`instore_return_location`)

The following store IDs appear in File 4 (`Instore return location` column). Additional store IDs appear in File 3 (`Return store` column) — the combined union is listed below.

**Total unique store IDs across both files: ~430+**

Store IDs are numeric strings organized in ranges that likely correspond to geographic regions or banner groupings:

| Range | Approximate Count | Notes |
|-------|-------------------|-------|
| `1011`–`1492` | ~80 stores | Likely ALDO US region 1 |
| `2002`–`2342` | ~150 stores | Likely ALDO US region 2 |
| `2507`–`2689` | ~60 stores | Likely ALDO US region 3 |
| `2816`–`2825` | ~5 stores | Possible outlet / specialty |
| `2901`–`2987` | ~50 stores | Likely ALDO CA region |
| `3001`–`3246` | ~80 stores | Likely CIS / multi-brand |
| `3470` | 1 store | Isolated ID |
| `3801`–`3845` | ~25 stores | Likely Sperry stores |
| `4501`–`4592` | ~20 stores | Likely Sperry US (File 3 only) |

### Full Store ID List (union of File 3 and File 4)

```
1011, 1018, 1019, 1020, 1028, 1029, 1034, 1037, 1038, 1039, 1041, 1042, 1048,
1053, 1054, 1055, 1057, 1058, 1059, 1065, 1066, 1067, 1068, 1070, 1071, 1073,
1075, 1076, 1077, 1080, 1081, 1082, 1083, 1088, 1090, 1094, 1105, 1106, 1107,
1109, 1113, 1115, 1116, 1117, 1119, 1121, 1124, 1125, 1127, 1128, 1132, 1133,
1134, 1138, 1141, 1146, 1150, 1152, 1156, 1162, 1165, 1175, 1188, 1304, 1324,
1327, 1348, 1352, 1412, 1413, 1414, 1415, 1416, 1417, 1418, 1419, 1420, 1422,
1471, 1476, 1480, 1485, 1486, 1488, 1492,
2002, 2005, 2006, 2007, 2011, 2012, 2019, 2020, 2022, 2024, 2028, 2029, 2030,
2031, 2032, 2033, 2042, 2043, 2044, 2049, 2050, 2052, 2053, 2055, 2056, 2057,
2059, 2060, 2062, 2064, 2066, 2067, 2070, 2072, 2073, 2075, 2077, 2078, 2080,
2081, 2082, 2085, 2086, 2087, 2088, 2089, 2090, 2091, 2095, 2098, 2099, 2102,
2103, 2104, 2106, 2107, 2111, 2113, 2114, 2116, 2117, 2120, 2121, 2122, 2123,
2125, 2126, 2128, 2132, 2134, 2135, 2138, 2139, 2143, 2144, 2146, 2150, 2154,
2156, 2157, 2161, 2163, 2167, 2168, 2170, 2173, 2175, 2179, 2181, 2184, 2186,
2187, 2193, 2201, 2203, 2211, 2213, 2215, 2216, 2217, 2222, 2224, 2226, 2227,
2230, 2232, 2234, 2238, 2240, 2244, 2245, 2253, 2254, 2256, 2257, 2258, 2259,
2261, 2263, 2264, 2265, 2267, 2268, 2269, 2272, 2277, 2279, 2289, 2293, 2296,
2310, 2342,
2507, 2513, 2519, 2522, 2524, 2527, 2529, 2533, 2540, 2548, 2550, 2555, 2556,
2562, 2570, 2571, 2573, 2578, 2579, 2580, 2581, 2587, 2588, 2596, 2597, 2598,
2601, 2609, 2611, 2613, 2629, 2630, 2632, 2633, 2636, 2639, 2641, 2645, 2648,
2649, 2651, 2656, 2657, 2663, 2672, 2681, 2687, 2688, 2689,
2816, 2822, 2823, 2824, 2825,
2901, 2902, 2903, 2904, 2905, 2907, 2908, 2909, 2910, 2911, 2912, 2913, 2914,
2916, 2919, 2921, 2922, 2924, 2925, 2930, 2940, 2941, 2942, 2951, 2952, 2955,
2956, 2960, 2961, 2962, 2964, 2966, 2967, 2969, 2970, 2973, 2986, 2987,
3001, 3002, 3003, 3005, 3006, 3012, 3015, 3019, 3021, 3022, 3027, 3028, 3030,
3031, 3032, 3033, 3037, 3038, 3040, 3042, 3044, 3045, 3047, 3048, 3049, 3050,
3051, 3052, 3053, 3054, 3055, 3056, 3057, 3058, 3063, 3064, 3065, 3066, 3067,
3069, 3070, 3071, 3081, 3085, 3087, 3089, 3093, 3095, 3096, 3103, 3107, 3110,
3111, 3115, 3122, 3123, 3124, 3125, 3126, 3133, 3135, 3138, 3143, 3154, 3156,
3205, 3212, 3213, 3231, 3232, 3235, 3237, 3239, 3243, 3246, 3470,
3801, 3803, 3804, 3807, 3811, 3812, 3814, 3815, 3816, 3818, 3820, 3821, 3823,
3824, 3825, 3826, 3828, 3829, 3830, 3837, 3842, 3843, 3845,
4501, 4503, 4504, 4505, 4507, 4519, 4523, 4524, 4530, 4531, 4534, 4536, 4540,
4551, 4553, 4554, 4558, 4559, 4562, 4592
```

> **Stores table seed:** This list is the basis for the `stores` lookup table. Each ID should be enriched with store name, city, province/state, country, and banner from ALDO's internal store master data.

---

## Summary of Data Quality Issues

| Issue | File | Severity | Recommended Action |
|-------|------|----------|--------------------|
| `reason` field contains `return.page.reasons.*` API slugs (truncated) | File 4 | Medium | Add reason canonicalization map in ETL |
| `reason` has French variants (`Trop grand`, `J'ai changé d'idée`) | File 4 | Low | Map to English canonical values or add `reason_lang` column |
| Case inconsistencies in `reason` (`Lost In Transit` vs `Lost in transit`) | File 4 | Low | Normalize to title case or a controlled vocabulary |
| 149 SKUs with multiple product name strings (short name vs. display name) | File 4 | Low | Prefer longer/non-underscore name as canonical |
| `exchange_confidence = ASSUMED` in File 3 | File 3 | Medium | Exclude or downweight in high-precision analyses |
| Empty `reason` rows (no reason recorded) | File 4 | Low | Label as `Unknown` in reporting |
| Store IDs in File 3 include range `4501–4592` not present in File 4 | Both | Low | Include in stores table; banner assignment needed |
