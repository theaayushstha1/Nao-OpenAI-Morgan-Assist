# chat_mode.py
# -*- coding: utf-8 -*-
# chat with modes; voice resets on start; server does transcription

from __future__ import print_function
import os, json, random, requests, time, re
from naoqi import ALProxy
from utils.camera_capture import capture_photo

# --- fallbacks (keep running even if some modules are missing) ---
try:
    from audio_handler import record_audio
except Exception:
    def record_audio(_nao_ip): return "/home/nao/last.wav"

try:
    from utils.face_utils import detect_face, detect_mood
except Exception:
    def detect_face(_nao_ip, _port=9559, _timeout=5): return True
    def detect_mood(_nao_ip, _port=9559): return "neutral"

try:
    from face_recognition_utils import identify_face, learn_face
except Exception:
    def identify_face(_): return None
    def learn_face(_, __): return False

import memory_manager

# --- server ---
SERVER_IP = "172.20.95.120"
SERVER_URL = "http://{}:5000/upload".format(SERVER_IP)
FACE_RECO_URL = "http://{}:5000/face/recognize".format(SERVER_IP)
FACE_ENROLL_URL = "http://{}:5000/face/enroll".format(SERVER_IP)

# --- voice profiles ---
# speed: 50..400 (100=default), pitchShift: ~0.5..2.0, volume: 0..1
DEFAULT_PROFILE = dict(speed=85, pitch=1.05, volume=0.8)
MODE_PROFILES = {
    "assistant":   dict(speed=85, pitch=1.05, volume=0.85, prompt="Assistant mode on. How can I help?"),
    "study":       dict(speed=80, pitch=1.00, volume=0.85, prompt="Study mode. What topic?"),
    "therapy":     dict(speed=75, pitch=0.97, volume=0.80, prompt="Therapy mode. I'm here to listen."),
    "humor":       dict(speed=98, pitch=1.12, volume=0.85, prompt="Humor mode. I’ll keep it light."),
    "coach":       dict(speed=88, pitch=1.03, volume=0.86, prompt="Coach mode. What’s your goal today?"),
    "storyteller": dict(speed=82, pitch=1.07, volume=0.85, prompt="Storyteller mode. Pick a genre."),
    "translator":  dict(speed=85, pitch=1.05, volume=0.85, prompt="Translator mode. Say a sentence to translate."),
    "default":     dict(speed=85, pitch=1.05, volume=0.80, prompt="Back to normal voice.")
}

# shown to new users (kept for reference; we now speak a single sentence)
MODE_BLURBS = [
    ("Assistant",   "General help and daily questions."),
    ("Study",       "Explain topics, quiz you, step by step."),
    ("Therapy",     "Calm, supportive chats. Not medical advice."),
    ("Humor",       "Jokes and light banter."),
    ("Coach",       "Goals, plans, motivation."),
    ("Storyteller", "Stories on any topic or style."),
    ("Translator",  "Translate what you say.")
]

# robust keywords -> internal keys
MODE_KEYS = {
    # assistant
    "assistant": "assistant", "help": "assistant", "general": "assistant", "default": "assistant",
    # study
    "study": "study", "tutor": "study", "homework": "study", "exam": "study", "learn": "study",
    # therapy
    "therapy": "therapy", "therapist": "therapy", "support": "therapy", "counsel": "therapy", "calm": "therapy",
    # humor
    "humor": "humor", "funny": "humor", "joke": "humor", "laugh": "humor", "banter": "humor",
    # coach
    "coach": "coach", "motivate": "coach", "motivation": "coach", "goal": "coach", "plan": "coach", "productivity": "coach",
    # storyteller
    "story": "storyteller", "storyteller": "storyteller", "fairy tale": "storyteller", "bedtime": "storyteller", "narrate": "storyteller",
    # translator
    "translator": "translator", "translate": "translator", "translation": "translator"
}

