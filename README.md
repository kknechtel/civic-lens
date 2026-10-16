# Civic Lens

Open-source document intelligence and **integrity monitoring** for local
government. Point it at a city, a county or a whole state. It collects the
public record (agendas, minutes, resolutions, budgets, audits, contracts,
RFPs, personnel reports, meeting video), extracts the text including scanned
PDFs, makes everything searchable with an AI assistant that cites sources,
and flags spending patterns that deserve a closer look.

The integrity flags cover:

- bids parked just under the threshold, and split purchases;
- emergency and no-bid concentration;
- single bidders and duplicate awards;
- separation payouts;
- change-order creep;
- officials' surnames on hiring reports;
- pay-to-play matches;
- adverse audit findings.

Every flag cites the page it came from and goes through human review. See
**[docs/INTEGRITY.md](docs/INTEGRITY.md)** for the approach and roadmap.

## Jurisdictions: city, county, state

Everything place-specific lives in YAML under [`jurisdictions/`](jurisdictions/):

```
jurisdictions/us/ny/jurisdiction.yaml                      State: records law (FOIL), accounting notes,
                                                           bid thresholds, source templates (State Comptroller audits…)
jurisdictions/us/ny/nassau/jurisdiction.yaml               County: Legislature agenda center
jurisdictions/us/ny/nassau/long-beach/jurisdiction.yaml    City: entities (city, school district, library), sources
jurisdictions/us/nj/…/monmouth/atlantic-highlands/          The original deployment (ahnj.info)
```

A deployment is rooted at any node (`DEPLOYMENT=us/ny/nassau/long-beach`,
`us/ny/nassau`, or `us/ny`):

- **Ancestors** contribute rules and their own government as an entity.
- **Descendants** contribute their sources.
- **State and county source templates** are stamped out for every town
  beneath them.

Adding a town is a YAML file; adding a state is a YAML file plus its laws.

| Deployment | Status |
|---|---|
| [`us/ny/nassau/long-beach`](jurisdictions/us/ny/nassau/long-beach/) | Test deployment. Indexes city council, finance, land-use and contract records, the school district, the library, and State Comptroller audits. Integrity rules run on the extracted spending actions. |
| [`us/nj/monmouth/atlantic-highlands`](jurisdictions/us/nj/monmouth/atlantic-highlands/) | The original production app ([ahnj.info](https://ahnj.info)); includes neighbouring Highlands. |
| `us/ny/nassau`, `us/ny`, `us/nj/monmouth`, `us/nj` | County- and state-wide roots over the towns configured so far. |

## What it does

- **Collect.** Configurable adapters handle municipal CMS index pages
  (`html_index`, with accordion and year-page support), sitemaps, RSS,
  YouTube (Data API), eCode360, iCal, and hand-written crawlers for odd
  sites. The crawl policy is set per deployment; the crawler always
  identifies itself honestly and rate-limits itself.
- **Extract.** For PDFs it uses the text layer, and Tesseract OCR only on
  pages that need it, under hard memory, CPU and time limits. It also reads
  DOCX, XLSX, legacy Office files and images.
- **Integrity.** It extracts spending actions (vendor, amount, procurement
  method, bids received, account code), personnel actions and roll calls,
  imports campaign finance, runs 11 rules with state-specific thresholds,
  and supports review.
- **Understand** (optional, needs `GEMINI_API_KEY`). One low-cost model call
  per document produces a title, summary, type, publishing government, date
  and fiscal year.
- **Search and ask.** Hybrid keyword and vector search. The chat assistant
  (`ANTHROPIC_API_KEY`) cites documents and can query red flags and the
  spending ledger.
- **More.** Meeting transcription and summaries, a calendar, email alerts,
  financial statement analysis, and presentation decks.

Without API keys it still collects, extracts, indexes, keyword-searches and
runs every integrity rule.

## Quick start (Docker)

```bash
cp .env.example .env          # set DEPLOYMENT and JWT_SECRET at minimum
docker compose up --build
docker compose exec api python create_admin.py you@example.org
docker compose exec api python -m scripts.scheduled_scrape     # crawl → process → integrity
open http://localhost:3000    # Integrity tab lists the flags
```

## Local development

```bash
cd api
uv venv -p 3.12 && uv pip install -r requirements.txt pytest pyyaml
export DEPLOYMENT=us/ny/nassau/long-beach JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))") \
       DATABASE_URL=postgresql://postgres:postgres@localhost:5432/civiclens STORAGE_BACKEND=local
uvicorn main:app --port 8002 --reload
pytest tests                                         # no network needed
python -m scripts.check_profile --all                # validate every jurisdiction
python -m scripts.check_profile long-beach-ny --live # dry-run each source
python -m scripts.run_integrity --report             # flags, worst first

cd ../web && npm ci && npm run dev                   # proxies /api/* to :8002
```

## Adding a town, county or state

See **[docs/ADDING_A_MUNICIPALITY.md](docs/ADDING_A_MUNICIPALITY.md)**.

## Crawling

Civic Lens collects public records. Whether to obey robots.txt is a
deployment decision (`crawl.respect_robots`), which can be overridden per
source. The NJ and NY state files in this repo set it to `false`, so those
deployments ignore robots.txt. Either way the crawler sends an honest
User-Agent (`CivicLens/0.1 (+repo URL)`), waits between requests, retries
with backoff, and trips a circuit breaker on failing hosts. See the
guardrails section of [docs/INTEGRITY.md](docs/INTEGRITY.md).

## Operations

- **OCR** renders and OCRs one page at a time. Pages are capped at
  `OCR_MAX_PIXELS`, a process-wide semaphore limits concurrent Tesseract
  runs, each page has a timeout, and oversized PDFs fall back to the text
  layer. Run the batch jobs under the systemd units in
  [`infra/systemd/`](infra/systemd/), which set `MemoryMax` and `CPUQuota`.
- **Storage:** `STORAGE_BACKEND=local` keeps files on disk; `auto` uses S3
  when it's available.

## Layout

```
api/                FastAPI app, scrapers, OCR/extraction, enrichment, search, chat
  municipality.py   jurisdiction tree loader → deployment view
  services/sources/ generic source adapters
  services/integrity/ fact extraction + red-flag rules + runner
  scripts/          scheduled_scrape, run_integrity, check_profile, digests, backfills
  tests/            pytest (fixtures are real public records)
web/                Next.js client (reads the deployment from GET /api/municipality)
jurisdictions/      us/<state>/<county>/<place>/jurisdiction.yaml
docs/               guides
infra/              systemd units, OCR Lambda
```

## License

Apache-2.0. See [LICENSE](LICENSE).
