"""Idempotent worker functions for the meeting-recording transcribe +
summarize pipeline.

Called from two places:
  - routes/meetings.py — when a user clicks Transcribe/Summarize in the UI
    (via FastAPI BackgroundTasks)
  - scripts/transcribe_pending.py — the systemd-timer-driven worker that
    continuously drains the queue of uploaded recordings

Both entrypoints take a meeting_id, open their own DB session, and update
the document row in place. Failures land in doc.status as
`transcription_failed` / `summary_failed` so subsequent runs skip the row
rather than retry forever.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from database import SessionLocal
from models.document import Document

logger = logging.getLogger(__name__)

_RECORDING_DOC_TYPES = (
    "recording_council",
    "recording_planning",
    "recording_harbor",
    "recording_school_board",
)
# Inline-safe SQL literal — doc_type values are constants we control, so a
# bound-parameter expansion for the IN clause isn't worth the syntax overhead.
_DOC_TYPES_SQL = "(" + ",".join(f"'{t}'" for t in _RECORDING_DOC_TYPES) + ")"


def _lock_meeting(db, meeting_id: str) -> None:
    """Acquire a Postgres advisory transaction lock keyed by meeting_id.

    Released automatically when the calling transaction commits or rolls
    back. Two places mutate a recording's chunks/embedding/status:
      1. routes/meetings.py BackgroundTasks (user clicks Transcribe in UI)
      2. scripts/transcribe_pending.py worker tick (systemd timer)
    Without this lock they can deadlock by taking documents + document_chunks
    locks in inverse order. With it, whichever transaction gets to the lock
    first runs to completion; the other waits.

    Uses the two-arg form (int4, int4) — hashtext() returns int4, so we
    pair it with 0 as a namespace partition rather than casting to bigint.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k), 0)"),
        {"k": meeting_id},
    )


