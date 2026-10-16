"""
html_index — crawl one or more index pages and collect document links.

Covers most municipal CMSs (Vision/Granicus, CivicPlus, Finalsite, Edlio,
WordPress) where agendas and minutes are plain <a href="...pdf"> links.

params:
  start_urls:      [url, ...]                      required
  link_pattern:    regex a document URL must match  (default: known doc extensions)
  exclude_pattern: regex to drop document URLs
  follow_pattern:  regex for sub-pages to crawl     (default: none — only start_urls)
  max_depth:       how many follow hops             (default 1 when follow_pattern set)
  max_pages:       hard cap on pages fetched        (default 200)
  allow_domains:   extra domains documents may live on (start URL domains always allowed)
  title_from:      "link" | "row"  — "row" uses the enclosing <tr>/<li> text,
                   useful when link text is just "Agenda" / "Minutes"
  group_selector:  CSS selector for sections that group links (accordion
                   panels, year blocks). The section heading is prefixed to
                   each title: "2025-2026 Minutes — June 9, 2026".
  group_heading_selector: CSS selector for the heading inside a group (default: h2,h3,h4)
  doc_type_rules:  [{pattern: regex, doc_type: minutes}, ...] matched in order
                   against "<group heading> <title>"; first hit sets doc_type
  min_year:        skip documents whose latest year (title, else URL) is before this
  max_year:        skip documents whose latest year is after this (for archive sources)
"""
from __future__ import annotations

import re
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

from services.scraper.config import DOWNLOAD_EXTENSIONS, SKIP_EXTENSIONS
from services.scraper.utils import url_to_filename

from .base import SourceAdapter, logger

_DOC_EXT_RE = "(" + "|".join(re.escape(e) for e in sorted(DOWNLOAD_EXTENSIONS)) + r")(\?|$)"


def _row_text(a) -> str:
    for parent in a.parents:
        if parent.name in ("tr", "li", "p", "article"):
            text = " ".join(parent.get_text(" ", strip=True).split())
            if 0 < len(text) <= 300:
                return text
            break
    return ""


# Link text that says nothing about the document.
_GENERIC_TITLE = re.compile(
    r"^\W*(download|click here|here|link|view|open|pdf|read( the)?( complete)? report|"
    r"full report|document|file|agenda|minutes)\W*(\(?pdf\)?)?\W*$", re.I)
_TITLE_NOISE = [
    re.compile(r"^\s*\.?(pdf|docx?|xlsx?|pptx?)\b\s*", re.I),             # file-type icon text
    re.compile(r"\s*,?\s*\(?opens? (?:in a new (?:window|tab)|(?:a )?(?:pdf|word|excel) (?:document|file))\)?\s*", re.I),  # a11y suffixes
    re.compile(r"\s*[\[(]\s*(pdf|docx?)\s*(,?\s*[\d.]+\s*[kmg]b)?\s*[\])]\s*", re.I),
]


def clean_title(text: str) -> str:
    t = " ".join((text or "").split())
    for rx in _TITLE_NOISE:
        t = rx.sub(" ", t).strip()
    return t


_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


def _latest_year(text: str) -> int | None:
    years = [int(m.group(0)) for m in _YEAR_RE.finditer(text or "")]
    return max(years) if years else None


class HtmlIndexAdapter(SourceAdapter):
    name = "html_index"

    def _groups(self, soup) -> dict:
        """{id(<a>): heading text} for links inside group_selector sections."""
        sel = self.params.get("group_selector")
        if not sel:
            return {}
        head_sel = self.params.get("group_heading_selector", "h2, h3, h4")
        out = {}
        for g in soup.select(sel):
            h = g.select_one(head_sel)
            heading = " ".join(h.get_text(" ", strip=True).split()) if h else ""
            for a in g.find_all("a", href=True):
                out[id(a)] = heading
        return out

    def _doc_type(self, text: str) -> str | None:
        for rule in self.params.get("doc_type_rules") or []:
            if re.search(rule["pattern"], text, re.I):
                return rule["doc_type"]
        return None

    def discover(self) -> list[dict]:
        p = self.params
        start_urls = p.get("start_urls") or []
        if isinstance(start_urls, str):
            start_urls = [start_urls]
        if not start_urls:
            raise ValueError(f"{self.source.id}: html_index needs params.start_urls")

        link_re = self._compile(p.get("link_pattern") or _DOC_EXT_RE)
        exclude_re = self._compile(p.get("exclude_pattern"))
        follow_re = self._compile(p.get("follow_pattern"))
        max_depth = int(p.get("max_depth", 1 if follow_re else 0))
        max_pages = int(p.get("max_pages", 200))
        title_from = p.get("title_from", "link")
        min_year = int(p["min_year"]) if p.get("min_year") else None
        max_year = int(p["max_year"]) if p.get("max_year") else None
        domains = {urlparse(u).netloc.lower() for u in start_urls} | {d.lower() for d in p.get("allow_domains", [])}

        queue = deque((u, 0) for u in start_urls)
        visited: set[str] = set()
        found: list[dict] = []

        while queue and len(visited) < max_pages:
            url, depth = queue.popleft()
            url = urldefrag(url)[0]
            if url in visited:
                continue
            visited.add(url)
            soup = self.basic.fetch_page(url)
            if soup is None:
                continue

            page_docs = 0
            groups = self._groups(soup)
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                    continue
                full = urldefrag(urljoin(url, href))[0]
                parsed = urlparse(full)
                if parsed.scheme not in ("http", "https"):
                    continue
                path = parsed.path.lower()
                if any(path.endswith(ext) for ext in SKIP_EXTENSIONS):
                    continue

                if link_re.search(full) and parsed.netloc.lower() in domains:
                    if exclude_re and exclude_re.search(full):
                        continue
                    title = clean_title(a.get_text(" ", strip=True))
                    if title_from == "row" or not title or _GENERIC_TITLE.match(title):
                        title = clean_title(_row_text(a)) or title
                    if not title or _GENERIC_TITLE.match(title):
                        title = url_to_filename(full).rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
                    heading = groups.get(id(a), "")
                    if heading and heading.lower() not in title.lower():
                        title = f"{heading} — {title}"
                    if min_year or max_year:
                        y = _latest_year(title) or _latest_year(full)
                        if y and ((min_year and y < min_year) or (max_year and y > max_year)):
                            continue
                    item = {"url": full, "title": title, "source_page": url}
                    dt = self._doc_type(f"{heading} {title} {url_to_filename(full)}")
                    if dt:
                        item["doc_type"] = dt
                    found.append(item)
                    page_docs += 1
                elif (follow_re and depth < max_depth and follow_re.search(full)
                      and parsed.netloc.lower() in domains and full not in visited):
                    queue.append((full, depth + 1))

            self._tick(page_docs)

        if queue:
            logger.warning(f"[{self.source.id}] stopped at max_pages={max_pages} with {len(queue)} pages unvisited")
        return found
