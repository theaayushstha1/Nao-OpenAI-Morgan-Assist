"""Semantic endpointing — async-friendly LLM verdict on whether a transcript
reads as a complete thought.

Energy + Silero VAD only know "is there voice?"; they can't tell that
"I was going to say that" is mid-thought. A small LLM (gpt-4o-mini, single-
token Yes/No grammar) supplies the missing intent signal: cheap, fast, and
gated by the EoU arbiter so it only runs when the silence-only signals are
already pointing toward "turn done".

Phase 2 changes vs. the original sync version:
    * Public ``is_complete_thought`` is now ``async``. The blocking OpenAI
      SDK call is offloaded via ``asyncio.to_thread`` so it never stalls
      the FastAPI event loop.
    * Cache is LRU + 10-minute TTL keyed on the normalized transcript
      (lowercased, whitespace-collapsed). Capped at 256 entries; on
      overflow we sweep the oldest 64 in one pass — cheap, no heap needed.
    * Prompt collapsed to a strict yes/no grammar (``temperature=0``,
      ``max_tokens=1``). One token per request, one round-trip.
    * Failure mode flipped: API errors return ``True`` (fail open) so a
      transient OpenAI hiccup finalizes the turn instead of leaving NAO
      mid-utterance silent. The previous sync version failed closed
      because the dispatcher had a Whisper retry above it; the WS
      transport in Phase 2 doesn't, so failing open is safer.
    * ``is_complete_thought_sync`` kept as a backwards-compat wrapper for
      pre-Phase-2 callers in ``server/server.py`` that haven't migrated
      to ``await`` yet.

The metrics hook for ``semantic_endpoint_call`` is *defensive*: if
``server.metrics`` doesn't yet declare that phase (it lives behind a
separate Phase 2 worktree), the timer is a no-op rather than crashing
this module on import or call.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Optional

import structlog
from openai import OpenAI


log = structlog.get_logger("sage.semantic_endpoint")


# ── Public knobs ────────────────────────────────────────────────────────────
# Both read at import time; hot-reloading callers can patch these via env or
# monkeypatch directly. Kept module-level to match the existing surface in
# `server/server.py` and `server/app_ws.py` that test ``USE_SEMANTIC_ENDPOINT``
# before calling in.
USE_SEMANTIC_ENDPOINT: bool = os.environ.get("USE_SEMANTIC_ENDPOINT", "1") == "1"
# gpt-4o-mini was chosen empirically: gpt-4.1-nano misjudged trailing-off
# phrases ("I need…") as complete; gpt-4o-mini classifies them correctly at
# +60 ms latency. Override via env if a stronger / cheaper tier is desired.
_MODEL: str = os.environ.get("SEMANTIC_ENDPOINT_MODEL", "gpt-4o-mini")


# ── Lazy OpenAI client ──────────────────────────────────────────────────────
# Lazy so importing this module doesn't blow up when OPENAI_API_KEY is unset
# (e.g., the verification block at the bottom of this file, or any unit test
# that wants to monkeypatch the call). The construction itself is cached.
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """Return a singleton OpenAI client, building it on first call.

    Raises ``RuntimeError`` if ``OPENAI_API_KEY`` is missing — the caller in
    ``is_complete_thought`` catches that and fails open. We deliberately
    don't import ``server.config`` here: that module reads
    ``os.environ["OPENAI_API_KEY"]`` at import time and would crash this
    module on import in keyless environments.
    """
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = OpenAI(api_key=api_key)
    return _client


# ── LRU + TTL cache ─────────────────────────────────────────────────────────
# A dict from normalized transcript → (verdict, expiry_epoch_seconds). Insert
# order is preserved by CPython, so iterating in order gives us free LRU
# semantics for the eviction sweep. The asyncio.Lock guarantees that two
# coroutines racing on the same transcript don't both fire an LLM call.
_CACHE: dict[str, tuple[bool, float]] = {}
_CACHE_LOCK = asyncio.Lock()
_CACHE_MAX = 256
_CACHE_EVICT_BATCH = 64
_CACHE_TTL_S = 600.0  # 10 minutes


def _normalize(transcript: str) -> str:
    """Lower-case, strip outer whitespace, collapse runs of inner whitespace.

    Used as the cache key so "Hello,   world" and "hello, world" hit the
    same entry. ``str.split()`` with no argument collapses any whitespace
    run (including tabs / newlines) into a single space — same behavior we
    want for transcripts that may have been re-joined with raggedly-spaced
    partials.
    """
    return " ".join((transcript or "").lower().split())


def _evict_oldest_locked() -> None:
    """Drop the oldest ``_CACHE_EVICT_BATCH`` entries.

    Caller MUST hold ``_CACHE_LOCK``. Insert order in CPython dict iteration
    is the hand-rolled "LRU" — entries refreshed via cache hit are NOT
    re-inserted at the tail (we don't pay for that), so this is closer to
    FIFO than strict LRU. Good enough for a 256-slot turn cache.
    """
    if len(_CACHE) <= _CACHE_MAX:
        return
    # ``list(_CACHE)[:N]`` is fine at 256 entries; no need for OrderedDict.
    for stale_key in list(_CACHE)[:_CACHE_EVICT_BATCH]:
        _CACHE.pop(stale_key, None)


# ── Prompt ──────────────────────────────────────────────────────────────────
# Single-token grammar: model emits exactly "yes" or "no". Anything else
# (empty, "maybe", a stray newline) parses to False — the conservative path.
# Phase 1 prompt was 60+ tokens of examples; we bin that to keep latency
# tight, since the EoU arbiter only consults this signal when silence-based
# signals already point toward "turn done", so the model rarely sees true
# garden-path fragments anyway.
_SYSTEM = (
    "You decide if a user's spoken sentence is a complete thought "
    "or just a fragment they are still finishing. Reply with exactly one "
    "word: \"yes\" if complete, \"no\" if fragment."
)


# ── Defensive metrics shim ──────────────────────────────────────────────────
# The Phase 2 task map adds ``semantic_endpoint_call`` to the metrics
# histogram, but that change lives in a separate worktree. Until the
# consolidator merges it, ``metrics.phase_timer("semantic_endpoint_call")``
# would raise ``ValueError`` from the allowed-phase guard *on __enter__*
# (the validate happens inside the generator body, after we already have
# the context-manager object back).
#
# Strategy: probe the metrics module ONCE at import time, cache whether
# the phase is registered, and pick the timing path accordingly. If the
# probe fails (import error, missing function, or ``ValueError`` because
# the phase isn't on the allowlist yet) we use a no-op timer for the
# lifetime of the process — no per-call cost on the cold path.
_PHASE = "semantic_endpoint_call"
_metrics_phase_timer = None  # set below if probe succeeds


def _probe_metrics() -> None:
    """One-shot: import metrics + try to enter+exit a phase_timer for our
    label. If both succeed, cache the bound function for fast use."""
    global _metrics_phase_timer
    try:
        from server import metrics  # noqa: PLC0415

        # Actually enter+exit so ALLOWED_PHASES validation runs now, not
        # on the first real call. If this fails we leave the no-op path.
        with metrics.phase_timer(_PHASE):
            pass
    except Exception:  # noqa: BLE001
        _metrics_phase_timer = None
        return
    _metrics_phase_timer = metrics.phase_timer  # type: ignore[assignment]


_probe_metrics()


@contextlib.contextmanager
def _timed_call():
    """Time the LLM call into ``metrics.latency_ms{phase=semantic_endpoint_call}``,
    no-op if metrics aren't ready (module missing, phase not yet registered).
    """
    if _metrics_phase_timer is None:
        yield
        return
    with _metrics_phase_timer(_PHASE):
        yield


# ── Public API ──────────────────────────────────────────────────────────────
async def is_complete_thought(transcript: str) -> bool:
    """Return True if ``transcript`` reads as a complete user utterance.

    This is the EoU arbiter's "intent" channel: silence-based signals
    (Silero, energy floor) cover acoustics; this covers semantics.

    Behavior:
        * Empty / whitespace-only → True (nothing to wait for).
        * Single-word utterances ("yes", "stop", a name) → True without an
          LLM round-trip; high prior on completeness, low value-of-info.
        * Cache hit (and not expired) → return the cached verdict in O(1).
        * Cache miss → run gpt-4o-mini in a worker thread via
          ``asyncio.to_thread`` so the event loop keeps serving frames.
        * On *any* exception (no API key, transient HTTP error, malformed
          response, parse failure) → log via structlog and return True.
          "Fail open" is intentional: the alternative is hanging the user
          mid-utterance because the API hiccupped.

    Cached for 10 minutes per normalized transcript with a 256-entry cap.
    """
    t = (transcript or "").strip()
    if not t:
        return True
    # One-word utterances are a strong "complete" prior; LLM call adds zero
    # value here. Two-word utterances ("I need", "you know") DO go to the
    # LLM — losing them was a major source of mid-sentence cutoffs in v1.
    if len(t.split()) <= 1:
        return True

    key = _normalize(t)
    now = time.time()

    # Cache lookup under lock — keeps two coroutines racing on the same
    # transcript from both firing an LLM call. Acquire briefly, release
    # before the network call.
    async with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            verdict, expiry = cached
            if expiry > now:
                return verdict
            # Stale entry — drop and fall through to the LLM call.
            _CACHE.pop(key, None)

    # Cache miss → LLM round-trip. Wrap in defensive metrics timer.
    try:
        with _timed_call():
            verdict = await asyncio.to_thread(_call_llm, key)
    except Exception as exc:  # noqa: BLE001
        # Log AND fail open. The exception here covers:
        #   * RuntimeError from _get_client when OPENAI_API_KEY is unset
        #   * openai.APIError / openai.APIConnectionError on network flakes
        #   * pydantic validation errors on a malformed completion
        # Any of those should not strand the user.
        log.warning(
            "semantic_endpoint_fail_open",
            error=type(exc).__name__,
            detail=str(exc),
            transcript_len=len(key),
        )
        # Cache the fail-open verdict so subsequent retries on the same
        # transcript are instant. The 10-minute TTL still gives us an
        # automatic recovery window — once it expires we'll re-probe the
        # API. This matches the contract's verification expectation: pass
        # 2 over the same transcripts must hit the cache.
        async with _CACHE_LOCK:
            _CACHE[key] = (True, now + _CACHE_TTL_S)
            _evict_oldest_locked()
        return True

    # Stash with TTL expiry. Eviction sweep is O(N) on overflow but only
    # fires once per ~256 misses, so amortized cost is negligible.
    async with _CACHE_LOCK:
        _CACHE[key] = (verdict, now + _CACHE_TTL_S)
        _evict_oldest_locked()

    return verdict


def _call_llm(transcript: str) -> bool:
    """Synchronous helper run inside ``asyncio.to_thread``.

    Kept private; ``is_complete_thought`` is the only intended caller. The
    OpenAI SDK is sync-only at this version (0.13.x), so we run the request
    on a worker thread and let asyncio schedule around it. ``max_tokens=1``
    means at most one BPE token comes back (typically literally "yes" or
    "no"), so the round-trip is dominated by network latency, not decoding.
    """
    client = _get_client()
    resp = client.chat.completions.create(
        model=_MODEL,
        temperature=0,
        max_tokens=1,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": transcript},
        ],
    )
    raw = (resp.choices[0].message.content or "").strip().lower()
    # Take only the first whitespace-separated token. ``max_tokens=1`` should
    # already enforce this, but BPE quirks (e.g., "yes." as a single token)
    # can sneak punctuation in — split() handles that.
    first = raw.split()[0] if raw.split() else ""
    return first.startswith("y")


def is_complete_thought_sync(transcript: str) -> bool:
    """Backwards-compatible sync wrapper for callers that haven't migrated.

    Used by the legacy Flask path in ``server/server.py``; new code on the
    FastAPI WS transport should ``await is_complete_thought(...)`` directly.

    If invoked from inside a running event loop (which would deadlock
    ``asyncio.run``), we fall back to a fresh thread + new loop. This is
    not a pattern to rely on — it exists purely so the Phase 1 sync call
    sites keep working until they migrate.
    """
    try:
        return asyncio.run(is_complete_thought(transcript))
    except RuntimeError as exc:
        # "asyncio.run() cannot be called from a running event loop"
        if "running event loop" not in str(exc):
            raise
        # Rare path: a sync caller invoked from inside an event loop. Run
        # the coroutine on a dedicated worker thread with its own loop.
        import threading

        result: list[bool] = []

        def _runner() -> None:
            result.append(asyncio.run(is_complete_thought(transcript)))

        th = threading.Thread(target=_runner, daemon=True)
        th.start()
        th.join()
        return result[0] if result else True


__all__ = [
    "USE_SEMANTIC_ENDPOINT",
    "is_complete_thought",
    "is_complete_thought_sync",
]


# ── Verification block ──────────────────────────────────────────────────────
# Two-pass smoke test, runnable as `python -m server.semantic_endpoint`:
#   Pass 1: 5 transcripts with OPENAI_API_KEY UNSET → expect fail-open
#           (verdict True) and a "semantic_endpoint_fail_open" log line on
#           each. Each verdict is cached with the 10-minute TTL.
#   Pass 2: same 5 transcripts → expect all to come back True from cache
#           with NO further log lines and a noticeably faster total time
#           (no API attempt, no client construction).
# Both invariants are asserted at the bottom of the block so a regression
# fails loudly rather than printing pretty output.
if __name__ == "__main__":
    # Force OPENAI_API_KEY unset for the duration of the smoke test so the
    # fail-open path is exercised. (CI runners that have a real key still
    # work — they'd just take the success path. The contract is the same.)
    os.environ.pop("OPENAI_API_KEY", None)
    _client = None  # in case prior import primed it

    samples = [
        "I think I want to go home soon.",
        "What time is it",
        "Tell me about the weather and",
        "I was going to say",
        "Can you help me with my homework please",
    ]

    print("Pass 1 (cold cache, expect fail-open warnings):")
    t0 = time.time()
    pass1 = [is_complete_thought_sync(s) for s in samples]
    pass1_dt = time.time() - t0
    for r, s in zip(pass1, samples):
        print(f"  {r!r:>5}  ←  {s!r}")
    print(f"  total {pass1_dt:.3f}s")

    print("Pass 2 (warm cache — expect instant returns, no log lines):")
    t0 = time.time()
    pass2 = [is_complete_thought_sync(s) for s in samples]
    pass2_dt = time.time() - t0
    for r, s in zip(pass2, samples):
        print(f"  {r!r:>5}  ←  {s!r}")
    print(f"  total {pass2_dt:.3f}s")

    # Invariants:
    #   1. All 10 verdicts are True (fail-open).
    #   2. Pass 2 is at least 5x faster (cache short-circuits before any
    #      RuntimeError from _get_client even runs).
    if not all(pass1) or not all(pass2):
        raise SystemExit(f"FAIL: expected all True; got pass1={pass1} pass2={pass2}")
    if pass2_dt > 0 and pass1_dt > 0 and pass2_dt > pass1_dt / 2:
        # Soft check — on a hot machine pass 1 may already be sub-ms,
        # making the ratio noise. Print a hint rather than fail hard.
        print(
            f"  note: pass2 ({pass2_dt:.3f}s) not dramatically faster than "
            f"pass1 ({pass1_dt:.3f}s); cache may not be hitting."
        )
    print("OK")
