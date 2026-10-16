"""
Document title + department + date extraction.

Many docs in the corpus have unhelpful filenames — CMS hashes like
`753231002.pdf.pdf` and `hboroughnj-meet-6d347ce73f0...pdf` — that tell
the user nothing about what the document is. This module derives a
human-readable title (and a date and department when we can) from the
best signal available, in order of preference:

  1. AI-extracted metadata (`metadata_['title']`, `metadata_['summary']`)
     — Gemini already produced these during document analysis.
  2. Filename pattern parsing — for the `Departments - X - Y - Z.pdf` and
     `Agendas - Archives - YYYY - Month DD, Description.pdf` formats the
     AH scraper uses, the filename itself encodes title + department.
  3. First significant line of `extracted_text` — most municipal docs
     open with the title (e.g., "PLANNING BOARD MEETING MINUTES"). Skip
     filler like "Meeting came to order", page numbers, blank seal text.
  4. Cleaned filename — last-resort fallback.

For dates: prefer `metadata_['date_references'][0]` (AI), then a date
parsed out of the title/first lines, then None.

All functions are pure and synchronous — safe to call in a hot path.
"""
import logging
import re
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# Lines we skip when scanning OCR text for a title. These are common boilerplate
# in municipal documents — meeting-call language, page numbers, seal stamps.
_SKIP_LINE = re.compile(
    r"""^(
        \s*$                                    # blank
      | \s*page\s+\d+\s*(of\s+\d+)?\s*$         # page X / page X of Y
      | \s*\d{1,4}\s*$                          # bare numbers
      | \s*-+\s*$                               # dividers
      | \s*the\s+borough\s+of.*\d{4}\s*$        # seal text
      | \s*meeting\s+came\s+to\s+order.*$       # boilerplate
      | \s*roll\s+call\s*$                      # boilerplate
      | \s*pledge\s+of\s+allegiance\s*$         # boilerplate
      | \s*meeting\s+statement\s*:.*$           # boilerplate
      | \s*present\s*:.*$                       # roll call
      | \s*absent\s*:.*$                        # roll call
      | \s*inc\.?\s*\d{4}\s*$                   # seal year
      | https?://\S+\s*$                        # URLs
      | \S+@\S+\s*$                             # emails
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

# Lines that look like dates — we extract the date but don't use as the title.
_DATE_LINE = re.compile(
    r"^\s*("
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{1,2}(?:,)?\s*\d{4}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")\s*[-–—,]?\s*(meeting minutes|meeting agenda)?\s*$",
    re.IGNORECASE,
)

# Patterns for parsing a date string into ISO YYYY-MM-DD.
_DATE_FORMATS = [
    "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
    "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y",
    "%Y-%m-%d",
]


def _parse_iso_date(s: str) -> Optional[str]:
    """Try to coerce a date string into ISO YYYY-MM-DD."""
    s = s.strip().rstrip(",.")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except (ValueError, TypeError):
            continue
    return None


# Filename patterns the AH scraper uses. Group captures the parts we want.
#
#   Departments - Planning Board - Agendas and Minutes - {actual title}.pdf
#   Departments - Shade Tree Commission - Minutes - September 2022.pdf
#
# We split on ` - ` and treat:
#   parts[1]  → department (canonical name from canon map)
#   parts[2]  → series label (skip; usually "Agendas and Minutes" / "Minutes")
#   parts[3+] → actual title
_DEPARTMENTS_RE = re.compile(r"^Departments\s+-\s+", re.IGNORECASE)

#   Agendas - Archives - 2009 - December 23, Regular Meeting Agenda.pdf
#   Minutes - Archives - 2013 - February 27, Regular Meeting Minutes.pdf
_ARCHIVES_RE = re.compile(r"^(Agendas|Minutes)\s+-\s+Archives\s+-\s+(\d{4})\s+-\s+", re.IGNORECASE)

# Body-specific filename prefixes (the AH scraper sometimes outputs files
# like "PB_Meeting_Minutes_May_1__2025.pdf" or "February_6__2025_PB_Agenda.pdf").
# The acronym can be at the start OR embedded between underscores anywhere
# in the name, so we look for it as a free-standing token.
#
# Order matters: longer-prefix matches (AHBOE) win over shorter (BOE).
_PREFIX_PATTERNS = [
    (re.compile(r"(?:^|[_\s\-])ahboe(?=[_\s\-])", re.IGNORECASE), "Board of Education"),
    (re.compile(r"(?:^|[_\s\-])zba(?=[_\s\-])", re.IGNORECASE),   "Zoning Board"),
    (re.compile(r"(?:^|[_\s\-])boe(?=[_\s\-])", re.IGNORECASE),   "Board of Education"),
    (re.compile(r"(?:^|[_\s\-])pb(?=[_\s\-])", re.IGNORECASE),    "Planning Board"),
    (re.compile(r"(?:^|[_\s\-])zb(?=[_\s\-])", re.IGNORECASE),    "Zoning Board"),
    (re.compile(r"(?:^|[_\s\-])bbc(?=[_\s\-])", re.IGNORECASE),   "Borough Council"),
    (re.compile(r"(?:^|[_\s\-])stc(?=[_\s\-])", re.IGNORECASE),   "Shade Tree Commission"),
    (re.compile(r"(?:^|[_\s\-])hpc(?=[_\s\-])", re.IGNORECASE),   "Historic Preservation Commission"),
    (re.compile(r"(?:^|[_\s\-])ec(?=[_\s\-])", re.IGNORECASE),    "Environmental Commission"),
]

# Dates inside filenames. `\b` treats underscore as a word character so
# `2024_03_06` has no real boundaries; use explicit non-letter/non-digit
# anchors. Use `[^a-zA-Z]` (not `[^\w]`) so underscore counts as a separator.
_FN_DATE_PATTERNS = [
    # Month name DD YYYY
    re.compile(r"(?:^|[^a-zA-Z])(january|february|march|april|may|june|july|august|september|october|november|december|"
               r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)[_\s]+(\d{1,2})[_,\s]+(\d{4})(?=[^\d]|$)", re.IGNORECASE),
    # YYYY_MM_DD / YYYY-MM-DD
    re.compile(r"(?:^|[^\d])(\d{4})[_\-](\d{1,2})[_\-](\d{1,2})(?=[^\d]|$)"),
    # MM-DD-YYYY (e.g. 3-19-2019)
    re.compile(r"(?:^|[^\d])(\d{1,2})[_\-](\d{1,2})[_\-](\d{4})(?=[^\d]|$)"),
]

_MONTH_TO_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _extract_date_from_filename(filename: str) -> Optional[str]:
    """Look for any of the common in-filename date encodings. Returns ISO."""
    name = re.sub(r"\.(pdf|pptx?|docx?|xlsx?|xls)$", "", filename, flags=re.IGNORECASE)
    # Month name pattern first (most unambiguous)
    m = _FN_DATE_PATTERNS[0].search(name)
    if m:
        month = _MONTH_TO_NUM.get(m.group(1).lower())
        if month:
            try:
                return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"
            except (ValueError, TypeError):
                pass
    # YYYY-MM-DD style
    m = _FN_DATE_PATTERNS[1].search(name)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1900 < y < 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
        except (ValueError, TypeError):
            pass
    # MM-DD-YYYY style — only fire if YYYY-MM-DD didn't (to avoid mis-parsing
    # the leading year of "2025_03_06" as a month).
    m = _FN_DATE_PATTERNS[2].search(name)
    if m:
        try:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1900 < y < 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
        except (ValueError, TypeError):
            pass
    return None


def _humanize_filename_stem(stem: str) -> str:
    """Strip body acronyms (PB/ZB/etc.) and underscores from a filename stem.
    'PB_Meeting_Minutes_May_1__2025'      → 'Meeting Minutes May 1 2025'
    'February_6__2025_PB_Agenda'          → 'February 6 2025 Agenda'
    """
    s = stem
    for pat, _ in _PREFIX_PATTERNS:
        # Replace the acronym (including its leading separator) with a
        # single underscore so the subsequent collapse leaves clean spaces.
        s = pat.sub("_", s, count=1)
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Canonical department names — same map as routes/search.py but defined here too
# so the extractor doesn't depend on the route module. Keep in sync.
_DEPT_CANONICAL: dict[str, str] = {
    "police": "Police", "police department": "Police", "pd": "Police", "ahpd": "Police",
    "public works": "Public Works", "dpw": "Public Works",
    "fire": "Fire", "fire department": "Fire", "fire company": "Fire", "ahvfd": "Fire",
    "ems": "EMS", "first aid": "EMS", "first aid squad": "EMS", "ambulance": "EMS",
    "borough council": "Borough Council", "town council": "Borough Council",
    "mayor and council": "Borough Council", "council": "Borough Council",
    "board of education": "Board of Education", "boe": "Board of Education",
    "school board": "Board of Education",
    "planning board": "Planning Board", "planning": "Planning Board",
    "zoning board": "Zoning Board", "zba": "Zoning Board",
    "zoning board of adjustment": "Zoning Board",
    "harbor commission": "Harbor Commission", "harbor": "Harbor Commission",
    "shade tree commission": "Shade Tree Commission", "shade tree": "Shade Tree Commission",
    "environmental commission": "Environmental Commission",
    "historic preservation commission": "Historic Preservation Commission",
    "finance": "Finance", "finance department": "Finance",
    "borough clerk": "Borough Clerk", "clerk": "Borough Clerk", "municipal clerk": "Borough Clerk",
    "tax assessor": "Tax Assessor", "assessor": "Tax Assessor",
    "tax collector": "Tax Collector", "collector": "Tax Collector",
    "building department": "Building Department",
    "recreation": "Recreation", "recreation department": "Recreation",
    "municipal court": "Municipal Court", "court": "Municipal Court",
}


def _canonical_dept(name: str) -> Optional[str]:
    if not name:
        return None
    return _DEPT_CANONICAL.get(name.strip().casefold())


def _clean_filename_for_display(filename: str) -> str:
    """Last-resort title: strip extensions, normalize separators, title-case."""
    s = filename
    # Strip extensions, including doubled ones like `.pdf.pdf`
    while True:
        new = re.sub(r"\.(pdf|pptx?|docx?|xlsx?|xls|csv|txt)$", "", s, flags=re.IGNORECASE)
        if new == s:
            break
        s = new
    s = re.sub(r"[_-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def derive_from_filename(filename: str) -> dict:
    """Extract structured info from filename patterns the AH scraper uses.

    Returns a dict that may include:
      - title (str): a human-readable title
      - department (str): canonical department name
      - doc_date (str): ISO date if present in the filename
    Empty dict if no pattern matched.
    """
    if not filename:
        return {}

    out: dict = {}

    # 1. Departments - {Dept} - {Series} - {Title}.pdf
    if _DEPARTMENTS_RE.match(filename):
        parts = [p.strip() for p in re.sub(r"\.(pdf|pptx?|docx?|xlsx?|xls)$", "", filename, flags=re.IGNORECASE).split(" - ")]
        # parts[0] = "Departments"; parts[1] = dept; parts[2] = series; parts[3..] = title
        if len(parts) >= 4:
            dept_canonical = _canonical_dept(parts[1])
            out["department"] = dept_canonical or parts[1]
            # Title: combine series + remainder so "Resolution PB23-13 Siclaire 52 Garfield Ave"
            # becomes "Planning Board — Resolution PB23-13 Siclaire 52 Garfield Ave"
            title_body = " ".join(parts[3:])
            out["title"] = f"{out['department']} — {title_body}"
        elif len(parts) == 3:
            # No series — "Departments - Dept - Title.pdf"
            dept_canonical = _canonical_dept(parts[1])
            out["department"] = dept_canonical or parts[1]
            out["title"] = f"{out['department']} — {parts[2]}"
        return out

    # 2. Agendas / Minutes - Archives - YYYY - {Month DD, Description}.pdf
    m = _ARCHIVES_RE.match(filename)
    if m:
        series = m.group(1).rstrip("s")  # "Agenda" or "Minute"
        year = m.group(2)
        remainder = re.sub(_ARCHIVES_RE, "", filename)
        remainder = re.sub(r"\.(pdf|pptx?|docx?|xlsx?|xls)$", "", remainder, flags=re.IGNORECASE)
        out["title"] = f"Borough Council {series} — {remainder.strip()} {year}"
        out["department"] = "Borough Council"
        # Try to pull date from remainder
        date_m = re.match(r"^([A-Za-z]+ \d{1,2})", remainder.strip())
        if date_m:
            iso = _parse_iso_date(f"{date_m.group(1)}, {year}")
            if iso:
                out["doc_date"] = iso
        return out

    # 3. Body-acronym style: PB_Meeting_Minutes_May_1__2025.pdf,
    #    February_6__2025_PB_Agenda.pdf, AHBOE_MINUTES_3-19-2019.pdf, etc.
    # The acronym can be anywhere in the filename as long as it's a
    # standalone token between separators.
    for pat, dept in _PREFIX_PATTERNS:
        if pat.search(filename):
            stem = re.sub(r"\.(pdf|pptx?|docx?|xlsx?|xls)$", "", filename, flags=re.IGNORECASE)
            body = _humanize_filename_stem(stem)
            out["department"] = dept
            out["title"] = f"{dept} — {body}" if body else dept
            date_iso = _extract_date_from_filename(filename)
            if date_iso:
                out["doc_date"] = date_iso
            return out

    # 4. Fallback: try to extract just a date if there is one in the filename.
    # Department/title fall through to other heuristics.
    date_iso = _extract_date_from_filename(filename)
    if date_iso:
        out["doc_date"] = date_iso

    return out


# Markdown header / page-number prefixes emitted by pymupdf4llm. The extractor
# was treating "## Page 1 — PLANNING BOARD" as a title verbatim; strip the
# leading garbage so we get the actual heading text.
_MARKDOWN_HEADER_RE = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_PAGE_PREFIX_RE = re.compile(r"^\s*Page\s+\d+\s*[-–—:.]?\s*", re.IGNORECASE)
_LEADING_NOISE_RE = re.compile(r"^[\s\*\-#_=•·]+")


def _clean_text_line(line: str) -> str:
    """Strip markdown formatting, page-number prefixes, and leading noise.
    Returns the bare heading text."""
    s = _MARKDOWN_HEADER_RE.sub("", line)
    s = _PAGE_PREFIX_RE.sub("", s)
    s = _LEADING_NOISE_RE.sub("", s)
    # Trim trailing punctuation/decoration
    s = re.sub(r"[\*_=]+$", "", s).strip()
    return s


def derive_from_text(extracted_text: Optional[str]) -> dict:
    """Pick the first significant line(s) of OCR'd text as the title.
    Returns {title, doc_date} when a useful candidate is found."""
    if not extracted_text:
        return {}
    head = extracted_text[:2000]
    lines = [_clean_text_line(l) for l in head.splitlines()]

    out: dict = {}

    # First pass: find a date line and remember it
    for line in lines:
        if not line:
            continue
        m = _DATE_LINE.match(line)
        if m:
            iso = _parse_iso_date(m.group(1))
            if iso and "doc_date" not in out:
                out["doc_date"] = iso
            break

    # Second pass: pick first non-skip non-date line that's "title-like"
    title_lines: list[str] = []
    for line in lines:
        if not line or _SKIP_LINE.match(line) or _DATE_LINE.match(line):
            continue
        # Skip very long lines (likely body text, not a title)
        if len(line) > 200:
            continue
        # Skip lines that look like body sentences (lots of lowercase prose)
        # The rule: at least 30% of chars must be uppercase OR the line must
        # be < 80 chars. Most municipal doc titles are ALL CAPS or short.
        upper_ratio = sum(1 for c in line if c.isupper()) / max(1, sum(1 for c in line if c.isalpha()))
        if upper_ratio < 0.3 and len(line) > 80:
            continue
        title_lines.append(line)
        if len(title_lines) >= 2:
            break

    if title_lines:
        # Combine up to 2 lines for richer context (entity + body)
        combined = " — ".join(title_lines[:2])
        # Title-case the result if it's all caps (easier to read than SHOUT)
        if combined.isupper():
            combined = combined.title()
        out["title"] = combined[:200]  # column is VARCHAR; keep tight

    return out


def derive_from_metadata(metadata: Optional[dict]) -> dict:
    """Trust the AI's extraction when it produced anything. metadata is the
    Document.metadata_ JSONB dict populated by document_processor."""
    if not metadata or not isinstance(metadata, dict):
        return {}
    out: dict = {}
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()[:200]
    # date_references is a list of strings; first one is usually the doc's date
    date_refs = metadata.get("date_references")
    if isinstance(date_refs, list) and date_refs:
        for d in date_refs:
            if isinstance(d, str):
                iso = _parse_iso_date(d)
                if iso:
                    out["doc_date"] = iso
                    break
    return out


def derive(
    filename: str,
    extracted_text: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Run all heuristics and return a merged dict.

    Precedence (winner takes the field):
      title:      filename pattern > metadata > content > cleaned filename
      department: filename pattern > metadata  (content is unreliable here)
      doc_date:   metadata > filename pattern > content

    Filename patterns win for title because they're the most structured signal
    we have — the AH scraper encodes department/title directly in the path.
    """
    from_fn = derive_from_filename(filename)
    from_md = derive_from_metadata(metadata)
    from_tx = derive_from_text(extracted_text)

    title = (
        from_fn.get("title")
        or from_md.get("title")
        or from_tx.get("title")
        or _clean_filename_for_display(filename)
        or None
    )
    department = from_fn.get("department") or (
        _canonical_dept(from_md.get("department", "")) if from_md.get("department") else None
    )
    doc_date = from_md.get("doc_date") or from_fn.get("doc_date") or from_tx.get("doc_date")

    out = {"title": title}
    if department:
        out["department"] = department
    if doc_date:
        out["doc_date"] = doc_date
    return out
