# -*- coding: utf-8 -*-
"""Long-lived WebSocket client between the NAO robot and the FastAPI server.

Runs on the robot under naoqi's bundled Python 2.7. Handles a single
persistent voice loop: outbound mic chunks + control frames, inbound TTS
audio + body actions + server controls. Reconnects with backoff. Coordinates
mic gating so NAO does not record itself while it is speaking.

Frame envelope and field names are pinned to ``docs/PHASE_1_TASK_MAP.md``.
The server (``server/app_ws.py``) parses these strictly; do not rename keys
without coordinating with the ``fastapi-app`` agent.

Phase 7 — Robot-Side Brain handshake (``docs/PHASE_7_TASK_MAP.md``):

* ``session_open`` now carries an optional ``brain_summary`` field built
  from ``brain_cache.summary()`` (face_id_count, version,
  last_seen_iso_per_face, brain_size_bytes). The server uses it to decide
  whether to ship a ``brain_sync`` delta back. If the cache implementation
  does not yet expose ``summary()``, the field is omitted — backwards
  compatible with the Phase 1 handshake.
* Inbound ``control { subtype: "brain_sync" }`` frames carry a
  ``data.updates`` dict that we forward to ``brain_cache.apply_updates``
  and persist via ``brain_cache.save``. The save is dispatched on a
  daemon thread so the receiver loop is never blocked on disk I/O.
* ``_handle_brain_sync`` routes the frame; both ``apply_updates`` and
  ``save`` failures are logged and swallowed so a corrupt push cannot
  kill the WS session.

Counterparts (sibling agents in Phase 1):
    nao.audio_module.NaoAudioStreamer     ALModule mic streamer + gate
    nao.stream_tts.StreamTtsPlayer        sentence-chunk MP3 player
    nao.utils.nao_execute.run             body action dispatcher
    nao.logger.get_logger                 rotating JSONL structured log

Counterparts (Phase 7 sibling agents):
    nao.utils.brain.BrainCache            ``summary``/``apply_updates``/``save``
    server.app_ws                         emits ``brain_sync`` after session_open
    server.session.pull_brain_updates     builds the delta payload
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


def _image_frame(b64_jpeg):
    return {"type": "image", "data": b64_jpeg}


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

        # Action worker thread + queue. Body actions (gestures, dances,
        # follow-me) come in over the WS as `action` frames. The
        # dispatcher can block for seconds on naoqi joint moves; running
        # it on the recv thread freezes audio + control reception. We
        # serialize through a dedicated worker so recv stays hot AND
        # actions don't trample each other on shared joint resources.
        self._action_queue = _queue.Queue()
        self._action_worker_thread = None

        # nao-therapy: mic liveness watchdog (see _mic_watchdog_loop).
        self._mic_watchdog_thread = None

        # Post-playback waiter thread. tts_ended from the server only
        # means "server is done streaming" — it does NOT mean local
        # playback is finished. The tts_player can still have several
        # MP3 chunks queued. We poll tts_player.is_playing() until it
        # returns False, then arm the grace timer, then open the mic.
        # Without this, the mic was opening while the speaker was still
        # broadcasting NAO's own voice, which then bounced into the mic
        # and got transcribed back as "self_echo".
        self._mic_resume_waiter = None
        self._mic_resume_waiter_stop = threading.Event()

        # ProcessingAnnouncer — disabled per operator request. The
        # built-in ALTextToSpeech voice clashed with ElevenLabs and
        # fillers were firing mid-conversation. Code path stays wired
        # (transcript handler still checks for non-None announcer,
        # audio_chunk handler still kills it) so re-enabling is a
        # one-line flip: instantiate ProcessingAnnouncer here.
        self._announcer = None
        # Per-turn flag: was the announcer started for this turn? Reset
        # when the next transcript arrives. Used to avoid stop()'ing a
        # never-started announcer (no-op, but cleaner logging).
        self._announcer_active = False
        # Per-turn flag flipped True the moment the first reply audio
        # chunk for the current turn arrives. While True, the filler is
        # NOT allowed to start (avoids the case where transcript fires,
        # filler kicks in 1.5 s later, and by then the real reply is
        # already mid-play). Reset when the next transcript arrives.
        self._reply_audio_arrived = False

        # Speaking-gesture loop state. While TTS is active, a daemon
        # thread picks a random body-language gesture from the canonical
        # 10-intent table every ~2.5 s. ``_speaking_gesture_stop`` is set
        # on tts_ended / barge to cleanly exit the loop. Stand-up
        # posture is also fired once when TTS first starts so gestures
        # have presence (sitting NAO with arm-waving looks weird).
        self._speaking_gesture_thread = None
        self._speaking_gesture_stop = threading.Event()
        self._stood_up_once = False

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

    def _snap_and_push_image(self, reason="turn"):
        """Capture a JPEG from NAO's front camera and queue it as an
        ``image`` frame for the server. Vision auto-runs server-side
        when ``sess.image_b64`` is set (see app_ws.py:_ingest_frame).

        Runs on a daemon thread so the ~150-300 ms ALPhotoCapture call
        doesn't block the receiver thread or delay the next audio chunk.
        Best-effort — failures are debug-logged and non-fatal.
        """
        def _do_snap():
            try:
                from utils import camera_capture
                import os as _os
                ip = _os.environ.get("NAO_IP", "127.0.0.1")
                # Snap to a tmp path. snap_quick already writes the
                # JPEG to disk; we read it and base64 it for the WS.
                path = camera_capture.snap_quick(ip, 9559)
                if not path:
                    self.log.debug("snap_image_path_empty", reason=reason)
                    return
                try:
                    with open(path, "rb") as fh:
                        raw = fh.read()
                except Exception as exc:
                    self.log.debug("snap_image_read_failed",
                                   reason=reason, error=str(exc))
                    return
                if not raw:
                    return
                b64 = base64.b64encode(raw)
                if isinstance(b64, bytes):
                    try:
                        b64 = b64.decode("ascii")
                    except Exception:
                        b64 = str(b64)
                # Push directly through the WS (not the control queue —
                # this is a top-level image frame, not a control subframe).
                ws = None
                with self._ws_lock:
                    ws = self._ws
                if ws is None:
                    return
                ok = self._send_json(ws, _image_frame(b64))
                if ok:
                    import sys as _sys
                    print("[vision] image snapped + sent ({0} bytes b64, "
                          "reason={1})".format(len(b64), reason))
                    _sys.stderr.flush()
                else:
                    self.log.debug("snap_image_send_failed", reason=reason)
                # Best-effort cleanup of the tmp file.
                try:
                    _os.unlink(path)
                except Exception:
                    pass
            except Exception as exc:
                self.log.debug("snap_image_failed",
                               reason=reason, error=str(exc))

        try:
            t = threading.Thread(target=_do_snap, name="nao-snap-image")
            t.daemon = True
            t.start()
        except Exception:
            pass

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
            import sys as _sys
            print("[tts_trace] audio_chunk_received_from_server but tts_player is None — DROPPED")
            _sys.stderr.flush()
            return
        b64 = frame.get("data") or ""
        text = frame.get("text") or ""
        try:
            mp3_bytes = base64.b64decode(b64) if b64 else b""
        except Exception as exc:
            self.log.warn("audio_chunk_b64_decode_failed", error=str(exc))
            return
        import sys as _sys
        print("[tts_trace] audio_chunk_received_from_server bytes={0} text_preview={1!r}".format(
            len(mp3_bytes), (text or "")[:40]))
        _sys.stderr.flush()
        # Real reply audio arrived → block any future filler and kill
        # the in-flight one if there is one. interrupt=True makes
        # ProcessingAnnouncer call our stop_all helper, which calls
        # ALTextToSpeech.stopAll() — that's the ONLY way to cut a
        # naoqi say() mid-word. Without it, the filler finishes the
        # phrase even after stop_event is set, and the user hears
        # "Hmm let me…" overlapping with the real reply.
        self._reply_audio_arrived = True
        if self._announcer_active and self._announcer is not None:
            try:
                self._announcer.stop(interrupt=True)
            except Exception:
                pass
            self._announcer_active = False
        # Belt-and-braces: also stop any ALTTS directly in case the
        # announcer hadn't initialized stop_all yet.
        try:
            self._announcer_stop_all()
        except Exception:
            pass
        # Embodiment: server doesn't always emit a tts_started control
        # frame before the first audio_chunk, so kick stand-up + the
        # speaking-gesture loop here too. Both are idempotent
        # (_kick_stand_up checks _stood_up_once, _start_speaking_gestures
        # is a no-op if the thread is already running).
        if not self._tts_active.is_set():
            self._tts_active.set()
        try:
            self._kick_stand_up()
        except Exception:
            pass
        try:
            self._start_speaking_gestures()
        except Exception:
            pass
        try:
            self.tts_player.enqueue(text, mp3_bytes)
        except Exception as exc:
            print("[tts_trace] tts_player.enqueue raised: {0}: {1}".format(
                type(exc).__name__, exc))
            _sys.stderr.flush()
            self.log.error("tts_enqueue_failed", error=str(exc))

    def _handle_action(self, frame):
        """Body action — hand off to the action worker thread.

        The dispatcher can call blocking naoqi APIs (motion.moveTo,
        posture.goToPosture, motion.angleInterpolation) that take
        seconds to return. Calling them on the WS receive thread
        starves audio chunk decoding and control frame handling,
        which is exactly the bug the user flagged. Pushing the
        action onto a single-worker queue means recv stays hot.
        """
        if self.action_dispatcher is None:
            return
        name = frame.get("name")
        args = frame.get("args") or {}
        try:
            self._action_queue.put_nowait((name, args))
        except Exception as exc:
            # Queue should never reject (unbounded), but if it does
            # we'd rather log + drop than block the recv loop.
            self.log.error("action_enqueue_failed", name=name, error=str(exc))

    def _cancel_actions(self, reason="cancel"):
        """Drop all pending queued actions and stop any NAOqi behaviors
        currently running. Called on barge-in and crisis-lock so a
        long-running dance / follow-me / animation can't keep playing
        after the user has interrupted.

        Best-effort: we open a fresh ALBehaviorManager proxy here to
        avoid coupling to whatever the dispatcher's closure captured.
        ``stopAllBehaviors`` is idempotent and safe to call when nothing
        is running.
        """
        # 1. Drop queued actions so the worker doesn't start another
        #    behavior right after we stop the current one.
        dropped = 0
        try:
            while True:
                self._action_queue.get_nowait()
                dropped += 1
        except Exception:
            pass

        # 2. Stop any in-flight behavior on the robot.
        try:
            from naoqi import ALProxy as _ALProxy  # type: ignore
            try:
                from . import config as _config  # py3 dev
            except Exception:
                import config as _config  # py2.7 robot path
            mgr = _ALProxy("ALBehaviorManager",
                           _config.NAO_IP, _config.NAO_PORT)
            try:
                mgr.stopAllBehaviors()
            except Exception:
                pass
        except Exception:
            # naoqi unavailable on dev box — silently skip.
            pass

        if dropped:
            self.log.info("actions_cancelled",
                          dropped=dropped, reason=reason)

    def _action_worker_loop(self):
        """Single-worker thread that drains _action_queue and runs the
        dispatcher. Sequencing actions through one worker matches what
        naoqi expects (most behaviors take exclusive resource locks)
        and means we never have two body moves racing for HeadYaw.
        """
        while not self.shutdown_event.is_set():
            try:
                item = self._action_queue.get(timeout=0.2)
            except Exception:
                continue
            if item is None:
                # Sentinel from shutdown — exit cleanly.
                return
            name, args = item
            try:
                self.action_dispatcher(name, args)
            except TypeError:
                # Legacy dispatcher signatures take a single dict.
                try:
                    self.action_dispatcher({"name": name, "args": args})
                except Exception as exc:
                    self.log.error("action_dispatch_failed", name=name,
                                   error=str(exc))
            except Exception as exc:
                self.log.error("action_dispatch_failed", name=name,
                               error=str(exc))

    def _handle_control(self, frame):
        sub = frame.get("subtype")
        data = frame.get("data") or {}

        if sub == "tts_started":
            self._on_tts_started(data)
        elif sub == "tts_ended":
            self._on_tts_ended(data)
        elif sub == "crisis_lock":
            self._on_crisis_lock(data)
        elif sub == "brain_sync":
            self._handle_brain_sync(data)
        elif sub == "transcript":
            # Transcript is for client-side logging only. Phase 1 keeps the
            # robot dumb about transcript content; future phases (3, 8) can
            # consume this for LED/UI cues.
            self.log.info("transcript",
                          transcript=data.get("transcript", ""),
                          stt_ms=data.get("stt_ms"))
            # Legacy self-echo path: the server's `_legacy_helpers`
            # bigram check sends a `transcript` control with
            # reject_reason=self_echo (the newer Phase-2 substring
            # check sends a dedicated `echo_reject` frame, handled
            # separately). Either way we need a recorder restart so
            # the tail of NAO's own voice doesn't keep getting
            # re-uploaded.
            reject_reason = (data.get("reject_reason") or "").strip()
            if reject_reason == "self_echo":
                self._on_echo_reject(data)
                return
            # Fire the processing announcer: server has the user's words
            # and is now generating + synthesizing TTS. We bridge that gap
            # with short filler ("Hmm.", "One sec.") so the user doesn't
            # stare at a silent robot. Stopped as soon as the first real
            # audio_chunk arrives in _handle_audio_chunk.
            # New turn — reset the "audio arrived" guard.
            self._reply_audio_arrived = False
            tx = (data.get("transcript") or "").strip()
            if tx and self._announcer is not None:
                try:
                    self._announcer.start()
                    self._announcer_active = True
                except Exception as exc:
                    self.log.debug("announcer_start_failed", error=str(exc))
        elif sub == "echo_reject":
            self._on_echo_reject(data)
        elif sub == "session_end":
            self.log.info("server_session_end", reason=data.get("reason"))
        elif sub == "agent_handoff":
            self.log.info("agent_handoff", **{k: data.get(k) for k in
                                              ("from", "to", "reason")
                                              if k in data})
        else:
            self.log.warn("control_subtype_unknown", subtype=sub)

    # --- Phase 7: brain_sync push handler -----------------------------
    def _handle_brain_sync(self, data):
        """Apply a server-pushed brain delta and persist it asynchronously.

        Frame shape (server -> client):
            { "type": "control", "subtype": "brain_sync",
              "data": { "updates": { "users": {...},
                                     "system_prompt_fragments": {...} } } }

        The server emits this right after ``session_open`` when its
        view of a face is newer than the robot's ``brain_version`` /
        ``brain_summary``. We forward ``data["updates"]`` to
        ``brain_cache.apply_updates`` and then ``brain_cache.save`` on a
        daemon thread so disk I/O never stalls the receiver loop. Both
        steps are wrapped: a malformed push must not kill the WS session.
        """
        cache = self.brain_cache
        if cache is None:
            self.log.warn("brain_sync_dropped", reason="no_cache")
            return
        updates = None
        if isinstance(data, dict):
            updates = data.get("updates")
        if not isinstance(updates, dict):
            self.log.warn("brain_sync_bad_payload",
                          got_type=type(updates).__name__)
            return

        apply_fn = getattr(cache, "apply_updates", None)
        if not callable(apply_fn):
            self.log.warn("brain_sync_unsupported",
                          reason="cache.apply_updates not callable")
            return

        try:
            apply_fn(updates)
        except Exception as exc:
            # Apply failure is recoverable — the cache is responsible for
            # rejecting bad input cleanly. Log and skip the save so we
            # don't persist a half-applied state.
            self.log.error("brain_apply_updates_failed", error=str(exc))
            return

        users_count = 0
        try:
            users = updates.get("users")
            if isinstance(users, dict):
                users_count = len(users)
        except Exception:
            users_count = 0
        has_fragments = isinstance(updates.get("system_prompt_fragments"),
                                   dict)
        self.log.info("brain_sync_applied",
                      users=users_count,
                      fragments=has_fragments)

        # Save in the background so receiver-thread latency stays low.
        # The cache is expected to do its own atomic write (temp + rename)
        # so a crash mid-save doesn't corrupt the JSON on disk.
        save_fn = getattr(cache, "save", None)
        if not callable(save_fn):
            self.log.debug("brain_sync_save_skipped",
                           reason="cache.save not callable")
            return
        try:
            saver = threading.Thread(
                target=self._brain_save_worker,
                args=(save_fn,),
                name="nao-brain-save")
            saver.daemon = True
            saver.start()
        except Exception as exc:
            # Couldn't spin a thread (resource exhaustion, etc) — fall
            # back to a synchronous save. Worst case we eat a few ms of
            # disk latency on this frame; better than dropping the write.
            self.log.warn("brain_save_thread_failed", error=str(exc))
            self._brain_save_worker(save_fn)

    def _brain_save_worker(self, save_fn):
        try:
            save_fn()
        except Exception as exc:
            self.log.error("brain_save_failed", error=str(exc))
        else:
            self.log.debug("brain_saved")

    # --- TTS gating: close mic on start, reopen on end + grace ---
    def _on_tts_started(self, data):
        self._tts_active.set()
        self._cancel_pending_mic_open()
        # Server is now streaming TTS — block any further filler and
        # cut a filler that's mid-utterance.
        self._reply_audio_arrived = True
        if self._announcer_active and self._announcer is not None:
            try:
                self._announcer.stop(interrupt=True)
            except Exception:
                pass
            self._announcer_active = False
        try:
            self._announcer_stop_all()
        except Exception:
            pass
        try:
            if self.audio_streamer is not None:
                self.audio_streamer.gate(True)  # close mic
        except Exception as exc:
            self.log.error("mic_gate_close_failed", error=str(exc))
        # Posture: stand up before speaking so NAO has presence and the
        # gestures don't look weird from a sitting/crouched stance.
        # Best-effort, non-blocking — fire-and-forget on a daemon thread
        # so we don't delay TTS playback by the ~3 s standUp animation.
        self._kick_stand_up()
        # Speaking-gesture loop: pick a random body-language intent every
        # 2.5 s while TTS is playing. Stops on tts_ended or barge.
        self._start_speaking_gestures()
        self.log.info("tts_started", text_preview=str(data.get("text") or "")[:80])

    def _on_tts_ended(self, data):
        # IMPORTANT: tts_ended from the server only signals that the
        # server has finished SENDING audio chunks. The robot's local
        # tts_player queue can still hold several MP3s being decoded +
        # blocking_play_done'd over the next several seconds. We must NOT
        # open the mic here on a fixed timer — the speaker would still be
        # broadcasting NAO's voice and the mic would record it.
        #
        # New behavior: clear the server-intent flag, stop gestures,
        # snap an image (cheap), and then SPAWN a waiter thread that
        # polls tts_player.is_playing() until the queue is fully drained.
        # Only then arm the grace timer and reopen the mic.
        self._tts_active.clear()
        self._stop_speaking_gestures()
        try:
            self._snap_and_push_image(reason="post_tts")
        except Exception:
            pass

        # Cancel any timer-based reopen that an older _on_tts_ended may
        # have scheduled. Then spawn the playback-aware waiter.
        self._cancel_pending_mic_open()
        self._spawn_mic_resume_waiter()

    def _spawn_mic_resume_waiter(self):
        """Background thread: wait until local TTS playback is fully
        drained, then add a post-playback grace, THEN open the mic.

        Idempotent — a second tts_ended (from a follow-up sentence
        chunk) just refreshes the existing waiter's deadline by
        re-spawning it; the prior thread sees its stop event and
        exits cleanly.
        """
        # Stop any previous waiter so back-to-back tts_ended frames
        # coalesce. The new waiter will see the same is_playing() truth.
        prev_stop = self._mic_resume_waiter_stop
        prev_thread = self._mic_resume_waiter
        prev_stop.set()
        if prev_thread is not None and prev_thread.is_alive() \
                and prev_thread is not threading.current_thread():
            try:
                prev_thread.join(timeout=0.05)
            except Exception:
                pass

        new_stop = threading.Event()
        self._mic_resume_waiter_stop = new_stop

        grace_s = _grace_seconds()
        # Outer cap on how long we'll wait for playback to drain. If the
        # tts_player gets wedged (rare, but possible on naoqi MP3 errors)
        # we still want the mic to come back so the user isn't stuck.
        max_wait_s = 30.0
        poll_s = 0.1

        def _waiter():
            t0 = time.time()
            # 1. Wait for the local player to truly finish — both
            #    "currently playing" AND "queue empty".
            while not new_stop.is_set():
                try:
                    playing = bool(self.tts_player.is_playing()) \
                        if self.tts_player is not None else False
                except Exception:
                    playing = False
                if not playing:
                    self.log.info("local_tts_queue_empty",
                                  elapsed_ms=int((time.time() - t0) * 1000))
                    break
                if time.time() - t0 > max_wait_s:
                    self.log.warn("local_tts_drain_timeout",
                                  elapsed_ms=int((time.time() - t0) * 1000))
                    break
                if new_stop.wait(poll_s):
                    return  # superseded by a newer waiter

            if new_stop.is_set():
                return

            self.log.info("playback_all_done")

            # 2. Post-playback grace — lets reverb / speaker cone tail
            #    decay so the mic doesn't catch the last echo.
            if grace_s > 0 and new_stop.wait(grace_s):
                return

            if new_stop.is_set():
                return

            # 3. Reopen the mic and notify the server. Wrapped in
            #    retry-with-backoff so a transient naoqi proxy hiccup
            #    can't leave the mic stuck closed. On terminal failure
            #    we tear the WS down so the connect-loop builds a fresh
            #    audio module.
            self._open_mic_with_retry(grace_s, source="post_playback")

        t = threading.Thread(target=_waiter, name="nao-mic-resume-waiter")
        t.daemon = True
        self._mic_resume_waiter = t
        t.start()

    def _open_mic_with_retry(self, grace_s, source):
        """Open the mic gate with up to 3 retries (100 ms apart). On
        persistent failure, force a WS shutdown so the outer connect-
        loop spawns a fresh audio module + recorder.
        """
        if self.audio_streamer is None:
            return
        last_err = None
        for attempt in range(1, 4):
            try:
                self.audio_streamer.gate(False)
                self.push_control("mic_resumed",
                                  {"grace_ms": int(grace_s * 1000)})
                self.log.info("mic_resume_after_playback",
                              grace_ms=int(grace_s * 1000),
                              source=source, attempt=attempt)
                return
            except Exception as exc:
                last_err = str(exc)
                self.log.warn("mic_gate_open_failed",
                              attempt=attempt, error=last_err)
                time.sleep(0.1)
        # All retries failed — escalate.
        self.log.error("mic_gate_open_failed_terminal",
                       source=source, error=last_err)
        try:
            self.shutdown_event.set()
        except Exception:
            pass

    def _cancel_pending_mic_open(self):
        with self._mic_timer_lock:
            timer = self._mic_open_timer
            self._mic_open_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _on_echo_reject(self, data):
        """Server rejected a transcript as self-echo (NAO heard itself).

        Force a clean recorder restart so any tail audio still in the
        fragment .wav doesn't keep getting re-uploaded. Sequence:
          1. Close gate (stops fragment recorder, stops feeding the
             upload buffer).
          2. Brief pause so the speaker cone settles.
          3. Reopen gate (spins a fresh fragment recorder writing a
             new stream.wav, resetting all tail offsets).
        """
        reason = data.get("reason") if isinstance(data, dict) else None
        self.log.warn("echo_reject_received", reason=reason)

        # Cancel any pending mic-open timer / drain waiter so they can't
        # race the manual restart below.
        self._cancel_pending_mic_open()
        try:
            self._mic_resume_waiter_stop.set()
        except Exception:
            pass

        if self.audio_streamer is None:
            return

        try:
            self.audio_streamer.gate(True)  # stop recorder
        except Exception as exc:
            self.log.error("echo_reject_gate_close_failed", error=str(exc))
            return

        # Small settle window before re-arming. Speaker cone + alsa
        # buffer flush — 250 ms is comfortably more than one fragment.
        time.sleep(0.25)

        # Reopen the recorder via the retry-with-shutdown helper so a
        # transient gate failure doesn't leave the mic stuck closed.
        self._open_mic_with_retry(grace_s=0.0, source="echo_reject_restart")
        self.log.info("recorder_restart_after_self_echo")

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
        # Stop any body action mid-flight — a crisis-mode 988 reply
        # should not be paired with NAO finishing a dance move.
        self._cancel_actions(reason="crisis_lock")

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

    # ------------------------------------------------------------------
    # ProcessingAnnouncer adapters: speak short filler phrases via the
    # robot's built-in ALTextToSpeech. We deliberately don't route these
    # through the same ElevenLabs MP3 path the real reply uses — fillers
    # are < 1 s, and queuing them through the chunk player would either
    # delay the real reply or fight with it for the speaker. ALTTS is
    # fast, free, and conveniently muted by stop_all() on barge.
    # ------------------------------------------------------------------
    def _announcer_say(self, phrase):
        """Speak ``phrase`` via ALTextToSpeech. Best-effort, never raises.
        Three guard rails — if ANY trip, we silently skip:
          1. Reply audio already arrived for this turn.
          2. tts_player is currently playing real reply audio.
          3. tts_active server flag set (server says it's now sending TTS).
        """
        if getattr(self, "_reply_audio_arrived", False):
            return
        try:
            if self._tts_active.is_set():
                return
        except Exception:
            pass
        try:
            if self.tts_player is not None and self.tts_player.is_playing():
                return
        except Exception:
            pass
        try:
            from naoqi import ALProxy
            import os as _os
            ip = _os.environ.get("NAO_IP", "127.0.0.1")
            tts = ALProxy("ALTextToSpeech", ip, 9559)
            tts.say(str(phrase or "").encode("utf-8") if isinstance(phrase, unicode) else str(phrase or ""))  # noqa: F821
        except NameError:
            # py3 path (no `unicode` builtin) — should never hit on robot
            try:
                from naoqi import ALProxy as _AL
                import os as _os2
                ip = _os2.environ.get("NAO_IP", "127.0.0.1")
                _AL("ALTextToSpeech", ip, 9559).say(str(phrase or ""))
            except Exception:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Embodiment: stand-up posture + random gestures during TTS playback
    # ------------------------------------------------------------------
    # Speaking-gesture pool. Mix of:
    #   - "stock" — gestures from nao_execute._GESTURE_TABLE (proven,
    #     tested, auto-restoring or paired with neutral reset).
    #   - "custom_*" — inline mini-gestures defined in this class.
    #     Built directly via ALMotion.angleInterpolation so we can ship
    #     more variety without touching the central gesture table.
    # Weighted so conversational moves dominate (nod, open_arms, head
    # tilts) and dramatic/large moves (full-body) appear rarely.
    _SPEAKING_GESTURE_POOL = (
        # Stock — well-tested
        "nod", "nod", "nod",
        "open_arms", "open_arms",
        "point_self", "point_self",
        "point_listener",
        "tilt_curious", "tilt_curious",
        "shake",
        # Custom — inline mini-gestures (see _run_custom_gesture)
        "custom_head_tilt_right", "custom_head_tilt_right",
        "custom_head_tilt_left", "custom_head_tilt_left",
        "custom_wave_right",
        "custom_wave_left",
        "custom_palms_up", "custom_palms_up",
        "custom_arm_sweep_right",
        "custom_arm_sweep_left",
        "custom_shoulder_shrug_quick",
        "custom_chest_breath",
        "custom_hand_circle_right",
        "custom_point_up",
        "custom_head_bow_slight",
        "custom_both_hands_forward",
    )

    def _kick_stand_up(self):
        """Fire ALRobotPosture.goToPosture('Stand', 0.7) once per session
        on the first tts_started. Runs on a daemon thread so the 2-3 s
        animation never blocks audio playback. No-op if naoqi is missing
        or the robot is already standing.
        """
        if self._stood_up_once:
            return
        self._stood_up_once = True

        def _do_stand():
            try:
                from naoqi import ALProxy
                import os as _os
                ip = _os.environ.get("NAO_IP", "127.0.0.1")
                # Wake actuators so motion calls actually move joints.
                try:
                    ALProxy("ALMotion", ip, 9559).wakeUp()
                except Exception:
                    pass
                # Posture goes through ALRobotPosture (smoother than
                # raw angleInterpolation). 0.7 = 70 % speed — bal between
                # snappy and stable.
                try:
                    ALProxy("ALRobotPosture", ip, 9559).goToPosture("Stand", 0.7)
                except Exception:
                    pass
            except Exception:
                # naoqi missing (off-robot dev) — silent.
                pass

        try:
            t = threading.Thread(target=_do_stand, name="nao-stand-up")
            t.daemon = True
            t.start()
        except Exception:
            pass

    def _start_speaking_gestures(self):
        """Spin a daemon thread that fires a random gesture every ~2.5 s
        while TTS is active. Idempotent — restarting before stop is a
        no-op (so back-to-back tts_started frames don't spawn N threads).
        """
        if self._speaking_gesture_thread is not None and \
                self._speaking_gesture_thread.is_alive():
            return
        self._speaking_gesture_stop.clear()
        try:
            t = threading.Thread(target=self._speaking_gesture_loop,
                                 name="nao-speak-gesture")
            t.daemon = True
            self._speaking_gesture_thread = t
            t.start()
        except Exception as exc:
            self.log.debug("speaking_gesture_start_failed", error=str(exc))

    def _stop_speaking_gestures(self):
        """Signal the loop to exit. Thread joins itself on stop event;
        we don't block here so tts_ended stays snappy.
        """
        self._speaking_gesture_stop.set()

    # Inline mini-gesture table. Each entry is a tuple:
    #   (joint_names, angle_lists, time_lists)
    # passed straight to motion.angleInterpolation(... isAbsolute=True).
    # Joints not listed stay where they are (the neutral-reset between
    # gestures handles the cleanup). ~0.6-1.0 s per gesture.
    #
    # NOTE: NAO H25 head is 2-DOF (HeadYaw + HeadPitch only — there is
    # NO HeadRoll). "head tilt" gestures use HeadYaw (turn left/right).
    _CUSTOM_GESTURES = {
        "custom_head_tilt_right": (
            ["HeadYaw"],
            [[-0.35, 0.0]],
            [[0.40, 0.80]],
        ),
        "custom_head_tilt_left": (
            ["HeadYaw"],
            [[0.35, 0.0]],
            [[0.40, 0.80]],
        ),
        "custom_head_bow_slight": (
            ["HeadPitch"],
            [[0.18, 0.0]],
            [[0.40, 0.80]],
        ),
        "custom_point_up": (
            # Right hand pointing up
            ["RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll"],
            [[-0.5, 1.5],   # shoulder up over ~0.5 s, hold, then back
             [-0.2, -0.15],
             [1.0, 1.2],
             [0.3, 0.5]],
            [[0.50, 1.30],
             [0.50, 1.30],
             [0.50, 1.30],
             [0.50, 1.30]],
        ),
        "custom_wave_right": (
            # Right hand wave: shoulder out, elbow bent, side-to-side
            ["RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll", "RWristYaw"],
            [[0.1, 0.1, 0.1, 1.5],
             [-0.4, -0.4, -0.4, -0.15],
             [1.5, 1.5, 1.5, 1.2],
             [0.8, 0.8, 0.8, 0.5],
             [0.3, -0.3, 0.3, 0.0]],
            [[0.30, 0.55, 0.80, 1.20],
             [0.30, 0.55, 0.80, 1.20],
             [0.30, 0.55, 0.80, 1.20],
             [0.30, 0.55, 0.80, 1.20],
             [0.30, 0.55, 0.80, 1.20]],
        ),
        "custom_wave_left": (
            ["LShoulderPitch", "LShoulderRoll", "LElbowYaw", "LElbowRoll", "LWristYaw"],
            [[0.1, 0.1, 0.1, 1.5],
             [0.4, 0.4, 0.4, 0.15],
             [-1.5, -1.5, -1.5, -1.2],
             [-0.8, -0.8, -0.8, -0.5],
             [-0.3, 0.3, -0.3, 0.0]],
            [[0.30, 0.55, 0.80, 1.20],
             [0.30, 0.55, 0.80, 1.20],
             [0.30, 0.55, 0.80, 1.20],
             [0.30, 0.55, 0.80, 1.20],
             [0.30, 0.55, 0.80, 1.20]],
        ),
        "custom_palms_up": (
            # Both forearms rotate up — explanatory "this is the deal"
            ["LShoulderPitch", "RShoulderPitch",
             "LElbowRoll", "RElbowRoll",
             "LWristYaw", "RWristYaw"],
            [[0.6, 1.5],  [0.6, 1.5],
             [-1.2, -0.5], [1.2, 0.5],
             [-1.5, 0.0],  [1.5, 0.0]],
            [[0.50, 1.10]] * 6,
        ),
        "custom_arm_sweep_right": (
            ["RShoulderPitch", "RShoulderRoll", "RElbowRoll"],
            [[0.3, 0.7, 1.5],
             [-0.6, -0.2, -0.15],
             [0.6, 0.4, 0.5]],
            [[0.40, 0.80, 1.20],
             [0.40, 0.80, 1.20],
             [0.40, 0.80, 1.20]],
        ),
        "custom_arm_sweep_left": (
            ["LShoulderPitch", "LShoulderRoll", "LElbowRoll"],
            [[0.3, 0.7, 1.5],
             [0.6, 0.2, 0.15],
             [-0.6, -0.4, -0.5]],
            [[0.40, 0.80, 1.20],
             [0.40, 0.80, 1.20],
             [0.40, 0.80, 1.20]],
        ),
        "custom_shoulder_shrug_quick": (
            # Quick shoulder lift then drop — "I dunno"
            ["LShoulderPitch", "RShoulderPitch",
             "LShoulderRoll",  "RShoulderRoll"],
            [[1.0, 1.5], [1.0, 1.5],
             [0.4, 0.15], [-0.4, -0.15]],
            [[0.30, 0.70]] * 4,
        ),
        "custom_chest_breath": (
            # Subtle shoulder lift then drop — visible "deep breath"
            # (HipPitch isn't a valid NAO joint name on H25 — pelvis
            # is per-leg via LHipPitch/RHipPitch — just use shoulders).
            ["LShoulderPitch", "RShoulderPitch"],
            [[1.3, 1.5], [1.3, 1.5]],
            [[0.50, 1.20]] * 2,
        ),
        "custom_hand_circle_right": (
            # Right hand draws a small circle in the air
            ["RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll"],
            [[0.5, 0.3, 0.5, 0.7, 1.5],
             [-0.5, -0.3, -0.1, -0.3, -0.15],
             [1.0, 0.8, 1.0, 1.2, 1.2],
             [0.6, 0.4, 0.6, 0.8, 0.5]],
            [[0.30, 0.55, 0.80, 1.05, 1.40]] * 4,
        ),
        "custom_both_hands_forward": (
            # Both hands extend forward — "here, look at this"
            ["LShoulderPitch", "RShoulderPitch",
             "LElbowYaw", "RElbowYaw",
             "LElbowRoll", "RElbowRoll"],
            [[0.4, 1.5], [0.4, 1.5],
             [-0.5, -1.2], [0.5, 1.2],
             [-0.5, -0.5], [0.5, 0.5]],
            [[0.40, 1.10]] * 6,
        ),
    }

    # Joints we explicitly reset to neutral between gestures so each
    # new gesture starts from the same known pose — without this, a
    # `nod` after a `point_self` looks invisible because the head and
    # arms haven't returned to neutral. Values are NAOqi standard
    # "StandInit" angles for these joints (radians).
    _NEUTRAL_POSE = {
        "HeadYaw":         0.0,
        "HeadPitch":       0.0,
        "LShoulderPitch":  1.5,   # arms hanging naturally
        "LShoulderRoll":   0.15,
        "LElbowYaw":      -1.2,
        "LElbowRoll":     -0.5,
        "RShoulderPitch":  1.5,
        "RShoulderRoll":  -0.15,
        "RElbowYaw":       1.2,
        "RElbowRoll":      0.5,
        # NOTE: NAO H25 has no HipPitch joint (legs are LHipPitch /
        # RHipPitch separately). We don't reset hips in the neutral
        # pose — fine in practice because none of our gestures touch
        # hip joints, so they stay at whatever stand-init set them to.
    }

    def _speaking_gesture_loop(self):
        """Loop: pick a random gesture, fire it, reset to neutral, repeat.

        Architecture (each iteration):
            1. Pin upper-body stiffness so motors actually move (NAO
               drops stiffness on idle joints to save power).
            2. Run the gesture function directly against ALMotion.
            3. Wait ~0.8 s for the gesture animation to play out.
            4. Reset head + arms to neutral so the NEXT gesture starts
               from a known pose (without this, a 'nod' after a
               'point_self' looks invisible — the head's already off
               center and the small nod delta is barely visible).
            5. Wait ~1.5 s before the next gesture so consecutive
               moves don't blur into each other.

        Total cadence: ~2.5 s per gesture cycle, same as before, but
        with deterministic per-gesture reset.
        """
        try:
            import random as _random
        except Exception:
            return
        # Build motion / posture / leds proxies once.
        motion = posture = leds = None
        try:
            from naoqi import ALProxy
            import os as _os
            ip = _os.environ.get("NAO_IP", "127.0.0.1")
            try:
                motion = ALProxy("ALMotion", ip, 9559)
            except Exception:
                motion = None
            try:
                posture = ALProxy("ALRobotPosture", ip, 9559)
            except Exception:
                posture = None
            try:
                leds = ALProxy("ALLeds", ip, 9559)
            except Exception:
                leds = None
        except Exception:
            return
        if motion is None:
            self.log.debug("speak_gesture_no_motion_proxy")
            return
        try:
            from utils.nao_execute import _GESTURE_TABLE
        except Exception as exc:
            self.log.debug("speak_gesture_table_import_failed",
                           error=str(exc))
            return

        import sys as _sys
        # Initial delay so a 1 s reply isn't gestured over.
        if self._speaking_gesture_stop.wait(timeout=0.6):
            return
        print("[speaking_gesture] loop active (motion=%s)" % (motion is not None,))
        _sys.stderr.flush()

        # Wake actuators + pin stiffness on the upper body once. This is
        # the load-bearing fix — without stiffness on the head/arm chains,
        # angleInterpolation calls return immediately but no joint moves.
        try:
            motion.wakeUp()
        except Exception:
            pass
        # NAOqi expands chain names internally, so passing a list of
        # chain names with a same-length list of values fails ("expected
        # the number of stiffnesses to equal the number of joints"
        # because Head=2 joints, LArm=6, RArm=6 = 14 joints, not 3
        # values). Issue per-chain calls so each chain expands to its
        # own joint set and applies the single value to all of them.
        for _chain in ("Head", "LArm", "RArm"):
            try:
                motion.setStiffnesses(_chain, 1.0)
            except Exception as exc:
                self.log.debug("speak_gesture_stiffness_failed",
                               chain=_chain, error=str(exc))
        print("[speaking_gesture] stiffness pinned on Head + arms")
        _sys.stderr.flush()

        neutral_names = list(self._NEUTRAL_POSE.keys())
        neutral_angles = [self._NEUTRAL_POSE[n] for n in neutral_names]

        while not self._speaking_gesture_stop.is_set() and \
                self._tts_active.is_set():
            # Avoid back-to-back identical picks — feels more natural.
            for _retry in range(3):
                intent = _random.choice(self._SPEAKING_GESTURE_POOL)
                if intent != getattr(self, "_last_gesture_intent", None):
                    break
            self._last_gesture_intent = intent

            # Re-pin stiffness EACH iteration — naoqi can drop it after
            # ~5 s of inactivity on a chain, and we want every gesture
            # to actually actuate. Per-chain calls (chain expansion
            # mismatches the values list when batched).
            for _chain in ("Head", "LArm", "RArm"):
                try:
                    motion.setStiffnesses(_chain, 1.0)
                except Exception:
                    pass

            # Dispatch: custom_* gestures use the inline table; everything
            # else hits the stock _GESTURE_TABLE.
            print("[speaking_gesture] -> {0}".format(intent))
            _sys.stderr.flush()
            if intent.startswith("custom_"):
                cg = self._CUSTOM_GESTURES.get(intent)
                if cg is not None:
                    names, angles, times = cg
                    try:
                        motion.angleInterpolation(names, angles, times, True)
                    except Exception as exc:
                        self.log.debug("speak_custom_gesture_failed",
                                       intent=intent, error=str(exc))
                else:
                    self.log.debug("speak_custom_gesture_unknown",
                                   intent=intent)
            else:
                fn = _GESTURE_TABLE.get(intent)
                if fn is not None:
                    try:
                        fn(motion, posture, leds)
                    except Exception as exc:
                        self.log.debug("speak_gesture_failed",
                                       intent=intent, error=str(exc))
            # Wait for the gesture itself to complete (~0.6-0.8 s for
            # most). Bail early if stop event fires.
            if self._speaking_gesture_stop.wait(timeout=0.8):
                return
            # Reset head + arms to a known neutral pose so the next
            # gesture starts from a clean baseline. Use angleInterpolation
            # over 0.6 s — slow enough to look natural, fast enough to
            # not stretch the cadence. Skip if shutdown landed during
            # the previous wait.
            if self._speaking_gesture_stop.is_set() or \
                    not self._tts_active.is_set():
                break
            try:
                motion.angleInterpolation(
                    neutral_names,
                    [[a] for a in neutral_angles],
                    [[0.6] for _ in neutral_angles],
                    True,  # absolute
                )
            except Exception as exc:
                self.log.debug("speak_gesture_neutral_reset_failed",
                               error=str(exc))
            # Brief pause so back-to-back gestures don't blur together.
            if self._speaking_gesture_stop.wait(timeout=1.1):
                return

    def _announcer_stop_all(self):
        """Stop any in-flight ALTextToSpeech utterance. Used by
        ProcessingAnnouncer.stop(interrupt=True) on barge.
        """
        try:
            from naoqi import ALProxy
            import os as _os
            ip = _os.environ.get("NAO_IP", "127.0.0.1")
            ALProxy("ALTextToSpeech", ip, 9559).stopAll()
        except Exception:
            pass

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
        # Print on first chunk + every 50th to prove send loop is alive.
        import sys as _sys
        if not hasattr(self, "_first_audio_logged"):
            self._first_audio_logged = True
            self._audio_send_count = 0
            print("[mic_trace] first_audio_frame_sent_to_ws bytes_b64={0} seq={1}".format(
                len(b64_pcm or ""), seq))
            _sys.stderr.flush()
        self._audio_send_count = getattr(self, "_audio_send_count", 0) + 1
        if self._audio_send_count % 50 == 0:
            print("[mic_trace] ws_audio_chunk_sent count={0}".format(
                self._audio_send_count))
            _sys.stderr.flush()
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
                # Also kill any in-flight filler utterance so the user
                # isn't talking over a half-finished "Hmm, let me…".
                if self._announcer is not None:
                    try:
                        self._announcer.stop(interrupt=True)
                    except Exception:
                        pass
                    self._announcer_active = False
                # And stop the speaking-gesture loop so NAO doesn't keep
                # waving while the user is now talking.
                self._stop_speaking_gestures()
                # Cancel any pending queued actions + stop currently
                # running NAOqi behaviors so a multi-second dance/pose
                # doesn't keep going after the user has interrupted.
                self._cancel_actions(reason="barge_in")
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

        # The 10 s above is the *connect* timeout. After handshake we need
        # to drop the read timeout to None (block forever) so the recv loop
        # doesn't kill the session while server is doing slow work
        # (vision call, LLM streaming, STT). Without this the recv socket
        # raises socket.timeout after 10 s of server silence and we
        # reconnect mid-turn — the exact bug we were seeing.
        try:
            ws.settimeout(None)
        except Exception as exc:
            self.log.warn("ws_settimeout_failed", error=str(exc))

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

        # Action worker — drains queued body actions off the recv thread.
        # Daemon so we don't have to coordinate exit on a hard shutdown.
        self._action_worker_thread = threading.Thread(
            target=self._action_worker_loop, name="nao-ws-actions")
        self._action_worker_thread.daemon = True
        self._action_worker_thread.start()

        # nao-therapy: mic watchdog. Polls the audio module's
        # last_pcm_age_ms() once per 5 s. If the gate is open, TTS is
        # NOT playing, AND no PCM has been captured for >4 s, we
        # assume the recorder is wedged and force a restart. This is
        # the safety net for the "stuck mic, doesn't listen" symptom
        # the user has been hitting.
        self._mic_watchdog_thread = threading.Thread(
            target=self._mic_watchdog_loop, name="nao-mic-watchdog")
        self._mic_watchdog_thread.daemon = True
        self._mic_watchdog_thread.start()

    def _mic_watchdog_loop(self):
        """Periodic recorder liveness check.

        Fires once per 5 s. Conditions to flag a wedged recorder:
          - audio_streamer exists
          - gate is currently OPEN (we expect mic to be active)
          - tts_active is FALSE (we're not gating ourselves)
          - last PCM was captured > 4000 ms ago (or never received
            after a 10 s grace from session start)
        Action: log `mic_silent_too_long` + force a recorder restart
        via the retry-with-shutdown helper. The first recovery is
        cheap (gate close + reopen); if it keeps failing we shut the
        WS down and let the connect-loop spin a fresh audio module.
        """
        # Per-loop state.
        SILENCE_THRESHOLD_MS = 4000.0
        STARTUP_GRACE_S = 10.0
        loop_start = time.time()
        while not self.shutdown_event.is_set():
            try:
                # Wait first so we don't fire on the very first tick of
                # a fresh session (recorder needs ~1-2 s to spin up).
                if self.shutdown_event.wait(5.0):
                    return
                if self.audio_streamer is None:
                    continue
                # Skip while TTS is playing (the gate is intentionally
                # closed during playback, so silence is expected).
                if self._tts_active.is_set():
                    continue
                gate_closed = bool(getattr(
                    self.audio_streamer, "gate_closed", False))
                if gate_closed:
                    continue
                age_ms = None
                try:
                    age_ms = self.audio_streamer.last_pcm_age_ms()
                except Exception as exc:
                    self.log.debug("mic_watchdog_age_lookup_failed",
                                   error=str(exc))
                    continue
                if age_ms is None:
                    # No PCM ever captured. Allow the startup grace
                    # window before flagging — recorder is still warming
                    # up on a fresh connection.
                    if (time.time() - loop_start) < STARTUP_GRACE_S:
                        continue
                    self.log.warn("mic_silent_no_pcm",
                                  uptime_s=round(time.time() - loop_start, 1))
                    self._force_recorder_restart(reason="no_pcm_ever")
                    continue
                if age_ms > SILENCE_THRESHOLD_MS:
                    self.log.warn("mic_silent_too_long",
                                  age_ms=round(age_ms, 1))
                    self._force_recorder_restart(reason="pcm_stale")
            except Exception as exc:
                # Watchdog must never die.
                self.log.debug("mic_watchdog_iter_error", error=str(exc))

    def _force_recorder_restart(self, reason="watchdog"):
        """Close+reopen the gate to spin a fresh fragment recorder.
        Used by the watchdog when the recorder appears wedged. Best-
        effort; on persistent failure the WS is shut down so the
        connect-loop builds a fresh audio module.
        """
        if self.audio_streamer is None:
            return
        try:
            self.audio_streamer.gate(True)
        except Exception as exc:
            self.log.error("recorder_restart_close_failed",
                           reason=reason, error=str(exc))
            try:
                self.shutdown_event.set()
            except Exception:
                pass
            return
        time.sleep(0.25)
        self._open_mic_with_retry(grace_s=0.0,
                                   source="watchdog:" + str(reason))
        self.log.info("recorder_force_restarted", reason=reason)

    def _join_workers(self):
        # 1s join — threads are daemon and will get nuked on process exit
        # if they refuse to wake up, but in practice the recv loop exits
        # on first failed recv() and the sender exits when its iterator
        # closes (audio_streamer should propagate the shutdown via its own
        # gate/close).

        # Drain the pending action queue before tear-down so a half-
        # processed gesture doesn't leave the robot mid-pose. We drop
        # rather than execute pending items here — the session is dying.
        try:
            while True:
                self._action_queue.get_nowait()
        except Exception:
            pass
        # Sentinel wakes the worker so it exits its blocking get().
        try:
            self._action_queue.put_nowait(None)
        except Exception:
            pass

        for t in (self._sender_thread, self._receiver_thread,
                  self._barge_thread, self._action_worker_thread,
                  self._mic_watchdog_thread):
            if t is None:
                continue
            try:
                t.join(timeout=1.0)
            except Exception:
                pass
        self._sender_thread = None
        self._receiver_thread = None
        self._barge_thread = None
        self._action_worker_thread = None
        self._mic_watchdog_thread = None

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
        # Phase 7: include a small brain summary so the server can decide
        # whether to push a brain_sync delta. The cache impl owns the
        # exact shape (face_id_count, version, last_seen_iso_per_face,
        # brain_size_bytes — see docs/PHASE_7_TASK_MAP.md). Tolerate caches
        # that don't (yet) expose summary() — older robots will simply
        # skip the field and the server will treat them as unknown.
        summary = self._brain_summary()
        if summary is not None:
            data["brain_summary"] = summary
        ok = self._send_json(ws, _control_frame("session_open", data))
        # Kick off a fresh image snap right away so the very FIRST turn
        # of this session has vision available (post_tts hook only fires
        # after the first reply). Best-effort, non-blocking.
        if ok:
            try:
                self._snap_and_push_image(reason="session_open")
            except Exception:
                pass
        return ok

    def _brain_summary(self):
        """Best-effort fetch of ``brain_cache.summary()``.

        Returns the dict on success, ``None`` if the cache is missing,
        the method is not yet implemented, or the call raises. Never
        propagates: a flaky summary must not block session_open.
        """
        cache = self.brain_cache
        if cache is None:
            return None
        summary_fn = getattr(cache, "summary", None)
        if not callable(summary_fn):
            self.log.debug("brain_summary_unavailable",
                           reason="cache.summary not callable")
            return None
        try:
            summary = summary_fn()
        except Exception as exc:
            self.log.debug("brain_summary_unavailable", error=str(exc))
            return None
        if summary is None:
            return None
        if not isinstance(summary, dict):
            # Defensive: anything non-dict can't be JSON-encoded as an
            # object and the server contract expects a mapping.
            self.log.warn("brain_summary_bad_type",
                          got_type=type(summary).__name__)
            return None
        return summary

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
        # Stop any in-flight body action so disengage doesn't leave NAO
        # halfway through a dance pose or with follow-me still running.
        try:
            self._cancel_actions(reason="shutdown")
        except Exception:
            pass
        # Push a session_close hint so the server can finalize the
        # transcript / save recap before the socket closes.
        self.push_control("session_close", {"reason": "shutdown"})
