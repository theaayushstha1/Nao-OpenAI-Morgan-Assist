# chat_mode.py
# -*- coding: utf-8 -*-
from __future__ import print_function
import os, json, random, requests, time, re, threading, qi
from naoqi import ALProxy
from utils.camera_capture import capture_photo
from processing_announcer import ProcessingAnnouncer
import memory_manager

SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.105")
SERVER_URL = "http://{}:5000/upload".format(SERVER_IP)
CHAT_TEXT_URL = "http://{}:5000/chat_text".format(SERVER_IP)
SESSION = requests.Session()
DEFAULT_TIMEOUT = 30
CHAT_MEMORY_FILE = "/data/home/nao/chat_users.json"

EXIT_PATTERNS = [
    r"^(goodbye|bye)$",
    r"\b(exit|quit|stop|end|goodbye|bye|close)\b.*\b(chat|mode|conversation|talking|session)\b",
    r"\b(chat|mode|conversation|talking|session)\b.*\b(exit|quit|stop|end|goodbye|bye|close)\b",
    r"^(exit|quit|stop now|end chat|bye bye|that's all|that is all)$",
    r"^(i'm done|i am done|we're done|we are done)$",
    r"\b(i (want|need) to (go|leave|stop)|let me (go|leave)|gotta go)\b",
    r"\b(talk to you later|catch you later|see you later)\b",
    r"\b(thanks.*bye|thank you.*bye|thanks.*good(bye)?)\b",
    r"\b(stop.*mode|exit.*mode|leave.*mode|quit.*mode)\b",
    r"\b(go back|return|switch back)\b.*\b(wake|main|menu)\b",
]

EXIT_KEYWORDS = ["exit", "quit", "stop", "end", "goodbye", "bye", "close", "done", "finished", "that's all", "no more", "leave", "go back"]
DANCE_KEYWORDS = ["dance", "dancing", "move", "groove", "boogie", "shake", "bust a move"]
FOLLOW_KEYWORDS = ["follow me", "follow", "come with me", "walk with me", "come along"]

EMOTION_KEYWORDS = {
    "happy": ["show me happy", "be happy", "happy emotion", "excited", "show excitement"],
    "sad": ["show me sad", "be sad", "sad emotion"],
    "angry": ["show me angry", "be angry", "angry emotion", "frustrated"],
    "laugh": ["laugh", "tell a joke", "make me laugh", "show me laugh"],
    "kungfu": ["kung fu", "karate", "martial arts", "fight", "show me kung fu"],
}

POSTURE_KEYWORDS = {
    "stand": ["stand", "stand up", "get up"],
    "sit": ["sit", "sit down"],
    "crouch": ["crouch", "crouch down", "squat"],
    "lyingbelly": ["lie down", "lay down", "lie on belly"],
    "lyingback": ["lie on back", "lay on back"],
}

def _detect_exit_intent(text):
    if not text:
        return False
    text_lower = text.lower().strip()
    for pattern in EXIT_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            print("[EXIT DETECTED] Pattern: {}".format(pattern))
            return True
    words = text_lower.split()
    if len(words) <= 3:
        for keyword in EXIT_KEYWORDS:
            if keyword in words:
                print("[EXIT DETECTED] Keyword: {}".format(keyword))
                return True
    return False

def _detect_dance_intent(text):
    if not text:
        return False
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in DANCE_KEYWORDS)

def _detect_follow_intent(text):
    if not text:
        return False
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in FOLLOW_KEYWORDS)

def _detect_emotion_intent(text):
    if not text:
        return None
    text_lower = text.lower()
    for emotion, keywords in EMOTION_KEYWORDS.items():
        if any(keyword in text_lower for keyword in keywords):
            return emotion
    return None

def _detect_posture_intent(text):
    if not text:
        return None
    text_lower = text.lower()
    for posture, keywords in POSTURE_KEYWORDS.items():
        if any(keyword in text_lower for keyword in keywords):
            return posture
    return None

def _apply_mode_voice(tts):
    try:
        tts.setParameter("speed", 100)
        tts.setParameter("pitchShift", 0.95)
        tts.setVolume(1.0)
    except:
        pass

def _stop_tts(tts):
    try:
        if hasattr(tts, "stopAll"):
            tts.stopAll()
    except:
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
            except:
                s = unicode_type(t)
        elif isinstance(t, unicode_type):
            s = t
        else:
            s = unicode_type(t)
        s = u''.join(c if 32 <= ord(c) <= 126 else u' ' for c in s).strip()
        return s.encode('utf-8') if s else "Okay."
    except:
        return "Okay."

