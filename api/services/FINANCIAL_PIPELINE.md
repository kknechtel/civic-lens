# Financial Analysis Pipeline — Operator's Guide

End-to-end process for ingesting a NJ municipal or school financial document
through extraction, reconciliation, anomaly detection, and agent drill-downs.

## Flow at a glance

```
  Upload PDF                                   GET /api/financial/diagnostics
     │                                                    ▲
     ▼                                                    │
  Document row (status=uploaded)                  health/troubleshooting
     │
     ▼  POST /api/financial/extract  {document_id, entity_type, statement_type}
     │
  FinancialStatement (status=processing)
     │
     ▼  background: services/financial_extractor_v2.extract_financial_statement_v2
     │   ├── pdf_to_markdown                       (pymupdf4llm)
     │   ├── HEADER_PROMPT                         → entity, FY, summary numbers,
     │   │                                            accounting_basis, fiscal_calendar
     │   ├── segment_by_sections                   (markdown headings → N sections)
     │   ├── SECTION_PROMPT × N (parallel, sem=4)  → line items per section
     │   ├── parse_account_code on each line       (NJ COA: program-function-object)
     │   ├── reconcile_statement                   → balanced | off_<1% | off_>1% | unbalanced
     │   └── detect_anomalies_for_statement        → severity-banded flag list
     │
  FinancialStatement (status=extracted)
     │
     ▼  POST /api/financial/statements/{id}/drill[?sync=true]
     │   OR
     │  POST /api/financial/drill-all?entity_type=school
     │
     ▼  services/financial_agent.run_full_drill (async, 4 drills + synthesis)
     │   Each drill opens its OWN DB session (concurrency-safe).
     │   Each drill returns either {ok output...} OR {error, error_message, error_trace}.
     │   `_meta` block records duration, success_count, models attempted.
     │
  FinancialStatement.drill_results = {revenue, expenditure, debt, fund_balance, synthesis, _meta}
  FinancialStatement.status = "drilled" (only if synthesis succeeded)
```

## Routes

| Method | Path | Use |
|---|---|---|
| `GET` | `/api/financial/diagnostics` | Pipeline health: counts by status, LLM key presence, drills with errors, `next_steps_hint` |
| `GET` | `/api/financial/statements` | List extracted statements |
| `POST` | `/api/financial/extract` | Run multi-pass extractor on a Document |
| `GET` | `/api/financial/statements/{id}` | Headline summary |
| `GET` | `/api/financial/statements/{id}/line-items` | All extracted line items |
| `GET` | `/api/financial/statements/{id}/raw` | Raw extraction blob (header + counts) |
| `GET` | `/api/financial/statements/{id}/anomalies` | Rule-based flag list |
| `GET` | `/api/financial/statements/{id}/drill` | Drill results + reconcile details |
| `POST` | `/api/financial/statements/{id}/drill` | Run drill (background) |
| `POST` | `/api/financial/statements/{id}/drill?sync=true` | Run drill inline, return errors directly |
| `POST` | `/api/financial/drill-all?entity_type=school` | Drill every extracted statement matching filters |

## Onboarding a NEW document — the canonical recipe

1. **Upload PDF** via the UI (`/document-library`) or via `/api/documents/presigned-upload`. Set `category` to `"town"` or `"school"`, set `doc_type` to `"budget"`/`"audit"`/`"financial_statement"`, set `fiscal_year`.

2. **Extract** — go to `/financial-analysis`, click **Extract Statement**, pick the document, set entity type + statement type. The `extract_financial_statement_v2` background task takes 1-3 minutes for a typical budget/audit, longer for a 200-page CAFR.

3. **Verify extraction** — wait for the card to show `extracted` status, then check:
   - Reconcile badge: green check = sums match summary totals; warning = off by >0.5%.
   - Open the statement → **Anomalies** tab → review reconcile details. If any section says `unbalanced`, the section detection or LLM extraction undercounted. Re-extract.
   - Line item count: open `GET /api/financial/statements/{id}/line-items`. A budget should produce 50-300 lines depending on detail level. Less than 20 = something went wrong (likely no markdown headings recognized — the document is a scanned PDF or has unusual structure).

