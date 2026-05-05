# -*- coding: utf-8 -*-
"""Intent detection: exit + mid-conversation mode switch.

Returns one of:
  - "exit"          → user said goodbye / "I'm done"
  - "switch:<mode>" → user asked to switch to chat/therapy/morgan/skills
  - None            → no intent detected; keep current turn flowing
"""
from __future__ import print_function
import re


# Short, anchored phrases that mean "I'm finished talking". Long sentences
# never count as exit.
_EXIT_PATTERNS = [
    r"^(goodbye|bye|bye bye)\.?$",
    r"^(exit|quit|stop now)\.?$",
    r"^(that'?s all|that is all|that'?s it for now)\.?$",
    r"^(i'?m done|i am done|we'?re done|we are done)\.?$",
    r"^(stop talking|stop listening|no more)\.?$",
    r"^(end chat|exit chat|stop chat|leave chat)\.?$",
    r"^(end conversation|exit conversation|stop conversation)\.?$",
    r"^(thanks bye|thank you bye|thanks goodbye|thank you goodbye)\.?$",
    r"^(talk to you later|catch you later|see you later)\.?$",
    r"^(gotta go|i gotta go|i have to go|i need to go)\.?$",
    r"^(all done|we are good|we'?re good|i'?m good)\.?$",
]

_EXIT_KEYWORDS = ("goodbye", "bye")


# Switch patterns: substring search. Order matters — therapy aliases first
# because "i need help" should map to therapy not chat.
_SWITCH_PATTERNS = [
    # Therapy
    (re.compile(r"\b(switch to|go to|enter|change to|let'?s go to)\s+(the\s+)?(therapy|therapist)( mode)?\b", re.I), "therapy"),
    (re.compile(r"\bi need (help|to talk|someone to talk to)\b", re.I), "therapy"),
    (re.compile(r"\btalk to (someone|a therapist)\b", re.I), "therapy"),
    (re.compile(r"\b(start|enter|begin)\s+therapy\b", re.I), "therapy"),
    # Morgan
    (re.compile(r"\b(switch to|go to|enter|change to)\s+(the\s+)?(morgan|chatbot|morgan assist)( mode)?\b", re.I), "morgan"),
    (re.compile(r"\b(ask|tell me) about morgan\b", re.I), "morgan"),
    # Chat
    (re.compile(r"\b(switch to|go to|enter|change to)\s+(the\s+)?chat( mode)?\b", re.I), "chat"),
    (re.compile(r"\blet'?s (just\s+)?chat\b", re.I), "chat"),
    # Skills
    (re.compile(r"\b(switch to|go to|enter|change to)\s+(the\s+)?(skills?|mini ?nao)( mode)?\b", re.I), "skills"),
]

# Words that look like "stop X" but are not exit (e.g. "stop the timer")
_STOP_TARGET_WORDS = ("timer", "alarm", "music", "sound", "ringtone", "countdown")


def detect(text, current_mode=None):
    """Return 'exit', 'switch:<mode>', or None.

    current_mode (string) is excluded from switch matches so e.g. saying
    'switch to chat' while already in chat does nothing. Normalises trivial
    punctuation; does NOT strip semantic content.
    """
    t = (text or "").strip().lower().rstrip("!?.,")
    if not t:
        return None

    # Don't exit on "stop the timer" etc.
    for tw in _STOP_TARGET_WORDS:
        if ("stop " + tw) in t or ("stop the " + tw) in t:
            pass  # fall through to switch detection

    words = t.split()

    # EXIT: only short anchored phrases (≤6 words) so a long monologue
    # mentioning "bye" mid-sentence doesn't tear the turn down.
    if len(words) <= 6:
        for pattern in _EXIT_PATTERNS:
            if re.search(pattern, t, re.I):
                return "exit"
        if len(words) <= 2:
            for kw in _EXIT_KEYWORDS:
                if kw in words:
                    return "exit"

    # SWITCH: substring patterns — work in long monologues too.
    for pattern, target in _SWITCH_PATTERNS:
        if pattern.search(t):
            if target == current_mode:
                continue
            return "switch:" + target

    return None
