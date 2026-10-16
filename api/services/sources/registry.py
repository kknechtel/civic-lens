"""
Adapter registry: maps `Source.adapter` names to implementations and builds
the runner's site table from the active municipality profile.

Adding a platform = one SourceAdapter subclass + one line in ADAPTERS.
"""
from __future__ import annotations

from typing import Callable

from municipality import Municipality, Source, get_municipality

from services.scraper.config import RESPECT_ROBOTS_TXT

from .base import SourceAdapter
from .feeds import RssAdapter, SitemapAdapter, YouTubeChannelAdapter
from .browser import Ecode360Adapter
from .html_index import HtmlIndexAdapter


class LegacyCrawlerAdapter(SourceAdapter):
    """Runs a hand-written crawler class from services/scraper/crawlers.py.

    Escape hatch for sites no generic adapter handles yet (params.class).
    """
    name = "legacy"

    def discover(self) -> list[dict]:
        from services.scraper import crawlers

        cls_name = self.params.get("class")
        cls = getattr(crawlers, cls_name or "", None)
        if cls is None:
            raise ValueError(f"{self.source.id}: unknown legacy crawler class {cls_name!r}")
        crawler = cls()
        crawler.historical = self.historical
        crawler.progress_callback = self.progress_callback
        if getattr(crawler, "basic", None) is not None:
            # Legacy crawlers build their own scraper; apply the deployment's crawl policy to it too.
            crawler.basic.respect_robots = RESPECT_ROBOTS_TXT or self.municipality.respects_robots(self.source)
            self.basic = crawler.basic  # so the runner harvests its connect errors
        return crawler.find_documents()


ADAPTERS: dict[str, type[SourceAdapter]] = {
    cls.name: cls
    for cls in (HtmlIndexAdapter, YouTubeChannelAdapter, RssAdapter, SitemapAdapter,
                Ecode360Adapter, LegacyCrawlerAdapter)
}


def build_adapter(source: Source, municipality: Municipality | None = None) -> SourceAdapter:
    muni = municipality or get_municipality()
    try:
        cls = ADAPTERS[source.adapter]
    except KeyError:
        raise ValueError(f"source {source.id!r}: unknown adapter {source.adapter!r} "
                         f"(known: {sorted(ADAPTERS)})") from None
    return cls(source, muni)


def site_table(municipality: Municipality | None = None) -> dict[str, tuple[str, Callable[[], SourceAdapter]]]:
    """{source_id: (display label, zero-arg factory)} for enabled sources —
    the shape services.scraper.runner expects."""
    muni = municipality or get_municipality()
    table = {}
    for s in muni.sources:
        if not s.enabled:
            continue
        table[s.id] = (s.label or s.id, (lambda s=s: build_adapter(s, muni)))
    return table
