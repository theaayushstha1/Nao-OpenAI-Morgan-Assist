# -*- coding: utf-8 -*-
from __future__ import print_function
import os, json, random, requests, time, re, threading
from naoqi import ALProxy
from utils.camera_capture import capture_photo
from processing_announcer import ProcessingAnnouncer
import memory_manager


SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.123")
SERVER_URL = "http://{}:5000/upload".format(SERVER_IP)
CHAT_TEXT_URL = "http://{}:5000/chat_text".format(SERVER_IP)
FACE_RECO_URL = "http://{}:5000/face/recognize".format(SERVER_IP)
FACE_ENROLL_URL = "http://{}:5000/face/enroll".format(SERVER_IP)
SESSION = requests.Session()
DEFAULT_TIMEOUT = 30


# Voice profiles for different modes (NOW WITH 5 MODES!)
VOICE_PROFILES = {
    "general": {"speed": 100, "pitch": 0.95},
    "study": {"speed": 110, "pitch": 1.19},
    "therapist": {"speed": 85, "pitch": 0.85},
    "broker": {"speed": 95, "pitch": 1.10},
    "morgan": {"speed": 105, "pitch": 1.05},  # New Morgan/Chatbot mode
}
VALID_FOR_SERVER = ("general", "study", "therapist", "broker", "morgan")


# ===== EXIT DETECTION SYSTEM =====
EXIT_PATTERNS = [
    # Direct exit commands
    r"\b(exit|quit|stop|end|goodbye|bye|close)\b.*\b(chat|mode|conversation|talking|session)\b",
    r"\b(chat|mode|conversation|talking|session)\b.*\b(exit|quit|stop|end|goodbye|bye|close)\b",
    
    # Standalone exit phrases
    r"^(exit|quit|stop now|end chat|goodbye|bye bye|that's all|that is all)$",
    r"^(i'm done|i am done|we're done|we are done)$",
    r"^(stop talking|stop listening|no more)$",
    
    # Polite exit phrases
    r"\b(i (want|need) to (go|leave|stop)|let me (go|leave)|gotta go)\b",
    r"\b(talk to you later|catch you later|see you later)\b",
    r"\b(thanks.*bye|thank you.*bye|thanks.*good(bye)?)\b",
    
    # Context-specific exits
    r"\b(stop.*mode|exit.*mode|leave.*mode|quit.*mode)\b",
    r"\b(go back|return|switch back)\b.*\b(wake|main|menu)\b",
]

EXIT_KEYWORDS = [
    "exit", "quit", "stop", "end", "goodbye", "bye", "close",
    "done", "finished", "that's all", "no more", "leave", "go back"
]


def _detect_exit_intent(text):
    """
    Analyze user input for exit intent using pattern matching.
    Returns: True if exit detected, False otherwise
    """
    if not text:
        return False
    
    text_lower = text.lower().strip()
    
    # Check regex patterns first (most accurate)
    for pattern in EXIT_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            print("[EXIT DETECTED] Pattern match: {}".format(pattern))
            return True
    
    # Check for standalone exit keywords
    words = text_lower.split()
    if len(words) <= 3:  # Short utterances
        for keyword in EXIT_KEYWORDS:
            if keyword in words:
                print("[EXIT DETECTED] Keyword: {}".format(keyword))
                return True
    
    return False


