# therapist_mode.py
# -*- coding: utf-8 -*-
from __future__ import print_function
from naoqi import ALProxy
import qi
import time
import os
import json
import requests
import re

from audio_handler import record_audio
from utils.camera_capture import capture_photo

NAO_IP = "127.0.0.1"
NAO_PORT = 9559
SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.105")
SERVER_URL = "http://{}:5000".format(SERVER_IP)
USER_DATA_FILE = "/data/home/nao/therapist_users.json"

tts = posture = leds = motion = None
session_obj = None
SESSION = requests.Session()

EXIT_PATTERNS = [
    r"^(goodbye|bye)$",
    r"\b(exit|quit|stop|end|goodbye|bye|close)\b.*\b(chat|mode|conversation|talking|session)\b",
    r"\b(chat|mode|conversation|talking|session)\b.*\b(exit|quit|stop|end|goodbye|bye|close)\b",
    r"^(exit|quit|stop now|end chat|goodbye|bye bye|that's all|that is all)$",
    r"^(i'm done|i am done|we're done|we are done)$",
    r"^(stop talking|stop listening|no more)$",
    r"\b(i (want|need) to (go|leave|stop)|let me (go|leave)|gotta go)\b",
    r"\b(talk to you later|catch you later|see you later)\b",
    r"\b(thanks.*bye|thank you.*bye|thanks.*good(bye)?)\b",
    r"\b(stop.*mode|exit.*mode|leave.*mode|quit.*mode)\b",
    r"\b(go back|return|switch back)\b.*\b(wake|main|menu)\b",
    r"\b(that'?s? (it|all|enough) (for (now|today))?)\b",
    r"\b(end (it|this|conversation|session) (now|here)?)\b",
    r"\b(i('m| am) (good|fine|ok|okay) (now|for now))\b",
]

EXIT_KEYWORDS = [
    "exit", "quit", "stop", "end", "goodbye", "bye", "close",
    "done", "finished", "that's all", "no more", "leave", "go back"
]

def _detect_exit_intent(text):
    if not text:
        return False
    text_lower = text.lower().strip()
    for pattern in EXIT_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            print("[EXIT DETECTED] Pattern match: {}".format(pattern))
            return True
    words = text_lower.split()
    if len(words) <= 3:
        for keyword in EXIT_KEYWORDS:
            if keyword in words:
                print("[EXIT DETECTED] Keyword: {}".format(keyword))
                return True
    return False

def clean_unicode_for_tts(text):
    if not text:
        return ""
    try:
        clean = text.replace(u'\u2019', "'").replace(u'\u2018', "'")\
            .replace(u'\u201c', '"').replace(u'\u201d', '"')\
            .replace(u'\u2013', '-').replace(u'\u2014', '-')\
            .replace(u'\u2026', '...')
        if isinstance(clean, unicode):
            clean = clean.encode('ascii', 'ignore').decode('ascii')
        return str(clean)
    except Exception as e:
        print("[Unicode clean error]:", e)
        return str(text.encode('ascii', 'ignore'))

def safe_print(label, val):
    try:
        if isinstance(val, unicode):
            print("{}: {}".format(label, val.encode('utf-8')))
        else:
            print("{}: {}".format(label, val))
    except Exception as e:
        print("[Print error]:", e)

