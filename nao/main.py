# -*- coding: utf-8 -*-
"""NAO entry point — Phase 1 v2 rework.

Boots the structured logger, pins speaker volume, disables ALAutonomousLife,
then runs the long-lived WebSocket client loop (NaoWsClient). On crash, stops
audio recorder + player and sleeps 2 s before reconnecting — same crash
recovery shape as the old wake-loop, just wired into the new transport.

Phase 1 KNOWN LIMITATION: the WS client is always-on. Sessions are not yet
gated by face/wake — Phase 3 wires nao/wake_state.py and gates session_open
on engagement signals. For now, the robot opens a WS handshake at boot and
streams audio whenever the energy VAD detects speech.

Python 2.7 compatible — runs under naoqi on the robot.
"""
from __future__ import print_function

import os
import time
import traceback


# --- Logger first, before any naoqi import that might fail noisily.
# The logger is stdlib-only and will configure itself on first use; doing it
# explicitly here lets us route boot-time errors through the same pipeline.
from logger import configure_logger, get_logger

# Local config + utilities — these are pure python, no naoqi binding.
import config
from utils import nao_execute, user_cache  # `user_cache` doubles as the brain
                                            # cache placeholder until Phase 7
                                            # ships nao/utils/brain.py.

# WS pipeline modules. These are owned by sibling Phase 1 agents; we import
# them inside main() (not at module top) so this file still `py_compile`s
# off-robot while those sibling modules are in flight in parallel worktrees.
# When the consolidator merges Phase 1, these imports resolve cleanly.

# naoqi proxy is robot-only. Guarded so this module can be byte-compiled and
# unit-imported on a developer laptop without naoqi installed.
try:
    from naoqi import ALProxy  # type: ignore  # noqa: F401
    _HAS_NAOQI = True
except ImportError:
    ALProxy = None
    _HAS_NAOQI = False


# ---------------------------------------------------------------------------
# Boot helpers preserved from the previous main.py — these are the bits the
# Phase 1 task brief explicitly asks us to keep: volume pinning + autonomous
# life shutdown + crash recovery (audio recorder/player teardown + 2 s sleep).
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
    """Crash-recovery teardown — make sure the recorder + player are quiet
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
# WS-pipeline factories. Imports are deferred so this file `py_compile`s even
# while sibling agents are still authoring audio_module.py / ws_client.py /
# the rewritten stream_tts.py. On the robot, these modules will be present
# after the Phase 1 consolidator merges.
# ---------------------------------------------------------------------------


def _build_audio_streamer(log):
    """Construct the ALAudioDevice subscriber. Owned by sibling agent
    `nao-audio-module`. We import lazily so a missing module surfaces as a
    runtime crash (logged + retried) rather than a boot-time ImportError that
    kills the whole process.
    """
    from audio_module import NaoAudioStreamer  # sibling agent owns this
    return NaoAudioStreamer("NaoAudioStream", config.NAO_IP, config.NAO_PORT)


def _build_tts_player(log):
    """Construct the streaming TTS chunk player. Owned by sibling agent
    `nao-stream-tts` (rewriting stream_tts.py in place).
    """
    from stream_tts import StreamTtsPlayer  # rewritten sibling
    return StreamTtsPlayer(config.NAO_IP)


def _build_ws_client(log, audio, tts, brain):
    """Construct the long-lived WS client. Owned by sibling `nao-ws-client`.
    Action dispatcher = the existing `nao_execute` module — the sibling agent
    will pick the right entry point (current public API is `nao_execute.run`,
    but the spec template uses `dispatch`; we pass the module so either works
    once the sibling lands).
    """
    from ws_client import NaoWsClient  # sibling agent owns this
    ws_url = os.environ.get(
        "WS_URL",
        "ws://{0}:{1}/ws/{2}".format(
            config.SERVER_IP,
            config.SERVER_PORT,
            os.environ.get("USER_NAME", "guest"),
        ),
    )
    # Resolve dispatcher: prefer `dispatch` per spec, fall back to `run`
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


# ---------------------------------------------------------------------------
# Main loop.
# ---------------------------------------------------------------------------


def main():
    configure_logger(level=os.environ.get("LOG_LEVEL", "INFO"))
    log = get_logger(component="main")
    log.info("boot_start",
             nao_ip=config.NAO_IP,
             server_ip=config.SERVER_IP,
             server_port=config.SERVER_PORT,
             has_naoqi=_HAS_NAOQI)

    _disable_autonomous(config.NAO_IP, config.NAO_PORT)
    _set_volume(config.NAO_IP, config.NAO_PORT, level=100)

    # Build long-lived components. Audio streamer is built once and reused
    # across reconnects so we don't churn the ALAudioDevice subscription.
    audio = None
    tts = None
    try:
        audio = _build_audio_streamer(log)
        tts = _build_tts_player(log)
    except Exception as exc:
        log.exception("boot_component_init_failed", error=str(exc))
        # Without these two, there's nothing useful to do — bail hard so
        # systemd / launchd restarts us cleanly with logs intact.
        raise

    brain = user_cache  # placeholder for the brain cache (Phase 7 replaces
                        # this with nao/utils/brain.py — capped 64 KB JSON)

    while True:
        client = None
        try:
            try:
                audio.start()
            except Exception as exc:
                log.exception("audio_start_failed", error=str(exc))
                # If the mic subscription itself fails, sleep and retry.
                time.sleep(2.0)
                continue

            client = _build_ws_client(log, audio, tts, brain)
            log.info("ws_session_begin")
            client.run()  # blocks until disconnect / shutdown
            log.info("ws_session_end_clean")
        except KeyboardInterrupt:
            log.info("shutdown_requested")
            break
        except Exception as exc:
            log.exception("crash", error=str(exc))
            # Make sure stdout has the same trace the legacy main.py logged
            # — useful when running under SSH without log shipping.
            try:
                traceback.print_exc()
            except Exception:
                pass
            # Best-effort teardown so the next iteration starts clean.
            try:
                audio.stop()
            except Exception:
                pass
            _stop_audio_proxies(config.NAO_IP, config.NAO_PORT)
            time.sleep(2.0)

    # Final teardown on graceful shutdown.
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
    log.info("boot_end")


if __name__ == "__main__":
    main()
