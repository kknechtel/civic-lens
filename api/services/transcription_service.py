"""Transcription for meeting recordings.

Three sources, one normalized result:

  - Town audio (.wma/.wav/.m4a/.dcr) → faster-whisper (local CPU, base model),
    OpenAI Whisper API fallback if OPENAI_API_KEY is set.
  - YouTube videos → youtube-transcript-api auto-captions (no download).

Returns segments with float-second offsets so the UI can render a clickable
transcript that seeks the player. The full text is stored on
Document.extracted_text so the existing RAG ingest pipeline can chunk + embed
it without changes.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Audio longer than this gets sliced into chunks (in seconds). Keeps peak
# memory bounded — a 94-min .wma decoded to 16k mono PCM is ~180MB, which
# combined with the Whisper model and ctranslate2 internals pushed our
# t3.medium past its 4GB cap. 20-min chunks cap that at ~40MB per chunk.
_CHUNK_THRESHOLD_SECONDS = int(os.getenv("WHISPER_CHUNK_THRESHOLD", "1500"))  # 25 min
_CHUNK_SECONDS = int(os.getenv("WHISPER_CHUNK_SECONDS", "1200"))  # 20 min


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    text: str
    segments: list[TranscriptSegment]
    engine: str
    language: str = "en"
    duration_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in self.segments
            ],
            "warnings": self.warnings,
        }


def _segments_to_timestamped_text(segments: list["TranscriptSegment"]) -> str:
    """Render segments as `[HH:MM:SS] text\n` lines.

    This is what we store in Document.extracted_text so the existing RAG
    chunker preserves timestamps inside each chunk — Claude can then cite
    a specific moment like "[01:23:45]" and the UI can deep-link to it.
    """
    out: list[str] = []
    for s in segments:
        if not s.text:
            continue
        secs = int(max(0.0, s.start))
        h, rem = divmod(secs, 3600)
        m, sec = divmod(rem, 60)
        out.append(f"[{h:02d}:{m:02d}:{sec:02d}] {s.text}")
    return "\n".join(out)


# ─── faster-whisper (local) ─────────────────────────────────────────
# Lazy singleton — loading the model is expensive (~200ms + first-run download).
# Module-level so successive calls in one process reuse it.
_whisper_model = None
_WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "base")  # base | small | tiny


def _get_whisper():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        logger.error("faster-whisper not installed: %s", e)
        return None
    # int8 quantization keeps memory low on CPU; on first run faster-whisper
    # downloads ~150MB to ~/.cache/huggingface/hub.
    try:
        _whisper_model = WhisperModel(
            _WHISPER_MODEL_SIZE, device="cpu", compute_type="int8"
        )
        logger.info("Loaded faster-whisper model: %s (int8 CPU)", _WHISPER_MODEL_SIZE)
    except Exception as e:
        logger.error("Failed to initialize faster-whisper: %s", e)
        _whisper_model = None
    return _whisper_model


def transcribe_local(audio_path: str) -> Optional[TranscriptionResult]:
    """Run faster-whisper against a local audio file. Returns None on failure."""
    model = _get_whisper()
    if model is None:
        return None
    try:
        # vad_filter=True drops silences which speeds up meeting audio noticeably.
        # word_timestamps=False — we only need segment-level for click-to-seek.
        segments_iter, info = model.transcribe(
            audio_path,
            vad_filter=True,
            beam_size=1,
            language=None,  # let it detect
        )
        segs: list[TranscriptSegment] = []
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if not text:
                continue
            segs.append(TranscriptSegment(
                start=float(seg.start),
                end=float(seg.end),
                text=text,
            ))
        return TranscriptionResult(
            text=_segments_to_timestamped_text(segs),
            segments=segs,
            engine=f"faster-whisper-{_WHISPER_MODEL_SIZE}",
            language=getattr(info, "language", "en") or "en",
            duration_seconds=float(getattr(info, "duration", 0.0) or 0.0),
        )
    except Exception as e:
        logger.exception("faster-whisper transcription failed: %s", e)
        return None


# ─── Chunked transcription (for long meetings) ─────────────────────

def _probe_duration_seconds(audio_path: str) -> Optional[float]:
    """Return audio duration in seconds via ffprobe, or None on failure.

    Used by transcribe_local_chunked() to decide whether to chunk at all
    and how many slices to take."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip()) if out.returncode == 0 else None
    except (ValueError, subprocess.SubprocessError):
        return None


