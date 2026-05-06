# -*- coding: utf-8 -*-
"""NAO entry point. Passive perception + wake-word dispatch."""
from __future__ import print_function

import threading
import traceback
import time
import qi

import config
import wake_listener
import conversation
import realtime_chat
from perceive import Watcher
from utils import camera_capture
from naoqi import ALProxy
import stream_tts


# Modes that route to the OpenAI Realtime API for sub-second voice replies.
# Therapy and skills stay on /stream_turn so they keep the SAGE-CBT agent
# graph + safety topology and the skills tool calls.
_REALTIME_HINTS = set()  # main: all modes use /stream_turn (proven stable)


_engaged = threading.Event()


def _on_person_seen(jpeg_path):
    """Proactive entry: called when a person is detected. Opens /greet SSE."""
    if not config.PROACTIVE_GREET_ENABLED:
        return
    if _engaged.is_set():
        return
    _engaged.set()
    image_file = None
    try:
        raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
        url = "http://{0}:{1}/greet".format(config.SERVER_IP, config.SERVER_PORT)
        image_file = open(jpeg_path, "rb")
        files = {"image": image_file}
        data = {}

        def noop_action(_):
            pass

        def on_done(_):
            pass

        stream_tts.consume(url, files, data, raw_tts, noop_action, on_done, timeout=60)
    except Exception as e:
        print("[proactive] error:", e)
    finally:
        if image_file:
            try:
                image_file.close()
            except Exception:
                pass
        _engaged.clear()


def _get_phrase():
    try:
        result = wake_listener.listen_for_command(config.NAO_IP, config.NAO_PORT)
        if isinstance(result, tuple):
            return result[0] if result else None
        return result
    except Exception as e:
        print("wake error:", e)
        return None


def _disable_autonomous(ip, port):
    """Kill NAO's built-in autonomous life so it doesn't talk over us.
    setAutonomousAbilityEnabled persists across reboots; setState is per-session.
    """
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
            try: al.setAutonomousAbilityEnabled(a, False)
            except Exception: pass
        try: al.setState("disabled")
        except Exception: pass
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


def _conversation_hint_for_phrase(phrase):
    """Return a server hint only for phrases that should start conversation."""
    return wake_listener.extract_hint(phrase)


def main():
    _disable_autonomous(config.NAO_IP, config.NAO_PORT)
    session = qi.Session()
    session.connect("tcp://{0}:{1}".format(config.NAO_IP, config.NAO_PORT))

    watcher = None
    if config.PROACTIVE_GREET_ENABLED:
        watcher = Watcher(session, camera_capture, _on_person_seen)
        watcher.start(config.NAO_IP, config.NAO_PORT)

    pending_hint = None
    try:
        while True:
            if pending_hint:
                hint = pending_hint
                pending_hint = None
                print("[main] resuming with switched hint={0}".format(hint))
            else:
                phrase = _get_phrase()
                if phrase == "exit":
                    return
                hint = _conversation_hint_for_phrase(phrase)
                if not hint:
                    print("[main] ignoring non-conversation phrase: {0!r}".format(phrase))
                    continue
            _engaged.set()
            try:
                if hint in _REALTIME_HINTS:
                    print("[main] entering realtime mode (hint={0})".format(hint))
                    next_hint = realtime_chat.run(session, initial_hint=hint)
                else:
                    next_hint = conversation.run_streaming(session, initial_hint=hint)
                if next_hint:
                    pending_hint = next_hint
            except KeyboardInterrupt:
                print("Exiting.")
                return
            except Exception as e:
                print("Conversation loop error:", e)
                traceback.print_exc()
                # Stop any lingering recorder so the next session can start clean.
                try:
                    ALProxy("ALAudioRecorder", config.NAO_IP, config.NAO_PORT).stopMicrophonesRecording()
                except Exception:
                    pass
                try:
                    ALProxy("ALAudioPlayer", config.NAO_IP, config.NAO_PORT).stopAll()
                except Exception:
                    pass
                # Let reverb and mic settle before re-arming the wake listener.
                time.sleep(2.0)
            finally:
                _engaged.clear()
    finally:
        if watcher:
            watcher.stop()


if __name__ == "__main__":
    main()
