# Civic Lens

Open-source document intelligence for local government. Point it at a town's
public websites and it collects agendas, minutes, budgets, audits, ordinances,
RFPs and meeting videos, extracts the text (including scanned PDFs), and makes
everything searchable, with an AI assistant that answers questions and cites
the source documents.

One deployment serves one municipality. Everything town-specific lives in a
YAML **profile** under [`municipalities/`](municipalities/). To add a town you
write a profile; you don't fork the code.

| Profile | Status |
|---|---|
| [`atlantic-highlands-nj`](municipalities/atlantic-highlands-nj/municipality.yaml) | The original production deployment ([ahnj.info](https://ahnj.info)). Most sources use the hand-written "legacy" crawlers. |
| [`long-beach-ny`](municipalities/long-beach-ny/municipality.yaml) | New. Its school district, library and State Comptroller sources are verified live. City sources are blocked by robots.txt until the city grants permission (see [the profile README](municipalities/long-beach-ny/README.md)). |

## What it does

- **Collect.** Configurable source adapters crawl municipal CMS index pages
  (`html_index`), XML sitemaps, RSS feeds, YouTube channels and eCode360. Every
  crawl honours robots.txt and identifies itself with an honest User-Agent.
- **Extract.** For PDFs it uses the text layer when there is one and runs
  Tesseract OCR only on pages that need it, under hard memory, CPU and time
  limits. It also reads DOCX, XLSX, legacy Office files and images.
- **Understand** (optional, needs `GEMINI_API_KEY`). One low-cost model call
  per document produces a title, summary, document type, the publishing
  government, the document date and the fiscal year.
- **Search.** Hybrid keyword and vector search over passage chunks (Postgres
  `tsvector` plus pgvector), with query expansion driven by the profile's
  glossary.
- **Ask** (optional, needs `ANTHROPIC_API_KEY`). An agentic chat assistant that
  searches, reads the documents and cites every claim. It knows the town's
  fiscal calendar and its state's accounting and records-law rules.
- **More.** Meeting transcription and summaries, a calendar, email alerts,
  financial statement extraction and analysis, and presentation decks.

Without any API keys the app still collects, extracts, indexes and keyword-searches
the documents.

## Quick start (Docker)

```bash
cp .env.example .env          # set MUNICIPALITY and JWT_SECRET at minimum
docker compose up --build
docker compose exec api python create_admin.py you@example.org
docker compose exec api python -m scripts.scheduled_scrape     # first crawl + processing
open http://localhost:3000
```

## Local development

```bash
# API (Python 3.12, Postgres 16 with pgvector, tesseract-ocr)
cd api
uv venv -p 3.12 && uv pip install -r requirements.txt pytest pyyaml
export MUNICIPALITY=long-beach-ny JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))") \
       DATABASE_URL=postgresql://postgres:postgres@localhost:5432/civiclens STORAGE_BACKEND=local
uvicorn main:app --port 8002 --reload
pytest tests

# Web (Node 20+)
cd web && npm ci && npm run dev    # proxies /api/* to http://localhost:8002
```

## Adding a municipality

See **[docs/ADDING_A_MUNICIPALITY.md](docs/ADDING_A_MUNICIPALITY.md)**. In short:

1. Copy a profile to `municipalities/<town>-<state>/municipality.yaml`.
2. List the entities (town, school district, library, county, state) and the
   sources.
3. Run `python -m scripts.check_profile <slug> --live` until every source
   finds what you expect.
4. Do a sample run with `SCRAPER_MAX_DOCS_PER_SOURCE=10`, then a full run.

If the state isn't covered yet, add `municipalities/_states/<code>.yaml` with
its public-records law, oversight agencies, and accounting notes for the AI.
NJ and NY ship today.

## Responsible collection

Civic Lens only collects documents that are already public, and it follows
the publisher's rules:

- **robots.txt is enforced by default** (RFC 9309), including `Crawl-delay`.
  A source can opt out only with a `permission_note` that records who granted
  permission and when. Without that note the profile fails validation.
- It sends an honest User-Agent (`CivicLens/0.1 (+repo URL)`) so site
  operators can identify the crawler and contact the deployer.
- Crawls are polite: they run one at a time per host with backoff, and a
  circuit breaker stops requests to a host that keeps failing.
- For YouTube it uses the official Data API rather than the robots-disallowed
  RSS feed.

## Operations notes

- **OCR is the heavy step.** Each page is rendered and OCR'd separately and
  capped at `OCR_MAX_PIXELS`. A process-wide semaphore limits concurrent
  Tesseract runs, each page has a timeout, and oversized PDFs fall back to the
  text layer. On a single box, run the scraper and transcription under the
  systemd units in [`infra/systemd/`](infra/systemd/). They set `MemoryMax` and
  `CPUQuota` so a large batch can't take the API down.
- **Storage.** `STORAGE_BACKEND=local` keeps files on disk; `auto` uses S3 when
  credentials and a bucket are available.

## Project layout

```
api/                FastAPI app, scrapers, OCR/extraction, enrichment, search, chat
  municipality.py   profile schema + loader (the one place towns are defined)
  services/sources/ generic source adapters + registry
  scripts/          scheduled scrape, digests, check_profile, backfills
  tests/            pytest suite (no network)
web/                Next.js client; reads the profile from GET /api/municipality
municipalities/     one folder per town + _states/ packs
infra/              systemd units, OCR Lambda
docs/               guides
```

## License

Apache-2.0. See [LICENSE](LICENSE).