# --- tiny utils ---
def _color_to_rgb(name):
    return {
        "red":[1,0,0], "green":[0,1,0], "blue":[0,0,1],
        "yellow":[1,1,0], "purple":[1,0,1], "white":[1,1,1]
    }.get((name or "").lower(), [1,1,1])

def sanitize_text(text):
    try:
        if isinstance(text, bytes):
            try: text = text.decode('utf-8', errors='ignore')
            except TypeError: text = text.decode('utf-8')
    except Exception:
        text = str(text)
    try:
        basestring
    except NameError:
        basestring = (str, bytes)
    if not isinstance(text, basestring):
        text = str(text)
    return ''.join(c if 32 <= ord(c) <= 126 else ' ' for c in text).strip()

def extract_name(text):
    try: lower = (text or "").lower()
    except Exception: lower = str(text).lower()
    m = re.search(r"(?:my name is|call me|i am)\s+([A-Za-z]+)", lower)
    return m.group(1).capitalize() if m else "friend"

def _post_image(url, img_path, extra=None, timeout=6.0):
    with open(img_path, "rb") as f:
        files = {"file": (os.path.basename(img_path), f, "image/jpeg")}
        data = extra or {}
        r = requests.post(url, files=files, data=data, timeout=timeout)
        r.raise_for_status()
        return r.json()

# --- TTS ---
def _apply_tts(tts, speed=None, pitch=None, volume=None):
    if volume is not None:
        try: tts.setParameter("volume", float(volume))
        except: pass
    if speed is not None:
        try: tts.setParameter("speed", float(speed))
        except: pass
    if pitch is not None:
        try: tts.setParameter("pitchShift", float(pitch))
        except: pass

def _reset_tts(tts):
    _apply_tts(tts,
        speed=DEFAULT_PROFILE["speed"],
        pitch=DEFAULT_PROFILE["pitch"],
        volume=DEFAULT_PROFILE["volume"]
    )

def _set_mode_profile(tts, mode_key):
    prof = MODE_PROFILES.get(mode_key) or MODE_PROFILES["default"]
    _apply_tts(tts, speed=prof["speed"], pitch=prof["pitch"], volume=prof["volume"])
    return prof.get("prompt")

# --- face ID ---
def recognize_or_enroll(robot, nao_ip, port):
    # try recognize first
    photo_path = capture_photo(nao_ip, port, "/home/nao/face.jpg")
    if photo_path and os.path.exists(photo_path):
        try:
            info = _post_image(FACE_RECO_URL, photo_path, {"tolerance": "0.60"})
            if info.get("ok") and info.get("match"):
                return info.get("name") or "friend", True
        except Exception:
            pass
    # enroll flow
    robot.say("I don't know you yet. Please tell me your first name after the beep.")
    time.sleep(0.5)
    name_wav = record_audio(nao_ip)
    user_name = "friend"
    try:
        with open(name_wav, "rb") as f:
            res = requests.post(SERVER_URL, files={"file": f}, data={"username": user_name}, timeout=10)
        spoken = (res.json() or {}).get("user_input", "")
        extracted = extract_name(spoken)
        if extracted and extracted.lower() != "friend":
            user_name = extracted
    except Exception:
        pass
    if user_name == "friend":
        robot.say("I did not catch your name. I'll call you friend for now.")
        return user_name, False
    robot.say("Nice to meet you, {}. Hold still while I learn your face.".format(user_name))
    for _ in range(5):
        time.sleep(0.4)
        p = capture_photo(nao_ip, port, "/home/nao/face.jpg")
        if not (p and os.path.exists(p)): continue
        try: _post_image(FACE_ENROLL_URL, p, {"name": user_name})
        except Exception: pass
    robot.say("Got it. I will remember you next time, {}.".format(user_name))
    return user_name, False

