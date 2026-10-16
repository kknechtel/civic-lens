"""
Query expansion for municipal / civic search.

Two layers:

  1. Static synonym dictionary (instant, no network). Generic civic vocabulary
     here, plus the active municipality profile's `search.glossary` and its
     state's glossary — local vocabulary Postgres' English stemmer can't reach:
       - acronyms ("CAFR" → "comprehensive annual financial report")
       - civic jargon ("redev" → "redevelopment")
       - euphemisms ("trash fees" → "sanitation rates")
     Every key/value pair is normalized lowercase. Look-ups are case-insensitive.

  2. Optional LLM paraphrase via Gemini 2.5-flash. Triggered only when a short
     query (≤3 tokens) doesn't hit the dictionary — long natural-language
     queries don't usually need it. Returns 3 paraphrases. Failure is silent;
     the caller gets back just the original query.

The expansion is intended to be wrapped in an FTS OR query, e.g.
  websearch_to_tsquery('english', 'CAFR OR "comprehensive annual financial report"')
"""
import logging
import os
import re
from functools import lru_cache

log = logging.getLogger(__name__)

# Civic / municipal synonym dictionary. Keys and values are both searched —
# i.e. searching "CAFR" expands to include the long form, and searching
# "comprehensive annual financial report" expands to include "CAFR".
#
# Conservative on purpose: only add entries where the synonym is unambiguous
# and the false-positive risk is low.
_SYNONYMS: dict[str, list[str]] = {
    # Financial documents
    "cafr": ["comprehensive annual financial report", "annual financial report"],
    "acfr": ["annual comprehensive financial report", "annual financial report"],
    "afs": ["annual financial statement", "financial statements"],
    "afr": ["annual financial report"],
    "afsl": ["audited financial statements"],

    # Budget / fiscal terminology
    "appropriation": ["budget allocation", "spending authority"],
    "appropriations": ["budget allocations"],
    "encumbrance": ["committed funds", "obligated"],
    "encumbrances": ["committed funds", "obligations"],
    "millage": ["tax rate", "tax levy"],
    "tax levy": ["tax rate", "millage"],
    "fund balance": ["surplus", "reserves"],
    "operating budget": ["general fund budget"],

    # Municipal departments / services
    "dpw": ["department of public works", "public works"],
    "ocean": ["beach", "shoreline"],
    "sanitation": ["trash", "garbage", "waste collection", "refuse"],
    "trash": ["sanitation", "garbage", "refuse"],
    "garbage": ["sanitation", "trash", "refuse"],
    "refuse": ["sanitation", "trash", "garbage"],
    "stormwater": ["drainage", "runoff"],
    "police": ["pd", "public safety"],
    "fire": ["fire department", "fire company"],
    "ems": ["emergency medical services", "first aid", "ambulance"],
    "first aid": ["ems", "ambulance"],

    # Governance / meetings
    "council": ["borough council", "town council"],
    "boe": ["board of education", "school board"],
    "school board": ["board of education", "boe"],
    "planning board": ["planning"],
    "zoning board": ["zoning", "zba"],
    "zba": ["zoning board of adjustment", "zoning board"],
    "harbor commission": ["harbor"],
    "ordinance": ["municipal code", "ordinances"],
    "resolution": ["resolutions"],

    # Land use
    "redev": ["redevelopment"],
    "redevelopment": ["redev", "rehabilitation area"],
    "rehab": ["rehabilitation"],
    "subdivision": ["lot split", "land division"],
    "variance": ["zoning variance"],
    "coah": ["affordable housing", "council on affordable housing"],
    "affordable housing": ["coah"],

    # Services / fees
    "sewer fees": ["sewer rates", "sewage charges"],
    "water fees": ["water rates", "water charges"],
    "parking permit": ["parking pass"],
    "abc license": ["alcoholic beverage license", "liquor license"],
    "liquor license": ["abc license", "alcoholic beverage"],
}



def _place() -> str:
    from municipality import get_municipality
    muni = get_municipality()
    others = [e.label for e in muni.entities[1:] if e.kind in ("school", "municipal")]
    return f"{muni.name}, {muni.state}" + (f" (and {', '.join(others)})" if others else "")


def _merge_profile_glossary() -> None:
    """Fold the municipality + state glossaries into the static dictionary."""
    from municipality import get_municipality
    for key, values in get_municipality().glossary.items():
        bucket = _SYNONYMS.setdefault(key.lower(), [])
        bucket += [v for v in values if v not in bucket]


_merge_profile_glossary()

