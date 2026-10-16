"""
Site-specific crawlers adapted for S3 upload.
Each crawler finds documents and uploads them to S3 via the provided callback.
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup

from .scraper import BasicScraper, SeleniumScraper
from .utils import categorize_url
from .config import SOURCES, RECORDING_SOURCES, REQUEST_DELAY

logger = logging.getLogger("ah_scraper")


def _deduplicate_docs(docs: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for doc in docs:
        if doc["url"] not in seen:
            seen.add(doc["url"])
            unique.append(doc)
    return unique


def _get_stealth_driver():
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


class AHNJCrawler:
    """Crawler for ahnj.com (Borough of Atlantic Highlands)."""

    def __init__(self):
        self.source_name = "ahnj"
        self.config = SOURCES["ahnj"]
        self.basic = BasicScraper()
        self.selenium = None
        # When False, skip historical_pages (2005-2013 archive years).
        # Runner toggles this from the recent_only request flag.
        self.historical = True

    def _get_scraper(self):
        if self.selenium is None:
            try:
                self.selenium = SeleniumScraper()
                self.selenium._ensure_driver()
            except Exception:
                logger.warning("Selenium not available for ahnj.com — using basic scraper")
                self.selenium = False
        return self.selenium if self.selenium else self.basic

    def find_documents(self) -> list[dict]:
        all_docs = []
        scraper = self._get_scraper()
        visited = set()
        # Two-level deep crawl from each seed: many AH archive trees nest
        # /Archive/<Year>/<Month-Day>/<file>.pdf, so depth=1 misses most PDFs.
        max_pages = 250
        cb = getattr(self, "progress_callback", None)

        def _crawl(url: str, depth: int):
            if url in visited or len(visited) >= max_pages:
                return
            visited.add(url)
            soup = scraper.fetch_page(url)
            if not soup:
                return
            docs = self.basic.find_document_links(soup, url)
            if docs:
                all_docs.extend(docs)
                logger.info(f"  Found {len(docs)} documents on {url}")
                if cb:
                    cb(len(docs))
            if depth <= 0:
                return
            subpages = self.basic.find_subpage_links(soup, url, same_domain=True)
            # Cap fanout per page so we don't explode on side-nav menus
            for subpage in subpages[:30]:
                if len(visited) >= max_pages:
                    break
                _crawl(subpage, depth - 1)

        seed_paths = list(self.config["pages_to_crawl"])
        if self.historical:
            seed_paths += list(self.config.get("historical_pages", []))
        for page_path in seed_paths:
            url = self.config["base_url"] + page_path
            _crawl(url, depth=2)

        self._cleanup()
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        if self.selenium and self.selenium is not False:
            self.selenium.close()


class ECode360Crawler:
    """
    Crawler for ecode360.com (Atlantic Highlands document archive).
    Requires visible Chrome to bypass Cloudflare Turnstile.
    """

    def __init__(self):
        self.source_name = "ecode360"
        self.config = SOURCES["ecode360"]
        self.base_url = "https://ecode360.com"
        self.driver = None

    # Document categories on ecode360
    CATEGORIES = [
        "/AT0153/documents/Agendas",
        "/AT0153/documents/Budgets",
        "/AT0153/documents/Comprehensive_Plans",
        "/AT0153/documents/Legislation",
        "/AT0153/documents/Minutes",
        "/AT0153/documents/Misc._Documents",
        "/AT0153/documents/Resolutions",
    ]

    def find_documents(self) -> list[dict]:
        all_docs = []
        self.driver = _get_stealth_driver()
        if not self.driver:
            logger.error("Cannot crawl ecode360 without Chrome - Cloudflare protection")
            return []

        try:
            for cat_path in self.CATEGORIES:
                url = self.base_url + cat_path
                cat_name = cat_path.split("/")[-1].replace("_", " ")
                logger.info(f"  Crawling ecode360: {cat_name}")

                self.driver.get(url)
                time.sleep(8)

                title = self.driver.title
                if "moment" in title.lower():
                    logger.warning(f"  Cloudflare blocking {cat_name}, waiting...")
                    time.sleep(15)

                soup = BeautifulSoup(self.driver.page_source, "html.parser")
                links = soup.find_all("a", href=True)

                for link in links:
                    href = link["href"]
                    if ".pdf" in href.lower():
                        full_url = urljoin(self.base_url, href)
                        link_text = link.get_text(strip=True) or ""
                        all_docs.append({
                            "url": full_url,
                            "title": link_text,
                            "source_page": url,
                            "category": cat_name,
                        })

                found = len([l for l in links if ".pdf" in l["href"].lower()])
                if found:
                    logger.info(f"  Found {found} PDFs in {cat_name}")

                # Check for subcategory links
                for link in links:
                    href = link["href"]
                    if "category=" in href or "subCategory=" in href:
                        sub_url = urljoin(self.base_url, href)
                        sub_name = link.get_text(strip=True)
                        logger.info(f"  Following subcategory: {sub_name}")
                        self.driver.get(sub_url)
                        time.sleep(5)
                        sub_soup = BeautifulSoup(self.driver.page_source, "html.parser")
                        for sub_link in sub_soup.find_all("a", href=True):
                            if ".pdf" in sub_link["href"].lower():
                                full_url = urljoin(self.base_url, sub_link["href"])
                                all_docs.append({
                                    "url": full_url,
                                    "title": sub_link.get_text(strip=True) or "",
                                    "source_page": sub_url,
                                    "category": cat_name,
                                })

        except Exception as e:
            logger.error(f"ECode360 crawl error: {e}", exc_info=True)
        finally:
            self._cleanup()

        result = _deduplicate_docs(all_docs)
        logger.info(f"  ECode360 total: {len(result)} unique documents")
        return result

    def _cleanup(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


class OPRACrawler:
    """Crawler for OPRAmachine.com - crowdsourced OPRA requests for Atlantic Highlands.
    Requires Selenium due to Cloudflare protection."""

    def __init__(self):
        self.source_name = "opra"
        self.config = SOURCES.get("opra", {})
        self.driver = None

    def find_documents(self) -> list[dict]:
        all_docs = []
        self.driver = _get_stealth_driver()
        if not self.driver:
            logger.warning("Cannot crawl OPRAmachine without Chrome — Cloudflare protection")
            return []

        try:
            # Try multiple possible slugs
            for slug in ["atlantic_highlands", "borough_of_atlantic_highlands"]:
                url = f"https://opramachine.com/body/{slug}"
                logger.info(f"  Trying OPRAmachine: {url}")
                self.driver.get(url)
                time.sleep(8)

                title = self.driver.title
                if "Attention Required" in title or "moment" in title.lower():
                    logger.warning(f"  Cloudflare blocking, waiting longer...")
                    time.sleep(15)

                if "Not Found" in self.driver.page_source or "404" in self.driver.title:
                    continue

                soup = BeautifulSoup(self.driver.page_source, "html.parser")

                # Find OPRA request links
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if "/request/" in href:
                        full_url = urljoin("https://opramachine.com", href)
                        link_text = link.get_text(strip=True) or "OPRA Request"
                        all_docs.append({
                            "url": full_url,
                            "title": f"OPRA: {link_text}",
                            "source_page": url,
                            "category": "opra",
                        })

                # Also grab any PDF attachments
                for link in soup.find_all("a", href=True):
                    if ".pdf" in link["href"].lower():
                        full_url = urljoin("https://opramachine.com", link["href"])
                        all_docs.append({
                            "url": full_url,
                            "title": link.get_text(strip=True) or "OPRA Attachment",
                            "source_page": url,
                            "category": "opra",
                        })

                if all_docs:
                    logger.info(f"  OPRAmachine: found {len(all_docs)} items from {slug}")
                    break

        except Exception as e:
            logger.error(f"OPRAmachine crawl error: {e}", exc_info=True)
        finally:
            self._cleanup()

        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


class PoliceCrawler:
    """Crawler for police/crime data sources."""

    def __init__(self):
        self.source_name = "police"
        self.config = SOURCES.get("police", {})
        self.basic = BasicScraper()

    def find_documents(self) -> list[dict]:
        all_docs = []
        for url in self.config.get("pages_to_crawl", []):
            try:
                soup = self.basic.fetch_page(url)
                if soup:
                    docs = self.basic.find_document_links(soup, url)
                    all_docs.extend(docs)
                    if docs:
                        logger.info(f"  Police: found {len(docs)} documents on {url}")
            except Exception as e:
                logger.warning(f"Police scrape failed for {url}: {e}")
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        pass


class FireCrawler:
    """Crawler for fire/EMS data sources."""

    def __init__(self):
        self.source_name = "fire"
        self.config = SOURCES.get("fire", {})
        self.basic = BasicScraper()

    def find_documents(self) -> list[dict]:
        all_docs = []
        for url in self.config.get("pages_to_crawl", []):
            try:
                soup = self.basic.fetch_page(url)
                if soup:
                    docs = self.basic.find_document_links(soup, url)
                    all_docs.extend(docs)
                    if docs:
                        logger.info(f"  Fire/EMS: found {len(docs)} documents on {url}")
            except Exception as e:
                logger.warning(f"Fire scrape failed for {url}: {e}")
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        pass


class HighlandsBoroughCrawler:
    """Crawler for highlandsnj.gov (HHRSD constituent town).
    The borough WordPress site has a small number of direct PDFs plus a
    Municode meetings portal that's handled separately."""

    def __init__(self):
        self.source_name = "highlands_borough"
        self.config = SOURCES.get("highlands_borough", {})
        self.basic = BasicScraper()

    def find_documents(self) -> list[dict]:
        all_docs = []
        # Direct downloads — known stable URLs (court letters, regionalization decisions)
        for url in self.config.get("direct_downloads", []):
            all_docs.append({
                "url": url,
                "title": url.split("/")[-1],
                "source_page": url,
                "category": "highlands_borough",
            })
        for path in self.config.get("pages_to_crawl", []):
            url = self.config["base_url"] + path if path.startswith("/") else path
            try:
                soup = self.basic.fetch_page(url)
                if soup:
                    docs = self.basic.find_document_links(soup, url)
                    all_docs.extend(docs)
                    if docs:
                        logger.info(f"  Highlands Borough: found {len(docs)} documents on {url}")
            except Exception as e:
                logger.warning(f"Highlands Borough scrape failed for {url}: {e}")
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        pass


