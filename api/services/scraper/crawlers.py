"""
Generic building blocks for hand-written ("legacy" adapter) crawlers.

Town-specific crawler classes live with their town as jurisdiction plugins:
jurisdictions/<path>/crawlers.py. services.sources.registry.LegacyCrawlerAdapter
resolves a source's `params.class` in the source's own jurisdiction plugin, then
its ancestors' plugins, and finally this module — so a crawler that is generic
enough to serve many towns can be defined here.

A crawler class takes no constructor arguments and exposes
`find_documents() -> list[dict]` (see services/sources/base.py for the item
shape); it may expose `basic` (a BasicScraper), `historical` and
`progress_callback`, which the adapter and runner set.
"""
import logging
import re
from datetime import datetime
from urllib.parse import unquote

logger = logging.getLogger("scraper.crawlers")


def dedupe_docs(docs: list[dict]) -> list[dict]:
    """Drop repeated URLs, keeping the first occurrence."""
    seen = set()
    unique = []
    for doc in docs:
        if doc["url"] not in seen:
            seen.add(doc["url"])
            unique.append(doc)
    return unique


def stealth_driver():
    """Create a Selenium driver with anti-detection flags for Cloudflare sites."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        options = Options()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--window-size=1920,1080')
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        })
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        logger.error(f"Failed to create stealth driver: {e}")
        return None


# ─── Meeting dates ─────────────────────────────────────────────────
# Common filename / link-text date formats on municipal portals:
#   2024.03.05, 2024-03-05  → ymd
#   3.5.2024, 03-05-2024    → mdy
#   "March 5, 2024"         → month_name
#   Council_20240208_130626 → compact (YYYYMMDD with non-digit boundaries)
_DATE_PATTERNS = [
    (re.compile(r"(20\d{2})[.\-_](\d{1,2})[.\-_](\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[.\-_](\d{1,2})[.\-_](20\d{2}|\d{2})"), "mdy"),
    (re.compile(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})",
        re.I,
    ), "month_name"),
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), "compact"),
]
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_meeting_date(text: str) -> str | None:
    """Return YYYY-MM-DD or None."""
    text = unquote(text)
    for pat, kind in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "mdy":
                mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if y < 100:
                    y += 2000
            elif kind == "compact":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:  # month_name
                mo = _MONTHS.get(m.group(1)[:3].lower())
                d, y = int(m.group(2)), int(m.group(3))
                if not mo:
                    continue
            datetime(y, mo, d)  # validate
            return f"{y:04d}-{mo:02d}-{d:02d}"
        except (ValueError, TypeError):
            continue
    return None


YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/watch\?v=|youtube\.com/embed/)([A-Za-z0-9_-]{11})"
)
