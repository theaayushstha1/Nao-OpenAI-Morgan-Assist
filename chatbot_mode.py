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


def with_processing_announcer(tts_proxy, server_call_func, first_delay=0.7, interval=3.0):
    """
    Runs a server call while announcing 'processing' only if it takes long.
    """
    ann = ProcessingAnnouncer(
        tts_say=tts_proxy.say,
        stop_all=getattr(tts_proxy, "stopAll", None),
        first_delay=first_delay,
        interval=interval
    )
    try:
        ann.start()
        return server_call_func()
    finally:
        try:
            ann.stop(interrupt=True)
        except:
            pass


def chatbot_mode(record_audio_func, tts_proxy):
    print("🧠 Entering Morgan Chatbot Mode")
    tts_proxy.say("You may now ask me a question about Morgan")

    while True:
        try:
            # 1. Record audio from NAO
            audio_path = record_audio_func()
            print("[User spoke] Audio saved to:", audio_path)

<<<<<<< HEAD
            # 2. Define server call function
            def server_call():
                with open(audio_path, 'rb') as f:
                    return requests.post(
                        SERVER_URL,
                        files={'file': f},
                        data={'username': 'friend', 'mode': 'chatbot'},  # 👈 force chatbot mode
                        timeout=TIMEOUT
                    )
=======
            # 2. Send audio to the server
            with open(audio_path, 'rb') as f:
                res = requests.post(
                    SERVER_URL,
                    files={'file': f},
                    data={'username': 'friend', 'mode': 'chatbot'},  
                    timeout=TIMEOUT
                )
>>>>>>> origin/main

            # 3. Call server with announcer wrapper
            res = with_processing_announcer(tts_proxy, server_call)

            if res.status_code != 200:
                print("[Server Error]", res.status_code, res.text)
                tts_proxy.say("Sorry, I couldn’t understand that.")
                continue

            data = res.json()
            user_text = (data.get("user_input") or "").strip()
            reply = (data.get("reply") or "").strip()

            print("[User said]:", user_text)
            print("[Reply]:", reply)

            # 4. Speak results
            if not user_text:
                tts_proxy.say("Sorry, I couldn't hear that.")
                continue

            if reply:
<<<<<<< HEAD
                # Python 2.7 safe UTF-8
                tts_proxy.say(reply.encode('utf-8'))
=======
                tts_proxy.say(reply.encode('utf-8'))  
>>>>>>> origin/main
            else:
                tts_proxy.say("I couldn't find anything useful.")

        except KeyboardInterrupt:
            print("👋 Exiting chatbot mode")
            tts_proxy.say("Exiting chatbot mode.")
            break

        except Exception as e:
            print("[Error]:", str(e))
            tts_proxy.say("Something went wrong. Please try again.")