def _extract_chunk(src_path: str, *, start: float, duration: float, out_path: str) -> bool:
    """Slice [start, start+duration) out of src into a 16kHz mono WAV.

    16k mono is faster-whisper's native input — no resampling cost inside
    Whisper. WAV at that rate is ~32 KB/s, so a 20-min chunk is ~38 MB."""
    if not shutil.which("ffmpeg"):
        return False
    try:
        # -ss before -i = fast seek (decoding skips). -ar/-ac/-c:a converts
        # to the format faster-whisper wants natively.
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
             "-i", src_path, "-vn",
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
             out_path],
            capture_output=True, text=True, timeout=600,
        )
        return proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except subprocess.SubprocessError:
        return False


def transcribe_local_chunked(audio_path: str, *, chunk_seconds: int = _CHUNK_SECONDS) -> Optional[TranscriptionResult]:
    """Stream-process a long audio file in N-second chunks.

    Extracts one chunk to disk, transcribes, deletes, repeats. Memory
    stays bounded regardless of total audio length. Each chunk's segments
    are stitched into a single timeline by adding the chunk's start offset.

    Returns None on early failure (e.g., ffprobe couldn't read duration).
    """
    duration = _probe_duration_seconds(audio_path)
    if duration is None:
        # Can't probe — fall back to one-shot transcribe (may OOM on long
        # files, but at least we try).
        return transcribe_local(audio_path)

    if duration <= _CHUNK_THRESHOLD_SECONDS:
        # Short enough to do in one pass — chunking adds overhead with no benefit.
        return transcribe_local(audio_path)

    model = _get_whisper()
    if model is None:
        return None

    logger.info(
        "Transcribing %s in chunks (duration=%.1fs, chunk=%ds → %d chunks)",
        audio_path, duration, chunk_seconds,
        int(duration // chunk_seconds) + (1 if duration % chunk_seconds else 0),
    )

    all_segments: list[TranscriptSegment] = []
    detected_lang: Optional[str] = None
    offset = 0.0
    chunk_idx = 0
    while offset < duration:
        chunk_path = audio_path + f".chunk{chunk_idx:03d}.wav"
        ok = _extract_chunk(
            audio_path, start=offset,
            duration=min(chunk_seconds, duration - offset),
            out_path=chunk_path,
        )
        if not ok:
            logger.error("Failed to extract chunk %d of %s at %.1fs", chunk_idx, audio_path, offset)
            return None
        try:
            # vad_filter + beam_size=1 = fastest settings. Each chunk gets its
            # own .transcribe() call; the model object is reused so weights
            # stay loaded.
            segments_iter, info = model.transcribe(
                chunk_path,
                vad_filter=True,
                beam_size=1,
                language=detected_lang,  # lock language after first chunk
            )
            for seg in segments_iter:
                text = (seg.text or "").strip()
                if not text:
                    continue
                all_segments.append(TranscriptSegment(
                    start=float(seg.start) + offset,
                    end=float(seg.end) + offset,
                    text=text,
                ))
            if detected_lang is None:
                detected_lang = getattr(info, "language", None)
        finally:
            try:
                os.unlink(chunk_path)
            except OSError:
                pass
        offset += chunk_seconds
        chunk_idx += 1

    return TranscriptionResult(
        text=_segments_to_timestamped_text(all_segments),
        segments=all_segments,
        engine=f"faster-whisper-{_WHISPER_MODEL_SIZE}-chunked",
        language=detected_lang or "en",
        duration_seconds=duration,
    )


# ─── OpenAI Whisper API (fallback) ──────────────────────────────────

def transcribe_openai(audio_path: str) -> Optional[TranscriptionResult]:
    """Cloud fallback. Only fires when OPENAI_API_KEY is set."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError as e:
        logger.error("openai package missing: %s", e)
        return None
    try:
        client = OpenAI(api_key=api_key)
        with open(audio_path, "rb") as fh:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=fh,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        # SDK returns either a dict-like or object depending on version.
        data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        segs = []
        for s in data.get("segments") or []:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            segs.append(TranscriptSegment(
                start=float(s.get("start", 0)),
                end=float(s.get("end", 0)),
                text=text,
            ))
        return TranscriptionResult(
            # Prefer our own timestamped rendering over OpenAI's flat `text`
            # so chunks in extracted_text preserve [HH:MM:SS] markers.
            text=_segments_to_timestamped_text(segs) or (data.get("text") or ""),
            segments=segs,
            engine="openai-whisper-1",
            language=data.get("language") or "en",
            duration_seconds=float(data.get("duration") or 0.0),
        )
    except Exception as e:
        logger.exception("OpenAI Whisper API failed: %s", e)
        return None


# ─── Public entrypoint for audio ────────────────────────────────────

def transcribe_audio_bytes(audio_bytes: bytes, *, filename_hint: str = "audio") -> TranscriptionResult:
    """Local-first with OpenAI fallback. Raises if both fail."""
    # Preserve the original extension so faster-whisper/PyAV can sniff the codec
    # (.wma needs the right hint to decode correctly).
    suffix = ""
    for ext in (".wma", ".wav", ".m4a", ".mp3", ".mp4", ".mov", ".dcr"):
        if filename_hint.lower().endswith(ext):
            suffix = ext
            break
    with tempfile.NamedTemporaryFile(suffix=suffix or ".bin", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        # Use chunked path: auto-detects duration and chunks when needed.
        # For short audio it just calls transcribe_local() directly.
        result = transcribe_local_chunked(tmp_path)
        if result and result.text.strip():
            return result
        logger.warning("Local Whisper produced empty/failed result for %s — trying OpenAI", filename_hint)
        result = transcribe_openai(tmp_path)
        if result and result.text.strip():
            return result
        raise RuntimeError(
            "Transcription failed: faster-whisper unavailable or produced no text, "
            "and OPENAI_API_KEY is unset / API call failed."
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ─── YouTube captions ───────────────────────────────────────────────

def _transcribe_youtube_captions(video_id: str, prefer_langs: tuple[str, ...]) -> TranscriptionResult:
    """Fast path: pull existing captions via youtube-transcript-api.

    Returns instantly on success. Raises RuntimeError when YouTube blocks
    the IP (datacenter/AWS), captions are disabled, or no language match —
    caller is expected to fall through to yt-dlp + Whisper."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            TranscriptsDisabled, NoTranscriptFound,
        )
    except ImportError as e:
        raise RuntimeError(f"youtube-transcript-api not installed: {e}")

    ytt = YouTubeTranscriptApi()
    try:
        transcript_list = ytt.list(video_id)
        try:
            transcript = transcript_list.find_manually_created_transcript(list(prefer_langs))
            engine = "youtube-captions-manual"
        except NoTranscriptFound:
            transcript = transcript_list.find_generated_transcript(list(prefer_langs))
            engine = "youtube-captions-auto"
        raw = transcript.fetch()
    except TranscriptsDisabled as e:
        raise RuntimeError(f"YouTube has captions disabled for {video_id}: {e}")
    except NoTranscriptFound as e:
        raise RuntimeError(f"No usable transcript found for {video_id}: {e}")
    except Exception as e:
        raise RuntimeError(f"YouTube transcript fetch failed for {video_id}: {e}")

    segs: list[TranscriptSegment] = []
    last_end = 0.0
    iterable = getattr(raw, "snippets", None) or raw
    for entry in iterable:
        if hasattr(entry, "start"):
            start = float(entry.start)
            dur = float(getattr(entry, "duration", 0.0))
            text = (entry.text or "").replace("\n", " ").strip()
        else:  # 0.6.x dict fallback
            start = float(entry.get("start", 0.0))
            dur = float(entry.get("duration", 0.0))
            text = (entry.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        segs.append(TranscriptSegment(start=start, end=start + dur, text=text))
        last_end = max(last_end, start + dur)
    return TranscriptionResult(
        text=_segments_to_timestamped_text(segs),
        segments=segs,
        engine=engine,
        language=getattr(transcript, "language_code", None) or "en",
        duration_seconds=last_end,
    )


def _download_youtube_audio(video_id: str) -> tuple[bytes, str]:
    """Use yt-dlp to fetch the best audio-only stream as bytes.

    Returns (audio_bytes, filename_hint). YouTube's default web-client
    player has been gating datacenter IPs behind a 'Sign in to confirm
    you're not a bot' challenge — yt-dlp gets the cookie-less variant
    rejected. The mobile/TV clients are still open in most cases, so we
    try them in order: ios → tv_embedded → android → mweb → web. First
    one that produces a stream wins. Each retry takes ~1s when refused
    so total overhead is bounded.

    Optional: if $YT_COOKIES_PATH exists (Netscape-
    format YouTube cookies exported from a logged-in browser), yt-dlp
    uses it. That sidesteps the bot challenge entirely but rotates with
    the user's YouTube session, so it's a fallback for cases where the
    client trick stops working.
    """
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError(f"yt-dlp not installed: {e}")

    cookies_path = os.getenv("YT_COOKIES_PATH", "/opt/civic-lens/yt-cookies.txt")
    has_cookies = os.path.exists(cookies_path)

    # Order matters: ios + tv_embedded historically dodge the bot check.
    # web is last (most likely to fail). If cookies are present we still
    # try the cheap clients first; cookies-on-web is the last resort.
    player_clients = ["ios", "tv_embedded", "android", "mweb", "web"]
    last_err: Exception | None = None
    with tempfile.TemporaryDirectory(prefix="ahmt_yt_") as td:
        for client in player_clients:
            out_template = os.path.join(td, f"audio_{client}.%(ext)s")
            ydl_opts = {
                # bestaudio sometimes returns "format not available" for the
                # ios/tv_embedded clients (they advertise HLS / non-default
                # audio codecs). Fall through to plain "best" (the full
                # video+audio stream) which always exists — we feed the
                # bytes to PyAV/Whisper which extracts audio regardless.
                "format": "bestaudio[acodec!=none]/bestaudio/best",
                "outtmpl": out_template,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "writeinfojson": False,
                "writethumbnail": False,
                "extractor_args": {"youtube": {"player_client": [client]}},
            }
            if has_cookies:
                ydl_opts["cookiefile"] = cookies_path
            url = f"https://www.youtube.com/watch?v={video_id}"
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.extract_info(url, download=True)
            except Exception as e:
                last_err = e
                logger.debug("yt-dlp %s client failed for %s: %s",
                             client, video_id, str(e)[:200])
                continue
            for fname in os.listdir(td):
                if fname.startswith(f"audio_{client}."):
                    path = os.path.join(td, fname)
                    logger.info("yt-dlp succeeded for %s via player_client=%s",
                                video_id, client)
                    with open(path, "rb") as fh:
                        return fh.read(), fname

    raise RuntimeError(
        f"yt-dlp audio download failed for {video_id} across all player_clients "
        f"({', '.join(player_clients)}). Last error: {last_err}"
    )


def transcribe_youtube(video_id: str, prefer_langs: tuple[str, ...] = ("en", "en-US")) -> TranscriptionResult:
    """Get a transcript for a YouTube video.

    Two paths:
      1. captions API (fast, ~1s) — works from residential IPs
      2. yt-dlp audio download + faster-whisper (~realtime) — works from
         datacenter IPs where YouTube blocks the captions endpoint

    Tries (1) first; falls back to (2) on any error so prod gets transcripts
    even from the EC2 IP block and local dev stays fast.
    """
    try:
        return _transcribe_youtube_captions(video_id, prefer_langs)
    except RuntimeError as e:
        logger.warning(
            "YouTube captions API unavailable for %s (%s); falling back to yt-dlp + Whisper",
            video_id, str(e)[:200],
        )

    audio_bytes, fname = _download_youtube_audio(video_id)
    result = transcribe_audio_bytes(audio_bytes, filename_hint=fname)
    # Tag the engine string so we can tell apart from town-audio transcriptions
    # in stats/debugging.
    return TranscriptionResult(
        text=result.text,
        segments=result.segments,
        engine=f"yt-dlp+{result.engine}",
        language=result.language,
        duration_seconds=result.duration_seconds,
    )
