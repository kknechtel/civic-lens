"""
Smarter document search.

Pipeline:
  1. Parse query for structured signals (year, $ range, doc type, category)
     → applied as filters automatically; user sees them as chips
  2. Expand query with civic synonyms (static dict + optional Gemini paraphrase)
  3. Hybrid retrieval (semantic embedding + keyword tsvector) at the chunk
     level — every page of every document is searchable, not just the first
     50 KB of `extracted_text`
  4. Rerank top 30 with Voyage rerank-2 (when key available)
  5. Recency boost — newer fiscal years rank higher with light decay
  6. Literal-match dedup (preserves the old stemming-collision fix:
     "highander" no longer drags in 685 docs that just contain "Highlands")
  7. Roll up chunks → documents: one row per doc, with the top 3 chunks as
     additional snippets, including page numbers so the UI can deep-link
  8. If result_count < 3, fetch a did-you-mean suggestion via pg_trgm
  9. Log every query (and clicks via /click) to search_query_log for analytics

Facets endpoint also got tighter: years are clamped to a plausible range,
department names are canonicalized via a synonym map so "Police" /
"Police Department" / "PD" all collapse to one entry.
"""
import logging
import math
import re
import time
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models.document import Document
from models.user import User
from services import query_expansion, query_parser, reranker
from services.rag_search import search_chunks

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Models ────────────────────────────────────────────────────────────────

class SearchResult(BaseModel):
    id: str
    filename: str
    title: str | None = None
    doc_date: str | None = None  # ISO YYYY-MM-DD when extractable
    # AI-generated one-paragraph summary (lives in documents.notes). Shown
    # below the title so users get a feel for the document at a glance,
    # separate from the search-snippet which shows the matching excerpt.
    summary: str | None = None
    doc_type: str | None
    category: str | None
    fiscal_year: str | None
    department: str | None = None
    status: str
    score: float
    snippet: str | None
    match_type: str  # "phrase" | "fts" | "hybrid" | "filename"
    additional_snippets: list[str] | None = None
    pages: list[int] | None = None
    match_count: int | None = None


class SearchRequest(BaseModel):
    query: str
    project_id: str | None = None
    category: str | None = None
    doc_type: str | None = None
    fiscal_year: str | None = None
    department: str | None = None
    municipality: str | None = None  # an Entity.key from the municipality profile
    document_id: str | None = None  # in-doc search
    limit: int = 30
    # Power-user escape hatches. Default off — leave them off unless you're
    # debugging or A/B'ing.
    disable_expansion: bool = False
    disable_rerank: bool = False


class ParsedFilters(BaseModel):
    fiscal_year: str | None = None
    category: str | None = None
    doc_type: str | None = None
    department: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    hits: list[str] = []


class SearchResponse(BaseModel):
    results: list[SearchResult]
    did_you_mean: str | None = None
    parsed_filters: ParsedFilters | None = None
    query_id: str | None = None
    latency_ms: int | None = None


class ClickRequest(BaseModel):
    query_id: str
    document_id: str


# ─── Helpers ────────────────────────────────────────────────────────────────

# Same MARK delimiters ts_headline uses, so the existing frontend highlighter
# keeps working without changes.
_MARK_START = "<<MARK>>"
_MARK_END = "<</MARK>>"


def _wrap_literal(window: str, literal: str) -> str:
    if not window or not literal:
        return window or ""
    pattern = re.compile(re.escape(literal), re.IGNORECASE)
    return pattern.sub(lambda m: f"{_MARK_START}{m.group(0)}{_MARK_END}", window)


