"""
Citation grounding: an answer may only cite documents the tools returned.

The agent records every document a tool returned this turn (`Sources.add`).
After the answer, `check()` resolves each `[source: …]` against that set:
  - verified   → document_id/title for the UI to link
  - unverified → cited, but no tool returned it this turn (likely invented)
The answer is stored with unverified citations marked, and the UI shows them
as such instead of as working links.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CITE_RE = re.compile(r"\[source:\s*([^\]]+?)\s*\]")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().strip("`'\"").lower())


@dataclass
class Sources:
    by_name: dict[str, dict] = field(default_factory=dict)

    def _put(self, filename, document_id=None, title=None, date=None):
        if filename:
            self.by_name.setdefault(_norm(str(filename)), {
                "filename": filename, "document_id": str(document_id) if document_id else None,
                "title": title, "date": date})

    def add(self, tool: str, result: dict) -> None:
        """Harvest documents from any tool result shape we produce."""
        if not isinstance(result, dict):
            return
        for key in ("chunks", "documents", "results", "awards", "contracts"):
            for row in result.get(key) or []:
                if isinstance(row, dict):
                    self._put(row.get("filename"), row.get("document_id") or row.get("id"),
                              row.get("title"), row.get("doc_date") or row.get("date"))
        if result.get("filename"):
            self._put(result["filename"], result.get("document_id"), result.get("title"))
        for flag in result.get("flags") or []:
            for ev in flag.get("evidence") or []:
                self._put(ev.get("filename") or ev.get("title"), ev.get("document_id"), ev.get("title"), ev.get("date"))

    def match(self, cited: str) -> dict | None:
        n = _norm(cited)
        if n in self.by_name:
            return self.by_name[n]
        # Tolerate a dropped extension or a title cited instead of the filename.
        for k, v in self.by_name.items():
            stem = re.sub(r"\.(pdf|docx?|xlsx?|txt|html?)$", "", k)
            if n in (stem, _norm(v.get("title") or "")):
                return v
        return None


def check(text: str, sources: Sources) -> dict:
    verified, unverified = {}, []
    for m in CITE_RE.finditer(text or ""):
        cited = m.group(1)
        hit = sources.match(cited)
        if hit:
            verified[hit["filename"]] = hit
        elif cited not in unverified:
            unverified.append(cited)
    return {"verified": list(verified.values()), "unverified": unverified,
            "cited": len(verified) + len(unverified)}


def mark_unverified(text: str, unverified: list[str]) -> str:
    if not unverified:
        return text
    bad = {_norm(u) for u in unverified}
    return CITE_RE.sub(lambda m: f"[unverified source: {m.group(1)}]" if _norm(m.group(1)) in bad else m.group(0), text)
