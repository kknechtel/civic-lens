"""Scraper routes - trigger and monitor document scraping from town/school websites."""
import asyncio
import logging
import threading
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from auth import get_current_user

logger = logging.getLogger(__name__)
from municipality import get_municipality

router = APIRouter()


class ScrapeRequest(BaseModel):
    sites: List[str] | None = None  # source ids from the municipality profile; None = all enabled
    project_id: str | None = None
    dry_run: bool = False
    historical: bool = True  # False = recent-only (skips sources marked historical)


class SiteStats(BaseModel):
    status: str  # pending | running | done | error
    documents_found: int
    documents_uploaded: int
    documents_skipped: int
    errors: int
    started_at: str | None = None
    completed_at: str | None = None


class ScrapeStatusResponse(BaseModel):
    running: bool
    current_site: str | None
    documents_found: int
    documents_uploaded: int
    documents_skipped: int
    errors: List[str]
    started_at: str | None
    completed_at: str | None
    per_site: Dict[str, SiteStats] = {}
    sites_planned: List[str] = []
    sites_completed: List[str] = []


def _run_scraper_in_thread(sites, project_id, user_id, dry_run, historical, triggered_by):
    """Run the async scraper inside its own OS thread + event loop.

    The scraper does long-running synchronous I/O (requests.get with up to
    3 × 30s connect timeouts per URL). Running it on the FastAPI worker's
    event loop via BackgroundTasks blocks the loop and produces 504s for
    every other request. A dedicated thread keeps the request handler
    responsive."""
    from services.scraper.runner import run_scraper
    try:
        asyncio.run(run_scraper(
            sites=sites,
            project_id=project_id,
            user_id=user_id,
            dry_run=dry_run,
            historical=historical,
            triggered_by=triggered_by,
        ))
    except Exception:
        logger.exception("Scraper thread crashed")


@router.post("/run")
async def start_scraper(
    req: ScrapeRequest,
    user: User = Depends(get_current_user),
):
    """Start the document scraper in the background."""
    from services.scraper.runner import get_scraper_status

    status = get_scraper_status()
    if status["running"]:
        return {"detail": "Scraper is already running", "status": status}

    threading.Thread(
        target=_run_scraper_in_thread,
        kwargs={
            "sites": req.sites,
            "project_id": req.project_id,
            "user_id": str(user.id),
            "dry_run": req.dry_run,
            "historical": req.historical,
            "triggered_by": f"ui:{user.id}",
        },
        daemon=True,
    ).start()

    return {
        "detail": "Scraper started",
        "sites": req.sites or [s.id for s in get_municipality().sources if s.enabled],
        "mode": "recent_only" if not req.historical else "all",
    }


@router.get("/sources")
def list_sources(user: User = Depends(get_current_user)):
    """Sources from the municipality profile, for the admin Scraper page.
    `site_id` is the label the runner keys per-site progress by."""
    muni = get_municipality()
    return [{
        "key": s.id,
        "site_id": s.label or s.id,
        "name": s.label or s.id,
        "description": s.description,
        "note": s.note or ("" if s.respect_robots else f"robots.txt override: {s.permission_note}"),
        "adapter": s.adapter,
        "entity": s.entity,
        "enabled": s.enabled,
        "historical": s.historical,
    } for s in muni.sources]


@router.get("/status", response_model=ScrapeStatusResponse)
def get_status(user: User = Depends(get_current_user)):
    """Get current scraper status."""
    from services.scraper.runner import get_scraper_status
    return get_scraper_status()


class ScraperRunSummary(BaseModel):
    id: str
    started_at: str
    completed_at: str | None
    sites: List[str]
    mode: str
    triggered_by: str | None
    documents_found: int
    documents_uploaded: int
    documents_skipped: int
    errors_count: int
    new_docs: List[Dict[str, Any]]  # [{filename, source, category, doc_type, url}, …]


@router.get("/runs", response_model=List[ScraperRunSummary])
def list_runs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the last N completed scraper runs, most recent first.
    Each run includes its full new_docs list so the UI can show 'what was
    added' without a second round-trip per row."""
    from models.scraper_run import ScraperRun
    rows = (
        db.query(ScraperRun)
        .order_by(ScraperRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        ScraperRunSummary(
            id=str(r.id),
            started_at=r.started_at.isoformat(),
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            sites=r.sites or [],
            mode=r.mode or "all",
            triggered_by=r.triggered_by,
            documents_found=r.documents_found or 0,
            documents_uploaded=r.documents_uploaded or 0,
            documents_skipped=r.documents_skipped or 0,
            errors_count=len(r.errors or []),
            new_docs=r.new_docs or [],
        )
        for r in rows
    ]
