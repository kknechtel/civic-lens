"""Source adapters against recorded HTML/XML (no network)."""
from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup

from conftest import FIXTURES
from municipality import Municipality, Source, load_municipality
from services.sources.feeds import RssAdapter, SitemapAdapter, YouTubeChannelAdapter, parse_date
from services.sources.html_index import HtmlIndexAdapter, clean_title
from services.sources.registry import build_adapter, site_table

MUNI = load_municipality("long-beach-ny")


def _adapter(cls, params, **src):
    source = Source(id=src.pop("id", "t"), adapter=cls.name, entity=src.pop("entity", "lbcsd"), params=params, **src)
    a = cls(source, MUNI)
    a.basic.respect_robots = False
    return a


def _serve(adapter, pages: dict[str, str]):
    adapter.basic.fetch_page = lambda url: BeautifulSoup(pages[url], "html.parser") if url in pages else None


BOARD = "https://www.lbeach.org/information-meetings-agendas-2"


def test_html_index_groups_and_doc_types():
    a = _adapter(HtmlIndexAdapter, {
        "start_urls": [BOARD],
        "allow_domains": ["files.smartsites.parentsquare.com"],
        "link_pattern": r"files\.smartsites\.parentsquare\.com/11147/.+\.pdf",
        "group_selector": "li.ss-accordion-record",
        "group_heading_selector": "h3.ss-accordion-heading",
        "doc_type_rules": [
            {"pattern": "minutes", "doc_type": "minutes"},
            {"pattern": "agenda", "doc_type": "agenda"},
            {"pattern": "notice", "doc_type": "notice"},
        ],
    }, category="school")
    _serve(a, {BOARD: (FIXTURES / "lbeach_board.html").read_text()})
    items = a.find_documents()
    assert len(items) == 6
    by_title = {i["title"]: i for i in items}
    assert "2025-2026 Minutes — June 24, 2026" in by_title
    assert by_title["2025-2026 Minutes — June 24, 2026"]["doc_type"] == "minutes"
    assert by_title["2025-2026 Agendas — June 9, 2026"]["doc_type"] == "agenda"
    assert all(i["entity"] == "lbcsd" and i["category"] == "school" for i in items)
    assert all(i["source_page"] == BOARD for i in items)


@pytest.mark.parametrize("params,expected", [({"min_year": 2020}, 4), ({"max_year": 2019}, 2)])
def test_html_index_year_window_partitions(params, expected):
    a = _adapter(HtmlIndexAdapter, {"start_urls": [BOARD], "allow_domains": ["files.smartsites.parentsquare.com"],
                                     "group_selector": "li.ss-accordion-record", **params})
    _serve(a, {BOARD: (FIXTURES / "lbeach_board.html").read_text()})
    assert len(a.find_documents()) == expected


def test_html_index_follows_only_matching_pages_and_dedupes():
    root = "https://city.example/meetings"
    year = "https://city.example/index.asp?SEC=1&DE=2026"
    pages = {
        root: f'<a href="{year}">2026</a><a href="/elsewhere">x</a><a href="/a.pdf">A</a>',
        year: '<a href="/a.pdf">A again</a><a href="https://cdn.other/b.pdf">offsite</a><a href=" /c.PDF ">C</a>',
    }
    a = _adapter(HtmlIndexAdapter, {"start_urls": [root], "follow_pattern": r"SEC=1&DE="})
    _serve(a, pages)
    urls = sorted(i["url"] for i in a.find_documents())
    assert urls == ["https://city.example/a.pdf", "https://city.example/c.PDF"]


@pytest.mark.parametrize("raw,clean", [
    (".pdf 2026-05-12 Budget 6-Day Notice", "2026-05-12 Budget 6-Day Notice"),
    ("July 13, 2026 Regular Meeting Minutes opens in a new window", "July 13, 2026 Regular Meeting Minutes"),
    ("Adopted Budget [PDF, 2.1 MB]", "Adopted Budget"),
])
def test_clean_title(raw, clean):
    assert clean_title(raw) == clean


def test_generic_link_text_falls_back_to_row_or_filename():
    page = "https://x.example/audits"
    html = ('<table><tr><td>City of Long Beach — Budget Review</td>'
            '<td><a href="/r/long-beach-b24-7-4.pdf">[read complete report – pdf]</a></td></tr></table>'
            '<p><a href="/r/annual_report_2025.pdf">Download</a></p>')
    a = _adapter(HtmlIndexAdapter, {"start_urls": [page]})
    _serve(a, {page: html})
    titles = sorted(i["title"] for i in a.find_documents())
    assert "annual report 2025" in titles
    assert any("Budget Review" in t for t in titles)


