"""OpenAI TTS helper for sentence-level synthesis.

Used by /stream_turn and /tts to convert text to speech.
Returns raw MP3 bytes; the SSE generator base64-encodes them and ships to
NAO, which plays via ALAudioPlayer.

tts-1 is the low-latency model (~200-400ms per short sentence).
tts-1-hd is higher quality but ~2x slower — set OPENAI_TTS_MODEL=tts-1-hd
in .env if quality matters more than speed.

NAO's speaker is small and OpenAI TTS peaks quite low. We pipe each MP3
through ffmpeg with a flat +N dB gain so the voice is actually audible
across a room. Disable by setting OPENAI_TTS_GAIN_DB=0.
"""
from __future__ import annotations

import logging
import os
import subprocess

from openai import OpenAI

from server import config

_log = logging.getLogger("sage.openai_tts")
_client = OpenAI(api_key=config.OPENAI_API_KEY)

_GAIN_DB = float(os.environ.get("OPENAI_TTS_GAIN_DB", "8"))
_FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")


def _amplify_mp3(mp3_bytes: bytes, gain_db: float) -> bytes:
    """Boost an MP3 by `gain_db` decibels. Returns input unchanged on any
    ffmpeg error so we never go silent because of a CLI hiccup.

    +6 dB ≈ 2x linear amplitude. +8 dB is louder still without obvious
    clipping on speech-rate content.
    """
    if gain_db <= 0:
        return mp3_bytes
    try:
        proc = subprocess.run(
            [_FFMPEG, "-loglevel", "error", "-i", "pipe:0",
             "-af", "volume={0}dB".format(gain_db),
             "-f", "mp3", "pipe:1"],
            input=mp3_bytes,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        _log.warning("[openai_tts] ffmpeg gain failed (rc=%s): %s",
                     proc.returncode,
                     proc.stderr[:200].decode("utf-8", "ignore") if proc.stderr else "")
        return mp3_bytes
    except (OSError, subprocess.TimeoutExpired) as e:
        _log.warning("[openai_tts] ffmpeg unavailable, skipping gain: %s", e)
        return mp3_bytes


def synthesize(text: str) -> bytes | None:
    """Generate an MP3 for `text` using OpenAI TTS. Returns None on failure."""
    if not text or not text.strip():
        return None
    if not config.USE_OPENAI_TTS:
        return None
    try:
        resp = _client.audio.speech.create(
            model=config.OPENAI_TTS_MODEL,
            voice=config.OPENAI_TTS_VOICE,
            input=text.strip(),
            response_format="mp3",
        )
        return _amplify_mp3(resp.content, _GAIN_DB)
    except Exception as e:  # noqa: BLE001
        _log.warning("[openai_tts] synthesis failed: %s", e)
        return None