class HighlandsMeetingsCrawler:
    """Crawler for highlands-nj.municodemeetings.com (Highlands Borough Council
    meeting agendas + packets, hosted on Microsoft Azure blob storage).

    The portal is a Drupal site with an infinite-scroll meetings table.
    Hitting `/` returns only the first ~15-20 meetings (~50 PDFs); the next
    pages live at `/?page=1`, `/?page=2`, … up to ~17. We paginate until a
    page yields no new PDFs."""

    MAX_PAGES = 30  # cap; real depth is ~18

    def __init__(self):
        self.source_name = "highlands_meetings"
        self.config = SOURCES.get("highlands_meetings", {})
        self.basic = BasicScraper()
        self.progress_callback = None

    def find_documents(self) -> list[dict]:
        all_docs = []
        base = self.config["base_url"].rstrip("/")
        for page in range(self.MAX_PAGES):
            url = f"{base}/?page={page}" if page else f"{base}/"
            try:
                soup = self.basic.fetch_page(url)
                if not soup:
                    break
                docs = self.basic.find_document_links(soup, url)
                # Filter out ecode360-hosted PDFs: they're Cloudflare-Turnstile
                # protected and 403 on every retry from our IP. Each download
                # attempt costs ~5s (3 retries × delay), and a typical run
                # finds ~170 of them — that's ~15 min of guaranteed-wasted
                # time per run. ecode360 content is crawled separately by the
                # local scrape_ecode_local.py script.
                docs = [d for d in docs if "ecode360.com" not in urlparse(d["url"]).netloc.lower()]
                if not docs:
                    # Empty page = past the end of the infinite-scroll list.
                    logger.info(f"  Highlands Meetings portal: no docs at page={page}, stopping")
                    break
                all_docs.extend(docs)
                logger.info(f"  Highlands Meetings portal: page={page} → {len(docs)} documents")
                if self.progress_callback:
                    self.progress_callback(len(docs))
            except Exception as e:
                logger.warning(f"Highlands Meetings scrape failed for {url}: {e}")
                break
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        pass


