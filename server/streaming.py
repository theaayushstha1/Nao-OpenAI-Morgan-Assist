"""Streaming TTS helpers.

Two layers of public API live here:

1. **Legacy sync sentence splitter** — ``iter_sentences`` / ``strip_pacing_tags``.
   Used by the old Flask SSE path (``/stream_turn``). Preserved verbatim so
   pre-Phase-1 callers keep working while ``server/server.py`` is still on the
   tree.

2. **Phase 1 async streaming chunker** — ``chunk_for_tts`` and
   ``synthesize_chunks_parallel``. Designed to minimise the gap between the
   first LLM token and audible audio:

       LLM tokens  ──►  chunk_for_tts  ──►  synthesize_chunks_parallel
       (asyncio)        (sentence units)     (bounded thread pool, in order)

   The chunker hands a sentence-sized fragment to the synthesiser the moment
   it sees a terminator (``.``, ``!``, ``?``, ``:``) past ``min_chars``. The
   synthesiser fans out up to ``max_concurrency`` synthesis calls in parallel
   while still yielding ``(text, mp3_bytes)`` to the consumer in original
   order — so the listener hears sentence 1 the instant it is ready, even if
   sentence 2 finished synthesising first.

   Tunables (read at import time, env-driven so deployment can adjust without
   a code change):

       TTS_CHUNK_MIN_CHARS     default  24  — don't emit below this
       TTS_CHUNK_TIMEOUT_MS    default 250  — flush partial chunk on stall

The chunker is deliberately conservative about *where* it splits:

* Inside parentheses, brackets, quotes (single or double), or fenced code
  blocks → never split.
* After ``Mr.``, ``Dr.``, ``e.g.`` etc. → not a sentence end.
* After a digit (``3.14``, ``v1.2``) → not a sentence end.
* On ``,`` / ``;`` only when the buffer is already > 120 chars (soft break for
  long sentences so prosody doesn't suffer).

Whitespace is collapsed and trimmed per emitted chunk.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import AsyncIterator, Iterable, Iterator

# ---------------------------------------------------------------------------
# Legacy sync API — preserved verbatim. /stream_turn in server/server.py and a
# handful of tests in server/tests/ still import these names.
# ---------------------------------------------------------------------------

# Common abbreviations that shouldn't end a sentence.
_ABBR = {"dr.", "mr.", "mrs.", "ms.", "prof.", "e.g.", "i.e.", "etc.", "vs.", "st.", "no."}

# Pacing tags the therapy agents may append (e.g. "tts_pacing: slow").
# Strip them before TTS so the robot doesn't literally read them aloud.
_PACING_TAG = re.compile(r"(?im)^\s*tts_pacing\s*:\s*\w+\s*$")
_PACING_INLINE = re.compile(r"(?i)\btts_pacing\s*:\s*\w+\b")


def strip_pacing_tags(text: str) -> str:
    """Remove tts_pacing markers from a reply so they aren't spoken."""
    text = _PACING_TAG.sub("", text)
    text = _PACING_INLINE.sub("", text)
    return text


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
        buf += strip_pacing_tags(chunk)
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
        out = strip_pacing_tags(buf).strip()
        if out:
            yield out


# ---------------------------------------------------------------------------
# Phase 1 async streaming chunker
# ---------------------------------------------------------------------------

# Env-tunable defaults. Read at import time; the documented call-site default
# is the same number, so a downstream caller passing its own value still wins.
_DEFAULT_MIN_CHARS = int(os.environ.get("TTS_CHUNK_MIN_CHARS", "24"))
_DEFAULT_TIMEOUT_MS = int(os.environ.get("TTS_CHUNK_TIMEOUT_MS", "250"))

# Buffer length above which a soft break (`,` / `;`) is allowed.
_SOFT_BREAK_AFTER = 120

# Sentence terminators we accept. ":" is treated like a soft terminator —
# allowed only past min_chars and respecting the same brackets/abbr rules.
_HARD_TERMINATORS = {".", "!", "?"}
_SOFT_TERMINATORS = {":"}
_COMMA_TERMINATORS = {",", ";"}

