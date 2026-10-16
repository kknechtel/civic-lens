"""
Integrity pipeline: extract facts from processed documents, run the rules,
upsert flags (preserving reviewer status), and import campaign-finance data.

    from services.integrity.runner import run_all
    run_all(db)                     # incremental: only docs not yet extracted
    run_all(db, force=True)         # re-extract everything (after parser changes)
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import date, datetime
from typing import Iterable

from sqlalchemy import or_

from municipality import get_municipality
from models.document import Document
from models.integrity import Award, Contribution, IntegrityFlag, Official, PersonnelAction

from .extract import extract_all, extract_bill_list, find_date, norm_name
from .rules import Ctx, run_rules

logger = logging.getLogger(__name__)

EXTRACT_VERSION = 1
# Document types worth parsing for resolutions/personnel/roll call.
_FACT_TYPES = {"agenda", "minutes", "resolution", "contract", "bid", "report", "notice", "general", "ordinance",
               "bill_list", "budget", None}
_OVERSIGHT_TITLE = re.compile(r"audit|comptroller|budget review|fiscal stress|management letter|single audit|"
                              r"internal control|corrective action", re.I)


def _doc_date(doc) -> date | None:
    if doc.doc_date:
        try:
            return date.fromisoformat(doc.doc_date[:10])
        except ValueError:
            pass
    meta = doc.metadata_ or {}
    return find_date(" ".join(filter(None, [doc.title, meta.get("title"), doc.filename])))


def extract_document(db, doc) -> dict:
    """Replace this document's facts with a fresh extraction. Caller commits."""
    for model in (Award, PersonnelAction, Official):
        db.query(model).filter(model.document_id == doc.id).delete(synchronize_session=False)
    text = doc.extracted_text or ""
    facts_date = _doc_date(doc) or find_date(text[:1500])
    facts = extract_all(text, facts_date)
    muni = get_municipality()
    entity = doc.municipality or muni.primary_entity.key
    for a in facts.awards:
        db.add(Award(document_id=doc.id, entity_key=entity, meeting_date=a.meeting_date, item_no=a.item_no,
                     page=a.page, title=a.title, vendor_name=a.vendor_name, vendor_norm=norm_name(a.vendor_name) or None,
                     vendor_address=a.vendor_address, amount=a.amount, action_type=a.action_type,
                     procurement=a.procurement, bids_received=a.bids_received, account_code=a.account_code,
                     quote=a.quote, details=getattr(a, "details", None) or {}))
    for b in extract_bill_list(text):
        db.add(Award(document_id=doc.id, entity_key=entity, meeting_date=facts_date, item_no=None, page=b.page,
                     title="Bill list: " + "; ".join(b.descriptions[:2]), vendor_name=b.vendor_name,
                     vendor_norm=norm_name(b.vendor_name) or None, amount=b.amount, action_type="payment",
                     procurement="n/a", account_code=(b.accounts or [None])[0], quote=b.quote, extracted_by="bill_list"))
    for p in facts.personnel:
        db.add(PersonnelAction(document_id=doc.id, entity_key=entity, last_name=p.last_name, first_name=p.first_name,
                               department=p.department, action=p.action, position=p.position, salary=p.salary,
                               effective=p.effective, page=p.page, quote=p.quote))
    for o in facts.officials:
        db.add(Official(document_id=doc.id, entity_key=entity, name=o.name, last_name=o.last_name, role=o.role,
                        seen_on=_doc_date(doc)))
    meta = dict(doc.metadata_ or {})
    meta["integrity_version"] = EXTRACT_VERSION
    doc.metadata_ = meta
    return {"awards": len(facts.awards), "personnel": len(facts.personnel), "officials": len(facts.officials)}


def extract_pending(db, force: bool = False, limit: int | None = None) -> dict:
    q = db.query(Document).filter(Document.extracted_text.isnot(None),
                                  or_(Document.doc_type.in_([t for t in _FACT_TYPES if t]), Document.doc_type.is_(None)))
    totals = {"documents": 0, "awards": 0, "personnel": 0, "officials": 0}
    # Iterate ids, not a streaming cursor: we commit every 50 docs, which
    # would invalidate a server-side cursor mid-loop.
    for (doc_id,) in q.with_entities(Document.id).all():
        doc = db.get(Document, doc_id)
        if not force and (doc.metadata_ or {}).get("integrity_version") == EXTRACT_VERSION:
            continue
        counts = extract_document(db, doc)
        totals["documents"] += 1
        for k, v in counts.items():
            totals[k] += v
        if totals["documents"] % 50 == 0:
            db.commit()
        if limit and totals["documents"] >= limit:
            break
    db.commit()
    return totals


