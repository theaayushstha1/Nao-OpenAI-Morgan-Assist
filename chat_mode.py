# -*- coding: utf-8 -*-
from __future__ import print_function
import os, json, random, requests, time, re, threading
from naoqi import ALProxy
from utils.camera_capture import capture_photo
from processing_announcer import ProcessingAnnouncer
import memory_manager

SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.105")

SERVER_URL      = "http://{}:5000/upload".format(SERVER_IP)
CHAT_TEXT_URL   = "http://{}:5000/chat_text".format(SERVER_IP)
FACE_RECO_URL   = "http://{}:5000/face/recognize".format(SERVER_IP)
FACE_ENROLL_URL = "http://{}:5000/face/enroll".format(SERVER_IP)

SESSION = requests.Session()
DEFAULT_TIMEOUT = 30

VOICE_PROFILES = {
    "general":   {"speed": 100, "pitch": 0.95},
    "study":     {"speed": 110, "pitch": 1.19},
    "therapist": {"speed":  85, "pitch": 0.85},
    "broker":    {"speed":  95, "pitch": 1.10},
}
VALID_FOR_SERVER = ("general","study","therapist","broker")
def _canon_for_server(mode): return mode if mode in VALID_FOR_SERVER else "general"

def _apply_mode_voice(tts, mode):
    prof = VOICE_PROFILES.get(mode, VOICE_PROFILES["general"])
    try:
        tts.setParameter("speed", float(prof["speed"]))
        tts.setParameter("pitchShift", float(prof["pitch"]))
        tts.setVolume(1.0)
    except:
        pass

# ---------- NEW: voice/tts helpers ----------
def _reset_voice(tts):
    """Return TTS to the default voice profile."""
    _apply_mode_voice(tts, "general")

def _stop_tts(tts):
    """Hard-stop any queued/ongoing NAO TTS, if supported."""
    try:
        stop_all = getattr(tts, "stopAll", None)
        if callable(stop_all):
            stop_all()
    except:
        pass

# ---------- Announcer wrapper ----------
def call_with_processing_announcer(tts, server_call, first_delay=2.5, interval=3.5, max_utterances=2):
    ann = ProcessingAnnouncer(
        tts_say=lambda s: _say(tts, s),
        stop_all=getattr(tts, "stopAll", None),
        first_delay=first_delay,
        interval=interval,
        max_utterances=max_utterances,
    )
    ann.start()
    try:
        return server_call()
    finally:
        try:
            ann.stop(interrupt=True)
        finally:
            _stop_tts(tts)  


# ---------- TTS safety ----------
try:
    unicode_type = unicode
except NameError:
    unicode_type = str

def _to_sayable(text):
    try:
        if text is None:
            s = u"Okay."
        elif isinstance(text, str):
            try:
                s = text.decode('utf-8', 'ignore')
            except Exception:
                try:
                    s = text.decode('latin-1','ignore')
                except Exception:
                    s = unicode_type(text)
        elif isinstance(text, unicode_type):
            s = text
        else:
            s = unicode_type(text)
        s = u''.join(c if 32 <= ord(c) <= 126 else u' ' for c in s).strip()
        if not s: s = u"Okay."
        try:
            return s.encode('utf-8')
        except Exception:
            return str(s)
    except Exception:
        return "Okay."

def _say(robot, text):
    try:
        s = _to_sayable(text)
        robot.say(s)
    except Exception as e:
        print("[WARN] TTS failed: {}".format(e))

# ---------- NLP for mode words (client fallback) ----------
KEYWORDS = {
    "general":   ["general","normal","default","assistant","regular","general mode","normal mode"],
    "study":     ["study","study mode","school","homework","learn","exam","class","test","assignment"],
    "therapist": ["therapist","therapy","therapist mode","therapy mode","mental","feelings","stress","anxious","depressed","mood","relax","calm"],
    "broker":    ["broker","broker mode","stock","stocks","market","markets","trading","finance"],
}
SWITCH_WORDS = ["switch mode","change mode","mode menu","set mode","choose mode","pick a mode",
                "switch to","change to","set to","go to","turn to","switch","change","set","go","turn"]

