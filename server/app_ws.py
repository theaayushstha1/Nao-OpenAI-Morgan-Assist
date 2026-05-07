"""FastAPI WebSocket transport — Phase 1 replacement for Flask /turn + /stream_turn.

Endpoints
---------
- ``GET  /health``  Liveness probe (no auth required).
- ``GET  /metrics`` Prometheus exposition (mounted from `server.metrics`).
- ``WS   /ws/{username}`` Long-lived bidirectional voice loop.

Frame envelope is defined in ``docs/PHASE_1_TASK_MAP.md`` and MUST match
exactly. Field names are load-bearing — the NAO client agent depends on them.

Per Phase 1 ownership, this module imports the agent runner, VAD, STT, and
filter helpers from ``server._legacy_helpers`` (verbatim copies of frozen
private helpers in ``server/server.py``). The legacy Flask app is untouched.

The handler streams TTS one sentence at a time:
  1. Run the agent graph in a worker thread (sync API).
  2. Slice the reply into sentence chunks (via ``streaming``).
  3. Synthesize each chunk in a worker thread (OpenAI TTS).
  4. Push one ``audio_chunk`` frame per sentence the moment it's ready.

Body actions accumulated in the agent context are flushed BEFORE the first
audio chunk so the robot can begin moving while it speaks.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable

from fastapi import (
    FastAPI,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from server import config, motion_trigger, openai_tts, safety
from server import _legacy_helpers as legacy

# ───────── observability adapters ─────────
#
# `server/metrics.py` and `server/logging_setup.py` are owned by other Phase-1
# agents (observability slug). We import lazily and fall back to no-op shims
# so this module boots even if those files don't exist yet — important for
# parallel agent execution where any one agent might land before another.

try:  # pragma: no cover — exercised via tests once observability lands
    from server import metrics as _metrics  # type: ignore[attr-defined]
    PROM_REGISTRY = getattr(_metrics, "PROM_REGISTRY", None)
    _phase_timer = getattr(_metrics, "phase_timer", None)
except Exception:  # noqa: BLE001
    _metrics = None
    PROM_REGISTRY = None
    _phase_timer = None


class _NullPhaseTimer:
    """No-op stand-in for ``metrics.phase_timer`` until the real one ships.

    Records the elapsed milliseconds in the ``phase_ms`` dict so the per-turn
    log event still has timing data even when Prometheus isn't wired.
    """

    __slots__ = ("_label", "_phase_ms", "_t0")

    def __init__(self, label: str, phase_ms: dict[str, float]) -> None:
        self._label = label
        self._phase_ms = phase_ms
        self._t0 = 0.0

    def __enter__(self):  # type: ignore[no-untyped-def]
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        # Always record into the per-turn dict (used by the structlog event).
        self._phase_ms[self._label] = round(elapsed_ms, 2)
        return False


def _phase(label: str, phase_ms: dict[str, float]):
    """Return a context manager that times a phase.

    Prefers ``metrics.phase_timer`` (Prometheus Histogram) when available;
    always also records the elapsed time into the per-turn ``phase_ms`` dict
    so the structured turn log retains timing data.
    """
    if _phase_timer is not None:
        try:
            real = _phase_timer(label)
            return _CombinedTimer(real, label, phase_ms)
        except Exception:  # pragma: no cover — defensive
            pass
    return _NullPhaseTimer(label, phase_ms)


class _CombinedTimer:
    """Chains the real ``metrics.phase_timer`` with our local phase_ms record."""

    __slots__ = ("_inner", "_label", "_phase_ms", "_t0")

    def __init__(self, inner: Any, label: str, phase_ms: dict[str, float]) -> None:
        self._inner = inner
        self._label = label
        self._phase_ms = phase_ms
        self._t0 = 0.0

    def __enter__(self):  # type: ignore[no-untyped-def]
        self._t0 = time.perf_counter()
        try:
            self._inner.__enter__()
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        try:
            self._inner.__exit__(exc_type, exc, tb)
        except Exception:
            pass
        self._phase_ms[self._label] = round(
            (time.perf_counter() - self._t0) * 1000.0, 2,
        )
        return False


try:  # pragma: no cover — exercised via tests once observability lands
    from server.logging_setup import logger as _structlog_logger  # type: ignore
except Exception:  # noqa: BLE001
    _structlog_logger = None


class _StdLogger:
    """Tiny adapter mimicking the structlog API surface this module uses."""

    def __init__(self) -> None:
        self._log = logging.getLogger("sage.app_ws")

    def info(self, event: str, **kwargs: Any) -> None:
        try:
            self._log.info("%s %s", event, json.dumps(kwargs, default=str))
        except Exception:
            self._log.info(event)

    def warning(self, event: str, **kwargs: Any) -> None:
        try:
            self._log.warning("%s %s", event, json.dumps(kwargs, default=str))
        except Exception:
            self._log.warning(event)

    def error(self, event: str, **kwargs: Any) -> None:
        try:
            self._log.error("%s %s", event, json.dumps(kwargs, default=str))
        except Exception:
            self._log.error(event)


logger = _structlog_logger if _structlog_logger is not None else _StdLogger()


# ───────── env-driven knobs ─────────

TTS_CHUNK_MIN_CHARS = int(os.environ.get("TTS_CHUNK_MIN_CHARS", "30"))
TTS_CHUNK_TIMEOUT_MS = int(os.environ.get("TTS_CHUNK_TIMEOUT_MS", "400"))


# ───────── auth ─────────

_OPEN_PATHS = {"/health", "/metrics"}


def _check_ws_auth(websocket: WebSocket) -> bool:
    """Validate the shared-secret on the WebSocket upgrade.

    Accepts the secret from either the ``X-NAO-Secret`` header (preferred
    for parity with HTTP) or the ``secret`` query string param (fallback for
    naoqi's WebSocket client which can't always set custom headers).
    """
    expected = config.NAO_SHARED_SECRET
    if not expected:
        return True
    got = websocket.headers.get("x-nao-secret", "")
    if got == expected:
        return True
    qp = websocket.query_params.get("secret", "")
    return qp == expected


# ───────── app factory ─────────

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    if not config.NAO_SHARED_SECRET:
        logging.getLogger("sage.app_ws").warning(
            "NAO_SHARED_SECRET unset — server is OPEN to anyone on the network. "
            "Set it in .env before exposing /ws/{username}.",
        )
    yield


app = FastAPI(
    title="NAO Morgan Assist — Phase 1 WebSocket transport",
    version="phase-1",
    lifespan=_lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": "phase-1"}


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus exposition endpoint.

    Delegates to ``server.metrics.PROM_REGISTRY`` once the observability
    agent ships that module. Until then, returns 503 so monitoring tools can
    detect the missing dependency cleanly. ``/metrics`` is intentionally in
    ``_OPEN_PATHS`` so Prometheus scrapers don't need the shared secret.
    """
    if PROM_REGISTRY is None:
        return Response(
            content=b"# metrics unavailable: server.metrics module not loaded\n",
            media_type="text/plain; version=0.0.4",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:  # pragma: no cover — exercised once observability lands
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        return Response(
            content=generate_latest(PROM_REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )
    except Exception as e:  # noqa: BLE001
        return Response(
            content="# metrics error: {0}\n".format(e).encode("utf-8"),
            media_type="text/plain; version=0.0.4",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


# ───────── WAV writing ─────────

# WS audio chunk format (per task map): 20 ms PCM16 mono @ 16 kHz, base64.
# 16 kHz × 2 bytes × 0.020 s = 640 bytes per chunk.
_WS_AUDIO_SR = 16_000
_WS_AUDIO_BYTES_PER_FRAME = 2  # PCM16 mono


def _write_pcm_to_wav(pcm: bytes, sr: int = _WS_AUDIO_SR) -> str:
    """Bundle the accumulated PCM bytes into a temp WAV file the legacy
    pipeline can consume.

    The legacy STT / VAD path (``has_voice``, ``transcribe``) is file-based.
    Rather than rewrite those for streaming bytes (Phase 2's job), Phase 1
    wraps the buffered chunks into a one-shot WAV per turn.
    """
    import wave
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="ws_turn_")
    try:
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(_WS_AUDIO_BYTES_PER_FRAME)
            w.setframerate(sr)
            w.writeframes(pcm)
        return path
    except Exception:
        try:
            os.unlink(path)
        except Exception:
            pass
        raise


# ───────── sentence chunker bridge ─────────

async def _stream_reply_sentences(reply: str) -> AsyncIterator[str]:
    """Yield TTS-ready sentence chunks from a finished agent reply.

    Prefers ``server.streaming.chunk_for_tts`` (the `tts-chunker` agent's
    async API contract: it accepts an ``AsyncIterator[str]`` and emits
    sentence-sized chunks) when available. Falls back to the existing
    synchronous ``iter_sentences`` helper otherwise — both are owned by the
    `tts-chunker` agent, so we tolerate either shape during the rollout.
    """
    if not reply:
        return

    chunker = None
    try:
        from server import streaming as _streaming
        chunker = getattr(_streaming, "chunk_for_tts", None)
    except Exception:  # noqa: BLE001
        chunker = None

    if chunker is not None and asyncio.iscoroutinefunction(chunker):
        async def _one_shot() -> AsyncIterator[str]:
            yield reply
        try:
            async for sent in chunker(  # type: ignore[misc]
                _one_shot(),
                min_chars=TTS_CHUNK_MIN_CHARS,
                timeout_ms=TTS_CHUNK_TIMEOUT_MS,
            ):
                if sent and sent.strip():
                    yield sent.strip()
            return
        except TypeError:
            # Signature differs from the documented contract — fall through
            # to the sync chunker.
            pass
        except Exception as e:  # noqa: BLE001 — defensive against in-flight rewrites
            logging.getLogger("sage.app_ws").warning(
                "chunk_for_tts failed (%s); falling back to iter_sentences", e,
            )

    # Synchronous fallback: use the existing iter_sentences generator.
    from server.streaming import iter_sentences

    def _sync_chunks() -> Iterable[str]:
        return iter_sentences(iter([reply]))

    for sent in await asyncio.to_thread(lambda: list(_sync_chunks())):
        if sent and sent.strip():
            yield sent.strip()


# ───────── frame helpers ─────────

async def _send_json(ws: WebSocket, payload: dict[str, Any]) -> None:
    """Send a JSON text frame, swallowing close-related errors."""
    try:
        await ws.send_text(json.dumps(payload, separators=(",", ":")))
    except (WebSocketDisconnect, RuntimeError):
        raise
    except Exception as e:  # noqa: BLE001
        logging.getLogger("sage.app_ws").warning("send_json failed: %s", e)


def _audio_chunk_frame(seq: int, text: str, mp3_bytes: bytes) -> dict[str, Any]:
    return {
        "type": "audio_chunk",
        "seq": seq,
        "format": "mp3",
        "text": text,
        "data": base64.b64encode(mp3_bytes).decode("ascii"),
    }


def _action_frame(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "action", "name": name, "args": args or {}}


def _control_frame(subtype: str, **data: Any) -> dict[str, Any]:
    return {"type": "control", "subtype": subtype, "data": data}


# ───────── per-session state ─────────

class _Session:
    """Per-WebSocket session state — one per connected user."""

    __slots__ = (
        "username", "session_id", "face_id", "hint",
        "asking_name",
        "audio_buf", "image_b64", "turn_idx",
        "out_seq",
    )

    def __init__(self, username: str) -> None:
        self.username = username
        self.session_id = str(uuid.uuid4())
        self.face_id: str | None = None
        self.hint: str | None = None
        self.asking_name: bool = False
        self.audio_buf = bytearray()
        self.image_b64: str | None = None
        self.turn_idx = 0
        self.out_seq = 0  # monotonic seq for outgoing audio_chunk frames

    def reset_turn(self) -> None:
        self.audio_buf = bytearray()
        self.image_b64 = None

    def next_seq(self) -> int:
        self.out_seq += 1
        return self.out_seq


# ───────── crisis path ─────────

async def _emit_crisis(ws: WebSocket, sess: _Session, transcript: str,
                       phase_ms: dict[str, float]) -> None:
    """Emit the hardcoded 988-hotline reply with TTS, plus a white-eye action."""
    sess.turn_idx += 1
    await _send_json(ws, _control_frame("crisis_lock",
                                        transcript=transcript,
                                        turn_idx=sess.turn_idx))
    await _send_json(ws, {
        "type": "control",
        "subtype": "transcript",
        "data": {"transcript": transcript,
                 "stt_ms": phase_ms.get("stt", 0)},
    })
    # Action so the robot's eyes shift while it speaks the hotline reply.
    await _send_json(ws, _action_frame("change_eye_color", {"color": "white"}))

    with _phase("tts_synth_first_chunk", phase_ms):
        mp3 = await asyncio.to_thread(openai_tts.synthesize, safety.HOTLINE_REPLY)
    if mp3:
        await _send_json(
            ws,
            _audio_chunk_frame(sess.next_seq(), safety.HOTLINE_REPLY, mp3),
        )
    legacy.LAST_REPLY[sess.username] = safety.HOTLINE_REPLY
    await _send_json(ws, _control_frame("tts_ended"))

    logger.info(
        "crisis_block",
        user=sess.username, session_id=sess.session_id,
        turn_idx=sess.turn_idx, phase_ms=phase_ms,
        transcript=transcript[:200],
        reply_preview=safety.HOTLINE_REPLY[:80],
        outcome="crisis",
    )


# ───────── motion-trigger short-circuit ─────────

async def _emit_motion(ws: WebSocket, sess: _Session, transcript: str,
                       motion: motion_trigger.MotionMatch,
                       phase_ms: dict[str, float]) -> None:
    sess.turn_idx += 1
    await _send_json(ws, {
        "type": "control",
        "subtype": "transcript",
        "data": {"transcript": transcript,
                 "stt_ms": phase_ms.get("stt", 0)},
    })
    # Action FIRST so the robot can begin the gesture as the ack starts.
    await _send_json(ws, _action_frame(motion.action, motion.args))

    with _phase("tts_synth_first_chunk", phase_ms):
        mp3 = await asyncio.to_thread(openai_tts.synthesize, motion.ack)
    if mp3:
        await _send_json(
            ws,
            _audio_chunk_frame(sess.next_seq(), motion.ack, mp3),
        )
    legacy.LAST_REPLY[sess.username] = motion.ack
    await _send_json(ws, _control_frame("tts_ended"))

    logger.info(
        "motion_match",
        user=sess.username, session_id=sess.session_id,
        turn_idx=sess.turn_idx, action=motion.action, args=motion.args,
        transcript=transcript[:200], reply_preview=motion.ack,
        phase_ms=phase_ms, outcome="motion_short_circuit",
    )


# ───────── full agent path ─────────

async def _emit_agent_turn(ws: WebSocket, sess: _Session,
                           transcript: str, image_b64: str | None,
                           phase_ms: dict[str, float],
                           t_user_done: float) -> None:
    """Run the agent graph, drain actions, stream sentence-by-sentence TTS."""
    sess.turn_idx += 1
    await _send_json(ws, {
        "type": "control",
        "subtype": "transcript",
        "data": {"transcript": transcript,
                 "stt_ms": phase_ms.get("stt", 0)},
    })

    with _phase("agent_complete", phase_ms):
        # `run_topology` is sync (calls asyncio.run inside) — keep it off the
        # event loop so we can keep handling incoming frames (barge-in).
        reply, active_agent, actions, suppress_image = await asyncio.to_thread(
            legacy.run_agent, sess.username, sess.hint, transcript, image_b64,
        )

    # Drain actions BEFORE the first audio chunk so the robot can prep
    # body movement during speech.
    with _phase("action_dispatch", phase_ms):
        for action in actions:
            name = action.get("name") if isinstance(action, dict) else None
            if not name:
                continue
            args = action.get("args") if isinstance(action, dict) else None
            await _send_json(ws, _action_frame(name, args or {}))

    await _send_json(ws, _control_frame(
        "agent_handoff",
        active_agent=active_agent,
        suppress_image=bool(suppress_image),
    ))

    await _send_json(ws, _control_frame("tts_started",
                                        active_agent=active_agent))

    first_chunk_emitted = False
    sent_count = 0
    tts_total_t0 = time.perf_counter()
    try:
        async for sentence in _stream_reply_sentences(reply):
            t_synth = time.perf_counter()
            mp3 = await asyncio.to_thread(openai_tts.synthesize, sentence)
            elapsed = (time.perf_counter() - t_synth) * 1000.0
            if not first_chunk_emitted:
                phase_ms["tts_synth_first_chunk"] = round(elapsed, 2)
                phase_ms["e2e_user_to_first_audio"] = round(
                    (time.perf_counter() - t_user_done) * 1000.0, 2,
                )
                first_chunk_emitted = True
            if not mp3:
                # TTS failed — emit a sentence-only control so the client can
                # at least log it; skip the audio chunk.
                await _send_json(ws, _control_frame(
                    "tts_chunk_skipped", text=sentence,
                ))
                continue
            await _send_json(
                ws,
                _audio_chunk_frame(sess.next_seq(), sentence, mp3),
            )
            sent_count += 1
    finally:
        phase_ms["tts_synth_total"] = round(
            (time.perf_counter() - tts_total_t0) * 1000.0, 2,
        )
        phase_ms["e2e_user_to_complete"] = round(
            (time.perf_counter() - t_user_done) * 1000.0, 2,
        )

    await _send_json(ws, _control_frame("tts_ended",
                                        sentences=sent_count,
                                        suppress_image=bool(suppress_image)))

    if reply:
        legacy.LAST_REPLY[sess.username] = reply

    logger.info(
        "turn_complete",
        user=sess.username, session_id=sess.session_id,
        turn_idx=sess.turn_idx, phase_ms=phase_ms,
        transcript=transcript[:200],
        reply_preview=(reply or "")[:80],
        active_agent=active_agent,
        actions=[(a.get("name") if isinstance(a, dict) else "?") for a in actions],
        outcome="ok",
    )


# ───────── per-turn pipeline ─────────

async def _process_turn(ws: WebSocket, sess: _Session) -> None:
    """Run the same pipeline as Flask /stream_turn:
    validate → has_voice → transcribe → reject → crisis → motion → agent.
    """
    if not sess.audio_buf:
        return

    pcm = bytes(sess.audio_buf)
    image_b64 = sess.image_b64
    sess.reset_turn()

    phase_ms: dict[str, float] = {}
    t_user_done = time.perf_counter()

    # Materialize PCM into a WAV for the legacy file-based STT/VAD path.
    wav_path: str | None = None
    try:
        wav_path = _write_pcm_to_wav(pcm)

        with _phase("vad", phase_ms):
            if not legacy.validate_wav(wav_path):
                logger.warning(
                    "turn_complete",
                    user=sess.username, session_id=sess.session_id,
                    turn_idx=sess.turn_idx + 1, phase_ms=phase_ms,
                    outcome="rejected", reject_reason="invalid_audio",
                )
                await _send_json(ws, _control_frame(
                    "transcript", transcript="", reject_reason="invalid_audio",
                ))
                return
            if not legacy.has_voice(wav_path):
                phase_ms["e2e_user_to_first_audio"] = round(
                    (time.perf_counter() - t_user_done) * 1000.0, 2,
                )
                logger.info(
                    "turn_complete",
                    user=sess.username, session_id=sess.session_id,
                    turn_idx=sess.turn_idx + 1, phase_ms=phase_ms,
                    outcome="rejected", reject_reason="no_voice",
                )
                await _send_json(ws, _control_frame(
                    "transcript", transcript="", reject_reason="no_voice",
                ))
                return

        with _phase("stt", phase_ms):
            transcript = await asyncio.to_thread(legacy.transcribe, wav_path)
    finally:
        if wav_path:
            try:
                os.unlink(wav_path)
            except Exception:
                pass

    reason = legacy.transcript_reject_reason(
        sess.username, transcript, asking_name=sess.asking_name,
    )
    if reason:
        logger.info(
            "turn_complete",
            user=sess.username, session_id=sess.session_id,
            turn_idx=sess.turn_idx + 1, phase_ms=phase_ms,
            transcript=(transcript or "")[:200],
            outcome="rejected", reject_reason=reason,
        )
        await _send_json(ws, _control_frame(
            "transcript", transcript=transcript, reject_reason=reason,
        ))
        return

    # Crisis FIRST — on the raw clip — so a partial like
    # "I keep thinking about" can't be quietly waited on.
    with _phase("crisis_check", phase_ms):
        crisis = await asyncio.to_thread(safety.crisis_check, transcript)
    if crisis.positive:
        legacy.consume_partial(sess.username, transcript)
        await _emit_crisis(ws, sess, transcript, phase_ms)
        return

    # Semantic endpointing — wait for more audio if the user trailed off.
    from server import semantic_endpoint
    if (semantic_endpoint.USE_SEMANTIC_ENDPOINT
            and not await asyncio.to_thread(
                semantic_endpoint.is_complete_thought, transcript)
            and not legacy.partial_wait_limit_hit(sess.username)):
        legacy.stash_partial(sess.username, transcript)
        await _send_json(ws, _control_frame(
            "transcript",
            transcript=transcript,
            wait=True,
            stt_ms=phase_ms.get("stt", 0),
        ))
        logger.info(
            "turn_complete",
            user=sess.username, session_id=sess.session_id,
            turn_idx=sess.turn_idx + 1, phase_ms=phase_ms,
            transcript=transcript[:200], outcome="rejected",
            reject_reason="wait_more_audio",
        )
        return

    # Stitch any buffered partial onto the current transcript.
    transcript = legacy.consume_partial(sess.username, transcript)

    # Motion-trigger short-circuit — bypass the LLM for clear body commands.
    with _phase("motion_trigger", phase_ms):
        motion = motion_trigger.detect(transcript)
    if motion is not None:
        await _emit_motion(ws, sess, transcript, motion, phase_ms)
        return

    await _emit_agent_turn(
        ws, sess, transcript, image_b64, phase_ms, t_user_done,
    )


# ───────── frame ingest ─────────

async def _ingest_frame(ws: WebSocket, sess: _Session,
                        frame: dict[str, Any]) -> bool:
    """Apply one inbound frame.

    Returns True to continue the loop, False if the session should close.
    """
    ftype = frame.get("type")

    if ftype == "audio_chunk":
        b64 = frame.get("data") or ""
        if not b64:
            return True
        try:
            sess.audio_buf.extend(base64.b64decode(b64))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "audio_decode_error",
                user=sess.username, error=repr(e),
            )
        return True

    if ftype == "image":
        b64 = frame.get("data") or ""
        if b64:
            sess.image_b64 = b64
        return True

    if ftype == "control":
        return await _ingest_control(ws, sess, frame)

    logger.warning("unknown_frame_type", user=sess.username, ftype=ftype)
    return True


async def _ingest_control(ws: WebSocket, sess: _Session,
                          frame: dict[str, Any]) -> bool:
    sub = frame.get("subtype")
    data = frame.get("data") or {}

    if sub == "session_open":
        sess.face_id = data.get("face_id") or sess.face_id
        sess.hint = data.get("hint") or None
        legacy.ensure_active_session(sess.username, sess.hint)
        await _send_json(ws, _control_frame(
            "session_open_ack",
            session_id=sess.session_id,
            face_id=sess.face_id,
            hint=sess.hint,
        ))
        logger.info(
            "session_open",
            user=sess.username, session_id=sess.session_id,
            face_id=sess.face_id, hint=sess.hint,
        )
        return True

    if sub == "session_close":
        await asyncio.to_thread(legacy.close_active_session, sess.username)
        await _send_json(ws, _control_frame(
            "session_end", session_id=sess.session_id,
        ))
        return False

    if sub == "wake_event":
        # Phase 1 just records this; the Phase 3 wake state machine consumes it.
        logger.info(
            "wake_event",
            user=sess.username, session_id=sess.session_id,
            face_id=data.get("face_id"), gate=data.get("gate"),
            confidence=data.get("confidence"), distance_m=data.get("distance_m"),
        )
        return True

    if sub == "barge_in":
        # Phase 1 records the event; Phase 2 will hook this into TTS abort.
        logger.info(
            "barge_in", user=sess.username, session_id=sess.session_id,
        )
        return True

    if sub == "mic_resumed":
        logger.info(
            "mic_resumed", user=sess.username, session_id=sess.session_id,
        )
        return True

    if sub == "end_of_utterance":
        sess.asking_name = bool(data.get("asking_name", sess.asking_name))
        await _process_turn(ws, sess)
        sess.asking_name = False
        return True

    logger.warning(
        "unknown_control_subtype",
        user=sess.username, subtype=sub,
    )
    return True


# ───────── WS endpoint ─────────

@app.websocket("/ws/{username}")
async def ws_handler(websocket: WebSocket, username: str) -> None:
    if not _check_ws_auth(websocket):
        await websocket.close(code=4401)  # custom: unauthorized
        return

    await websocket.accept()
    sess = _Session(username=username or "guest")

    logger.info(
        "ws_connected", user=sess.username, session_id=sess.session_id,
    )

    # Wait for the FIRST frame and require it to be `session_open` per spec.
    first_raw: str | None = None
    try:
        first_raw = await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info(
            "ws_disconnected_pre_handshake",
            user=sess.username, session_id=sess.session_id,
        )
        return

    try:
        first = json.loads(first_raw)
    except Exception:
        await websocket.close(code=4400)  # bad request
        return
    if not (isinstance(first, dict)
            and first.get("type") == "control"
            and first.get("subtype") == "session_open"):
        await websocket.close(code=4400)
        return

    await _ingest_control(websocket, sess, first)

    # Main receive loop.
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            logger.info(
                "turn_complete",
                user=sess.username, session_id=sess.session_id,
                turn_idx=sess.turn_idx, outcome="client_dropped",
            )
            return

        try:
            frame = json.loads(raw)
        except Exception:
            logger.warning("malformed_json", user=sess.username)
            continue
        if not isinstance(frame, dict):
            continue

        try:
            keep = await _ingest_frame(websocket, sess, frame)
        except WebSocketDisconnect:
            logger.info(
                "turn_complete",
                user=sess.username, session_id=sess.session_id,
                turn_idx=sess.turn_idx, outcome="client_dropped",
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.error(
                "turn_error",
                user=sess.username, session_id=sess.session_id,
                turn_idx=sess.turn_idx, error=repr(e),
            )
            try:
                await _send_json(websocket, _control_frame(
                    "session_end", reason="server_error", error=repr(e),
                ))
            except Exception:
                pass
            try:
                await websocket.close(code=1011)  # internal error
            except Exception:
                pass
            return

        if not keep:
            try:
                await websocket.close(code=1000)
            except Exception:
                pass
            return


__all__ = ["app"]
