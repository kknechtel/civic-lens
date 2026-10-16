"""Download YouTube audio LOCALLY (from a residential IP), upload to S3.

YouTube blocks media downloads from AWS datacenter IPs even with valid
cookies — they fingerprint the IP-to-session mismatch and serve degraded
responses (storyboards only). The only sustainable workaround that
doesn't cost ongoing $$ is to do the download from a residential IP.

Run this script on your laptop whenever HHRSD posts new meetings:

    cd api
    python -m scripts.sync_youtube_audio

It will:
  1. Query prod for all `recording_school_board` docs with no
     `metadata.recording.playback_s3_key` set (i.e., still need audio).
  2. For each, run yt-dlp locally → bestaudio → ~30s per video.
  3. Upload the audio bytes to prod S3 at
     `recordings/youtube/<youtube_id>.m4a`.
  4. Update the doc's metadata so prod's transcription worker downloads
     from S3 instead of trying YouTube directly.
  5. Reset the doc's status to `uploaded` so the worker retries
     transcription.

Requires AWS creds in your local env (same bucket as prod) and a working
yt-dlp installation locally.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# So `python -m scripts.sync_youtube_audio` works from api/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("yt_sync")


def _pending_youtube_docs(db):
    """Return list of (doc_id, youtube_id, title) tuples needing audio sync."""
    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT id::text,
               metadata->'recording'->>'youtube_id' AS yt,
               filename
        FROM documents
        WHERE doc_type = 'recording_school_board'
          AND (metadata->'recording'->>'playback_s3_key') IS NULL
          AND (metadata->'recording'->>'youtube_id') IS NOT NULL
        ORDER BY COALESCE((metadata->'recording'->>'meeting_date')::date, created_at::date) DESC
    """)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _download_audio_local(video_id: str, out_dir: str) -> tuple[bytes, str]:
    """yt-dlp from a residential IP — fetches the audio bytes."""
    import yt_dlp
    out_template = os.path.join(out_dir, f"{video_id}.%(ext)s")
    ydl_opts = {
        "format": "bestaudio[acodec!=none]/bestaudio/best",
        "outtmpl": out_template,
        "quiet": False,
        "no_warnings": True,
        "noprogress": True,
        "writeinfojson": False,
        "writethumbnail": False,
    }
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)
    for fname in os.listdir(out_dir):
        if fname.startswith(f"{video_id}."):
            path = os.path.join(out_dir, fname)
            with open(path, "rb") as fh:
                return fh.read(), fname
    raise RuntimeError(f"yt-dlp produced no file for {video_id}")


def main() -> int:
    from database import SessionLocal
    from sqlalchemy import text
    from services.s3_service import S3Service

    db = SessionLocal()
    s3 = S3Service()
    try:
        docs = _pending_youtube_docs(db)
        if not docs:
            log.info("No YouTube docs need syncing — all caught up.")
            return 0
        log.info("Syncing %d YouTube doc(s)", len(docs))

        synced = 0
        for doc_id, yt_id, title in docs:
            log.info("→ %s  (yt=%s)", title[:60], yt_id)
            try:
                with tempfile.TemporaryDirectory(prefix="ytsync_") as td:
                    audio_bytes, fname = _download_audio_local(yt_id, td)
            except Exception as e:
                log.error("  download failed: %s", str(e)[:200])
                continue

            # Determine extension from yt-dlp's output filename.
            ext = os.path.splitext(fname)[1].lstrip(".") or "m4a"
            s3_key = f"recordings/youtube/{yt_id}.{ext}"
            content_type = {
                "m4a": "audio/mp4", "mp4": "audio/mp4",
                "webm": "audio/webm", "opus": "audio/opus",
                "mp3": "audio/mpeg",
            }.get(ext, "application/octet-stream")
            log.info("  uploading %d KB to %s", len(audio_bytes) // 1024, s3_key)
            s3.upload_file(audio_bytes, s3_key, content_type)

            # Update the doc so the prod worker uses the S3 audio instead
            # of trying YouTube. Also reset status so it re-enters the
            # transcribe queue. Use playback_s3_key for streaming AND a
            # new `transcribe_from_s3_key` for transcription.
            # SQLAlchemy mis-parses `:name::text` (interprets `::` weirdly).
            # Use CAST(:name AS text) and a distinct bind name for the JSON
            # value to keep both substitutions clean.
            db.execute(text("""
                UPDATE documents
                SET status = 'uploaded',
                    s3_key = :s3_key,
                    metadata = jsonb_set(
                        jsonb_set(
                            metadata - 'transcription_error',
                            '{recording,transcribe_from_s3_key}',
                            to_jsonb(CAST(:s3_key_a AS text))
                        ),
                        '{recording,playback_s3_key}',
                        to_jsonb(CAST(:s3_key_b AS text))
                    )
                WHERE id = CAST(:doc_id AS uuid)
            """), {"doc_id": doc_id, "s3_key": s3_key, "s3_key_a": s3_key, "s3_key_b": s3_key})
            db.commit()
            synced += 1
            log.info("  ✓ %s ready for prod transcription", yt_id)

        log.info("Done — synced %d/%d", synced, len(docs))
        return 0 if synced == len(docs) else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
