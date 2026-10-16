"""
Generic scraper settings (file types, politeness, identification).

Per-site settings for hand-written crawlers live with their jurisdiction, in
jurisdictions/<path>/crawlers.py; generic sources are configured in the
jurisdiction profile (jurisdiction.yaml).
"""

# ─── File types to download ────────────────────────────────────────
DOWNLOAD_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".txt", ".rtf",
}

# Skip these - too large or not useful as documents
SKIP_EXTENSIONS = {
    ".wav", ".wma", ".mp3", ".mp4", ".avi", ".mov", ".wmv",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico",
}

# Meeting recording audio/video — the *recordings* crawlers explicitly look for
# these (the general document crawlers still skip them via SKIP_EXTENSIONS).
RECORDING_EXTENSIONS = {
    ".wma", ".wav", ".m4a", ".mp3", ".mp4", ".mov", ".dcr",
}

# ─── Scraper settings ──────────────────────────────────────────────
REQUEST_DELAY = 1.5
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
# Identify ourselves honestly. Site operators can find the project and
# contact the deployer; robots.txt rules are matched on ROBOTS_AGENT.
import os as _os
ROBOTS_AGENT = "CivicLens"
USER_AGENT = _os.getenv(
    "SCRAPER_USER_AGENT",
    "CivicLens/0.1 (+https://github.com/kknechtel/civic-lens; public-records indexer)",
)
# robots.txt is advisory and these are public records, so it is not consulted
# by default. A deployment can opt in (crawl.respect_robots: true, or
# RESPECT_ROBOTS_TXT=true). Either way we identify honestly and rate-limit.
RESPECT_ROBOTS_TXT = _os.getenv("RESPECT_ROBOTS_TXT", "false").lower() == "true"
