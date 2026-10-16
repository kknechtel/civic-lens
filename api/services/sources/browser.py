"""
Browser-backed sources for sites behind bot challenges (Cloudflare Turnstile).

A real Chromium (Playwright) loads the pages and clears the challenge once;
its cookies (cf_clearance) and User-Agent are then copied onto the adapter's
requests session so file downloads go through the ordinary, fast path.

Chromium comes from PLAYWRIGHT_CHROMIUM (executable path) or Playwright's own
install (`playwright install chromium`).
"""
from __future__ import annotations

import os
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import SourceAdapter, logger

BROWSER_UA = os.getenv(
    "BROWSER_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
)


def _chromium_path() -> str | None:
    p = os.getenv("PLAYWRIGHT_CHROMIUM")
    if p:
        return p
    for cand in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",):
        if os.path.exists(cand):
            return cand
    return None


class BrowserPages:
    """Context manager: fetch rendered HTML for URLs, then hand cookies to a requests session."""

    def __init__(self, challenge_wait_s: int = 30):
        self.challenge_wait_s = challenge_wait_s
        self._pw = self._browser = self._ctx = self._page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        kw = {"args": ["--disable-blink-features=AutomationControlled"]}
        if _chromium_path():
            kw["executable_path"] = _chromium_path()
        self._browser = self._pw.chromium.launch(**kw)
        self._ctx = self._browser.new_context(user_agent=BROWSER_UA, locale="en-US", viewport={"width": 1300, "height": 900})
        self._page = self._ctx.new_page()
        return self

    def get(self, url: str) -> str | None:
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            for _ in range(self.challenge_wait_s // 2):
                if "just a moment" not in (self._page.title() or "").lower():
                    break
                self._page.wait_for_timeout(2000)
            else:
                logger.warning("challenge not cleared for %s", url)
                return None
            self._page.wait_for_timeout(1000)
            return self._page.content()
        except Exception as exc:
            logger.warning("browser fetch failed for %s: %s", url, exc)
            return None

    def share_cookies(self, session) -> None:
        session.headers["User-Agent"] = BROWSER_UA
        for c in self._ctx.cookies():
            session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

    def __exit__(self, *exc):
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception:
                pass


class Ecode360Adapter(SourceAdapter):
    """General Code eCode360 public-document library.

    params:
      customer:   eCode360 customer code, e.g. "AT0153"
      categories: {Resolutions: resolution, Minutes: minutes, ...}  (library folder → doc_type)
      min_year:   skip documents whose title/date is older than this
    """
    name = "ecode360"
    DEFAULT_CATEGORIES = {"Resolutions": "resolution", "Minutes": "minutes", "Agendas": "agenda",
                          "Budgets": "budget", "Legislation": "ordinance", "Misc._Documents": None,
                          "Comprehensive_Plans": "planning"}

    def discover(self) -> list[dict]:
        customer = self.params.get("customer")
        if not customer:
            raise ValueError(f"{self.source.id}: ecode360 needs params.customer")
        cats = self.params.get("categories") or self.DEFAULT_CATEGORIES
        if isinstance(cats, list):
            cats = {c: self.DEFAULT_CATEGORIES.get(c) for c in cats}
        min_year = int(self.params["min_year"]) if self.params.get("min_year") else None
        base = "https://ecode360.com"
        found: list[dict] = []
        with BrowserPages() as browser:
            for cat, doc_type in cats.items():
                url = f"{base}/{customer}/documents/{cat}"
                html = browser.get(url)
                if not html:
                    self.basic.errors.append(f"{self.source.id}: could not load {url}")
                    continue
                soup = BeautifulSoup(html, "html.parser")
                n = 0
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/document/" not in href or ".pdf" not in href.lower():
                        continue
                    title = " ".join(a.get_text(" ", strip=True).split())
                    years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)", title)]
                    if min_year and years and max(years) < min_year:
                        continue
                    item = {"url": urljoin(base, href), "title": f"{cat.replace('_', ' ').rstrip('.')} — {title}",
                            "source_page": url}
                    if doc_type:
                        item["doc_type"] = doc_type
                    found.append(item)
                    n += 1
                logger.info("[%s] %s: %d documents", self.source.id, cat, n)
                self._tick(n)
            browser.share_cookies(self.basic.session)
        self._install_refreshing_downloader(base + f"/{customer}/documents/Resolutions")
        return found

    def _install_refreshing_downloader(self, warm_url: str) -> None:
        """Downloads with the shared clearance cookie, adapting to the host:
        - 429: honour Retry-After (else exponential backoff) and slow the pace for good;
        - 403/503: the clearance expired; re-clear once with the browser and retry."""
        import time

        import requests
        from services.scraper.utils import url_to_filename
        session, basic = self.basic.session, self.basic
        state = {"delay": float(self.municipality.crawl.min_delay_seconds)}

        def download(url: str):
            fname = url_to_filename(url)
            recleared = False
            for attempt in range(6):
                try:
                    r = session.get(url, timeout=(10, 90))
                except requests.RequestException as exc:
                    basic.errors.append(f"download failed {url}: {exc}")
                    return None, fname
                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After") or 0) or min(300, 15 * 2 ** attempt)
                    state["delay"] = min(10.0, state["delay"] * 1.5 + 0.5)
                    logger.info("[%s] 429 - waiting %.0fs, pace now %.1fs/request", self.source.id, wait, state["delay"])
                    time.sleep(wait)
                    continue
                if r.status_code in (403, 503) and not recleared:
                    logger.info("[%s] clearance expired - re-clearing", self.source.id)
                    with BrowserPages() as b:
                        if b.get(warm_url):
                            b.share_cookies(session)
                    recleared = True
                    continue
                if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                    logger.warning("[%s] HTTP %s for %s", self.source.id, r.status_code, url)
                    return None, fname
                time.sleep(state["delay"])
                return r.content, fname
            basic.errors.append(f"gave up on {url} after repeated 429s")
            return None, fname

        basic.download_file_to_bytes = download
