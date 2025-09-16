# chatbot_mode.py (Python 2.7 compatible)
# -*- coding: utf-8 -*-
from naoqi import ALProxy
import os
import requests
import json
import time

# === Configuration ===
SERVER_URL = "http://172.20.95.120:5000/upload"  # Update this as needed
TIMEOUT = 20

def chatbot_mode(record_audio_func, tts_proxy):
    print("🧠 Entering Morgan Chatbot Mode")
    tts_proxy.say("You may now ask me a question about Morgan")

    while True:
        try:
            # 1. Record audio from NAO
            audio_path = record_audio_func()
            print("[User spoke] Audio saved to:", audio_path)

            # 2. Send audio to the server
            with open(audio_path, 'rb') as f:
                res = requests.post(SERVER_URL, files={'file': f}, timeout=TIMEOUT)

            if res.status_code != 200:
                print("[Server Error]", res.status_code, res.text)
                tts_proxy.say("Sorry, I couldn’t understand that.")
                continue

            data = res.json()
            user_text = data.get("user_input", "").strip()
            reply = data.get("reply", "").strip()

            print("[User said]:", user_text)
            print("[Reply]:", reply)

            # Sanitize to string before saying
            if not user_text:
                tts_proxy.say("Sorry, I couldn't hear that.")
                continue

            if reply:
                tts_proxy.say(reply.encode('utf-8'))  # 🔥 Encode to UTF-8 for Python 2
            else:
                tts_proxy.say("I couldn't find anything useful.")

        except KeyboardInterrupt:
            print("👋 Exiting chatbot mode")
            tts_proxy.say("Exiting chatbot mode.")
            break

        except Exception as e:
            print("[Error]:", str(e))
            tts_proxy.say("Something went wrong. Please try again.")
