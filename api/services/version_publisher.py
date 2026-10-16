"""Publish a Presentation as an immutable PresentationVersion snapshot.

The editor mutates `presentations.sections` as the user types — auto-save
fires every few seconds. The public viewer reads from the row in
`presentation_versions` where `is_current_public=True`, NOT from that draft
blob, so editor changes have zero impact on what the public sees until the
operator clicks Republish.

Doc snapshot policy
-------------------
At publish time we walk every section for [DOC:id] tokens, resolve each
against the documents table, copy the underlying S3 object to a
version-scoped key under `presentation-versions/{pid}/v{N}/docs/`, and
record the source + snapshot mapping in `version.doc_snapshots`. The
public preview endpoint reads that map first when serving citation links,
so even if the source Document row is later replaced or deleted, the
published version's links keep resolving to the exact bytes that shipped
with it.
"""
from __future__ import annotations

import copy
import logging
import os
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from models.document import Document
from models.presentation import Presentation
from models.presentation_version import PresentationVersion

log = logging.getLogger(__name__)

# Same regex MarkdownRenderer uses to spot citation pills, mirrored on
# the server. We intentionally include the trailing-bracket-optional
# variant so partially-broken tokens still get snapshotted (the public
# resolver tolerates them too).
DOC_TOKEN_RE = re.compile(r"\[DOC:([A-Za-z0-9_\-]+)(?:\|[^\]]*)?\]")
EMAIL_TOKEN_RE = re.compile(r"\[EMAIL:([A-Za-z0-9_\-]+)(?:\|[^\]]*)?\]")


def _strings_in(blob: Any) -> Iterable[str]:
    """Yield every string value reachable inside a JSON-shaped blob."""
    if isinstance(blob, str):
        yield blob
    elif isinstance(blob, dict):
        for v in blob.values():
            yield from _strings_in(v)
    elif isinstance(blob, list):
        for v in blob:
            yield from _strings_in(v)


def _collect_doc_ids(sections: list[dict]) -> set[str]:
    """Find every [DOC:id] token referenced anywhere in section content
    (or top-level section keys — AH sections store narrative bodies at
    section.body, not section.content.body)."""
    out: set[str] = set()
    for s in sections or []:
        for text in _strings_in(s):
            for m in DOC_TOKEN_RE.finditer(text):
                out.add(m.group(1))
    return out


def _resolve_document(db: Session, raw_id: str) -> Optional[Document]:
    """Resolve a [DOC:id] token's id (UUID string) to a Document row."""
    try:
        return db.query(Document).filter(Document.id == raw_id).first()
    except Exception:
        return None


