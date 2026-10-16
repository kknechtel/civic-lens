"""
Core scraper module.
Level 1: requests + BeautifulSoup (works for most static pages)
Level 2: Selenium fallback for JavaScript-heavy pages
"""
import io
import os
import time
import logging
import requests
from requests.exceptions import ConnectTimeout, ConnectionError as RequestsConnectionError
from urllib.parse import urljoin, urlparse, urlencode
from bs4 import BeautifulSoup

from .config import (
    RESPECT_ROBOTS_TXT,
    ROBOTS_AGENT,
    DOWNLOAD_EXTENSIONS,
    SKIP_EXTENSIONS,
    RECORDING_EXTENSIONS,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    USER_AGENT,
)
from .utils import url_to_filename, categorize_url

logger = logging.getLogger("scraper")


_HOST_FAIL_THRESHOLD = 3
_CONNECT_TIMEOUT = 10  # short connect TO so blocked hosts fail fast; read TO stays at REQUEST_TIMEOUT

# Per-host proxy routing. Some target hosts firewall or throttle AWS source
# IPs (ahnj.com outright firewalls; highlands-nj.municodemeetings.com appears
# to throttle pagination from datacenter IPs). When AHNJ_PROXY_URL is set we
# route requests to listed hosts through a Cloudflare Worker that fetches
# the target from a residential-class IP and returns the bytes verbatim.
# The Worker source is in workers/ahnj-proxy/.
_PROXY_URL = (os.getenv("AHNJ_PROXY_URL") or "").rstrip("/")
_PROXY_SECRET = os.getenv("AHNJ_PROXY_SECRET") or ""
_PROXIED_HOSTS = {"www.ahnj.com", "ahnj.com", "highlands-nj.municodemeetings.com"}


def _proxy_for(url: str) -> tuple[str, dict]:
    """If url's host is proxied and a proxy is configured, return
    (rewritten_url, extra_headers). Otherwise return (url, {}).
    Falls back to the direct URL when AHNJ_PROXY_URL is unset so local
    dev keeps working without the Worker."""
    if not _PROXY_URL:
        return url, {}
    host = urlparse(url).netloc.lower()
    if host not in _PROXIED_HOSTS:
        return url, {}
    return f"{_PROXY_URL}/?{urlencode({'url': url})}", {"X-Proxy-Secret": _PROXY_SECRET}