# Whitespace collapsing.
_WS_RUN = re.compile(r"\s+")


def _collapse_ws(s: str) -> str:
    """Collapse runs of whitespace and strip ends."""
    return _WS_RUN.sub(" ", s).strip()


def _looks_like_decimal_or_version(buf: str, dot_idx: int) -> bool:
    """``True`` if the ``.`` at ``dot_idx`` is between digits (3.14, v1.2)."""
    if dot_idx <= 0 or dot_idx >= len(buf) - 1:
        return False
    left = buf[dot_idx - 1]
    right = buf[dot_idx + 1]
    return left.isdigit() and right.isdigit()


def _ends_with_abbreviation(buf_up_to_and_incl_dot: str) -> bool:
    """``True`` if the substring ending at the dot is a known abbreviation.

    We look at the last alphabetic-run + ``.`` so ``Hello Mr.`` returns True
    but ``Hello world.`` returns False.
    """
    # Walk back from the dot to the start of the current word.
    dot = len(buf_up_to_and_incl_dot) - 1
    if dot < 0 or buf_up_to_and_incl_dot[dot] != ".":
        return False
    i = dot - 1
    while i >= 0 and (buf_up_to_and_incl_dot[i].isalpha() or buf_up_to_and_incl_dot[i] == "."):
        i -= 1
    word = buf_up_to_and_incl_dot[i + 1:dot + 1].lower()
    return word in _ABBR


class _BracketState:
    """Track open parens, brackets, quotes, and code fences across tokens.

    Cheap state machine — increment/decrement counters per char so the chunker
    can ask ``inside_anything()`` before deciding to split.
    """

    __slots__ = ("paren", "bracket", "brace", "single_q", "double_q", "in_code")

    def __init__(self) -> None:
        self.paren = 0
        self.bracket = 0
        self.brace = 0
        self.single_q = False
        self.double_q = False
        self.in_code = False

    def feed(self, s: str) -> None:
        i = 0
        n = len(s)
        while i < n:
            c = s[i]
            # Triple-backtick fenced code block toggle.
            if not self.single_q and not self.double_q and s[i:i + 3] == "```":
                self.in_code = not self.in_code
                i += 3
                continue
            if self.in_code:
                i += 1
                continue
            if c == "(" and not self.single_q and not self.double_q:
                self.paren += 1
            elif c == ")" and not self.single_q and not self.double_q:
                if self.paren > 0:
                    self.paren -= 1
            elif c == "[" and not self.single_q and not self.double_q:
                self.bracket += 1
            elif c == "]" and not self.single_q and not self.double_q:
                if self.bracket > 0:
                    self.bracket -= 1
            elif c == "{" and not self.single_q and not self.double_q:
                self.brace += 1
            elif c == "}" and not self.single_q and not self.double_q:
                if self.brace > 0:
                    self.brace -= 1
            elif c == '"' and not self.single_q:
                self.double_q = not self.double_q
            elif c == "'" and not self.double_q:
                # Best-effort: don't toggle on contractions like "don't".
                # Heuristic — treat ' as a quote only when not between letters.
                left = s[i - 1] if i > 0 else " "
                right = s[i + 1] if i + 1 < n else " "
                if not (left.isalpha() and right.isalpha()):
                    self.single_q = not self.single_q
            i += 1

    def inside_anything(self) -> bool:
        return (
            self.paren > 0
            or self.bracket > 0
            or self.brace > 0
            or self.single_q
            or self.double_q
            or self.in_code
        )