# --- onboarding: one clear sentence ---
def _speak_onboarding(robot):
    robot.say(
        "I can chat in different modes: Assistant, Study, Therapy, Humor, Coach, Storyteller, and Translator. "
        "Please say one mode now, for example: Assistant Mode."
    )

# server-only transcription (with quick retry)
def _listen_transcribe(nao_ip, username="picker", timeout=15):
    try:
        p = record_audio(nao_ip)
        if not (p and os.path.exists(p)):
            return ""
        with open(p, "rb") as f:
            r = requests.post(SERVER_URL, files={"file": f}, data={"username": username}, timeout=timeout)
        if not r:
            return ""
        j = r.json() or {}
        return (j.get("user_input", "") or "").strip()
    except Exception:
        try:
            with open(p, "rb") as f:
                r = requests.post(SERVER_URL, files={"file": f}, data={"username": username}, timeout=timeout)
            j = r.json() or {}
            return (j.get("user_input", "") or "").strip()
        except Exception:
            return ""

# parse to (mode, confidence)
def _parse_mode(text):
    t = (text or "").lower()

    # exact "<mode> mode" → high
    m = re.search(r"\b([a-z]+)\s+mode\b", t)
    if m:
        token = m.group(1)
        key = MODE_KEYS.get(token)
        if key:
            return key, 1.0

    # token hits
    hits = []
    for token, key in MODE_KEYS.items():
        if token in t:
            hits.append(key)

    if not hits:
        return None, 0.0

    unique = list(set(hits))
    if len(unique) == 1:
        strong = unique[0] in ("assistant","study","therapy","humor","coach","storyteller","translator")
        return unique[0], (1.0 if strong else 0.6)

    # multiple modes mentioned → pick first by common priority
    for key in ("assistant","study","therapy","humor","coach","storyteller","translator"):
        if key in hits:
            return key, 0.4

    return None, 0.0

# only confirm if low confidence
def _confirm_choice_if_needed(robot, nao_ip, chosen_key, confidence):
    if confidence >= 0.9:
        return True
    pretty = "Storyteller" if chosen_key == "storyteller" else chosen_key.capitalize()
    robot.say("You said {} mode. Is that right?".format(pretty))
    ans = _listen_transcribe(nao_ip, username="confirm").lower()
    if any(w in ans for w in ["yes","yeah","yep","correct","right","okay","ok","sure"]):
        return True
    if any(w in ans for w in ["no","nope","wrong","change","different","cancel"]):
        return False
    return True

# picker loop (few tries then default)
def pick_mode_interactive(tts, robot, nao_ip, default_mode="assistant", max_tries=3):
    tries = 0
    while tries < max_tries:
        _speak_onboarding(robot)   # single clear sentence
        choice_text = _listen_transcribe(nao_ip)
        chosen, conf = _parse_mode(choice_text)

        if chosen:
            if _confirm_choice_if_needed(robot, nao_ip, chosen, conf):
                prompt = _set_mode_profile(tts, chosen)
                if prompt: robot.say(prompt)
                return chosen
            else:
                robot.say("Okay, let's try again.")
        else:
            robot.say("Sorry, I didn't catch a valid mode.")

        tries += 1

    robot.say("I'll start in {} mode for now.".format(default_mode.capitalize()))
    prompt = _set_mode_profile(tts, default_mode)
    if prompt: robot.say(prompt)
    return default_mode