def _extract_mode_from_text(text):
    if not text: return None
    t = text.lower()
    for mode, kws in KEYWORDS.items():
        for kw in kws:
            if re.search(r"\b"+re.escape(kw)+r"\b", t):
                return mode
    return None

def _is_switch_request(text):
    if not text: return False
    t = text.lower()
    return any(kw in t for kw in SWITCH_WORDS)

# ---------- helpers ----------
def _color_to_rgb(name):
    return {"red":[1,0,0],"green":[0,1,0],"blue":[0,0,1],
            "yellow":[1,1,0],"purple":[1,0,1],"white":[1,1,1]}.get((name or "").lower(), [1,1,1])

def sanitize_text(text):
    text = text if isinstance(text, (str, bytes)) else str(text)
    if isinstance(text, bytes):
        try: text = text.decode("utf-8", "ignore")
        except: text = text.decode("utf-8")
    return ''.join(c if 32 <= ord(c) <= 126 else ' ' for c in text).strip()

def extract_name(text):
    m = re.search(r"(?:my name is|call me|i am)\s+([A-Za-z]+)", (text or "").lower())
    return m.group(1).capitalize() if m else "friend"

def _post_image(url, img_path, extra=None, timeout=6.0):
    with open(img_path, "rb") as f:
        files = {"file": (os.path.basename(img_path), f, "image/jpeg")}
        data = extra or {}
        r = SESSION.post(url, files=files, data=data, timeout=timeout)
        r.raise_for_status()
        return r.json()

# ---------- gestures ----------
def _stiffen(motion, eff, on=True):
    try: motion.setStiffnesses(eff, 1.0 if on else 0.0)
    except: pass
    try: motion.setBreathEnabled(eff, False if on else True)
    except: pass

def _arm_neutral(motion):
    names = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw",
             "LShoulderPitch","LShoulderRoll","LElbowYaw","LElbowRoll","LWristYaw"]
    angles = [1.25, 0.05, 1.20, 0.50, 0.00, 1.25,-0.05,-1.20,-0.50, 0.00]
    try: motion.angleInterpolationWithSpeed(names, angles, 0.5)
    except: pass

def _g_open_arms_in_front(motion, speed=0.55):
    names = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RHand",
             "LShoulderPitch","LShoulderRoll","LElbowYaw","LElbowRoll","LHand"]
    openA  = [0.90, 0.25, 1.00, 0.70, 1.0, 0.90,-0.25,-1.00,-0.70, 1.0]
    closeA = [1.10, 0.10, 1.20, 0.50, 0.6, 1.10,-0.10,-1.20,-0.50, 0.6]
    try:
        motion.angleInterpolationWithSpeed(names, openA,  speed)
        motion.angleInterpolationWithSpeed(names, closeA, speed)
    except: pass

def _g_wide_point_at_user(motion, speed=0.55):
    try:
        motion.angleInterpolationWithSpeed(
            ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw","RHand"],
            [0.55, 0.35, 1.15, 0.85, 0.00, 0.8], speed)
    except: pass

def _g_point_forward(motion, right=True, speed=0.55):
    if right:
        names = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw","RHand"]; angles= [0.60, 0.15, 1.20, 0.85, 0.00, 0.8]
    else:
        names = ["LShoulderPitch","LShoulderRoll","LElbowYaw","LElbowRoll","LWristYaw","LHand"]; angles= [0.60,-0.15,-1.20,-0.85, 0.00, 0.8]
    try: motion.angleInterpolationWithSpeed(names, angles, speed)
    except: pass

def _g_finger_roll(motion, right=True, cycles=3, speed=0.5):
    hand  = "RHand" if right else "LHand"; wrist = "RWristYaw" if right else "LWristYaw"
    try:
        for _ in range(cycles):
            motion.angleInterpolationWithSpeed(hand, 1.0, speed)
            motion.angleInterpolationWithSpeed(wrist,  0.3, speed)
            motion.angleInterpolationWithSpeed(hand, 0.4, speed)
            motion.angleInterpolationWithSpeed(wrist, -0.3, speed)
    except: pass