# ===== MODE SWITCHING SYSTEM =====
MODE_KEYWORDS = {
    "general": [
        r"\b(general|normal|default|regular|basic)\b.*\bmode\b",
        r"\bmode\b.*\b(general|normal|default|regular|basic)\b",
        r"^(general|normal|default) mode$",
        r"\bswitch to (general|normal|default)\b",
    ],
    "study": [
        r"\b(study|school|homework|learn|learning|education|academic)\b.*\bmode\b",
        r"\bmode\b.*\b(study|school|homework|learn|learning|education)\b",
        r"^study mode$",
        r"\bswitch to (study|school|learning)\b",
        r"\b(help me (study|learn)|study (help|assist))\b",
    ],
    "therapist": [
        r"\b(therapist|therapy|mental|stress|mood|counseling|emotional)\b.*\bmode\b",
        r"\bmode\b.*\b(therapist|therapy|mental|stress|counseling)\b",
        r"^(therapist|therapy) mode$",
        r"\bswitch to (therapist|therapy|counseling)\b",
        r"\b(i (feel|need)|talk about (feelings|emotions|stress))\b",
    ],
    "broker": [
        r"\b(broker|stock|market|finance|trading|investment|stocks)\b.*\bmode\b",
        r"\bmode\b.*\b(broker|stock|market|finance|trading)\b",
        r"^(broker|finance|stock) mode$",
        r"\bswitch to (broker|finance|stock|trading)\b",
        r"\b(stock (price|market)|financial (advice|info))\b",
    ],
    "morgan": [
        # Morgan-specific triggers
        r"\b(morgan|chatbot|university|campus)\b.*\b(mode|assist|help|chat)\b",
        r"\b(mode|assist|help)\b.*\b(morgan|chatbot|university)\b",
        r"^(morgan|chatbot|university) (mode|assist)$",
        r"\bswitch to (morgan|chatbot|university)\b",
        r"\b(morgan (state|university)|about morgan|ask about (morgan|campus|university))\b",
        r"\b(university (help|info|question)|campus (info|question))\b",
    ],
}


def _detect_mode_switch(text):
    """
    Analyze user input for mode switching intent.
    Returns: mode name (str) or None
    """
    if not text:
        return None
    
    text_lower = text.lower().strip()
    
    # Check each mode's patterns
    for mode, patterns in MODE_KEYWORDS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                print("[MODE SWITCH DETECTED] Mode: {}, Pattern: {}".format(mode, pattern))
                return mode
    
    return None


# ===== HELPER FUNCTIONS =====
def _canon_for_server(m):
    # Map "morgan" mode to "chatbot" for server compatibility
    if m == "morgan":
        return "chatbot"
    return m if m in VALID_FOR_SERVER else "general"


def _apply_mode_voice(tts, mode):
    p = VOICE_PROFILES.get(mode, VOICE_PROFILES["general"])
    try:
        tts.setParameter("speed", float(p["speed"]))
        tts.setParameter("pitchShift", float(p["pitch"]))
        tts.setVolume(1.0)
    except Exception:
        pass


def _reset_voice(tts):
    _apply_mode_voice(tts, "general")


def _stop_tts(tts):
    try:
        stop_all = getattr(tts, "stopAll", None)
        if callable(stop_all):
            stop_all()
    except Exception:
        pass


def call_with_processing_announcer(tts, func):
    ann = ProcessingAnnouncer(
        tts_say=lambda s: _say(tts, s),
        stop_all=getattr(tts, "stopAll", None),
        first_delay=2.5,
        interval=3.5,
        max_utterances=2
    )
    ann.start()
    try:
        return func()
    finally:
        try:
            ann.stop(interrupt=True)
        finally:
            _stop_tts(tts)


try:
    unicode_type = unicode
except NameError:
    unicode_type = str


def _to_sayable(t):
    try:
        if t is None:
            s = u"Okay."
        elif isinstance(t, str):
            try:
                s = t.decode('utf-8', 'ignore')
            except Exception:
                s = unicode_type(t)
        elif isinstance(t, unicode_type):
            s = t
        else:
            s = unicode_type(t)
        s = u''.join(c if 32 <= ord(c) <= 126 else u' ' for c in s).strip()
        return s.encode('utf-8') if s else "Okay."
    except Exception:
        return "Okay."


def _say(robot, text):
    try:
        robot.say(_to_sayable(text))
    except Exception as e:
        print("[WARN] say:", e)


def _color_to_rgb(n):
    color_map = {
        "red": [1, 0, 0],
        "green": [0, 1, 0],
        "blue": [0, 0, 1],
        "yellow": [1, 1, 0],
        "purple": [1, 0, 1],
        "white": [1, 1, 1]
    }
    return color_map.get((n or "").lower(), [1, 1, 1])


def extract_name(t):
    m = re.search(r"(?:my name is|call me|i am)\s+([A-Za-z]+)", (t or "").lower())
    return m.group(1).capitalize() if m else "friend"