# --- main entry ---
def enter_chat_mode(robot, nao_ip="127.0.0.1", port=9559):
    motion  = ALProxy("ALMotion",       nao_ip, port)
    posture = ALProxy("ALRobotPosture", nao_ip, port)
    leds    = ALProxy("ALLeds",         nao_ip, port)
    tts     = ALProxy("ALTextToSpeech", nao_ip, port)

    # reset voice every run
    _reset_tts(tts)
    current_mode = "assistant"

    # presence
    robot.say("Scanning for a friend...")
    if not detect_face(nao_ip):
        robot.say("I don't see anyone. Come back when you're ready.")
        return

    # mood LEDs
    mood = detect_mood(nao_ip) or "neutral"
    r,g,b = _color_to_rgb({"happy":"yellow","neutral":"white","annoyed":"purple"}.get(mood,"white"))
    try: leds.fadeRGB("FaceLeds", r,g,b, 0.3)
    except: pass

    # face ID
    user_name, recognized = recognize_or_enroll(robot, nao_ip, port)
    if recognized: robot.say("Welcome back, {}!".format(user_name))

    # mode picker (single prompt handles it)
    current_mode = pick_mode_interactive(tts, robot, nao_ip, default_mode="assistant")

    # local memory (optional)
    try: memory_manager.initialize_user(user_name)
    except Exception: pass

    # chat loop
    while True:
        robot.say("I'm listening.")
        audio_path = record_audio(nao_ip)
        if not os.path.exists(audio_path):
            robot.say("Please repeat.")
            continue

        # send to server with mode so it can style the reply
        try:
            with open(audio_path, "rb") as f:
                res = requests.post(
                    SERVER_URL,
                    files={"file": f},
                    data={"username": user_name, "mode": current_mode}
                )
            res.raise_for_status()
            data = res.json()
        except Exception:
            robot.say("Network issue. Trying again.")
            continue

        user_text  = (data.get("user_input") or "")
        reply_text = (data.get("reply") or "")
        func_call  = data.get("function_call", {}) or {}
        ut = user_text.lower()

        # quick mode switches mid-chat
        if "assistant mode" in ut or ("assistant" in ut and "mode" in ut):
            current_mode = "assistant"; robot.say(_set_mode_profile(tts,"assistant")); continue
        if any(k in ut for k in ["study mode","study","tutor","homework","exam","learn"]):
            current_mode = "study"; robot.say(_set_mode_profile(tts,"study")); continue
        if any(k in ut for k in ["therapy mode","therapy","therapist","support","counsel","calm"]):
            current_mode = "therapy"; robot.say(_set_mode_profile(tts,"therapy")); continue
        if any(k in ut for k in ["humor mode","humor","funny","joke","laugh","banter"]):
            current_mode = "humor"; robot.say(_set_mode_profile(tts,"humor")); continue
        if any(k in ut for k in ["coach mode","coach","motivate","motivation","goal","plan","productivity"]):
            current_mode = "coach"; robot.say(_set_mode_profile(tts,"coach")); continue
        if any(k in ut for k in ["storyteller mode","story mode","story","fairy tale","bedtime","narrate"]):
            current_mode = "storyteller"; robot.say(_set_mode_profile(tts,"storyteller")); continue
        if any(k in ut for k in ["translator mode","translator","translate","translation"]):
            current_mode = "translator"; robot.say(_set_mode_profile(tts,"translator")); continue

        # quick reset if voice drifts
        if any(k in ut for k in ["normal voice","reset voice","default voice"]):
            _reset_tts(tts); current_mode = "assistant"; robot.say("Back to normal voice."); continue

        # persist (optional)
        try:
            if user_text: memory_manager.add_user_message(user_name, user_text)
            log_payload = reply_text if reply_text else json.dumps(func_call)
            try: memory_manager.add_bot_reply(user_name, log_payload)
            except TypeError: memory_manager.add_bot_reply(log_payload)
            try: memory_manager.save_chat_history(user_name)
            except TypeError: memory_manager.save_chat_history()
        except Exception:
            pass

        # speak
        if reply_text:
            robot.say(sanitize_text(reply_text))

        # exit
        if any(k in ut for k in ["stop","goodbye","bye","exit","quit"]):
            robot.say("Catch you later!")
            break

        # simple actions (optional)
        name = func_call.get("name")
        if name == "stand_up":
            try: posture.goToPosture("StandInit", 0.6)
            except: pass
        elif name == "sit_down":
            try: posture.goToPosture("Sit", 0.6)
            except: pass
