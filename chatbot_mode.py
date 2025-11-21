# chatbot_mode.py
# -*- coding: utf-8 -*-
from naoqi import ALProxy
import os, json, requests, time, random, re, threading, qi

SERVER_URL = "http://172.20.95.105:5000/upload"
TIMEOUT = 20
CHATBOT_MEMORY_FILE = "/data/home/nao/chatbot_users.json"

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

def _detect_exit_intent(text):
    if not text:
        return False
    text_lower = text.lower().strip()
    for pattern in EXIT_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    words = text_lower.split()
    if len(words) <= 3:
        for keyword in EXIT_KEYWORDS:
            if keyword in words:
                return True
    return False

def get_available_gestures(behav_mgr):
    try:
        allb = behav_mgr.getInstalledBehaviors()
    except:
        return []
    built = [b for b in allb if "animations/Stand/Gestures/" in b]
    key = ["ShowSky", "Explain", "This", "YouKnowWhat", "Point", "Think", "Yes", "No", "ComeOn", "Shrug", "Hey", "Excited"]
    priority = [g for g in built if any(k in g for k in key)]
    if len(priority) < 10:
        priority += random.sample(built, min(len(built), 10 - len(priority)))
    pool = sorted(list(set(priority)))
    return pool

def _split_sentences(t):
    return [p.strip() for p in re.split(r'(?<=[.!?]) +', t) if p.strip()]

def _loop_gestures(behav_mgr, pool, stop_flag):
    last = None
    while not stop_flag.is_set() and pool:
        try:
            g = random.choice([x for x in pool if x != last] or pool)
            last = g
            if behav_mgr.isBehaviorRunning(g):
                behav_mgr.stopBehavior(g)
            behav_mgr.runBehavior(g)
            time.sleep(random.uniform(0.8, 1.5))
        except Exception:
            time.sleep(0.5)

def _safe_say(tts, behav_mgr, text, pool):
    try:
        parts = _split_sentences(text) or [text]
        for p in parts:
            stop = threading.Event()
            t = threading.Thread(target=_loop_gestures, args=(behav_mgr, pool, stop))
            t.daemon = True
            t.start()
            time.sleep(0.05)
            tts.say(p)
            stop.set()
            t.join(timeout=0.1)
            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            time.sleep(0.1)
    except:
        try:
            tts.say("Okay.")
        except:
            pass

def with_processing_announcer(tts, func):
    from processing_announcer import ProcessingAnnouncer
    ann = ProcessingAnnouncer(tts_say=tts.say, stop_all=getattr(tts, "stopAll", None),
                              first_delay=2.0, interval=3.0, max_utterances=2)
    try:
        ann.start()
        return func()
    finally:
        try:
            ann.stop(interrupt=True)
        except:
            pass

def recognize_face_naoqi(qi_session, tts, timeout=10):
    try:
        memory = qi_session.service("ALMemory")
        face_detection = qi_session.service("ALFaceDetection")
        face_detection.subscribe("ChatbotFaceReco")
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
                                    tts.say("Hello {}! Nice to see you.".format(recognized_name))
                                    break
            except Exception:
                pass
            time.sleep(0.3)
        face_detection.unsubscribe("ChatbotFaceReco")
        return recognized_name
    except Exception:
        try:
            face_detection.unsubscribe("ChatbotFaceReco")
        except:
            pass
        return None

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