def _snapshot_doc_to_version_prefix(
    s3_client,
    bucket: str,
    source_key: str,
    presentation_id: str,
    version_no: int,
    doc_id: str,
    filename: str,
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Copy the S3 object to a version-scoped key.

    Returns (snapshot_key, source_etag, size_bytes). On any failure,
    returns (None, None, None) and logs — the publish flow keeps going
    so a missing object on one citation doesn't sink the whole snapshot.
    """
    safe = re.sub(r"[^A-Za-z0-9._\-]", "_", (filename or f"doc-{doc_id}.bin"))[:120]
    snapshot_key = f"presentation-versions/{presentation_id}/v{version_no}/docs/{doc_id}_{safe}"
    try:
        head = s3_client.head_object(Bucket=bucket, Key=source_key)
        size_bytes = int(head.get("ContentLength") or 0)
        source_etag = (head.get("ETag") or "").strip('"') or None
    except Exception as e:
        log.warning(
            "version snapshot HEAD failed for doc=%s key=%s: %s",
            doc_id, source_key, e,
        )
        return (None, None, None)
    try:
        s3_client.copy_object(
            Bucket=bucket,
            Key=snapshot_key,
            CopySource={"Bucket": bucket, "Key": source_key},
            MetadataDirective="COPY",
        )
    except Exception as e:
        log.warning(
            "version snapshot COPY failed for doc=%s %s -> %s: %s",
            doc_id, source_key, snapshot_key, e,
        )
        return (None, None, None)
    return (snapshot_key, source_etag, size_bytes)


def publish_new_version(
    p: Presentation,
    db: Session,
    user_id: Optional[str] = None,
    rolled_back_from: Optional[int] = None,
) -> PresentationVersion:
    """Snapshot the current draft state of `p` as a new immutable
    PresentationVersion, copy every cited document into a version-scoped
    S3 prefix, and flip `is_current_public` so the public viewer starts
    serving the new version. Returns the new row.

    Caller is responsible for committing the session.
    """
    # 1. Determine the next version_no for this presentation.
    last = (
        db.query(PresentationVersion)
        .filter(PresentationVersion.presentation_id == p.id)
        .order_by(PresentationVersion.version_no.desc())
        .first()
    )
    next_version_no = 1 if last is None else (last.version_no + 1)

    # 2. Build the doc_snapshots manifest by walking the draft sections
    #    for every [DOC:id] token, resolving each, and copying the S3
    #    bytes to the version-scoped key. We do this BEFORE writing the
    #    version row so a partial COPY storm doesn't leave a row claiming
    #    snapshots that don't exist.
    doc_ids = _collect_doc_ids(p.sections or [])
    doc_snapshots: dict[str, dict] = {}
    if doc_ids:
        try:
            import boto3
            s3 = boto3.client("s3")
        except Exception as e:
            log.warning("version snapshot: boto3 unavailable, skipping doc copies: %s", e)
            s3 = None
        # AH uses one bucket per document row — use the doc's s3_bucket
        # when copying. Fall back to env in case of missing values.
        default_bucket = os.environ.get("S3_BUCKET") or "atlantic-highlands-docs"
        for raw_id in doc_ids:
            doc = _resolve_document(db, raw_id)
            if not doc or not doc.s3_key:
                continue
            bucket = doc.s3_bucket or default_bucket
            if s3 is None:
                # Best-effort manifest: record the source even if we can't
                # copy. The public resolver will fall back to the live S3
                # key for these entries (less safe but at least functional).
                doc_snapshots[raw_id] = {
                    "snapshot_s3_key": None,
                    "filename": doc.filename,
                    "size_bytes": None,
                    "snapshotted_at": None,
                    "source_s3_key": doc.s3_key,
                    "source_bucket": bucket,
                    "source_etag": None,
                }
                continue
            snap_key, source_etag, size_bytes = _snapshot_doc_to_version_prefix(
                s3, bucket, doc.s3_key, str(p.id), next_version_no, raw_id, doc.filename or "doc.bin",
            )
            doc_snapshots[raw_id] = {
                "snapshot_s3_key": snap_key,
                "filename": doc.filename,
                "size_bytes": size_bytes,
                "snapshotted_at": datetime.utcnow().isoformat(),
                "source_s3_key": doc.s3_key,
                "source_bucket": bucket,
                "source_etag": source_etag,
            }

    # 3. Demote whatever was previously current_public — only one row
    #    per presentation can hold the flag (partial unique index would
    #    raise on the insert below otherwise).
    db.query(PresentationVersion).filter(
        PresentationVersion.presentation_id == p.id,
        PresentationVersion.is_current_public.is_(True),
    ).update({PresentationVersion.is_current_public: False})
    db.flush()

    # 4. Insert the new version with deep-copied content. The deep copy
    #    is critical: SQLAlchemy reuses the JSONB list/dict objects, so
    #    a later edit to p.sections would otherwise mutate the version's
    #    snapshot in place and silently break the public viewer.
    disclosure_blob = getattr(p, "disclosure", None)
    v = PresentationVersion(
        presentation_id=p.id,
        version_no=next_version_no,
        title=p.title,
        sections=copy.deepcopy(p.sections or []),
        attachments=copy.deepcopy(p.attachments or []),
        disclosure=copy.deepcopy(disclosure_blob) if disclosure_blob else None,
        doc_snapshots=doc_snapshots,
        public_password_hash=p.public_password_hash,
        is_current_public=True,
        published_at=datetime.utcnow(),
        published_by=user_id,
        rolled_back_from_version_no=rolled_back_from,
    )
    db.add(v)
    db.flush()

    # 5. Update parent Presentation's published_at + status.
    p.published_at = v.published_at
    if p.status != "published":
        p.status = "published"

    log.info(
        "Published presentation %s as v%s (docs snapshotted: %d)",
        p.id, next_version_no,
        len([d for d in doc_snapshots.values() if d.get("snapshot_s3_key")]),
    )
    return v


def get_current_public_version(db: Session, p: Presentation) -> Optional[PresentationVersion]:
    return (
        db.query(PresentationVersion)
        .filter(
            PresentationVersion.presentation_id == p.id,
            PresentationVersion.is_current_public.is_(True),
        )
        .first()
    )


def diff_summary(p: Presentation, current_version: Optional[PresentationVersion]) -> dict:
    """Cheap structural diff between draft and current public version.
    Returns the kind of summary the editor's "X unpublished changes"
    badge needs — counts of sections changed/added/removed plus a
    boolean indicating whether the title changed."""
    if current_version is None:
        # Never published — every section is an "unpublished change".
        n = len(p.sections or [])
        return {
            "ever_published": False,
            "title_changed": False,
            "sections_added": n,
            "sections_removed": 0,
            "sections_changed": 0,
            "total_changes": n,
        }

    pub_secs = {s.get("id"): s for s in (current_version.sections or []) if s.get("id")}
    draft_secs = {s.get("id"): s for s in (p.sections or []) if s.get("id")}
    pub_ids = set(pub_secs.keys())
    draft_ids = set(draft_secs.keys())
    added = len(draft_ids - pub_ids)
    removed = len(pub_ids - draft_ids)
    changed = 0
    for sid in pub_ids & draft_ids:
        if pub_secs[sid] != draft_secs[sid]:
            changed += 1
    title_changed = (current_version.title or "") != (p.title or "")
    return {
        "ever_published": True,
        "title_changed": title_changed,
        "sections_added": added,
        "sections_removed": removed,
        "sections_changed": changed,
        "total_changes": added + removed + changed + (1 if title_changed else 0),
    }
