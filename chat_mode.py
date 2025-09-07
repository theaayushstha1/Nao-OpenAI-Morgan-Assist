# chat_mode.py
# -*- coding: utf-8 -*-
from __future__ import print_function

"""
NAO chat mode with safe fallbacks when face modules are missing on Python 2.7.
Flow:
  - Detect presence & mood LEDs
  - Capture photo, try server face recognition
  - If unknown -> ask name once, enroll face with that name (5 samples)
  - Greet user and start chat loop; all uploads include username
"""

import os
import json
import random
import requests
import time
import re
from naoqi import ALProxy

from utils.camera_capture import capture_photo  # requires utils/camera_capture.py

# --- optional deps (face/mood) with fallbacks ---
try:
    from audio_handler import record_audio
except Exception:
    def record_audio(_nao_ip):
        return "/home/nao/last.wav"  # stub if your real recorder isn't present

try:
    from utils.face_utils import detect_face, detect_mood
except Exception:
    def detect_face(_nao_ip, _port=9559, _timeout=5):
        return True
    def detect_mood(_nao_ip, _port=9559):
        return "neutral"

# These are NAO-local stubs; real face-ID is on the server
try:
    from face_recognition_utils import identify_face, learn_face
except Exception:
    def identify_face(_nao_ip_or_robot):
        return None
    def learn_face(_nao_ip_or_robot, _user_name):
        return False

import memory_manager

# --- CONFIG: set your laptop/server IP ---
SERVER_IP = "172.20.95.118"
SERVER_URL = "http://{}:5000/upload".format(SERVER_IP)
FACE_RECO_URL = "http://{}:5000/face/recognize".format(SERVER_IP)
FACE_ENROLL_URL = "http://{}:5000/face/enroll".format(SERVER_IP)

def _color_to_rgb(name):
    return {
        "red":    [1.0, 0.0, 0.0],
        "green":  [0.0, 1.0, 0.0],
        "blue":   [0.0, 0.0, 1.0],
        "yellow": [1.0, 1.0, 0.0],
        "purple": [1.0, 0.0, 1.0],
        "white":  [1.0, 1.0, 1.0],
    }.get((name or "").lower(), [1.0, 1.0, 1.0])

def sanitize_text(text):
    try:
        if isinstance(text, bytes):
            try:
                text = text.decode('utf-8', errors='ignore')
            except TypeError:
                text = text.decode('utf-8')
    except Exception:
        text = str(text)
    try:
        basestring
    except NameError:
        basestring = (str, bytes)
    if not isinstance(text, basestring):
        text = str(text)
    return ''.join(c if 32 <= ord(c) <= 126 else ' ' for c in text).strip()

def extract_name(text):
    try:
        lower = (text or "").lower()
    except Exception:
        lower = str(text).lower()
    m = re.search(r"(?:my name is|call me|i am)\s+([A-Za-z]+)", lower)
    return m.group(1).capitalize() if m else "friend"

def _post_image(url, img_path, extra=None, timeout=6.0):
    with open(img_path, "rb") as f:
        files = {"file": (os.path.basename(img_path), f, "image/jpeg")}
        data = extra or {}
        r = requests.post(url, files=files, data=data, timeout=timeout)
        r.raise_for_status()
        return r.json()

def recognize_or_enroll(robot, nao_ip, port):
    """
    1) Capture one photo and try /face/recognize (tolerance 0.60)
    2) If recognized -> return (name, True)
    3) If unknown -> ask name once, then capture & enroll 5 samples
    4) Return (name, False) after enrollment
    """
    # --- Try recognize first ---
    photo_path = capture_photo(nao_ip, port, "/home/nao/face.jpg")
    if photo_path and os.path.exists(photo_path):
        try:
            info = _post_image(FACE_RECO_URL, photo_path, {"tolerance": "0.60"})
            if info.get("ok") and info.get("match"):
                return info.get("name") or "friend", True
        except Exception:
            pass  # fall through to enroll

    # --- Unknown -> ask name once ---
    robot.say("I don't know you yet. Please tell me your first name after the beep.")
    time.sleep(0.5)
    name_wav = record_audio(nao_ip)
    user_name = "friend"
    try:
        with open(name_wav, "rb") as f:
            res = requests.post(SERVER_URL, files={"file": f}, data={"username": user_name}, timeout=10)
        spoken = (res.json() or {}).get("user_input", "")
        extracted = extract_name(spoken)
        if extracted and extracted.lower() != "friend":
            user_name = extracted
    except Exception:
        pass
    if user_name == "friend":
        robot.say("I did not catch your name. I'll call you friend for now.")
        return user_name, False

    # --- Enroll 5 shots to improve robustness ---
    robot.say("Nice to meet you, {}. Hold still while I learn your face.".format(user_name))
    for _ in range(5):
        time.sleep(0.4)
        p = capture_photo(nao_ip, port, "/home/nao/face.jpg")
        if not (p and os.path.exists(p)):
            continue
        try:
            _post_image(FACE_ENROLL_URL, p, {"name": user_name})
        except Exception:
            pass

    robot.say("Got it. I will remember you next time, {}.".format(user_name))
    return user_name, False

