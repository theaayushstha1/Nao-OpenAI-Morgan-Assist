# -*- coding: utf-8 -*-
"""NAO entry point - Phase 3 v2 rework.

Boots the structured logger, pins speaker volume, disables ALAutonomousLife,
then drives a face-first ``WakeStateMachine`` that ONLY opens the WebSocket
session once an engagement gate fires (mutual gaze, proximity, sustained
face, speech onset, or "hey nao" keyword fallback). On crash, stops audio
recorder + player and sleeps 2 s before reconnecting - same crash recovery
shape as Phase 1, just gated by wake state above the WS client.

Phase 3 contract (see docs/PHASE_3_TASK_MAP.md):
  IDLE -> AWARE (face) -> ENGAGED (gate fired) -> WS session opens.
  AWARE timeout (8 s with no gate) silently returns to IDLE; no chime,
  no TTS, no WS handshake. This is the main false-wake protection.

Defensive imports - every Phase 3 sibling module (``leds``, ``wake_state``)
and Phase 1/2 dependency (``audio_module``, ``stream_tts``, ``ws_client``,
``audio_handler``, ``wake_listener``) is guarded with try/except so this
file ``py_compile``s in isolation while sibling agents finish their work
in parallel worktrees. Real ImportError surfaces at boot as a structured
log + 2 s retry, not a crashing import at top of file.

Python 2.7 compatible - runs under naoqi on the robot.
"""
from __future__ import print_function

import os
import threading
import time
import traceback


# --- Logger first, before any naoqi import that might fail noisily.
# The logger is stdlib-only and will configure itself on first use; doing it
# explicitly here lets us route boot-time errors through the same pipeline.
from logger import configure_logger, get_logger

# Local config + utilities - these are pure python, no naoqi binding.
import config
from utils import nao_execute, user_cache  # `user_cache` doubles as the brain
                                            # cache placeholder until Phase 7
                                            # ships nao/utils/brain.py.

# naoqi proxy is robot-only. Guarded so this module can be byte-compiled and
# unit-imported on a developer laptop without naoqi installed.
try:
    from naoqi import ALProxy  # type: ignore  # noqa: F401
    _HAS_NAOQI = True
except ImportError:
    ALProxy = None
    _HAS_NAOQI = False


# ---------------------------------------------------------------------------
# Boot helpers preserved from Phase 1 main.py - volume pinning, autonomous
# life shutdown, and crash recovery teardown. Phase 3 keeps these verbatim;
# the only new thing is the wake state machine sitting above the WS client.
# ---------------------------------------------------------------------------


def _set_volume(ip, port, level=100):
    """Pin NAO's master speaker output high so OpenAI TTS MP3 is audible
    in a noisy classroom. setOutputVolume takes 0-100. Best-effort.
    """
    if not _HAS_NAOQI:
        return
    try:
        ALProxy("ALAudioDevice", ip, port).setOutputVolume(int(level))
    except Exception as exc:
        print("[volume] setOutputVolume failed:", exc)
    try:
        ALProxy("ALTextToSpeech", ip, port).setVolume(min(1.0, level / 100.0))
    except Exception:
        pass


def _disable_autonomous(ip, port):
    """Kill NAO's built-in autonomous life so it doesn't talk over us.
    setAutonomousAbilityEnabled persists across reboots; setState is per
    session. Best-effort, swallows naoqi exceptions.
    """
    if not _HAS_NAOQI:
        return
    abilities = [
        "AutonomousBlinking",
        "BackgroundMovement",
        "BasicAwareness",
        "ListeningMovement",
        "SpeakingMovement",
    ]
    try:
        al = ALProxy("ALAutonomousLife", ip, port)
        for a in abilities:
            try:
                al.setAutonomousAbilityEnabled(a, False)
            except Exception:
                pass
        try:
            al.setState("disabled")
        except Exception:
            pass
    except Exception:
        pass
    for svc, calls in [
        ("ALBasicAwareness", [("stopAwareness", [])]),
        ("ALAutonomousMoves", [("setBackgroundStrategy", ["none"]),
                               ("setExpressiveListeningEnabled", [False])]),
        ("ALSpeakingMovement", [("setEnabled", [False])]),
    ]:
        try:
            p = ALProxy(svc, ip, port)
            for method, args in calls:
                try:
                    getattr(p, method)(*args)
                except Exception:
                    pass
        except Exception:
            pass


