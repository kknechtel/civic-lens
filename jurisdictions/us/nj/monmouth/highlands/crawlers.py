"""
Crawlers for the Borough of Highlands, NJ (highlandsnj.gov and its Municode
meetings portal). Loaded as a jurisdiction plugin by
services.sources.registry.LegacyCrawlerAdapter.
"""
import logging
from urllib.parse import urlparse

from services.scraper.crawlers import dedupe_docs
from services.scraper.scraper import BasicScraper

logger = logging.getLogger("scraper.crawlers.highlands")

# ─── Per-site settings (formerly services/scraper/config.py) ───────
SOURCES = {
    "highlands_borough": {
        "base_url": "https://highlandsnj.gov",
        "description": "Borough of Highlands NJ (HHRSD constituent town)",
        "pages_to_crawl": [
            "/",
            "/regionalization-survey/",
            "/category/council-agendas/",
        ],
        "direct_downloads": [
            "https://highlandsnj.gov/wp-content/uploads/2024/12/Public-Letter-on-Regionalization.pdf",
            "https://highlandsnj.gov/wp-content/uploads/2025/06/Decision-on-Sea-Bright-Amended-Petition-June-2025-2.pdf",
        ],
    },
    "highlands_meetings": {
        "base_url": "https://highlands-nj.municodemeetings.com",
        "description": "Highlands Borough Council — Municode meeting portal (agendas/packets)",
        "pages_to_crawl": ["/"],
    },
}


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
        return dedupe_docs(all_docs)

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
        return dedupe_docs(all_docs)

    def _cleanup(self):
        pass