def _rows(q) -> list[dict]:
    return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in q]


_GENERIC_VENDOR_WORDS = set("""and of the industries group associates contracting contractors construction services solutions
engineering engineers architects consultants consulting systems technologies technology supply supplies electric
environmental partners company corp inc llc enterprises sales""".split())


def canonical_vendors(norms: list[str]) -> dict[str, str]:
    """Merge spelling variants of one vendor ("a i i allen industries" → "allen industries",
    "t novelli contracting" ~ "thomas novelli contracting"). Two names link when every
    word of the shorter (initials dropped) appears in the longer and they share a
    distinctive word — so "allen electric" and "allen industries" stay apart. Links are
    transitive (union-find); each group is named by its most complete variant."""
    def toks(n):
        return frozenset(t for t in n.split() if len(t) > 1)
    names = sorted(set(n for n in norms if n))
    parent = {n: n for n in names}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    tk = {n: toks(n) for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ta, tb = tk[a], tk[b]
            small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if small and small <= big and (small - _GENERIC_VENDOR_WORDS or not (big - _GENERIC_VENDOR_WORDS)):
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    canon = {}
    for members in groups.values():
        rep = max(members, key=lambda m: (len(tk[m]), m))
        for m in members:
            canon[m] = rep
    return canon


def dedupe_awards(awards: list[dict]) -> list[dict]:
    """One resolution shows up in the agenda, the backup packet and the minutes.
    Collapse copies (same entity, meeting date, amount and vendor/title) and keep
    the other documents as extra evidence."""
    keep: dict[tuple, dict] = {}
    for a in sorted(awards, key=lambda a: str(a.get("document_id"))):
        subject = a.get("vendor_norm") or re.sub(r"\W+", " ", (a.get("title") or "").lower())[:60]
        key = (a.get("entity_key"), a.get("meeting_date"), round(a.get("amount") or 0, 2), subject, a.get("action_type"))
        if key in keep:
            keep[key].setdefault("copies", []).append(a)
        else:
            keep[key] = a
    return list(keep.values())


def estimate_dates_from_numbering(awards: list[dict]) -> None:
    """NJ resolutions are numbered sequentially per year (068-2025). An undated one
    sits between its dated neighbours; take the earlier neighbour's date."""
    import re as _re
    by_year: dict = {}
    for a in awards:
        m = _re.fullmatch(r"(\d{1,4})-(\d{4})", str((a.get("details") or {}).get("resolution_no") or ""))
        if m:
            by_year.setdefault((a.get("entity_key"), m.group(2)), []).append((int(m.group(1)), a))
    for (_, yr), rows in by_year.items():
        dated = sorted((n, a["meeting_date"]) for n, a in rows if a.get("meeting_date") and str(a["meeting_date"].year) == yr)
        for n, a in rows:
            if a.get("meeting_date") or not dated:
                continue
            before = [d for k, d in dated if k < n]
            after = [d for k, d in dated if k > n]
            if before and after:
                a["meeting_date"] = before[-1]
                a["details"] = {**(a.get("details") or {}), "date_estimated": True,
                                "date_range": [str(before[-1]), str(after[0])]}


def build_context(db) -> Ctx:
    muni = get_municipality()
    awards = [a for a in _rows(db.query(Award).all()) if not (a.get("details") or {}).get("withdrawn")]
    canon = canonical_vendors([a["vendor_norm"] for a in awards if a.get("vendor_norm")])
    for a in awards:
        if a.get("vendor_norm"):
            a["vendor_norm"] = canon.get(a["vendor_norm"], a["vendor_norm"])
    awards = dedupe_awards(awards)
    estimate_dates_from_numbering(awards)
    for a in awards:
        if a.get("meeting_date"):
            a["fiscal_year"] = muni.fiscal_year_for(a["meeting_date"].year, a["meeting_date"].month, a.get("entity_key"))
    doc_ids = {str(a["document_id"]) for a in awards}
    personnel = _rows(db.query(PersonnelAction).all())
    officials = _rows(db.query(Official).all())
    doc_ids |= {str(p["document_id"]) for p in personnel}

    oversight = []
    # Audits, plus any document with audit text embedded (councils often attach
    # State Comptroller reports to their minutes).
    audit_text = or_(Document.extracted_text.ilike("%Report of Examination%"),
                     Document.extracted_text.ilike("%Local Government and School Accountability%"),
                     Document.extracted_text.ilike("%Independent Auditor%Report%"),
                     Document.extracted_text.ilike("%Auditor%Management Report%"))
    for d in db.query(Document).filter(Document.extracted_text.isnot(None)).filter(
            or_(Document.doc_type.in_(["audit", "audit_management_report"]), audit_text)).all():
        title = d.title or (d.metadata_ or {}).get("title") or d.filename
        if True:
            oversight.append({"document_id": d.id, "title": title, "text": d.extracted_text,
                              "url": (d.metadata_ or {}).get("source_url"), "entity_key": d.municipality})
            doc_ids.add(str(d.id))

    docs = {}
    if doc_ids:
        for d in db.query(Document.id, Document.title, Document.filename, Document.metadata_).filter(
                Document.id.in_(list(doc_ids))).all():
            docs[str(d.id)] = {"title": d.title or (d.metadata_ or {}).get("title") or d.filename,
                               "url": (d.metadata_ or {}).get("source_url")}
    return Ctx(awards=awards, personnel=personnel, officials=officials,
               contributions=_rows(db.query(Contribution).all()), oversight_docs=oversight,
               config=muni.integrity, docs=docs)


def apply_flags(db, flags) -> dict:
    now = datetime.utcnow()
    existing = {f.fingerprint: f for f in db.query(IntegrityFlag).all()}
    seen, created, updated = set(), 0, 0
    for f in flags:
        seen.add(f.fingerprint)
        row = existing.get(f.fingerprint)
        if row is None:
            db.add(IntegrityFlag(fingerprint=f.fingerprint, rule=f.rule, severity=f.severity, score=f.score,
                                 title=f.title, explanation=f.explanation, entity_key=f.entity_key, subject=f.subject,
                                 amount=f.amount, evidence=f.evidence, first_seen=now, last_seen=now, active=1))
            created += 1
        else:
            row.severity, row.score, row.title, row.explanation = f.severity, f.score, f.title, f.explanation
            row.subject, row.amount, row.evidence, row.last_seen, row.active = f.subject, f.amount, f.evidence, now, 1
            updated += 1
    retired = 0
    for fp, row in existing.items():
        if fp not in seen and row.active:
            row.active = 0
            retired += 1
    db.commit()
    return {"flags": len(flags), "created": created, "updated": updated, "retired": retired}


def run_all(db, force: bool = False) -> dict:
    ex = extract_pending(db, force=force)
    ctx = build_context(db)
    flags = run_rules(ctx)
    result = {"extracted": ex, **apply_flags(db, flags)}
    logger.info("integrity: %s", result)
    return result


# ── Campaign finance import ──────────────────────────────────────────────────

# Column-name guesses for common state exports; override with `columns=`.
_COLUMN_GUESSES = {
    "contributor": ["contributor", "contributor_name", "name", "flng_ent_name", "contributor name", "ContributorName"],
    "employer": ["employer", "employer_name", "occupation_employer", "EmpName", "employer name"],
    "address": ["address", "contributor_address", "street", "flng_ent_add1", "Address1"],
    "recipient": ["recipient", "committee", "candidate", "filer_name", "cand_comm_name", "Recipient", "committee name"],
    "amount": ["amount", "contribution_amount", "org_amt", "ContributionAmount", "Amount"],
    "date": ["date", "contribution_date", "sched_date", "ContributionDate", "Date"],
}


def _pick(row: dict, names: Iterable[str]):
    lower = {k.lower().strip(): v for k, v in row.items() if k}
    for n in names:
        if n.lower() in lower and lower[n.lower()] not in (None, ""):
            return lower[n.lower()]
    return None


def import_contributions_csv(db, path: str, source: str, columns: dict | None = None,
                             replace_source: bool = True) -> int:
    """Load a campaign-finance CSV (NJ ELEC, NYS BOE, local filings…)."""
    cols = {k: [columns[k]] if columns and k in columns else v for k, v in _COLUMN_GUESSES.items()}
    if replace_source:
        db.query(Contribution).filter(Contribution.source == source).delete(synchronize_session=False)
    n = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            amount = _pick(row, cols["amount"])
            try:
                amount = float(str(amount).replace("$", "").replace(",", "")) if amount is not None else None
            except ValueError:
                amount = None
            raw_date = _pick(row, cols["date"]) or ""
            d = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d"):
                try:
                    d = datetime.strptime(raw_date[:10].strip(), fmt).date()
                    break
                except ValueError:
                    continue
            contributor = _pick(row, cols["contributor"])
            employer = _pick(row, cols["employer"])
            db.add(Contribution(source=source, contributor=contributor, contributor_norm=norm_name(contributor) or None,
                                employer=employer, employer_norm=norm_name(employer) or None,
                                address=_pick(row, cols["address"]), recipient=_pick(row, cols["recipient"]),
                                amount=amount, date=d, raw=row))
            n += 1
            if n % 1000 == 0:
                db.flush()
    db.commit()
    return n
