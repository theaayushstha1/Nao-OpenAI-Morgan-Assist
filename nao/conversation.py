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
from utils import face_naoqi, ask_name_utils, nao_execute, camera_capture, exit_detection, intent as _intent
from utils.voice_clone import clone_say
from utils.speech import expressive_say, time_of_day_greeting


_DEFAULT_TIMEOUT = 45


def _post(wav_path, img_path, username, hint, end_session=False):
    url = "http://{0}:{1}/turn".format(config.SERVER_IP, config.SERVER_PORT)
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
    """Recognize known face, otherwise ask once for a name.

    Face recognition is silent (2s scan, no voice prompt) so the user isn't
    left wondering why the robot is asking them to look at it. If unknown,
    we ask once and fall back to 'guest' on server failure.
    """
    try:
        name = face_naoqi.recognize_face_naoqi(qi_session, tts, timeout=2)
        if name:
            return name.lower(), True
    except Exception as e:
        print("[face recognize error]:", e)
    try:
        asked = ask_name_utils.ask_name(
            tts, nao_ip, "http://{0}:{1}".format(config.SERVER_IP, config.SERVER_PORT),
            qi_session, audio_handler.record_audio,
        )
        if asked and asked != "Guest":
            try:
                face_naoqi.learn_new_face_naoqi(qi_session, tts, asked)
            except Exception:
                pass
            return asked.lower(), False
    except Exception as e:
        print("[ask_name error]:", e)
    return "guest", False


def run(qi_session, initial_hint=None):
    tts = ALProxy("ALAnimatedSpeech", config.NAO_IP, config.NAO_PORT)
    raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
    motion = ALProxy("ALMotion", config.NAO_IP, config.NAO_PORT)
    posture = ALProxy("ALRobotPosture", config.NAO_IP, config.NAO_PORT)
    leds = ALProxy("ALLeds", config.NAO_IP, config.NAO_PORT)
    behav_mgr = ALProxy("ALBehaviorManager", config.NAO_IP, config.NAO_PORT)

    username, recognized = _resolve_username(qi_session, raw_tts, config.NAO_IP)
    if recognized and username != "guest":
        expressive_say(raw_tts, "Welcome back, {0}.".format(username))

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


import stream_tts


