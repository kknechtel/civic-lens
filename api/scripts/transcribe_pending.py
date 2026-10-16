"""Drain the meeting-recording processing queue.

Runs as a systemd timer (`ah-transcribe.timer`). Each invocation:

  1. Transcodes every pending .wma/.wmv recording to .mp3 (fast — ~30s per
     90-min meeting on CPU). Drains ALL pending so playback comes online
     immediately across the whole library.
  2. Transcribes ONE pending recording (slow — ~1x realtime on CPU with
     faster-whisper base/int8, so ~1hr per 1hr meeting). YouTube
     recordings prioritized since their captions API is instant.
  3. Summarizes whatever has a fresh transcript but no summary yet (fast).

Why one transcription per tick: keeps memory steady, lets systemd OOM-kill
or wall-clock cap recover gracefully, and the next tick picks up the next
meeting cleanly. The timer fires again 5 minutes after this run exits.

Why drain transcodes in one pass: they're fast and a backlog of unplayable
recordings is the most user-visible bug. Get playback working first.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, "/opt/atlantic-highlands/api")
# Local dev fallback so `python -m scripts.transcribe_pending` works from api/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ah_transcribe_pending")

# Wall-clock cap per run. Without a cap, a stuck Whisper call could pin the
# CPU forever. systemd will also kill via TimeoutStartSec= but we want a
# graceful exit before that.
MAX_RUNTIME_SECONDS = int(os.getenv("TRANSCRIBE_MAX_RUNTIME", "5400"))  # 90 min


def _within_budget(start: float) -> bool:
    return (time.monotonic() - start) < MAX_RUNTIME_SECONDS


def main() -> int:
    from services.meeting_pipeline import (
        process_playback_ready, process_summarize, process_transcribe,
        pending_playback_ids, pending_summarize_ids, pending_transcribe_ids,
        reingest_pending,
    )

    started = time.monotonic()

    # ── 0. Re-ingest any recordings that have extracted_text but no chunks
    # (transcript landed but vector pipeline crashed, or text predates the
    # timestamped-text rollout). Fast — no transcription work, just embedding.
    try:
        n = reingest_pending()
        if n:
            logger.info("Re-ingested %d recording(s) into chunks + embeddings", n)
    except Exception as e:
        logger.exception("Re-ingest pass errored: %s", e)

    # ── 1. Transcode all pending playback. Fast — drain everything.
    playback_pending = pending_playback_ids()
    if playback_pending:
        logger.info("Transcoding %d pending audio file(s)", len(playback_pending))
    for mid in playback_pending:
        if not _within_budget(started):
            logger.warning("Time budget exceeded during transcode pass; stopping")
            return 0
        try:
            outcome = process_playback_ready(mid)
            logger.info("Transcode %s → %s", mid, outcome)
        except Exception as e:
            logger.exception("Unhandled error transcoding %s: %s", mid, e)

    # ── 2. Transcribe one. Audio first (always works from our S3), YouTube
    # second (frequently blocked from EC2 IPs — see comment in
    # services/transcription_service.py::transcribe_youtube).
    if _within_budget(started):
        candidates = pending_transcribe_ids()
        from sqlalchemy import text as sql_text
        from database import SessionLocal
        if candidates:
            db = SessionLocal()
            try:
                rows = db.execute(
                    sql_text(
                        "SELECT id, metadata->'recording'->>'platform' AS p "
                        "FROM documents WHERE id::text = ANY(:ids)"
                    ),
                    {"ids": candidates},
                ).fetchall()
                yt = [str(r[0]) for r in rows if r[1] == "youtube"]
                audio = [str(r[0]) for r in rows if r[1] != "youtube"]
            finally:
                db.close()

            target = (audio or yt)[0] if (audio or yt) else None
            if target:
                logger.info(
                    "Transcribing 1 of %d pending (audio=%d, yt=%d): %s",
                    len(candidates), len(audio), len(yt), target,
                )
                try:
                    outcome = process_transcribe(target)
                    logger.info("Transcribe %s → %s", target, outcome)
                except Exception as e:
                    logger.exception("Unhandled error transcribing %s: %s", target, e)

    # ── 3. Summarize anything that has a fresh transcript.
    if _within_budget(started):
        summary_pending = pending_summarize_ids()
        if summary_pending:
            logger.info("Summarizing %d transcript(s)", len(summary_pending))
        for mid in summary_pending:
            if not _within_budget(started):
                logger.warning("Time budget exceeded during summary pass; stopping")
                return 0
            try:
                outcome = process_summarize(mid)
                logger.info("Summarize %s → %s", mid, outcome)
            except Exception as e:
                logger.exception("Unhandled error summarizing %s: %s", mid, e)

    elapsed = int(time.monotonic() - started)
    logger.info("Pipeline tick complete in %ds", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