def learn_new_face_naoqi(qi_session, tts, name):
    try:
        face_detection = qi_session.service("ALFaceDetection")
        memory = qi_session.service("ALMemory")
        tts.say("Look into my eyes so I can remember you.")
        time.sleep(1)
        try:
            face_detection.subscribe("ChatbotFaceLearn")
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
            tts.say("Perfect. Hold still please.")
            time.sleep(1)
            face_detection.learnFace(name)
            time.sleep(3)
            tts.say("Got it, {}!".format(name))
            result = True
        else:
            tts.say("Couldn't see you clearly. Let's continue anyway.")
            result = False
        try:
            face_detection.unsubscribe("ChatbotFaceLearn")
        except:
            pass
        return result
    except Exception:
        try:
            face_detection.unsubscribe("ChatbotFaceLearn")
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
                tts.say("Didn't catch that. Please say your name.")
            continue
        try:
            import requests
            SERVER_URL = "http://172.20.95.105:5000/upload"
            with open(wav, 'rb') as f:
                r = requests.post(SERVER_URL, files={"file": f}, data={"username": "guest"}, timeout=20)
            spoken = (r.json() or {}).get("user_input", "")
            name = extract_name(spoken)
            if name:
                return name
            elif attempt == 0:
                tts.say("Didn't catch your name. Please repeat.")
                time.sleep(0.3)
        except Exception:
            if attempt == 0:
                tts.say("Sorry, could you repeat your name?")
    return "Guest"

def load_user_sessions(username):
    try:
        if os.path.exists(CHATBOT_MEMORY_FILE):
            with open(CHATBOT_MEMORY_FILE, 'r') as f:
                data = json.load(f)
            if username in data:
                return data[username].get("sessions", [])
    except:
        pass
    return []

def save_user_session(username, session_data):
    try:
        data = {}
        if os.path.exists(CHATBOT_MEMORY_FILE):
            with open(CHATBOT_MEMORY_FILE, 'r') as f:
                data = json.load(f)
        if username not in data:
            data[username] = {'sessions': []}
        data[username]['sessions'].append(session_data)
        data[username]['sessions'] = data[username]['sessions'][-10:]
        with open(CHATBOT_MEMORY_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except:
        pass

def chatbot_mode(record_audio_func, nao_ip="127.0.0.1", nao_port=9559):
    print("🧠 Morgan Chatbot Mode (with RAG/Pinecone + Face reco/memory)")
    qi_session = qi.Session()
    qi_session.connect("tcp://127.0.0.1:9559")
    tts = ALProxy("ALTextToSpeech", nao_ip, nao_port)
    try:
        behav_mgr = ALProxy("ALBehaviorManager", nao_ip, nao_port)
        pool = get_available_gestures(behav_mgr)
    except Exception:
        behav_mgr, pool = None, []

    username = recognize_face_naoqi(qi_session, tts, timeout=10)
    if not username:
        username = ask_name(tts, nao_ip)
        learned = learn_new_face_naoqi(qi_session, tts, username)
        if learned:
            pass

    try:
        posture = ALProxy("ALRobotPosture", nao_ip, nao_port)
        posture.goToPosture("StandInit", 0.6)
    except:
        pass

    tts.say("Hello {}! You may ask any Morgan question.".format(username))

    messages = []
    prior_sessions = load_user_sessions(username)

    while True:
        audio = record_audio_func()
        def call():
            with open(audio, 'rb') as f:
                return requests.post(
                    SERVER_URL,
                    files={'file': f},
                    data={'username': username, 'mode': 'chatbot'},
                    timeout=TIMEOUT
                )
        res = with_processing_announcer(tts, call)
        if res.status_code != 200:
            _safe_say(tts, behav_mgr, "Sorry, I couldn't understand that.", pool)
            continue

        data = res.json() or {}
        user_input = (data.get("user_input") or "").strip()
        reply = (data.get("reply") or "").strip()

        if _detect_exit_intent(user_input):
            _safe_say(tts, behav_mgr, "Exiting chatbot mode. See you later!", pool)
            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            break

        messages.append({'user': user_input, 'bot': reply})

        if reply:
            _safe_say(tts, behav_mgr, reply, pool)
        else:
            _safe_say(tts, behav_mgr, "I couldn't find anything useful.", pool)

        try:
            behav_mgr.stopAllBehaviors()
        except:
            pass

    save_user_session(username, {
        "timestamp": time.time(),
        "messages": messages,
        "previous_sessions": prior_sessions[-5:]
    })
