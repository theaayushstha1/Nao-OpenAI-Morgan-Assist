# -*- coding: utf-8 -*-
from __future__ import print_function
import re

EXIT_PATTERNS = [
    r"^(goodbye|bye)$",
    r"\b(exit|quit|stop|end|goodbye|bye|close)\b.*\b(chat|mode|conversation|talking|session)\b",
    r"\b(chat|mode|conversation|talking|session)\b.*\b(exit|quit|stop|end|goodbye|bye|close)\b",
    r"^(exit|quit|stop now|end chat|goodbye|bye bye|that's all|that is all)$",
    r"^(i'm done|i am done|we're done|we are done)$",
    r"^(stop talking|stop listening|no more)$",
    r"\b(i (want|need) to (go|leave|stop)|let me (go|leave)|gotta go)\b",
    r"\b(talk to you later|catch you later|see you later)\b",
    r"\b(thanks.*bye|thank you.*bye|thanks.*good(bye)?)\b",
    r"\b(stop.*mode|exit.*mode|leave.*mode|quit.*mode)\b",
    r"\b(go back|return|switch back)\b.*\b(wake|main|menu)\b",
    r"\b(that'?s? (it|all|enough) (for (now|today))?)\b",
    r"\b(end (it|this|conversation|session) (now|here)?)\b",
    r"\b(i('m| am) (good|fine|ok|okay) (now|for now))\b",
]

EXIT_KEYWORDS = [
    "exit", "quit", "stop", "end", "goodbye", "bye", "close",
    "done", "finished", "that's all", "no more", "leave", "go back"
]

# Words that indicate "stop X" rather than exiting
_STOP_TARGET_WORDS = ("timer", "alarm", "music", "sound", "ringtone", "countdown")


def detect_exit_intent(text):
    """Check transcribed text for exit intent.

    Regex-based with guards against false positives like "stop the timer".
    """
    t = (text or "").strip().lower()
    if not t:
        return False

    # Don't exit for "stop the timer/music..."
    for tw in _STOP_TARGET_WORDS:
        if ("stop " + tw) in t or ("stop the " + tw) in t:
            return False

    for pattern in EXIT_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            print("[EXIT DETECTED] Pattern: {}".format(pattern))
            return True

    words = t.split()
    if len(words) <= 3:
        for keyword in EXIT_KEYWORDS:
            if keyword in words:
                print("[EXIT DETECTED] Keyword: {}".format(keyword))
                return True

    return False