def _find_split_point(buf: str, min_chars: int, state: _BracketState) -> int | None:
    """Return an index ``i`` such that ``buf[:i+1]`` is a clean chunk.

    Walks the buffer once, tracking the same bracket state the chunker carries
    across tokens (clone, not mutate). Returns the index of the *terminator*
    (so caller slices ``buf[:i+1]``). ``None`` if no valid split point exists.

    Priority: hard terminator past ``min_chars`` > soft (`:`) past ``min_chars``
    > comma/semicolon past ``_SOFT_BREAK_AFTER``. We always require the next
    char to be whitespace OR end-of-buffer, so ``3.14`` stays whole.
    """
    # Local bracket walker — start from the same state we already have, so a
    # paren that opened in a prior token still suppresses splits here.
    paren = state.paren
    bracket = state.bracket
    brace = state.brace
    single_q = state.single_q
    double_q = state.double_q
    in_code = state.in_code

    n = len(buf)
    soft_break_idx: int | None = None  # remember best comma/semicolon split

    i = 0
    while i < n:
        c = buf[i]
        # Track triple-backtick code fences.
        if not single_q and not double_q and buf[i:i + 3] == "```":
            in_code = not in_code
            i += 3
            continue
        if in_code:
            i += 1
            continue
        if c == "(" and not single_q and not double_q:
            paren += 1
        elif c == ")" and not single_q and not double_q:
            if paren > 0:
                paren -= 1
        elif c == "[" and not single_q and not double_q:
            bracket += 1
        elif c == "]" and not single_q and not double_q:
            if bracket > 0:
                bracket -= 1
        elif c == "{" and not single_q and not double_q:
            brace += 1
        elif c == "}" and not single_q and not double_q:
            if brace > 0:
                brace -= 1
        elif c == '"' and not single_q:
            double_q = not double_q
        elif c == "'" and not double_q:
            left = buf[i - 1] if i > 0 else " "
            right = buf[i + 1] if i + 1 < n else " "
            if not (left.isalpha() and right.isalpha()):
                single_q = not single_q

        inside = (
            paren > 0 or bracket > 0 or brace > 0
            or single_q or double_q or in_code
        )

        if not inside and (c in _HARD_TERMINATORS or c in _SOFT_TERMINATORS or c in _COMMA_TERMINATORS):
            # Splits only on whitespace OR end-of-buffer after the punct.
            next_is_space_or_end = (i + 1 == n) or buf[i + 1].isspace()
            if not next_is_space_or_end:
                i += 1
                continue
            # Reject decimals / version numbers.
            if c == "." and _looks_like_decimal_or_version(buf, i):
                i += 1
                continue
            # Reject abbreviations.
            if c == "." and _ends_with_abbreviation(buf[:i + 1]):
                i += 1
                continue
            chunk_len = i + 1
            if c in _HARD_TERMINATORS and chunk_len >= min_chars:
                return i
            if c in _SOFT_TERMINATORS and chunk_len >= min_chars:
                return i
            if c in _COMMA_TERMINATORS and chunk_len >= _SOFT_BREAK_AFTER:
                # Remember the *latest* soft break, but only return it if no
                # hard terminator turns up later in this buffer.
                soft_break_idx = i
        i += 1

    return soft_break_idx


