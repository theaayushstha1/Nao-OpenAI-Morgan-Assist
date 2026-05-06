# -*- coding: utf-8 -*-
from __future__ import print_function
import time


def recognize_face_naoqi(qi_session, tts, subscriber_name="FaceReco", timeout=10):
    """Use NAO's ALFaceDetection to recognize a known face.

    Silent — no spoken prompt. The caller indicates listening via LEDs so
    the user doesn't sit through a 4-second dead-air "look at me" pause.
    Returns the recognized name, or None if no face was recognized.
    """
    face_detection = None
    try:
        memory = qi_session.service("ALMemory")
        face_detection = qi_session.service("ALFaceDetection")
        face_detection.subscribe(subscriber_name)
        # No TTS prompt — just scan silently. ALFaceDetection populates
        # ALMemory key "FaceDetected" within ~200ms when a face is in frame.
        start_time = time.time()
        recognized_name = None
        while time.time() - start_time < timeout:
            try:
                face_data = memory.getData("FaceDetected")
                if face_data and isinstance(face_data, list) and len(face_data) >= 2:
                    face_info_list = face_data[1]
                    if face_info_list and len(face_info_list) > 0:
                        first_face = face_info_list[0]
                        if isinstance(first_face, list) and len(first_face) >= 2:
                            extra_info = first_face[1]
                            if isinstance(extra_info, list) and len(extra_info) >= 3:
                                face_name = extra_info[2]
                                if face_name and isinstance(face_name, (str,)) and str(face_name).strip() != "":
                                    recognized_name = str(face_name)
                                    print("[Recognized]: {}".format(recognized_name))
                                    break
            except Exception as e:
                print("[Memory read error]:", e)
            time.sleep(0.3)
        return recognized_name
    except Exception as e:
        print("[Face recognition error]:", e)
        return None
    finally:
        if face_detection is not None:
            try:
                face_detection.unsubscribe(subscriber_name)
            except Exception:
                pass


def learn_new_face_naoqi(qi_session, tts, name, subscriber_name="FaceLearn"):
    """Try to learn the face currently visible to NAO. Silent — no spoken
    prompt and no follow-up greeting. The caller already had a conversation
    with the user (asking their name) so the camera almost always has a face
    in frame; saying "please look at me" again is redundant and was the main
    reason onboarding felt slow.

    Returns True if a face was captured and learnFace was called.
    """
    face_detection = None
    try:
        face_detection = qi_session.service("ALFaceDetection")
        memory = qi_session.service("ALMemory")
        try:
            face_detection.subscribe(subscriber_name)
        except Exception:
            pass
        start_time = time.time()
        face_found = False
        while time.time() - start_time < 4:
            try:
                face_data = memory.getData("FaceDetected")
                if face_data and isinstance(face_data, list) and len(face_data) >= 2:
                    if face_data[1] and len(face_data[1]) > 0:
                        face_found = True
                        break
            except Exception:
                pass
            time.sleep(0.2)
        if face_found:
            print("[Learning face as]: {}".format(name))
            try:
                face_detection.learnFace(name)
            except Exception as e:
                print("[learnFace error]:", e)
                return False
            time.sleep(0.4)
            return True
        print("[Learn face]: no face in frame for {0}, skipping".format(name))
        return False
    except Exception as e:
        print("[Learn face error]:", e)
        return False
    finally:
        if face_detection is not None:
            try:
                face_detection.unsubscribe(subscriber_name)
            except Exception:
                pass