def _post_image(url, img_path, extra=None, timeout=6.0):
    with open(img_path, "rb") as f:
        files = {"file": (os.path.basename(img_path), f, "image/jpeg")}
        r = SESSION.post(url, files=files, data=(extra or {}), timeout=timeout)
        r.raise_for_status()
        return r.json()


def get_available_gestures(behav_mgr):
    try:
        allb = behav_mgr.getInstalledBehaviors()
    except Exception:
        return []
    built = [b for b in allb if "animations/Stand/Gestures/" in b]
    priority_keys = ["Explain", "ShowSky", "YouKnowWhat", "Point", "Yes", "No", "ComeOn", "This", "Think", "Shrug"]
    pri = [g for g in built if any(k in g for k in priority_keys)]
    if len(pri) < 10:
        remaining = min(len(built), 10 - len(pri))
        if remaining > 0:
            pri += random.sample(built, remaining)
    return sorted(list(set(pri)))


def _split_sentences(t):
    return [p.strip() for p in re.split(r'(?<=[.!?]) +', t) if p.strip()]


def _loop_gestures(behav_mgr, pool, stop):
    last = None
    while not stop.is_set() and pool:
        try:
            candidates = [x for x in pool if x != last] or pool
            g = random.choice(candidates)
            last = g
            print("[Gesture]", g)
            if behav_mgr.isBehaviorRunning(g):
                behav_mgr.stopBehavior(g)
            behav_mgr.runBehavior(g)
            time.sleep(random.uniform(0.8, 1.5))
        except Exception as e:
            print("[G err]", e)
            time.sleep(1)


def _speak_with_gestures(robot, tts, behav_mgr, text, mode, pool):
    parts = _split_sentences(text) or [text]
    for p in parts:
        stop = threading.Event()
        th = threading.Thread(target=_loop_gestures, args=(behav_mgr, pool, stop))
        th.daemon = True
        th.start()
        time.sleep(0.05)
        _say(robot, p)
        stop.set()
        th.join(timeout=0.1)
        try:
            behav_mgr.stopAllBehaviors()
        except Exception:
            pass
        time.sleep(0.1)


# --- Face recognition ---
def recognize_or_enroll(robot, nao_ip, port):
    from audio_handler import record_audio
    photo_path = capture_photo(nao_ip, port, "/home/nao/face.jpg")

    # Try recognizing
    if photo_path and os.path.exists(photo_path):
        try:
            info = _post_image(FACE_RECO_URL, photo_path, {"tolerance": "0.60"})
            if info.get("ok") and info.get("match"):
                name = info.get("name") or "friend"
                _say(robot, "Welcome back, {}! I recognize you.".format(name))
                return name, True
        except Exception:
            pass

    # New user
    _say(robot, "I don't know you yet. Please tell me your first name.")
    time.sleep(0.3)
    wav = record_audio(nao_ip)
    user = "friend"
    try:
        with open(wav, "rb") as f:
            r = SESSION.post(SERVER_URL, files={"file": f}, data={"username": user}, timeout=DEFAULT_TIMEOUT)
        spoken = (r.json() or {}).get("user_input", "")
        e = extract_name(spoken)
        if e and e.lower() != "friend":
            user = e
    except Exception:
        pass

    if user == "friend":
        _say(robot, "I'll call you friend for now.")
        return user, False

    _say(robot, "Nice to meet you, {}. Let me take your picture.".format(user))
    for _ in range(3):
        time.sleep(0.4)
        p = capture_photo(nao_ip, port, "/home/nao/face.jpg")
        if p and os.path.exists(p):
            try:
                _post_image(FACE_ENROLL_URL, p, {"name": user})
            except Exception:
                pass
    _say(robot, "All set, {}! I'll remember you next time.".format(user))
    return user, False


