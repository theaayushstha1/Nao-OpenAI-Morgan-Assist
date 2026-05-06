# wake_listener.py
# -*- coding: utf-8 -*-
"""
Wake listener: head-only face tracking, polite greet, consent handshake, no walking.
"""

from naoqi import ALProxy
import time
import threading
import subprocess
import os

from utils.speech import random_phrase, time_of_day_greeting, format_expressive

_MODE_HINT_MAP = {
    "chat": "chat",
    "let's chat": "chat",
    "chat mode": "chat",
    "let's talk": "chat",
    "talk mode": "chat",
    "start chat": "chat",
    "chatbot": "chat",
    "chatbot mode": "chat",
    "morgan assist": "morgan",
    "morgan chat": "morgan",
    "morgan chatbot": "morgan",
    "morgan mode": "morgan",
    "morgan state": "morgan",
    "morgan state mode": "morgan",
    "therapist": "therapy",
    "therapist mode": "therapy",
    "therapy": "therapy",
    "therapy mode": "therapy",
    "talk to someone": "therapy",
    "i need help": "therapy",
    "mini nao": "skills",
    "mini": "skills",
    "mininao": "skills",
    "mini-nao": "skills",
    "skills": "skills",
}


def extract_hint(phrase):
    """Return one of chat/morgan/therapy/skills, or None if no match."""
    if not phrase:
        return None
    key = phrase.strip().lower()
    return _MODE_HINT_MAP.get(key)


DEBOUNCE_SECONDS    = 2.0
SAME_WORD_COOLDOWN  = 3.0
MIN_CONF            = 0.45
NAO_WAKE_MIN_CONF   = 0.38
MORGAN_MIN_CONF     = 0.62
ASR_SENSITIVITY     = 0.68
MODE_SELECTION_TIMEOUT_S = 20.0
MODE_PROMPT_DEADZONE_S   = 0.15

YESNO_TIMEOUT_S     = 6.0
YESNO_MIN_CONF      = 0.40

# Distance (m)
NEAR_SHAKE_DIST     = 0.60
MIN_VALID_DIST      = 0.10
MAX_VALID_DIST      = 3.00

YES_WORDS = ["yes","yeah","yep","sure","ok","okay","please"]
NO_WORDS  = ["no","nope","nah","not now","later","maybe later","no thanks"]

ASSIST_LINE = "Say chat, therapy, Morgan assist, or skills."


# --- small utils ---

def _safe_unsub(asr, name):
    try: asr.unsubscribe(name)
    except: pass

def _say_paused(tts, asr, text):
    try: asr.pause(True)
    except: pass
    try: tts.say(text)
    except: pass
    time.sleep(0.02)
    try: asr.pause(False)
    except: pass

def _say_nowait(tts, asr, text):
    def _work():
        try:
            tts.say(text)
        finally:
            try: asr.pause(False)
            except: pass
    try: asr.pause(True)
    except: pass
    th = threading.Thread(target=_work)
    th.daemon = True
    th.start()

def _flush_word(memory):
    try: memory.insertData("WordRecognized", ["", 0.0])
    except: pass

def _read_word(memory):
    try:
        data = memory.getData("WordRecognized")
    except:
        return "", 0.0
    if isinstance(data, list) and len(data) >= 2:
        w = (data[0] or "")
        # NAOqi's ALSpeechRecognition wraps recognized vocab tokens in "<...>"
        # silence markers when spotting mode is off (e.g., "<...> nao <...>"
        # or "<...> let's chat <...>"). Strip those so vocab lookup works.
        import re
        w = re.sub(r"<[^>]*>", " ", w)
        w = re.sub(r"\s+", " ", w).strip()
        try: c = float(data[1] or 0.0)
        except: c = 0.0
        return w.lower(), c
    return "", 0.0

def _run_bg(fn, *a, **k):
    th = threading.Thread(target=fn, args=a, kwargs=k)
    th.daemon = True
    th.start()
    return th

def _set_vocab(asr, vocab, spotting=False):
    try:
        asr.pause(True)
        asr.setVocabulary(vocab, spotting)
        asr.pause(False)
    except:
        pass

def _word_threshold(word):
    if word == "nao":
        return NAO_WAKE_MIN_CONF
    if word in ("morgan assist", "morgan chat", "morgan chatbot",
                "morgan mode", "morgan state", "morgan state mode"):
        return MORGAN_MIN_CONF
    return MIN_CONF

