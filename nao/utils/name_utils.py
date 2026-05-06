# -*- coding: utf-8 -*-
from __future__ import print_function
import re


_NON_NAMES = set([
    "the", "a", "an", "my", "is", "am", "i", "im", "i'm",
    "hey", "hi", "hello", "there", "before", "quick", "intro",
    "what", "should", "call", "you", "me", "look", "face", "name",
    "just", "this", "that", "it", "guest", "nao",
])


def _clean_token(token):
    return re.sub(r"[^A-Za-z]", "", token or "")


def _valid_name(token):
    token = _clean_token(token)
    return bool(token and len(token) > 1 and token.lower() not in _NON_NAMES)


def extract_name(text):
    """Extract a person's name from transcribed speech.

    Tries pattern matching first, then falls back to the first word.
    Filters out common non-name words.
    """
    if not text:
        return None
    text = text.strip()
    patterns = [
        # Includes bare "name is X" because users often drop the "my" when
        # answering NAO's prompt — "Name is Ayush" was being thrown out.
        r"(?:my name is|the name is|name'?s|name is|i am|i'?m|call me|this is|i'?m called|i go by|just call me|it'?s)\s+([A-Za-z]+)",
        r"^([A-Za-z]+)[\s.!?]*$",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            token = _clean_token(m.group(1))
            if _valid_name(token):
                return token.capitalize()
    # Do not fall back to the first word of an arbitrary sentence. NAO's own
    # prompt echo often starts with "Hey there..." and was being learned as
    # the user's face name. Standalone one-word names are already accepted by
    # the anchored pattern above.
    return None
