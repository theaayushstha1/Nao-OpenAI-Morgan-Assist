"""WebSocket smoke test for the Phase 1 FastAPI transport.

This file is OWNED by the `tests` agent. Boots `server.app_ws.app` via
FastAPI's sync TestClient, drives a 5-turn synthetic conversation against
the `/ws/{username}` endpoint, and asserts the per-turn frame envelope
matches the contract in `docs/PHASE_1_TASK_MAP.md`.

All heavy dependencies (Whisper/Deepgram, OpenAI TTS, the agent graph,
the safety LLM) are monkeypatched at the canonical module path AND at any
WS-app re-export site, so the test never touches the network. If the new
`server/app_ws.py` is not yet present in the worktree, the whole file is
skipped via `pytest.importorskip` — collection still passes cleanly.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

# Skip the whole module if app_ws hasn't landed yet in this worktree.
# The fastapi-app agent owns that file; this test was authored before it
# merged. `pytest.importorskip` keeps `pytest --collect-only` clean.
pytest.importorskip("server.app_ws")

from fastapi.testclient import TestClient  # noqa: E402  (after importorskip)

from server import app_ws  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Frame helpers — mirror the JSON envelope in docs/PHASE_1_TASK_MAP.md.
# ─────────────────────────────────────────────────────────────────────────────


def _audio_chunk_frame(seq: int, pcm_bytes: bytes, ts_ms: float) -> dict:
    """Client → server `audio_chunk` frame (PCM16 mono @ 16 kHz, base64)."""
    return {
        "type": "audio_chunk",
        "seq": seq,
        "ts_ms": ts_ms,
        "data": base64.b64encode(pcm_bytes).decode("ascii"),
    }


def _control_frame(subtype: str, data: dict | None = None) -> dict:
    return {"type": "control", "subtype": subtype, "data": data or {}}


def _drain_one_turn(ws, *, max_frames: int = 50, timeout_s: float = 5.0):
    """Receive frames until we see a turn-terminating control or the cap.

    Returns a list of decoded JSON dicts in receive order. Treats `tts_ended`,
    `session_end`, and any per-turn equivalent as terminators. Also stops on
    any control frame whose subtype name contains 'end' so we are tolerant of
    minor naming drift while the FastAPI app is in flight.
    """
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
            if sub in {"tts_ended", "session_end"} or "end" in sub:
                break
    return frames


def _silent_pcm_chunk(ms: int = 20, sample_rate_hz: int = 16000) -> bytes:
    """20 ms of mono PCM16 silence (default), the standard frame size used
    on the WS. Returns raw bytes; caller base64-encodes via _audio_chunk_frame."""
    samples = int(sample_rate_hz * ms / 1000)
    return b"\x00\x00" * samples


# ─────────────────────────────────────────────────────────────────────────────
# Mock-installer used by all WS tests.
# Keep it explicit (not a fixture) because we need to install BEFORE the
# WS handler runs — TestClient's `websocket_connect` triggers the handler
# synchronously in a background thread, so monkeypatching after the connect
# is racy. Each test calls this from its own with-block.
# ─────────────────────────────────────────────────────────────────────────────


def _install_mocks(monkeypatch, *, transcript: str, reply: str,
                   actions: list[dict] | None = None,
                   crisis: bool = False,
                   fake_mp3: bytes) -> None:
    actions = list(actions or [])

    # 1) STT — patch every plausible site.
    def _fake_transcribe(_path):
        return transcript

    try:
        from server import server as _legacy
        monkeypatch.setattr(_legacy, "_transcribe", _fake_transcribe, raising=False)
    except Exception:
        pass
    monkeypatch.setattr(app_ws, "_transcribe", _fake_transcribe, raising=False)

    # 2) TTS — patch the module-level function so any importer sees the stub.
    from server import openai_tts

    def _fake_synth(text):
        if not text or not str(text).strip():
            return None
        return fake_mp3

    monkeypatch.setattr(openai_tts, "synthesize", _fake_synth)
    monkeypatch.setattr(app_ws, "synthesize", _fake_synth, raising=False)

    # 3) Crisis check — return a CrisisResult-shaped object so any consumer
    #    that does `.positive` / `.source` access doesn't trip.
    from server import safety

    def _fake_crisis(_text):
        return safety.CrisisResult(positive=crisis, source="clean" if not crisis else "keyword")

    monkeypatch.setattr(safety, "crisis_check", _fake_crisis)
    monkeypatch.setattr(app_ws, "crisis_check", _fake_crisis, raising=False)

    # 4) Agent runner — patch the legacy helper, the new WS helper if any,
    #    and the SDK Runner.run as a last line of defense.
    def _fake_run_agent(username, hint, transcript_, image_b64):
        return (reply, "chat", list(actions), False)

    try:
        from server import server as _legacy
        monkeypatch.setattr(_legacy, "_run_agent", _fake_run_agent, raising=False)
    except Exception:
        pass
    monkeypatch.setattr(app_ws, "_run_agent", _fake_run_agent, raising=False)

    try:
        from agents import Runner

        class _FakeResult:
            def __init__(self, text):
                self.final_output = text

            def final_output_as(self, _typ):
                return reply

        async def _fake_run(agent, message, **kwargs):
            ctx = kwargs.get("context") or {}
            if isinstance(ctx, dict):
                queue = ctx.get("actions_queue")
                if isinstance(queue, list):
                    queue.extend(actions)
            return _FakeResult(reply)

        monkeypatch.setattr(Runner, "run", _fake_run, raising=False)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_ws_single_turn_smoke(monkeypatch, fake_mp3_bytes):
    """Drive one full turn end-to-end and assert the frame contract.

    Send: session_open -> audio_chunks (~2 s of silence) -> end_of_utterance.
    Expect: server emits at least one transcript control then at least one
    audio_chunk frame, and the latency from end_of_utterance to first
    audio_chunk is < 1.5 s in the mock-driven test.
    """
    _install_mocks(
        monkeypatch,
        transcript="hello what time is it",
        reply="It is 3 PM.",
        actions=[],
        crisis=False,
        fake_mp3=fake_mp3_bytes,
    )

    client = TestClient(app_ws.app, headers={"X-NAO-Secret": ""})
    try:
        with client.websocket_connect("/ws/test_user") as ws:
            # 1. session_open
            ws.send_text(json.dumps(_control_frame("session_open", {
                "face_id": "test_user",
                "brain_version": 2,
                "hint": "chat",
            })))

            # 2. ~2 s of audio at 20 ms/frame = 100 frames
            chunk = _silent_pcm_chunk(ms=20)
            base_ts = 1714956000123.4
            for i in range(100):
                ws.send_text(json.dumps(_audio_chunk_frame(
                    seq=i, pcm_bytes=chunk, ts_ms=base_ts + i * 20,
                )))

            # 3. end_of_utterance — start the latency clock
            t_eou = time.monotonic()
            ws.send_text(json.dumps(_control_frame("end_of_utterance", {
                "robot_eou_hint": True, "energy_floor": 240, "trail_ms": 320,
            })))

            frames = _drain_one_turn(ws, timeout_s=5.0)

            # Latency: end_of_utterance -> first audio_chunk must be < 1.5 s.
            first_audio_idx = next(
                (i for i, f in enumerate(frames) if f.get("type") == "audio_chunk"),
                None,
            )
            # We don't have a per-frame receive timestamp from the fixture, so
            # the latency assertion uses the elapsed time from EoU until we
            # finished draining — which is a strict upper bound.
            elapsed_first = time.monotonic() - t_eou
            assert first_audio_idx is not None, (
                "Expected at least one server-side audio_chunk; got %r" % frames
            )
            assert elapsed_first < 1.5, (
                "First audio took %.3fs, expected < 1.5s" % elapsed_first
            )

            # Order: a transcript control must appear before the first audio chunk.
            transcript_idx = next(
                (i for i, f in enumerate(frames)
                 if f.get("type") == "control" and f.get("subtype") == "transcript"),
                None,
            )
            assert transcript_idx is not None, (
                "Expected a transcript control frame before audio; got %r" % frames
            )
            assert transcript_idx < first_audio_idx, (
                "transcript should arrive before first audio_chunk; "
                "got transcript_idx=%d first_audio_idx=%d frames=%r" % (
                    transcript_idx, first_audio_idx, frames,
                )
            )

            # Audio frame shape sanity: format must be mp3, data is base64.
            audio_frame = frames[first_audio_idx]
            assert audio_frame.get("format") == "mp3", audio_frame
            assert isinstance(audio_frame.get("data"), str), audio_frame
            decoded = base64.b64decode(audio_frame["data"])
            assert decoded == fake_mp3_bytes, "audio payload mismatch"

            # Send session_close so the server can tear down cleanly.
            try:
                ws.send_text(json.dumps(_control_frame("session_close")))
            except Exception:
                pass
    finally:
        client.close()


def test_ws_five_turn_loop_under_six_seconds(monkeypatch, fake_mp3_bytes):
    """Five back-to-back turns in one session must complete in < 6 s."""
    _install_mocks(
        monkeypatch,
        transcript="what's the weather",
        reply="Sunny and 72.",
        actions=[],
        crisis=False,
        fake_mp3=fake_mp3_bytes,
    )

    client = TestClient(app_ws.app, headers={"X-NAO-Secret": ""})
    chunk = _silent_pcm_chunk(ms=20)
    try:
        with client.websocket_connect("/ws/test_user_5x") as ws:
            ws.send_text(json.dumps(_control_frame("session_open", {
                "face_id": "test_user_5x", "brain_version": 2, "hint": "chat",
            })))

            t_start = time.monotonic()
            seq = 0
            for turn in range(5):
                # ~0.5 s of audio per turn — keeps the test fast but realistic.
                for _ in range(25):
                    ws.send_text(json.dumps(_audio_chunk_frame(
                        seq=seq, pcm_bytes=chunk, ts_ms=time.time() * 1000,
                    )))
                    seq += 1
                ws.send_text(json.dumps(_control_frame("end_of_utterance", {
                    "robot_eou_hint": True, "energy_floor": 240, "trail_ms": 320,
                })))
                frames = _drain_one_turn(ws, timeout_s=5.0)
                assert any(f.get("type") == "audio_chunk" for f in frames), (
                    "Turn %d emitted no audio_chunk: %r" % (turn, frames)
                )

            total = time.monotonic() - t_start
            assert total < 6.0, "5 turns took %.3fs, expected < 6.0s" % total

            try:
                ws.send_text(json.dumps(_control_frame("session_close")))
            except Exception:
                pass
    finally:
        client.close()