def _accept_word(word, conf, vocab):
    if not word or word not in vocab:
        return False
    threshold = _word_threshold(word)
    return conf > threshold

def _mode_gate_allows(word, now, mode_armed_until, mode_ignore_until):
    """Idle only accepts the wake word; modes require a recent wake prompt."""
    if word == "nao":
        return True
    return now <= mode_armed_until and now >= mode_ignore_until

# --- head tracking  ---

def _head_track_guard(nao_ip, port, flag):
    tr = None
    while not flag["stop"]:
        try:
            if tr is None:
                tr = ALProxy("ALTracker", nao_ip, port)
                try: tr.setEffector("None")
                except: pass
                try: tr.registerTarget("Face", 0.1)
                except: pass
                try: tr.setMode("Head")
                except: pass
                try: tr.track("Face")
                except: pass
            for _ in range(20):
                if flag["stop"]: break
                time.sleep(0.1)
        except:
            tr = None
            time.sleep(0.3)
    try:
        if tr:
            try: tr.stopTracker()
            except: pass
            try: tr.unregisterAllTargets()
            except: pass
    except:
        pass

def _tracker_stop_now(nao_ip, port):
    try:
        tr = ALProxy("ALTracker", nao_ip, port)
        try: tr.stopTracker()
        except: pass
        try: tr.unregisterAllTargets()
        except: pass
    except:
        pass

# --- distance --

def _face_distance_now(memory):
    try:
        pos = memory.getData("ALTracker/TargetPosition")
        if isinstance(pos, list) and len(pos) >= 3:
            x = float(pos[0])
            if MIN_VALID_DIST <= x <= MAX_VALID_DIST:
                return x
    except:
        pass
    return None

def _sonar_distance(memory):
    keys = [
        "Device/SubDeviceList/US/Left/Sensor/Value",
        "Device/SubDeviceList/US/Right/Sensor/Value",
        "SonarLeftDetected",
        "SonarRightDetected"
    ]
    vals = []
    for k in keys:
        try:
            v = memory.getData(k)
            if isinstance(v, (int, float)):
                v = float(v)
                if MIN_VALID_DIST <= v <= MAX_VALID_DIST:
                    vals.append(v)
        except:
            pass
    return min(vals) if vals else None

def _estimate_user_distance(memory):
    d = _face_distance_now(memory)
    if d is not None: return d
    return _sonar_distance(memory)

# --- motion --

def _stiffen(motion, on=True):
    try: motion.setStiffnesses("Body", 1.0 if on else 0.0)
    except: pass

def _stop_move_now(nao_ip, port):
    try:
        m = ALProxy("ALMotion", nao_ip, port)
        m.stopMove()
    except:
        pass

def _extend_hand_for_shake_hold(nao_ip, port, tts, asr, hold_s=4.0):
    try:
        m  = ALProxy("ALMotion", nao_ip, port)
    except:
        _say_nowait(tts, asr, format_expressive("I hope you are having a wonderful day. ", "warm") + ASSIST_LINE)
        return

    almoves = None
    try:
        almoves = ALProxy("ALAutonomousMoves", nao_ip, port)
        try: almoves.setExpressiveListeningEnabled(False)
        except: pass
        try: almoves.setBackgroundStrategy("none")
        except: pass
    except:
        pass

    try: m.setStiffnesses("RArm", 1.0)
    except: pass
    try: m.setBreathEnabled("RArm", False)
    except: pass

    try:
        names  = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw","RHand"]
        angles = [ 0.35,            0.12,           1.20,        0.90,         0.00,      1.00]
        m.angleInterpolationWithSpeed(names, angles, 0.55)

        _say_nowait(tts, asr, format_expressive("I hope you are having a wonderful day. ", "warm") + ASSIST_LINE)

        t_end = time.time() + float(hold_s)
        while time.time() < t_end:
            try: m.setAngles(names, angles, 0.10)
            except: pass
            time.sleep(0.12)

        names2  = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw","RHand"]
        angles2 = [ 1.40,           -0.20,           1.20,        0.50,         0.00,      0.40]
        m.angleInterpolationWithSpeed(names2, angles2, 0.45)
    except:
        _say_nowait(tts, asr, format_expressive("I truly hope you are having a great day. ", "warm") + ASSIST_LINE)
    finally:
        try: m.setBreathEnabled("RArm", True)
        except: pass
        if almoves:
            try: almoves.setExpressiveListeningEnabled(True)
            except: pass

