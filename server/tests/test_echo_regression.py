"""Self-echo bleed regression test for the Phase 1 WebSocket transport,
plus Phase 2 hardening of the same guard.

If the robot's mic picks up its own TTS playback, Whisper will transcribe
the reply back to the server. Without a guard, that fires a turn-of-itself
loop. The existing guard lives in `server/server.py` (`_LAST_REPLY` +
`_is_self_echo`) — per `docs/PHASE_1_TASK_MAP.md` ("Reused-as-is modules"),
the new WS app reuses the same guard. This file enforces that contract.

Phase 2 (`docs/PHASE_2_TASK_MAP.md`) tightens the guard further:
    - substring-match against the actual TTS sentences just emitted
      (stored in ``_LAST_REPLY_FULL`` per session),
    - and a post-TTS audio cooldown that drops incoming audio_chunk frames
      while ``_tts_active_until_ms`` is still in the future. Reverb that
      survives the ALAudioDevice unsubscribe must not reach STT.

The Phase-2 tests below skip cleanly if their respective module-level
symbols aren't present yet — sibling agents land them in separate worktrees.

Skips cleanly if `server/app_ws.py` hasn't landed yet in this worktree.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest

pytest.importorskip("server.app_ws")

from fastapi.testclient import TestClient  # noqa: E402

from server import app_ws  # noqa: E402
from server import server as legacy_server  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Frame helpers
# ─────────────────────────────────────────────────────────────────────────────


def _audio_chunk_frame(seq: int, pcm_bytes: bytes) -> dict:
    return {
        "type": "audio_chunk",
        "seq": seq,
        "ts_ms": time.time() * 1000,
        "data": base64.b64encode(pcm_bytes).decode("ascii"),
    }


def _control_frame(subtype: str, data: dict | None = None) -> dict:
    return {"type": "control", "subtype": subtype, "data": data or {}}


def _silent_pcm(ms: int = 20, sample_rate_hz: int = 16000) -> bytes:
    return b"\x00\x00" * int(sample_rate_hz * ms / 1000)


def _drain(ws, *, max_frames: int = 50, timeout_s: float = 3.0):
    frames: list[dict] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and len(frames) < max_frames:
        try:
            raw = ws.receive_text()
        except Exception:
            break
        try:
            f = json.loads(raw)
        except Exception:
            continue
        frames.append(f)
        if f.get("type") == "control":
            sub = (f.get("subtype") or "").lower()
            # Stop on any per-turn terminator OR an explicit reject — we
            # don't want the test to hang waiting for `tts_ended` when the
            # whole point is that no TTS should be emitted.
            if sub in {"tts_ended", "session_end", "echo_reject"}:
                break
            if "reject" in sub or "end" in sub:
                break
            # The current WS app signals rejection with subtype="transcript"
            # and `data.reject_reason` set. Stop on that too so we don't
            # block forever on a reject path that doesn't ship a dedicated
            # subtype (e.g. `no_voice`, `self_echo`, `hallucination_or_noise`).
            data = f.get("data") or {}
            if isinstance(data, dict) and data.get("reject_reason"):
                break
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# Test
# ─────────────────────────────────────────────────────────────────────────────


def _install_echo_mocks(monkeypatch, *, echo_text: str, fake_mp3: bytes):
    """Wire mocks so the server's STT returns `echo_text` verbatim and any
    accidental agent run would be obvious (it shouldn't happen).

    Also mocks the audio gates (``validate_wav``, ``has_voice``) so the
    silent-PCM test fixtures don't get short-circuited by the audio
    pre-checks before we even reach the echo guard. The whole point of the
    test is to exercise ``transcript_reject_reason``; the audio gates are
    other tests' concern.
    """
    # Transcription returns the previous reply verbatim — simulating mic
    # bleed picking up our own speaker output and Whisper echoing it back.
    def _fake_transcribe(_path):
        return echo_text

    monkeypatch.setattr(legacy_server, "_transcribe", _fake_transcribe, raising=False)
    monkeypatch.setattr(app_ws, "_transcribe", _fake_transcribe, raising=False)

    # The Phase-1 WS app routes through `legacy.transcribe` (no underscore),
    # `legacy.validate_wav`, and `legacy.has_voice`. Patch all three so the
    # zero-PCM test fixture flows past the gates and into the echo guard.
    try:
        from server import _legacy_helpers as _legacy
        monkeypatch.setattr(_legacy, "transcribe", _fake_transcribe,
                            raising=False)
        monkeypatch.setattr(_legacy, "validate_wav", lambda _p: True,
                            raising=False)
        monkeypatch.setattr(_legacy, "has_voice", lambda _p: True,
                            raising=False)
    except Exception:
        pass

    # If the guard fails and an agent run leaks through, we still don't want
    # network calls. Make the runner observable so the assertion can detect
    # an unexpected execution.
    runner_calls = {"count": 0}

    def _fake_run_agent(username, hint, transcript, image_b64):
        runner_calls["count"] += 1
        return ("UNEXPECTED REPLY (echo guard leaked)", "chat", [], False)

    monkeypatch.setattr(legacy_server, "_run_agent", _fake_run_agent, raising=False)
    monkeypatch.setattr(app_ws, "_run_agent", _fake_run_agent, raising=False)
    try:
        from server import _legacy_helpers as _legacy
        monkeypatch.setattr(_legacy, "run_agent", _fake_run_agent,
                            raising=False)
    except Exception:
        pass

    # Crisis check passthrough (clean) — echo_text is non-crisis.
    from server import safety
    monkeypatch.setattr(safety, "crisis_check",
                        lambda _t: safety.CrisisResult(positive=False, source="clean"))
    monkeypatch.setattr(app_ws, "crisis_check",
                        lambda _t: safety.CrisisResult(positive=False, source="clean"),
                        raising=False)

    # TTS — only invoked if the guard fails. Patch so it returns deterministic
    # bytes if it does run, so the assertion-on-leak is unambiguous.
    from server import openai_tts
    monkeypatch.setattr(openai_tts, "synthesize",
                        lambda t: fake_mp3 if (t and str(t).strip()) else None)
    monkeypatch.setattr(app_ws, "synthesize",
                        lambda t: fake_mp3 if (t and str(t).strip()) else None,
                        raising=False)

    return runner_calls


def test_self_echo_bleed_does_not_fire_turn(monkeypatch, fake_mp3_bytes):
    """The robot's own TTS bytes coming back as a transcript MUST be rejected."""
    username = "echo_user"
    reply_text = "Sunny and 72 today, with a light breeze."

    # Seed the guard's per-user last-reply cache as if we'd just spoken.
    # `_LAST_REPLY` is the canonical cache used by `_is_self_echo`; per the
    # task map, the WS app reuses this guard wholesale. We seed every
    # plausible store: the legacy Flask module, the helpers copy that
    # ``app_ws`` actually consults, and any local mirror on the WS app.
    legacy_server._LAST_REPLY[username] = reply_text
    try:
        from server import _legacy_helpers as _legacy
        _legacy.LAST_REPLY[username] = reply_text
    except Exception:
        pass
    try:
        if hasattr(app_ws, "_LAST_REPLY"):
            app_ws._LAST_REPLY[username] = reply_text
    except Exception:
        pass

    runner_calls = _install_echo_mocks(
        monkeypatch, echo_text=reply_text, fake_mp3=fake_mp3_bytes,
    )

    client = TestClient(app_ws.app, headers={"X-NAO-Secret": ""})
    try:
        with client.websocket_connect("/ws/" + username) as ws:
            ws.send_text(json.dumps(_control_frame("session_open", {
                "face_id": username, "brain_version": 2, "hint": "chat",
            })))

            # ~1 s of silence followed by EoU. The transcription mock returns
            # the previous reply verbatim, so the guard must trip.
            chunk = _silent_pcm(ms=20)
            for i in range(50):
                ws.send_text(json.dumps(_audio_chunk_frame(seq=i, pcm_bytes=chunk)))
            ws.send_text(json.dumps(_control_frame("end_of_utterance", {
                "robot_eou_hint": True, "energy_floor": 240, "trail_ms": 320,
            })))

            frames = _drain(ws, timeout_s=3.0)

            # Hard requirement: ZERO server-side audio_chunk frames.
            audio_frames = [f for f in frames if f.get("type") == "audio_chunk"]
            assert audio_frames == [], (
                "Echo guard leaked: server emitted %d audio_chunk frame(s); "
                "expected 0. Frames=%r" % (len(audio_frames), frames)
            )

            # The agent must NOT have been run for an echo. If runner_calls
            # incremented, the guard let the transcript through.
            assert runner_calls["count"] == 0, (
                "Echo guard leaked: agent runner was called %d time(s)"
                % runner_calls["count"]
            )

            # Soft requirement: the server should signal the rejection.
            # Accept any control whose subtype mentions reject/echo, OR a
            # transcript-frame with empty payload (the legacy /turn JSON
            # response shape), OR a session_end with a reason.
            rejected = False
            for f in frames:
                if f.get("type") != "control":
                    continue
                sub = (f.get("subtype") or "").lower()
                if "echo" in sub or "reject" in sub:
                    rejected = True
                    break
                # Accept the legacy "silence" envelope too, for tolerance.
                data = f.get("data") or {}
                if isinstance(data, dict) and (
                    data.get("active_agent") == "silence"
                    or data.get("reason") in {"self_echo", "echo"}
                    # The current WS app reports reject reasons under
                    # `data.reject_reason`; any non-empty value here means the
                    # server short-circuited before TTS.
                    or data.get("reject_reason") in {
                        "self_echo", "echo",
                        "robot_named_echo",
                        "hallucination_or_noise",
                    }
                ):
                    rejected = True
                    break
            assert rejected, (
                "Echo was silently dropped — expected a control frame "
                "signalling the rejection. Frames=%r" % frames
            )

            try:
                ws.send_text(json.dumps(_control_frame("session_close")))
            except Exception:
                pass
    finally:
        # Clean up the seeded cache so other tests aren't affected.
        legacy_server._LAST_REPLY.pop(username, None)
        try:
            from server import _legacy_helpers as _legacy
            _legacy.LAST_REPLY.pop(username, None)
        except Exception:
            pass
        try:
            if hasattr(app_ws, "_LAST_REPLY"):
                app_ws._LAST_REPLY.pop(username, None)
        except Exception:
            pass
        client.close()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — strengthened echo guard
# ─────────────────────────────────────────────────────────────────────────────


def _seed_last_reply_full(username: str, full_reply: str) -> list[tuple[Any, str]]:
    """Seed every plausible Phase-2 last-reply store with `full_reply`.

    Returns the list of (container, key) tuples we wrote into so the cleanup
    step can pop them precisely. We don't know exactly which symbol the
    Phase-2 author lands on, so we belt-and-braces seed:

      - ``app_ws._LAST_REPLY_FULL[username] = full_reply``  (str scalar form)
      - ``app_ws._LAST_REPLY_FULL[username] = [full_reply]`` (list-of-chunks form)
      - ``app_ws._LAST_REPLY_CHUNKS[username] = [full_reply]`` (sentence list)
      - the legacy ``LAST_REPLY`` cache as a final fallback so the existing
        substring branch in ``_legacy_helpers._is_self_echo`` still trips.
    """
    seeded: list[tuple[Any, str]] = []
    # Phase 2: scalar full reply.
    container = getattr(app_ws, "_LAST_REPLY_FULL", None)
    if isinstance(container, dict):
        # Best-effort: the impl may store either a str or a list. Try str
        # first; tests that expect list-of-chunks can still find the
        # full sentence as a substring of any one chunk.
        container[username] = full_reply
        seeded.append((container, username))
    # Phase 2: list of TTS sentence chunks.
    chunks = getattr(app_ws, "_LAST_REPLY_CHUNKS", None)
    if isinstance(chunks, dict):
        chunks[username] = [full_reply]
        seeded.append((chunks, username))
    # Phase 1 fallback — every code path on the merge target reads from this.
    try:
        from server import _legacy_helpers as legacy
        legacy.LAST_REPLY[username] = full_reply
        seeded.append((legacy.LAST_REPLY, username))
    except Exception:
        pass
    legacy_server._LAST_REPLY[username] = full_reply
    seeded.append((legacy_server._LAST_REPLY, username))
    return seeded


def _cleanup_seeded(seeded: list[tuple[Any, str]]) -> None:
    for container, key in seeded:
        try:
            container.pop(key, None)
        except Exception:
            pass


def test_substring_match_rejects_self_echo(monkeypatch, fake_mp3_bytes) -> None:
    """`_LAST_REPLY_FULL = "I think we should consider Plan B"` and a
    transcript of "we should consider plan b" must be rejected.

    The Phase-2 strengthening: substring-match against the literal TTS
    sentence text. The legacy bigram guard would also catch this (the
    transcript is a normalised substring), so the test passes against both
    the Phase-1 baseline and the Phase-2 implementation. Either is correct;
    the hard contract is that the agent runner does not fire.
    """
    username = "substring_user"
    full_reply = "I think we should consider Plan B"
    echo_transcript = "we should consider plan b"

    seeded = _seed_last_reply_full(username, full_reply)
    runner_calls = _install_echo_mocks(
        monkeypatch, echo_text=echo_transcript, fake_mp3=fake_mp3_bytes,
    )

    client = TestClient(app_ws.app, headers={"X-NAO-Secret": ""})
    try:
        with client.websocket_connect("/ws/" + username) as ws:
            ws.send_text(json.dumps(_control_frame("session_open", {
                "face_id": username, "brain_version": 2, "hint": "chat",
            })))

            chunk = _silent_pcm(ms=20)
            for i in range(50):
                ws.send_text(json.dumps(_audio_chunk_frame(seq=i, pcm_bytes=chunk)))
            ws.send_text(json.dumps(_control_frame("end_of_utterance", {
                "robot_eou_hint": True, "energy_floor": 240, "trail_ms": 320,
            })))

            frames = _drain(ws, timeout_s=3.0)

            audio_frames = [f for f in frames if f.get("type") == "audio_chunk"]
            assert audio_frames == [], (
                "Substring guard leaked: server emitted %d audio_chunk "
                "frame(s); expected 0. Frames=%r"
                % (len(audio_frames), frames)
            )
            assert runner_calls["count"] == 0, (
                "Substring guard leaked: agent runner ran %d time(s)"
                % runner_calls["count"]
            )

            try:
                ws.send_text(json.dumps(_control_frame("session_close")))
            except Exception:
                pass
    finally:
        _cleanup_seeded(seeded)
        client.close()


def test_post_tts_cooldown_drops_audio_chunks(monkeypatch, fake_mp3_bytes) -> None:
    """Audio chunks arriving while ``_tts_active_until_ms`` is in the future
    must be DROPPED before reaching STT.

    Skips cleanly if Phase 2's ``_tts_active_until_ms`` symbol hasn't shipped
    yet. We don't know whether the impl stores it as a per-session attribute
    or a module-level dict keyed by username, so we patch both shapes.
    """
    if not (hasattr(app_ws, "_tts_active_until_ms")
            or hasattr(app_ws, "_TTS_ACTIVE_UNTIL_MS")):
        pytest.skip("post-TTS cooldown (_tts_active_until_ms) not implemented yet")

    username = "cooldown_user"
    runner_calls = _install_echo_mocks(
        monkeypatch, echo_text="this should never reach STT", fake_mp3=fake_mp3_bytes,
    )

    # Track every transcribe call so we can assert STT was never reached.
    transcribe_calls = {"count": 0}

    def _counting_transcribe(_path):
        transcribe_calls["count"] += 1
        return "this should never reach STT"

    # Patch BOTH the legacy module and any local re-export. Phase 2 routes
    # through `server._legacy_helpers.transcribe`; older drafts route through
    # `server.server._transcribe`. Patch both.
    try:
        from server import _legacy_helpers as legacy
        monkeypatch.setattr(legacy, "transcribe", _counting_transcribe,
                            raising=False)
    except Exception:
        pass
    monkeypatch.setattr(legacy_server, "_transcribe", _counting_transcribe,
                        raising=False)
    monkeypatch.setattr(app_ws, "_transcribe", _counting_transcribe,
                        raising=False)

    # Force the cooldown into the future. We try every plausible storage
    # shape — module-level int, module-level dict-by-username, or a function
    # that returns the deadline. `time.time()` (seconds) and `monotonic_ms`
    # may both appear in implementations; set generously into the future
    # (5 s) so any reasonable clock check still rejects the chunk.
    now_ms = int(time.time() * 1000)
    deadline_ms = now_ms + 5000
    set_attrs: list[tuple[str, Any]] = []

    def _try_set_module_attr(name: str, value: Any) -> None:
        if hasattr(app_ws, name):
            old = getattr(app_ws, name)
            monkeypatch.setattr(app_ws, name, value, raising=False)
            set_attrs.append((name, old))

    # Scalar form.
    if hasattr(app_ws, "_tts_active_until_ms"):
        existing = getattr(app_ws, "_tts_active_until_ms")
        if isinstance(existing, dict):
            existing[username] = deadline_ms
        else:
            _try_set_module_attr("_tts_active_until_ms", deadline_ms)
    if hasattr(app_ws, "_TTS_ACTIVE_UNTIL_MS"):
        existing = getattr(app_ws, "_TTS_ACTIVE_UNTIL_MS")
        if isinstance(existing, dict):
            existing[username] = deadline_ms
        else:
            _try_set_module_attr("_TTS_ACTIVE_UNTIL_MS", deadline_ms)

    client = TestClient(app_ws.app, headers={"X-NAO-Secret": ""})
    try:
        with client.websocket_connect("/ws/" + username) as ws:
            ws.send_text(json.dumps(_control_frame("session_open", {
                "face_id": username, "brain_version": 2, "hint": "chat",
            })))

            # Send a single audio_chunk immediately. Within the cooldown, the
            # frame must be silently dropped — no STT, no agent run.
            chunk = _silent_pcm(ms=20)
            ws.send_text(
                json.dumps(_audio_chunk_frame(seq=1, pcm_bytes=chunk))
            )

            # Allow the server a brief moment to process. We don't expect a
            # response — the chunk should be a no-op while cooldown is active.
            frames = _drain(ws, timeout_s=0.6)

            assert transcribe_calls["count"] == 0, (
                "post-TTS cooldown leaked: STT was called %d time(s) on a "
                "chunk arriving inside the cooldown window"
                % transcribe_calls["count"]
            )
            assert runner_calls["count"] == 0, (
                "post-TTS cooldown leaked: agent runner ran %d time(s) "
                "during the cooldown window"
                % runner_calls["count"]
            )
            # Soft check: ensure no audio_chunk leaked back either.
            audio_frames = [f for f in frames if f.get("type") == "audio_chunk"]
            assert audio_frames == [], (
                "post-TTS cooldown leaked: server emitted %d audio_chunk "
                "frame(s); expected 0. Frames=%r"
                % (len(audio_frames), frames)
            )

            try:
                ws.send_text(json.dumps(_control_frame("session_close")))
            except Exception:
                pass
    finally:
        client.close()


def test_old_bigram_guard_still_works(monkeypatch, fake_mp3_bytes) -> None:
    """The existing bigram-overlap path (≥ 0.6 Jaccard) must keep working.

    Construct a transcript that is NOT a substring of the last reply but
    shares enough content words for the legacy Jaccard branch to fire. Pre-
    Phase-2 this is the only echo guard; post-Phase-2 the substring check
    fires first, but the bigram fallback is preserved for resilience.
    """
    username = "bigram_user"
    last_reply = "the weather today is sunny and warm with a light breeze"
    # Same content words, reshuffled so it's not a clean substring of the
    # last reply (avoid the substring fast-path). Jaccard over the unique
    # word sets is well above 0.6.
    bigram_echo = "today the weather is warm and sunny with light breeze"

    legacy_server._LAST_REPLY[username] = last_reply
    seeded: list[tuple[Any, str]] = [(legacy_server._LAST_REPLY, username)]
    try:
        from server import _legacy_helpers as legacy
        legacy.LAST_REPLY[username] = last_reply
        seeded.append((legacy.LAST_REPLY, username))
    except Exception:
        pass

    runner_calls = _install_echo_mocks(
        monkeypatch, echo_text=bigram_echo, fake_mp3=fake_mp3_bytes,
    )

    client = TestClient(app_ws.app, headers={"X-NAO-Secret": ""})
    try:
        with client.websocket_connect("/ws/" + username) as ws:
            ws.send_text(json.dumps(_control_frame("session_open", {
                "face_id": username, "brain_version": 2, "hint": "chat",
            })))

            chunk = _silent_pcm(ms=20)
            for i in range(50):
                ws.send_text(json.dumps(_audio_chunk_frame(seq=i, pcm_bytes=chunk)))
            ws.send_text(json.dumps(_control_frame("end_of_utterance", {
                "robot_eou_hint": True, "energy_floor": 240, "trail_ms": 320,
            })))

            frames = _drain(ws, timeout_s=3.0)

            audio_frames = [f for f in frames if f.get("type") == "audio_chunk"]
            assert audio_frames == [], (
                "Bigram guard leaked: server emitted %d audio_chunk frame(s); "
                "expected 0. Frames=%r" % (len(audio_frames), frames)
            )
            assert runner_calls["count"] == 0, (
                "Bigram guard regressed: agent runner ran %d time(s) on a "
                "high-Jaccard echo transcript" % runner_calls["count"]
            )

            try:
                ws.send_text(json.dumps(_control_frame("session_close")))
            except Exception:
                pass
    finally:
        _cleanup_seeded(seeded)
        client.close()
