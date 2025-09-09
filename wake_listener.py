# wake_listener.py
# -*- coding: utf-8 -*-
"""
Wake listener: head-only face tracking, polite greet, consent handshake, no walking.
"""

from naoqi import ALProxy
import time
import threading

DEBOUNCE_SECONDS    = 2.0
SAME_WORD_COOLDOWN  = 3.0
MIN_CONF            = 0.45

YESNO_TIMEOUT_S     = 6.0
YESNO_MIN_CONF      = 0.40

# Distance (m)
NEAR_SHAKE_DIST     = 0.60
MIN_VALID_DIST      = 0.10
MAX_VALID_DIST      = 3.00

YES_WORDS = ["yes","yeah","yep","sure","ok","okay","please"]
NO_WORDS  = ["no","nope","nah","not now","later","maybe later","no thanks"]

ASSIST_LINE = (
    "To start a conversation, please say 'let's talk' or 'let's chat'."
)

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

# --- head tracking (no base motion) ---

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
        _say_nowait(tts, asr, "I hope you are doing great. " + ASSIST_LINE)
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

        _say_nowait(tts, asr, "I hope you are doing great. " + ASSIST_LINE)

        t_end = time.time() + float(hold_s)
        while time.time() < t_end:
            try: m.setAngles(names, angles, 0.10)
            except: pass
            time.sleep(0.12)

        names2  = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw","RHand"]
        angles2 = [ 1.40,           -0.20,           1.20,        0.50,         0.00,      0.40]
        m.angleInterpolationWithSpeed(names2, angles2, 0.45)
    except:
        _say_nowait(tts, asr, "I really hope you are doing great. " + ASSIST_LINE)
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

# --- yes/no (FIXED: subscribe a temporary listener) ---

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
        asr.setVocabulary(vocab, False)  # small set, no spotting
        asr.subscribe(listener)
        asr.pause(False)
    except:
        pass

    _flush_word(memory)
    _say_paused(tts, asr, "Hello, I am Nao. Nice to meet you. Would you like to shake my hand?")

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

# --- main ---

def listen_for_command(nao_ip, port=9559):
    tts     = ALProxy("ALTextToSpeech",      nao_ip, port)
    asr     = ALProxy("ALSpeechRecognition", nao_ip, port)
    memory  = ALProxy("ALMemory",            nao_ip, port)

    MAIN_VOCAB = [
        "nao",
        "stand up",
        "sit down",
        "let's chat",
        "let's talk",
        "talk mode",
        "start a conversation",
        "chat mode"
    ]

    _safe_unsub(asr, "NAO_Chat_Listener")
    asr.pause(True)
    try: asr.setLanguage("English")
    except: pass
    _set_vocab(asr, MAIN_VOCAB, spotting=False)
    asr.subscribe("NAO_Chat_Listener")

    # head tracking
    head_flag = {"stop": False}
    _run_bg(_head_track_guard, nao_ip, port, head_flag)

    _say_paused(tts, asr,
        "System Initializing. I am your robot assistant. Say 'Nao' to ACTIVATE me.")

    last_trigger = 0.0
    last_word    = ""

    try:
        while True:
            word, conf = _read_word(memory)
            if not word:
                time.sleep(0.05); continue

            now = time.time()
            if conf <= MIN_CONF or word not in MAIN_VOCAB:
                time.sleep(0.05); continue

            if (now - last_trigger) < DEBOUNCE_SECONDS:
                time.sleep(0.05); continue
            if word == last_word and (now - last_trigger) < SAME_WORD_COOLDOWN:
                time.sleep(0.05); continue

            last_trigger = now
            last_word    = word

            _safe_unsub(asr, "NAO_Chat_Listener")
            print("[Heard]: {} (conf {:.2f})".format(word, conf))

            if word == "nao":
                d = _estimate_user_distance(memory)

                if d is not None and d <= NEAR_SHAKE_DIST:
                    ans = _ask_and_listen_yes_no(nao_ip, port, tts, asr, memory, timeout_s=YESNO_TIMEOUT_S)
                    # restore main vocab for the rest of the loop
                    _set_vocab(asr, MAIN_VOCAB, spotting=False)
                    if ans is True:
                        _extend_hand_for_shake_hold(nao_ip, port, tts, asr, hold_s=5.0)
                    else:
                        _say_nowait(tts, asr, ASSIST_LINE)
                else:
                    _wave_any_posture_bg(nao_ip, port)
                    _say_nowait(tts, asr, "Hello, I am Nao. It is very nice to meet you. " + ASSIST_LINE)

                _flush_word(memory)

            elif word in ["let's chat", "let's talk", "talk mode", "start a conversation", "chat mode"]:
                _stop_move_now(nao_ip, port)
                head_flag["stop"] = True
                _tracker_stop_now(nao_ip, port)
                _say_paused(tts, asr, "Okay, let's have a chat!")
                _flush_word(memory)
                return "chat"

            elif word == "stand up":
                _go_to_posture_bg_delayed(nao_ip, port, "StandInit", 0.6, delay=0.05)
                _say_paused(tts, asr, "Okay, standing up.")

            elif word == "sit down":
                _go_to_posture_bg_delayed(nao_ip, port, "Sit", 0.6, delay=0.05)
                _say_paused(tts, asr, "Okay, sitting down.")

            _flush_word(memory)
            try: asr.subscribe("NAO_Chat_Listener")
            except: pass
            time.sleep(0.20)
    finally:
        head_flag["stop"] = True
        _tracker_stop_now(nao_ip, port)
        _stop_move_now(nao_ip, port)
