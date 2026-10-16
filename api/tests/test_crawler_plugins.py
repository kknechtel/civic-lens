"""Jurisdiction crawler plugins: legacy `params.class` resolves in the source's
own jurisdiction's crawlers.py, then its ancestors', then core."""
import pytest

import municipality
from municipality import Source, load_deployment
from services.scraper import crawlers as core_crawlers
from services.sources import registry
from services.sources.registry import build_adapter, resolve_crawler_class

AH = load_deployment("atlantic-highlands-nj")


def _legacy(muni):
    return [s for s in muni.sources if s.adapter == "legacy"]


def test_ah_legacy_source_resolves_to_its_plugin():
    cls = resolve_crawler_class(AH.source("ahnj"))
    assert cls.__name__ == "AHNJCrawler"
    assert cls.__module__ == "civic_plugins.us_nj_monmouth_atlantic_highlands.crawlers"
    assert not hasattr(core_crawlers, "AHNJCrawler")  # town code no longer lives in core


def test_included_neighbour_resolves_in_its_own_plugin():
    src = AH.source("us/nj/monmouth/highlands:highlands_borough")
    assert src.jurisdiction == "us/nj/monmouth/highlands"
    assert resolve_crawler_class(src).__module__ == "civic_plugins.us_nj_monmouth_highlands.crawlers"


@pytest.mark.parametrize("deployment", ["atlantic-highlands-nj", "us/nj/monmouth/highlands"])
def test_every_legacy_source_resolves_and_constructs_offline(deployment):
    muni = load_deployment(deployment)
    sources = _legacy(muni)
    assert sources
    for s in sources:
        adapter = build_adapter(s, muni)
        crawler = adapter.crawler_class()()  # constructors do no network I/O
        assert callable(crawler.find_documents)


def test_class_defined_only_in_core_still_resolves(monkeypatch):
    class CoreOnlyCrawler:
        def find_documents(self):
            return []

    monkeypatch.setattr(core_crawlers, "CoreOnlyCrawler", CoreOnlyCrawler, raising=False)
    src = AH.source("ahnj").model_copy(update={"params": {"class": "CoreOnlyCrawler"}})
    assert resolve_crawler_class(src) is CoreOnlyCrawler


def test_unknown_class_is_a_clear_error():
    src = AH.source("ahnj").model_copy(update={"params": {"class": "NoSuchCrawler"}})
    with pytest.raises(ValueError, match=r"unknown legacy crawler class 'NoSuchCrawler'") as e:
        build_adapter(src, AH).discover()
    assert "jurisdictions/us/nj/monmouth/atlantic-highlands/crawlers.py" in str(e.value)
    assert "services.scraper.crawlers" in str(e.value)
    with pytest.raises(ValueError, match="params.class is empty"):
        resolve_crawler_class(Source(id="z", adapter="legacy", entity="x"))


def test_resolution_order_own_then_ancestor_then_core(tmp_path, monkeypatch):
    (tmp_path / "us" / "zz" / "town").mkdir(parents=True)
    (tmp_path / "us" / "zz" / "crawlers.py").write_text(
        "from services.scraper.crawlers import dedupe_docs\n"
        "class Shared:\n    where = 'state'\n"
        "class Both:\n    where = 'state'\n")
    (tmp_path / "us" / "zz" / "town" / "crawlers.py").write_text("class Both:\n    where = 'town'\n")
    monkeypatch.setattr(municipality, "JURISDICTIONS_DIR", tmp_path)
    monkeypatch.setattr(core_crawlers, "Both", type("Both", (), {"where": "core"}), raising=False)

    def src(cls, j="us/zz/town"):
        return Source(id="t", adapter="legacy", entity="x", params={"class": cls}, jurisdiction=j)

    assert resolve_crawler_class(src("Both")).where == "town"
    assert resolve_crawler_class(src("Shared")).where == "state"
    assert resolve_crawler_class(src("Both", "us/zz")).where == "state"
    assert resolve_crawler_class(src("Both", "us/other")).where == "core"
    # Loaded once and cached.
    assert registry.load_plugin("us/zz") is registry.load_plugin("us/zz")


def test_proxy_hosts_come_from_the_profile(monkeypatch):
    from services.scraper import scraper

    assert "www.ahnj.com" in AH.crawl.proxy.hosts
    monkeypatch.setattr(municipality, "get_municipality", lambda: AH)
    monkeypatch.setenv(AH.crawl.proxy.url_env, "https://proxy.example/")
    monkeypatch.setenv(AH.crawl.proxy.secret_env, "s3cret")
    scraper._proxy_config.cache_clear()
    try:
        url, headers = scraper._proxy_for("https://www.ahnj.com/a.pdf")
        assert url.startswith("https://proxy.example/?url=") and headers == {"X-Proxy-Secret": "s3cret"}
        assert scraper._proxy_for("https://other.example/a.pdf") == ("https://other.example/a.pdf", {})
    finally:
        scraper._proxy_config.cache_clear()