def _wave_any_posture_now(nao_ip, port):
    try:
        m  = ALProxy("ALMotion",       nao_ip, port)
        _  = ALProxy("ALRobotPosture", nao_ip, port)
    except:
        return
    _stiffen(m, True)
    try:
        names = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw"]
        angles = [0.0, 0.25, 1.2, 0.9, 0.0]
        m.angleInterpolationWithSpeed(names, angles, 0.55)
        for _ in range(2):
            m.angleInterpolationWithSpeed("RShoulderRoll", 0.05, 0.55)
            m.angleInterpolationWithSpeed("RShoulderRoll", 0.30, 0.55)
        names2  = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw"]
        angles2 = [1.4, -0.2, 1.2, 0.5, 0.0]
        m.angleInterpolationWithSpeed(names2, angles2, 0.45)
    except:
        pass

def _wave_any_posture_bg(nao_ip, port):
    _run_bg(_wave_any_posture_now, nao_ip, port)

def _go_to_posture_bg_delayed(nao_ip, port, name, speed, delay=0.05):
    def _work():
        try:
            time.sleep(max(0.0, float(delay)))
            p = ALProxy("ALRobotPosture", nao_ip, port)
            p.goToPosture(name, speed)
        except:
            pass
    _run_bg(_work)

# --- yes/no ---

def _ask_and_listen_yes_no(nao_ip, port, tts, asr, memory, timeout_s=YESNO_TIMEOUT_S):
    """
    Speaks the question, then listens using a temporary ASR subscription.
    Returns True / False / None(timeout). Leaves main listener untouched.
    """
    listener = "NAO_YesNo"
    vocab = list(set(YES_WORDS + NO_WORDS))

    # Prepare temporary listener
    _safe_unsub(asr, listener)
    try:
        asr.pause(True)
        asr.setVocabulary(vocab, False)
        asr.subscribe(listener)
        asr.pause(False)
    except:
        pass

    _flush_word(memory)
    _say_paused(tts, asr, format_expressive(
        "{} My name is NAO. It's a pleasure to meet you. Would you like to shake my hand?".format(time_of_day_greeting()), "warm"))

    t0 = time.time()
    result = None
    while time.time() - t0 < float(timeout_s):
        w, c = _read_word(memory)
        if w and c >= YESNO_MIN_CONF:
            if w in YES_WORDS:
                result = True
                break
            if w in NO_WORDS:
                result = False
                break
        time.sleep(0.05)

    # Clean up temporary listener
    _safe_unsub(asr, listener)
    _flush_word(memory)
    return result

# --- Shutdown/Sleep helpers ---

def _shutdown_robot(tts, asr):
    """Properly shut down NAO robot"""
    _say_paused(tts, asr, format_expressive("Shutting down now. Thank you, and goodbye!", "warm"))
    time.sleep(1)
    try:
        # Create a flag file to signal shutdown
        with open("/tmp/nao_shutdown_requested", "w") as f:
            f.write("1")
        # Try system shutdown
        subprocess.call(["sudo", "shutdown", "-h", "now"])
    except:
        try:
            subprocess.call(["shutdown", "-h", "now"])
        except:
            pass

def _sleep_robot(nao_ip, port, tts, asr):
    """Put NAO to rest/crouch position and stop all activities"""
    _say_paused(tts, asr, format_expressive("Going to rest now. Good night, and take care!", "calm"))
    try:
        # Stop all movements
        motion = ALProxy("ALMotion", nao_ip, port)
        motion.rest()
    except:
        pass
    # Return "exit" to stop the main loop
    return "exit"

# --- TaiChi behavior helper ---

def _start_taichi_dance(nao_ip, port, tts):
    """
    Start a preinstalled TaiChi dance behavior via ALBehaviorManager.
    """
    try:
        mgr = ALProxy("ALBehaviorManager", nao_ip, port)
    except:
        try: tts.say("I can't reach my behavior manager right now.")
        except: pass
        return

    candidates = [
        "taich-dance-free/behavior",
        "taichi-dance-free/behavior",
        "taich-dance-free/startTaichiFree",
        "taichi-dance-free/startTaichiFree",
        "taich-dance-free",
        "taichi-dance-free",
    ]

    try:
        installed = set(mgr.getInstalledBehaviors() or [])
        defaults  = set(mgr.getDefaultBehaviors() or [])
        all_known = installed.union(defaults)
    except:
        all_known = set()

    chosen = None
    for b in candidates:
        if b in all_known:
            chosen = b
            break

    if not chosen:
        for b in all_known:
            if "taich" in b.lower():
                chosen = b
                break

    if not chosen:
        try: tts.say("I couldn't find a Tai Chi behavior on this robot.")
        except: pass
        return

    try:
        if mgr.isBehaviorRunning(chosen):
            mgr.stopBehavior(chosen)
            time.sleep(0.3)
    except:
        pass

    try: tts.say("Okay, starting Tai Chi dance.")
    except: pass
    try:
        mgr.startBehavior(chosen)
    except:
        try: tts.say("Sorry, I couldn't start the Tai Chi behavior.")
        except: pass

