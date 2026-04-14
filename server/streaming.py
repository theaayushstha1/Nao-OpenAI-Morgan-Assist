"""SSE helpers: split a stream of text chunks into complete sentences."""
from __future__ import annotations

import re
from typing import Iterable, Iterator

# Common abbreviations that shouldn't end a sentence.
_ABBR = {"dr.", "mr.", "mrs.", "ms.", "prof.", "e.g.", "i.e.", "etc.", "vs.", "st.", "no."}


def _is_abbreviation_end(text: str) -> bool:
    """Return True if text ends with a known abbreviation (case-insensitive)."""
    lower = text.lower()
    return any(lower.endswith(a) for a in _ABBR)


def iter_sentences(chunks: Iterable[str]) -> Iterator[str]:
    """Yield complete sentences from a stream of text fragments."""
    buf = ""
    for chunk in chunks:
        buf += chunk
        # Try to extract complete sentences from the buffer.
        while True:
            m = re.search(r"[.!?](\s|$)", buf)
            if not m:
                break
            # candidate includes the terminating punctuation
            end = m.start() + 1
            candidate = buf[:end].strip()
            if _is_abbreviation_end(candidate):
                # This period belongs to an abbreviation; advance past it and
                # keep looking for a real sentence boundary.
                search_start = m.end()
                next_m = re.search(r"[.!?](\s|$)", buf[search_start:])
                if not next_m:
                    break
                # Recalculate with the new match position
                abs_start = search_start + next_m.start()
                abs_end_char = abs_start + 1  # include punctuation
                candidate = buf[:abs_end_char].strip()
                if _is_abbreviation_end(candidate):
                    break
                yield candidate
                buf = buf[search_start + next_m.end():].lstrip()
            else:
                yield candidate
                buf = buf[m.end():].lstrip()
    if buf.strip():
        yield buf.strip()
