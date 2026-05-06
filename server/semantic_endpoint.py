"""Semantic endpointing — ask a small LLM whether a transcript looks complete.

Energy + Silero VAD only know "is there voice?"; they can't tell that
"I was going to say that" is mid-thought. We feed the partial transcript to
gpt-4.1-nano (fastest tier) and let it decide. If incomplete, the server
returns a wait signal so NAO records more audio.

Cached in-memory by transcript hash to avoid duplicate LLM calls on
identical inputs (e.g., when NAO retries after a wait).
"""
from __future__ import annotations

import hashlib
import logging
import os

from openai import OpenAI

from server import config

log = logging.getLogger("sage.semantic_endpoint")

USE_SEMANTIC_ENDPOINT = os.environ.get("USE_SEMANTIC_ENDPOINT", "1") == "1"
_MODEL = os.environ.get("SEMANTIC_ENDPOINT_MODEL", "gpt-4.1-nano")

_client = OpenAI(api_key=config.OPENAI_API_KEY)
_cache: dict[str, bool] = {}

_SYSTEM = (
    "You decide if a user's spoken sentence is a complete thought or if they "
    "are still mid-sentence. Reply with exactly 'yes' if complete, 'no' if "
    "they sound mid-thought (trailing off, incomplete clause). One word only."
)


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()


def is_complete_thought(transcript: str) -> bool:
    """True if the transcript looks like a complete user utterance.

    Defaults to True (don't wait) on errors or empty input — safer to run the
    agent than to leave the user hanging.
    """
    t = (transcript or "").strip()
    if not t:
        return True
    # Very short tokens are almost always complete or noise; either way the
    # downstream hallucination filter handles them.
    if len(t.split()) <= 2:
        return True
    key = _hash(t)
    if key in _cache:
        return _cache[key]
    try:
        resp = _client.chat.completions.create(
            model=_MODEL,
            temperature=0,
            max_tokens=2,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": t},
            ],
        )
        ans = (resp.choices[0].message.content or "").strip().lower()
        complete = not ans.startswith("n")
    except Exception as e:  # noqa: BLE001
        log.warning("semantic_endpoint: LLM call failed: %s", e)
        complete = True
    _cache[key] = complete
    return complete
