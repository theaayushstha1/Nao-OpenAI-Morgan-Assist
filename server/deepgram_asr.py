"""Deepgram Nova-2 prerecorded ASR client.

NAO uploads a complete WAV file via multipart POST, so we use Deepgram's
synchronous /v1/listen endpoint (prerecorded) rather than the websocket
streaming API. Returns the top-1 transcript or "" on any failure (callers
already handle empty transcripts via _transcript_reject_reason).

nao-therapy: timeout was lowered from 15.0 -> 6.0 s. Most utterances
finalize in under 2 s; 15 s was way too generous and turned a slow
Deepgram into a 15-s wall-clock penalty per turn. We retry once on
timeout with a 3 s budget before giving up; if both attempts fail the
caller falls through to OpenAI Whisper.

Every call logs `stt_provider=deepgram stt_latency_ms=X stt_outcome=Y`
so we can trace which path served a given turn.
"""
from __future__ import annotations

import time

import requests

from server import config

_LISTEN_URL = "https://api.deepgram.com/v1/listen"

# Per-attempt timeouts. First try gets a generous budget (most realistic
# utterances finalize in <1.5 s including network); on timeout we retry
# with a tight budget so a wedged Deepgram doesn't burn another 15 s.
_TIMEOUT_PRIMARY_S = 6.0
_TIMEOUT_RETRY_S = 3.0

# Boost domain terms NAO and Morgan students actually say. Keyword:weight syntax.
_KEYWORDS = [
    "Morgan:5",
    "NAO:5",
    "CBT:5",
    "therapist:5",
    "Aayush:5",
]


def _log(outcome: str, latency_ms: float, **extra) -> None:
    fields = ["stt_provider=deepgram",
              "stt_outcome={0}".format(outcome),
              "stt_latency_ms={0:.1f}".format(latency_ms)]
    for k, v in extra.items():
        fields.append("{0}={1}".format(k, v))
    print("[stt] " + " ".join(fields), flush=True)


def _do_request(audio_bytes: bytes, timeout_s: float):
    """One Deepgram POST. Returns (transcript_or_empty, http_status, error_str)."""
    params = {
        "model": config.DEEPGRAM_MODEL,
        "language": config.DEEPGRAM_LANGUAGE,
        "smart_format": "true",
        "punctuate": "true",
    }
    headers = {
        "Authorization": "Token {0}".format(config.DEEPGRAM_API_KEY),
        "Content-Type": "audio/wav",
    }
    from urllib.parse import urlencode
    flat_params = list(params.items()) + [("keywords", k) for k in _KEYWORDS]
    url = _LISTEN_URL + "?" + urlencode(flat_params)
    try:
        resp = requests.post(url, headers=headers, data=audio_bytes,
                              timeout=timeout_s)
    except requests.exceptions.Timeout:
        return "", 0, "timeout"
    except Exception as e:
        return "", 0, "exception:{0!r}".format(e)

    if resp.status_code != 200:
        return "", resp.status_code, "non200:{0!r}".format(resp.text[:200])

    try:
        data = resp.json()
        alts = data["results"]["channels"][0]["alternatives"]
        if not alts:
            return "", resp.status_code, "empty_alts"
        return (alts[0].get("transcript") or "").strip(), resp.status_code, ""
    except Exception as e:
        return "", resp.status_code, "parse:{0!r}".format(e)


def transcribe(path: str) -> str:
    if not config.DEEPGRAM_API_KEY:
        return ""

    try:
        with open(path, "rb") as f:
            audio_bytes = f.read()
    except Exception as e:
        print("[stt] stt_provider=deepgram stt_outcome=read_failed "
              "error={0!r}".format(e), flush=True)
        return ""

    audio_kb = len(audio_bytes) // 1024

    # Primary attempt.
    t0 = time.perf_counter()
    transcript, status, err = _do_request(audio_bytes, _TIMEOUT_PRIMARY_S)
    primary_ms = (time.perf_counter() - t0) * 1000.0
    if transcript:
        _log("ok", primary_ms,
             attempt="primary", audio_kb=audio_kb, transcript_len=len(transcript))
        return transcript
    _log("fail", primary_ms,
         attempt="primary", status=status, err=err, audio_kb=audio_kb)

    # Only retry on timeout — non-200 / parse errors won't get better.
    if err != "timeout":
        return ""

    # Retry attempt.
    t1 = time.perf_counter()
    transcript, status, err = _do_request(audio_bytes, _TIMEOUT_RETRY_S)
    retry_ms = (time.perf_counter() - t1) * 1000.0
    if transcript:
        _log("ok", retry_ms,
             attempt="retry", audio_kb=audio_kb, transcript_len=len(transcript))
        return transcript
    _log("fail", retry_ms,
         attempt="retry", status=status, err=err, audio_kb=audio_kb)
    return ""