def _stop_audio_proxies(ip, port):
    """Crash-recovery teardown - make sure the recorder + player are quiet
    before the next reconnect attempt. Same shape as the old conversation
    loop's except-branch.
    """
    if not _HAS_NAOQI:
        return
    try:
        ALProxy("ALAudioRecorder", ip, port).stopMicrophonesRecording()
    except Exception:
        pass
    try:
        ALProxy("ALAudioPlayer", ip, port).stopAll()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Component factories. Imports are deferred so this file ``py_compile``s
# even while sibling agents are still authoring ``nao/wake_state.py`` and
# ``nao/leds.py`` in parallel worktrees. On the robot, these modules will
# be present after the Phase 3 consolidator merges.
# ---------------------------------------------------------------------------


def _build_audio_streamer(log):
    """Construct the ALAudioDevice subscriber. Owned by Phase 1 sibling
    ``nao-audio-module``. Constructed but NOT started here - ``start()`` is
    deferred to ``on_engaged`` so the mic only subscribes once a wake gate
    fires (saves ~1% CPU and avoids surfacing user audio before consent).
    """
    from audio_module import NaoAudioStreamer  # Phase 1 sibling
    return NaoAudioStreamer("NaoAudioStream", config.NAO_IP, config.NAO_PORT)


def _build_tts_player(log):
    """Construct the streaming TTS chunk player. Owned by Phase 1 sibling
    ``nao-stream-tts``. Cheap to construct (no naoqi handles touched until
    first ``enqueue``).
    """
    from stream_tts import StreamTtsPlayer  # Phase 1 sibling
    return StreamTtsPlayer(config.NAO_IP)


def _build_adaptive_vad(log):
    """Construct the Phase 2 adaptive VAD. The wake state machine queries
    this for the "speech onset" engagement gate; once a session is open we
    swap in the live ``ws_client`` so EoU hints flow as control frames.
    """
    from audio_handler import AdaptiveVad  # Phase 2 sibling
    return AdaptiveVad(ws_client=None)


def _build_led_driver(log):
    """Construct the Phase 3 LED driver. Owned by sibling ``led-driver``
    (``nao/leds.py``). Cheap to construct - no naoqi calls until first
    ``fade()``/``pulse()``.
    """
    from leds import LedDriver  # Phase 3 sibling
    return LedDriver(config.NAO_IP, config.NAO_PORT)


def _build_wake_listener(log):
    """Resolve the keyword fallback listener.

    The Phase 3 task map references ``wake_listener.WakeListener`` as the
    expected class. The current ``nao/wake_listener.py`` exposes a
    procedural ``listen_for_command(nao_ip, port)`` instead. We probe both
    so this file works whether the wake-state-machine sibling sticks with
    the procedural API or wraps it in a class.

    Returns whichever symbol exists, or ``None`` if neither is importable
    (in which case the WSM disables its keyword gate cleanly).
    """
    try:
        import wake_listener  # Phase 3 reused-as-is sibling
    except Exception as exc:
        log.warn("wake_listener_import_failed", error=str(exc))
        return None
    cls = getattr(wake_listener, "WakeListener", None)
    if cls is not None:
        try:
            return cls(config.NAO_IP, config.NAO_PORT)
        except Exception as exc:
            log.warn("wake_listener_construct_failed", error=str(exc))
    fn = getattr(wake_listener, "listen_for_command", None)
    if fn is not None:
        # Hand back the module itself - the WSM can call either symbol it
        # finds. Module objects expose attribute access just like an
        # instance, so the sibling's "fallback_word_listener.listen()"
        # contract is satisfied either way.
        return wake_listener
    return None


