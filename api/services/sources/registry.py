"""
Adapter registry: maps `Source.adapter` names to implementations and builds
the runner's site table from the active municipality profile.

Adding a platform = one SourceAdapter subclass + one line in ADAPTERS.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Callable

import municipality as _municipality_mod
from municipality import Municipality, Source, get_municipality

from services.scraper.config import RESPECT_ROBOTS_TXT

from .base import SourceAdapter
from .feeds import RssAdapter, SitemapAdapter, YouTubeChannelAdapter
from .browser import Ecode360Adapter
from .html_index import HtmlIndexAdapter


PLUGIN_FILE = "crawlers.py"
CORE_CRAWLERS = "services.scraper.crawlers"
_plugins: dict[Path, ModuleType | None] = {}
_plugins_lock = threading.Lock()  # the runner builds adapters from several threads


def _plugin_module_name(jurisdiction: str) -> str:
    return "civic_plugins." + re.sub(r"\W", "_", jurisdiction.replace("/", "_")) + ".crawlers"


def load_plugin(jurisdiction: str) -> ModuleType | None:
    """The jurisdiction's crawlers.py as a module (cached), or None if it has none."""
    path = (Path(_municipality_mod.JURISDICTIONS_DIR) / jurisdiction / PLUGIN_FILE).resolve()
    with _plugins_lock:
        if path in _plugins:
            return _plugins[path]
        mod = None
        if path.is_file():
            name = _plugin_module_name(jurisdiction)
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop(name, None)
                raise
        _plugins[path] = mod
        return mod


def crawler_search_path(jurisdiction: str) -> list[str]:
    """Jurisdictions whose plugins are searched for a source's class: its own, then ancestors."""
    parts = [p for p in (jurisdiction or "").split("/") if p]
    return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]


def resolve_crawler_class(source: Source) -> type:
    """Find `params.class` in the source's jurisdiction plugin, its ancestors', then core."""
    cls_name = (source.params or {}).get("class")
    searched = []
    if cls_name:
        for j in crawler_search_path(source.jurisdiction):
            mod = load_plugin(j)
            if mod is None:
                continue
            searched.append(f"jurisdictions/{j}/{PLUGIN_FILE}")
            cls = getattr(mod, cls_name, None)
            if isinstance(cls, type):
                return cls
        core = importlib.import_module(CORE_CRAWLERS)
        searched.append(CORE_CRAWLERS)
        cls = getattr(core, cls_name, None)
        if isinstance(cls, type):
            return cls
    raise ValueError(f"source {source.id!r}: unknown legacy crawler class {cls_name!r} "
                     f"(searched: {', '.join(searched) or 'nothing; params.class is empty'})")


class LegacyCrawlerAdapter(SourceAdapter):
    """Runs a hand-written crawler class named by params.class.

    Escape hatch for sites no generic adapter handles yet. The class is looked
    up in jurisdictions/<source.jurisdiction>/crawlers.py, then in each
    ancestor jurisdiction's crawlers.py, then in services/scraper/crawlers.py.
    """
    name = "legacy"

    def crawler_class(self) -> type:
        return resolve_crawler_class(self.source)

    def discover(self) -> list[dict]:
        crawler = self.crawler_class()()
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