def enter_chat_mode(robot, nao_ip="127.0.0.1", port=9559):
    motion  = ALProxy("ALMotion",       nao_ip, port)
    posture = ALProxy("ALRobotPosture", nao_ip, port)
    leds    = ALProxy("ALLeds",         nao_ip, port)

    # Step 1: Presence
    robot.say("Scanning for a friend...")
    if not detect_face(nao_ip):
        robot.say("I don't see anyone. Come back when you're ready.")
        return

    # Step 2: Mood LEDs
    mood = detect_mood(nao_ip) or "neutral"
    r, g, b = _color_to_rgb({"happy": "yellow", "neutral": "white", "annoyed": "purple"}.get(mood, "white"))
    try:
        leds.fadeRGB("FaceLeds", r, g, b, 0.3)
    except Exception:
        pass

    # Step 3: One-shot recognize or enroll (no duplicate logic)
    user_name, recognized = recognize_or_enroll(robot, nao_ip, port)
    if recognized:
        robot.say("Welcome back, {}!".format(user_name))

    # Step 4: Friendly greeting
    greetings = [
        "Let's chat!",
        "Conversation mode active!",
        "I am all ears—let's talk!",
        "Let's start a chat!",
        "Ask me anything!"
    ]
    robot.say("Hey {}! You seem {}. {}".format(user_name, mood, random.choice(greetings)))

    # Step 5: Local memory init (NAO-side store, optional)
    try:
        memory_manager.initialize_user(user_name)
    except Exception:
        pass

    # Step 6: Chat loop
    while True:
        robot.say("I am listening—go ahead!")
        audio_path = record_audio(nao_ip)
        if not os.path.exists(audio_path):
            robot.say("I did not catch that—could you repeat?")
            continue

        try:
            with open(audio_path, "rb") as f:
                res = requests.post(SERVER_URL, files={"file": f}, data={"username": user_name})
            res.raise_for_status()
            data = res.json()
        except Exception:
            robot.say("Oops, something broke. Let's try again.")
            continue

        user_text  = data.get("user_input", "") or ""
        reply_text = data.get("reply", "") or ""
        func_call  = data.get("function_call", {}) or {}

        # Persist (NAO-side), optional
        try:
            if user_text:
                memory_manager.add_user_message(user_name, user_text)
            log_payload = reply_text if reply_text else json.dumps(func_call)
            try:
                memory_manager.add_bot_reply(user_name, log_payload)
            except TypeError:
                memory_manager.add_bot_reply(log_payload)
            try:
                memory_manager.save_chat_history(user_name)
            except TypeError:
                memory_manager.save_chat_history()
        except Exception:
            pass

        if reply_text:
            robot.say(sanitize_text(reply_text))

        if "stop" in user_text.lower():
            robot.say("Catch you later!")
            break

        # Handle function calls
        name = func_call.get("name")
        if name == "stand_up":
            posture.goToPosture("StandInit", 0.6)
        elif name == "sit_down":
            posture.goToPosture("Sit", 0.6)
        elif name == "down":
            try:
                motion.setStiffnesses("Body", 1.0)
                joints = ["RHipPitch","LHipPitch","RKneePitch","LKneePitch","RAnklePitch","LAnklePitch"]
                angles = [0.3,        0.3,        0.5,         0.5,         -0.2,          -0.2]
                motion.setAngles(joints, angles, 0.2)
            except Exception:
                pass