def process_playback_ready(meeting_id: str) -> str:
    """Transcode .wma/.wmv/etc. to .mp3 so browsers can play it.

    Stores the mp3 in S3 under <orig_key>.mp3 and writes the new key into
    metadata.recording.playback_s3_key. Skips meetings whose source is
    already a native playback format (mp3/m4a/wav) or that have already
    been transcoded.

    Returns one of: 'native', 'transcoded', 'youtube', 'skip', 'failed'.
    """
    from services.s3_service import S3Service
    from services.transcoding_service import (
        is_native_playback, needs_transcode, transcode_to_mp3, derive_mp3_key,
    )

    db = SessionLocal()
    try:
        _lock_meeting(db, meeting_id)
        doc = db.query(Document).filter(Document.id == meeting_id).first()
        if not doc:
            return "missing"
        meta = doc.metadata_ or {}
        rec = meta.get("recording") or {}
        platform = rec.get("platform") or "audio"

        if platform == "youtube":
            return "youtube"  # YouTube player handles its own decode
        if rec.get("playback_s3_key"):
            return "skip"  # already done
        source_name = doc.original_filename or doc.filename
        if is_native_playback(source_name):
            # Already playable as-is — flag so the API can serve the original.
            new_meta = {**meta, "recording": {**rec, "playback_s3_key": doc.s3_key, "playback_content_type": "audio/mpeg"}}
            doc.metadata_ = new_meta
            db.commit()
            return "native"
        if not needs_transcode(source_name):
            return "skip"

        try:
            s3 = S3Service()
            audio_bytes = s3.download_file(doc.s3_key)
            mp3_bytes = transcode_to_mp3(audio_bytes, source_hint=source_name)
            mp3_key = derive_mp3_key(doc.s3_key)
            s3.upload_file(mp3_bytes, mp3_key, "audio/mpeg")
        except Exception as e:
            logger.exception("Transcode failed for %s: %s", meeting_id, e)
            new_meta = {**meta, "transcode_error": str(e)[:500]}
            doc.metadata_ = new_meta
            db.commit()
            return "failed"

        new_rec = {**rec, "playback_s3_key": mp3_key, "playback_content_type": "audio/mpeg"}
        new_meta = {**meta, "recording": new_rec}
        new_meta.pop("transcode_error", None)
        doc.metadata_ = new_meta
        db.commit()
        logger.info("Transcoded meeting %s → %s (%d KB)", meeting_id, mp3_key, len(mp3_bytes) // 1024)
        return "transcoded"
    finally:
        db.close()


def process_transcribe(meeting_id: str) -> str:
    """Transcribe a single recording. Returns the resulting status string.

    Status transitions:
      uploaded → transcribing → transcribed | transcription_failed
    """
    from services.s3_service import S3Service
    from services.transcription_service import (
        transcribe_audio_bytes, transcribe_youtube,
    )

    db = SessionLocal()
    try:
        _lock_meeting(db, meeting_id)
        doc = db.query(Document).filter(Document.id == meeting_id).first()
        if not doc:
            logger.warning("Transcribe target %s vanished", meeting_id)
            return "missing"
        meta = doc.metadata_ or {}
        rec = meta.get("recording") or {}
        platform = rec.get("platform") or "audio"

        try:
            # If a sync script (scripts/sync_youtube_audio.py) has uploaded
            # the YouTube audio to S3 from a residential IP, prefer that —
            # YouTube blocks media downloads from EC2 even with cookies.
            s3_override = rec.get("transcribe_from_s3_key")
            if s3_override:
                s3 = S3Service()
                audio_bytes = s3.download_file(s3_override)
                result = transcribe_audio_bytes(
                    audio_bytes,
                    filename_hint=s3_override,
                )
            elif platform == "youtube":
                yt_id = rec.get("youtube_id")
                if not yt_id:
                    raise RuntimeError("youtube_id missing from metadata")
                result = transcribe_youtube(yt_id)
            else:
                s3 = S3Service()
                audio_bytes = s3.download_file(doc.s3_key)
                result = transcribe_audio_bytes(
                    audio_bytes,
                    filename_hint=doc.original_filename or doc.filename,
                )
        except Exception as e:
            logger.exception("Transcription failed for %s: %s", meeting_id, e)
            doc.status = "transcription_failed"
            doc.metadata_ = {**(doc.metadata_ or {}), "transcription_error": str(e)[:500]}
            db.commit()
            return "transcription_failed"

        doc.extracted_text = result.text
        # SQLAlchemy doesn't auto-detect JSONB mutation; reassign so it persists.
        new_meta = {**meta}
        new_meta["transcript"] = result.to_dict()
        new_meta.pop("transcription_error", None)
        doc.metadata_ = new_meta
        doc.status = "transcribed"
        db.commit()
        logger.info(
            "Transcribed meeting %s (%d segments, engine=%s)",
            meeting_id, len(result.segments), result.engine,
        )

        # Hand off to existing chunk+embed pipeline so chat can search it.
        try:
            from services.ingestion import ingest_document
            ingest_document(db, doc)
        except Exception as e:
            logger.warning("Post-transcribe ingest failed for %s: %s", meeting_id, e)

        return "transcribed"
    finally:
        db.close()


def process_summarize(meeting_id: str) -> str:
    """Summarize a transcribed recording. Returns the resulting status.

    Status transitions:
      transcribed → summarizing → summarized | summary_failed
    """
    from services.meeting_summary import summarize_meeting as run_summary

    db = SessionLocal()
    try:
        _lock_meeting(db, meeting_id)
        doc = db.query(Document).filter(Document.id == meeting_id).first()
        if not doc:
            return "missing"
        meta = doc.metadata_ or {}
        rec = meta.get("recording") or {}
        transcript = meta.get("transcript") or {}
        segments = transcript.get("segments") or []

        try:
            summary = run_summary(
                meeting_body=rec.get("meeting_body") or "Unknown body",
                meeting_date=rec.get("meeting_date"),
                segments=segments,
                raw_text=doc.extracted_text,
            )
        except Exception as e:
            logger.exception("Summarization failed for %s: %s", meeting_id, e)
            doc.status = "summary_failed"
            doc.metadata_ = {**meta, "summary_error": str(e)[:500]}
            db.commit()
            return "summary_failed"

        new_meta = {**meta}
        new_meta["summary"] = summary
        new_meta.pop("summary_error", None)
        doc.metadata_ = new_meta
        doc.status = "summarized"
        db.commit()
        logger.info("Summarized meeting %s", meeting_id)
        return "summarized"
    finally:
        db.close()


# ─── Queue helpers (used by scripts/transcribe_pending.py) ─────────────

def _list_recording_ids(*, where: str) -> list[str]:
    """Return UUIDs of recording documents matching a JSON/status predicate.
    Caller writes the WHERE clause; keep it parameterless and SQL-injection-free."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                f"SELECT id FROM documents "
                f"WHERE doc_type IN {_DOC_TYPES_SQL} "
                f"  AND ({where}) "
                f"ORDER BY COALESCE((metadata->'recording'->>'meeting_date')::date, created_at::date) DESC "
                f"LIMIT 200"
            )
        ).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        db.close()


def pending_playback_ids() -> list[str]:
    """Audio recordings with a transcode-required source and no playback key yet."""
    return _list_recording_ids(
        where=(
            "(metadata->'recording'->>'platform') = 'audio' "
            "AND (metadata->'recording'->>'playback_s3_key') IS NULL "
            "AND COALESCE(metadata->>'transcode_error', '') = ''"
        ),
    )


def pending_transcribe_ids() -> list[str]:
    """Recordings with no transcript and not previously failed."""
    return _list_recording_ids(
        where=(
            "(metadata->'transcript') IS NULL "
            "AND status NOT IN ('transcription_failed','transcribing')"
        ),
    )


def pending_summarize_ids() -> list[str]:
    """Recordings with a transcript but no summary and not previously failed."""
    return _list_recording_ids(
        where=(
            "(metadata->'transcript') IS NOT NULL "
            "AND (metadata->'summary') IS NULL "
            "AND status NOT IN ('summary_failed','summarizing')"
        ),
    )


def reingest_pending() -> int:
    """Chunk + embed any recording that has extracted_text but no chunks.

    Covers three cases:
      1. Transcribe succeeded but the post-transcribe ingest call crashed
         (e.g. embeddings API outage) — the row sat with text but no chunks.
      2. Existing transcripts written before the timestamped-text rollout
         have flat extracted_text + stale chunks; rebuild both from segments.
      3. (Implicit) Once a doc has timestamped extracted_text AND chunks,
         it's skipped on subsequent runs — idempotent.

    Returns the number of documents re-ingested.

    Each doc is processed in its OWN session/transaction so the advisory
    lock (per doc) doesn't bottleneck the whole batch and so a failure on
    one row doesn't poison the others' transactions.
    """
    # Discovery is read-only — no lock needed.
    discovery_db = SessionLocal()
    try:
        rows = discovery_db.execute(
            text(
                f"SELECT d.id "
                f"FROM documents d "
                f"LEFT JOIN document_chunks dc ON dc.document_id = d.id "
                f"WHERE d.doc_type IN {_DOC_TYPES_SQL} "
                f"  AND d.extracted_text IS NOT NULL "
                f"  AND length(d.extracted_text) > 100 "
                f"GROUP BY d.id "
                f"HAVING COUNT(dc.id) = 0 "
                f"   OR d.extracted_text NOT LIKE '[%:%:%]%' "
                f"LIMIT 50"
            )
        ).fetchall()
    finally:
        discovery_db.close()

    if not rows:
        return 0

    logger.info("Re-ingesting %d recording(s) for chunks/timestamps", len(rows))
    n = 0
    for (doc_id,) in rows:
        doc_id_str = str(doc_id)
        try:
            if _reingest_one(doc_id_str):
                n += 1
        except Exception as e:
            logger.warning("Re-ingest failed for %s: %s", doc_id_str, e)
    return n


def _reingest_one(doc_id: str) -> bool:
    """Do a full transcript rebuild for one meeting under an advisory lock.

    Opens a fresh session so the lock scope is tight and failures don't
    poison sibling docs in the batch."""
    from services.ingestion import ingest_document
    from models.document_chunk import DocumentChunk
    from services.transcription_service import (
        TranscriptSegment, _segments_to_timestamped_text,
    )

    db = SessionLocal()
    try:
        _lock_meeting(db, doc_id)
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            return False

        # Rebuild extracted_text from stored segments so it carries
        # [HH:MM:SS] markers (older rows had flat text).
        meta = doc.metadata_ or {}
        segments = (meta.get("transcript") or {}).get("segments") or []
        if segments:
            seg_objs = [
                TranscriptSegment(
                    start=float(s.get("start", 0)),
                    end=float(s.get("end", 0)),
                    text=s.get("text") or "",
                )
                for s in segments
            ]
            ts_text = _segments_to_timestamped_text(seg_objs)
            if ts_text and ts_text != doc.extracted_text:
                doc.extracted_text = ts_text

        # Wipe stale chunks so re-ingest produces a clean set; ingest_document
        # is idempotent for new chunks but doesn't clean up old ones.
        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
        db.flush()
        # ingest_document commits internally — that's our lock-release point.
        ingest_document(db, doc)
        return True
    finally:
        db.close()
