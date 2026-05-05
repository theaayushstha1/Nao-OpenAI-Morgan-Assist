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


_EARLY_FLUSH_MIN_CHARS = 20  # flush on , ; : after this many chars
_EARLY_FLUSH_PUNCT = re.compile(r"[,;:]\s")


def iter_sentences(chunks: Iterable[str]) -> Iterator[str]:
    """Yield complete sentences (or phrase-sized chunks) from a stream of text.

    Optimised for low first-audio latency: in addition to sentence-ending
    punctuation, flush early on a comma/semicolon/colon once the buffer has
    enough chars to sound like a natural phrase. Subsequent fragments still
    flow through the same gate so prosody doesn't degrade for long replies.
    """
    buf = ""
    for chunk in chunks:
        buf += chunk
        while True:
            m = re.search(r"[.!?](\s|$)", buf)
            if not m:
                # No sentence boundary yet — try an earlier prosodic break
                # (comma/semicolon/colon) once we have enough text.
                if len(buf) >= _EARLY_FLUSH_MIN_CHARS:
                    em = _EARLY_FLUSH_PUNCT.search(buf, _EARLY_FLUSH_MIN_CHARS)
                    if em:
                        end = em.start() + 1
                        candidate = buf[:end].strip()
                        yield candidate
                        buf = buf[em.end():].lstrip()
                        continue
                break
            end = m.start() + 1
            candidate = buf[:end].strip()
            if _is_abbreviation_end(candidate):
                search_start = m.end()
                next_m = re.search(r"[.!?](\s|$)", buf[search_start:])
                if not next_m:
                    break
                abs_start = search_start + next_m.start()
                abs_end_char = abs_start + 1
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
