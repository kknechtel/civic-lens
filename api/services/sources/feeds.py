"""
Feed-based adapters: YouTube channels, RSS/Atom feeds, XML sitemaps.

youtube_channel params:
  channel_id:     UC... id                                   required
  max_items:      newest N uploads to consider (default 50; 500 with backfill)

  Listing uses the YouTube Data API v3 when YOUTUBE_API_KEY is set (free
  quota covers this easily). YouTube's robots.txt disallows the public
  /feeds/videos.xml RSS for crawlers, so without a key the adapter only uses
  RSS when the source sets respect_robots: false.
  title_pattern:  regex a video title must match (e.g. "council|board")
  meeting_body:   label stored on the recording ("City Council")
  backfill:       true → list the whole channel with yt-dlp instead of the
                  15-item RSS feed (use for the first run)
  max_age_days:   skip videos older than this (default 365; 0 = no limit)

rss params:
  url:            feed URL                                    required
  link_pattern:   regex an item/enclosure URL must match (default: doc extensions)

sitemap params:
  url:            sitemap.xml (sitemap indexes are followed)  required
  link_pattern:   regex a <loc> must match                    required
  max_urls:       cap (default 5000)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from .base import SourceAdapter, logger
from .html_index import _DOC_EXT_RE

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_MONTH_NUM = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_DATE_PATTERNS = [
    (re.compile(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[/_.-](\d{1,2})[/_.-](20\d{2}|\d{2})(?!\d)"), "mdy"),
    (re.compile(rf"({_MONTHS})[a-z]*\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})", re.I), "Mdy"),
]


def parse_date(text: str) -> str | None:
    """Best-effort YYYY-MM-DD from a title like 'City Council Meeting 10-07-2025'."""
    for rx, kind in _DATE_PATTERNS:
        m = rx.search(text or "")
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m[1]), int(m[2]), int(m[3])
            elif kind == "mdy":
                mo, d, y = int(m[1]), int(m[2]), int(m[3])
                y = y + 2000 if y < 100 else y
            else:
                mo = _MONTH_NUM[m[1][:3].lower()]
                d, y = int(m[2]), int(m[3])
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            continue
    return None


class YouTubeChannelAdapter(SourceAdapter):
    name = "youtube_channel"

    def discover(self) -> list[dict]:
        p = self.params
        channel_id = p.get("channel_id")
        if not channel_id:
            raise ValueError(f"{self.source.id}: youtube_channel needs params.channel_id")
        title_re = self._compile(p.get("title_pattern"))
        max_age = int(p.get("max_age_days", 365))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age)).date() if max_age else None
        body = p.get("meeting_body") or self.source.label or self.municipality.short_name

        import os
        max_items = int(p.get("max_items", 500 if p.get("backfill") else 50))
        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if api_key:
            videos = self._list_api(channel_id, api_key, max_items)
        elif p.get("backfill"):
            videos = self._list_backfill(channel_id)
        else:
            videos = self._list_rss(channel_id)
            if not videos and self.basic.respect_robots:
                self.basic.errors.append(
                    f"{self.source.id}: set YOUTUBE_API_KEY to list channel {channel_id} "
                    "(YouTube's robots.txt disallows the RSS feed for crawlers)")
        out = []
        for v in videos:
            title = v["title"]
            if title_re and not title_re.search(title):
                continue
            meeting_date = parse_date(title) or v.get("published")
            if cutoff and meeting_date and datetime.strptime(meeting_date, "%Y-%m-%d").date() < cutoff:
                continue
            out.append({
                "url": f"https://www.youtube.com/watch?v={v['id']}",
                "title": title,
                "source_page": f"https://www.youtube.com/channel/{channel_id}",
                "doc_type": self.source.doc_type or "recording_meeting",
                "recording": {
                    "platform": "youtube",
                    "meeting_body": body,
                    "meeting_date": meeting_date,
                    "youtube_id": v["id"],
                },
            })
        self._tick(len(out))
        return out

    def _list_api(self, channel_id: str, api_key: str, max_items: int) -> list[dict]:
        """Uploads playlist via the Data API: 1 quota unit per 50 videos."""
        uploads = "UU" + channel_id[2:]
        vids, token = [], None
        while len(vids) < max_items:
            params = {"part": "snippet", "playlistId": uploads, "maxResults": 50, "key": api_key}
            if token:
                params["pageToken"] = token
            r = self.basic.session.get("https://www.googleapis.com/youtube/v3/playlistItems",
                                       params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            for it in data.get("items", []):
                sn = it.get("snippet", {})
                vid = (sn.get("resourceId") or {}).get("videoId")
                if vid:
                    vids.append({"id": vid, "title": sn.get("title") or vid,
                                 "published": (sn.get("publishedAt") or "")[:10] or None})
            token = data.get("nextPageToken")
            if not token:
                break
        return vids[:max_items]

    def _list_rss(self, channel_id: str) -> list[dict]:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        resp = self._get(url)
        if resp is None:
            return []
        root = ET.fromstring(resp.content)
        vids = []
        for e in root.findall(f"{_ATOM}entry"):
            vid = e.findtext(f"{_YT}videoId")
            if vid:
                vids.append({
                    "id": vid,
                    "title": e.findtext(f"{_ATOM}title") or vid,
                    "published": (e.findtext(f"{_ATOM}published") or "")[:10] or None,
                })
        return vids

    def _list_backfill(self, channel_id: str) -> list[dict]:
        import yt_dlp  # heavy import; only for backfill runs

        opts = {"quiet": True, "extract_flat": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/channel/{channel_id}/videos", download=False)
        vids = []
        for e in (info or {}).get("entries") or []:
            if e and e.get("id"):
                vids.append({"id": e["id"], "title": e.get("title") or e["id"], "published": None})
        return vids


class RssAdapter(SourceAdapter):
    name = "rss"

    def discover(self) -> list[dict]:
        url = self.params.get("url")
        if not url:
            raise ValueError(f"{self.source.id}: rss needs params.url")
        link_re = self._compile(self.params.get("link_pattern") or _DOC_EXT_RE)
        resp = self._get(url)
        if resp is None:
            return []
        root = ET.fromstring(resp.content)
        out = []
        items = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
        for it in items:
            title = (it.findtext("title") or it.findtext(f"{_ATOM}title") or "").strip()
            links = [it.findtext("link") or ""]
            links += [enc.get("url", "") for enc in it.findall("enclosure")]
            links += [l.get("href", "") for l in it.findall(f"{_ATOM}link")]
            for link in filter(None, (l.strip() for l in links)):
                if link_re.search(link):
                    out.append({"url": link, "title": title, "source_page": url})
        self._tick(len(out))
        return out


class SitemapAdapter(SourceAdapter):
    name = "sitemap"

    def discover(self) -> list[dict]:
        url = self.params.get("url")
        pattern = self.params.get("link_pattern")
        if not url or not pattern:
            raise ValueError(f"{self.source.id}: sitemap needs params.url and params.link_pattern")
        link_re = self._compile(pattern)
        max_urls = int(self.params.get("max_urls", 5000))
        todo, seen, out = [url], set(), []
        while todo and len(out) < max_urls:
            sm = todo.pop()
            if sm in seen:
                continue
            seen.add(sm)
            try:
                resp = self._get(sm)
            except Exception as e:
                logger.warning(f"[{self.source.id}] sitemap {sm} failed: {e}")
                continue
            if resp is None:
                continue
            root = ET.fromstring(resp.content)
            todo += [l.text.strip() for l in root.findall(f"{_SM}sitemap/{_SM}loc") if l.text]
            for loc in root.findall(f"{_SM}url/{_SM}loc"):
                if loc.text and link_re.search(loc.text):
                    out.append({"url": loc.text.strip(), "title": "", "source_page": sm})
        self._tick(len(out))
        return out[:max_urls]
