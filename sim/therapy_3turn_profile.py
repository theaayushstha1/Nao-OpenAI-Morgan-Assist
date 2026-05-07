# -*- coding: utf-8 -*-
"""3-turn therapy profile — verify the vision cache works.

Simulates the server's WS handler pipeline across three sequential
therapy turns in ONE continuous session. Tracks per-turn:

    vision_status        : success | unavailable | failed | skipped
    vision_source        : fresh | cached | skipped
    vision_latency_ms    : real wall-clock for the API call (0 on cache hit)
    agent_latency_ms     : run_agent_streamed → first 'done' event
    tts_first_audio_ms   : sentence-boundary → first MP3 bytes ready
    total_first_audio_ms : turn start → first audio bytes
    observe_face_called  : True if the API was actually hit this turn

Expected:
    Turn 1: vision_source=fresh   (first turn)
    Turn 2: vision_source=cached  (no visual trigger, within TTL)
    Turn 3: vision_source=fresh   ("how do I look right now?" trigger)

Usage:
    set -a; source .env; set +a
    imagesnap -w 0.6 /tmp/live_proof_face.jpg
    python -m sim.therapy_3turn_profile
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnResult:
    turn_idx: int
    transcript: str
    vision_status: str
    vision_source: str
    vision_latency_ms: float
    vision_summary: str
    agent_latency_ms: float
    tts_first_audio_ms: float
    total_first_audio_ms: float
    reply_text: str
    observe_face_called: bool


@dataclass
class FakeSession:
    """Mimics the per-WS _Session fields the cache logic reads."""
    username: str = "therapy_3turn_user"
    hint: str = "therapy"
    image_b64: str | None = None
    _cached_vision: dict | None = None
    _cached_vision_at_ms: float = 0.0


# Counter wrapping observe_face_for_turn so we can prove the cache.
class VisionSpy:
    def __init__(self):
        self.call_count = 0
        self.last_latency_ms: float = 0.0

    def wrap(self, real_fn):
        def wrapped(image_b64):
            self.call_count += 1
            t = time.perf_counter()
            res = real_fn(image_b64)
            self.last_latency_ms = (time.perf_counter() - t) * 1000.0
            return res
        return wrapped


async def run_one_turn(
    sess: FakeSession,
    transcript: str,
    turn_idx: int,
    spy: VisionSpy,
) -> TurnResult:
    """Replicate the WS handler decision + agent stream for one turn."""
    from server.app_ws import _should_refresh_vision
    from server.tools import emotion as _emotion
    from server import _legacy_helpers as legacy
    from server.streaming import chunk_for_tts
    from server import openai_tts

    t_turn_start = time.perf_counter()
    pre_call_count = spy.call_count

    # ── 1. Cache decision (matches _process_turn logic) ──────────────────
    is_fast_chat = (sess.hint or "").lower() == "chat"
    vision_observation: dict | None = None
    vision_source = "skipped"
    fresh_latency_ms = 0.0

    if is_fast_chat:
        vision_observation = {
            "vision_status": "skipped",
            "vision_summary": "",
            "vision_latency_ms": None,
            "raw": None,
        }
        vision_source = "skipped"
    elif sess.image_b64:
        refresh, reason = _should_refresh_vision(sess, transcript)
        if refresh:
            # Fresh call (parallel in real server; sequential here for
            # measurement clarity — agent runs after).
            vision_observation = await asyncio.to_thread(
                _emotion.observe_face_for_turn, sess.image_b64,
            )
            fresh_latency_ms = vision_observation.get(
                "vision_latency_ms") or 0.0
            vision_source = "fresh"
            if vision_observation.get("vision_status") == "success":
                sess._cached_vision = vision_observation
                sess._cached_vision_at_ms = time.time() * 1000.0
        else:
            cached = dict(sess._cached_vision or {})
            cached["vision_age_ms"] = round(
                (time.time() * 1000.0) - sess._cached_vision_at_ms, 1)
            cached["vision_cached"] = True
            vision_observation = cached
            vision_source = "cached"
    else:
        vision_observation = {
            "vision_status": "skipped",
            "vision_summary": "",
            "vision_latency_ms": None,
            "raw": None,
        }
        vision_source = "skipped"

    # ── 2. Stream the agent. Measure agent latency + first-audio ─────────
    delta_q: asyncio.Queue[str | None] = asyncio.Queue()
    final = {"text": ""}

    async def deltas():
        while True:
            x = await delta_q.get()
            if x is None:
                return
            yield x

    t_agent_start = time.perf_counter()
    t_first_delta: float | None = None
    t_done: float | None = None

    async def drive():
        nonlocal t_first_delta, t_done
        async for ev in legacy.run_agent_streamed(
                sess.username, sess.hint, transcript, None, vision_observation):
            if ev["type"] == "delta":
                if t_first_delta is None:
                    t_first_delta = time.perf_counter()
                final["text"] += ev["text"]
                await delta_q.put(ev["text"])
            elif ev["type"] == "done":
                t_done = time.perf_counter()
                if not t_first_delta:
                    final["text"] = ev.get("reply") or ""
                    if final["text"]:
                        await delta_q.put(final["text"])
        await delta_q.put(None)

    drive_task = asyncio.create_task(drive())

    t_first_audio: float | None = None
    async for sent in chunk_for_tts(deltas()):
        if t_first_audio is None:
            mp3 = await asyncio.to_thread(openai_tts.synthesize, sent)
            t_first_audio = time.perf_counter()
        else:
            await asyncio.to_thread(openai_tts.synthesize, sent)
    await drive_task

    agent_latency = (
        (t_done - t_agent_start) * 1000.0 if t_done else 0.0
    )
    tts_first = (
        (t_first_audio - (t_first_delta or t_agent_start)) * 1000.0
        if t_first_audio else 0.0
    )
    total_first = (
        (t_first_audio - t_turn_start) * 1000.0
        if t_first_audio else 0.0
    )

    return TurnResult(
        turn_idx=turn_idx,
        transcript=transcript,
        vision_status=(vision_observation or {}).get("vision_status", "skipped"),
        vision_source=vision_source,
        vision_latency_ms=round(fresh_latency_ms, 1),
        vision_summary=((vision_observation or {}).get("vision_summary") or "")[:120],
        agent_latency_ms=round(agent_latency, 1),
        tts_first_audio_ms=round(tts_first, 1),
        total_first_audio_ms=round(total_first, 1),
        reply_text=final["text"],
        observe_face_called=(spy.call_count > pre_call_count),
    )


async def main(disable_cache: bool = False) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    img_path = os.environ.get(
        "LIVE_PROOF_IMAGE", "/tmp/live_proof_face.jpg")
    if not os.path.exists(img_path):
        print(f"WARN: no image at {img_path} — running with vision unavailable")
        image_b64 = None
    else:
        with open(img_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("ascii")

    # Wrap observe_face_for_turn so we can count actual API calls.
    from server.tools import emotion
    spy = VisionSpy()
    real_fn = emotion.observe_face_for_turn
    emotion.observe_face_for_turn = spy.wrap(real_fn)

    sess = FakeSession(image_b64=image_b64)

    # If --no-cache mode, bypass _should_refresh_vision by clearing the
    # cache between every turn so every turn is "first turn".
    if disable_cache:
        # Monkeypatch to always return refresh=True
        from server import app_ws
        original = app_ws._should_refresh_vision
        app_ws._should_refresh_vision = lambda s, t: (True, "no_cache_mode")
        print("=== NO-CACHE MODE — every turn forces fresh vision ===\n")
    else:
        print("=== CACHE MODE — Phase 11.6 default ===\n")

    transcripts = [
        ("I'm feeling really anxious about my midterms",
         "expected: fresh (first turn)"),
        ("What if I fail and disappoint everyone?",
         "expected: cached (no visual trigger)"),
        ("How do I look right now? Do I look tired?",
         "expected: fresh (visual trigger 'how do I look')"),
    ]

    results: list[TurnResult] = []
    for i, (txt, hint) in enumerate(transcripts, start=1):
        print(f"▸ Turn {i}: {txt!r}  ({hint})")
        r = await run_one_turn(sess, txt, i, spy)
        results.append(r)
        print(f"    vision_status        = {r.vision_status}")
        print(f"    vision_source        = {r.vision_source}")
        print(f"    vision_latency_ms    = {r.vision_latency_ms:.0f}")
        print(f"    observe_face_called  = {r.observe_face_called}")
        if r.vision_summary:
            print(f"    vision_summary       = {r.vision_summary!r}")
        print(f"    agent_latency_ms     = {r.agent_latency_ms:.0f}")
        print(f"    tts_first_audio_ms   = {r.tts_first_audio_ms:.0f}")
        print(f"    total_first_audio_ms = {r.total_first_audio_ms:.0f}")
        print(f"    reply                = {r.reply_text!r}")
        print()

    # Restore observe_face_for_turn original
    emotion.observe_face_for_turn = real_fn

    # Summary line
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print()
    print(f"{'turn':<6}{'source':<10}{'vision_ms':>10}"
           f"{'agent_ms':>10}{'tts_ms':>9}{'total_first_audio_ms':>22}"
           f"{'  fn called':>12}")
    print(f"{'----':<6}{'------':<10}{'---------':>10}"
           f"{'--------':>10}{'------':>9}{'----------':>22}"
           f"{'---------':>12}")
    for r in results:
        print(f"{r.turn_idx:<6}{r.vision_source:<10}"
              f"{r.vision_latency_ms:>10.0f}"
              f"{r.agent_latency_ms:>10.0f}"
              f"{r.tts_first_audio_ms:>9.0f}"
              f"{r.total_first_audio_ms:>22.0f}"
              f"{str(r.observe_face_called):>12}")
    print()
    cache_hit_count = sum(1 for r in results if r.vision_source == "cached")
    api_calls = sum(1 for r in results if r.observe_face_called)
    print(f"observe_face_for_turn API calls this run: {api_calls}/{len(results)}")
    print(f"cache hits: {cache_hit_count}/{len(results)}")
    return 0


if __name__ == "__main__":
    no_cache = "--no-cache" in sys.argv
    sys.exit(asyncio.run(main(disable_cache=no_cache)))
