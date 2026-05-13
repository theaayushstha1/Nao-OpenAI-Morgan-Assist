"""Deterministic pacing for spoken breathing counts.

Prompt-only pacing is not reliable enough for NAO. TTS providers may ignore
SSML break tags, and "one two three four" is usually spoken in under a second.
This module expands breath-count text into small TTS chunks plus explicit
post-chunk pauses that the robot enforces locally.
"""
from __future__ import annotations

import re
from typing import Iterable

DEFAULT_COUNT_PAUSE_MS = 800
MAX_PAUSE_MS = 6000

_BREAK_RE = re.compile(
    r"<break\s+time=[\"'](?P<value>\d+(?:\.\d+)?)(?P<unit>ms|s)[\"']\s*/?>",
    re.IGNORECASE,
)
_BREATH_CONTEXT_RE = re.compile(
    r"\b(?:breath|breathe|breathing|inhale|exhale|hold)\b",
    re.IGNORECASE,
)
_NUM_WORDS = {
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "one": "one",
    "two": "two",
    "three": "three",
    "four": "four",
    "five": "five",
    "six": "six",
    "seven": "seven",
    "eight": "eight",
    "nine": "nine",
}
_COUNT_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in sorted(_NUM_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _pause_to_ms(value: str, unit: str) -> int:
    try:
        raw = float(value)
    except Exception:
        return DEFAULT_COUNT_PAUSE_MS
    ms = int(raw if unit.lower() == "ms" else raw * 1000.0)
    return max(0, min(MAX_PAUSE_MS, ms))


def _clean_segment(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.strip(" ,;")


def _count_word(token: str) -> str:
    return _NUM_WORDS.get((token or "").strip().lower(), token.strip())


def _looks_count_only(text: str) -> bool:
    stripped = re.sub(r"[.,;:!?]+", " ", text or "").strip()
    if not stripped:
        return False
    tokens = stripped.split()
    return len(tokens) >= 3 and all(t.lower() in _NUM_WORDS for t in tokens)


def _expand_ssml_breaks(text: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    pos = 0
    for match in _BREAK_RE.finditer(text):
        segment = _clean_segment(text[pos:match.start()])
        pause_ms = _pause_to_ms(match.group("value"), match.group("unit"))
        if segment:
            out.append((segment, pause_ms))
        elif out:
            prev_text, prev_pause = out[-1]
            out[-1] = (prev_text, max(prev_pause, pause_ms))
        pos = match.end()
    tail = _clean_segment(text[pos:])
    if tail and not re.fullmatch(r"[.?!:;,\-]+", tail):
        out.append((tail, 0))
    return out or [(_clean_segment(_BREAK_RE.sub("", text)), 0)]


def _count_runs(tokens: list[re.Match[str]], text: str) -> Iterable[tuple[int, int]]:
    i = 0
    while i < len(tokens):
        j = i + 1
        while j < len(tokens):
            between = text[tokens[j - 1].end():tokens[j].start()]
            if not re.fullmatch(r"[\s,]+", between or ""):
                break
            j += 1
        if j - i >= 3:
            yield i, j
        i = max(j, i + 1)


def _expand_count_text(text: str) -> list[tuple[str, int]]:
    tokens = list(_COUNT_TOKEN_RE.finditer(text or ""))
    if len(tokens) < 3:
        return [(_clean_segment(text), 0)]
    if not (_BREATH_CONTEXT_RE.search(text or "") or _looks_count_only(text)):
        return [(_clean_segment(text), 0)]

    out: list[tuple[str, int]] = []
    pos = 0
    expanded_any = False
    for start, end in _count_runs(tokens, text):
        run = tokens[start:end]
        prefix = _clean_segment(text[pos:run[0].start()])
        if prefix:
            first = _clean_segment(prefix + " " + _count_word(run[0].group(0)))
        else:
            first = _count_word(run[0].group(0))
        out.append((first, DEFAULT_COUNT_PAUSE_MS))
        for tok in run[1:]:
            out.append((_count_word(tok.group(0)), DEFAULT_COUNT_PAUSE_MS))
        pos = run[-1].end()
        expanded_any = True

    if not expanded_any:
        return [(_clean_segment(text), 0)]

    tail = _clean_segment(text[pos:])
    if tail and not re.fullmatch(r"[.?!:;,\-]+", tail):
        out.append((tail, 0))
    return [(segment, pause) for segment, pause in out if segment]


def expand_tts_pacing(text: str) -> list[tuple[str, int]]:
    """Return ``(tts_text, pause_after_ms)`` chunks for a sentence.

    Non-breathing text returns one chunk with zero pause. Breathing counts
    return separate chunks so NAO's local queue can enforce the timing.
    """
    cleaned = _clean_segment(text)
    if not cleaned:
        return []
    if _BREAK_RE.search(cleaned):
        return _expand_ssml_breaks(cleaned)
    return _expand_count_text(cleaned)
