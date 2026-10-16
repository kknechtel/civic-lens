"""Convert recording audio to a browser-friendly format.

The town meetings publish .wma (Windows Media Audio) files — no major
browser plays these natively, so we transcode to .mp3 once at ingest
time and store the mp3 alongside the original in S3. Playback then
streams the mp3, transcription still uses the original.

Bitrate is intentionally low (~64 kbps mono) — these are spoken-word
council meetings, not music. A 90-minute meeting at 64k mono is ~43 MB
vs. ~80 MB at the original .wma encoding.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# Codecs that browsers play natively — no transcode needed.
NATIVE_PLAYBACK_EXTS = {".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".wav"}
# Codecs that need transcoding. .wma is the big one for AH town recordings.
NEEDS_TRANSCODE_EXTS = {".wma", ".wmv", ".asf", ".dcr"}


def needs_transcode(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in NEEDS_TRANSCODE_EXTS)


def is_native_playback(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in NATIVE_PLAYBACK_EXTS)


def transcode_to_mp3(audio_bytes: bytes, *, source_hint: str = "audio.wma") -> bytes:
    """Run audio_bytes through ffmpeg, return mp3 bytes.

    Raises RuntimeError if ffmpeg isn't on PATH or the conversion fails.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg not found on PATH. Install with `apt-get install ffmpeg` "
            "on prod or `winget install Gyan.FFmpeg` on Windows."
        )

    # Pick a suffix that matches the source so ffmpeg can sniff the codec.
    suffix = ""
    for ext in (".wma", ".wmv", ".asf", ".dcr", ".m4a", ".mp3", ".wav"):
        if source_hint.lower().endswith(ext):
            suffix = ext
            break
    if not suffix:
        suffix = ".bin"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as inp:
        inp.write(audio_bytes)
        in_path = inp.name
    out_path = in_path + ".mp3"

    try:
        # -ac 1: mono. -b:a 64k: 64 kbps. -ar 22050: lower sample rate for speech.
        # -loglevel error: don't pollute stdout. -y: overwrite existing.
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", in_path,
                "-vn",  # drop any video stream
                "-c:a", "libmp3lame", "-ac", "1", "-ar", "22050", "-b:a", "64k",
                out_path,
            ],
            capture_output=True, text=True, timeout=900,  # 15 min hard cap
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed (rc={proc.returncode}): {proc.stderr[:500]}"
            )
        with open(out_path, "rb") as fh:
            return fh.read()
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def derive_mp3_key(s3_key: str) -> str:
    """For 'recordings/foo/bar.wma' return 'recordings/foo/bar.mp3'."""
    base, _, ext = s3_key.rpartition(".")
    if base and "." in s3_key:
        return f"{base}.mp3"
    return f"{s3_key}.mp3"
