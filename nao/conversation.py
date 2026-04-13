# -*- coding: utf-8 -*-
"""Single conversation loop. Replaces chat_mode, chatbot_mode, therapist_mode, mini_nao."""
from __future__ import print_function

import os
import time
import requests

from naoqi import ALProxy

import config
import audio_handler
from processing_announcer import ProcessingAnnouncer
from utils import face_naoqi, ask_name_utils, nao_execute, camera_capture, exit_detection
from utils.speech import expressive_say, time_of_day_greeting


_DEFAULT_TIMEOUT = 45


def _post(wav_path, img_path, username, hint, end_session=False):
    url = "http://{0}:5000/turn".format(config.SERVER_IP)
    files = {}
    if wav_path:
        files["audio"] = open(wav_path, "rb")
    if img_path:
        files["image"] = open(img_path, "rb")
    data = {"username": username or "guest"}
    if hint:
        data["hint"] = hint
    if end_session:
        data["end_session"] = "true"
    try:
        r = requests.post(url, files=files, data=data, timeout=_DEFAULT_TIMEOUT)
        return r.json() if r.status_code == 200 else None
    finally:
        for f in files.values():
            f.close()


def _resolve_username(qi_session, tts, nao_ip):
    """Recognize face or ask for a name. Returns a string username."""
    name = face_naoqi.recognize_face_naoqi(qi_session, tts, timeout=4)
    if name:
        return name.lower()
    asked = ask_name_utils.ask_name(
        tts, nao_ip, "http://{0}:5000".format(config.SERVER_IP),
        qi_session, audio_handler.record_audio,
    )
    if asked and asked != "Guest":
        try:
            face_naoqi.learn_new_face_naoqi(qi_session, tts, asked)
        except Exception:
            pass
        return asked.lower()
    return "guest"


def run(qi_session, initial_hint=None):
    tts = ALProxy("ALAnimatedSpeech", config.NAO_IP, config.NAO_PORT)
    raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
    motion = ALProxy("ALMotion", config.NAO_IP, config.NAO_PORT)
    posture = ALProxy("ALRobotPosture", config.NAO_IP, config.NAO_PORT)
    leds = ALProxy("ALLeds", config.NAO_IP, config.NAO_PORT)
    behav_mgr = ALProxy("ALBehaviorManager", config.NAO_IP, config.NAO_PORT)

    username = _resolve_username(qi_session, raw_tts, config.NAO_IP)
    expressive_say(raw_tts, "{0}, {1}".format(time_of_day_greeting(), username))

    suppress_image = False
    hint = initial_hint

    while True:
        wav = audio_handler.record_audio(config.NAO_IP)
        if not wav:
            continue

        img_path = None
        if not suppress_image:
            img_path = camera_capture.snap_quick(config.NAO_IP, config.NAO_PORT)

        ann = ProcessingAnnouncer(raw_tts)
        ann.start()
        try:
            resp = _post(wav, img_path, username, hint)
        finally:
            ann.stop()
            try:
                if wav and os.path.exists(wav):
                    os.unlink(wav)
                if img_path and os.path.exists(img_path):
                    os.unlink(img_path)
            except Exception:
                pass

        hint = None

        if resp is None:
            expressive_say(raw_tts, "My brain's not responding. Let's try again.")
            continue

        if resp.get("crisis"):
            expressive_say(raw_tts, resp.get("reply") or "")
            for action in resp.get("actions") or []:
                nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)
            break

        if resp.get("suppress_image"):
            suppress_image = True

        reply = resp.get("reply") or ""
        expressive_say(raw_tts, reply)

        for action in resp.get("actions") or []:
            nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)

        user_input = resp.get("user_input") or ""
        if exit_detection.detect_exit_intent(user_input):
            try:
                _post(None, None, username, None, end_session=True)
            except Exception:
                pass
            expressive_say(raw_tts, "Take care.")
            break