def _build_ws_client(log, audio, tts, brain):
    """Construct the long-lived WS client. Owned by Phase 1 sibling
    ``nao-ws-client``. We build a fresh instance per ENGAGED transition so
    a torn-down session doesn't carry state into the next one.
    """
    from ws_client import NaoWsClient  # Phase 1 sibling
    ws_url = os.environ.get(
        "WS_URL",
        "ws://{0}:{1}/ws/{2}".format(
            config.SERVER_IP,
            config.SERVER_PORT,
            os.environ.get("USER_NAME", "guest"),
        ),
    )
    # Resolve dispatcher: prefer ``dispatch`` per spec, fall back to ``run``
    # (the existing public symbol) so we work with whatever the sibling
    # agent settles on without a coordination round-trip.
    dispatcher = getattr(nao_execute, "dispatch", None) \
        or getattr(nao_execute, "run", None)
    return NaoWsClient(
        server_url=ws_url,
        username=os.environ.get("USER_NAME", "guest"),
        shared_secret=os.environ.get(
            "NAO_SHARED_SECRET", config.NAO_SHARED_SECRET
        ),
        audio_streamer=audio,
        tts_player=tts,
        action_dispatcher=dispatcher,
        brain_cache=brain,
    )


def _build_wake_state_machine(log, leds, fallback_listener, vad,
                              on_engaged, on_lost, on_listening,
                              on_speaking_done):
    """Construct the Phase 3 wake state machine. Owned by sibling
    ``wake-state-machine``. The constructor signature is pinned in
    docs/PHASE_3_TASK_MAP.md; callbacks are wired here so main.py is the
    single coordinator between wake gates and WS session lifecycle.
    """
    from wake_state import WakeStateMachine  # Phase 3 sibling
    # Pass the AdaptiveVad as a kwarg so the WSM can consult it for the
    # "speech onset" engagement gate without reinventing the energy
    # calculation. The sibling's exact kwarg name is open; we try the
    # documented one first and, if the constructor rejects it, fall back
    # to the spec-required positional signature without the VAD wired.
    try:
        return WakeStateMachine(
            config.NAO_IP, config.NAO_PORT,
            leds, fallback_listener,
            on_engaged, on_lost, on_listening, on_speaking_done,
            adaptive_vad=vad,
        )
    except TypeError:
        log.debug("wsm_no_adaptive_vad_kwarg",
                  note="constructor signature lacks adaptive_vad= - falling back")
        return WakeStateMachine(
            config.NAO_IP, config.NAO_PORT,
            leds, fallback_listener,
            on_engaged, on_lost, on_listening, on_speaking_done,
        )


# ---------------------------------------------------------------------------
# Session controller - bundles the per-ENGAGED state (WS client + thread)
# and gives main.py a clean handle to tear it down on AWARE timeout / face
# loss / shutdown. Defined as a class so the wake-state callbacks (which
# fire on the WSM thread) close over a stable object rather than a tangle
# of nonlocals.
# ---------------------------------------------------------------------------