class BasicScraper:
    """Level 1 scraper using requests + BeautifulSoup."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        # Per-host circuit breaker. Some target hosts (e.g. small municipal CMS
        # behind a firewall) silently drop TCP from AWS IP ranges. Without this,
        # the scraper grinds for hours retrying every URL on a dead host.
        self._host_fails: dict[str, int] = {}
        self._dead_hosts: set[str] = set()
        self.errors: list[str] = []  # consumed by runner to surface in UI
        self.respect_robots = RESPECT_ROBOTS_TXT
        self._robots: dict[str, "RobotFileParser | None"] = {}
        self._robots_blocked: set[str] = set()

    # ── robots.txt (RFC 9309) ────────────────────────────────────────
    def _robots_for(self, url: str):
        from urllib.robotparser import RobotFileParser
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}".lower()
        if origin in self._robots:
            return self._robots[origin]
        rp = RobotFileParser()
        try:
            r = self.session.get(origin + "/robots.txt", timeout=(_CONNECT_TIMEOUT, REQUEST_TIMEOUT))
            if r.status_code >= 500:
                rp.disallow_all = True  # RFC 9309 §2.3.1.4: server error → assume disallowed
            elif r.status_code >= 400:
                rp.allow_all = True  # 4xx → no rules
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp.disallow_all = True  # unreachable → be conservative
        self._robots[origin] = rp
        return rp

    def robots_allows(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        allowed = self._robots_for(url).can_fetch(ROBOTS_AGENT, url)
        if not allowed:
            host = self._host(url)
            if host not in self._robots_blocked:
                self._robots_blocked.add(host)
                msg = (f"robots.txt on {host} disallows {ROBOTS_AGENT}; skipping because this "
                       "deployment opted into robots.txt (crawl.respect_robots: true).")
                logger.warning(msg)
                self.errors.append(msg)
        return allowed

    def crawl_delay(self, url: str) -> float:
        if not self.respect_robots:
            return REQUEST_DELAY
        delay = self._robots_for(url).crawl_delay(ROBOTS_AGENT)
        return max(REQUEST_DELAY, float(delay or 0))

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc.lower()

    def _is_host_dead(self, url: str) -> bool:
        return self._host(url) in self._dead_hosts

    def _record_connect_failure(self, url: str) -> None:
        host = self._host(url)
        self._host_fails[host] = self._host_fails.get(host, 0) + 1
        if self._host_fails[host] >= _HOST_FAIL_THRESHOLD and host not in self._dead_hosts:
            self._dead_hosts.add(host)
            msg = (
                f"{host}: unreachable after {self._host_fails[host]} connect failures "
                f"(firewall block or host offline) — skipping remaining URLs"
            )
            logger.error(msg)
            self.errors.append(msg)

    def _record_success(self, url: str) -> None:
        # Reset on a clean response so a transient hiccup doesn't permanently kill a host.
        self._host_fails.pop(self._host(url), None)

    def fetch_page(self, url: str) -> BeautifulSoup | None:
        """Fetch a page and return parsed HTML."""
        url_lower = url.lower()
        if any(url_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
            logger.debug(f"Skipping media file: {url}")
            return None
        if self._is_host_dead(url) or not self.robots_allows(url):
            return None
        fetch_url, proxy_headers = _proxy_for(url)
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"Fetching: {url}" + (" (via proxy)" if proxy_headers else ""))
                resp = self.session.get(fetch_url, timeout=(_CONNECT_TIMEOUT, REQUEST_TIMEOUT), headers=proxy_headers or None)
                resp.raise_for_status()
                self._record_success(url)
                time.sleep(self.crawl_delay(url))
                return BeautifulSoup(resp.text, "html.parser")
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (403, 404, 410):
                    logger.warning(f"HTTP {status} for {url} — skipping")
                    return None
                logger.warning(f"Attempt {attempt+1}/{MAX_RETRIES} failed for {url}: {e}")
                time.sleep(REQUEST_DELAY * 2)
            except (ConnectTimeout, RequestsConnectionError) as e:
                logger.warning(f"Connect failure {attempt+1}/{MAX_RETRIES} for {url}: {e}")
                self._record_connect_failure(url)
                if self._is_host_dead(url):
                    return None
                time.sleep(REQUEST_DELAY * 2)
            except requests.RequestException as e:
                logger.warning(f"Attempt {attempt+1}/{MAX_RETRIES} failed for {url}: {e}")
                time.sleep(REQUEST_DELAY * 2)
        logger.error(f"Failed to fetch after {MAX_RETRIES} attempts: {url}")
        return None

    def find_document_links(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        """Extract all links to downloadable documents from a page."""
        documents = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            path_lower = parsed.path.lower()

            # Skip audio/video/image files
            if any(path_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
                continue

            is_document = any(path_lower.endswith(ext) for ext in DOWNLOAD_EXTENSIONS)
            if not is_document:
                if "download" in href.lower() or "document" in href.lower():
                    # But still skip if it's a media file
                    if not any(path_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
                        is_document = True

            if is_document:
                link_text = link.get_text(strip=True) or url_to_filename(full_url)
                documents.append({
                    "url": full_url,
                    "title": link_text,
                    "source_page": base_url,
                })
        return documents

    def find_media_links(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        """Extract links to audio/video recording files. Counterpart to
        find_document_links — *intentionally* keeps RECORDING_EXTENSIONS that
        find_document_links() filters out."""
        media = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            path_lower = parsed.path.lower()
            if not any(path_lower.endswith(ext) for ext in RECORDING_EXTENSIONS):
                continue
            link_text = link.get_text(strip=True) or url_to_filename(full_url)
            media.append({
                "url": full_url,
                "title": link_text,
                "source_page": base_url,
            })
        return media

    def find_subpage_links(self, soup: BeautifulSoup, base_url: str, same_domain: bool = True) -> list[str]:
        """Find links to other pages on the same site to crawl deeper."""
        links = set()
        base_domain = urlparse(base_url).netloc
        for link in soup.find_all("a", href=True):
            href = link["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            if parsed.scheme not in ("http", "https"):
                continue
            if href.startswith("#") or href.startswith("mailto:"):
                continue
            if same_domain and parsed.netloc != base_domain:
                continue
            if any(parsed.path.lower().endswith(ext) for ext in DOWNLOAD_EXTENSIONS):
                continue
            links.add(full_url)
        return list(links)

    def download_file_to_bytes(self, url: str) -> tuple[bytes | None, str]:
        """Download a file and return (content_bytes, filename). Returns (None, '') on failure."""
        filename = url_to_filename(url)
        if self._is_host_dead(url) or not self.robots_allows(url):
            return None, filename
        fetch_url, proxy_headers = _proxy_for(url)
        try:
            logger.info(f"  [DOWNLOAD] {filename}" + (" (via proxy)" if proxy_headers else ""))
            resp = self.session.get(fetch_url, timeout=(_CONNECT_TIMEOUT, REQUEST_TIMEOUT), stream=True, headers=proxy_headers or None)
            resp.raise_for_status()

            buf = io.BytesIO()
            for chunk in resp.iter_content(chunk_size=8192):
                buf.write(chunk)

            self._record_success(url)
            time.sleep(self.crawl_delay(url))
            return buf.getvalue(), filename
        except (ConnectTimeout, RequestsConnectionError) as e:
            logger.error(f"  [FAIL] Connect failure for {url}: {e}")
            self._record_connect_failure(url)
            return None, filename
        except requests.RequestException as e:
            logger.error(f"  [FAIL] Download failed for {url}: {e}")
            return None, filename


class SeleniumScraper:
    """Level 2 scraper for JavaScript-heavy sites."""

    def __init__(self):
        self.driver = None

    def _ensure_driver(self):
        if self.driver is not None:
            return
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service

            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument(f"--user-agent={USER_AGENT}")
            options.add_argument("--window-size=1920,1080")

            try:
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
            except ImportError:
                self.driver = webdriver.Chrome(options=options)

            self.driver.set_page_load_timeout(60)
            logger.info("Selenium WebDriver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Selenium: {e}")
            raise

    def fetch_page(self, url: str) -> BeautifulSoup | None:
        self._ensure_driver()
        try:
            logger.info(f"[Selenium] Fetching: {url}")
            self.driver.get(url)
            time.sleep(3)
            html = self.driver.page_source
            time.sleep(REQUEST_DELAY)
            return BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.error(f"[Selenium] Failed to fetch {url}: {e}")
            return None

    def close(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