# ─── Meeting-recording crawlers ────────────────────────────────────
# Output records look like normal docs but always include `recording`
# metadata that runner.py reads to populate Document.metadata_:
#   { "url", "title", "source_page", "doc_type",
#     "recording": {
#       "platform":     "audio" | "youtube",
#       "meeting_body": "Borough Council" | "Planning Board" | ...,
#       "meeting_date": "YYYY-MM-DD",    # parsed from filename or page label
#       "youtube_id":   "..."            # YouTube only
#     }
#   }

# Year regex used by AH recording-folder discovery. Year-only links live next
# to month/day named subfolders we don't want to recurse into.
_YEAR_FOLDER_RE = re.compile(r"/(20\d{2})(?:%20Recordings)?/?$", re.I)

# Match a year segment anywhere in a URL path: /2024/, /2024%20Recordings/,
# / 2024 Recordings/. Used as fallback when the filename has no year — many
# Harbor files are like "Harbor Commission 04.07.wma" inside /2020 Recordings/.
_URL_YEAR_RE = re.compile(r"/(20\d{2})(?:%20Recordings|\s+Recordings)?/", re.I)


def _year_from_url(url: str) -> int | None:
    m = _URL_YEAR_RE.search(unquote(url))
    return int(m.group(1)) if m else None