async def chunk_for_tts(
    text_iter: AsyncIterator[str],
    min_chars: int = _DEFAULT_MIN_CHARS,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
) -> AsyncIterator[str]:
    """Yield TTS-ready sentence chunks from a streaming token source.

    See module docstring for the chunking rules. Empty input → empty output.
    On timeout while a partial chunk is buffered, flush whatever we have.
    """
    buffer = ""
    state = _BracketState()
    timeout_s = max(0.0, timeout_ms / 1000.0)
    aiter_obj = text_iter.__aiter__()

    while True:
        try:
            if buffer:
                # Have partial content — wait at most timeout_ms for next token.
                token = await asyncio.wait_for(aiter_obj.__anext__(), timeout=timeout_s)
            else:
                # Empty buffer — block indefinitely; nothing to flush.
                token = await aiter_obj.__anext__()
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            # Model paused. Flush whatever we have if non-empty AND not inside
            # something we mustn't split. If we *are* inside, hold and wait.
            if buffer and not state.inside_anything():
                cleaned = _collapse_ws(strip_pacing_tags(buffer))
                if cleaned:
                    yield cleaned
                buffer = ""
                state = _BracketState()
            # Loop back — keep polling.
            continue

        if not token:
            continue

        # Strip pacing markers per token so we never split across one.
        token = strip_pacing_tags(token)
        if not token:
            continue

        buffer += token
        # Update bracket state for everything we appended.
        state.feed(token)

        # Pull as many chunks as we can out of buffer in one tick — a single
        # token can contain multiple sentences ("Hi. Bye."), so we loop.
        # ``_find_split_point`` walks from index 0 of ``buffer``; the state at
        # that point is always fresh because we re-feed after every slice.
        while True:
            split_idx = _find_split_point(buffer, min_chars, _BracketState())
            if split_idx is None:
                break
            head = buffer[:split_idx + 1]
            tail = buffer[split_idx + 1:].lstrip()
            cleaned = _collapse_ws(strip_pacing_tags(head))
            if cleaned:
                yield cleaned
            buffer = tail
            # Recompute the carry-state for the *next* timeout decision.
            state = _BracketState()
            state.feed(buffer)

    # Stream exhausted — flush remainder.
    if buffer.strip():
        cleaned = _collapse_ws(strip_pacing_tags(buffer))
        if cleaned:
            yield cleaned


# ---------------------------------------------------------------------------
# Parallel synthesis with order preservation
# ---------------------------------------------------------------------------


async def synthesize_chunks_parallel(
    chunks: AsyncIterator[str],
    max_concurrency: int = 3,
) -> AsyncIterator[tuple[str, bytes]]:
    """Synthesise each chunk in a thread pool, yield results IN ORDER.

    Sentence ``i+1`` may finish synthesising before sentence ``i`` (TTS API
    latency varies). To keep the listener hearing sentence 1 first, results
    are buffered by sequence number and released only when the next-in-order
    one is ready.

    On per-chunk synthesis failure we log via structlog (when available) and
    skip that chunk — the others still flow.
    """
    sem = asyncio.Semaphore(max(1, max_concurrency))
    log = _get_logger()

    # Lazy import — avoid pulling the OpenAI client at import time of this
    # module (server/__init__.py imports streaming for iter_sentences).
    from server import openai_tts  # noqa: WPS433

    next_to_yield = 0
    pending: dict[int, asyncio.Task[tuple[str, bytes | None]]] = {}
    produced: dict[int, tuple[str, bytes | None]] = {}
    seq = 0
    intake_done = False
    consumer_intake = chunks.__aiter__()

    async def _synth(idx: int, text: str) -> tuple[str, bytes | None]:
        async with sem:
            try:
                # openai_tts.synthesize is sync (blocking HTTP + ffmpeg). Run
                # in a worker thread so we don't block the event loop while
                # other chunks are concurrently synthesising.
                mp3 = await asyncio.to_thread(openai_tts.synthesize, text)
                return text, mp3
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "tts_chunk_synth_failed",
                    chunk_idx=idx,
                    text_preview=text[:80],
                    error=str(exc),
                )
                return text, None

    async def _ingest_next() -> None:
        """Pull one chunk from the upstream iterator, schedule synthesis."""
        nonlocal seq, intake_done
        try:
            text = await consumer_intake.__anext__()
        except StopAsyncIteration:
            intake_done = True
            return
        if not text:
            return
        idx = seq
        seq += 1
        task = asyncio.create_task(_synth(idx, text))
        pending[idx] = task

    # Eagerly fill the pipeline so synthesis runs in parallel with intake.
    while not intake_done and len(pending) < max_concurrency:
        await _ingest_next()

    while pending or not intake_done:
        # Pull the next-in-order task only — we want order-preserving emission.
        if next_to_yield in pending:
            task = pending.pop(next_to_yield)
            text, mp3 = await task
            produced[next_to_yield] = (text, mp3)
        elif next_to_yield in produced:
            pass  # already done
        else:
            # The next-in-order chunk hasn't even been ingested yet.
            if intake_done and not pending:
                break
            await _ingest_next()
            # Top up the pipeline — any new arrivals can start synthesising.
            while not intake_done and len(pending) < max_concurrency:
                await _ingest_next()
            continue

        # Drain any contiguous run of completed entries starting at next_to_yield.
        while next_to_yield in produced:
            text, mp3 = produced.pop(next_to_yield)
            next_to_yield += 1
            if mp3 is None:
                # Synthesis failed — already logged; skip emission.
                continue
            yield text, mp3

        # After yielding, top up the pipeline to keep it busy.
        while not intake_done and len(pending) < max_concurrency:
            await _ingest_next()


