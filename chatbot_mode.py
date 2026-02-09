# chatbot_mode.py
# -*- coding: utf-8 -*-
from __future__ import print_function
from naoqi import ALProxy
import os, json, requests, time, random, re, threading, qi

from utils.exit_detection import detect_exit_intent
from utils.name_utils import extract_name
from utils.face_naoqi import recognize_face_naoqi, learn_new_face_naoqi
from utils.ask_name_utils import ask_name
from utils.speech import (random_phrase, time_of_day_greeting, add_filler,
                          animated_expressive_say, expressive_say)

SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.105")
SERVER_URL = "http://{}:5000/upload".format(SERVER_IP)
TIMEOUT = 20
CHATBOT_MEMORY_FILE = "/data/home/nao/chatbot_users.json"

SESSION = requests.Session()

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

def chatbot_mode(nao_ip="127.0.0.1", nao_port=9559):
    from audio_handler import record_audio

    print("Morgan Chatbot Mode (with RAG/Pinecone + Face reco/memory)")
    qi_session = qi.Session()
    qi_session.connect("tcp://127.0.0.1:9559")
    tts = ALProxy("ALTextToSpeech", nao_ip, nao_port)
    try:
        behav_mgr = ALProxy("ALBehaviorManager", nao_ip, nao_port)
        pool = get_available_gestures(behav_mgr)
    except Exception:
        behav_mgr, pool = None, []

    username = recognize_face_naoqi(qi_session, tts, subscriber_name="ChatbotFaceReco", timeout=10)
    if not username:
        username = ask_name(tts, nao_ip, SERVER_URL, SESSION, lambda ip: record_audio(ip))
        learned = learn_new_face_naoqi(qi_session, tts, username, subscriber_name="ChatbotFaceLearn")
        if learned:
            pass
    else:
        expressive_say(tts, "{} {}".format(
            time_of_day_greeting(username),
            random_phrase("greeting_known", name=username)), "warm")

    try:
        posture = ALProxy("ALRobotPosture", nao_ip, nao_port)
        posture.goToPosture("StandInit", 0.6)
    except:
        pass

    expressive_say(tts, random_phrase("entering_chatbot"), "warm")

    messages = []
    prior_sessions = load_user_sessions(username)

    while True:
        audio = record_audio(nao_ip)
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
            _safe_say(tts, behav_mgr, random_phrase("error_connection"), pool)
            continue

        data = res.json() or {}
        user_input = (data.get("user_input") or "").strip()
        reply = (data.get("reply") or "").strip()

        if detect_exit_intent(user_input):
            _safe_say(tts, behav_mgr, random_phrase("farewell", name=username), pool)
            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            break

        messages.append({'user': user_input, 'bot': reply})

        if reply:
            animated_expressive_say(qi_session, add_filler(reply), "warm", fallback_tts=tts)
        else:
            _safe_say(tts, behav_mgr, random_phrase("error_not_understood"), pool)

        try:
            behav_mgr.stopAllBehaviors()
        except:
            pass

    save_user_session(username, {
        "timestamp": time.time(),
        "messages": messages,
        "previous_sessions": prior_sessions[-5:]
    })
