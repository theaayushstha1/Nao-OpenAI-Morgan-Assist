"""Deepgram Nova-2 prerecorded ASR client.

NAO uploads a complete WAV file via multipart POST, so we use Deepgram's
synchronous /v1/listen endpoint (prerecorded) rather than the websocket
streaming API. Returns the top-1 transcript or "" on any failure (callers
already handle empty transcripts via _transcript_reject_reason).
"""
from __future__ import annotations

import requests

from server import config

_LISTEN_URL = "https://api.deepgram.com/v1/listen"
_TIMEOUT_S = 15.0

# Boost domain terms NAO and Morgan students actually say. Keyword:weight syntax.
_KEYWORDS = [
    "Morgan:5",
    "NAO:5",
    "CBT:5",
    "therapist:5",
    "Aayush:5",
]


def transcribe(path: str) -> str:
    if not config.DEEPGRAM_API_KEY:
        return ""

    params = {
        "model": config.DEEPGRAM_MODEL,
        "language": config.DEEPGRAM_LANGUAGE,
        "smart_format": "true",
        "punctuate": "true",
    }
    # requests serializes repeated `keywords` query params correctly when
    # given a list value with the same key.
    headers = {
        "Authorization": "Token {0}".format(config.DEEPGRAM_API_KEY),
        "Content-Type": "audio/wav",
    }

    try:
        with open(path, "rb") as f:
            audio_bytes = f.read()
        # Build the URL manually so we can append multiple `keywords` params.
        from urllib.parse import urlencode
        flat_params = list(params.items()) + [("keywords", k) for k in _KEYWORDS]
        url = _LISTEN_URL + "?" + urlencode(flat_params)
        resp = requests.post(url, headers=headers, data=audio_bytes, timeout=_TIMEOUT_S)
    except Exception as e:
        print("[deepgram] request failed: {0!r}".format(e), flush=True)
        return ""

    if resp.status_code != 200:
        print(
            "[deepgram] non-200 status={0} body={1!r}".format(
                resp.status_code, resp.text[:300],
            ),
            flush=True,
        )
        return ""

    try:
        data = resp.json()
        alts = data["results"]["channels"][0]["alternatives"]
        if not alts:
            return ""
        return (alts[0].get("transcript") or "").strip()
    except Exception as e:
        print("[deepgram] parse failed: {0!r}".format(e), flush=True)
        return ""