def _wait_tts_idle(memory, settle_s=0.2, timeout=3.0):
    """Block until ALTextToSpeech reports done/stopped, then a short settle."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            status = memory.getData("ALTextToSpeech/Status")
            if isinstance(status, list) and len(status) >= 2 and status[1] in ("done", "stopped"):
                break
        except Exception:
            break
        time.sleep(0.05)
    time.sleep(settle_s)


def run_streaming(qi_session, initial_hint=None):
    """Streaming variant: sentences arrive over SSE and are spoken as generated."""
    tts = ALProxy("ALAnimatedSpeech", config.NAO_IP, config.NAO_PORT)
    raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
    memory = ALProxy("ALMemory", config.NAO_IP, config.NAO_PORT)
    audio_device = ALProxy("ALAudioDevice", config.NAO_IP, config.NAO_PORT)
    motion = ALProxy("ALMotion", config.NAO_IP, config.NAO_PORT)
    posture = ALProxy("ALRobotPosture", config.NAO_IP, config.NAO_PORT)
    leds = ALProxy("ALLeds", config.NAO_IP, config.NAO_PORT)
    behav_mgr = ALProxy("ALBehaviorManager", config.NAO_IP, config.NAO_PORT)

    username, recognized = _resolve_username(qi_session, raw_tts, config.NAO_IP)
    if recognized and username != "guest":
        clone_say(raw_tts, "Welcome back, {0}. What can I help with?".format(username))
    elif username == "guest":
        clone_say(raw_tts, "I'm listening.")

    # Audible "go" + green eyes so the user always knows when to start.
    try:
        leds.fadeRGB("FaceLeds", 0.0, 1.0, 0.0, 0.1)  # green = listening
    except Exception:
        pass

    suppress_image = False
    hint = initial_hint
    skip_tts_wait = False
    silent_streak = 0  # count consecutive no-speech turns to re-prompt
    barge_config = {
        "enabled": config.BARGE_ENABLED,
        "threshold": config.BARGE_THRESHOLD,
        "sustain_ms": config.BARGE_SUSTAIN_MS,
        "deadzone_ms": config.BARGE_DEADZONE_MS,
        "poll_ms": config.BARGE_POLL_MS,
    }

    while True:
        if skip_tts_wait:
            skip_tts_wait = False
        else:
            _wait_tts_idle(memory)
        wav = audio_handler.record_audio(config.NAO_IP)
        if wav is None or not wav:
            silent_streak += 1
            # After 2 consecutive silent windows, prompt the user so they
            # know NAO is still alive and waiting.
            if silent_streak == 2:
                clone_say(raw_tts, "I'm here when you're ready.")
                try:
                    leds.fadeRGB("FaceLeds", 0.0, 1.0, 0.0, 0.1)
                except Exception:
                    pass
            continue
        silent_streak = 0
        # Camera snap is opt-in (saves ~500ms per turn). Therapist agent can
        # call observe_face tool when it actually needs vision.
        img_path = None
        if config.IMAGE_PER_TURN and not suppress_image:
            img_path = camera_capture.snap_quick(config.NAO_IP, config.NAO_PORT)

        files = {}
        if wav:
            files["audio"] = open(wav, "rb")
        if img_path:
            files["image"] = open(img_path, "rb")
        data = {"username": username}
        if hint:
            data["hint"] = hint

        def handle_action(action):
            nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)

        def handle_done(info):
            pass

        url = "http://{0}:{1}/stream_turn".format(config.SERVER_IP, config.SERVER_PORT)
        info = stream_tts.consume(
            url, files, data, raw_tts, handle_action, handle_done,
            audio_device=audio_device, barge_config=barge_config,
            memory=memory,
        )

        for f in files.values():
            f.close()
        try:
            if wav and os.path.exists(wav):
                os.unlink(wav)
            if img_path and os.path.exists(img_path):
                os.unlink(img_path)
        except Exception:
            pass

        # Preserve hint until we get an actual agent turn. Otherwise the very
        # first audio (often a partial echo or VAD false-trigger) consumes the
        # mode hint and the next turn falls back to router triage.
        active = info.get("active_agent", "")
        if active and active not in ("silence", "barge"):
            hint = None
        print("[stream_turn done] info={0}".format(info))
        if info.get("barge_in"):
            print("[barge-in] user interrupted NAO speech; listening now")
            # Visual confirmation: hold yellow for ~400ms so the user actually
            # sees the acknowledgement before record_audio paints the eyes
            # green for listening.
            try:
                leds.fadeRGB("FaceLeds", 1.0, 0.5, 0.0, 0.08)  # 80ms fade-in
                time.sleep(0.4)                                 # hold
            except Exception:
                pass
            skip_tts_wait = True
            continue
        if info.get("crisis"):
            print("[exit reason] crisis flag")
            break
        if info.get("suppress_image"):
            suppress_image = True
        user_input = info.get("user_input") or ""
        action = _intent.detect(user_input, current_mode=initial_hint or "")
        if action == "exit":
            print("[exit reason] exit_intent on: {0!r}".format(user_input))
            try:
                requests.post(
                    "http://{0}:{1}/turn".format(config.SERVER_IP, config.SERVER_PORT),
                    data={"username": username, "end_session": "true"},
                    timeout=10,
                )
            except Exception:
                pass
            expressive_say(raw_tts, "Goodbye, {0}. See you next time.".format(username))
            return None
        if action and action.startswith("switch:"):
            target = action.split(":", 1)[1]
            print("[switch] {0} -> {1}".format(initial_hint, target))
            expressive_say(raw_tts, "Switching to {0} mode.".format(target))
            return target
