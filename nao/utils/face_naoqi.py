# -*- coding: utf-8 -*-
from __future__ import print_function
import time

try:
    unicode_type = unicode  # noqa: F821  (Py2.7 on NAO)
except NameError:
    unicode_type = str

_TEXT_TYPES = (str, unicode_type)


def recognize_face_naoqi(qi_session, tts, subscriber_name="FaceReco", timeout=10,
                         stop_event=None, return_seen=False):
    """Use NAO's ALFaceDetection to recognize a known face.

    Silent — no spoken prompt. The caller indicates listening via LEDs so
    the user doesn't sit through a 4-second dead-air "look at me" pause.

    Default returns: recognized name string, or None if no face was recognized.
    With return_seen=True, returns (name_or_None, face_was_visible) — the
    second element is True if ALFaceDetection saw ANY face in frame during
    the scan, even if the face couldn't be identified. This lets callers
    distinguish "the cached user just isn't looking at me" (face not visible)
    from "an unknown stranger is in frame" (face visible but no match), which
    matters for greeting logic — we don't want to greet a stranger by the
    cached user's name.

    stop_event: optional threading.Event — when set, the polling loop exits
    promptly. Used by callers (conversation._onboard_new_user) that run this
    in a background thread alongside ask_name and want to abort the moment
    ask_name returns, rather than letting the full timeout drain.
    """
    face_detection = None
    face_was_visible = False
    try:
        memory = qi_session.service("ALMemory")
        face_detection = qi_session.service("ALFaceDetection")
        face_detection.subscribe(subscriber_name)
        # No TTS prompt — just scan silently. ALFaceDetection populates
        # ALMemory key "FaceDetected" within ~200ms when a face is in frame.
        start_time = time.time()
        recognized_name = None
        while time.time() - start_time < timeout:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                face_data = memory.getData("FaceDetected")
                if face_data and isinstance(face_data, list) and len(face_data) >= 2:
                    face_info_list = face_data[1]
                    if face_info_list and len(face_info_list) > 0:
                        # Even if the face can't be matched, note that a face
                        # was visible at all — caller may need to know.
                        face_was_visible = True
                        first_face = face_info_list[0]
                        if isinstance(first_face, list) and len(first_face) >= 2:
                            extra_info = first_face[1]
                            if isinstance(extra_info, list) and len(extra_info) >= 3:
                                face_name = extra_info[2]
                                if face_name and isinstance(face_name, _TEXT_TYPES) and face_name.strip() != "":
                                    recognized_name = face_name.strip()
                                    print("[Recognized]: {}".format(recognized_name))
                                    break
            except Exception as e:
                print("[Memory read error]:", e)
            time.sleep(0.3)
        if return_seen:
            return (recognized_name, face_was_visible)
        return recognized_name
    except Exception as e:
        print("[Face recognition error]:", e)
        if return_seen:
            return (None, face_was_visible)
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
                # learnFace may return bool, None, or raise. Capture the
                # return so a False ("face not clear enough") doesn't get
                # silently treated as success and leave us claiming we
                # learned the user when we didn't.
                ret = face_detection.learnFace(name)
                print("[learnFace] returned:", ret)
            except Exception as e:
                print("[learnFace error]:", e)
                return False
            time.sleep(0.4)
            # Verify by reading the persisted list. If the name isn't there,
            # something silently failed (insufficient face data, etc.) and
            # the caller needs to know so they can retry next session.
            try:
                learned = face_detection.getLearnedFacesList() or []
                if name in learned:
                    return True
                print("[Learn face] verify FAILED; learned list:", learned)
                return False
            except Exception as e:
                # If we can't read the list, be optimistic — learnFace
                # didn't raise, so probably it worked.
                print("[Learn face] verify read error:", e)
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