4. **Drill** — click **Run Drill** (background, ~30-90s) or **Sync** (blocking, returns errors inline). Watch the meta strip: `4/4 drills OK` is the goal.

5. **Review drill output** — Synthesis tab is the executive summary. Each tab (Revenue / Expenditure / Debt / Fund Balance) shows the agent's structured analysis. Any errored drill renders an error card with `error`, `error_message`, and stack trace.

6. **Chat** — the chat sidebar can now answer questions about this statement using `get_drill_results`, `get_line_items`, `get_anomalies`, `search_contracts`, `get_vendor_summary` tools. The chat is wired to the new tables.

## Bulk processing — onboarding many documents at once

Two endpoints handle bulk:
- `POST /api/financial/extract-all?entity_type=school` — creates a FinancialStatement
  for every Document of doc_type `budget`/`audit`/`financial_statement` that doesn't
  already have one, then runs v2 extraction in background. Skips RFP / Synopsis /
  Presentation / INTRODUCED noise files automatically.
- `POST /api/financial/drill-all?entity_type=school` — runs drill on every statement
  in `extracted` or `error` state.

Both take `concurrency` (default 2, max 4) and `entity_type` / `fiscal_year` filters.
Both run in background — use `GET /api/financial/diagnostics` to poll status.

### Remote management CLI: `scripts/manage_pipeline.py`

Drives the deployed API from your workstation. No local DB or local API required.

```bash
# One-time env setup
export AH_BASE_URL=https://your-api.example.com
export AH_EMAIL=you@example.com
export AH_PASSWORD=...

# What's in the system?
python scripts/manage_pipeline.py diagnose

# Bulk-extract every school financial doc
python scripts/manage_pipeline.py extract --entity school

# Bulk-drill them
python scripts/manage_pipeline.py drill --entity school

# Or do both end-to-end with progress polling and final diagnostics:
python scripts/manage_pipeline.py run --entity school

# Debug a stuck statement (shows full per-drill errors inline)
python scripts/manage_pipeline.py sync-one --statement-id <uuid>

# Watch the pipeline live while it processes
python scripts/manage_pipeline.py status
```

The `run` command does this in sequence:
1. Initial diagnostics print
2. `POST /extract-all` for the entity
3. Poll `/diagnostics` every 15s until `processing == 0` (or timeout)
4. `POST /drill-all` for the entity
5. Poll until `extracted == 0` (all drilled)
6. Final diagnostics print

CLI authentication: pass `--email`/`--password` (or use env vars), or pass
`--token <bearer>` to skip login. Login uses the same `/api/auth/login` flow as
the web UI.

## Troubleshooting — "drills aren't running"

In order from most-to-least likely:

