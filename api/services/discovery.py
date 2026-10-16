"""
Discovery: point at a government website, get a draft list of sources.

    report = discover(["https://www.rumsonnj.gov"])
    report.sources   → [{id, label, adapter, params, doc_type?, evidence}]
    report.platforms → {"ecode360": {...}, "civicplus": {...}, ...}

Crawls the site breadth-first (same host, records-related pages first,
~60 pages, polite delay) and fingerprints the records platforms
municipalities use: eCode360, CivicPlus (AgendaCenter / DocumentCenter / RSS),
Granicus, Legistar, BoardDocs, Municode (library + meetings), Laserfiche
WebLink, iCal feeds, YouTube channels, ParentSquare/Finalsite file hosts. Pages
dense with document links become `html_index` sources grouped by topic.

The output is a starting point: `scripts/new_jurisdiction.py` writes it into a
jurisdiction.yaml for a person to review, then `check_profile --live` counts
what each source really finds.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

UA = "CivicLens/0.1 (+https://github.com/kknechtel/civic-lens; public-records indexer; discovery)"
DOC_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?)(\?|$)", re.I)
PRIORITY_RE = re.compile(r"agenda|minute|meeting|council|board|commission|budget|audit|financ|ordinance|resolution|"
                         r"clerk|bid|rfp|procure|planning|zoning|document|archive|records|opra|foil|treasur|tax|"
                         r"legislat|code|notice|public", re.I)
SKIP_RE = re.compile(r"\.(jpe?g|png|gif|svg|webp|mp4|mp3|zip|css|js)(\?|$)|/(login|signin|cart|search)\b|mailto:|tel:", re.I)

TOPICS = [  # (bucket, label, doc_type, pattern on url + link text + page title)
    ("meetings", "Meeting agendas & minutes", None, r"agenda|minute|meeting|council|caucus|work.?session|board"),
    ("finance", "Budgets, audits & financial reports", None, r"budget|audit|financ|treasur|tax|debt|bond|acfr|cafr|annual.?report"),
    ("legislation", "Ordinances & resolutions", "ordinance", r"ordinance|resolution|legislat|local.?law|code"),
    ("land_use", "Planning & zoning", "planning", r"planning|zoning|land.?use|redevelop|master.?plan|variance"),
    ("bids", "Bids & RFPs", "bid", r"\bbid|rfp|rfq|procure|tender|contract"),
]


@dataclass
class Report:
    start_urls: list[str]
    pages_fetched: int = 0
    blocked: list[str] = field(default_factory=list)
    platforms: dict[str, dict] = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _host(u: str) -> str:
    return urlparse(u).netloc.lower().removeprefix("www.")


class _Fetcher:
    def __init__(self, delay: float):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.delay, self.last = delay, 0.0

    @staticmethod
    def _public(url: str) -> bool:
        """Refuse private, loopback, link-local and metadata addresses (SSRF)."""
        import ipaddress
        import socket
        host = urlparse(url).hostname
        if not host or urlparse(url).scheme not in ("http", "https"):
            return False
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True

    def get(self, url: str):
        if not self._public(url):
            log.info("discovery: refusing non-public address %s", url)
            return None
        wait = self.delay - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        try:
            r = self.s.get(url, timeout=(8, 20), allow_redirects=False)
            for _ in range(5):  # follow redirects ourselves so every hop is checked
                if r.status_code not in (301, 302, 303, 307, 308) or not r.headers.get("location"):
                    break
                nxt = urljoin(r.url, r.headers["location"])
                if not self._public(nxt):
                    log.info("discovery: refusing redirect to %s", nxt)
                    return None
                r = self.s.get(nxt, timeout=(8, 20), allow_redirects=False)
        except requests.RequestException as exc:
            log.info("discovery: %s failed: %s", url, exc)
            return None
        return r


def _challenge(r) -> bool:
    body = (r.text or "")[:3000].lower()
    return r.status_code in (403, 429, 503) and ("just a moment" in body or "cf-chl" in body or "challenge" in body)


def discover(start_urls: list[str], max_pages: int = 60, delay: float = 0.4) -> Report:
    rep = Report(start_urls=start_urls)
    f = _Fetcher(delay)
    hosts = {_host(u) for u in start_urls}
    queue: deque[tuple[str, int]] = deque((u, 0) for u in start_urls)
    seen: set[str] = set()
    all_links: list[tuple[str, str, str]] = []   # (href, text, page_url)
    page_docs: dict[str, list[tuple[str, str]]] = {}  # page → [(doc_url, text)]
    page_title: dict[str, str] = {}

    while queue and rep.pages_fetched < max_pages:
        url, depth = queue.popleft()
        url = urldefrag(url)[0]
        if url in seen:
            continue
        seen.add(url)
        r = f.get(url)
        if r is None:
            continue
        if _challenge(r):
            rep.blocked.append(url)
            continue
        if "html" not in r.headers.get("content-type", "") or r.status_code != 200:
            continue
        rep.pages_fetched += 1
        soup = BeautifulSoup(r.text, "html.parser")
        page_title[url] = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())[:120]
        docs, nxt = [], []
        for tag in soup.find_all(["a", "iframe", "script", "link"]):
            href = tag.get("href") or tag.get("src")
            if not href or SKIP_RE.search(href) and not DOC_RE.search(href):
                continue
            absu = urljoin(r.url, href.strip())
            text = " ".join(tag.get_text(" ", strip=True).split())[:120] if tag.name == "a" else tag.name
            all_links.append((absu, text, url))
            if DOC_RE.search(absu):
                docs.append((absu, text))
            elif tag.name == "a" and _host(absu) in hosts and depth < 2 and absu not in seen:
                nxt.append((absu, text))
        if docs:
            page_docs[url] = docs
        # Records-related pages first, then the rest.
        nxt.sort(key=lambda x: 0 if PRIORITY_RE.search(x[0] + " " + x[1]) else 1)
        for u, _ in nxt[:40]:
            queue.append((u, depth + 1))

    _fingerprint(rep, all_links, f)
    _index_sources(rep, page_docs, page_title, hosts)
    if rep.blocked:
        rep.notes.append(f"{len(rep.blocked)} page(s) answered with a bot challenge; use a browser-backed adapter for that host.")
    return rep


def _add_platform(rep: Report, key: str, **info) -> dict:
    p = rep.platforms.setdefault(key, {"evidence": []})
    for k, v in info.items():
        if k == "evidence":
            if v not in p["evidence"] and len(p["evidence"]) < 3:
                p["evidence"].append(v)
        else:
            p.setdefault(k, v)
    return p


def _fingerprint(rep: Report, links: list[tuple[str, str, str]], f: _Fetcher) -> None:
    for href, text, page in links:
        u = urlparse(href)
        host = u.netloc.lower()
        m = re.search(r"ecode360\.com/([A-Z]{2}\d{4})", href)
        if m:
            _add_platform(rep, "ecode360", customer=m.group(1), evidence=href)
        if "/AgendaCenter" in href:
            _add_platform(rep, "civicplus_agenda", base=f"{u.scheme}://{u.netloc}", evidence=href)
        if "/DocumentCenter" in href:
            _add_platform(rep, "civicplus_documents", base=f"{u.scheme}://{u.netloc}", evidence=href)
        if "RSSFeed.aspx" in href:
            _add_platform(rep, "civicplus_rss", evidence=href).setdefault("feeds", set()).add(href)
        if host.endswith("granicus.com"):
            vid = parse_qs(u.query).get("view_id", [None])[0]
            _add_platform(rep, "granicus", host=host, view_id=vid, evidence=href)
        if host.endswith("legistar.com"):
            _add_platform(rep, "legistar", host=host, evidence=href)
        if "boarddocs.com" in host:
            m = re.search(r"boarddocs\.com/(\w+)/(\w+)/", href)
            _add_platform(rep, "boarddocs", path="/".join(m.groups()) if m else None, evidence=href)
        if host == "library.municode.com":
            _add_platform(rep, "municode_library", evidence=href)
        if host.endswith("municodemeetings.com"):
            _add_platform(rep, "municode_meetings", host=host, evidence=href)
        if "weblink" in href.lower() and ("laserfiche" in href.lower() or "/WebLink/" in href):
            _add_platform(rep, "laserfiche", evidence=href)
        if href.lower().endswith(".ics") or href.lower().startswith("webcal:") or "iCalendar" in href:
            _add_platform(rep, "ical", evidence=href).setdefault("feeds", set()).add(href.replace("webcal:", "https:"))
        m = re.search(r"youtube\.com/(?:channel/(UC[\w-]{22})|(@[\w.-]+)|c/([\w.-]+)|user/([\w.-]+))", href)
        if m:
            p = _add_platform(rep, "youtube", evidence=href)
            if m.group(1):
                p["channel_id"] = m.group(1)
            else:
                p.setdefault("handle", m.group(2) or m.group(3) or m.group(4))
        if "parentsquare.com" in host or "finalsite" in host or "edlio" in host:
            _add_platform(rep, "school_cms", host=host, evidence=href)
        if "opramachine.com" in host or "nextrequest.com" in host or "govqa" in host:
            _add_platform(rep, "records_requests", host=host, evidence=href)

    # Resolve a YouTube @handle to its channel id (one fetch of the channel page).
    yt = rep.platforms.get("youtube")
    if yt and not yt.get("channel_id") and yt.get("handle"):
        r = f.get(f"https://www.youtube.com/{yt['handle'] if yt['handle'].startswith('@') else 'c/' + yt['handle']}")
        m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', r.text if r is not None else "")
        if m:
            yt["channel_id"] = m.group(1)

    for k in ("civicplus_rss", "ical"):
        if k in rep.platforms:
            rep.platforms[k]["feeds"] = sorted(rep.platforms[k].get("feeds", []))[:5]


def _index_sources(rep: Report, page_docs, page_title, hosts) -> None:
    p = rep.platforms
    if "ecode360" in p:
        rep.sources.append({
            "id": "ecode360", "label": "ecode360.com", "adapter": "ecode360",
            "description": "eCode360 public documents — resolutions, minutes, agendas, budgets, legislation",
            "params": {"customer": p["ecode360"]["customer"]}, "evidence": p["ecode360"]["evidence"]})
    if "civicplus_agenda" in p:
        base = p["civicplus_agenda"]["base"]
        rep.sources.append({
            "id": "agenda_center", "label": "AgendaCenter", "adapter": "html_index",
            "description": "CivicPlus AgendaCenter — agendas and minutes for every board",
            "params": {"start_urls": [f"{base}/AgendaCenter"], "follow_pattern": r"AgendaCenter/(Search|ViewFile)",
                       "link_pattern": r"AgendaCenter/ViewFile/", "title_from": "row", "max_depth": 1,
                       "doc_type_rules": [{"pattern": "minute", "doc_type": "minutes"}, {"pattern": "agenda", "doc_type": "agenda"}]},
            "evidence": p["civicplus_agenda"]["evidence"]})
    for i, feed in enumerate((p.get("civicplus_rss") or {}).get("feeds", [])[:2]):
        rep.sources.append({"id": f"rss_{i + 1}", "label": "RSS feed", "adapter": "rss", "params": {"url": feed},
                            "evidence": [feed]})
    if p.get("youtube", {}).get("channel_id"):
        rep.sources.append({
            "id": "meeting_video", "label": "Meeting videos (YouTube)", "adapter": "youtube_channel",
            "doc_type": "recording_meeting",
            "params": {"channel_id": p["youtube"]["channel_id"], "title_pattern": "council|board|planning|zoning|meeting|hearing|budget"},
            "evidence": p["youtube"]["evidence"]})
    for k, note in (("granicus", "Granicus video/agenda portal"), ("legistar", "Legistar legislative portal"),
                    ("boarddocs", "BoardDocs (school board)"), ("municode_meetings", "Municode Meetings portal"),
                    ("laserfiche", "Laserfiche WebLink repository"), ("municode_library", "Municode code library")):
        if k in p:
            rep.notes.append(f"Found {note} ({p[k]['evidence'][0]}). No generic adapter yet — add an html_index "
                             "source pointed at its listing page, or contribute an adapter.")

    covered = {"ecode360.com"} if "ecode360" in p else set()
    # Document links in the site header/footer (an OPRA form, a permit app)
    # show up on most pages; they aren't records of any topic.
    seen_on = Counter(d for docs in page_docs.values() for d in {u for u, _ in docs})
    boiler = {d for d, n in seen_on.items() if n >= 3 and n >= 0.3 * max(1, len(page_docs))}
    page_docs = {pg: [(u, t) for u, t in docs if u not in boiler] for pg, docs in page_docs.items()}
    exclude = "|".join(sorted(re.escape(urlparse(d).path.rsplit("/", 1)[-1]) for d in boiler if urlparse(d).path.rsplit("/", 1)[-1])[:12])
    # Topic buckets from pages dense with document links (on-site or hosted elsewhere).
    buckets: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for page, docs in page_docs.items():
        if len(docs) < 3:
            continue
        hay = page + " " + page_title.get(page, "") + " " + " ".join(t for _, t in docs[:30])
        scores = Counter()
        for bucket, _, _, pat in TOPICS:
            scores[bucket] = len(re.findall(pat, hay, re.I))
        bucket, hits = scores.most_common(1)[0]
        if hits:
            buckets[bucket].append((page, len(docs)))
    for bucket, label, doc_type, _ in TOPICS:
        pages = sorted(buckets.get(bucket, []), key=lambda x: -x[1])[:6]
        if not pages:
            continue
        src = {"id": bucket, "label": label, "adapter": "html_index",
               "description": f"{label} — {sum(n for _, n in pages)} document links on {len(pages)} page(s) at discovery time",
               "params": {"start_urls": [u for u, _ in pages], "title_from": "row"},
               "evidence": [f"{u} ({n} docs)" for u, n in pages[:3]]}
        if doc_type:
            src["doc_type"] = doc_type
        if bucket == "meetings":
            src["params"]["doc_type_rules"] = [{"pattern": "minute", "doc_type": "minutes"},
                                               {"pattern": "agenda", "doc_type": "agenda"}]
        if exclude:
            src["params"]["exclude_pattern"] = exclude
        # Off-site file hosts (Wix/Squarespace/S3 file stores) that serve this topic's documents.
        ext = Counter(_host(d) for u, _ in pages for d, _ in page_docs[u])
        doc_hosts = sorted(h for h, n in ext.items() if n >= 3 and h not in hosts and h not in covered)
        if doc_hosts:
            src["params"]["allow_domains"] = doc_hosts[:5]
        rep.sources.append(src)
    if not rep.sources:
        rep.notes.append("No document sources found automatically. The records may sit behind a search form or "
                         "a platform listed above; configure sources by hand (docs/ADDING_A_MUNICIPALITY.md).")


def to_yaml(rep: Report, entity: str) -> str:
    """Draft `sources:` entries for a jurisdiction.yaml."""
    import yaml
    out = []
    for src in rep.sources:
        d = {k: v for k, v in {"id": src["id"], "label": src.get("label"), "description": src.get("description"),
                               "adapter": src["adapter"], "entity": entity, "doc_type": src.get("doc_type"),
                               "params": src["params"]}.items() if v}
        out.append(d)
    return yaml.safe_dump({"sources": out}, sort_keys=False, allow_unicode=True, width=110)