def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_user_data(data):
    try:
        with open(USER_DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except:
        pass

def get_user_sessions(username):
    data = load_user_data()
    if username in data:
        return data[username].get('sessions', [])
    return []

def add_user_session(username, session_data):
    data = load_user_data()
    if username not in data:
        data[username] = {'name': username, 'sessions': []}
    data[username]['sessions'].append(session_data)
    data[username]['sessions'] = data[username]['sessions'][-10:]
    save_user_data(data)

def recognize_face_naoqi(qi_session, timeout=10):
    global tts
    try:
        memory = qi_session.service("ALMemory")
        face_detection = qi_session.service("ALFaceDetection")
        face_detection.subscribe("TherapistFaceReco")
        tts.say("Look into my eyes. Let me see who you are.")
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
                                    print("[NAO recognized]: {}".format(recognized_name))
                                    tts.say("Welcome back, {}!".format(recognized_name))
                                    break
            except Exception as e:
                print("[Memory read error]:", e)
            time.sleep(0.3)
        face_detection.unsubscribe("TherapistFaceReco")
        if not recognized_name:
            print("[No face recognized after {} seconds]".format(timeout))
        return recognized_name
    except Exception as e:
        print("[NAO face recognition error]:", e)
        try:
            face_detection.unsubscribe("TherapistFaceReco")
        except:
            pass
        return None

def learn_new_face_naoqi(qi_session, name):
    global tts
    try:
        face_detection = qi_session.service("ALFaceDetection")
        memory = qi_session.service("ALMemory")
        tts.say("Look into my eyes. I will learn your face.")
        time.sleep(1)
        try:
            face_detection.subscribe("TherapistFaceLearn")
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
                        print("[Face detected, preparing to learn...]")
                        break
            except:
                pass
            time.sleep(0.3)
        if face_found:
            tts.say("Perfect. Hold still for just a moment.")
            time.sleep(1)
            print("[Learning face as]: {}".format(name))
            face_detection.learnFace(name)
            time.sleep(3)
            tts.say("Got it. I'll remember you next time, {}.".format(name))
            print("[Face learned successfully]: {}".format(name))
            result = True
        else:
            tts.say("I couldn't see your face clearly. Let's continue anyway.")
            print("[Face not detected during learning]")
            result = False
        try:
            face_detection.unsubscribe("TherapistFaceLearn")
        except:
            pass
        return result
    except Exception as e:
        print("[Learn face error]:", e)
        import traceback
        traceback.print_exc()
        try:
            face_detection.unsubscribe("TherapistFaceLearn")
        except:
            pass
        tts.say("I had trouble with that, but let's continue.")
        return False

def extract_name(t):
    if not t:
        return None
    patterns = [
        r"(?:my name is|i am|i'm|call me|this is)\s+([A-Za-z]+)",
        r"^([A-Za-z]+)$",
        r"^([A-Za-z]+)\s*$",
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

def ask_name():
    global tts
    tts.say("I don't think we've met before. What's your name?")
    time.sleep(0.5)
    for attempt in range(2):
        wav = record_audio(NAO_IP)
        if not wav or not os.path.exists(wav):
            if attempt == 0:
                tts.say("I didn't catch that. Could you say your name again?")
            continue
        try:
            with open(wav, 'rb') as f:
                r = SESSION.post(SERVER_URL + "/upload", files={"file": f}, data={"username": "guest"}, timeout=30)
            spoken = (r.json() or {}).get("user_input", "")
            print("[Heard]: '{}'".format(spoken))
            name = extract_name(spoken)
            if name and name.lower() != "friend":
                print("[Extracted name]: {}".format(name))
                return name
            else:
                if attempt == 0:
                    tts.say("I didn't catch your name. Could you say it one more time?")
                    time.sleep(0.3)
        except Exception as e:
            print("[Name capture error]:", e)
            if attempt == 0:
                tts.say("Sorry, could you repeat your name?")
    print("[Using fallback name: Guest]")
    return "Guest"

def detect_mood_from_speech(user_text):
    if not user_text:
        return "neutral"
    text_lower = user_text.lower()
    sad_words = ["sad", "depressed", "unhappy", "lonely", "crying", "hopeless", "hurt", "pain", "miss", "lost", "tired"]
    happy_words = ["happy", "excited", "great", "amazing", "wonderful", "love", "joy", "good", "awesome", "fantastic", "smile"]
    angry_words = ["angry", "mad", "furious", "frustrated", "annoyed", "hate", "irritated", "upset"]
    stressed_words = ["stressed", "overwhelmed", "pressure", "anxious", "worried", "nervous"]
    calm_words = ["calm", "relaxed", "peaceful", "serene", "content"]
    sad_count = sum(1 for word in sad_words if word in text_lower)
    happy_count = sum(1 for word in happy_words if word in text_lower)
    angry_count = sum(1 for word in angry_words if word in text_lower)
    stressed_count = sum(1 for word in stressed_words if word in text_lower)
    calm_count = sum(1 for word in calm_words if word in text_lower)
    mood_scores = {"happy": happy_count, "sad": sad_count, "angry": angry_count, "stressed": stressed_count, "calm": calm_count}
    mood, max_score = "neutral", 0
    for m, score in mood_scores.items():
        if score > max_score:
            mood, max_score = m, score
    return mood

def mood_personalized_response(mood, username):
    global tts, leds
    try:
        if mood == "sad":
            tts.say("I sense you're feeling sad, {}. I'm here for you.".format(username))
            leds.fadeRGB("FaceLeds", 0.5, 0.5, 1, 0.5)
        elif mood == "happy":
            tts.say("You seem happy today, {}! That's great.".format(username))
            leds.fadeRGB("FaceLeds", 0, 1, 0, 0.5)
        elif mood == "angry":
            tts.say("I sense some frustration, {}. Let's talk about it.".format(username))
            leds.fadeRGB("FaceLeds", 1, 0.5, 0, 0.5)
        elif mood == "stressed":
            tts.say("You seem stressed, {}. Take a deep breath.".format(username))
            leds.fadeRGB("FaceLeds", 1, 0, 0, 0.5)
        elif mood == "calm":
            tts.say("You seem calm today, {}.".format(username))
            leds.fadeRGB("FaceLeds", 0, 1, 1, 0.5)
    except Exception as e:
        print("[Mood response error]:", e)

def therapy_loop(username, mood):
    global tts, leds
    session_data = {'timestamp': time.time(), 'mood': mood, 'messages': []}
    history = []
    previous_sessions = get_user_sessions(username)
    if previous_sessions:
        tts.say("Good to see you again, {}. I remember our last talk.".format(username))
    try:
        leds.fadeRGB("FaceLeds", 0.0, 0.8, 1.0, 0.4)
    except:
        pass
    if mood != "neutral":
        mood_personalized_response(mood, username)
    tts.say("How are you feeling today?")
    for turn in range(1, 1000):
        print("[THERAPIST] Turn {}".format(turn))
        wav = record_audio(NAO_IP)
        print("[Audio recorded]", wav)
        if not wav or not os.path.exists(wav):
            tts.say("I didn't hear you. Could you say that again?")
            continue
        try:
            with open(wav, 'rb') as f:
                files = {'audio': f}
                data = {'username': username, 'mood': mood, 'history': json.dumps(history), 'previous_sessions': json.dumps(previous_sessions[-3:])}
                print("[Sending audio to server...]")
                resp = requests.post(SERVER_URL + "/therapist_chat", files=files, data=data, timeout=45)
                if resp.status_code != 200:
                    print("[Server error]:", resp.status_code)
                    tts.say("Sorry, I'm having trouble understanding. Try again.")
                    continue
                result = resp.json()
                user_input = result.get('user_input', '')
                reply = result.get('reply', '')
                safe_print("[USER]", user_input)
                safe_print("[REPLY]", reply)
                if _detect_exit_intent(user_input):
                    tts.say("Thank you for talking with me today, {}. Take care.".format(username))
                    break
                speech_mood = detect_mood_from_speech(user_input)
                if speech_mood != "neutral" and speech_mood != mood:
                    mood = speech_mood
                    print("[Mood updated]: {}".format(mood))
                    mood_personalized_response(mood, username)
                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": reply})
                session_data['messages'].append({'user': user_input, 'assistant': reply})
                if reply:
                    try:
                        clean_reply = clean_unicode_for_tts(reply)
                        tts.say(clean_reply)
                    except Exception as e:
                        print("[TTS ERROR]:", e)
                        tts.say("I'm listening.")
                else:
                    tts.say("Tell me more.")
        except requests.Timeout:
            print("[TIMEOUT] Server took too long.")
            tts.say("That took too long. Let's try again.")
            continue
        except Exception as e:
            print("[ERROR]:", e)
            import traceback
            traceback.print_exc()
            tts.say("Let me try that again.")
            continue
    add_user_session(username, session_data)
    try:
        leds.fadeRGB("FaceLeds", 1.0, 1.0, 1.0, 0.3)
    except:
        pass

def start_therapist_mode():
    global tts, posture, leds, motion, session_obj
    session_obj = qi.Session()
    try:
        session_obj.connect("tcp://127.0.0.1:9559")
        tts = session_obj.service("ALTextToSpeech")
        posture = session_obj.service("ALRobotPosture")
        leds = session_obj.service("ALLeds")
        motion = session_obj.service("ALMotion")
        print("[Connected to NAO services]")
    except Exception as e:
        print("[THERAPIST] Could not connect to qi session:", e)
        return
    print("[THERAPIST] Starting therapist mode...")
    tts.say("Starting therapist mode.")
    time.sleep(0.5)
    print("[Attempting face recognition...]")
    username = recognize_face_naoqi(session_obj, timeout=10)
    if not username:
        print("[No face recognized, asking for name...]")
        username = ask_name()
        print("[Name captured]: {}".format(username))
        print("[Starting face learning for {}...]".format(username))
        learned = learn_new_face_naoqi(session_obj, username)
        if learned:
            print("[Face successfully learned as {}]".format(username))
        else:
            print("[Face learning failed, continuing with name only]")
    mood = "neutral"
    try:
        tts.say("Let's get comfortable.")
        posture.goToPosture("Sit", 0.5)
    except:
        pass
    therapy_loop(username, mood)
    print("[THERAPIST] Session complete")

if __name__ == "__main__":
    start_therapist_mode()