def _make_snippet(content: str, literal: str, window: int = 300) -> str:
    if not content:
        return ""
    if literal:
        pos = content.lower().find(literal.lower())
        if pos >= 0:
            start = max(0, pos - window // 3)
            end = min(len(content), pos + len(literal) + (window * 2 // 3))
            slice_ = content[start:end]
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(content) else ""
            return prefix + _wrap_literal(slice_, literal) + suffix
    return content[:window].strip() + ("…" if len(content) > window else "")


def _strip_operators(query: str) -> str:
    return re.sub(r'["\-+()]', "", query).strip()


def _recency_boost(fiscal_year: Optional[str], created_at: Optional[datetime]) -> float:
    """Mild recency boost in [0, 0.2], decays over ~5 yrs. Prefers fiscal_year
    over created_at (upload time is not substantive recency)."""
    now_year = datetime.utcnow().year
    target: Optional[int] = None

    if fiscal_year:
        m = re.search(r"(19|20)\d{2}", fiscal_year)
        if m:
            target = int(m.group(0))
    if target is None and created_at:
        target = created_at.year

    if target is None:
        return 0.0
    age = max(0, now_year - target)
    return 0.2 * math.exp(-age / 5.0)


def _resolve_filters(req: SearchRequest, parsed: query_parser.ParsedQuery) -> dict:
    """Merge explicit filters (UI dropdowns) with parsed signals (query text).
    Explicit filters always win — the dropdown is more intentional."""
    return {
        "fiscal_year": req.fiscal_year or parsed.fiscal_year,
        "category": req.category or parsed.category,
        "doc_type": req.doc_type or parsed.doc_type,
        "department": req.department,  # inferred departments only boost (see rank_chunks)
        "municipality": req.municipality,
        "project_id": req.project_id,
    }


def _did_you_mean(db: Session, query: str) -> Optional[str]:
    """pg_trgm similarity against filename word corpus. Returns None on no
    pg_trgm extension, no candidates ≥ 0.4 similarity, or no real change."""
    cleaned = _strip_operators(query).strip()
    if not cleaned or len(cleaned) < 4:
        return None
    try:
        tokens = [t for t in re.split(r"\W+", cleaned.lower()) if len(t) >= 4]
        if not tokens:
            return None

        suggestions: list[str] = []
        any_change = False
        for tok in tokens:
            row = db.execute(sql_text("""
                SELECT word, similarity(word, :tok) AS sim
                FROM (
                    SELECT DISTINCT lower(unnest(string_to_array(
                        regexp_replace(filename, '[^a-zA-Z0-9 ]', ' ', 'g'),
                        ' '
                    ))) AS word
                    FROM documents
                    LIMIT 5000
                ) corpus
                WHERE length(word) >= 4
                  AND word % :tok
                ORDER BY similarity(word, :tok) DESC
                LIMIT 1
            """), {"tok": tok}).fetchone()
            if row and float(row.sim) >= 0.4 and row.word != tok:
                suggestions.append(row.word)
                any_change = True
            else:
                suggestions.append(tok)
        if not any_change:
            return None
        return " ".join(suggestions)
    except Exception as exc:
        logger.debug("did_you_mean skipped: %s", exc)
        return None


def _log_search(
    db: Session, user_id, query: str, normalized: str,
    parsed_filters: dict, result_count: int, latency_ms: int,
) -> Optional[str]:
    """Best-effort insert into search_query_log. Returns query_id or None."""
    try:
        import json
        qid = str(uuid.uuid4())
        db.execute(sql_text("""
            INSERT INTO search_query_log
                (id, query, normalized_query, parsed_filters,
                 result_count, user_id, latency_ms)
            VALUES (CAST(:id AS uuid), :q, :nq, CAST(:pf AS jsonb),
                    :rc, CAST(:uid AS uuid), :lm)
        """), {
            "id": qid,
            "q": query[:2000],
            "nq": normalized[:2000],
            "pf": json.dumps(parsed_filters),
            "rc": result_count,
            "uid": str(user_id) if user_id else None,
            "lm": latency_ms,
        })
        db.commit()
        return qid
    except Exception as exc:
        logger.debug("search log insert failed: %s", exc)
        db.rollback()
        return None


# ─── Search endpoint ───────────────────────────────────────────────────────

def rank_chunks(
    db: Session,
    query: str,
    *,
    fiscal_year: Optional[str] = None,
    category: Optional[str] = None,
    doc_type: Optional[str] = None,
    department: Optional[str] = None,
    municipality: Optional[str] = None,
    project_id: Optional[str] = None,
    document_id: Optional[str] = None,
    pool_size: int = 30,
    expand: bool = True,
    rerank: bool = True,
):
    """The full ranking pipeline shared by /api/search and the chat tools:
    parse → expand → hybrid RRF retrieval → Voyage rerank → recency/title/literal
    boosts. Returns (chunks sorted best-first, parsed, expansions, base_query)."""
    # ── 1. Parse for structured signals
    parsed = query_parser.parse(query)
    base_query = parsed.stripped or query
    merged = {
        "fiscal_year": fiscal_year or parsed.fiscal_year,
        "category": category or parsed.category,
        "doc_type": doc_type or parsed.doc_type,
        # Only an explicit department filters; one inferred from the query
        # text ("harbor" -> Harbor Commission) is a boost below, since many
        # docs carry the body in their title/header rather than department.
        "department": department,
    }

    # ── 2. Synonym expansion
    expansions: list[str] = []
    if expand:
        try:
            expansions = query_expansion.expand(base_query, allow_llm=True)
        except Exception as exc:
            logger.warning("query expansion failed: %s", exc)
            expansions = []
    fts_query = query_expansion.build_or_query(base_query, expansions)

    # ── 3. Hybrid chunk-level retrieval. Pull a pool 3x the limit so the
    # reranker has headroom.
    has_quotes = '"' in query
    try:
        chunks = search_chunks(
            db,
            query=fts_query,
            top_k=pool_size,
            fiscal_year=merged["fiscal_year"],
            category=merged["category"],
            doc_type=merged["doc_type"],
            document_id=document_id,
            project_id=project_id,
            department=merged["department"],
            municipality=municipality,
            use_websearch=True,
            # Embed the plain query; the OR-expanded string is keyword syntax.
            embed_text=base_query,
        )
    except Exception as exc:
        logger.warning("hybrid chunk search failed: %s", exc)
        chunks = []

    # ── 4. Voyage rerank-2 on top-30 (when key + enough results)
    if chunks and len(chunks) > 3 and rerank and reranker.is_available():
        try:
            # Rerank sees the chunk's doc header too (title · town · body · date).
            docs_for_rerank = [f"{c.get('context') or ''}\n{c.get('content') or ''}" for c in chunks]
            ranking = reranker.rerank(base_query, docs_for_rerank, top_k=min(30, len(chunks)))
            rerank_map = {idx: score for idx, score in ranking}
            for i, c in enumerate(chunks):
                if i in rerank_map:
                    c["score"] = 0.7 * rerank_map[i] + 0.3 * float(c.get("score") or 0)
                else:
                    c["score"] = 0.3 * float(c.get("score") or 0)
            chunks.sort(key=lambda r: r["score"], reverse=True)
        except Exception as exc:
            logger.warning("rerank failed, keeping hybrid order: %s", exc)

    # ── 5. Recency boost + title-match boost.
    #
    # Title boost is the bigger lever: a doc whose TITLE matches the user's
    # query is almost certainly what they want, far more than a doc that
    # just mentions the term in passing. +0.4 is roughly equivalent to
    # going from cosine 0.5 → 0.9, enough to leap over body-only mentions.
    q_literal = _strip_operators(query)
    for c in chunks:
        base = float(c.get("score") or 0)
        base += _recency_boost(c.get("fiscal_year"), c.get("created_at"))
        title = (c.get("title") or "")
        if q_literal and title and q_literal.lower() in title.lower():
            base += 0.4
        c["score"] = base
    chunks.sort(key=lambda r: r["score"], reverse=True)

    if parsed.department and not department:
        dept = parsed.department.lower()
        for c in chunks:
            hay = f"{c.get('department') or ''} {c.get('context') or ''} {c.get('title') or ''}".lower()
            if dept in hay:
                c["score"] = float(c.get("score") or 0) + 0.1
        chunks.sort(key=lambda r: r["score"], reverse=True)

    # ── 6. Literal-match boost (outranks stemmer over-match like "highander").
    # Title matches count too. A boost rather than a filter: dropping every
    # non-literal chunk threw away good semantic hits whenever one chunk
    # happened to contain the exact string.
    if q_literal and not has_quotes and chunks:
        for c in chunks:
            haystack = (c.get("content") or "") + " " + (c.get("title") or "")
            if q_literal.lower() in haystack.lower():
                c["score"] = float(c.get("score") or 0) + 0.15
        chunks.sort(key=lambda r: r["score"], reverse=True)

    return chunks, parsed, expansions, base_query


@router.post("", response_model=SearchResponse, include_in_schema=False)
@router.post("/", response_model=SearchResponse)
def search_documents(
    req: SearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    started = time.perf_counter()
    query = req.query.strip()

    if not query:
        return SearchResponse(results=[])

    chunks, parsed, expansions, base_query = rank_chunks(
        db, query,
        fiscal_year=req.fiscal_year, category=req.category, doc_type=req.doc_type,
        department=req.department, municipality=req.municipality,
        project_id=req.project_id, document_id=req.document_id,
        pool_size=max(req.limit * 3, 30),
        expand=not req.disable_expansion, rerank=not req.disable_rerank,
    )
    merged = _resolve_filters(req, parsed)
    has_quotes = '"' in query
    q_literal = _strip_operators(query)

    # ── 7. Roll up chunks → documents
    by_doc: dict[str, dict] = {}
    for c in chunks:
        doc_id = str(c["document_id"])
        chunk_snippet = _make_snippet(c.get("content") or "", q_literal)
        page = c.get("page_start")
        if doc_id not in by_doc:
            by_doc[doc_id] = {
                "row": c,
                "snippets": [chunk_snippet] if chunk_snippet else [],
                "pages": [page] if page else [],
                "best_score": float(c.get("score") or 0),
                "match_count": 1,
            }
        else:
            slot = by_doc[doc_id]
            if chunk_snippet and len(slot["snippets"]) < 3:
                slot["snippets"].append(chunk_snippet)
            if page and page not in slot["pages"]:
                slot["pages"].append(page)
            slot["match_count"] += 1

    docs_sorted = sorted(by_doc.values(), key=lambda d: d["best_score"], reverse=True)[:req.limit]

    results: list[SearchResult] = []
    for d in docs_sorted:
        r = d["row"]
        match_type = "phrase" if has_quotes else ("hybrid" if reranker.is_available() else "fts")
        # Truncate the AI summary to a reasonable display length — the full
        # notes field can be 1-2 paragraphs; the search row only needs the
        # gist. Detail view (GET /documents/{id}) still returns the full text.
        summary = r.get("notes")
        if summary and len(summary) > 240:
            summary = summary[:237].rstrip() + "…"
        results.append(SearchResult(
            id=str(r["document_id"]),
            filename=r.get("filename") or "",
            title=r.get("title"),
            doc_date=r.get("doc_date"),
            summary=summary,
            doc_type=r.get("doc_type"),
            category=r.get("category"),
            fiscal_year=r.get("fiscal_year"),
            department=r.get("department"),
            status=r.get("status") or "processed",
            score=round(float(d["best_score"]), 4),
            snippet=d["snippets"][0] if d["snippets"] else None,
            match_type=match_type,
            additional_snippets=d["snippets"][1:] if len(d["snippets"]) > 1 else None,
            pages=d["pages"] if d["pages"] else None,
            match_count=d["match_count"],
        ))

    # ── 8. Filename-ILIKE fallback when hybrid+chunk returns nothing AND
    # the user didn't quote (a quoted query that misses chunks should NOT
    # silently match an unrelated filename).
    if not results and not has_quotes:
        like = f"%{query}%"
        fallback_q = (
            db.query(Document)
            .filter(
                or_(
                    Document.filename.ilike(like),
                    Document.notes.ilike(like),
                    Document.doc_type.ilike(like),
                    Document.department.ilike(like),
                ),
                ~Document.filename.ilike("%.xlsx"),
                ~Document.filename.ilike("%.xls"),
                ~Document.filename.ilike("%.csv"),
                ~Document.filename.ilike("document_summaries%"),
            )
        )
        if merged["category"]:
            fallback_q = fallback_q.filter(Document.category == merged["category"])
        if merged["doc_type"]:
            fallback_q = fallback_q.filter(Document.doc_type == merged["doc_type"])
        if merged["fiscal_year"]:
            fallback_q = fallback_q.filter(Document.fiscal_year == merged["fiscal_year"])
        if merged["department"]:
            fallback_q = fallback_q.filter(Document.department == merged["department"])
        if req.project_id:
            fallback_q = fallback_q.filter(Document.project_id == req.project_id)

        docs = fallback_q.limit(req.limit).all()
        results = [
            SearchResult(
                id=str(d.id), filename=d.filename,
                title=d.title, doc_date=d.doc_date,
                summary=(d.notes[:237].rstrip() + "…") if (d.notes and len(d.notes) > 240) else d.notes,
                doc_type=d.doc_type,
                category=d.category, fiscal_year=d.fiscal_year, department=d.department,
                status=d.status, score=1.0,
                snippet=(d.notes[:200] if d.notes else None),
                match_type="filename",
            )
            for d in docs
        ]

    # ── 9. Did-you-mean — only when results are sparse and no quote
    suggestion: Optional[str] = None
    if len(results) < 3 and not has_quotes:
        suggestion = _did_you_mean(db, query)

    # ── 10. Log to analytics
    latency_ms = int((time.perf_counter() - started) * 1000)
    parsed_filters_dict = parsed.to_filters_dict()
    parsed_filters_dict["expansions"] = expansions
    parsed_filters_dict["reranker_used"] = bool(
        not req.disable_rerank and reranker.is_available() and len(chunks) > 3
    )
    query_id = _log_search(
        db, getattr(user, "id", None), query, base_query,
        parsed_filters_dict, len(results), latency_ms,
    )

    return SearchResponse(
        results=results,
        did_you_mean=suggestion,
        parsed_filters=ParsedFilters(
            fiscal_year=parsed.fiscal_year,
            category=parsed.category,
            doc_type=parsed.doc_type,
            department=parsed.department,
            min_amount=parsed.min_amount,
            max_amount=parsed.max_amount,
            hits=parsed.hits,
        ) if parsed.hits else None,
        query_id=query_id,
        latency_ms=latency_ms,
    )


# ─── Click tracking ────────────────────────────────────────────────────────

@router.post("/click")
def record_click(
    req: ClickRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a logged search query with the doc the user opened — the
    strongest relevance signal we have for tuning ranking later."""
    try:
        db.execute(sql_text("""
            UPDATE search_query_log
            SET clicked_document_id = CAST(:doc_id AS uuid)
            WHERE id = CAST(:qid AS uuid)
              AND (user_id IS NULL OR user_id = CAST(:uid AS uuid))
        """), {
            "qid": req.query_id,
            "doc_id": req.document_id,
            "uid": str(user.id),
        })
        db.commit()
        return {"ok": True}
    except Exception as exc:
        logger.debug("click record failed: %s", exc)
        db.rollback()
        return {"ok": False}


# ─── Facets ─────────────────────────────────────────────────────────────────

# Plausible fiscal-year range. Anything outside this is treated as OCR/regex
# noise (resolution numbers like "PB-2059-12", etc.) and dropped from the
# dropdown. The upper bound is fluid — `_year_window()` returns current+2.
_YEAR_RE_FOUR = re.compile(r"\b(19|20)\d{2}\b")


def _year_window() -> tuple[int, int]:
    """Return (min, max) plausible fiscal years. Cached implicitly per request."""
    cur = datetime.utcnow().year
    # Lower bound comes from the profile (most towns have no digital records
    # before ~1990). Allow a small grace window on the upper end so a
    # forward-dated budget (e.g. FY2027 introduced 2026-Q3) shows up.
    from municipality import get_municipality
    return get_municipality().search.earliest_year, cur + 2


def _normalize_year(raw: Optional[str]) -> Optional[str]:
    """Return a clean 'YYYY' or 'YYYY-YY(YY)' school year, or None.

    Validates:
      - School-year ranges only count when the second half is exactly y1+1.
        (Stops "2026-07" → fake range from a resolution number.)
      - Bare years must fall inside [1990, current_year + 2].
        (Stops "2099" / "1925" junk from inflating the dropdown.)
    """
    if not raw:
        return None
    s = str(raw).strip()
    y_min, y_max = _year_window()

    def _in_range(y: int) -> bool:
        return y_min <= y <= y_max

    # YYYY-YYYY school year (4-digit second half)
    m = re.match(r"^((?:19|20)\d{2})\s*[-–/]\s*((?:19|20)\d{2})$", s)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        if y2 == y1 + 1 and _in_range(y1):
            return f"{y1}-{y2}"

    # YYYY-YY school year (2-digit second half)
    m = re.match(r"^((?:19|20)\d{2})\s*[-–/]\s*(\d{2})$", s)
    if m:
        y1, suffix = int(m.group(1)), int(m.group(2))
        if suffix == (y1 + 1) % 100 and _in_range(y1):
            return f"{y1}-{m.group(2)}"

    # Bare 4-digit year somewhere in the string
    m = _YEAR_RE_FOUR.search(s)
    if m:
        y = int(m.group(0))
        if _in_range(y):
            return str(y)
    return None


# ── Department canonicalization ─────────────────────────────────────────
#
# The AI extracts department names from document text — same logical unit
# appears as "Police", "Police Department", "PD", "AHPD" in different docs.
# Without consolidation the dropdown becomes a wall of near-duplicates.
#
# The map is conservative: only entries where we're confident the variants
# refer to the same department. When in doubt, leave it alone — the
# casefold dedup below still collapses pure-casing variants.
_DEPT_CANONICAL: dict[str, str] = {
    # Police
    "police": "Police",
    "police department": "Police",
    "police dept": "Police",
    "pd": "Police",

    # Public works
    "public works": "Public Works",
    "dpw": "Public Works",
    "department of public works": "Public Works",
    "public works department": "Public Works",

    # Fire
    "fire": "Fire",
    "fire department": "Fire",
    "fire dept": "Fire",
    "fire company": "Fire",
    "fire co": "Fire",

    # EMS / First aid
    "ems": "EMS",
    "first aid": "EMS",
    "first aid squad": "EMS",
    "emergency medical services": "EMS",
    "ambulance": "EMS",

    # Governance
    "borough council": "Borough Council",
    "town council": "Borough Council",
    "mayor and council": "Borough Council",
    "council": "Borough Council",
    "mayor & council": "Borough Council",

    # School board
    "board of education": "Board of Education",
    "boe": "Board of Education",
    "school board": "Board of Education",

    # Planning
    "planning board": "Planning Board",
    "planning": "Planning Board",

    # Zoning
    "zoning board": "Zoning Board",
    "zba": "Zoning Board",
    "zoning board of adjustment": "Zoning Board",
    "zoning": "Zoning Board",

    # Other named boards / commissions
    "harbor commission": "Harbor Commission",
    "harbor": "Harbor Commission",
    "shade tree commission": "Shade Tree Commission",
    "shade tree": "Shade Tree Commission",
    "environmental commission": "Environmental Commission",
    "environmental": "Environmental Commission",
    "historic preservation commission": "Historic Preservation Commission",
    "historic preservation": "Historic Preservation Commission",

    # Finance / admin
    "finance": "Finance",
    "finance department": "Finance",
    "cfo": "Finance",
    "treasurer": "Finance",

    "borough clerk": "Borough Clerk",
    "clerk": "Borough Clerk",
    "municipal clerk": "Borough Clerk",

    "tax assessor": "Tax Assessor",
    "assessor": "Tax Assessor",

    "tax collector": "Tax Collector",
    "collector": "Tax Collector",

    "building department": "Building Department",
    "building dept": "Building Department",
    "construction": "Building Department",
    "construction office": "Building Department",
    "code enforcement": "Building Department",

    "recreation": "Recreation",
    "recreation department": "Recreation",
    "recreation dept": "Recreation",

    "court": "Municipal Court",
    "municipal court": "Municipal Court",

}


def _localize_dept_map() -> None:
    """Name the governing body/clerk after the government type ("City Council",
    "Village Clerk") and add the profile's own aliases (local PD acronyms,
    school names)."""
    from municipality import get_municipality
    muni = get_municipality()
    kind = muni.government_type.replace("_", " ").title()
    for k, v in list(_DEPT_CANONICAL.items()):
        if v == "Borough Council":
            _DEPT_CANONICAL[k] = f"{kind} Council"
        elif v == "Borough Clerk":
            _DEPT_CANONICAL[k] = f"{kind} Clerk"
    _DEPT_CANONICAL[f"{kind.lower()} council"] = f"{kind} Council"
    _DEPT_CANONICAL[f"{kind.lower()} clerk"] = f"{kind} Clerk"
    for alias, canonical in muni.search.department_aliases.items():
        _DEPT_CANONICAL[alias.casefold()] = canonical


_localize_dept_map()


def _normalize_dept(raw: Optional[str]) -> Optional[str]:
    """Canonicalize a department string. Returns the canonical name if the
    input maps to one, otherwise the input with whitespace collapsed."""
    if not raw:
        return None
    s = " ".join(raw.split())
    if not s:
        return None
    canonical = _DEPT_CANONICAL.get(s.casefold())
    return canonical if canonical else s


@router.get("/facets")
def get_facets(
    project_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get filter facets — deduped, normalized.

      doc_types:    raw enum values (controlled vocab — no normalization needed)
      categories:   raw (town/school/general)
      fiscal_years: clamped to [1990, current_year+2], YYYY-YY(YY) validated
      departments:  canonicalized via _DEPT_CANONICAL, then case-folded dedup
    """
    base = db.query(Document)
    if project_id:
        base = base.filter(Document.project_id == project_id)

    # Doc type / category — controlled enums, no normalization
    doc_types = base.with_entities(Document.doc_type, func.count(Document.id)).group_by(Document.doc_type).all()
    categories = base.with_entities(Document.category, func.count(Document.id)).group_by(Document.category).all()

    # Fiscal years — sanitize to plausible 4-digit years only, dedupe
    raw_years = base.with_entities(Document.fiscal_year, func.count(Document.id)).group_by(Document.fiscal_year).all()
    year_counts: dict[str, int] = {}
    for raw, count in raw_years:
        norm = _normalize_year(raw)
        if not norm:
            continue
        year_counts[norm] = year_counts.get(norm, 0) + int(count)

    # Departments — canonicalize, then case-insensitive dedup
    raw_depts = base.with_entities(Document.department, func.count(Document.id)).group_by(Document.department).all()
    dept_counts: dict[str, int] = {}
    for raw, count in raw_depts:
        norm = _normalize_dept(raw)
        if not norm:
            continue
        key = norm.casefold()
        dept_counts[key] = dept_counts.get(key, 0) + int(count)
    dept_display: dict[str, str] = {}
    for raw, _ in raw_depts:
        norm = _normalize_dept(raw)
        if not norm:
            continue
        key = norm.casefold()
        # Prefer the canonical capitalization from _DEPT_CANONICAL when we
        # canonicalized; fall back to the first occurrence's casing.
        if key not in dept_display:
            dept_display[key] = norm
    departments_out = {dept_display[k]: v for k, v in dept_counts.items()}

    return {
        "doc_types": {k or "unclassified": v for k, v in doc_types},
        "categories": {k or "uncategorized": v for k, v in categories},
        "fiscal_years": dict(sorted(year_counts.items(), reverse=True)),
        "departments": dict(sorted(departments_out.items(), key=lambda kv: -kv[1])),
    }


# ─── Admin: zero-result queries (find ingestion / vocab gaps) ──────

@router.get("/analytics/zero-results")
def zero_result_queries(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Top queries that returned no results in the last N days.

    Each row is either a missing document (need to scrape / re-OCR) or a
    vocabulary gap (add to query_expansion synonyms). Reading material
    for the librarian / data curator."""
    try:
        rows = db.execute(sql_text("""
            SELECT query, count(*) AS n, max(created_at) AS last_seen
            FROM search_query_log
            WHERE result_count = 0
              AND created_at > now() - (:days || ' days')::interval
            GROUP BY query
            ORDER BY n DESC, last_seen DESC
            LIMIT :lim
        """), {"days": days, "lim": limit}).fetchall()
        return {
            "days": days,
            "queries": [
                {"query": r.query, "count": int(r.n), "last_seen": r.last_seen.isoformat() if r.last_seen else None}
                for r in rows
            ],
        }
    except Exception as exc:
        logger.warning("zero-results analytics failed: %s", exc)
        return {"days": days, "queries": []}
