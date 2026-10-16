"""robots.txt enforcement in the downloader, OCR resource caps, iCal parsing."""
from unittest.mock import MagicMock

import pytest

from services.scraper.scraper import BasicScraper

LB_ROBOTS = """User-agent: Googlebot
Disallow: /admin/

User-agent: *
Disallow: /
"""
SCHOOL_ROBOTS = """User-agent: *
Disallow: /admin
Crawl-delay: 5
"""


def _scraper(robots_by_origin: dict[str, tuple[int, str]]) -> BasicScraper:
    s = BasicScraper()
    s.respect_robots = True

    def fake_get(url, timeout=None, **kw):
        origin = url.rsplit("/robots.txt", 1)[0]
        code, body = robots_by_origin.get(origin, (404, ""))
        return MagicMock(status_code=code, text=body)

    s.session.get = fake_get
    return s


def test_disallow_all_blocks_and_reports_once():
    s = _scraper({"https://www.longbeachny.gov": (200, LB_ROBOTS)})
    assert not s.robots_allows("https://www.longbeachny.gov/councilmeetings")
    assert not s.robots_allows("https://www.longbeachny.gov/audit")
    assert len(s.errors) == 1 and "opted into robots.txt" in s.errors[0]
    # The gate is applied before any page or file request.
    assert s.fetch_page("https://www.longbeachny.gov/budget") is None
    assert s.download_file_to_bytes("https://www.longbeachny.gov/x.pdf")[0] is None


def test_allowed_paths_and_crawl_delay():
    s = _scraper({"https://www.lbeach.org": (200, SCHOOL_ROBOTS)})
    assert s.robots_allows("https://www.lbeach.org/information-meetings-agendas-2")
    assert not s.robots_allows("https://www.lbeach.org/admin/x")
    assert s.crawl_delay("https://www.lbeach.org/") == 5


@pytest.mark.parametrize("code,allowed", [(404, True), (403, True), (503, False)])
def test_robots_http_status_semantics(code, allowed):
    """RFC 9309: 4xx → no restrictions; 5xx → assume full disallow."""
    s = _scraper({"https://h.example": (code, "")})
    assert s.robots_allows("https://h.example/doc.pdf") is allowed


def test_opt_out_skips_robots():
    s = _scraper({"https://www.longbeachny.gov": (200, LB_ROBOTS)})
    s.respect_robots = False
    assert s.robots_allows("https://www.longbeachny.gov/councilmeetings")


def test_legacy_crawlers_inherit_crawl_policy(monkeypatch):
    """A legacy crawler's own scraper must follow the deployment's respect_robots: false."""
    from municipality import load_deployment
    from services.scraper import crawlers
    from services.sources.registry import build_adapter

    class Fake:
        def __init__(self):
            self.basic = BasicScraper()
            self.basic.respect_robots = True  # whatever the crawler defaults to, the deployment decides
        def find_documents(self):
            return [{"respect": self.basic.respect_robots}]

    monkeypatch.setattr(crawlers, "FakeCrawler", Fake, raising=False)
    muni = load_deployment("atlantic-highlands-nj")  # inherits respect_robots: false from NJ
    src = muni.source("ahnj").model_copy(update={"params": {"class": "FakeCrawler"}})
    assert build_adapter(src, muni).discover() == [{"respect": False}]
    opted_in = muni.model_copy(update={"crawl": muni.crawl.model_copy(update={"respect_robots": True})})
    assert build_adapter(src, opted_in).discover() == [{"respect": True}]


# ── OCR limits ──────────────────────────────────────────────────────────────

def test_page_zoom_caps_giant_pages():
    from services.tesseract_ocr import page_zoom
    letter = page_zoom(612, 792, 200, max_pixels=12_000_000)
    assert letter == pytest.approx(200 / 72)  # under the cap → full DPI
    plan = page_zoom(36 * 72, 48 * 72, 200, max_pixels=12_000_000)  # 36x48in site plan
    w, h = 36 * 72 * plan, 48 * 72 * plan
    assert w * h <= 12_000_000 * 1.0001


def test_oversized_pdf_skips_ocr(monkeypatch):
    import fitz
    from services import ocr_pipeline

    doc = fitz.open()
    doc.new_page()  # blank page → "needs OCR"
    pdf = doc.tobytes()
    monkeypatch.setattr(ocr_pipeline, "MAX_OCR_PDF_BYTES", 10)
    called = []
    monkeypatch.setattr("services.tesseract_ocr.ocr_pages", lambda *a, **k: called.append(1) or {})
    res = ocr_pipeline._extract_sync(pdf, "big.pdf")
    assert res.truncated and not called


# ── iCal ────────────────────────────────────────────────────────────────────

ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
DTSTART;TZID=America/New_York:20991020T180000\r
DTEND;TZID=America/New_York:20991020T193000\r
SUMMARY:Teen Book Club\\, Season 2\r
LOCATION:Long Beach Public Library\r
DESCRIPTION:Bring a friend.\\nSnacks provided.\r
 More text.\r
URL:https://example.org/e/1\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20991021\r
SUMMARY:All-day closure\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_ical_parse_and_unfold():
    from services.event_scrapers.ical import _parse_dt, parse_ics

    evs = parse_ics(ICS)
    assert len(evs) == 2
    assert evs[0]["SUMMARY"] == "Teen Book Club\\, Season 2"
    assert evs[0]["DESCRIPTION"].endswith("More text.")
    d, t = _parse_dt(evs[0]["DTSTART"])
    assert (d.isoformat(), t) == ("2099-10-20", "6:00 PM")
    assert _parse_dt(evs[1]["DTSTART"])[1] is None


def test_ical_fetch_events(monkeypatch):
    from services.event_scrapers import ical

    monkeypatch.setattr(ical.requests, "get", lambda *a, **k: MagicMock(text=ICS, raise_for_status=lambda: None))
    evs = ical.fetch_events("Long Beach Public Library", "Long Beach", "https://x/ics", days_ahead=365 * 100)
    assert [e["title"] for e in evs] == ["Teen Book Club, Season 2", "All-day closure"]
    assert evs[0]["end_time"] == "7:30 PM" and evs[0]["event_type"] == "community"
