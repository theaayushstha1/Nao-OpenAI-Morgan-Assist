# -*- coding: utf-8 -*-
"""NAO entry point. Passive perception + wake-word dispatch."""
from __future__ import print_function

import threading
import qi

import config
import wake_listener
import conversation
from perceive import Watcher
from utils import camera_capture
from naoqi import ALProxy
import stream_tts


_engaged = threading.Event()


def _on_person_seen(jpeg_path):
    """Proactive entry: called when a person is detected. Opens /greet SSE."""
    if _engaged.is_set():
        return
    _engaged.set()
    try:
        raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
        url = "http://{0}:5000/greet".format(config.SERVER_IP)
        files = {"image": open(jpeg_path, "rb")}
        data = {}

        def noop_action(_):
            pass

        def on_done(_):
            pass

        stream_tts.consume(url, files, data, raw_tts, noop_action, on_done, timeout=60)
        files["image"].close()
    except Exception as e:
        print("[proactive] error:", e)
    finally:
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


def main():
    session = qi.Session()
    session.connect("tcp://{0}:{1}".format(config.NAO_IP, config.NAO_PORT))

    watcher = Watcher(session, camera_capture, _on_person_seen)
    watcher.start(config.NAO_IP, config.NAO_PORT)

    try:
        while True:
            phrase = _get_phrase()
            hint = wake_listener.extract_hint(phrase)
            _engaged.set()
            try:
                conversation.run_streaming(session, initial_hint=hint)
            except KeyboardInterrupt:
                print("Exiting.")
                return
            except Exception as e:
                print("Conversation loop error:", e)
            finally:
                _engaged.clear()
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()