def _pick_mode(robot, nao_ip, user, default="general"):
    from audio_handler import record_audio
    _say(robot, "Which mode would you like? Say general, study, therapist, broker, or morgan.")
    
    def hear():
        w = record_audio(nao_ip)
        try:
            with open(w, "rb") as f:
                r = SESSION.post(SERVER_URL, files={"file": f}, data={"username": user}, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            d = r.json() or {}
            user_input = d.get("user_input", "")
            
            # Check for mode switch in user input
            detected_mode = _detect_mode_switch(user_input)
            if detected_mode:
                return detected_mode
            
            # Fallback to server mode
            sm = (d.get("active_mode") or "").lower()
            if sm == "chatbot":  # Server sends "chatbot", we use "morgan"
                return "morgan"
            if sm in VALID_FOR_SERVER:
                return sm
                
            return None
        except Exception:
            return None
    
    m = hear() or hear()
    if not m:
        _say(robot, "Using {} mode.".format(default))
        return default
    _say(robot, "{} mode selected.".format(m.capitalize()))
    return m


def _requery_immediate(user, text, new_mode):
    try:
        p = {"username": user, "text": text, "mode": _canon_for_server(new_mode)}
        r = SESSION.post(CHAT_TEXT_URL, json=p, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _mode_enter_actions(robot, posture, tts, behav_mgr, mode):
    if mode == "therapist":
        _say(robot, "Let's sit down and talk comfortably.")
        try:
            posture.goToPosture("Sit", 0.6)
        except Exception:
            pass
    elif mode == "study":
        _say(robot, "Stand with me, let's learn together.")
        try:
            posture.goToPosture("StandInit", 0.6)
        except Exception:
            pass
    elif mode == "broker":
        _say(robot, "Ready to discuss financial matters.")
    elif mode == "morgan":
        _say(robot, "Morgan assist mode active. Ask me anything about Morgan State University.")
    elif mode == "general":
        _say(robot, "General conversation mode active.")


def _perform_mode_switch(robot, posture, tts, behav_mgr, old_mode, new_mode, user, user_text, pool):
    """Handle smooth mode transition"""
    print("[MODE SWITCH] {} -> {}".format(old_mode, new_mode))
    
    # Apply new voice settings
    _apply_mode_voice(tts, new_mode)
    
    # Perform mode-specific actions
    _mode_enter_actions(robot, posture, tts, behav_mgr, new_mode)
    
    # Requery server with new mode
    again = _requery_immediate(user, user_text, new_mode)
    reply = (again or {}).get("reply", "") or "Switched to {} mode successfully.".format(new_mode)
    
    # Speak the response
    _speak_with_gestures(robot, tts, behav_mgr, reply, new_mode, pool)
    
    return new_mode, True


# --- Main loop ---
def enter_chat_mode(robot, nao_ip="127.0.0.1", port=9559):
    motion = ALProxy("ALMotion", nao_ip, port)
    posture = ALProxy("ALRobotPosture", nao_ip, port)
    leds = ALProxy("ALLeds", nao_ip, port)
    tts = ALProxy("ALTextToSpeech", nao_ip, port)
    behav_mgr = ALProxy("ALBehaviorManager", nao_ip, port)

    pool = get_available_gestures(behav_mgr)
    _reset_voice(tts)
    _say(robot, "Scanning for a friend...")

    # Face detection and mood
    try:
        from utils.face_utils import detect_face, detect_mood
        if not detect_face(nao_ip):
            _say(robot, "I don't see anyone yet.")
            return
        mood = detect_mood(nao_ip) or "neutral"
    except Exception:
        mood = "neutral"

    mood_colors = {"happy": "yellow", "neutral": "white", "annoyed": "purple"}
    r, g, b = _color_to_rgb(mood_colors.get(mood, "white"))
    try:
        leds.fadeRGB("FaceLeds", r, g, b, 0.3)
    except Exception:
        pass

    # Face recognition
    name, known = recognize_or_enroll(robot, nao_ip, port)
    if known:
        _say(robot, "Welcome back, {}!".format(name))

    # Mode selection
    mode = _pick_mode(robot, nao_ip, name)
    _apply_mode_voice(tts, mode)
    _mode_enter_actions(robot, posture, tts, behav_mgr, mode)
    
    if mode == "morgan":
        _say(robot, "Hey {}! Morgan mode is on. I can help you with university questions.".format(name))
    else:
        _say(robot, "Hey {}! {} mode is on.".format(name, mode.capitalize()))

    # Initialize memory
    try:
        memory_manager.initialize_user(name)
    except Exception:
        pass

    from audio_handler import record_audio
    
    # ===== MAIN CONVERSATION LOOP =====
    while True:
        _say(robot, "I'm listening.")
        path = record_audio(nao_ip)
        
        if not os.path.exists(path):
            _say(robot, "Repeat please.")
            continue

        # Send audio to server (with proper mode mapping)
        def call():
            with open(path, "rb") as f:
                return SESSION.post(
                    SERVER_URL,
                    files={"file": f},
                    data={"username": name, "mode": _canon_for_server(mode)},
                    timeout=DEFAULT_TIMEOUT
                )

        try:
            res = call_with_processing_announcer(tts, call)
            res.raise_for_status()
            data = res.json()
        except Exception:
            _say(robot, "Connection hiccup.")
            continue

        user_t = data.get("user_input", "") or ""
        reply = data.get("reply", "") or ""
        func = data.get("function_call", {}) or {}
        server_m = (data.get("active_mode") or "").lower() or None
        
        # Map server "chatbot" back to "morgan"
        if server_m == "chatbot":
            server_m = "morgan"
        
        print("[USER INPUT] {}".format(user_t))
        print("[CURRENT MODE] {}".format(mode))
        print("[SERVER MODE] {}".format(server_m))

        # ===== CHECK FOR EXIT INTENT =====
        if _detect_exit_intent(user_t):
            _say(robot, "Understood. Exiting chat mode. Catch you later, {}!".format(name))
            
            # Save memory before exit
            try:
                if user_t:
                    memory_manager.add_user_message(name, user_t)
                memory_manager.add_bot_reply(name, "Exiting chat mode.")
                memory_manager.save_chat_history(name)
            except Exception:
                pass
            
            # Clean exit
            try:
                behav_mgr.stopAllBehaviors()
            except Exception:
                pass
            _reset_voice(tts)
            break

        # ===== CHECK FOR MODE SWITCH =====
        detected_new_mode = _detect_mode_switch(user_t)
        mode_switched = False
        
        if detected_new_mode and detected_new_mode != mode:
            # User explicitly requested mode switch
            mode, mode_switched = _perform_mode_switch(
                robot, posture, tts, behav_mgr, mode, detected_new_mode, name, user_t, pool
            )
        elif data.get("mode_prompt"):
            # Server requested mode clarification
            _say(robot, "Which mode do you want?")
            pick = _pick_mode(robot, nao_ip, name, default=(server_m or mode))
            if pick and pick != mode:
                mode, mode_switched = _perform_mode_switch(
                    robot, posture, tts, behav_mgr, mode, pick, name, user_t, pool
                )
        elif server_m and server_m != mode:
            # Server detected implicit mode switch
            mode, mode_switched = _perform_mode_switch(
                robot, posture, tts, behav_mgr, mode, server_m, name, user_t, pool
            )

        # ===== SAVE TO MEMORY =====
        try:
            if user_t:
                memory_manager.add_user_message(name, user_t)
            memory_manager.add_bot_reply(name, reply if reply else json.dumps(func))
            memory_manager.save_chat_history(name)
        except Exception:
            pass

        # ===== SPEAK RESPONSE =====
        if reply and not mode_switched:
            # Special handling for Morgan mode - more descriptive
            if mode == "morgan" and not reply:
                _say(robot, "I couldn't find information about that in my Morgan database.")
            else:
                _speak_with_gestures(robot, tts, behav_mgr, reply, mode, pool)

        # ===== HANDLE FUNCTION CALLS =====
        f = func.get("name")
        if f == "stand_up":
            _say(robot, "Standing up.")
            try:
                posture.goToPosture("StandInit", 0.6)
            except Exception:
                pass
        elif f == "sit_down":
            _say(robot, "Sitting down.")
            try:
                posture.goToPosture("Sit", 0.6)
            except Exception:
                pass
        elif f == "down":
            try:
                motion.setStiffnesses("Body", 1.0)
                j = ["RHipPitch", "LHipPitch", "RKneePitch", "LKneePitch", "RAnklePitch", "LAnklePitch"]
                a = [0.3, 0.3, 0.5, 0.5, -0.2, -0.2]
                motion.setAngles(j, a, 0.2)
            except Exception:
                pass

    _reset_voice(tts)
