# -*- coding: utf-8 -*-
# utils/face_utils.py
# Python 2.7 compatible – for face + mood detection using NAO camera

from naoqi import ALProxy
import time

def detect_face(robot_ip="127.0.0.1", port=9559, timeout=5):
    """
    Returns True if a face is detected within timeout seconds, else False.
    """
    try:
        face_proxy = ALProxy("ALFaceDetection", robot_ip, port)
        memory     = ALProxy("ALMemory", robot_ip, port)
        face_proxy.subscribe("FaceUtils")

        print("[Face Detection] Looking for faces...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            time.sleep(0.5)
            data = memory.getData("FaceDetected")
            if data and isinstance(data, list) and len(data) > 1:
                print("[Face Detection] Face detected!")
                face_proxy.unsubscribe("FaceUtils")
                return True

        print("[Face Detection] No face detected.")
        face_proxy.unsubscribe("FaceUtils")
        return False

    except Exception as e:
        print("[Face Detection] Error:", str(e))
        return False


def detect_mood(robot_ip="127.0.0.1", port=9559):
    """
    Dummy mood detector for now. Always returns 'neutral'.
    In future: Hook up to facial emotion model or voice tone analyzer.
    """
    return "great"
