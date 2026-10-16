"""
Keyword retrieval that works for plain-English questions.

Postgres full-text matching ANDs every word, so "how much did the town pay to
fix the retaining wall on Upper Prospect?" finds nothing unless one chunk says
"much", "pay" and "fix" too. Here a chunk matches if it has ANY query term,
and is scored by the terms it has, each weighted by rarity (BM25-style IDF over
the corpus). "prospect" and "retain" carry the score; "town", "long", "beach"
(which appear in nearly every chunk) barely count.

Quotes, "-exclusions" and explicit OR keep their websearch meaning as an extra
filter, so power-user syntax still works.

Term document frequencies come from ts_stat over document_chunks, cached per
process and refreshed when the chunk count moves or after an hour.
"""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

_CACHE: dict = {"df": {}, "n": 0, "at": 0.0}
_LOCK = threading.Lock()
_TTL_S = 3600
MAX_TERMS = 12
# A term in more than this share of chunks is noise for ranking (unless it's all we have).
COMMON_SHARE = 0.35
_OPERATORS = re.compile(r'"|(?:^|\s)-\w|\bOR\b')


def _stats(db: Session) -> tuple[dict[str, int], int]:
    n = db.execute(text("SELECT count(*) FROM document_chunks WHERE fts_vector IS NOT NULL")).scalar() or 0
    with _LOCK:
        fresh = time.time() - _CACHE["at"] < _TTL_S
        stable = _CACHE["n"] and abs(n - _CACHE["n"]) <= max(50, 0.05 * _CACHE["n"])
        if fresh and stable:
            return _CACHE["df"], _CACHE["n"]
    rows = db.execute(text("SELECT word, ndoc FROM ts_stat('SELECT fts_vector FROM document_chunks "
                           "WHERE fts_vector IS NOT NULL')")).fetchall()
    df = {w: c for w, c in rows}
    with _LOCK:
        _CACHE.update(df=df, n=n, at=time.time())
    return df, n


def reset_cache() -> None:
    with _LOCK:
        _CACHE.update(df={}, n=0, at=0.0)


@dataclass
class KeywordQuery:
    terms: list[tuple[str, float]]  # (lexeme, weight)
    websearch: str | None  # original query when it carries operators

    @property
    def empty(self) -> bool:
        return not self.terms

    def params(self, prefix: str = "kt") -> dict:
        p = {f"{prefix}{i}": t for i, (t, _) in enumerate(self.terms)}
        p[f"{prefix}_or"] = " | ".join(f"'{t}'" for t, _ in self.terms) or "''"
        p[f"{prefix}_ws"] = self.websearch
        return p

    def where(self, col: str, prefix: str = "kt") -> str:
        clause = f"{col} @@ to_tsquery('simple', :{prefix}_or)"
        if self.websearch:
            clause += f" AND {col} @@ websearch_to_tsquery('english', :{prefix}_ws)"
        return clause

    def score(self, col: str, prefix: str = "kt") -> str:
        parts = [f"{w:.4f} * ({col} @@ to_tsquery('simple', :{prefix}{i}))::int"
                 for i, (_, w) in enumerate(self.terms)]
        # Proximity/density tiebreak among chunks with the same terms.
        parts.append(f"0.5 * ts_rank_cd({col}, to_tsquery('simple', :{prefix}_or))")
        return "(" + " + ".join(parts) + ")"


def build(db: Session, query: str, primary: str | None = None) -> KeywordQuery:
    """`query` may carry synonym expansions ("a b OR c"); terms that also occur in
    `primary` (the user's own words) get full weight, expansion-only terms 0.6."""
    lex = lambda s: db.execute(text("SELECT unnest(tsvector_to_array(to_tsvector('english', :q)))"),
                               {"q": s or ""}).scalars().all()
    words = [w for w in dict.fromkeys(lex(query)) if "'" not in w and "\\" not in w and len(w) > 1]
    own = set(lex(primary)) if primary else set(words)
    df, n = _stats(db)
    n = max(n, 1)
    scored = []
    for w in words:
        d = df.get(w, 0)
        if d == 0:
            continue  # not in the corpus: can't match, would only dilute
        idf = math.log(1 + (n - d + 0.5) / (d + 0.5))
        weight = idf * (1.0 if w in own else 0.6)
        scored.append((w, weight, d / n))
    rare = [s for s in scored if s[2] <= COMMON_SHARE]
    keep = rare or sorted(scored, key=lambda s: s[2])[:1]
    keep = sorted(keep, key=lambda s: -s[1])[:MAX_TERMS]
    ws = query if primary is None and _OPERATORS.search(query or "") else (
        primary if primary and _OPERATORS.search(primary) else None)
    return KeywordQuery(terms=[(w, wt) for w, wt, _ in keep], websearch=ws)
