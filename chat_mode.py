# chat_mode.py
# -*- coding: utf-8 -*-
"""
Listens for user speech, sends it to our Flask/GPT server,
speaks back the reply, executes robot actions,
and logs per-user history for future context.
"""

import os
import json
import math
import requests
import time                     # ← Add this import
from naoqi import ALProxy
from audio_handler import record_audio
from utils.face_utils import detect_face, detect_mood
import memory_manager

# ──────────────────────────────────────────────────────────────────────────────
SERVER_URL = "http://172.20.95.118:5000/upload"
# ──────────────────────────────────────────────────────────────────────────────

def _color_to_rgb(name):
    """Map basic color names to NAO LED RGB triples."""
    return {
        "red":    [1.0, 0.0, 0.0],
        "green":  [0.0, 1.0, 0.0],
        "blue":   [0.0, 0.0, 1.0],
        "yellow": [1.0, 1.0, 0.0],
        "purple": [1.0, 0.0, 1.0],
        "white":  [1.0, 1.0, 1.0],
    }.get(name.lower(), [1.0, 1.0, 1.0])

def sanitize_text(text):
    """Strip non-ASCII chars so NAO’s TTS won’t choke."""
    if isinstance(text, bytes):
        text = text.decode('utf-8', errors='ignore')
    if not isinstance(text, str):
        text = str(text)
    return ''.join(c if 32 <= ord(c) <= 126 else ' ' for c in text).strip()

def enter_chat_mode(robot, nao_ip="127.0.0.1"):
    """
    1) Wait for user presence & mood
    2) Ask for and capture name via audio
    3) Initialize per-user memory
    4) Loop: prompt → record → send → speak → act → log
    5) Stop on 'stop'
    """

    # Setup proxies
    motion  = ALProxy("ALMotion",       nao_ip, 9559)
    posture = ALProxy("ALRobotPosture", nao_ip, 9559)
    leds    = ALProxy("ALLeds",         nao_ip, 9559)

    # 1) Face detection & mood
    robot.say("Scanning for a friend...")
    if not detect_face(nao_ip):
        robot.say("I don't see anyone. Come back when you're ready.")
        return

    mood      = detect_mood(nao_ip)
    color_map = {"happy": "yellow", "neutral": "white", "annoyed": "purple"}
    r, g, b   = _color_to_rgb(color_map.get(mood, "white"))
    leds.fadeRGB("FaceLeds", r, g, b, 0.3)

    # 2) Ask for and capture user name
    robot.say("What's your name? Please say it after the beep.")
    time_to_beep = 0.5
    time.sleep(time_to_beep)

    name_wav = record_audio(nao_ip)
    try:
        with open(name_wav, "rb") as f:
            res = requests.post(SERVER_URL, files={"file": f})
        res.raise_for_status()
        data = res.json()
        user_name = data.get("user_input", "").strip().split()[0]
    except Exception:
        user_name = "friend"

    if not user_name:
        user_name = "friend"

    robot.say("Hey {}! You seem {}. Let's chat—say 'stop' to end.".format(user_name, mood))

    # 3) Initialize per-user memory
    memory_manager.initialize_user(user_name)

    while True:
        # 4) Prompt and record
        robot.say("I'm listening—go ahead!")
        audio_path = record_audio(nao_ip)
        if not os.path.exists(audio_path):
            robot.say("I didn't catch that—could you repeat?")
            continue

        # 5) Send to server
        try:
            with open(audio_path, "rb") as f:
                res = requests.post(
                    SERVER_URL,
                    files={"file": f},
                    data={"username": user_name}
                )
            res.raise_for_status()
            data = res.json()
        except Exception:
            robot.say("Oops, something broke. Let's try again.")
            continue

        # 6) Parse
        user_text  = data.get("user_input", "")
        reply_text = data.get("reply", "")
        func_call  = data.get("function_call", {})

        # 7) Log & persist
        if user_text:
            memory_manager.add_user_message(user_text)
        log_payload = reply_text if reply_text else json.dumps(func_call)
        memory_manager.add_bot_reply(log_payload)
        memory_manager.save_chat_history()

        # 8) Speak
        if reply_text:
            robot.say(sanitize_text(reply_text))

        # 9) Stop?
        if "stop" in user_text.lower():
            robot.say("Catch you later!")
            break

        # 10) Execute actions
        name = func_call.get("name")
        args = func_call.get("args", {})

        if name == "stand_up":
            posture.goToPosture("StandInit", 0.6)
        elif name == "sit_down":
            posture.goToPosture("Sit", 0.6)
        elif name == "kneel":
            motion.setAngles(["HipPitch"], [0.5], 0.2)

        # ... other actions unchanged ...

        else:
            pass