# ---------------------------------------------------------------------------
# Logger plumbing
# ---------------------------------------------------------------------------


def _get_logger():
    """Return the project structlog logger, falling back to stdlib logging.

    The ``observability`` agent owns ``server/logging_setup.py`` and writes a
    structlog logger there. Until that lands we degrade gracefully so this
    module is independently testable.
    """
    try:
        from server.logging_setup import logger  # type: ignore[attr-defined]
        return logger
    except Exception:  # noqa: BLE001
        import logging

        class _Shim:
            _l = logging.getLogger("server.streaming")

            def warning(self, event: str, **kwargs: object) -> None:
                self._l.warning("%s %s", event, kwargs)

            def info(self, event: str, **kwargs: object) -> None:
                self._l.info("%s %s", event, kwargs)

            def error(self, event: str, **kwargs: object) -> None:
                self._l.error("%s %s", event, kwargs)

        return _Shim()


# ---------------------------------------------------------------------------
# Smoke test (run with: python server/streaming.py)
# ---------------------------------------------------------------------------


if __name__ == "__main__":  # pragma: no cover
    async def _fake_token_stream(text: str, chunk: int = 7, pause: float = 0.0):
        """Yield ``text`` in fixed-size slices to mimic an LLM token stream."""
        for i in range(0, len(text), chunk):
            yield text[i:i + chunk]
            if pause:
                await asyncio.sleep(pause)

    async def _slow_then_silent_stream():
        """Yield one short sentence, then go silent past the timeout."""
        yield "Hi there. "
        await asyncio.sleep(0.05)
        yield "Quick partial without a"  # no terminator — should flush via timeout
        await asyncio.sleep(0.6)
        # never yields a terminator before the loop ends

    async def main() -> None:
        sample = (
            "Hello there! I'm NAO, the friendly robot at Morgan State. "
            "Today is a great day, isn't it? "
            "Pi is 3.14, and Mr. Smith said e.g. \"keep going past abbreviations.\" "
            "Here is a list (with parens that span, including a comma) and we keep it whole. "
            "Sometimes the model writes a really long sentence that just keeps going and going, "
            "with multiple clauses and no early period, until finally it ends here. "
            "Code blocks like ```print('hi. bye.')``` should not split mid-fence. "
            "Done."
        )

        print("--- chunk_for_tts on a long paragraph ---")
        async for c in chunk_for_tts(_fake_token_stream(sample, chunk=11)):
            print(repr(c))

        print("\n--- chunk_for_tts under timeout (partial flush) ---")
        async for c in chunk_for_tts(_slow_then_silent_stream(), timeout_ms=200):
            print(repr(c))

        print("\n--- empty input ---")
        async def _empty():
            if False:
                yield ""
        out = [c async for c in chunk_for_tts(_empty())]
        print("count =", len(out))

        print("\n--- single short word, smaller than min_chars ---")
        async def _word():
            yield "Hi."
        out = [c async for c in chunk_for_tts(_word())]
        print(out)

        print("\n--- multiple sentences in one token ---")
        async def _bursty():
            yield "Hello there! How are you doing today? I am well."
        out = [c async for c in chunk_for_tts(_bursty(), min_chars=10)]
        print(out)

    asyncio.run(main())
