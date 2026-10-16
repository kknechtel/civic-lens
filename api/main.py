"""
Civic Lens - Main FastAPI Application
Document library, meetings, and financial analysis for one municipality
(selected by the MUNICIPALITY env var; see municipalities/).
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import APP_NAME, ALLOWED_ORIGINS, DEBUG
from database import init_db, rag_health

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title=APP_NAME, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and register routes
from routes.auth import router as auth_router
from routes.documents import router as documents_router
from routes.projects import router as projects_router
from routes.financial import router as financial_router
from routes.admin import router as admin_router
from routes.scraper import router as scraper_router
from routes.chat import router as chat_router
from routes.search import router as search_router
from routes.processing import router as processing_router
from routes.export import router as export_router
from routes.websearch import router as websearch_router
from routes.reports import router as reports_router
from routes.calendar import router as calendar_router
from routes.ingestion import router as ingestion_router
from routes.presentations import router as presentations_router
from routes.contracts import router as contracts_router
from routes.extraction import router as extraction_router
from routes.meetings import router as meetings_router
from routes.alerts import router as alerts_router
from routes.event_submissions import router as event_submissions_router
from routes.event_ingest import router as event_ingest_router
from routes.municipality import router as municipality_router

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(documents_router, prefix="/api/documents", tags=["documents"])
app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
app.include_router(financial_router, prefix="/api/financial", tags=["financial-analysis"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(scraper_router, prefix="/api/scraper", tags=["scraper"])
app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
app.include_router(search_router, prefix="/api/search", tags=["search"])
app.include_router(processing_router, prefix="/api/processing", tags=["processing"])
app.include_router(export_router, prefix="/api/export", tags=["export"])
app.include_router(websearch_router, prefix="/api/websearch", tags=["websearch"])
app.include_router(reports_router, prefix="/api/reports", tags=["reports"])
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])
app.include_router(ingestion_router, prefix="/api/ingestion", tags=["ingestion"])
app.include_router(presentations_router, prefix="/api/presentations", tags=["presentations"])
app.include_router(contracts_router, prefix="/api/contracts", tags=["contracts"])
app.include_router(extraction_router, prefix="/api/extraction", tags=["extraction"])
app.include_router(meetings_router, prefix="/api/meetings", tags=["meetings"])
app.include_router(alerts_router, prefix="/api/alerts", tags=["alerts"])
app.include_router(event_submissions_router, prefix="/api/event-submissions", tags=["event-submissions"])
app.include_router(event_ingest_router, prefix="/api/event-ingest", tags=["event-ingest"])
app.include_router(municipality_router, prefix="/api/municipality", tags=["municipality"])


@app.on_event("startup")
async def startup():
    init_db()
    # One-shot maintenance pass: clean junk fiscal_year values left behind by the
    # old regex (e.g. "2026-07" extracted from a resolution number). Idempotent;
    # only mutates rows whose value isn't already a clean YYYY or YYYY-YY(YY).
    try:
        from database import SessionLocal
        from routes.documents import filename_year_backfill
        with SessionLocal() as db:
            stats = filename_year_backfill(db)
            if stats["cleaned"] or stats["filled"]:
                logger.info(f"fiscal_year backfill: cleaned={stats['cleaned']} filled={stats['filled']}")
    except Exception as e:
        logger.warning(f"fiscal_year backfill skipped: {e}")
    logger.info(f"{APP_NAME} started")


@app.get("/health")
async def health():
    return {"status": "healthy", "app": APP_NAME}


@app.get("/health/rag")
async def health_rag():
    """RAG pipeline readiness — pgvector, embedding columns, Voyage key, coverage.
    Same shape as the startup banner. Use for monitoring/alerting."""
    return rag_health()
