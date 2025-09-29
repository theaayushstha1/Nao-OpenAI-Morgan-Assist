# chatbot_mode.py (Python 2.7 compatible)
# -*- coding: utf-8 -*-
from naoqi import ALProxy
import requests
import json
import time

from processing_announcer import ProcessingAnnouncer

# === Configuration ===
SERVER_URL = "http://172.20.95.105:5000/upload"
TIMEOUT = 20

# Python 2/3 unicode helper
try:
    unicode  # noqa
except NameError:
    unicode = str


def _safe_say(tts_proxy, text):
    try:
        if isinstance(text, unicode):
            tts_proxy.say(text.encode('utf-8'))
        else:
            tts_proxy.say(text)
    except:
        try:
            tts_proxy.say("Okay.")
        except:
            pass


def with_processing_announcer(tts_proxy, server_call_func, first_delay=2.5, interval=3.5, max_utterances=2):
    """
    Runs a server call while announcing 'processing' only if it takes long.
    Speaks at most `max_utterances` times, starting after `first_delay`.
    """
    ann = ProcessingAnnouncer(
        tts_say=tts_proxy.say,
        stop_all=getattr(tts_proxy, "stopAll", None),
        first_delay=first_delay,
        interval=interval,
        max_utterances=max_utterances,
    )
    try:
        ann.start()
        return server_call_func()
    finally:
        try:
            ann.stop(interrupt=True)  # also calls stopAll() if available
        except:
            pass


def chatbot_mode(record_audio_func, tts_proxy):
    print("🧠 Entering Morgan Chatbot Mode")
    _safe_say(tts_proxy, "You may now ask me a question about Morgan")

    while True:
        try:
            # 1) Record audio from NAO
            audio_path = record_audio_func()
            print("[User spoke] Audio saved to:", audio_path)

            # 2) Define server call function (FORCE chatbot mode)
            def server_call():
                with open(audio_path, 'rb') as f:
                    return requests.post(
                        SERVER_URL,
                        files={'file': f},
                        data={'username': 'friend', 'mode': 'chatbot'},
                        timeout=TIMEOUT
                    )

            # 3) Call server with announcer wrapper
            res = with_processing_announcer(tts_proxy, server_call)

            # 4) HTTP / server error handling
            if res.status_code == 503:
                try:
                    detail = (res.json() or {}).get("detail", "") or ""
                except:
                    detail = ""
                if "audio_too_short" in detail:
                    _safe_say(tts_proxy, "I didn’t catch that — could you speak a little longer?")
                elif "bad_wav_header" in detail:
                    _safe_say(tts_proxy, "Hmm, I couldn’t read that clip. Let’s try again.")
                else:
                    _safe_say(tts_proxy, "The server is busy — let’s try again.")
                continue

            if res.status_code != 200:
                print("[Server Error]", res.status_code, res.text)
                _safe_say(tts_proxy, "Sorry, I couldn’t understand that.")
                continue

            # 5) Parse response
            data = res.json() or {}
            user_text = (data.get("user_input") or "").strip()
            reply = (data.get("reply") or "").strip()

            print("[User said]:", user_text)
            print("[Reply]:", reply)

            # 6) Speak results
            if not user_text:
                _safe_say(tts_proxy, "Sorry, I couldn't hear that.")
                continue

            if reply:
                _safe_say(tts_proxy, reply)
            else:
                _safe_say(tts_proxy, "I couldn't find anything useful.")

        except KeyboardInterrupt:
            print("👋 Exiting chatbot mode")
            _safe_say(tts_proxy, "Exiting chatbot mode.")
            break

        except Exception as e:
            print("[Error]:", str(e))
            _safe_say(tts_proxy, "Something went wrong. Please try again.")