@pytest.mark.parametrize("text,iso", [
    ("City Council Meeting 10-07-2025", "2025-10-07"),
    ("Planning Board — Sept 9, 2026", "2026-09-09"),
    ("Board of Ed Oct. 13, 2026 Budget Hearing", "2026-10-13"),
    ("2026_0914 minutes", None),
    ("Zoning 2026-02-30", None),
])
def test_parse_date(text, iso):
    assert parse_date(text) == iso


YT_FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
<entry><yt:videoId>AAA</yt:videoId><title>City Council Meeting 10/07/2025</title><published>2025-10-08T01:00:00+00:00</published></entry>
<entry><yt:videoId>BBB</yt:videoId><title>Beach cleanup recap</title><published>2025-10-01T01:00:00+00:00</published></entry>
</feed>"""


def test_youtube_rss_filters_and_builds_recordings(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    a = _adapter(YouTubeChannelAdapter, {"channel_id": "UCxyz", "title_pattern": "council", "max_age_days": 0},
                 entity="long_beach", doc_type="recording_council")
    a._get = lambda url: MagicMock(content=YT_FEED.encode())
    items = a.find_documents()
    assert len(items) == 1
    rec = items[0]["recording"]
    assert rec == {"platform": "youtube", "meeting_body": "Long Beach", "meeting_date": "2025-10-07", "youtube_id": "AAA"}
    assert items[0]["doc_type"] == "recording_council"


def test_youtube_without_key_reports_robots_block(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    src = Source(id="yt", adapter="youtube_channel", entity="long_beach", params={"channel_id": "UCxyz"})
    a = YouTubeChannelAdapter(src, MUNI)
    a._get = lambda url: None  # robots.txt said no
    assert a.find_documents() == []
    assert any("YOUTUBE_API_KEY" in e for e in a.basic.errors)


def test_youtube_data_api_paginates(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    pages = [
        {"items": [{"snippet": {"title": "Council 1/6/2026", "publishedAt": "2026-01-07T00:00:00Z",
                                "resourceId": {"videoId": "V1"}}}], "nextPageToken": "p2"},
        {"items": [{"snippet": {"title": "Council 1/20/2026", "publishedAt": "2026-01-21T00:00:00Z",
                                "resourceId": {"videoId": "V2"}}}]},
    ]
    a = _adapter(YouTubeChannelAdapter, {"channel_id": "UCabc", "max_age_days": 0}, entity="long_beach")
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return MagicMock(json=lambda: pages[len(calls) - 1], raise_for_status=lambda: None)

    a.basic.session.get = fake_get
    ids = [i["recording"]["youtube_id"] for i in a.find_documents()]
    assert ids == ["V1", "V2"]
    assert calls[0]["playlistId"] == "UUabc" and calls[1]["pageToken"] == "p2"


def test_rss_enclosures_and_sitemap():
    rss = """<rss><channel><item><title>Notice</title><link>https://x.example/post</link>
    <enclosure url="https://x.example/n.pdf"/></item></channel></rss>"""
    a = _adapter(RssAdapter, {"url": "https://x.example/feed"})
    a._get = lambda url: MagicMock(content=rss.encode())
    assert [i["url"] for i in a.find_documents()] == ["https://x.example/n.pdf"]

    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://x.example/s1.xml</loc></sitemap></sitemapindex>"""
    s1 = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://x.example/agenda.pdf</loc></url>
    <url><loc>https://x.example/about</loc></url></urlset>"""
    b = _adapter(SitemapAdapter, {"url": "https://x.example/sitemap.xml", "link_pattern": r"\.pdf$"})
    b._get = lambda url: MagicMock(content=(index if url.endswith("sitemap.xml") else s1).encode())
    assert [i["url"] for i in b.find_documents()] == ["https://x.example/agenda.pdf"]


def test_site_table_matches_enabled_sources():
    table = site_table(MUNI)
    assert set(table) == {s.id for s in MUNI.sources if s.enabled}
    label, factory = table["lbcsd_board"]
    assert label == "Board of Education agendas, minutes & notices"
    assert factory().source.id == "lbcsd_board"


def test_unknown_adapter_is_a_clear_error():
    with pytest.raises(ValueError, match="unknown adapter"):
        build_adapter(Source(id="z", adapter="ftp", entity="long_beach"), MUNI)