# Parse common AH filename date formats:
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


def _parse_meeting_date(text: str) -> str | None:
    """Return YYYY-MM-DD or None. Try several formats found across AH portals."""
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


class AHRecordingsCrawler:
    """Crawler for Atlantic Highlands meeting recordings (audio files on ahnj.com).

    Three bodies — Council (flat), Planning Board (year subfolders),
    Harbor (year subfolders). Filters to last 12 months by parsed filename
    date when present, else keeps the file if its source page is the
    current-year folder."""

    def __init__(self):
        self.source_name = "ah_recordings"
        self.config = RECORDING_SOURCES["ah_recordings"]
        self.basic = BasicScraper()
        self.progress_callback = None
        # 12 months back from today. We compare to the parsed file date.
        self.cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date()

    def find_documents(self) -> list[dict]:
        all_docs: list[dict] = []
        for body in self.config["bodies"]:
            base = self.config["base_url"] + body["path"]
            try:
                soup = self.basic.fetch_page(base)
            except Exception as e:
                logger.warning(f"AH recordings ({body['name']}) parent fetch failed: {e}")
                continue
            if not soup:
                continue

            pages_to_scan = [base]
            if body.get("year_subfolders"):
                # Two patterns in the wild:
                #   Harbor:  /Harbor Meeting Recordings/<YEAR> Recordings/<file>.wma
                #   Planning: /_Agendas and Minutes/<YEAR>/Recordings/<file>.wma
                # Walk year folders, then if the year folder has no media itself
                # look for a "Recordings" subfolder inside it.
                subs = self.basic.find_subpage_links(soup, base, same_domain=True)
                for s in subs:
                    m = _YEAR_FOLDER_RE.search(s)
                    if not m:
                        continue
                    year = int(m.group(1))
                    if year < self.cutoff.year - 1:
                        continue
                    pages_to_scan.append(s)
                    # Speculatively also add the per-year Recordings subfolder.
                    if not s.rstrip("/").lower().endswith("recordings"):
                        pages_to_scan.append(s.rstrip("/") + "/Recordings/")

            body_docs: list[dict] = []
            for page_url in pages_to_scan:
                try:
                    page_soup = self.basic.fetch_page(page_url) if page_url != base else soup
                    if not page_soup:
                        continue
                    media = self.basic.find_media_links(page_soup, page_url)
                except Exception as e:
                    logger.warning(f"AH recordings page failed {page_url}: {e}")
                    continue

                for item in media:
                    title_with_url = item["title"] + " " + item["url"]
                    meeting_date = _parse_meeting_date(title_with_url)
                    if meeting_date:
                        d = datetime.strptime(meeting_date, "%Y-%m-%d").date()
                        if d < self.cutoff:
                            continue
                    else:
                        # Filename has no year (Harbor's "M.D" naming inside a
                        # /YYYY Recordings/ folder). Fall back to the URL path.
                        url_year = _year_from_url(item["url"])
                        if url_year is not None and url_year < self.cutoff.year - 1:
                            continue
                        if url_year is None:
                            # No year anywhere — refuse to ingest rather than
                            # silently keep undated audio.
                            continue
                    body_docs.append({
                        **item,
                        "doc_type": body["doc_type"],
                        "category": "town",
                        "recording": {
                            "platform": "audio",
                            "meeting_body": body["name"],
                            "meeting_date": meeting_date,
                        },
                    })

            if body_docs:
                logger.info(f"  AH recordings — {body['name']}: {len(body_docs)} files")
                if self.progress_callback:
                    self.progress_callback(len(body_docs))
                all_docs.extend(body_docs)

        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        pass


_YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/watch\?v=|youtube\.com/embed/)([A-Za-z0-9_-]{11})"
)


class HHRSDRecordingsCrawler:
    """Crawler for the HHRSD ('tri-district') Board Meeting Recordings page.

    The page is a flat list of <a href="https://youtu.be/<id>">Month Day, Year
    Board of Education Meeting</a> entries. We extract the YouTube video ID
    and the meeting date from the anchor text; no download is performed —
    transcripts come from YouTube's auto-captions and the player is embedded
    in the UI."""

    def __init__(self):
        self.source_name = "hhrsd_recordings"
        self.config = RECORDING_SOURCES["hhrsd_recordings"]
        self.basic = BasicScraper()
        self.progress_callback = None
        self.cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date()

    def find_documents(self) -> list[dict]:
        all_docs: list[dict] = []
        seen_ids: set[str] = set()
        for path in self.config["pages_to_crawl"]:
            url = self.config["base_url"] + path
            try:
                soup = self.basic.fetch_page(url)
            except Exception as e:
                logger.warning(f"HHRSD recordings page failed: {e}")
                continue
            if not soup:
                continue

            for link in soup.find_all("a", href=True):
                m = _YOUTUBE_ID_RE.search(link["href"])
                if not m:
                    continue
                yt_id = m.group(1)
                if yt_id in seen_ids:
                    continue
                seen_ids.add(yt_id)

                anchor_text = link.get_text(strip=True) or f"HHRSD BOE Meeting {yt_id}"
                meeting_date = _parse_meeting_date(anchor_text)
                if meeting_date:
                    d = datetime.strptime(meeting_date, "%Y-%m-%d").date()
                    if d < self.cutoff:
                        continue

                # Use a stable synthetic URL so dedup against existing rows works.
                synthetic_url = f"https://www.youtube.com/watch?v={yt_id}"
                all_docs.append({
                    "url": synthetic_url,
                    "title": anchor_text,
                    "source_page": url,
                    "doc_type": self.config["doc_type"],
                    "category": "school",
                    "recording": {
                        "platform": "youtube",
                        "meeting_body": "HHRSD Board of Education",
                        "meeting_date": meeting_date,
                        "youtube_id": yt_id,
                    },
                })

        if all_docs:
            logger.info(f"  HHRSD recordings: {len(all_docs)} YouTube videos")
            if self.progress_callback:
                self.progress_callback(len(all_docs))
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        pass


class CountyCrawler:
    """Crawler for Monmouth County records."""

    def __init__(self):
        self.source_name = "county"
        self.config = SOURCES.get("county", {})
        self.basic = BasicScraper()

    def find_documents(self) -> list[dict]:
        all_docs = []
        for url in self.config.get("pages_to_crawl", []):
            try:
                soup = self.basic.fetch_page(url)
                if soup:
                    docs = self.basic.find_document_links(soup, url)
                    all_docs.extend(docs)
                    # Also follow subpages
                    subpages = self.basic.find_subpage_links(soup, url, same_domain=True)
                    for sub in subpages[:15]:
                        sub_soup = self.basic.fetch_page(sub)
                        if sub_soup:
                            sub_docs = self.basic.find_document_links(sub_soup, sub)
                            all_docs.extend(sub_docs)
                    if docs:
                        logger.info(f"  County: found {len(docs)} documents on {url}")
            except Exception as e:
                logger.warning(f"County scrape failed for {url}: {e}")
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        pass


class CensusCrawler:
    """Fetches Census ACS data via API and saves as a JSON document."""

    def __init__(self):
        self.source_name = "census"
        self.config = SOURCES.get("census", {})

    def find_documents(self) -> list[dict]:
        api_config = self.config.get("api_config", {})
        if not api_config:
            return []

        variables = api_config.get("variables", [])
        state = api_config.get("state_fips", "34")
        place = api_config.get("place_fips", "01960")

        try:
            import requests, json
            var_str = ",".join(["NAME"] + variables)
            url = f"https://api.census.gov/data/2022/acs/acs5?get={var_str}&for=place:{place}&in=state:{state}"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                # Save as a synthetic document
                from pathlib import Path
                out_dir = Path(__file__).parent.parent.parent / "storage" / "census"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / "atlantic_highlands_acs_2022.json"
                with open(out_file, "w") as f:
                    json.dump({"source": "Census ACS 5-Year 2022", "raw": data, "variables": variables}, f, indent=2)
                logger.info(f"  Census: saved ACS data to {out_file}")
                return [{
                    "url": str(out_file),
                    "title": "Atlantic Highlands Census ACS 2022 Demographics",
                    "source_page": url,
                    "category": "census",
                }]
        except Exception as e:
            logger.warning(f"Census API failed: {e}")
        return []

    def _cleanup(self):
        pass


