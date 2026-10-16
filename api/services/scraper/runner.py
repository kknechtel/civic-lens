"""
Scraper runner - orchestrates crawling, downloading, and uploading to S3.
Creates Document records in the database for each file found.

Sites are crawled in parallel — each gets its own DB session + downloader and
runs in a worker thread, capped by SCRAPER_MAX_PARALLEL (default 4). Shared
state (status dict, dedup sets, all_uploaded list) is protected by a single
re-entrant lock. Per-doc commits remain so a kill mid-run loses no work.
"""
import asyncio
import logging
import os
import re
import threading
import uuid
import mimetypes
from datetime import datetime
from typing import Optional

from municipality import get_municipality
from services.sources import site_table

from .scraper import BasicScraper
from .utils import categorize_url, url_to_filename, url_to_descriptive_name, detect_doc_type_from_name, detect_fiscal_year

logger = logging.getLogger("scraper")

# Re-entrant so a holder can call a helper that re-acquires it (e.g. logging
# an error from inside a critical section that also needs the lock).
_status_lock = threading.RLock()

# Per-thread isolation is what makes the speedup safe. The cap exists because
# the source sites are small municipal hosts — opening too many parallel
# connections risks 429/connection-reset. 4 is a conservative default; bump
# with SCRAPER_MAX_PARALLEL when you trust the upstreams. Setting it to 1
# reproduces the original sequential behavior (useful for debugging races).
MAX_PARALLEL_SITES = max(1, int(os.environ.get("SCRAPER_MAX_PARALLEL", "4")))

# Global state for tracking scraper progress.
# `per_site` is keyed by site_name and contains a sub-dict of the same metric set
# so the UI can render a per-source progress strip ("ahnj.com: 312 found, 287 uploaded, 25 skipped").
_scraper_status = {
    "running": False,
    "current_site": None,
    "documents_found": 0,
    "documents_uploaded": 0,
    "documents_skipped": 0,
    "errors": [],
    "started_at": None,
    "completed_at": None,
    "per_site": {},
    "sites_planned": [],
    "sites_completed": [],
}


def _empty_site_stats() -> dict:
    return {
        "status": "pending",   # pending | running | done | error
        "documents_found": 0,
        "documents_uploaded": 0,
        "documents_skipped": 0,
        "errors": 0,
        "started_at": None,
        "completed_at": None,
    }


def get_scraper_status() -> dict:
    # Shallow copy + deep-copy per_site so callers can't mutate live state.
    with _status_lock:
        s = dict(_scraper_status)
        s["per_site"] = {k: dict(v) for k, v in _scraper_status.get("per_site", {}).items()}
        s["errors"] = list(_scraper_status.get("errors", []))
        s["sites_planned"] = list(_scraper_status.get("sites_planned", []))
        s["sites_completed"] = list(_scraper_status.get("sites_completed", []))
        return s