def _g_enumerate(motion, right=True, speed=0.60):
    jr = ("RElbowRoll","RWristYaw") if right else ("LElbowRoll","LWristYaw")
    try:
        for a in [0.55, 0.95, 0.55]:
            motion.angleInterpolationWithSpeed(jr[0], a if right else -a, speed)
            motion.angleInterpolationWithSpeed(jr[1], 0.35, speed)
            motion.angleInterpolationWithSpeed(jr[1],-0.35, speed)
    except: pass

def _g_arc_explain(motion, right=True, speed=0.60):
    j = ("RShoulderPitch","RShoulderRoll") if right else ("LShoulderPitch","LShoulderRoll")
    seq = [(0.78,  0.30 if right else -0.30),(1.12,  0.08 if right else -0.08),(0.92,  0.22 if right else -0.22)]
    try:
        for a,b in seq:
            motion.angleInterpolationWithSpeed(j[0], a, speed); motion.angleInterpolationWithSpeed(j[1], b, speed)
    except: pass

def _gesture_block(motion, mode):
    speed = 0.60 if mode == "study" else (0.45 if mode == "therapist" else 0.52)
    pool = ["open","widepoint","fingerroll","point","arc","enumerate"]
    if mode == "therapist": pool = ["open","point","arc"]
    v = random.choice(pool)
    if v == "open": _g_open_arms_in_front(motion, speed)
    elif v == "widepoint": _g_wide_point_at_user(motion, speed)
    elif v == "fingerroll": _g_finger_roll(motion, True, 2, speed)
    elif v == "arc": _g_arc_explain(motion, True, speed)
    elif v == "enumerate": _g_enumerate(motion, True, speed)
    else: _g_point_forward(motion, True, speed)

def _gesture_loop(motion, mode, total_s):
    t0 = time.time()
    try: _stiffen(motion, "RArm", True); _stiffen(motion, "LArm", True)
    except: pass
    _arm_neutral(motion)
    while time.time() - t0 < total_s:
        _gesture_block(motion, mode); time.sleep(0.04)
    _arm_neutral(motion)
    try: _stiffen(motion, "RArm", False); _stiffen(motion, "LArm", False)
    except: pass

def _estimate_speech_secs(text, mode):
    words = max(1, len((text or "").split()))
    wps = 2.7 if mode == "study" else (1.8 if mode == "therapist" else 2.3)
    return max(2.0, min(12.0, words / wps + 0.7))

def _speak_with_gestures(robot, tts, motion, text, mode):
    try: tts.setVolume(1.0)
    except: pass
    dur = _estimate_speech_secs(text, mode)
    try:
        th = threading.Thread(target=_gesture_loop, args=(motion, mode, dur)); th.daemon = True; th.start()
    except: pass
    _say(robot, text)

# ---------- face ID ----------
def recognize_or_enroll(robot, nao_ip, port):
    photo_path = capture_photo(nao_ip, port, "/home/nao/face.jpg")
    if photo_path and os.path.exists(photo_path):
        try:
            info = _post_image(FACE_RECO_URL, photo_path, {"tolerance": "0.60"})
            if info.get("ok") and info.get("match"):
                return info.get("name") or "friend", True
        except Exception:
            pass

    _say(robot, "I don't know you yet. Tell me your first name, please.")
    from audio_handler import record_audio
    time.sleep(0.3)
    name_wav = record_audio(nao_ip)
    user_name = "friend"
    try:
        with open(name_wav, "rb") as f:
            res = SESSION.post(SERVER_URL, files={"file": f}, data={"username": user_name}, timeout=DEFAULT_TIMEOUT)
        spoken = (res.json() or {}).get("user_input", "")
        extracted = extract_name(spoken)
        if extracted and extracted.lower() != "friend": user_name = extracted
    except Exception:
        pass

    if user_name == "friend":
        _say(robot, "I didn't catch it—I'll call you friend for now.")
        return user_name, False

    _say(robot, "Nice to meet you, {}. Hold still for a photo.".format(user_name))
    for _ in range(5):
        time.sleep(0.3)
        p = capture_photo(nao_ip, port, "/home/nao/face.jpg")
        if not (p and os.path.exists(p)): continue
        try: _post_image(FACE_ENROLL_URL, p, {"name": user_name})
        except Exception:
            pass
    _say(robot, "All set, {}. I'll remember you.")
    return user_name, False