# --- Follow-me behavior helper ---

def _start_follow_me(nao_ip, port, tts):
    """
    Start a preinstalled Follow/Follow me behavior.
    """
    try:
        mgr = ALProxy("ALBehaviorManager", nao_ip, port)
    except:
        try: tts.say("I can't reach my behavior manager right now.")
        except: pass
        return

    candidates = [
        "Follow me/behavior",
        "follow me/behavior",
        "follow_me/behavior",
        "follow-me/behavior",
        "Follow me",
        "follow_me",
        "follow-me",
    ]

    try:
        installed = set(mgr.getInstalledBehaviors() or [])
        defaults  = set(mgr.getDefaultBehaviors() or [])
        all_known = installed.union(defaults)
    except:
        all_known = set()

    chosen = None
    for b in candidates:
        if b in all_known:
            chosen = b
            break

    if not chosen:
        for b in all_known:
            if "follow" in b.lower():
                chosen = b
                break

    if not chosen:
        try: tts.say("I couldn't find a Follow behavior on this robot.")
        except: pass
        return

    try:
        if mgr.isBehaviorRunning(chosen):
            mgr.stopBehavior(chosen)
            time.sleep(0.3)
    except:
        pass

    try: tts.say("Okay, let's go. I will follow you.")
    except: pass
    try:
        mgr.startBehavior(chosen)
    except:
        try: tts.say("Sorry, I couldn't start the Follow behavior.")
        except: pass

# --- main ---

