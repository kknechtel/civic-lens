"""
Utility functions for the scraper.
Handles dedup tracking, URL categorization, naming, and filename generation.
"""
import os
import re
import hashlib
import logging
from urllib.parse import urlparse, unquote

logger = logging.getLogger("scraper")


def url_to_filename(url: str) -> str:
    """Convert a URL to a safe filename, preserving the original file extension."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    filename = os.path.basename(path)

    if not filename:
        segments = [s for s in path.split("/") if s]
        filename = segments[-1] if segments else hashlib.md5(url.encode()).hexdigest()

    # Clean up the filename
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_()")
    cleaned = ""
    for ch in filename:
        cleaned += ch if ch in safe_chars else "_"

    return cleaned[:200]


def url_to_descriptive_name(url: str, source_page: str = "", title: str = "") -> str:
    """
    Generate a descriptive filename from the URL path tree.
    e.g. https://www.ahnj.com/ahnj/Government/Employees/2024%20Holiday%20Closures.pdf
    -> Government - Employees - 2024 Holiday Closures.pdf
    """
    parsed = urlparse(url)
    path = unquote(parsed.path)
    segments = [s for s in path.split("/") if s]

    if not segments:
        return url_to_filename(url)

    # Get the actual filename (last segment)
    filename = segments[-1]
    ext = ""
    for e in [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".zip", ".png", ".jpg", ".jpeg"]:
        if filename.lower().endswith(e):
            ext = e
            filename = filename[: -len(e)]
            break

    # Build descriptive prefix from path segments (skip domain-specific prefixes)
    skip_segments = {"ahnj", "at0153", "documents", "apps", "pages", "index.jsp", "www.ahnj.com", "ecode360.com"}
    path_parts = []
    for seg in segments[:-1]:  # exclude filename
        clean = seg.strip()
        if clean.lower() in skip_segments:
            continue
        # Clean up URL encoding artifacts
        clean = clean.replace("%20", " ").replace("+", " ").replace("_", " ")
        if clean and len(clean) > 1:
            path_parts.append(clean)

    # Clean up filename
    clean_name = filename.replace("%20", " ").replace("_", " ").replace("+", " ").strip()

    # If we have path context, prefix it. Use the LAST 3 segments (closest to
    # the file) so the discriminating bits — year folders, district codes,
    # archive subtree leaves — survive. Using the first 3 segments collapsed
    # NJ Education ACFR years (.../acfr/search/18/0130.pdf → .../search/24/...)
    # and AHNJ Planning Board year subfolders into identical names.
    if path_parts:
        prefix = " - ".join(path_parts[-3:])
        result = f"{prefix} - {clean_name}{ext}"
    else:
        result = f"{clean_name}{ext}"

    # If the title provides better info and filename is generic
    if title and len(title) > 5 and title.lower() not in ["download", "click here", "link"]:
        # Use title if filename is just a hash or ID
        if re.match(r'^[a-f0-9]{8,}$', clean_name.replace(" ", "").replace("-", "")):
            # Link text is often the filename itself ("753221795.pdf") —
            # don't append the extension twice.
            base = title[: -len(ext)] if ext and title.lower().endswith(ext.lower()) else title
            result = f"{_safe_filename(base)}{ext}"

    return _safe_filename(result)[:250]


def _safe_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    # Remove characters that aren't safe in filenames
    safe = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Collapse multiple spaces/underscores
    safe = re.sub(r'[_\s]+', ' ', safe).strip()
    return safe


def categorize_url(url: str) -> str:
    """Categorize a document URL into a subfolder/doc_type name."""
    url_lower = url.lower()

    categories = {
        "agendas": ["agenda"],
        "minutes": ["minute"],
        "budgets": ["budget", "financial", "audit", "cafr"],
        "ordinances": ["ordinance", "code"],
        "resolutions": ["resolution"],
        "policies": ["polic"],
        "strategic_planning": ["strategic", "plan"],
        "board_docs": ["board"],
        "general": [],
    }

    for category, keywords in categories.items():
        if any(kw in url_lower for kw in keywords):
            return category

    return "general"


def detect_doc_type_from_name(filename: str) -> str:
    """Detect document type from a descriptive filename."""
    lower = filename.lower()
    if "agenda" in lower:
        return "agenda"
    if "minute" in lower:
        return "minutes"
    if "budget" in lower:
        return "budget"
    # AMR = Auditor's Management Report — supplementary cap/findings worksheet,
    # NOT a full ACFR. Distinguish so the financial dashboard doesn't try to
    # surface its handful of Excess Surplus lines as the "audit" for the year.
    if re.search(r'\bamr\b', lower) or "auditor's management" in lower or "auditors management" in lower:
        return "audit_management_report"
    if "audit" in lower:
        return "audit"
    if any(kw in lower for kw in ["financial statement", "comprehensive financial", " fs", "cafr"]):
        return "financial_statement"
    if "resolution" in lower:
        return "resolution"
    if "ordinance" in lower or "code" in lower:
        return "ordinance"
    if "performance report" in lower:
        return "performance_report"
    if any(kw in lower for kw in ["civil case", "olszewski", "reply brief", "motion", "order"]):
        return "legal"
    if "opra" in lower or "ferpa" in lower:
        return "records_request"
    if "presentation" in lower or lower.endswith(".pptx"):
        return "presentation"
    if any(kw in lower for kw in ["election", "ballot", "vote"]):
        return "election"
    if "plan" in lower or "strategic" in lower:
        return "planning"
    return "general"


def detect_fiscal_year(filename: str) -> str | None:
    """Extract a fiscal year from a filename.

    Accepts 2024, 2024-2025, or 2024-25 — but only when the second half is
    actually the next year (so "2026-071 Payment of Bills" doesn't get
    mis-tagged as a "2026-07" school year).
    """
    # School-year YYYY-YYYY (must be year+1)
    for m in re.finditer(r'(20\d{2})[-/](20\d{2})', filename):
        y1, y2 = int(m.group(1)), int(m.group(2))
        if y2 == y1 + 1:
            return f"{y1}-{y2}"
    # School-year YYYY-YY (must be last two digits of year+1)
    for m in re.finditer(r'(20\d{2})[-/](\d{2})(?!\d)', filename):
        y1, suffix = int(m.group(1)), int(m.group(2))
        if suffix == (y1 + 1) % 100:
            return f"{y1}-{m.group(2)}"
    # Single 4-digit year, with non-digit boundary so "2026071" doesn't match
    m = re.search(r'(?<!\d)(20\d{2})(?!\d)', filename)
    if m:
        return m.group(1)
    return None