class NJStateCrawler:
    """Downloads direct PDF links from NJ state sources, court opinions, and key documents."""

    def __init__(self):
        self.source_name = "nj_state"
        self.config = SOURCES["nj_state"]
        self.basic = BasicScraper()

    def find_documents(self) -> list[dict]:
        all_docs = []
        for url in self.config.get("direct_downloads", []):
            filename = url.split("/")[-1]
            if not filename or "." not in filename:
                filename = url.split("/")[-2] + ".pdf"
            all_docs.append({
                "url": url,
                "title": filename,
                "source_page": url,
                "category": "nj_state",
            })
            logger.info(f"  Queued direct download: {filename}")
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        pass


class TriDistrictCrawler:
    """Crawler for tridistrict.org (School District)."""

    def __init__(self):
        self.source_name = "tridistrict"
        self.config = SOURCES["tridistrict"]
        self.basic = BasicScraper()
        self.selenium = None

    def _get_scraper(self):
        if self.selenium is None:
            try:
                self.selenium = SeleniumScraper()
                self.selenium._ensure_driver()
            except Exception:
                logger.warning("Selenium not available for tridistrict — using basic scraper")
                self.selenium = False
        return self.selenium if self.selenium else self.basic

    def _is_crawlable(self, url: str) -> bool:
        ok_patterns = ["/apps/pages/", "/apps/news/", "/apps/events/",
                       "/apps/documents/", "/legalnotices", "/apps/maps"]
        path = urlparse(url).path.lower()
        if path in ("", "/"):
            return True
        return any(p in path for p in ok_patterns)

    def find_documents(self) -> list[dict]:
        all_docs = []
        scraper = self._get_scraper()
        visited = set()
        cb = getattr(self, "progress_callback", None)

        # Seed with: main domain root + explicit pages_to_crawl + school_sites + school_pages
        # The explicit pages_to_crawl entries (BOE 25-26, 24-25, archive, budget, etc.) are
        # high-PDF-yield landing pages that must be hit even if depth-bounded discovery misses them.
        seed_urls: list[str] = []
        seed_urls.append(self.config["base_url"] + "/")
        for path in self.config.get("pages_to_crawl", []):
            if path.startswith("http"):
                seed_urls.append(path)
            elif path == "/":
                continue
            else:
                seed_urls.append(self.config["base_url"] + path)
        for school_url in self.config.get("school_sites", []):
            seed_urls.append(school_url)
        for sp in self.config.get("school_pages", []):
            seed_urls.append(sp)

        # Direct fetch of every seed first (single-level), then expand discovered subpages.
        to_crawl = list(dict.fromkeys(seed_urls))
        depth = 0
        max_depth = 2
        max_pages = 200

        while to_crawl and depth <= max_depth and len(visited) < max_pages:
            next_round = []
            for url in to_crawl:
                if url in visited:
                    continue
                visited.add(url)

                soup = scraper.fetch_page(url)
                if not soup:
                    continue

                docs = self.basic.find_document_links(soup, url)
                all_docs.extend(docs)
                if docs:
                    logger.info(f"  Found {len(docs)} documents on {url}")
                    if cb:
                        cb(len(docs))

                subpages = self.basic.find_subpage_links(soup, url, same_domain=False)
                for subpage in subpages:
                    if subpage not in visited and self._is_crawlable(subpage):
                        host = urlparse(subpage).netloc
                        if "tridistrict.org" in host:
                            next_round.append(subpage)

            to_crawl = next_round
            depth += 1
            logger.info(f"  Depth {depth}: discovered {len(next_round)} more pages to crawl")

        self._cleanup()
        return _deduplicate_docs(all_docs)

    def _cleanup(self):
        if self.selenium and self.selenium is not False:
            self.selenium.close()
