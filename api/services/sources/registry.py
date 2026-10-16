"""
Adapter registry: maps `Source.adapter` names to implementations and builds
the runner's site table from the active municipality profile.

Adding a platform = one SourceAdapter subclass + one line in ADAPTERS.
"""
from __future__ import annotations

from typing import Callable

from municipality import Municipality, Source, get_municipality

from .base import SourceAdapter
from .feeds import RssAdapter, SitemapAdapter, YouTubeChannelAdapter
from .html_index import HtmlIndexAdapter


class Ecode360Adapter(SourceAdapter):
    """General Code eCode360 document library.

    params:
      customer: eCode360 customer code, e.g. "AT0153"
      categories: ["Agendas", "Minutes", ...]  (default: the common set)

    eCode360 sits behind Cloudflare Turnstile, so this needs the Selenium
    stealth driver (a real Chrome). Without it the adapter logs and returns [].
    """
    name = "ecode360"
    DEFAULT_CATEGORIES = ["Agendas", "Budgets", "Comprehensive_Plans", "Legislation",
                          "Minutes", "Misc._Documents", "Resolutions"]

    def discover(self) -> list[dict]:
        from services.scraper import crawlers

        customer = self.params.get("customer")
        if not customer:
            raise ValueError(f"{self.source.id}: ecode360 needs params.customer")
        crawler = crawlers.ECode360Crawler()
        crawler.CATEGORIES = [f"/{customer}/documents/{c}"
                              for c in self.params.get("categories", self.DEFAULT_CATEGORIES)]
        docs = crawler.find_documents()
        for d in docs:
            d.pop("category", None)  # eCode360 folder name, not our town/school category
        self._tick(len(docs))
        return docs


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
