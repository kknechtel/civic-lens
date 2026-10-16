"""
Crawlers for Atlantic Highlands, NJ and the sources its profile declares
(ahnj.com, tridistrict.org / HHRSD, NJ state downloads, Monmouth County, Census,
police/fire feeds, OPRAmachine, meeting recordings).

Loaded as a jurisdiction plugin by services.sources.registry.LegacyCrawlerAdapter
for sources whose `params.class` names a class here.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import time

from bs4 import BeautifulSoup

import services
from services.scraper.crawlers import YOUTUBE_ID_RE, dedupe_docs, parse_meeting_date, stealth_driver
from services.scraper.scraper import BasicScraper, SeleniumScraper

logger = logging.getLogger("scraper.crawlers.atlantic_highlands")

_API_DIR = Path(services.__file__).resolve().parent.parent

# ─── Per-site settings (formerly services/scraper/config.py) ───────
SOURCES = {
    "ahnj": {
        "base_url": "https://www.ahnj.com",
        "description": "Borough of Atlantic Highlands Official Site",
        # `pages_to_crawl` are visited on every run (current activity).
        # `historical_pages` are only visited when historical=True (the default for
        # manual runs from the UI; recent_only mode skips them).
        "pages_to_crawl": [
            # Government & Administration
            "/ahnj/Government",
            "/ahnj/Government/Budget%20Information/",
            "/ahnj/Government/Borough%20Ordinances/",
            "/ahnj/Government/Government%20Records/",
            "/ahnj/Government/Government%20Records/Most%20Referenced%20Ordinances/",
            # Meetings & Agendas (current)
            "/ahnj/Meetings%20and%20Agendas/",
            "/ahnj/Agendas/",
            # Planning Board — current year + the two prior years (where revisions
            # to recent meetings still land). Older years are in historical_pages.
            "/ahnj/Departments/Planning%20Board/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2024/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2025/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2026/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/2024/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/2025/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/2026/",
            # Departments
            "/ahnj/Departments",
            "/ahnj/Departments/Building%20Department/",
            "/ahnj/Departments/Finance%20Department/",
            "/ahnj/Departments/Zoning%20Office/",
            "/ahnj/Departments/Affordable%20Housing/",
            "/ahnj/Departments/Tax%20Assessor/",
            "/ahnj/Departments/Tax%20Collector/",
            "/ahnj/Departments/Police%20Department/",
            "/ahnj/Departments/Fire%20Department/",
            # Other
            "/ahnj/Community",
            "/ahnj/Court",
            "/ahnj/Emergency",
            "/ahnj/Events/",
            "/ahnj/Recreation/",
            "/ahnj/Forms/",
            "/ahnj/Forms/Planning%20and%20Zoning/",
            "/ahnj/Other%20Links/BIDS%20and%20RFPs/",
            "/ahnj/Announcements/",
            # Events calendar - has all meeting schedules with times
            "/ahnj/Upcoming%20Events/",
            "/ahnj/Upcoming%20Events/#gsc.tab=0",
            "/ahnj/Events/Community%20Events/",
        ],
        "historical_pages": [
            # 2005-2013 Council Agenda archives — frozen content. Skipped in
            # recent_only mode.
            "/ahnj/Agendas/Archives/",
            "/ahnj/Agendas/Archives/2005/",
            "/ahnj/Agendas/Archives/2006/",
            "/ahnj/Agendas/Archives/2007/",
            "/ahnj/Agendas/Archives/2008/",
            "/ahnj/Agendas/Archives/2009/",
            "/ahnj/Agendas/Archives/2010/",
            "/ahnj/Agendas/Archives/2011/",
            "/ahnj/Agendas/Archives/2012/",
            "/ahnj/Agendas/Archives/2013/",
            # Older Planning Board archive tree
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2022/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2023/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/Archives/",
        ],
    },
    "tridistrict": {
        "base_url": "https://www.tridistrict.org",
        "description": "Henry Hudson Regional School District (Tri-District)",
        "pages_to_crawl": [
            "/",
            # BOE Agendas & Minutes — by school year
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2675269",  # 2025-2026
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593279",  # 2024-2025
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593280",  # Archive
            # Board of Education main + supporting pages
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2432532",  # BOE main
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593229",  # Meeting Information
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593225",  # Board Members
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593533",  # Board Committees
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593278",  # Board Policies
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2580048",  # School Budget Information
            # District-wide
            "/apps/pages/index.jsp?uREC_ID=3733525&type=d&pREC_ID=2432536",  # Strategic Planning Documents
            "/apps/pages/index.jsp?uREC_ID=3733531&type=d&pREC_ID=2432534",  # Curriculum & Instruction
            "/apps/pages/index.jsp?uREC_ID=3733531&type=d",                  # Curriculum dept root
        ],
        "school_sites": [
            "https://ahes.tridistrict.org",
            "https://hes.tridistrict.org",
            "https://hhrs.tridistrict.org",
        ],
        # Per-school deep-link landing pages (Performance Reports, etc.)
        "school_pages": [
            # Henry Hudson Regional (the operating consolidated district as of 7/1/2024)
            "https://hhrs.tridistrict.org/apps/pages/index.jsp?uREC_ID=3758135&type=d&pREC_ID=2439311",  # Performance Report
            # Atlantic Highlands Elementary (predecessor, AHSD/0130)
            "https://ahes.tridistrict.org/apps/pages/index.jsp?uREC_ID=3733534&type=d&pREC_ID=2432548",  # Performance Report
            # Highlands Elementary (predecessor, HSD)
            "https://hes.tridistrict.org/apps/pages/index.jsp?uREC_ID=3763022&type=d&pREC_ID=2440904",   # Performance Report
        ],
    },
    "opra": {
        "base_url": "https://opramachine.com",
        "description": "OPRAmachine - Crowdsourced OPRA Requests for Atlantic Highlands",
        "api_url": "https://opramachine.com/body/atlantic_highlands.json",
        "pages_to_crawl": [
            "/body/atlantic_highlands",
        ],
    },
    "police": {
        "base_url": "https://spotcrime.com",
        "description": "Police/Crime Data - SpotCrime, CrimeMapping, Nixle",
        "pages_to_crawl": [
            # SpotCrime
            "https://spotcrime.com/nj/atlantic+highlands",
            # CrimeMapping (may need Selenium)
            "https://www.crimemapping.com/map/nj/atlantichighlands",
            # Nixle/Rave alerts for AH PD
            "https://www.nixle.com/atlantic-highlands-police-department/",
            # AH PD on borough website
            "https://www.ahnj.com/ahnj/Departments/Police%20Department/",
        ],
    },
    "fire": {
        "base_url": "https://web.pulsepoint.org",
        "description": "Fire/EMS Data - PulsePoint, Monmouth County OEM",
        "pages_to_crawl": [
            # PulsePoint web viewer
            "https://web.pulsepoint.org/",
            # AH Fire Department on borough site
            "https://www.ahnj.com/ahnj/Departments/Fire%20Department/",
            # Monmouth County OEM
            "https://www.co.monmouth.nj.us/page.aspx?ID=2434",
        ],
    },
    "county": {
        "base_url": "https://www.monmouthcountyclerk.com",
        "description": "Monmouth County Property Records, Tax Data, Clerk Archives",
        "pages_to_crawl": [
            # County clerk archives for AH
            "https://www.monmouthcountyclerk.gov/archives/record-groups/municipal-records/atlantic-highlands/",
            # Property records search
            "https://oprs.co.monmouth.nj.us/oprs/clerk/clerkhome.aspx?op=basic",
        ],
    },
    "census": {
        "base_url": "https://api.census.gov",
        "description": "US Census ACS Data for Atlantic Highlands",
        "api_config": {
            "state_fips": "34",
            "county_fips": "025",
            "place_fips": "01960",
            "full_fips": "3402501960",
            # Variables: population, median income, median home value, poverty rate, etc.
            "variables": [
                "B01003_001E",  # Total population
                "B19013_001E",  # Median household income
                "B25077_001E",  # Median home value
                "B17001_002E",  # Below poverty level
                "B01002_001E",  # Median age
                "B25001_001E",  # Total housing units
                "B25003_002E",  # Owner occupied
                "B25003_003E",  # Renter occupied
            ],
        },
    },
    "nj_state": {
        "base_url": "https://www.nj.gov",
        "description": "NJ State Data Sources (ACFR, DLGS, Court Opinions)",
        "direct_downloads": [
            # ── NJ DOE School ACFR reports — verified years 18-24 only.
            # 0130 = Atlantic Highlands SD (HHRSD predecessor)
            # 2120 = Henry Hudson Regional SD (HHRSD predecessor)
            # 2160 = Highlands SD (HHRSD third predecessor)
            # Years 15-17 return 404 — removed.
            *[f"https://www.nj.gov/education/finance/fp/acfr/search/{y}/0130.pdf" for y in (18, 19, 20, 21, 22, 23, 24)],
            *[f"https://www.nj.gov/education/finance/fp/acfr/search/{y}/2120.pdf" for y in (18, 19, 20, 21, 22, 23, 24)],
            *[f"https://www.nj.gov/education/finance/fp/acfr/search/{y}/2160.pdf" for y in (18, 19, 20, 21, 22, 23, 24)],
            # ── NJ DCA Division of Local Government Services
            # User-Friendly Budget Database (single .xlsm, all NJ municipalities incl. Atlantic Highlands)
            "https://www.nj.gov/dca/dlgs/programs/mc_budget_docs/UFB%20Database%20-%20FINAL.xlsm",
            # Sea Bright litigation court opinions
            "https://www.njcourts.gov/system/files/court-opinions/2024/a0716-23.pdf",
            "https://www.njcourts.gov/system/files/court-opinions/2025/a_68_24.pdf",
            # Community letters on Sea Bright
            "https://www.ahnj.com/ahnj/Announcements/LettertoCommnity-SeaBrightDecision.pdf",
            "https://highlandsnj.gov/wp-content/uploads/2024/12/Public-Letter-on-Regionalization.pdf",
            "https://highlandsnj.gov/wp-content/uploads/2025/06/Decision-on-Sea-Bright-Amended-Petition-June-2025-2.pdf",
            # Master Plan
            "https://www.ahnj.com/ahnj/Departments/Planning%20Board/Master%20Plan%20(Adopted%202019).pdf",
            "https://www.ahnj.com/ahnj/Departments/Planning%20Board/Zoning%20Map.pdf",
            # Housing Plan
            "https://www.ahnj.com/ahnj/_zumu_user_doc_cache/Housing_Plan_Element_and_Fair_Share_Plan___Atlantic_Highlands___Adopted_June_12_2025.pdf",
        ],
    },
}

# Last-12-months filter is applied by the crawler at scrape time so we don't
# pull every back-year on every run.
RECORDING_SOURCES = {
    "ah_recordings": {
        "base_url": "https://www.ahnj.com",
        "description": "Atlantic Highlands meeting recordings (Council, Planning, Harbor)",
        "bodies": [
            {
                "name": "Borough Council",
                "path": "/ahnj/Administration/Council%20Recordings/",
                "doc_type": "recording_council",
            },
            {
                "name": "Planning Board",
                # Year folders live under _Agendas and Minutes/<YYYY>/Recordings/;
                # the parent index page lists them. Crawler walks year subfolders.
                "path": "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/",
                "doc_type": "recording_planning",
                "year_subfolders": True,
            },
            {
                "name": "Harbor Commission",
                "path": "/ahnj/AH%20Harbor/Harbor%20Meeting%20Recordings/",
                "doc_type": "recording_harbor",
                "year_subfolders": True,
            },
        ],
    },
    "hhrsd_recordings": {
        "base_url": "https://www.tridistrict.org",
        "description": "Henry Hudson Regional School District BOE meeting recordings (YouTube)",
        # The "Board Meeting Recordings" page on tridistrict.org is a flat list
        # of anchors → youtu.be/<id>, grouped by school year. We scrape anchors.
        "pages_to_crawl": [
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2596745",
        ],
        "doc_type": "recording_school_board",
    },
}


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
        return dedupe_docs(all_docs)

    def _cleanup(self):
        if self.selenium and self.selenium is not False:
            self.selenium.close()



class OPRACrawler:
    """Crawler for OPRAmachine.com - crowdsourced OPRA requests for Atlantic Highlands.
    Requires Selenium due to Cloudflare protection."""

    def __init__(self):
        self.source_name = "opra"
        self.config = SOURCES.get("opra", {})
        self.driver = None

    def find_documents(self) -> list[dict]:
        all_docs = []
        self.driver = stealth_driver()
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
                    logger.warning("  Cloudflare blocking, waiting longer...")
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

        return dedupe_docs(all_docs)

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
        return dedupe_docs(all_docs)

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
        return dedupe_docs(all_docs)

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
                    meeting_date = parse_meeting_date(title_with_url)
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

        return dedupe_docs(all_docs)

    def _cleanup(self):
        pass



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
                m = YOUTUBE_ID_RE.search(link["href"])
                if not m:
                    continue
                yt_id = m.group(1)
                if yt_id in seen_ids:
                    continue
                seen_ids.add(yt_id)

                anchor_text = link.get_text(strip=True) or f"HHRSD BOE Meeting {yt_id}"
                meeting_date = parse_meeting_date(anchor_text)
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
        return dedupe_docs(all_docs)

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
        return dedupe_docs(all_docs)

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
                out_dir = _API_DIR / "storage" / "census"
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
        return dedupe_docs(all_docs)

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
        return dedupe_docs(all_docs)

    def _cleanup(self):
        if self.selenium and self.selenium is not False:
            self.selenium.close()
