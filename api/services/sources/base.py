"""
Source adapter contract.

An adapter turns one `Source` entry from a municipality profile into a list of
discovered items. The scraper runner (services/scraper/runner.py) downloads
and stores them; adapters only discover.

Each discovered item is a dict with:
    url          absolute URL of the file (or a stable synthetic URL for videos)
    title        link text / feed title
    source_page  page the link was found on
    entity       Entity.key the record belongs to
    doc_type     optional; runner falls back to filename heuristics
    category     optional legacy category ("town" | "school")
    recording    optional {"platform": "youtube", "youtube_id": ..., ...}
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from municipality import Municipality, Source
from services.scraper.config import RESPECT_ROBOTS_TXT
from services.scraper.scraper import BasicScraper

logger = logging.getLogger("sources")


class SourceAdapter:
    #: registry key, e.g. "html_index"
    name: str = ""

    def __init__(self, source: Source, municipality: Municipality):
        self.source = source
        self.municipality = municipality
        self.params = dict(source.params or {})
        # Runner contract (shared with the legacy crawler classes).
        self.source_name = source.id
        self.historical = True
        self.progress_callback: Optional[Callable[[int], None]] = None
        self.basic = BasicScraper()
        # Deployment policy (crawl.respect_robots), overridable per source;
        # RESPECT_ROBOTS_TXT=true in the environment turns it on everywhere.
        self.basic.respect_robots = RESPECT_ROBOTS_TXT or municipality.respects_robots(source)

    # ── to override ───────────────────────────────────────────────────
    def discover(self) -> list[dict]:
        raise NotImplementedError

    # ── runner entry point ────────────────────────────────────────────
    def find_documents(self) -> list[dict]:
        items = self.discover()
        out, seen = [], set()
        for it in items:
            url = it.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            it.setdefault("entity", self.source.entity)
            it.setdefault("category", self.source.category or self.municipality.entity_category(self.source.entity))
            if self.source.doc_type:
                it.setdefault("doc_type", self.source.doc_type)
            out.append(it)
        logger.info(f"[{self.source.id}] discovered {len(out)} items")
        return out

    def _get(self, url: str):
        """GET for feed-style adapters, with the same robots.txt gate as page fetches."""
        from services.scraper.config import REQUEST_TIMEOUT
        if not self.basic.robots_allows(url):
            return None
        resp = self.basic.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp

    def _tick(self, n: int) -> None:
        if n and self.progress_callback:
            self.progress_callback(n)

    @staticmethod
    def _compile(pattern: str | None, flags=re.I) -> re.Pattern | None:
        return re.compile(pattern, flags) if pattern else None
