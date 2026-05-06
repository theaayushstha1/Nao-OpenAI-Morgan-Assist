"""OpenAI TTS helper for sentence-level synthesis.

Used by /stream_turn and /tts to convert text to speech.
Returns raw MP3 bytes; the SSE generator base64-encodes them and ships to
NAO, which plays via ALAudioPlayer.

tts-1 is the low-latency model (~200-400ms per short sentence).
tts-1-hd is higher quality but ~2x slower — set OPENAI_TTS_MODEL=tts-1-hd
in .env if quality matters more than speed.
"""
from __future__ import annotations

import logging

from openai import OpenAI

from server import config

_log = logging.getLogger("sage.openai_tts")
_client = OpenAI(api_key=config.OPENAI_API_KEY)


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
        return resp.content
    except Exception as e:  # noqa: BLE001
        _log.warning("[openai_tts] synthesis failed: %s", e)
        return None
