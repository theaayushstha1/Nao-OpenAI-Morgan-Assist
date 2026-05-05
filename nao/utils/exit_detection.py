# -*- coding: utf-8 -*-
from __future__ import print_function
import re

EXIT_PATTERNS = [
    r"^(goodbye|bye|bye bye)\.?$",
    r"^(exit|quit)\.?$",
    r"^(stop now|that's all|that is all)\.?$",
    r"^(i'm done|i am done|we're done|we are done)\.?$",
    r"^(stop talking|stop listening|no more)\.?$",
    r"^(end chat|exit chat|stop chat|leave chat)\.?$",
    r"^(end conversation|exit conversation|stop conversation)\.?$",
    r"^(thanks bye|thank you bye|thanks goodbye|thank you goodbye)\.?$",
    r"^(talk to you later|catch you later|see you later)\.?$",
    r"^(gotta go|i gotta go|i have to go|i need to go)\.?$",
]

EXIT_KEYWORDS = ["goodbye", "bye"]

# Words that indicate "stop X" rather than exiting
_STOP_TARGET_WORDS = ("timer", "alarm", "music", "sound", "ringtone", "countdown")


def detect_exit_intent(text):
    """Match only short, anchored exit phrases. Long sentences never exit
    (a casual mention of 'stop' or 'bye' inside a paragraph is not intent)."""
    t = (text or "").strip().lower().rstrip("!?.,")
    if not t:
        return False

    words = t.split()
    if len(words) > 6:
        return False

    for tw in _STOP_TARGET_WORDS:
        if ("stop " + tw) in t or ("stop the " + tw) in t:
            return False

    for pattern in EXIT_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            print("[EXIT DETECTED] Pattern: {}".format(pattern))
            return True

    if len(words) <= 2:
        for keyword in EXIT_KEYWORDS:
            if keyword in words:
                print("[EXIT DETECTED] Keyword: {}".format(keyword))
                return True

    return False