def _process_site_blocking(
    site_key: str,
    site_name: str,
    crawler_cls,
    project_id: str,
    fallback_user_id: str,
    s3,
    historical: bool,
    dry_run: bool,
    existing_keys: set,
    existing_filenames: set,
    all_uploaded: list,
    site_stats: dict,
) -> None:
    """Discover + download + insert for one source site, end-to-end.

    Runs in a worker thread (via asyncio.to_thread) with its own DB session
    and BasicScraper instance so threads don't share mutable per-connection
    state. Shared state (`_scraper_status`, `existing_*`, `all_uploaded`) is
    guarded by `_status_lock`. Per-doc commits preserve survivability on kill.
    """
    from database import SessionLocal
    from models.document import Document

    db = SessionLocal()
    downloader = BasicScraper()
    muni = get_municipality()

    with _status_lock:
        site_stats["status"] = "running"
        site_stats["started_at"] = datetime.utcnow().isoformat()
        _scraper_status["current_site"] = site_name  # last starter wins; per_site is the source of truth during parallel runs

    logger.info(f"\n--- Crawling: {site_name} ---")

    try:
        crawler = crawler_cls()
        # Adapters may ship their own downloader (proxy config, session cookies).
        downloader = getattr(crawler, "basic", None) or downloader
        if hasattr(crawler, "historical"):
            crawler.historical = historical

        # Live progress: each crawler calls this after every page. We update
        # the shared counter under the lock so concurrent crawlers don't race
        # on the += operation.
        def _on_page_docs(n: int):
            with _status_lock:
                _scraper_status["documents_found"] += n
                site_stats["documents_found"] += n
        crawler.progress_callback = _on_page_docs

        docs = crawler.find_documents()
        # Sampling for first runs / testing a new profile: newest-first order
        # isn't guaranteed, so this takes the first N the adapter yielded.
        max_docs = int(os.environ.get("SCRAPER_MAX_DOCS_PER_SOURCE", "0") or 0)
        if max_docs and len(docs) > max_docs:
            logger.info(f"  SCRAPER_MAX_DOCS_PER_SOURCE={max_docs}: sampling {max_docs} of {len(docs)}")
            docs = docs[:max_docs]

        # Reconcile against deduped final count (live ticks counted raw).
        with _status_lock:
            final_n = len(docs)
            delta = final_n - site_stats["documents_found"]
            if delta:
                _scraper_status["documents_found"] += delta
                site_stats["documents_found"] = final_n
        logger.info(f"  Found {final_n} documents on {site_name}")

        # Surface connect-level failures (e.g. host firewalled our IP) to the UI.
        basic = getattr(crawler, "basic", None)
        if basic is not None and getattr(basic, "errors", None):
            with _status_lock:
                for msg in basic.errors:
                    if msg not in _scraper_status["errors"]:
                        _scraper_status["errors"].append(msg)
                        site_stats["errors"] += 1
                basic.errors.clear()

        if dry_run:
            logger.info(f"  [DRY RUN] Would download {len(docs)} files")
            with _status_lock:
                site_stats["status"] = "done"
                site_stats["completed_at"] = datetime.utcnow().isoformat()
                _scraper_status["sites_completed"].append(site_name)
            return

        # Download and upload each document
        for doc_info in docs:
            try:
                recording = doc_info.get("recording")  # set by recording crawlers
                entity_key = doc_info.get("entity") or muni.primary_entity.key

                # ── YouTube recordings: no download, metadata-only row ──
                if recording and recording.get("platform") == "youtube":
                    yt_id = recording.get("youtube_id")
                    if not yt_id:
                        continue
                    mdate = recording.get("meeting_date") or "undated"
                    body = recording.get("meeting_body") or site_name
                    descriptive_name = f"{body} - {mdate} - {doc_info.get('title') or yt_id}.youtube"
                    raw_filename = f"youtube_{yt_id}.json"
                    s3_key = f"recordings/youtube/{yt_id}"
                    # Test-and-claim atomically so two threads can't both pass
                    # the dedup check for the same key and double-insert.
                    with _status_lock:
                        if (descriptive_name.lower() in existing_filenames
                                or s3_key in existing_keys):
                            _scraper_status["documents_skipped"] += 1
                            site_stats["documents_skipped"] += 1
                            continue
                        existing_filenames.add(descriptive_name.lower())
                        existing_keys.add(s3_key)

                    doc_record = Document(
                        project_id=project_id,
                        filename=descriptive_name,
                        original_filename=raw_filename,
                        s3_key=s3_key,
                        s3_bucket=s3.bucket,
                        file_size=0,
                        content_type="video/youtube",
                        doc_type=doc_info.get("doc_type") or "recording_meeting",
                        category=doc_info.get("category") or muni.entity_category(entity_key),
                        municipality=entity_key,
                        fiscal_year=detect_fiscal_year(doc_info.get("title", "") + " " + mdate),
                        uploaded_by=fallback_user_id,
                        status="uploaded",
                        metadata_={
                            "source_url": doc_info["url"],
                            "source_page": doc_info.get("source_page"),
                            "source_site": crawler.source_name,
                            "title": doc_info.get("title"),
                            "scraped_at": datetime.utcnow().isoformat(),
                            "recording": recording,
                        },
                    )
                    db.add(doc_record)
                    db.commit()  # per-doc commit so partial work survives a worker restart
                    with _status_lock:
                        _scraper_status["documents_uploaded"] += 1
                        site_stats["documents_uploaded"] += 1
                        all_uploaded.append({
                            "filename": descriptive_name,
                            "source": crawler.source_name,
                            "category": doc_info.get("category") or muni.entity_category(entity_key),
                            "doc_type": doc_record.doc_type,
                            "url": doc_info["url"],
                        })
                    continue

                # Use descriptive name from URL tree
                descriptive_name = url_to_descriptive_name(
                    doc_info["url"],
                    source_page=doc_info.get("source_page", ""),
                    title=doc_info.get("title", ""),
                )
                raw_filename = url_to_filename(doc_info["url"])

                # Pre-download dedup check (we don't claim yet — the file
                # might be a 404 or hit a download error, in which case we
                # shouldn't permanently mark its name as taken). The final
                # claim happens just before the DB insert below.
                with _status_lock:
                    skip = descriptive_name.lower() in existing_filenames
                    if not skip and descriptive_name == raw_filename:
                        skip = raw_filename.lower() in existing_filenames
                    if skip:
                        _scraper_status["documents_skipped"] += 1
                        site_stats["documents_skipped"] += 1
                        continue

                # Download file content
                content, fname = downloader.download_file_to_bytes(doc_info["url"])
                if not content:
                    continue

                # Categorize from URL and descriptive name
                category = categorize_url(doc_info["url"])
                entity_type = muni.entity_category(entity_key)
                # Recording crawlers pre-tag doc_type/category; honor that.
                doc_type = doc_info.get("doc_type") or detect_doc_type_from_name(descriptive_name)
                if doc_info.get("category"):
                    entity_type = doc_info["category"]
                fiscal_year = detect_fiscal_year(descriptive_name)
                # Recordings live under a distinct prefix so the audio
                # bucket stays browsable separately from documents.
                prefix = "recordings" if recording else "scraped"
                s3_key = f"{prefix}/{crawler.source_name}/{category}/{descriptive_name}"

                # Atomic test-and-claim before S3 upload + DB insert. Another
                # thread may have raced us between the pre-download check and
                # here — bail without uploading.
                with _status_lock:
                    if s3_key in existing_keys or descriptive_name.lower() in existing_filenames:
                        _scraper_status["documents_skipped"] += 1
                        site_stats["documents_skipped"] += 1
                        continue
                    existing_filenames.add(descriptive_name.lower())
                    if descriptive_name == raw_filename:
                        existing_filenames.add(raw_filename.lower())
                    existing_keys.add(s3_key)

                # Upload to S3
                content_type = mimetypes.guess_type(raw_filename)[0] or "application/octet-stream"
                s3.upload_file(content, s3_key, content_type)

                # Create database record with rich metadata
                metadata = {
                    "source_url": doc_info["url"],
                    "source_page": doc_info.get("source_page"),
                    "source_site": crawler.source_name,
                    "title": doc_info.get("title"),
                    "scraped_at": datetime.utcnow().isoformat(),
                }
                if recording:
                    metadata["recording"] = recording

                doc_record = Document(
                    project_id=project_id,
                    filename=descriptive_name,
                    original_filename=raw_filename,
                    s3_key=s3_key,
                    s3_bucket=s3.bucket,
                    file_size=len(content),
                    content_type=content_type,
                    doc_type=doc_type,
                    category=entity_type,
                    municipality=entity_key,
                    # Readable title before (or without) LLM enrichment; the
                    # enrichment pass replaces it unless a person locked it.
                    title=(doc_info.get("title") or "").strip()[:200] or None,
                    fiscal_year=fiscal_year,
                    uploaded_by=fallback_user_id,
                    status="uploaded",
                    metadata_=metadata,
                )
                db.add(doc_record)
                db.commit()  # per-doc commit so partial work survives a worker restart
                with _status_lock:
                    _scraper_status["documents_uploaded"] += 1
                    site_stats["documents_uploaded"] += 1
                    all_uploaded.append({
                        "filename": descriptive_name,
                        "source": crawler.source_name,
                        "category": category,
                        "doc_type": doc_type,
                        "url": doc_info["url"],
                    })

            except Exception as e:
                db.rollback()
                err = f"Error processing {doc_info.get('url', '?')}: {e}"
                logger.error(err)
                with _status_lock:
                    _scraper_status["errors"].append(err)
                    site_stats["errors"] += 1

        # Harvest any new connect-failure messages picked up by this site's downloader.
        if getattr(downloader, "errors", None):
            with _status_lock:
                for msg in downloader.errors:
                    if msg not in _scraper_status["errors"]:
                        _scraper_status["errors"].append(msg)
                        site_stats["errors"] += 1
                downloader.errors.clear()

        with _status_lock:
            site_stats["status"] = "done"
            site_stats["completed_at"] = datetime.utcnow().isoformat()
            _scraper_status["sites_completed"].append(site_name)

    except Exception as e:
        err = f"Error crawling {site_name}: {e}"
        logger.error(err, exc_info=True)
        with _status_lock:
            _scraper_status["errors"].append(err)
            site_stats["status"] = "error"
            site_stats["errors"] += 1
            site_stats["completed_at"] = datetime.utcnow().isoformat()
    finally:
        db.close()


