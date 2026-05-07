"""Thin scenario driver — the facade scenarios use to talk to the system.

The driver exposes five methods scenarios call:

    connect_ws(url, secret)                  → ws (a minimal handle, see below)
    inject_face(face_id, confidence, distance_m)
    say(text_or_wav)
    expect(predicate, timeout_s)             → matched frame (or raises TimeoutError)
    assert_no_more_audio(timeout_s)          → True or AssertionError

Internally the driver uses ``fastapi.testclient.TestClient`` because that
is the only way to exercise ``server.app_ws.app`` in-process without
spinning up a uvicorn worker. ``TestClient`` ships a synchronous
``websocket_connect`` context manager whose ``send_text`` / ``receive_text``
calls return the same JSON envelope the real client would see.

The driver guards its imports so the scenario modules import cleanly even
when the sibling worktrees (``live_nao_driver``, ``fake_naoqi``) haven't
landed yet — the per-method calls raise a clear ``DriverUnavailable`` if
a missing dep is hit, and tests can skip rather than crash on collection.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

_log = logging.getLogger("sim.driver")


class DriverUnavailable(RuntimeError):
    """Raised when a required dependency (FastAPI app, fake naoqi) is missing.

    Scenarios catch this and translate it into ``outcome="skipped"`` so the
    runner reports a clean skip instead of a hard failure.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Connection helper. We accept a URL for API parity with the live driver, but
# in TestClient mode we ignore the host part — TestClient mounts the FastAPI
# app in-process. The path component is used so scenarios that want a custom
# username can pass ``ws://localhost/ws/aayush`` and still get a session.
# ─────────────────────────────────────────────────────────────────────────────