def listen_for_command(nao_ip, port=9559):
    tts     = ALProxy("ALTextToSpeech",      nao_ip, port)
    asr     = ALProxy("ALSpeechRecognition", nao_ip, port)
    memory  = ALProxy("ALMemory",            nao_ip, port)

    # Vocab is intentionally tight — only mode triggers. Random ambient noise
    # used to match "sit down" / "dance" / "sleep" and made NAO act on its own.
    MAIN_VOCAB = [
        "nao",
        "chat",
        "let's chat",
        "let's talk",
        "chat mode",
        "talk mode",
        "start chat",
        "morgan assist",
        "morgan chat",
        "morgan chatbot",
        "morgan mode",
        "morgan state",
        "morgan state mode",
        "chatbot",
        "chatbot mode",
        "skills",
        "mini nao",
        "therapy",
        "therapist",
        "therapy mode",
        "talk to someone",
        "i need help",
    ]

    _safe_unsub(asr, "NAO_Chat_Listener")
    asr.pause(True)
    try: asr.setLanguage("English")
    except: pass
    try: asr.setParameter("Sensitivity", ASR_SENSITIVITY)
    except: pass
    _set_vocab(asr, MAIN_VOCAB, spotting=True)
    asr.subscribe("NAO_Chat_Listener")

    # head tracking
    head_flag = {"stop": False}
    _run_bg(_head_track_guard, nao_ip, port, head_flag)

    _say_paused(tts, asr, "Ready. Say nao to begin.")

    last_trigger = 0.0
    last_word    = ""
    mode_armed_until = 0.0
    mode_ignore_until = 0.0

    try:
        while True:
            word, conf = _read_word(memory)
            if not word:
                time.sleep(0.05)
                continue

            now = time.time()
            if not _accept_word(word, conf, MAIN_VOCAB):
                time.sleep(0.05)
                continue

            if word != "nao":
                if not _mode_gate_allows(word, now, mode_armed_until, mode_ignore_until):
                    if now < mode_ignore_until:
                        print("[Heard ignored during prompt deadzone]: {} (conf {:.2f})".format(word, conf))
                    else:
                        print("[Heard ignored outside wake gate]: {} (conf {:.2f})".format(word, conf))
                    _flush_word(memory)
                    time.sleep(0.05)
                    continue

            allow_fast_mode_after_wake = (last_word == "nao" and word != "nao")
            if not allow_fast_mode_after_wake and (now - last_trigger) < DEBOUNCE_SECONDS:
                time.sleep(0.05)
                continue
            if word == last_word and (now - last_trigger) < SAME_WORD_COOLDOWN:
                time.sleep(0.05)
                continue

            last_trigger = now
            last_word    = word

            _safe_unsub(asr, "NAO_Chat_Listener")
            print("[Heard]: {} (conf {:.2f})".format(word, conf))

           
            if word in ["shutdown", "shut down", "nao shutdown", "nao shut down", "power off", "turn off"]:
                _stop_move_now(nao_ip, port)
                head_flag["stop"] = True
                _tracker_stop_now(nao_ip, port)
                _shutdown_robot(tts, asr)
                return "exit"  # Exit main loop
            
            
            elif word in ["sleep", "go to sleep", "nao sleep", "good night"]:
                _stop_move_now(nao_ip, port)
                head_flag["stop"] = True
                _tracker_stop_now(nao_ip, port)
                result = _sleep_robot(nao_ip, port, tts, asr)
                _flush_word(memory)
                return result  # Returns "exit"
            
            elif word in ["therapy", "therapist", "therapy mode", "i need help"]:
                _stop_move_now(nao_ip, port)
                head_flag["stop"] = True
                _tracker_stop_now(nao_ip, port)
                _say_paused(tts, asr, format_expressive(random_phrase("entering_therapist"), "calm"))
                _flush_word(memory)
                return "therapist"


            elif word == "nao":
                _say_paused(tts, asr, ASSIST_LINE)
                _flush_word(memory)
                mode_armed_until = time.time() + MODE_SELECTION_TIMEOUT_S
                mode_ignore_until = time.time() + MODE_PROMPT_DEADZONE_S
                print("[wake gate armed] waiting for mode for {:.1f}s".format(MODE_SELECTION_TIMEOUT_S))

            # ALL CHAT TRIGGERS NOW RETURN "chat" 
            elif word in ["chat", "let's chat", "let's talk", "chat mode", "talk mode",
                          "start chat", "chatbot", "chatbot mode"]:
                _stop_move_now(nao_ip, port)
                head_flag["stop"] = True
                _tracker_stop_now(nao_ip, port)
                _say_paused(tts, asr, format_expressive(random_phrase("entering_chat"), "warm"))
                _flush_word(memory)
                return "chat"
            
            # Morgan triggers route to Realtime with Morgan-specific instructions.
            elif word in ["morgan assist", "morgan chat", "morgan chatbot",
                          "morgan mode", "morgan state", "morgan state mode"]:
                _stop_move_now(nao_ip, port)
                head_flag["stop"] = True
                _tracker_stop_now(nao_ip, port)
                _say_paused(tts, asr, format_expressive(random_phrase("entering_chatbot"), "warm"))
                _flush_word(memory)
                return "morgan assist"
            
            elif word in ["mini nao", "mininao", "skills"]:
                _stop_move_now(nao_ip, port)
                head_flag["stop"] = True
                _tracker_stop_now(nao_ip, port) 
                _say_paused(tts, asr, format_expressive(random_phrase("entering_mininao") + " Let me stand up first.", "warm"))
                _flush_word(memory)
                return "mininao"

            elif word == "stand up":
                _go_to_posture_bg_delayed(nao_ip, port, "StandInit", 0.6, delay=0.05)
                _say_paused(tts, asr, "Okay, standing up.")

            elif word == "sit down":
                _go_to_posture_bg_delayed(nao_ip, port, "Sit", 0.6, delay=0.05)
                _say_paused(tts, asr, "Okay, sitting down.")

            elif word in ["dance", "can you dance", "dance for me", "do a dance"]:
                _stop_move_now(nao_ip, port)
                _start_taichi_dance(nao_ip, port, tts)

            elif word in ["follow me", "let's go", "come with me", "give me your hand", "let's go nao"]:
                _stop_move_now(nao_ip, port)
                _start_follow_me(nao_ip, port, tts)

            _flush_word(memory)
            try: asr.subscribe("NAO_Chat_Listener")
            except: pass
            time.sleep(0.20)
    finally:
        head_flag["stop"] = True
        _tracker_stop_now(nao_ip, port)
        _stop_move_now(nao_ip, port)
