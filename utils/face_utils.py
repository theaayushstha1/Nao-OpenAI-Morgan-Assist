# face_utils.py
# -*- coding: utf-8 -*-
"""
Utilities for detecting a user’s presence (face) and mood on NAO.
"""

import time
from naoqi import ALProxy

def detect_face(nao_ip, timeout=5.0):
    """
    Returns True if a face is seen within `timeout` seconds.
    """
    face_proxy = ALProxy("ALFaceDetection", nao_ip, 9559)
    memory     = ALProxy("ALMemory",        nao_ip, 9559)

    face_proxy.subscribe("FaceTest", 500, 0.1)
    start = time.time()
    seen  = False

    while time.time() - start < timeout:
        data = memory.getData("FaceDetected")
        if data and isinstance(data, list) and len(data) > 1:
            seen = True
            break
        time.sleep(0.2)

    face_proxy.unsubscribe("FaceTest")
    return seen

def detect_mood(nao_ip, sample_time=2.0):
    """
    Uses NAO’s ALMood module to get a quick sense of emotional state.
    Returns a string like 'happy', 'neutral', or 'annoyed'.
    """
    mood_proxy = ALProxy("ALMood", nao_ip, 9559)
    # Let it “listen” for a moment to estimate mood
    time.sleep(sample_time)
    try:
        mood = mood_proxy.getMood()  # e.g. ['happy', 0.8]
        label, score = mood[0], mood[1]
        return label
    except Exception:
        return "neutral"
