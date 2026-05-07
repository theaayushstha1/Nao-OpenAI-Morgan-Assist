# -*- coding: utf-8 -*-
"""Long-lived WebSocket client between the NAO robot and the FastAPI server.

Runs on the robot under naoqi's bundled Python 2.7. Handles a single
persistent voice loop: outbound mic chunks + control frames, inbound TTS
audio + body actions + server controls. Reconnects with backoff. Coordinates
mic gating so NAO does not record itself while it is speaking.

Frame envelope and field names are pinned to ``docs/PHASE_1_TASK_MAP.md``.
The server (``server/app_ws.py``) parses these strictly; do not rename keys
without coordinating with the ``fastapi-app`` agent.

Counterparts (sibling agents in Phase 1):
    nao.audio_module.NaoAudioStreamer     ALModule mic streamer + gate
    nao.stream_tts.StreamTtsPlayer        sentence-chunk MP3 player
    nao.utils.nao_execute.run             body action dispatcher
    nao.logger.get_logger                 rotating JSONL structured log
"""
from __future__ import print_function

import base64
import json
import os
import threading
import time

try:
    import Queue as _queue  # py2 stdlib name
except ImportError:  # pragma: no cover - py3 dev fallback
    import queue as _queue

# websocket-client 0.59.0 is the last py2.7-compatible release. On a Mac
# dev environment the module may not be installed; keep the import guarded
# so `python -m py_compile nao/ws_client.py` still passes there.
try:
    import websocket  # type: ignore
except ImportError:  # pragma: no cover - dev environments only
    websocket = None


# ---------------------------------------------------------------------------
# Logger fallback
# ---------------------------------------------------------------------------
# `nao.logger.get_logger()` is owned by the `nao-logger-main` agent. While
# that file is being authored in parallel, fall back to a tiny adapter that
# prints structured records via `print()` so this module is usable on its
# own (smoke runs, py_compile checks, dev imports).
try:
    from nao import logger as _nao_logger  # type: ignore
    _get_logger = getattr(_nao_logger, "get_logger", None)
except Exception:  # pragma: no cover - logger not yet present
    _get_logger = None


class _PrintFallbackLogger(object):
    """Minimal stand-in for ``structlog``-style ``BoundLogger``."""

    def __init__(self, name):
        self.name = name

    def _emit(self, level, event, **kw):
        try:
            payload = {"level": level, "event": event, "logger": self.name}
            payload.update(kw)
            print("[{0}] {1}".format(self.name, json.dumps(payload, default=repr)))
        except Exception:
            print("[{0}] {1} {2}".format(self.name, level, event))

    def debug(self, event, **kw):
        self._emit("debug", event, **kw)

    def info(self, event, **kw):
        self._emit("info", event, **kw)

    def warn(self, event, **kw):
        self._emit("warn", event, **kw)

    warning = warn

    def error(self, event, **kw):
        self._emit("error", event, **kw)


def _resolve_logger(name):
    if _get_logger is None:
        return _PrintFallbackLogger(name)
    try:
        return _get_logger(name)
    except Exception:
        return _PrintFallbackLogger(name)


# ---------------------------------------------------------------------------
# Env-driven knobs (mirrors `runner-config` agent's server-side defaults so
# the robot makes the same assumptions when the env var is absent).
# ---------------------------------------------------------------------------

def _parse_backoff(spec):
    """Parse '300,600,1200,2400' -> [0.3, 0.6, 1.2, 2.4] seconds. Filter junk."""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ms = int(part)
        except (TypeError, ValueError):
            continue
        if ms <= 0:
            continue
        out.append(ms / 1000.0)
    if not out:
        out = [0.3, 0.6, 1.2, 2.4]
    return out


_DEFAULT_BACKOFF_MS = "300,600,1200,2400"
_DEFAULT_GRACE_MS = "200"


def _grace_seconds():
    raw = os.environ.get("MIC_GATE_GRACE_MS", _DEFAULT_GRACE_MS)
    try:
        return max(0, int(raw)) / 1000.0
    except (TypeError, ValueError):
        return 0.2


def _backoff_schedule():
    return _parse_backoff(os.environ.get("WS_RECONNECT_BACKOFF_MS", _DEFAULT_BACKOFF_MS))