# Precompute reverse mappings so "comprehensive annual financial report"
# also expands to "CAFR". Skip multi-word values that would already match
# via the forward mapping.
_REVERSE: dict[str, list[str]] = {}
for k, vs in _SYNONYMS.items():
    for v in vs:
        _REVERSE.setdefault(v.lower(), []).append(k)


# Strip FTS operators when looking up synonyms; otherwise a quoted phrase
# never matches the dictionary keys (which are plain lowercase strings).
_OP_STRIP = re.compile(r'[\"\-+()]')


@lru_cache(maxsize=512)
def expand(query: str, allow_llm: bool = True) -> list[str]:
    """Return a deduplicated list of expansion phrases for `query` (excluding
    the original). Empty list means no expansions were found.

    Lookup is case-insensitive and operator-stripped — quoted phrases and
    +/- operators don't block dictionary hits.
    """
    if not query:
        return []
    cleaned = _OP_STRIP.sub(" ", query).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return []

    expansions: list[str] = []
    seen: set[str] = {cleaned}

    # 1. Whole-query match (handles multi-word synonym keys like "tax levy")
    if cleaned in _SYNONYMS:
        for syn in _SYNONYMS[cleaned]:
            if syn.lower() not in seen:
                expansions.append(syn)
                seen.add(syn.lower())
    if cleaned in _REVERSE:
        for syn in _REVERSE[cleaned]:
            if syn.lower() not in seen:
                expansions.append(syn)
                seen.add(syn.lower())

    # 2. Per-token match (for queries like "cafr 2024" → "comprehensive annual
    # financial report 2024"). Only expand tokens that are dictionary keys.
    tokens = cleaned.split()
    if len(tokens) <= 6:
        for tok in tokens:
            if tok in _SYNONYMS:
                for syn in _SYNONYMS[tok]:
                    if syn.lower() not in seen:
                        expansions.append(syn)
                        seen.add(syn.lower())

    # 3. LLM paraphrase — only for short, dict-miss queries. Long natural-
    # language queries usually don't need it (the FTS English stemmer handles
    # them), and the LLM round-trip is the most expensive part of the request.
    if allow_llm and not expansions and 0 < len(tokens) <= 3:
        try:
            paraphrases = _llm_paraphrase(cleaned)
            for p in paraphrases:
                if p.lower() not in seen:
                    expansions.append(p)
                    seen.add(p.lower())
        except Exception as exc:
            log.debug("llm paraphrase skipped: %s", exc)

    return expansions


def _llm_paraphrase(query: str) -> list[str]:
    """Ask Gemini for 3 paraphrases of a short query. Returns [] on any error.

    Cached at the module level (via expand's lru_cache) so a repeated user
    query doesn't re-pay the round-trip.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return []

    prompt = (
        "You are helping search a municipal government document corpus for "
        f"{_place()}. Given the user's search query, return 3 short paraphrases "
        "or synonym phrases that would help find relevant documents — civic "
        "vocabulary, government acronyms, related terms. Return as a JSON "
        "array of strings, lowercase, no explanation.\n\n"
        f"Query: {query}\n\n"
        "JSON:"
    )

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=200,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        if not response or not response.text:
            return []
        # Best-effort JSON extraction — Gemini sometimes wraps in ```json
        import json
        text = response.text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        arr = json.loads(m.group(0))
        # Record usage for observability
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                from database import SessionLocal
                from services.usage import record_usage
                sess = SessionLocal()
                try:
                    record_usage(
                        sess, source="query_expansion", model="gemini-2.5-flash",
                        input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                        output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
                        estimated_cost_usd=0.0,  # gemini-flash is near-free at this size
                        metadata={"query": query},
                    )
                finally:
                    sess.close()
        except Exception:
            pass
        # Sanitize: strings only, non-empty, reasonable length
        return [
            str(x).strip()
            for x in arr
            if isinstance(x, str) and x.strip() and len(x) < 80
        ][:3]
    except Exception as exc:
        log.debug("gemini paraphrase failed: %s", exc)
        return []


def build_or_query(original: str, expansions: list[str]) -> str:
    """Combine original query and expansion phrases into a websearch_to_tsquery
    string using OR. Quotes multi-word expansions so they match as phrases.

    Returns the original unchanged when there are no expansions, so we don't
    pay the OR-traversal cost for queries that didn't need expansion.
    """
    if not expansions:
        return original
    parts = [original]
    for exp in expansions:
        if " " in exp:
            parts.append(f'"{exp}"')
        else:
            parts.append(exp)
    return " OR ".join(parts)