class _SessionController(object):
    """Owns the WS client lifecycle for one engagement period.

    Lifecycle:
        engage(face_id, gate, conf, dist) -> opens audio.start(), spawns the
            WS client thread, and pushes the ``wake_event`` control frame so
            the server can resume a 24 h SQLiteSession + greet.
        disengage(reason)                 -> shuts the WS client down, joins
            its thread, and stops the audio streamer. Idempotent - the
            wake-state callbacks may fire ``on_lost`` more than once on
            edge-case face flicker.
    """

    def __init__(self, log, audio, tts, vad, brain):
        self._log = log
        self._audio = audio
        self._tts = tts
        self._vad = vad
        self._brain = brain
        self._client = None
        self._thread = None
        self._lock = threading.Lock()

    def engage(self, face_id, gate, confidence, distance_m):
        """Open the audio subscriber + spawn WS client + send wake_event."""
        with self._lock:
            if self._client is not None:
                # Already engaged - this is a duplicate fire. Just refresh
                # the wake_event so the server sees the latest gate metadata.
                try:
                    self._client.push_control(
                        "wake_event",
                        {
                            "face_id": face_id,
                            "gate": gate,
                            "confidence": float(confidence or 0.0),
                            "distance_m": float(distance_m or 0.0),
                            "is_returning_user": bool(face_id),
                        },
                    )
                except Exception as exc:
                    self._log.warn("wake_event_refresh_failed", error=str(exc))
                return

            # 1. Start mic subscription (deferred from boot until wake).
            try:
                if self._audio is not None:
                    self._audio.start()
            except Exception as exc:
                self._log.exception("audio_start_failed_on_engage",
                                    error=str(exc))
                # Bail without spawning the client so the WSM falls back to
                # IDLE on the next tick. Caller's outer crash loop will
                # re-init audio on the next attempt.
                return

            # 2. Build a fresh WS client per engagement.
            try:
                self._client = _build_ws_client(
                    self._log, self._audio, self._tts, self._brain,
                )
            except Exception as exc:
                self._log.exception("ws_client_build_failed", error=str(exc))
                self._stop_audio_safe()
                return

            # 3. Wire the live WS client into the AdaptiveVad so EoU hints
            # ride the same socket. AdaptiveVad accepts any object with
            # ``push_control`` so this is duck-typed and safe.
            try:
                if self._vad is not None:
                    self._vad.ws_client = self._client
            except Exception as exc:
                self._log.debug("vad_ws_attach_failed", error=str(exc))

            # 4. Spawn the WS run loop on a daemon thread. ``client.run()``
            # blocks until ``client.shutdown()`` is called or the socket
            # closes terminally, so we must not call it on the WSM thread
            # (which still needs to drive face detection + state).
            self._thread = threading.Thread(
                target=self._run_client_safe,
                name="nao-ws-engaged",
            )
            self._thread.daemon = True
            self._thread.start()

            # 5. Initial wake_event control frame - per Phase 3 contract.
            # ``push_control`` is thread-safe; the sender thread will pick
            # it up as soon as the connection is up.
            try:
                self._client.push_control(
                    "wake_event",
                    {
                        "face_id": face_id,
                        "gate": gate,
                        "confidence": float(confidence or 0.0),
                        "distance_m": float(distance_m or 0.0),
                        "is_returning_user": bool(face_id),
                    },
                )
            except Exception as exc:
                self._log.warn("wake_event_send_failed", error=str(exc))

            self._log.info(
                "session_engaged",
                face_id=face_id, gate=gate,
                confidence=float(confidence or 0.0),
                distance_m=float(distance_m or 0.0),
            )

    def disengage(self, reason):
        """Shut the WS client down and join the run thread. Idempotent."""
        with self._lock:
            client = self._client
            thread = self._thread
            self._client = None
            self._thread = None

        if client is None and thread is None:
            # Nothing to tear down.
            return

        # Detach VAD first so its EoU emitter doesn't push to a dying ws.
        try:
            if self._vad is not None:
                self._vad.ws_client = None
        except Exception:
            pass

        if client is not None:
            try:
                client.shutdown()
            except Exception as exc:
                self._log.warn("ws_shutdown_failed", error=str(exc))

        if thread is not None:
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass

        self._stop_audio_safe()
        self._log.info("session_disengaged", reason=reason)

    def _run_client_safe(self):
        """Wrap ``client.run()`` so a crash in the WS loop doesn't take the
        whole process down - we just log + let the wake state machine
        decide whether to re-engage.
        """
        client = self._client
        if client is None:
            return
        try:
            client.run()
        except Exception as exc:
            self._log.exception("ws_run_crashed", error=str(exc))

    def _stop_audio_safe(self):
        try:
            if self._audio is not None:
                self._audio.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main loop - boots the wake state machine and blocks on it.
# ---------------------------------------------------------------------------


