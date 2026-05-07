"""Self-echo bleed regression test for the Phase 1 WebSocket transport.

If the robot's mic picks up its own TTS playback, Whisper will transcribe
the reply back to the server. Without a guard, that fires a turn-of-itself
loop. The existing guard lives in `server/server.py` (`_LAST_REPLY` +
`_is_self_echo`) — per `docs/PHASE_1_TASK_MAP.md` ("Reused-as-is modules"),
the new WS app reuses the same guard. This test enforces that contract:

    1. Pre-seed `server.server._LAST_REPLY[username]` with the reply we just
       said (simulating the state right after a normal turn).
    2. Send an audio_chunk + end_of_utterance whose mocked transcription
       is the exact same string.
    3. Assert the server REJECTS the turn — emits an `echo_reject` control
       frame (or any control whose subtype mentions reject/echo) AND emits
       NO server-side audio_chunk for that turn.

Skips cleanly if `server/app_ws.py` hasn't landed yet in this worktree.
"""
from __future__ import annotations

import base64
import json
import time

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
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# Test
# ─────────────────────────────────────────────────────────────────────────────


def _install_echo_mocks(monkeypatch, *, echo_text: str, fake_mp3: bytes):
    """Wire mocks so the server's STT returns `echo_text` verbatim and any
    accidental agent run would be obvious (it shouldn't happen)."""
    # Transcription returns the previous reply verbatim — simulating mic
    # bleed picking up our own speaker output and Whisper echoing it back.
    def _fake_transcribe(_path):
        return echo_text

    monkeypatch.setattr(legacy_server, "_transcribe", _fake_transcribe, raising=False)
    monkeypatch.setattr(app_ws, "_transcribe", _fake_transcribe, raising=False)

    # If the guard fails and an agent run leaks through, we still don't want
    # network calls. Make the runner observable so the assertion can detect
    # an unexpected execution.
    runner_calls = {"count": 0}

    def _fake_run_agent(username, hint, transcript, image_b64):
        runner_calls["count"] += 1
        return ("UNEXPECTED REPLY (echo guard leaked)", "chat", [], False)

    monkeypatch.setattr(legacy_server, "_run_agent", _fake_run_agent, raising=False)
    monkeypatch.setattr(app_ws, "_run_agent", _fake_run_agent, raising=False)

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
    # task map, the WS app reuses this guard wholesale.
    legacy_server._LAST_REPLY[username] = reply_text
    # Also seed any WS-app local mirror if it exists, best-effort.
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
            if hasattr(app_ws, "_LAST_REPLY"):
                app_ws._LAST_REPLY.pop(username, None)
        except Exception:
            pass
        client.close()