# ---------------------------------------------------------------------------
# Frame factory helpers — keep the field names byte-for-byte identical to
# docs/PHASE_1_TASK_MAP.md. Server parses by string match.
# ---------------------------------------------------------------------------

def _audio_chunk_frame(seq, ts_ms, b64_pcm):
    return {
        "type": "audio_chunk",
        "seq": int(seq),
        "ts_ms": float(ts_ms),
        "data": b64_pcm,
    }


def _control_frame(subtype, data=None):
    payload = data if isinstance(data, dict) else {}
    return {
        "type": "control",
        "subtype": subtype,
        "data": payload,
    }


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class NaoWsClient(object):
    """Long-lived WebSocket session manager.

    Public contract is the constructor + ``run()``. ``run()`` blocks the
    caller (``nao/main.py`` after ``nao-logger-main`` rewires it) and only
    returns when ``self.shutdown_event`` is set.

    The client owns two background threads while a connection is up: a
    sender that drains mic chunks + a control queue, and a receiver that
    routes server frames. On disconnect it joins both, sleeps the next
    backoff slot, and reconnects, repeating the last backoff value forever
    after the schedule is exhausted (so the robot self-heals when the
    server comes back online without us having to choose an arbitrary
    "give up" point).
    """

    def __init__(self, server_url, username, shared_secret,
                 audio_streamer, tts_player, action_dispatcher, brain_cache,
                 hint=None, logger=None):
        self.server_url = server_url
        self.username = username or "guest"
        self.shared_secret = shared_secret or ""
        self.audio_streamer = audio_streamer
        self.tts_player = tts_player
        self.action_dispatcher = action_dispatcher
        self.brain_cache = brain_cache
        self.hint = hint

        self.shutdown_event = threading.Event()
        self.log = logger if logger is not None else _resolve_logger("nao.ws_client")

        # Outbound queue for control frames pushed from the audio module
        # (``barge_in``, ``end_of_utterance``, ``wake_event``, ``mic_resumed``,
        # ``session_close``, etc). Audio chunks come straight from the
        # streamer iterator on the sender thread; mixing both into one queue
        # would back-pressure mic delivery. Two paths, single ws.send call
        # site, lock-protected.
        self._control_queue = _queue.Queue()

        # The active websocket reference is set/cleared by the connect loop
        # so the receiver and sender can both call .send/.recv safely.
        self._ws = None
        self._ws_lock = threading.Lock()

        # Sender/receiver threads — owned per-connection. Recreated each
        # successful upgrade.
        self._sender_thread = None
        self._receiver_thread = None
        self._barge_thread = None

        # Connection-scoped flag flipped by tts_started / tts_ended frames.
        # The barge thread checks this in addition to tts_player.is_playing()
        # so we react to server intent even before the first MP3 hits the
        # ALAudioPlayer queue.
        self._tts_active = threading.Event()

        # Track the latest scheduled mic-gate-open timer so a back-to-back
        # tts_ended can cancel the prior one. Otherwise the "open mic in
        # 200ms" callback from sentence N could race the close from
        # sentence N+1's start.
        self._mic_open_timer = None
        self._mic_timer_lock = threading.Lock()

    # ------------------------------------------------------------------
    # External: queue a control frame from anywhere on the robot side
    # (e.g. audio_module pushing wake_event, end_of_utterance from the
    # VAD). Thread-safe.
    # ------------------------------------------------------------------
    def push_control(self, subtype, data=None):
        try:
            self._control_queue.put_nowait(_control_frame(subtype, data))
        except _queue.Full:  # bounded queues; we use unbounded but be safe
            self.log.warn("control_queue_full", subtype=subtype)

    # ------------------------------------------------------------------
    # Frame send/recv primitives
    # ------------------------------------------------------------------
    def _send_json(self, ws, obj):
        if ws is None:
            return False
        try:
            payload = json.dumps(obj)
        except Exception as exc:
            self.log.error("frame_serialize_failed", error=str(exc),
                           type=obj.get("type") if isinstance(obj, dict) else None)
            return False
        try:
            with self._ws_lock:
                ws.send(payload)
        except Exception as exc:
            self.log.warn("frame_send_failed", error=str(exc),
                          type=obj.get("type") if isinstance(obj, dict) else None)
            return False
        # Per-frame DEBUG log: keep it small (no payload, just type/subtype).
        try:
            kind = obj.get("type") if isinstance(obj, dict) else None
            sub = obj.get("subtype") if isinstance(obj, dict) else None
            self.log.debug("ws_send", type=kind, subtype=sub,
                           seq=obj.get("seq") if isinstance(obj, dict) else None)
        except Exception:
            pass
        return True

    def _recv_loop(self, ws):
        while not self.shutdown_event.is_set():
            try:
                raw = ws.recv()
            except Exception as exc:
                # websocket.WebSocketConnectionClosedException covers the
                # clean-close case; OSError / socket.error covers abrupt
                # network drops. Either way, fall out and let the outer
                # connect-loop reconnect.
                if websocket is not None and isinstance(
                        exc, getattr(websocket, "WebSocketConnectionClosedException",
                                     Exception)):
                    self.log.info("ws_closed", reason="server")
                else:
                    self.log.warn("ws_recv_error", error=str(exc))
                return
            if not raw:
                # ws.recv() returns "" on close in websocket-client 0.59.0
                self.log.info("ws_closed", reason="empty_frame")
                return
            try:
                frame = json.loads(raw)
            except Exception as exc:
                self.log.warn("frame_parse_failed", error=str(exc),
                              raw_len=len(raw) if raw is not None else 0)
                continue
            self._handle_frame(frame)

    # ------------------------------------------------------------------
    # Server -> client routing
    # ------------------------------------------------------------------
    def _handle_frame(self, frame):
        if not isinstance(frame, dict):
            self.log.warn("frame_not_dict", got_type=type(frame).__name__)
            return
        ftype = frame.get("type")
        try:
            self.log.debug("ws_recv", type=ftype, subtype=frame.get("subtype"),
                           seq=frame.get("seq"))
        except Exception:
            pass

        if ftype == "audio_chunk":
            self._handle_audio_chunk(frame)
        elif ftype == "action":
            self._handle_action(frame)
        elif ftype == "control":
            self._handle_control(frame)
        else:
            self.log.warn("frame_type_unknown", type=ftype)

    def _handle_audio_chunk(self, frame):
        """Server sent us a sentence-chunk MP3. Hand to the TTS player."""
        if self.tts_player is None:
            return
        b64 = frame.get("data") or ""
        text = frame.get("text") or ""
        try:
            mp3_bytes = base64.b64decode(b64) if b64 else b""
        except Exception as exc:
            self.log.warn("audio_chunk_b64_decode_failed", error=str(exc))
            return
        try:
            self.tts_player.enqueue(text, mp3_bytes)
        except Exception as exc:
            self.log.error("tts_enqueue_failed", error=str(exc))

    def _handle_action(self, frame):
        """Body action — dispatch immediately, do not wait on TTS audio."""
        if self.action_dispatcher is None:
            return
        name = frame.get("name")
        args = frame.get("args") or {}
        try:
            self.action_dispatcher(name, args)
        except TypeError:
            # Some dispatcher signatures take a single dict — fall back
            # rather than crash the receiver loop.
            try:
                self.action_dispatcher({"name": name, "args": args})
            except Exception as exc:
                self.log.error("action_dispatch_failed", name=name,
                               error=str(exc))
        except Exception as exc:
            self.log.error("action_dispatch_failed", name=name, error=str(exc))

    def _handle_control(self, frame):
        sub = frame.get("subtype")
        data = frame.get("data") or {}

        if sub == "tts_started":
            self._on_tts_started(data)
        elif sub == "tts_ended":
            self._on_tts_ended(data)
        elif sub == "crisis_lock":
            self._on_crisis_lock(data)
        elif sub == "transcript":
            # Transcript is for client-side logging only. Phase 1 keeps the
            # robot dumb about transcript content; future phases (3, 8) can
            # consume this for LED/UI cues.
            self.log.info("transcript",
                          transcript=data.get("transcript", ""),
                          stt_ms=data.get("stt_ms"))
        elif sub == "session_end":
            self.log.info("server_session_end", reason=data.get("reason"))
        elif sub == "agent_handoff":
            self.log.info("agent_handoff", **{k: data.get(k) for k in
                                              ("from", "to", "reason")
                                              if k in data})
        else:
            self.log.warn("control_subtype_unknown", subtype=sub)

    # --- TTS gating: close mic on start, reopen on end + grace ---
    def _on_tts_started(self, data):
        self._tts_active.set()
        self._cancel_pending_mic_open()
        try:
            if self.audio_streamer is not None:
                self.audio_streamer.gate(True)  # close mic
        except Exception as exc:
            self.log.error("mic_gate_close_failed", error=str(exc))
        self.log.info("tts_started", text_preview=str(data.get("text") or "")[:80])

    def _on_tts_ended(self, data):
        self._tts_active.clear()
        grace_s = _grace_seconds()

        def _open_mic():
            try:
                if self.audio_streamer is not None:
                    self.audio_streamer.gate(False)  # open mic
                self.push_control("mic_resumed",
                                  {"grace_ms": int(grace_s * 1000)})
                self.log.info("mic_resumed", grace_ms=int(grace_s * 1000))
            except Exception as exc:
                self.log.error("mic_gate_open_failed", error=str(exc))

        if grace_s <= 0:
            _open_mic()
            return

        # Schedule the reopen so the tail-end of TTS audio (already queued
        # in ALAudioPlayer) has time to drain through the speaker before
        # the mic is live again. Cancel any prior pending timer first so
        # back-to-back sentence chunks coalesce into a single reopen.
        self._cancel_pending_mic_open()
        timer = threading.Timer(grace_s, _open_mic)
        timer.daemon = True
        with self._mic_timer_lock:
            self._mic_open_timer = timer
        timer.start()

    def _cancel_pending_mic_open(self):
        with self._mic_timer_lock:
            timer = self._mic_open_timer
            self._mic_open_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _on_crisis_lock(self, data):
        """Server flagged a crisis. End the local turn immediately.

        The actual 988 hotline reply audio is already on its way as
        regular ``audio_chunk`` frames; we just stop *future* mic frames
        from being treated as user input until the server re-opens the
        session. The TTS player keeps playing whatever is in flight.
        """
        self.log.warn("crisis_lock_received", reason=data.get("reason"))
        try:
            if self.audio_streamer is not None:
                self.audio_streamer.gate(True)
        except Exception as exc:
            self.log.error("crisis_lock_gate_failed", error=str(exc))
        # Drop any pending mic-open timer so we don't accidentally reopen
        # the mic in 200ms.
        self._cancel_pending_mic_open()

    # ------------------------------------------------------------------
    # Sender thread: mic chunks + queued control frames
    # ------------------------------------------------------------------
    def _send_loop(self, ws):
        if self.audio_streamer is None:
            self.log.warn("no_audio_streamer", note="sender will only forward control frames")
            self._send_only_control_loop(ws)
            return
        try:
            chunk_iter = self.audio_streamer.read_chunks()
        except Exception as exc:
            self.log.error("audio_stream_open_failed", error=str(exc))
            self._send_only_control_loop(ws)
            return

        for chunk in chunk_iter:
            if self.shutdown_event.is_set():
                break
            # Drain any control frames first so wake_event / barge_in
            # arrive ahead of audio when both fire in the same poll.
            self._drain_control_queue(ws)

            if not self._send_audio_chunk(ws, chunk):
                # Break out so the outer connect loop can reconnect; the
                # streamer iterator will be reopened on the next attempt.
                return
        # Streamer exhausted (typically: process tearing down). Drain any
        # remaining control frames before we let the receiver close.
        self._drain_control_queue(ws)

    def _send_only_control_loop(self, ws):
        """Used when the audio streamer isn't available (e.g. mic init
        failed). The control queue still needs servicing so wake events
        and barge-in still reach the server."""
        while not self.shutdown_event.is_set():
            try:
                frame = self._control_queue.get(timeout=0.1)
            except _queue.Empty:
                continue
            if not self._send_json(ws, frame):
                return

    def _drain_control_queue(self, ws):
        # Non-blocking drain: send everything currently buffered, but
        # don't block on more arriving — that's what the audio loop is for.
        while True:
            try:
                frame = self._control_queue.get_nowait()
            except _queue.Empty:
                return
            if not self._send_json(ws, frame):
                return

    def _send_audio_chunk(self, ws, chunk):
        seq, ts_ms, b64_pcm = self._unpack_chunk(chunk)
        if b64_pcm is None:
            # Streamer returned a sentinel (e.g. silence dropped on the
            # floor by the gate). Skip silently — DEBUG log already at
            # streamer side.
            return True
        return self._send_json(ws, _audio_chunk_frame(seq, ts_ms, b64_pcm))

    @staticmethod
    def _unpack_chunk(chunk):
        """Accept (seq, ts_ms, b64) tuples or dicts with the same keys."""
        if isinstance(chunk, dict):
            return (chunk.get("seq", 0), chunk.get("ts_ms", 0.0),
                    chunk.get("data"))
        if isinstance(chunk, (list, tuple)) and len(chunk) >= 3:
            return chunk[0], chunk[1], chunk[2]
        # Unknown shape — drop, but keep the loop alive.
        return 0, 0.0, None

    # ------------------------------------------------------------------
    # Barge-in watcher: speech onset during TTS -> tell server, stop TTS
    # ------------------------------------------------------------------
    def _barge_loop(self):
        if self.audio_streamer is None or self.tts_player is None:
            return
        speech_onset = getattr(self.audio_streamer, "speech_onset", None)
        if not callable(speech_onset):
            # No detector exposed by the streamer — Phase 3 will add the
            # full detector. For now barge-in coordination relies on the
            # server-side echo guard + the server pushing barge_in itself.
            self.log.debug("barge_loop_disabled",
                           reason="audio_streamer.speech_onset not available")
            return
        is_playing = getattr(self.tts_player, "is_playing", lambda: False)
        last_fire = 0.0
        cooldown_s = 0.6  # don't spam barge_in frames during a single overlap

        while not self.shutdown_event.is_set():
            try:
                playing = bool(is_playing()) or self._tts_active.is_set()
            except Exception:
                playing = self._tts_active.is_set()
            if not playing:
                time.sleep(0.05)
                continue
            try:
                onset = bool(speech_onset())
            except Exception as exc:
                self.log.warn("speech_onset_failed", error=str(exc))
                onset = False
            now = time.time()
            if onset and (now - last_fire) > cooldown_s:
                last_fire = now
                self.push_control("barge_in", {"detector": "robot_local"})
                try:
                    self.tts_player.stop()
                except Exception as exc:
                    self.log.error("tts_stop_failed", error=str(exc))
                self.log.info("barge_in_local")
            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def _connect_once(self):
        if websocket is None:
            self.log.error("websocket_module_missing",
                           hint="install websocket-client==0.59.0 on the robot")
            return False

        headers = []
        if self.shared_secret:
            headers.append("X-NAO-Secret: {0}".format(self.shared_secret))

        # websocket-client 0.59.0: use create_connection for a blocking
        # synchronous handle. WebSocketApp's threaded callback model is
        # also fine, but create_connection lines up cleanly with an
        # explicit recv() loop and is easier to reason about with our
        # own threads.
        try:
            ws = websocket.create_connection(
                self.server_url,
                header=headers,
                timeout=10,
            )
        except Exception as exc:
            self.log.warn("ws_connect_failed",
                          url=self.server_url, error=str(exc))
            return False

        self.log.info("ws_connected", url=self.server_url, user=self.username)
        with self._ws_lock:
            self._ws = ws

        # Hello frame. The server uses face_id + brain_version to decide
        # whether to ship cache deltas back during Phase 7. For Phase 1
        # they're advisory.
        if not self._send_session_open(ws):
            self._teardown_ws()
            return False

        # Spawn workers and block until the receiver returns (server close
        # or transport error).
        self._tts_active.clear()
        self._spawn_workers(ws)
        try:
            self._receiver_thread.join()
        finally:
            self._teardown_ws()
            self._join_workers()
        return True

    def _spawn_workers(self, ws):
        self._receiver_thread = threading.Thread(
            target=self._recv_loop, args=(ws,), name="nao-ws-recv")
        self._receiver_thread.daemon = True
        self._receiver_thread.start()

        self._sender_thread = threading.Thread(
            target=self._send_loop, args=(ws,), name="nao-ws-send")
        self._sender_thread.daemon = True
        self._sender_thread.start()

        self._barge_thread = threading.Thread(
            target=self._barge_loop, name="nao-ws-barge")
        self._barge_thread.daemon = True
        self._barge_thread.start()

    def _join_workers(self):
        # 1s join — threads are daemon and will get nuked on process exit
        # if they refuse to wake up, but in practice the recv loop exits
        # on first failed recv() and the sender exits when its iterator
        # closes (audio_streamer should propagate the shutdown via its own
        # gate/close).
        for t in (self._sender_thread, self._receiver_thread, self._barge_thread):
            if t is None:
                continue
            try:
                t.join(timeout=1.0)
            except Exception:
                pass
        self._sender_thread = None
        self._receiver_thread = None
        self._barge_thread = None

    def _teardown_ws(self):
        with self._ws_lock:
            ws = self._ws
            self._ws = None
        if ws is None:
            return
        try:
            ws.close()
        except Exception:
            pass
        # Cancel any pending mic-open timer so a half-open mic doesn't
        # outlive the connection.
        self._cancel_pending_mic_open()

    def _send_session_open(self, ws):
        face_id = self._cache_get("face_id")
        brain_version = self._cache_get("brain_version", 0)
        data = {
            "face_id": face_id,
            "brain_version": brain_version,
            "hint": self.hint,
        }
        # Optional: include a brain summary so server can decide if we
        # need a sync delta. brain_cache.summary() is added in Phase 7;
        # tolerate its absence.
        try:
            summary = self.brain_cache.summary() if self.brain_cache is not None else None
            if summary is not None:
                data["brain_summary"] = summary
        except Exception as exc:
            self.log.debug("brain_summary_unavailable", error=str(exc))
        return self._send_json(ws, _control_frame("session_open", data))

    def _cache_get(self, key, default=None):
        cache = self.brain_cache
        if cache is None:
            return default
        # Support either dict-style .get(key) or direct attribute access.
        try:
            getter = getattr(cache, "get", None)
            if callable(getter):
                return getter(key, default) if default is not None else getter(key)
            return getattr(cache, key, default)
        except Exception:
            return default

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self):
        """Block forever, reconnecting per ``WS_RECONNECT_BACKOFF_MS``.

        Returns when ``shutdown_event`` is set. Threads spawned per
        connection are daemon and joined within 1 s on each disconnect.
        """
        backoff = _backoff_schedule()
        idx = 0
        while not self.shutdown_event.is_set():
            connected = self._connect_once()
            if self.shutdown_event.is_set():
                break
            # Either connect failed or the connection dropped. Pick the
            # next backoff slot; after we exhaust the schedule, keep
            # using the last value forever (the spec calls this out).
            wait_s = backoff[idx] if idx < len(backoff) else backoff[-1]
            self.log.warn("ws_reconnect_scheduled",
                          attempt=idx + 1,
                          wait_s=wait_s,
                          url=self.server_url,
                          last_attempt_connected=bool(connected))
            # Sleep in small slices so shutdown_event short-circuits us
            # without waiting out the full backoff.
            slept = 0.0
            slice_s = 0.1
            while slept < wait_s and not self.shutdown_event.is_set():
                time.sleep(slice_s)
                slept += slice_s
            idx = min(idx + 1, len(backoff) - 1)

        # Final teardown when run() exits.
        self._teardown_ws()
        try:
            if self.tts_player is not None and hasattr(self.tts_player, "stop"):
                self.tts_player.stop()
        except Exception:
            pass
        self.log.info("ws_client_stopped")

    def shutdown(self):
        """Public helper so main.py can signal a clean exit."""
        self.shutdown_event.set()
        self._cancel_pending_mic_open()
        # Push a session_close hint so the server can finalize the
        # transcript / save recap before the socket closes.
        self.push_control("session_close", {"reason": "shutdown"})