def main():
    configure_logger(level=os.environ.get("LOG_LEVEL", "INFO"))
    log = get_logger(component="main")
    log.info("boot_start",
             nao_ip=config.NAO_IP,
             server_ip=config.SERVER_IP,
             server_port=config.SERVER_PORT,
             has_naoqi=_HAS_NAOQI,
             phase="phase_3_main_rewire")

    _disable_autonomous(config.NAO_IP, config.NAO_PORT)
    _set_volume(config.NAO_IP, config.NAO_PORT, level=100)

    brain = user_cache  # placeholder for the brain cache (Phase 7 replaces
                        # this with nao/utils/brain.py - capped 64 KB JSON)

    while True:
        wsm = None
        session = None
        audio = None
        tts = None
        leds = None
        vad = None
        try:
            # Build long-lived components. Each builder returns ``None``-or
            # raises on import failure; we catch and log so the outer loop
            # retries cleanly rather than crashing the whole process.
            try:
                audio = _build_audio_streamer(log)
            except Exception as exc:
                log.exception("audio_streamer_build_failed", error=str(exc))
                raise

            try:
                tts = _build_tts_player(log)
            except Exception as exc:
                log.exception("tts_player_build_failed", error=str(exc))
                raise

            try:
                vad = _build_adaptive_vad(log)
            except Exception as exc:
                # AdaptiveVad is optional for the speech-onset gate; without
                # it the WSM still works on face/proximity/keyword.
                log.warn("adaptive_vad_build_failed", error=str(exc))
                vad = None

            try:
                leds = _build_led_driver(log)
            except Exception as exc:
                log.exception("led_driver_build_failed", error=str(exc))
                raise

            fallback = _build_wake_listener(log)

            session = _SessionController(log, audio, tts, vad, brain)

            # ------------------------------------------------------------------
            # Wake-state callbacks. These run on the WSM thread; they delegate
            # heavy work (audio.start / WS client thread) to ``session`` so the
            # WSM stays responsive to face detection.
            # ------------------------------------------------------------------
            def on_engaged(face_id, gate, confidence, distance_m):
                log.info("wake_engaged",
                         face_id=face_id, gate=gate,
                         confidence=float(confidence or 0.0),
                         distance_m=float(distance_m or 0.0))
                session.engage(face_id, gate, confidence, distance_m)

            def on_lost():
                log.info("wake_lost", reason="aware_timeout_or_face_lost")
                session.disengage(reason="wake_lost")
                if leds is not None:
                    try:
                        leds.set_idle()
                    except Exception:
                        pass

            def on_listening():
                log.info("wake_listening")

            def on_speaking_done():
                log.info("wake_speaking_done")

            wsm = _build_wake_state_machine(
                log, leds, fallback, vad,
                on_engaged, on_lost, on_listening, on_speaking_done,
            )

            log.info("wake_state_machine_start")
            # Idle LED before we hand control to the WSM, in case its own
            # init lags. Best-effort.
            if leds is not None:
                try:
                    leds.set_idle()
                except Exception:
                    pass

            wsm.start()  # blocks until stop()
            log.info("wake_state_machine_stopped")

            # Clean exit out of start(): treat as graceful shutdown.
            break

        except KeyboardInterrupt:
            log.info("shutdown_requested")
            break
        except Exception as exc:
            log.exception("crash", error=str(exc))
            # Mirror the Phase 1 stdout trace for SSH-only debug sessions
            # without log shipping.
            try:
                traceback.print_exc()
            except Exception:
                pass
            # Tear down the wake state machine first so its face-detection
            # subscriber doesn't outlive the next iteration's instance.
            if wsm is not None:
                try:
                    wsm.stop()
                except Exception:
                    pass
            # Drop any active session before we recycle audio handles.
            if session is not None:
                try:
                    session.disengage(reason="crash")
                except Exception:
                    pass
            # Final safety net: kill recorder + player at the proxy level
            # so the next boot starts with a quiet audio stack.
            _stop_audio_proxies(config.NAO_IP, config.NAO_PORT)
            time.sleep(2.0)
            continue

    # Final teardown on graceful shutdown.
    if wsm is not None:
        try:
            wsm.stop()
        except Exception:
            pass
    if session is not None:
        try:
            session.disengage(reason="shutdown")
        except Exception:
            pass
    try:
        if audio is not None:
            audio.stop()
    except Exception:
        pass
    try:
        if tts is not None:
            tts.shutdown()
    except Exception:
        pass
    try:
        if vad is not None:
            vad.stop()
    except Exception:
        pass
    if leds is not None:
        try:
            leds.set_idle()
        except Exception:
            pass
    log.info("boot_end")


if __name__ == "__main__":
    main()