def _say(robot, text):
    try:
        robot.say(_to_sayable(text))
    except Exception as e:
        print("[WARN] say:", e)

def extract_name(t):
    if not t:
        return None
    patterns = [
        r"(?:my name is|i am|i'm|call me|this is)\s+([A-Za-z]+)",
        r"^([A-Za-z]+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, t.strip(), re.IGNORECASE)
        if m:
            name = m.group(1).capitalize()
            if name.lower() not in ["the", "a", "an", "my", "is", "am"]:
                return name
    words = t.strip().split()
    if words:
        first_word = words[0].capitalize()
        if len(first_word) > 1 and first_word.isalpha():
            return first_word
    return None

def get_available_gestures(behav_mgr):
    try:
        allb = behav_mgr.getInstalledBehaviors()
    except:
        return []
    built = [b for b in allb if "animations/Stand/Gestures/" in b]
    priority_keys = ["Think", "Explain", "Yes", "No", "Point", "This", "Shrug", "Hey", "Excited"]
    pri = [g for g in built if any(k in g for k in priority_keys)]
    if len(pri) < 10:
        remaining = min(len(built), 10 - len(pri))
        if remaining > 0:
            pri += random.sample(built, remaining)
    return sorted(list(set(pri)))

def get_dance_behaviors(behav_mgr):
    try:
        allb = behav_mgr.getInstalledBehaviors()
    except:
        return []
    dance_names = [
        "taichi-dance-free",
        "animations/Stand/Waiting/FunnyDancer_1",
        "animations/Stand/Waiting/Headbang_1",
        "animations/Stand/Waiting/AirGuitar_1",
        "animations/Stand/Waiting/Robot_1",
        "animations/Stand/Waiting/Zombie_1",
        "animations/Stand/Waiting/Monster_1",
        "animations/Stand/Waiting/Waddle_1",
        "animations/Stand/Waiting/Waddle_2",
    ]
    return [b for b in dance_names if b in allb]

def get_emotion_behavior(behav_mgr, emotion):
    try:
        allb = behav_mgr.getInstalledBehaviors()
    except:
        return None
    emotion_map = {
        "happy": ["animations/Stand/Emotions/Positive/Happy_1", "animations/Stand/Emotions/Positive/Excited_1", "animations/Stand/Emotions/Positive/Winner_1"],
        "sad": ["animations/Stand/Emotions/Negative/Sad_1", "animations/Stand/Emotions/Negative/Disappointed_1"],
        "angry": ["animations/Stand/Emotions/Negative/Angry_1", "animations/Stand/Emotions/Negative/Frustrated_1"],
        "laugh": ["animations/Stand/Emotions/Positive/Laugh_1", "animations/Stand/Emotions/Positive/Laugh_2"],
        "kungfu": ["animations/Stand/Waiting/KungFu_1"],
    }
    candidates = emotion_map.get(emotion, [])
    available = [b for b in candidates if b in allb]
    return random.choice(available) if available else None

def _split_sentences(t):
    return [p.strip() for p in re.split(r'(?<=[.!?]) +', t) if p.strip()]

def _loop_gestures(behav_mgr, pool, stop):
    last = None
    while not stop.is_set() and pool:
        try:
            candidates = [x for x in pool if x != last] or pool
            g = random.choice(candidates)
            last = g
            if behav_mgr.isBehaviorRunning(g):
                behav_mgr.stopBehavior(g)
            behav_mgr.runBehavior(g)
            time.sleep(random.uniform(0.8, 1.2))
        except Exception as e:
            print("[Gesture error]", e)
            time.sleep(1)

def _speak_with_gestures(robot, tts, behav_mgr, text, pool):
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
        except:
            pass
        time.sleep(0.1)

def perform_dance(tts, behav_mgr):
    dances = get_dance_behaviors(behav_mgr)
    if not dances:
        tts.say("I don't have any dance moves installed.")
        return
    dance = random.choice(dances)
    print("[Dancing]: {}".format(dance))
    tts.say("Watch this!")
    try:
        if behav_mgr.isBehaviorRunning(dance):
            behav_mgr.stopBehavior(dance)
        behav_mgr.runBehavior(dance)
        time.sleep(1)
        while behav_mgr.isBehaviorRunning(dance):
            time.sleep(0.5)
    except Exception as e:
        print("[Dance error]:", e)
    tts.say("How was that?")

def perform_emotion(tts, behav_mgr, emotion):
    behavior = get_emotion_behavior(behav_mgr, emotion)
    if not behavior:
        tts.say("I can't show that emotion right now.")
        return
    print("[Emotion]: {} -> {}".format(emotion, behavior))
    try:
        if behav_mgr.isBehaviorRunning(behavior):
            behav_mgr.stopBehavior(behavior)
        behav_mgr.runBehavior(behavior)
        while behav_mgr.isBehaviorRunning(behavior):
            time.sleep(0.3)
    except Exception as e:
        print("[Emotion error]:", e)

def perform_follow_me(tts, behav_mgr):
    tts.say("Okay, let's go! I'll follow you.")
    try:
        allb = behav_mgr.getInstalledBehaviors()
        follow_behavior = None
        for b in allb:
            if "follow" in b.lower():
                follow_behavior = b
                break
        if follow_behavior:
            behav_mgr.runBehavior(follow_behavior)
        else:
            tts.say("Follow me behavior not found.")
    except Exception as e:
        print("[Follow error]:", e)
        tts.say("I had trouble following.")

def change_posture(tts, posture, target_posture):
    posture_map = {
        "stand": "StandInit",
        "sit": "Sit",
        "crouch": "Crouch",
        "lyingbelly": "LyingBelly",
        "lyingback": "LyingBack",
    }
    nao_posture = posture_map.get(target_posture, "StandInit")
    print("[Changing posture]: {}".format(nao_posture))
    try:
        posture.goToPosture(nao_posture, 0.6)
        tts.say("Done.")
    except Exception as e:
        print("[Posture error]:", e)

def recognize_face_naoqi(qi_session, tts, timeout=10):
    try:
        memory = qi_session.service("ALMemory")
        face_detection = qi_session.service("ALFaceDetection")
        face_detection.subscribe("ChatFaceReco")
        tts.say("Look into my eyes.")
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
                                if face_name and isinstance(face_name, (str, unicode)) and str(face_name).strip() != "":
                                    recognized_name = str(face_name)
                                    print("[Recognized]: {}".format(recognized_name))
                                    tts.say("Hey {}! Good to see you!".format(recognized_name))
                                    break
            except Exception as e:
                print("[Memory read error]:", e)
            time.sleep(0.3)
        face_detection.unsubscribe("ChatFaceReco")
        return recognized_name
    except Exception as e:
        print("[Face recognition error]:", e)
        try:
            face_detection.unsubscribe("ChatFaceReco")
        except:
            pass
        return None

def learn_new_face_naoqi(qi_session, tts, name):
    try:
        face_detection = qi_session.service("ALFaceDetection")
        memory = qi_session.service("ALMemory")
        tts.say("Look into my eyes so I can remember you.")
        time.sleep(1)
        try:
            face_detection.subscribe("ChatFaceLearn")
        except:
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
            except:
                pass
            time.sleep(0.3)
        if face_found:
            tts.say("Perfect. Hold still.")
            time.sleep(1)
            print("[Learning face as]: {}".format(name))
            face_detection.learnFace(name)
            time.sleep(3)
            tts.say("Got it, {}!".format(name))
            result = True
        else:
            tts.say("Couldn't see you clearly. Let's continue anyway.")
            result = False
        try:
            face_detection.unsubscribe("ChatFaceLearn")
        except:
            pass
        return result
    except Exception as e:
        print("[Learn face error]:", e)
        try:
            face_detection.unsubscribe("ChatFaceLearn")
        except:
            pass
        return False

def ask_name(tts, nao_ip):
    from audio_handler import record_audio
    tts.say("What's your name?")
    time.sleep(0.5)
    for attempt in range(2):
        wav = record_audio(nao_ip)
        if not wav or not os.path.exists(wav):
            if attempt == 0:
                tts.say("Didn't catch that. Say your name again?")
            continue
        try:
            with open(wav, 'rb') as f:
                r = SESSION.post(SERVER_URL, files={"file": f}, data={"username": "guest"}, timeout=30)
            spoken = (r.json() or {}).get("user_input", "")
            print("[Heard]: '{}'".format(spoken))
            name = extract_name(spoken)
            if name:
                print("[Extracted name]: {}".format(name))
                return name
            elif attempt == 0:
                tts.say("Didn't catch your name. One more time?")
                time.sleep(0.3)
        except Exception as e:
            print("[Name error]:", e)
            if attempt == 0:
                tts.say("Sorry, repeat your name?")
    return "Guest"

def save_chat_session(username, messages):
    try:
        data = {}
        if os.path.exists(CHAT_MEMORY_FILE):
            with open(CHAT_MEMORY_FILE, 'r') as f:
                data = json.load(f)
        if username not in data:
            data[username] = {'sessions': []}
        data[username]['sessions'].append({'timestamp': time.time(), 'messages': messages})
        data[username]['sessions'] = data[username]['sessions'][-10:]
        with open(CHAT_MEMORY_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except:
        pass

def enter_chat_mode(robot, nao_ip="127.0.0.1", port=9559):
    from audio_handler import record_audio
    
    qi_session = qi.Session()
    try:
        qi_session.connect("tcp://127.0.0.1:9559")
        motion = qi_session.service("ALMotion")
        posture = qi_session.service("ALRobotPosture")
        leds = qi_session.service("ALLeds")
        tts = qi_session.service("ALTextToSpeech")
        behav_mgr = qi_session.service("ALBehaviorManager")
        print("[Connected to NAO services]")
    except Exception as e:
        print("[Connection error]:", e)
        return

    pool = get_available_gestures(behav_mgr)
    _apply_mode_voice(tts)
    
    tts.say("Starting chat mode.")
    time.sleep(0.5)
    
    username = recognize_face_naoqi(qi_session, tts, timeout=10)
    if not username:
        username = ask_name(tts, nao_ip)
        print("[Name]: {}".format(username))
        learned = learn_new_face_naoqi(qi_session, tts, username)
        if learned:
            print("[Face learned]: {}".format(username))
    
    try:
        posture.goToPosture("StandInit", 0.6)
        leds.fadeRGB("FaceLeds", 1, 1, 1, 0.3)
    except:
        pass
    
    tts.say("Hey {}! Ready to chat?".format(username))
    
    try:
        memory_manager.initialize_user(username)
    except:
        pass
    
    messages = []
    
    while True:
        path = record_audio(nao_ip)
        if not os.path.exists(path):
            tts.say("Didn't hear you. Try again?")
            continue
        
        def call():
            with open(path, "rb") as f:
                return SESSION.post(SERVER_URL, files={"file": f}, data={"username": username, "mode": "general"}, timeout=DEFAULT_TIMEOUT)
        
        try:
            res = call_with_processing_announcer(tts, call)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print("[Server error]:", e)
            tts.say("Connection hiccup. Try again.")
            continue
        
        user_t = data.get("user_input", "") or ""
        reply = data.get("reply", "") or ""
        func = data.get("function_call", {}) or {}
        
        print("[USER]: {}".format(user_t))
        print("[REPLY]: {}".format(reply))
        
        if _detect_exit_intent(user_t):
            tts.say("See you later, {}!".format(username))
            try:
                if user_t:
                    memory_manager.add_user_message(username, user_t)
                memory_manager.add_bot_reply(username, "Exiting chat mode.")
                memory_manager.save_chat_history(username)
            except:
                pass
            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            save_chat_session(username, messages)
            break
        
        if _detect_dance_intent(user_t):
            perform_dance(tts, behav_mgr)
            messages.append({'user': user_t, 'bot': "Performed dance"})
            continue
        
        emotion = _detect_emotion_intent(user_t)
        if emotion:
            perform_emotion(tts, behav_mgr, emotion)
            messages.append({'user': user_t, 'bot': "Showed {} emotion".format(emotion)})
            continue
        
        if _detect_follow_intent(user_t):
            perform_follow_me(tts, behav_mgr)
            messages.append({'user': user_t, 'bot': "Following user"})
            continue
        
        posture_detected = _detect_posture_intent(user_t)
        if posture_detected:
            change_posture(tts, posture, posture_detected)
            messages.append({'user': user_t, 'bot': "Changed posture to {}".format(posture_detected)})
            continue
        
        messages.append({'user': user_t, 'bot': reply})
        
        try:
            if user_t:
                memory_manager.add_user_message(username, user_t)
            memory_manager.add_bot_reply(username, reply if reply else json.dumps(func))
            memory_manager.save_chat_history(username)
        except:
            pass
        
        if reply:
            _speak_with_gestures(robot, tts, behav_mgr, reply, pool)
        elif not func:
            tts.say("Hmm, not sure what to say.")
        
        f = func.get("name")
        if f == "stand_up":
            change_posture(tts, posture, "stand")
        elif f == "sit_down":
            change_posture(tts, posture, "sit")
    
    save_chat_session(username, messages)
