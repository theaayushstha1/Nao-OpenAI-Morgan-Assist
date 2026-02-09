# -*- coding: utf-8 -*-
from __future__ import print_function
import time


def recognize_face_naoqi(qi_session, tts, subscriber_name="FaceReco", timeout=10):
    """Use NAO's ALFaceDetection to recognize a known face.

    Returns the recognized name, or None if no face was recognized.
    """
    face_detection = None
    try:
        memory = qi_session.service("ALMemory")
        face_detection = qi_session.service("ALFaceDetection")
        face_detection.subscribe(subscriber_name)
        tts.say("Please look toward me for a moment.")
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
    """Use NAO's ALFaceDetection to learn a new face.

    Returns True if the face was learned, False otherwise.
    """
    face_detection = None
    try:
        face_detection = qi_session.service("ALFaceDetection")
        memory = qi_session.service("ALMemory")
        tts.say("Please look at me so I can remember your face.")
        time.sleep(1)
        try:
            face_detection.subscribe(subscriber_name)
        except Exception:
            pass
        start_time = time.time()
        face_found = False
        while time.time() - start_time < 8:
            try:
                face_data = memory.getData("FaceDetected")
                if face_data and isinstance(face_data, list) and len(face_data) >= 2:
                    if face_data[1] and len(face_data[1]) > 0:
                        face_found = True
                        break
            except Exception:
                pass
            time.sleep(0.3)
        if face_found:
            tts.say("Perfect. Please hold still for just a moment.")
            time.sleep(1)
            print("[Learning face as]: {}".format(name))
            face_detection.learnFace(name)
            time.sleep(3)
            tts.say("Wonderful! I'll remember you, {}.".format(name))
            return True
        else:
            tts.say("I wasn't able to get a clear view, but let's continue.")
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