class _WsHandle:
    """Wraps the TestClient WebSocket so scenarios get a uniform API.

    Buffered receive: a background reader thread drains frames into a
    deque so ``expect(...)`` can scan history without blocking the main
    thread on a slow frame. Frames already inspected stay in history so
    later predicates can revisit them — scenarios assert on order, not
    on consumption.
    """

    def __init__(self, ws_ctx: Any, ws_handle: Any, client: Any,
                 username: str, secret: str | None) -> None:
        self._ws_ctx = ws_ctx
        self._ws = ws_handle
        self._client = client
        self.username = username
        self.secret = secret or ""
        self._frames: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._reader = threading.Thread(
            target=self._reader_loop, name="sim-ws-reader", daemon=True,
        )
        self._reader.start()

    # ── reader ──────────────────────────────────────────────────────────

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._ws.receive_text()
            except Exception:
                # Connection closed / TestClient teardown.
                break
            try:
                frame = json.loads(raw)
            except Exception:
                continue
            with self._lock:
                self._frames.append(frame)

    def close(self) -> None:
        self._stop.set()
        try:
            self._ws_ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 — TestClient teardown is best-effort
            pass
        try:
            self._client.close()
        except Exception:
            pass

    # ── send ────────────────────────────────────────────────────────────

    def send(self, frame: dict[str, Any]) -> None:
        try:
            self._ws.send_text(json.dumps(frame))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"ws.send_text failed: {e!r}")

    # ── receive ─────────────────────────────────────────────────────────

    def frames_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._frames)

    def wait_for(self, predicate: Callable[[dict[str, Any]], bool],
                 timeout_s: float, since: int = 0) -> dict[str, Any]:
        """Block until a frame at or after index ``since`` matches ``predicate``.

        ``since`` lets callers consume already-seen frames without rewinding
        on the next call. Defaults to 0 (full history).

        Raises ``TimeoutError`` if no match by deadline.
        """
        deadline = time.monotonic() + max(0.001, float(timeout_s))
        seen_so_far = max(0, int(since))
        while time.monotonic() < deadline:
            with self._lock:
                frames = list(self._frames)
            for f in frames[seen_so_far:]:
                if predicate(f):
                    return f
            seen_so_far = len(frames)
            time.sleep(0.01)
        raise TimeoutError(
            f"timed out after {timeout_s}s waiting on predicate; "
            f"saw {len(self._frames)} frames (since={since})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Audio helpers. Scenarios talk in canned WAV files placed under
# ``sim/scenarios/audio/``. We don't need real audio for headless runs because
# the scenario monkeypatches the STT call to return a fixed transcript — the
# bytes are just there to satisfy the buffer-non-empty check on EoU.
# ─────────────────────────────────────────────────────────────────────────────


_AUDIO_DIR = Path(__file__).resolve().parent / "audio"


def _ensure_stub_wavs() -> None:
    """Create silent / sine-tone WAV stubs on first import.

    Avoids any external dep — uses the stdlib ``wave`` module. Files are
    only written if missing, so an operator can replace them with real
    recordings without us clobbering the swap.
    """
    import math
    import struct
    import wave

    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # 0.5 s of silence at 16 kHz mono PCM16
    silent = _AUDIO_DIR / "silent_500ms.wav"
    if not silent.exists():
        with wave.open(str(silent), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * (16000 // 2))

    # 1.0 s of 440 Hz sine — a token "non-silent" audio so VAD-aware paths
    # have something to chew on if a scenario disables the silent shortcut.
    sine = _AUDIO_DIR / "sine_440hz_1s.wav"
    if not sine.exists():
        with wave.open(str(sine), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            frames = bytearray()
            for i in range(16000):
                v = int(0.2 * 32767 * math.sin(2 * math.pi * 440 * i / 16000))
                frames.extend(struct.pack("<h", v))
            wf.writeframes(bytes(frames))


_ensure_stub_wavs()


def _silent_pcm(ms: int = 20, sample_rate_hz: int = 16000) -> bytes:
    samples = int(sample_rate_hz * ms / 1000)
    return b"\x00\x00" * samples


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────


class Driver:
    """Thin facade scenarios use to drive the WS pipeline.

    All side effects are scoped to one driver instance. Scenarios should
    instantiate one ``Driver`` per ``run(...)`` call — sharing across
    scenarios is supported but not required.
    """

    def __init__(self) -> None:
        self.ws: _WsHandle | None = None
        self._face_injected: dict[str, Any] | None = None
        self._mocks_installed = False
        self._monkeypatch_undo: list[Callable[[], None]] = []

    # ── connect / disconnect ────────────────────────────────────────────

    def connect_ws(self, url: str = "ws://localhost/ws/sim_user",
                   secret: str | None = None) -> _WsHandle:
        """Open a WS connection against the in-process FastAPI app.

        ``url`` only matters for the path (the username after ``/ws/``);
        TestClient ignores the scheme/host. ``secret`` is the value
        passed via the ``X-NAO-Secret`` header. If ``None``, we read
        ``server.config.NAO_SHARED_SECRET`` so a `.env`-configured
        secret on the dev machine doesn't reject the simulator.
        """
        try:
            from fastapi.testclient import TestClient  # type: ignore
        except Exception as e:
            raise DriverUnavailable(
                f"fastapi.testclient unavailable: {e!r}. "
                "Run `pip install fastapi httpx` (httpx ships TestClient)."
            )
        try:
            from server import app_ws  # type: ignore
            from server import config as _cfg  # type: ignore
        except Exception as e:
            raise DriverUnavailable(
                f"server.app_ws import failed: {e!r}. "
                "The Phase 1 FastAPI app must be present on this branch."
            )

        # Suppress test-time noise from defensive paths.
        os.environ.setdefault("PYTHONWARNINGS", "ignore")

        # Resolve secret. None → config default; "" → explicit no-auth (which
        # only works if the server itself has no secret set).
        if secret is None:
            secret = getattr(_cfg, "NAO_SHARED_SECRET", "") or ""

        # Path → username
        path = url.rsplit("/ws/", 1)[-1] if "/ws/" in url else "sim_user"
        username = path.split("?", 1)[0] or "sim_user"

        client = TestClient(app_ws.app, headers={"X-NAO-Secret": secret})
        ws_ctx = client.websocket_connect(f"/ws/{username}")
        ws_handle = ws_ctx.__enter__()

        self.ws = _WsHandle(ws_ctx, ws_handle, client, username, secret)
        # Send the mandatory session_open frame so the server transitions
        # past the handshake guard.
        self.ws.send({
            "type": "control",
            "subtype": "session_open",
            "data": {"face_id": username, "brain_version": 0, "hint": None},
        })
        # Wait for session_open_ack so subsequent calls see a settled session.
        try:
            self.ws.wait_for(
                lambda f: (f.get("type") == "control"
                           and f.get("subtype") == "session_open_ack"),
                timeout_s=2.0,
            )
        except TimeoutError:
            _log.warning("session_open_ack not received within 2s — continuing")
        return self.ws

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None
        for undo in reversed(self._monkeypatch_undo):
            try:
                undo()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
        self._monkeypatch_undo.clear()

    # ── mocks (so scenarios are deterministic) ──────────────────────────

    def install_mocks(self, *, transcript: str, reply: str,
                      actions: Iterable[dict[str, Any]] | None = None,
                      crisis: bool = False,
                      active_agent: str = "chat",
                      fake_mp3: bytes | None = None,
                      tts_per_chunk_delay_ms: int = 0) -> None:
        """Patch STT/TTS/agent/safety so the WS pipeline is deterministic.

        Mirrors the ``_install_mocks`` helper from
        ``server/tests/test_ws_smoke.py`` but exposes the active-agent so
        scenarios can assert on the ``agent_handoff`` control. Patches are
        undone on ``Driver.close()`` via a saved-callable stack.

        ``tts_per_chunk_delay_ms`` adds a synchronous sleep before each
        synthesize call returns. The barge-in scenario uses this to keep
        TTS in flight long enough to send the barge_in control mid-stream.
        """
        actions = list(actions or [])
        fake_mp3 = fake_mp3 or _make_fake_mp3()

        try:
            from server import app_ws, openai_tts, safety  # type: ignore
        except Exception as e:
            raise DriverUnavailable(f"server modules missing: {e!r}")

        # 1) STT
        def _fake_transcribe(_path: Any) -> str:
            return transcript

        try:
            from server import server as _legacy  # type: ignore
            self._mp_setattr(_legacy, "_transcribe", _fake_transcribe, raising=False)
        except Exception:
            pass
        self._mp_setattr(app_ws, "_transcribe", _fake_transcribe, raising=False)

        try:
            from server import _legacy_helpers as _lh  # type: ignore
            self._mp_setattr(_lh, "validate_wav",
                             lambda *_a, **_k: True, raising=False)
            self._mp_setattr(_lh, "has_voice",
                             lambda *_a, **_k: True, raising=False)
            self._mp_setattr(_lh, "transcribe", _fake_transcribe, raising=False)
        except Exception:
            pass

        # 2) TTS
        delay_s = max(0.0, float(tts_per_chunk_delay_ms) / 1000.0)

        def _fake_synth(text: str) -> bytes | None:
            if not text or not str(text).strip():
                return None
            if delay_s:
                time.sleep(delay_s)
            return fake_mp3

        self._mp_setattr(openai_tts, "synthesize", _fake_synth)
        self._mp_setattr(app_ws, "synthesize", _fake_synth, raising=False)

        # 3) Crisis check
        def _fake_crisis(_text: str) -> Any:
            return safety.CrisisResult(
                positive=crisis,
                source="clean" if not crisis else "keyword",
            )

        self._mp_setattr(safety, "crisis_check", _fake_crisis)
        self._mp_setattr(app_ws, "crisis_check", _fake_crisis, raising=False)

        # 4) Agent runner — return (reply, active_agent, actions, suppress_image)
        def _fake_run_agent(username: str, hint: Any, transcript_: str,
                            image_b64: Any) -> tuple[str, str, list, bool]:
            return (reply, active_agent, list(actions), False)

        try:
            from server import server as _legacy  # type: ignore
            self._mp_setattr(_legacy, "_run_agent", _fake_run_agent, raising=False)
            self._mp_setattr(_legacy, "run_agent", _fake_run_agent, raising=False)
        except Exception:
            pass
        self._mp_setattr(app_ws, "_run_agent", _fake_run_agent, raising=False)
        try:
            from server import _legacy_helpers as _lh  # type: ignore
            self._mp_setattr(_lh, "run_agent", _fake_run_agent, raising=False)
        except Exception:
            pass

        # 5) Phase 6/7 quiesce — mirror the smoke test (the simulator is the
        #    deterministic substrate; per-scenario opt-ins reinstall these).
        try:
            from server import session as _session  # type: ignore
            self._mp_setattr(_session, "pull_brain_updates",
                             lambda *_a, **_k: {}, raising=False)
            self._mp_setattr(_session, "is_first_turn",
                             lambda _sid: False, raising=False)
            self._mp_setattr(_session, "get_camera_consent",
                             lambda _u: True, raising=False)  # default-on per Phase 6
        except Exception:
            pass

        # 6) Echo cooldown windows — zero them out so scenarios can drive
        #    rapid-fire turns without the post-TTS gate eating their audio.
        try:
            self._mp_setattr(app_ws, "TTS_COOLDOWN_PADDING_MS", 0, raising=False)
            self._mp_setattr(app_ws.config, "MIC_GATE_GRACE_MS", 0, raising=False)
        except Exception:
            pass

        # 7) Bypass the hallucination/echo rejector so scenarios with short
        #    canonical transcripts ("hello", "thanks", etc.) reach the agent
        #    path. The rejector is real production behavior — the
        #    `05_echo_bleed` scenario re-enables it via its own override.
        try:
            from server import _legacy_helpers as _lh  # type: ignore
            self._mp_setattr(_lh, "transcript_reject_reason",
                             lambda *_a, **_k: None, raising=False)
            self._mp_setattr(app_ws.legacy, "transcript_reject_reason",
                             lambda *_a, **_k: None, raising=False)
        except Exception:
            pass

        # 8) Bypass the substring/sentence echo guard for the same reason.
        try:
            self._mp_setattr(app_ws, "_is_substring_or_sentence_echo",
                             lambda *_a, **_k: False, raising=False)
        except Exception:
            pass

        self._mocks_installed = True

    def _mp_setattr(self, obj: Any, name: str, value: Any,
                    raising: bool = True) -> None:
        sentinel = object()
        old = getattr(obj, name, sentinel)
        try:
            setattr(obj, name, value)
        except Exception:
            if raising:
                raise
            return

        def _undo() -> None:
            if old is sentinel:
                try:
                    delattr(obj, name)
                except Exception:
                    pass
            else:
                try:
                    setattr(obj, name, old)
                except Exception:
                    pass

        self._monkeypatch_undo.append(_undo)

    # ── face / audio injection ──────────────────────────────────────────

    def inject_face(self, face_id: str = "aayush",
                    confidence: float = 0.9,
                    distance_m: float = 0.8,
                    gate: str = "voice") -> None:
        """Send a ``wake_event`` control frame as if the robot recognised a face."""
        if self.ws is None:
            raise RuntimeError("connect_ws() before inject_face()")
        self._face_injected = {
            "face_id": face_id,
            "confidence": float(confidence),
            "distance_m": float(distance_m),
            "gate": gate,
        }
        self.ws.send({
            "type": "control",
            "subtype": "wake_event",
            "data": self._face_injected,
        })

    def say(self, text_or_wav: str, *, audio_ms: int = 400,
            send_eou: bool = True) -> str:
        """Inject a user utterance. Returns the transcript that the server STT mock will see.

        ``text_or_wav`` is either a transcript string OR an absolute path to
        a WAV file under ``sim/scenarios/audio/``. We always ship silent PCM
        chunks on the wire — the scenario is expected to have called
        ``install_mocks(transcript=text)`` so the mock STT returns the
        intended phrase.
        """
        if self.ws is None:
            raise RuntimeError("connect_ws() before say()")

        # Send N x 20 ms chunks of silent PCM so the server's audio buffer is
        # non-empty when EoU fires (see _ingest_control: empty buf is a no-op).
        chunk = _silent_pcm(20)
        n = max(1, audio_ms // 20)
        import base64
        for i in range(n):
            self.ws.send({
                "type": "audio_chunk",
                "seq": i,
                "ts_ms": time.time() * 1000.0,
                "data": base64.b64encode(chunk).decode("ascii"),
            })
        if send_eou:
            self.ws.send({
                "type": "control",
                "subtype": "end_of_utterance",
                "data": {"robot_eou_hint": True,
                         "energy_floor": 240, "trail_ms": 320},
            })
        return text_or_wav

    def send_barge_in(self) -> None:
        """Send a ``barge_in`` control mid-TTS."""
        if self.ws is None:
            raise RuntimeError("connect_ws() before send_barge_in()")
        self.ws.send({
            "type": "control",
            "subtype": "barge_in",
            "data": {"reason": "test"},
        })

    def send_session_close(self) -> None:
        if self.ws is None:
            return
        self.ws.send({
            "type": "control",
            "subtype": "session_close",
            "data": {},
        })

    # ── expectations ────────────────────────────────────────────────────

    def expect(self, predicate: Callable[[dict[str, Any]], bool],
               timeout_s: float = 5.0, since: int = 0) -> dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("connect_ws() before expect()")
        return self.ws.wait_for(predicate, timeout_s, since=since)

    def cursor(self) -> int:
        """Return the current frames_snapshot length so callers can pass it
        as ``since=...`` on subsequent expect() calls."""
        if self.ws is None:
            return 0
        return len(self.ws.frames_snapshot())

    def assert_no_more_audio(self, timeout_s: float = 1.0) -> bool:
        """Verify that NO audio_chunk frame arrives within the window.

        Raises ``AssertionError`` if one does.
        """
        if self.ws is None:
            raise RuntimeError("connect_ws() before assert_no_more_audio()")
        baseline = len(self.ws.frames_snapshot())
        deadline = time.monotonic() + max(0.001, float(timeout_s))
        while time.monotonic() < deadline:
            time.sleep(0.05)
            for f in self.ws.frames_snapshot()[baseline:]:
                if f.get("type") == "audio_chunk":
                    raise AssertionError(
                        "unexpected audio_chunk after assertion: %r" % f
                    )
        return True


# ─────────────────────────────────────────────────────────────────────────────


def _make_fake_mp3() -> bytes:
    """Return a tiny non-empty bytes payload that looks vaguely MP3-shaped.

    The smoke tests use a similar magic-byte stub. Real MP3 sync isn't
    important — none of the WS pipeline decodes the audio in TestClient mode.
    """
    return b"\xff\xfb\x90\x00fake-mp3-payload"


# ─────────────────────────────────────────────────────────────────────────────


def predicate_control(subtype: str) -> Callable[[dict[str, Any]], bool]:
    """Helper: match any control frame whose subtype equals ``subtype``."""
    target = subtype

    def _p(f: dict[str, Any]) -> bool:
        return f.get("type") == "control" and f.get("subtype") == target
    return _p


def predicate_audio_chunk() -> Callable[[dict[str, Any]], bool]:
    def _p(f: dict[str, Any]) -> bool:
        return f.get("type") == "audio_chunk"
    return _p


def predicate_action(name: str | None = None) -> Callable[[dict[str, Any]], bool]:
    def _p(f: dict[str, Any]) -> bool:
        if f.get("type") != "action":
            return False
        return name is None or f.get("name") == name
    return _p


__all__ = [
    "Driver",
    "DriverUnavailable",
    "predicate_control",
    "predicate_audio_chunk",
    "predicate_action",
]
