"""
Structured query parser.

Pulls filter-like signals out of a natural-language query so the user can
type "2024 budget" or "school audit > $500k" and have the obvious filters
applied automatically instead of having to operate the dropdowns.

  Input:   "school audit 2024 over $50k"
  Output:  ParsedQuery(
              stripped="audit",
              fiscal_year="2024",
              category="school",
              min_amount=50_000,
              max_amount=None,
              hits=["2024", "school", "> $50k"],
           )

Conservative: only strips a token from the query if we're confident it's a
filter signal. Anything ambiguous stays in the FTS query so the user's
phrasing still influences ranking.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

# Recognize 4-digit years 1990–2099. We don't bother with 2-digit years —
# too ambiguous in a small corpus.
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
# School-year ranges: 2023-24 / 2023-2024 / FY23-24
_SCHOOL_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\s*[-/–]\s*(\d{2,4})\b")
# Fiscal-year prefix: FY2024, FY 2024, fy24
_FY_PREFIX_RE = re.compile(r"\bfy\s*((?:19|20)?\d{2})\b", re.IGNORECASE)

# Dollar amounts:
#   $100, $1,000, $1.5M, $500k, 1.2 million, 500 thousand
_DOLLAR_RE = re.compile(
    r"""
    (?:^|[\s(])                                # word boundary (not inside other token)
    (?P<op>over|under|above|below|more\ than|less\ than|>=?|<=?|≥|≤)? \s*
    \$? \s*
    (?P<num>\d{1,3}(?:[,.\s]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*
    (?P<mag>k|m|b|thousand|million|billion)?
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Category hints — only when the token appears in a clear "of/about/for X"
# position or as a standalone word. We avoid grabbing "school" if it's
# part of "high school" since a high-school doc might still be borough-related.
def _category_patterns() -> list:
    from municipality import get_municipality
    muni = get_municipality()

    def alt(words):
        return "|".join(re.escape(w.lower()).replace(r"\ ", r"\s+") for w in words if w)

    school = [a for e in muni.entities if e.kind == "school" for a in (e.short, *e.aliases)]
    town = [muni.short_name, muni.government_type, *muni.primary_entity.aliases]
    return [
        (re.compile(rf"\b(school|district|board\s+of\s+education|boe{'|' + alt(school) if school else ''})\b", re.IGNORECASE), "school"),
        (re.compile(rf"\b(town|city|village|borough|township|municipal|council|{alt(town)})\b", re.IGNORECASE), "town"),
    ]


_CATEGORY_PATTERNS = _category_patterns()

# Doc type hints — same conservative approach. Only fire on the unambiguous
# words; "report" alone is too vague.
_DOC_TYPE_PATTERNS = [
    (re.compile(r"\b(budget|budgets)\b", re.IGNORECASE), "budget"),
    (re.compile(r"\b(audit|audits|auditor)\b", re.IGNORECASE), "audit"),
    (re.compile(r"\b(minutes|meeting\s+minutes)\b", re.IGNORECASE), "minutes"),
    (re.compile(r"\b(agenda|agendas)\b", re.IGNORECASE), "agenda"),
    (re.compile(r"\b(ordinance|ordinances)\b", re.IGNORECASE), "ordinance"),
    (re.compile(r"\b(resolution|resolutions)\b", re.IGNORECASE), "resolution"),
    (re.compile(r"\b(financial\s+statement|financial\s+statements|cafr|acfr)\b", re.IGNORECASE), "financial_statement"),
]

# Department hints — fire when the user types a department by name or its
# common acronym. The canonical name on the right matches `_DEPT_CANONICAL`
# in routes/search.py + services/title_extractor.py so the filter actually
# joins against the values written by the backfill. Longest patterns first
# so "planning board" wins over "planning" alone (regex .search() picks
# whichever matches first in the list).
_DEPT_PATTERNS = [
    (re.compile(r"\b(planning\s+board|planning)\b", re.IGNORECASE), "Planning Board"),
    (re.compile(r"\b(zoning\s+board\s+of\s+adjustment|zoning\s+board|zba|zoning)\b", re.IGNORECASE), "Zoning Board"),
    (re.compile(r"\b(board\s+of\s+education|school\s+board|boe)\b", re.IGNORECASE), "Board of Education"),
    (re.compile(r"\b(borough\s+council|town\s+council|mayor\s+and\s+council|mayor\s*&\s*council)\b", re.IGNORECASE), "Borough Council"),
    (re.compile(r"\b(harbor\s+commission|harbor)\b", re.IGNORECASE), "Harbor Commission"),
    (re.compile(r"\b(shade\s+tree\s+commission|shade\s+tree)\b", re.IGNORECASE), "Shade Tree Commission"),
    (re.compile(r"\b(environmental\s+commission)\b", re.IGNORECASE), "Environmental Commission"),
    (re.compile(r"\b(historic\s+preservation\s+commission|historic\s+preservation)\b", re.IGNORECASE), "Historic Preservation Commission"),
    (re.compile(r"\b(public\s+works|dpw)\b", re.IGNORECASE), "Public Works"),
    (re.compile(r"\b(police\s+department|police|ahpd)\b", re.IGNORECASE), "Police"),
    (re.compile(r"\b(fire\s+department|fire\s+company|ahvfd|fire)\b", re.IGNORECASE), "Fire"),
    (re.compile(r"\b(first\s+aid|ems|emergency\s+medical|ambulance)\b", re.IGNORECASE), "EMS"),
    (re.compile(r"\b(municipal\s+court|court)\b", re.IGNORECASE), "Municipal Court"),
    (re.compile(r"\b(tax\s+assessor|assessor)\b", re.IGNORECASE), "Tax Assessor"),
    (re.compile(r"\b(tax\s+collector|collector)\b", re.IGNORECASE), "Tax Collector"),
    (re.compile(r"\b(building\s+department|construction\s+office|code\s+enforcement)\b", re.IGNORECASE), "Building Department"),
    (re.compile(r"\b(recreation\s+department|recreation)\b", re.IGNORECASE), "Recreation"),
    (re.compile(r"\b(borough\s+clerk|municipal\s+clerk|clerk)\b", re.IGNORECASE), "Borough Clerk"),
]


@dataclass
class ParsedQuery:
    """Result of parsing a natural-language query.

    `stripped` is the residual after removing filter-signal tokens — pass this
    to the FTS engine. `hits` lists what was extracted, for UI display as
    "applied filters" chips.
    """
    original: str
    stripped: str
    fiscal_year: Optional[str] = None
    category: Optional[str] = None
    doc_type: Optional[str] = None
    department: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    hits: list[str] = field(default_factory=list)

    def to_filters_dict(self) -> dict:
        """Return only the populated filters as a plain dict — for storing
        on search_query_log.parsed_filters or returning to the frontend."""
        out: dict = {}
        if self.fiscal_year:
            out["fiscal_year"] = self.fiscal_year
        if self.category:
            out["category"] = self.category
        if self.doc_type:
            out["doc_type"] = self.doc_type
        if self.department:
            out["department"] = self.department
        if self.min_amount is not None:
            out["min_amount"] = self.min_amount
        if self.max_amount is not None:
            out["max_amount"] = self.max_amount
        return out


def _parse_amount(num_str: str, mag: Optional[str]) -> Optional[float]:
    try:
        cleaned = num_str.replace(",", "").replace(" ", "")
        n = float(cleaned)
    except (ValueError, TypeError):
        return None
    if mag:
        mag = mag.lower()
        if mag in ("k", "thousand"):
            n *= 1_000
        elif mag in ("m", "million"):
            n *= 1_000_000
        elif mag in ("b", "billion"):
            n *= 1_000_000_000
    return n


def parse(query: str) -> ParsedQuery:
    """Extract structured filters from a natural-language query.

    Idempotent and pure — same input always returns same output. No DB
    queries, no network. Safe to call in a hot path.
    """
    if not query:
        return ParsedQuery(original="", stripped="")

    residual = query
    hits: list[str] = []
    fy: Optional[str] = None
    category: Optional[str] = None
    doc_type: Optional[str] = None
    min_amt: Optional[float] = None
    max_amt: Optional[float] = None

    # 1. Fiscal year — prefix form (FY2024) takes precedence over bare year
    m = _FY_PREFIX_RE.search(residual)
    if m:
        yr = m.group(1)
        if len(yr) == 2:
            yr = ("20" if int(yr) < 70 else "19") + yr
        fy = yr
        hits.append(f"FY {fy}")
        residual = _FY_PREFIX_RE.sub(" ", residual, count=1)

    # School-year range
    if fy is None:
        m = _SCHOOL_YEAR_RE.search(residual)
        if m:
            y1 = int(m.group(1))
            y2_raw = m.group(2)
            y2 = int(y2_raw)
            if len(y2_raw) == 2:
                if y2 == (y1 + 1) % 100:
                    fy = f"{y1}-{y2_raw}"
                    hits.append(f"FY {fy}")
                    residual = _SCHOOL_YEAR_RE.sub(" ", residual, count=1)
            elif y2 == y1 + 1:
                fy = f"{y1}-{y2}"
                hits.append(f"FY {fy}")
                residual = _SCHOOL_YEAR_RE.sub(" ", residual, count=1)

    # Bare 4-digit year
    if fy is None:
        m = _YEAR_RE.search(residual)
        if m:
            fy = m.group(1)
            hits.append(f"FY {fy}")
            # Strip the year so it doesn't double-count in FTS (the
            # fiscal_year filter already constrains the result set).
            residual = _YEAR_RE.sub(" ", residual, count=1)

    # 2. Dollar amounts. Only the first one — multi-bound queries (like
    # "between $50k and $100k") are rare; treat the first as a lower bound
    # if preceded by "over"/">", an upper bound if preceded by "under"/"<",
    # or as a neutral mention (no filter) otherwise so we don't lock the
    # user out of plain "$50k" searches that are looking for that number.
    m = _DOLLAR_RE.search(residual)
    if m:
        amount = _parse_amount(m.group("num"), m.group("mag"))
        op = (m.group("op") or "").lower().strip()
        if amount is not None and op:
            if op in ("over", "above", "more than", ">", ">="):
                min_amt = amount
                hits.append(f"> ${_fmt_amount(amount)}")
                residual = residual.replace(m.group(0), " ", 1)
            elif op in ("under", "below", "less than", "<", "<="):
                max_amt = amount
                hits.append(f"< ${_fmt_amount(amount)}")
                residual = residual.replace(m.group(0), " ", 1)

    # 3. Category hint — only mark category if we find ONE side. If both
    # "school" and "town" appear, the user explicitly wants both, so leave
    # the filter off.
    cat_matches: list[str] = []
    for pattern, cat in _CATEGORY_PATTERNS:
        if pattern.search(residual):
            cat_matches.append(cat)
    if len(set(cat_matches)) == 1:
        category = cat_matches[0]
        hits.append(category.capitalize())
        # Don't strip the category word — it can still be a useful FTS signal
        # (e.g. "town hall renovation" — keeping "town" in the FTS query
        # helps rank docs mentioning Town Hall above generic town docs).

    # 4. Doc type hint
    for pattern, dt in _DOC_TYPE_PATTERNS:
        if pattern.search(residual):
            doc_type = dt
            hits.append(dt.replace("_", " "))
            break  # first match wins — these are mutually exclusive

    # 5. Department hint. Longest-first ordering in _DEPT_PATTERNS already
    # ensures "planning board" beats "planning". Don't strip the matched
    # phrase from the residual — it's still useful as an FTS signal AND
    # makes the snippet more readable.
    department: Optional[str] = None
    for pattern, dept in _DEPT_PATTERNS:
        if pattern.search(residual):
            department = dept
            hits.append(dept)
            break  # one dept per query — multi-dept queries are rare

    # Clean up the residual: collapse whitespace
    stripped = re.sub(r"\s+", " ", residual).strip()
    # If we stripped everything (e.g. user typed just "2024"), keep the
    # original so the FTS query isn't empty — fiscal_year alone returns
    # too many results, and the residual will still rank well.
    if not stripped:
        stripped = query.strip()

    return ParsedQuery(
        original=query,
        stripped=stripped,
        fiscal_year=fy,
        category=category,
        doc_type=doc_type,
        department=department,
        min_amount=min_amt,
        max_amount=max_amt,
        hits=hits,
    )


def _fmt_amount(n: float) -> str:
    """Compact-format an amount for the UI chip — '50k', '1.5M', etc."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B".rstrip("0").rstrip(".")
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".rstrip("0").rstrip(".")
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return f"{n:.0f}"