def _pick_mode(robot, nao_ip, user_name, default_mode="general"):
    _say(robot, "Choose a chat mode")
    from audio_handler import record_audio

    def _hear_once():
        wav = record_audio(nao_ip)
        try:
            with open(wav, "rb") as f:
                res = SESSION.post(
                    SERVER_URL,
                    files={"file": f},
                    data={"username": user_name},
                    timeout=DEFAULT_TIMEOUT
                )
            res.raise_for_status()
            data = res.json() or {}
            server_mode = (data.get("active_mode") or "").lower()
            if server_mode in VALID_FOR_SERVER:
                return server_mode
            return _extract_mode_from_text(data.get("user_input", "") or "")
        except Exception:
            return None

    chosen = _hear_once()
    if not chosen:
        _say(robot, "Sorry—say: General, Study, Therapist, or Broker.")
        chosen = _hear_once()

    if not chosen:
        _say(robot, "Using {} mode.".format(default_mode))
        return default_mode

    _say(robot, "{} mode selected.".format(chosen.capitalize()))
    return chosen

def _requery_immediate(username, text, new_mode):
    try:
        payload = {"username": username, "text": text, "mode": _canon_for_server(new_mode)}
        r = SESSION.post(CHAT_TEXT_URL, json=payload, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def _mode_enter_actions(robot, posture, tts, motion, mode):
    if mode == "therapist":
        _say(robot, "Please sit with me. I’ll listen to your problems carefully.")
        try: posture.goToPosture("Sit", 0.6)
        except: pass
    elif mode == "study":
        _say(robot, "First of all, Stand up with me and lets do some exercise. Let’s learn together.")
        try: posture.goToPosture("StandInit", 0.6)
        except: pass

# ---------- main ----------
def enter_chat_mode(robot, nao_ip="127.0.0.1", port=9559):
    motion  = ALProxy("ALMotion",       nao_ip, port)
    posture = ALProxy("ALRobotPosture", nao_ip, port)
    leds    = ALProxy("ALLeds",         nao_ip, port)
    tts     = ALProxy("ALTextToSpeech", nao_ip, port)

    _reset_voice(tts)

    _say(robot, "Scanning for a friend...")
    try:
        from utils.face_utils import detect_face, detect_mood
        if not detect_face(nao_ip):
            _say(robot, "I don't see anyone yet. Come back when you're ready.")
            return
        mood = detect_mood(nao_ip) or "neutral"
    except Exception:
        mood = "neutral"

    r,g,b = _color_to_rgb({"happy":"yellow","neutral":"white","annoyed":"purple"}.get(mood, "white"))
    try: leds.fadeRGB("FaceLeds", r, g, b, 0.3)
    except: pass

    user_name, recognized = recognize_or_enroll(robot, nao_ip, port)
    if recognized:
        _say(robot, "Welcome back, {}!".format(user_name))

    mode = _pick_mode(robot, nao_ip, user_name, default_mode="general")
    _apply_mode_voice(tts, mode)
    _mode_enter_actions(robot, posture, tts, motion, mode)
    _say(robot, "Hey {}! {} mode is on. Ask me anything!".format(user_name, mode.capitalize()))

    try:
        memory_manager.initialize_user(user_name)
    except Exception:
        pass

    try:
        from audio_handler import record_audio
        while True:
            _say(robot, "I’m listening.")
            audio_path = record_audio(nao_ip)
            if not os.path.exists(audio_path):
                _say(robot, "I didn’t catch that—please repeat.")
                continue

            def server_call():
                with open(audio_path, "rb") as f:
                    return SESSION.post(
                        SERVER_URL,
                        files={"file": f},
                        data={"username": user_name, "mode": _canon_for_server(mode)},
                        timeout=DEFAULT_TIMEOUT
                    )

            try:
                res = call_with_processing_announcer(tts, server_call)

                # Handle Whisper 503 gracefully
                if res.status_code == 503:
                    try:
                        err = res.json().get("detail", "") or ""
                    except Exception:
                        err = ""
                    if "audio_too_short" in err:
                        _say(robot, "I didn’t catch that — could you say it again a little longer?")
                    elif "bad_wav_header" in err:
                        _say(robot, "Hmm, I couldn’t read that clip. Let’s try again.")
                    else:
                        _say(robot, "The server is busy — let’s try once more.")
                    time.sleep(0.6)
                    continue

                res.raise_for_status()
                data = res.json()

            except Exception:
                _say(robot, "The connection hiccupped — please try again.")
                continue

            user_text  = data.get("user_input", "") or ""
            reply_text = data.get("reply", "") or ""
            func_call  = data.get("function_call", {}) or {}

            server_mode         = (data.get("active_mode") or "").strip().lower() or None
            server_mode_changed = bool(data.get("mode_changed"))
            server_mode_prompt  = bool(data.get("mode_prompt"))

            immediate_switch = False

            if server_mode_prompt:
                _say(robot, "Which mode would you like: General, Study, Therapist, or Broker?")
                picked = _pick_mode(robot, nao_ip, user_name, default_mode=(server_mode or mode or "general"))
                if picked and picked != mode:
                    mode = picked
                    _apply_mode_voice(tts, mode)
                    _mode_enter_actions(robot, posture, tts, motion, mode)
                    print(">> MODE (client): switched to {}".format(mode))
                    again = _requery_immediate(user_name, user_text, mode)
                    reply_text = (again or {}).get("reply", "") or \
                                 "✅ Switched to {} mode. Ask me anything!".format(mode.capitalize())
                    immediate_switch = True
            else:
                if server_mode and (server_mode != mode or server_mode_changed):
                    mode = server_mode
                    _apply_mode_voice(tts, mode)
                    _mode_enter_actions(robot, posture, tts, motion, mode)
                    print(">> MODE (client): adopted server mode {}".format(mode))
                    if not reply_text:
                        reply_text = "✅ Switched to {} mode. Ask me anything!".format(mode.capitalize())
                    immediate_switch = True
                else:
                    chosen_direct = _extract_mode_from_text(user_text)
                    asked_switch  = _is_switch_request(user_text)
                    if asked_switch or (chosen_direct and chosen_direct != mode):
                        new_mode = chosen_direct or mode
                        if new_mode != mode:
                            mode = new_mode
                            _apply_mode_voice(tts, mode)
                            _mode_enter_actions(robot, posture, tts, motion, mode)
                            print(">> MODE (client-fallback): switched to {}".format(mode))
                            again = _requery_immediate(user_name, user_text, mode)
                            reply_text = (again or {}).get("reply", "") or \
                                         "✅ Switched to {} mode. Ask me anything!".format(mode.capitalize())
                            immediate_switch = True

            # persist
            try:
                if user_text:
                    memory_manager.add_user_message(user_name, user_text)
                memory_manager.add_bot_reply(user_name, reply_text if reply_text else json.dumps(func_call))
                memory_manager.save_chat_history(user_name)
            except Exception:
                pass

            # speak
            if reply_text:
                _speak_with_gestures(robot, tts, motion, reply_text, mode)
            elif immediate_switch:
                _speak_with_gestures(robot, tts, motion,
                                     "✅ Switched to {} mode.".format(mode.capitalize()), mode)

            if "stop" in (user_text or "").lower():
                _say(robot, "Catch you later!")
                break

            # function calls
            name = (func_call or {}).get("name")
            if name == "stand_up":
                try: posture.goToPosture("StandInit", 0.6)
                except: pass
            elif name == "sit_down":
                try: posture.goToPosture("Sit", 0.6)
                except: pass
            elif name == "down":
                try:
                    motion.setStiffnesses("Body", 1.0)
                    joints = ["RHipPitch","LHipPitch","RKneePitch","LKneePitch","RAnklePitch","LAnklePitch"]
                    angles = [0.3, 0.3, 0.5, 0.5, -0.2, -0.2]
                    motion.setAngles(joints, angles, 0.2)
                except: pass
    finally:
        _reset_voice(tts)
