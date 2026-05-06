"""Minimal ElevenLabs TTS helper for sentence-level synthesis.

Used by /stream_turn when ELEVENLABS_API_KEY + VOICE_ID are configured.
Returns raw MP3 bytes for a sentence; the SSE generator base64-encodes them
and ships to NAO, which plays the file via ALAudioPlayer.

Why turbo_v2_5: lowest-latency model that still supports voice cloning.
Generation time ≈ 0.4-0.8s per short sentence at 22 kHz.
"""
from __future__ import annotations

import logging

import requests

from server import config

_log = logging.getLogger("sage.elevenlabs")

_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


def synthesize(text: str, *, output_format: str = "mp3_22050_32") -> bytes | None:
    """Generate a voice-cloned MP3 for `text`. Returns None on failure.

    Tuned for low latency: flash_v2_5 model + 22 kHz / 32 kbps output is
    typically 300-500 ms per short sentence end-to-end.
    """
    if not text or not text.strip():
        return None
    if not config.USE_ELEVENLABS:
        return None

    url = _URL_TEMPLATE.format(voice_id=config.ELEVENLABS_VOICE_ID)
    # `optimize_streaming_latency=4` aggressively cuts time-to-first-byte
    # at the cost of small quality drops — fine for short conversational
    # replies. Pair with output_format on the URL so we get back smaller
    # chunks faster.
    url += "?optimize_streaming_latency=4&output_format=" + output_format
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": config.ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": 0.40,         # lower = more dynamic/faster, less consistent
            "similarity_boost": 0.85,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as e:
        _log.warning("[elevenlabs] request failed: %s", e)
        return None
    if resp.status_code != 200:
        _log.warning("[elevenlabs] HTTP %s: %s", resp.status_code, resp.text[:200])
        return None
    return resp.content
