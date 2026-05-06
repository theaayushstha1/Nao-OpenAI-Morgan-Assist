# -*- coding: utf-8 -*-
"""Thin compatibility wrapper around utils.intent for the legacy `run` loop.

Single source of truth for exit phrases is utils.intent._EXIT_PATTERNS — this
module just re-exports a boolean helper so the older non-streaming flow
doesn't need to know about switch intents.
"""
from __future__ import print_function

from utils import intent as _intent

# Re-export so any callers that imported the old constants still work.
EXIT_PATTERNS = _intent._EXIT_PATTERNS
EXIT_KEYWORDS = list(_intent._EXIT_KEYWORDS)


def detect_exit_intent(text):
    """True iff `text` is a short, anchored exit/sleep phrase.

    Delegates to utils.intent.detect so the pattern set stays in lockstep —
    we don't want one path recognizing "go to sleep" and the other ignoring
    it after a future edit.
    """
    return _intent.detect(text) == "exit"