| Symptom | Cause | Fix |
|---|---|---|
| All drills return `error: llm_call_failed`, message mentions auth | API key not set | `GET /api/financial/diagnostics` → `llm_keys` block. Set `ANTHROPIC_API_KEY` and/or `GEMINI_API_KEY` in env, restart API. |
| Drills error with `error: prompt_format_error` | Brace bug in a prompt template | The module-load `_validate_prompts()` should have caught it on startup. If it's slipping through, check the failing prompt for unescaped `{...}`. Should always be `{{...}}` for literal braces inside JSON examples. |
| Drills error with `error: build_inputs_failed` | DB query or serialization fail | Check `error_trace` in the drill result. Often: a missing column from a stale schema. Restart API to re-run inline migrations. |
| Drills succeed but synthesis fails | Output too long for synthesis budget | Synthesis truncates inputs to 8K each. If still failing, lower per-drill JSON size or raise `max_output` in `run_synthesis_by_id`. |
| Statement status stuck at `processing` | Extractor crashed mid-run | Check `notes` field on the row. Re-trigger via `POST /api/financial/extract` with the same document. |
| Statement says `extracted` but has 0 line items | PDF→markdown produced no content (scanned image) OR no recognized section headings | OCR the PDF first (outside scope of this app), or extend `SECTION_PATTERNS` in `financial_extractor_v2.py` for the document's heading style. |
| Drilled in background but UI still says "Run Drill" | `_meta.synthesis_ok = false` because synthesis errored | Click **Sync** to see the error inline. Common: rate limit on the second LLM provider too. |
| BackgroundTasks never run on AWS Lambda | Lambda kills request after response | Use `?sync=true` (response blocks until done — fits inside Lambda's 30s API Gateway / 15min Lambda window) or queue via SQS. |

## Per-drill error structure

Every drill returns either a successful payload or this error structure:

```json
{
  "error": "llm_call_failed | prompt_format_error | build_inputs_failed | unexpected_exception | statement_not_found",
  "error_message": "human-readable string from the underlying exception",
  "error_trace": "first 2000 chars of the Python stack trace",
  "label": "revenue | expenditure | debt | fund_balance | synthesis",
  "duration_s": 12.4,
  "run_at": "2026-05-04T14:23:00Z"
}
```

The synthesis still runs even if some drills failed — the synthesis LLM gets
the error JSON and can report meta-failure honestly ("we don't have revenue
data for FY2024 because that drill failed").

## Schema upgrade summary (Phase 1 + accounting-review fixes)

`FinancialStatement`:
- `accounting_basis` ∈ {gaap, nj_regulatory} — branches every analysis rule
- `fiscal_calendar` ∈ {calendar_year, school_year, sfy}
- `predecessor_entity` — for HHRSD pre-7/1/2024 from AHSD/HSD/HHRS-HS
- `extraction_pass` — counter, 0 → 5 as passes complete
- `reconcile_status` ∈ {balanced, off_lt_1pct, off_gt_1pct, unbalanced, not_attempted}
- `reconcile_details` — per-section sums vs reported totals
- `anomaly_flags` — list of `{code, severity, message, value?, line_id?}`
- `drill_results` — full drill output incl. `_meta`

`FinancialLineItem`:
- `fund` ∈ {general, capital, capital_projects, debt_service, special_revenue, enterprise, trust}
- `account_code`, `program_code`, `function_code`, `object_code` — NJ COA segments
- `is_total_row` — set true on subtotals/totals to avoid double-counting
- `yoy_change_pct`, `variance_pct` — pre-computed

`Vendor` / `Contract` / `Payment` — Phase 3, ingestion service still TODO.

## Files

| File | Purpose |
|---|---|
| `services/financial_extractor_v2.py` | Multi-pass extractor (5 passes) |
| `services/financial_reconcile.py` | Sums-vs-reported reconciliation |
| `services/financial_anomaly.py` | Rule-based flag detection (basis-branched) |
| `services/financial_agent.py` | Four parallel drill agents + synthesis (basis-branched) |
| `routes/financial.py` | All financial routes incl. drill / drill-all / diagnostics |
| `routes/contracts.py` | Vendor / Contract / Payment routes |
| `web/components/financial/v2/FinancialDashboardV2.tsx` | Dashboard root |
| `web/components/financial/v2/DrillPanel.tsx` | Per-statement drill view + error tab |
| `web/components/financial/v2/StatementCard.tsx` | FY card with reconcile/anomaly badges |
| `web/components/financial/v2/AnomalyBadge.tsx` | Severity-coded badge |

## NJ-specific gotchas (load-bearing)

These ARE encoded in the code; do not undo them:
- Schools (GAAP) flag fund balance ABOVE the 2%/$250K cap; municipalities (NJ regulatory) flag below 5% of expenditures.
- Salary-ratio thresholds for small NJ schools are 75% (info) / 82% (warn) — small districts run higher than the state average.
- For FY26+ municipalities, CMPTRA = $0 because it consolidated into ETR — do not flag as decline.
- HHRSD pre-7/1/2024 figures are predecessor-district data and must not be summed across the three.
- AH borough = calendar year; HHRSD = school year (Jul-Jun). Never compare YoY across these on the same period axis.
- NJ COA fund whitelist is `{11, 12, 13, 20, 30, 40, 60, 63, 70, 80, 90}`. Fund 30 = Capital Projects, Fund 40 = Debt Service. Don't conflate.
