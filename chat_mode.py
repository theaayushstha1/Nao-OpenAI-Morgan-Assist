# chat_mode.py
# -*- coding: utf-8 -*-
from __future__ import print_function
import os, json, random, requests, time, re, threading, qi
from naoqi import ALProxy
from processing_announcer import ProcessingAnnouncer
from utils.exit_detection import detect_exit_intent
from utils.name_utils import extract_name
from utils.face_naoqi import recognize_face_naoqi, learn_new_face_naoqi
from utils.ask_name_utils import ask_name
from utils.speech import (random_phrase, time_of_day_greeting, add_filler,
                          animated_say, animated_expressive_say, expressive_say,
                          format_expressive)
import memory_manager

SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.105")
SERVER_URL = "http://{}:5000/upload".format(SERVER_IP)
CHAT_TEXT_URL = "http://{}:5000/chat_text".format(SERVER_IP)
SESSION = requests.Session()
DEFAULT_TIMEOUT = 30
CHAT_MEMORY_FILE = "/data/home/nao/chat_users.json"

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

def _speak_with_gestures(robot, tts, behav_mgr, text, pool, qi_session=None):
    # Try ALAnimatedSpeech first — it auto-selects gestures
    if qi_session:
        try:
            anim_speech = qi_session.service("ALAnimatedSpeech")
            anim_speech.say(text)
            return
        except Exception:
            pass  # fall back to manual gesture threading

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
    expressive_say(tts, random_phrase("dance_intro"), "excited")
    try:
        if behav_mgr.isBehaviorRunning(dance):
            behav_mgr.stopBehavior(dance)
        behav_mgr.runBehavior(dance)
        time.sleep(1)
        while behav_mgr.isBehaviorRunning(dance):
            time.sleep(0.5)
    except Exception as e:
        print("[Dance error]:", e)
    expressive_say(tts, random_phrase("dance_followup"), "excited")

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
        expressive_say(tts, random_phrase("posture_done"), "neutral")
    except Exception as e:
        print("[Posture error]:", e)

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

    expressive_say(tts, random_phrase("entering_chat"), "warm")
    time.sleep(0.5)

    username = recognize_face_naoqi(qi_session, tts, subscriber_name="ChatFaceReco", timeout=10)
    if not username:
        username = ask_name(tts, nao_ip, SERVER_URL, SESSION, lambda ip: record_audio(ip))
        print("[Name]: {}".format(username))
        learned = learn_new_face_naoqi(qi_session, tts, username, subscriber_name="ChatFaceLearn")
        if learned:
            print("[Face learned]: {}".format(username))
    else:
        expressive_say(tts, time_of_day_greeting(username), "warm")

    try:
        posture.goToPosture("StandInit", 0.6)
        leds.fadeRGB("FaceLeds", 1, 1, 1, 0.3)
    except:
        pass

    expressive_say(tts, "Welcome, {}! I'm ready whenever you are.".format(username), "warm")

    try:
        memory_manager.initialize_user(username)
    except:
        pass

    messages = []

    while True:
        path = record_audio(nao_ip)
        if not os.path.exists(path):
            expressive_say(tts, random_phrase("error_not_heard"), "thinking")
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
            expressive_say(tts, random_phrase("error_connection"), "thinking")
            continue

        user_t = data.get("user_input", "") or ""
        reply = data.get("reply", "") or ""
        func = data.get("function_call", {}) or {}

        print("[USER]: {}".format(user_t))
        print("[REPLY]: {}".format(reply))

        if detect_exit_intent(user_t):
            expressive_say(tts, random_phrase("farewell", name=username), "warm")
            try:
                if user_t:
                    memory_manager.add_user_message(username, user_t)
                memory_manager.add_bot_reply(username, "Exiting chat mode.")
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
        except:
            pass

        if reply:
            _speak_with_gestures(robot, tts, behav_mgr,
                                 format_expressive(add_filler(reply), "warm"),
                                 pool, qi_session=qi_session)
        elif not func:
            expressive_say(tts, random_phrase("error_not_understood"), "thinking")

        f = func.get("name")
        if f == "stand_up":
            change_posture(tts, posture, "stand")
        elif f == "sit_down":
            change_posture(tts, posture, "sit")

    save_chat_session(username, messages)