def _system_user_id(db) -> str:
    from models.user import User
    admin = db.query(User).filter(User.is_admin.is_(True)).order_by(User.created_at).first()
    if admin:
        return str(admin.id)
    system = db.query(User).filter(User.email == "system@civic-lens.local").first()
    if not system:
        system = User(email="system@civic-lens.local", username="system",
                      full_name="Scraper (system)", is_active=False, is_admin=False)
        db.add(system)
        db.commit()
        db.refresh(system)
    return str(system.id)


async def run_scraper(
    sites: list[str] | None = None,
    project_id: str | None = None,
    user_id: str | None = None,
    dry_run: bool = False,
    historical: bool = True,
    triggered_by: str | None = None,
):
    """
    Main scraper entry point. Crawls sites, downloads files, uploads to S3,
    and creates Document records in the database.

    historical: when False, crawlers skip seed paths flagged as archive
        material (AHNJ 2005-2013, older Planning Board archive trees). The
        UI "Skip historical archives" checkbox passes False.
    triggered_by: arbitrary string written to scraper_runs.triggered_by
        for later auditing — typically "ui:<user_id>", "schedule", or
        "manual" (when invoked from a CLI script).
    """
    from database import SessionLocal
    from models.document import Document, Project
    from models.scraper_run import ScraperRun
    from services.s3_service import S3Service

    global _scraper_status
    _scraper_status = {
        "running": True,
        "current_site": None,
        "documents_found": 0,
        "documents_uploaded": 0,
        "documents_skipped": 0,
        "errors": [],
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": None,
        "per_site": {},
        "sites_planned": [],
        "sites_completed": [],
    }

    db = SessionLocal()
    s3 = S3Service()
    downloader = BasicScraper()

    try:
        # Scraped rows need a real owner (projects.created_by / documents.uploaded_by
        # are FKs to users): the caller, else the first admin, else a disabled
        # system account so a fresh install can scrape before anyone signs up.
        if not user_id:
            user_id = _system_user_id(db)

        # Get or create project
        if project_id:
            project = db.query(Project).filter(Project.id == project_id).first()
        else:
            # Create a default scraper project
            muni = get_municipality()
            project_name = f"{muni.short_name} Scraped Documents"
            project = db.query(Project).filter(Project.name == project_name).first()
            if not project:
                project = Project(
                    name=project_name,
                    description=f"Documents automatically scraped from {muni.short_name} public websites",
                    entity_type="town",
                    created_by=user_id or str(uuid.uuid4()),
                )
                db.add(project)
                db.commit()
                db.refresh(project)

        # Get existing S3 keys to avoid re-uploading
        existing_keys = set()
        existing_docs = db.query(Document.s3_key).filter(Document.project_id == project.id).all()
        for (key,) in existing_docs:
            existing_keys.add(key)

        # Also track existing filenames for dedup
        existing_filenames = set()
        existing_fn_docs = db.query(Document.filename).filter(Document.project_id == project.id).all()
        for (fn,) in existing_fn_docs:
            existing_filenames.add(fn.lower())
            # Rows stored before the ".pdf.pdf" fix must still match the
            # corrected single-extension name.
            collapsed = re.sub(r"(\.[a-z0-9]{2,4})\1$", r"\1", fn.lower())
            existing_filenames.add(collapsed)

        # Sites come from the municipality profile (municipalities/<slug>/municipality.yaml).
        crawlers = site_table()
        muni = get_municipality()
        if sites:
            all_sites = [s for s in sites if s in crawlers]
            unknown = sorted(set(sites) - set(crawlers))
            if unknown:
                _scraper_status["errors"].append(f"Unknown or disabled sources: {unknown}")
        else:
            all_sites = [
                sid for sid in crawlers
                if historical or not muni.source(sid).historical
            ]
        all_uploaded = []

        # Pre-populate the planned site list so the UI can render the full list
        # of sites (and their pending status) before any of them start.
        _scraper_status["sites_planned"] = [
            crawlers[k][0] for k in all_sites if k in crawlers
        ]
        for sk in all_sites:
            if sk in crawlers:
                _scraper_status["per_site"][crawlers[sk][0]] = _empty_site_stats()

        # Run each site's full pipeline in a worker thread. Concurrency is
        # capped by MAX_PARALLEL_SITES — small municipal hosts don't love
        # being hit by 13 parallel crawlers, and we want to leave headroom
        # for the API itself.
        fallback_user_id = user_id or str(project.created_by)
        sem = asyncio.Semaphore(MAX_PARALLEL_SITES)

        async def _run_one(site_key: str) -> None:
            if site_key not in crawlers:
                return
            site_name, CrawlerClass = crawlers[site_key]
            site_stats = _scraper_status["per_site"][site_name]
            async with sem:
                await asyncio.to_thread(
                    _process_site_blocking,
                    site_key, site_name, CrawlerClass,
                    str(project.id), fallback_user_id, s3,
                    historical, dry_run,
                    existing_keys, existing_filenames, all_uploaded,
                    site_stats,
                )

        logger.info(
            f"Starting parallel crawl: {len(all_sites)} sites, cap={MAX_PARALLEL_SITES}"
        )
        # return_exceptions=False so any thread-level surprise propagates to
        # the outer try/except and gets logged with a traceback. Per-site
        # errors are already caught inside _process_site_blocking.
        await asyncio.gather(*(_run_one(sk) for sk in all_sites), return_exceptions=False)

        _scraper_status["current_site"] = None
        _scraper_status["running"] = False
        _scraper_status["completed_at"] = datetime.utcnow().isoformat()

        # Persist the run for the UI history panel + scheduled-job auditing.
        # Wrapped in try so a row-write failure can't poison the response.
        try:
            run = ScraperRun(
                started_at=datetime.fromisoformat(_scraper_status["started_at"]),
                completed_at=datetime.utcnow(),
                sites=list(all_sites),
                mode="recent_only" if not historical else "all",
                triggered_by=triggered_by,
                documents_found=_scraper_status["documents_found"],
                documents_uploaded=_scraper_status["documents_uploaded"],
                documents_skipped=_scraper_status["documents_skipped"],
                errors=list(_scraper_status["errors"]),
                new_docs=list(all_uploaded),
            )
            db.add(run)
            db.commit()
            logger.info(f"  scraper_runs row written: {run.id}")
        except Exception as e:
            logger.error(f"Failed to persist ScraperRun row: {e}", exc_info=True)

        logger.info(f"\nScraper complete: {_scraper_status['documents_uploaded']} uploaded, "
                     f"{_scraper_status['documents_skipped']} skipped, "
                     f"{len(_scraper_status['errors'])} errors")

        return all_uploaded

    except Exception as e:
        logger.error(f"Scraper failed: {e}", exc_info=True)
        _scraper_status["running"] = False
        _scraper_status["errors"].append(str(e))
        raise
    finally:
        db.close()
