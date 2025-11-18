# therapist_mode.py
# -*- coding: utf-8 -*-
"""
Therapist Mode - FULLY FEATURED WITH FACE RECOGNITION, REGISTRATION, MOOD DETECTION,
SESSION MEMORY, UNICODE FIX, SAFE PRINTS, FORCED FACE REGISTER AFTER TIMEOUT,
AND MOOD-PERSONALIZED RESPONSES WITH DANCE
"""
from __future__ import print_function
from naoqi import ALProxy
import qi
import time
import os
import json
import requests
import re

from audio_handler import record_audio

NAO_IP = os.environ.get("NAO_IP", "172.20.95.100")
NAO_PORT = int(os.environ.get("NAO_PORT", "9559"))
SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.106")
SERVER_URL = "http://{}:5000".format(SERVER_IP)

USER_DATA_FILE = "/data/home/nao/therapist_users.json"

# Global proxies, set after qi session connection
tts = posture = leds = motion = None
session = None


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
        print("[Unicode clean error]", e)
        return str(text.encode('ascii', 'ignore'))


def safe_print(text_label, text_value):
    try:
        if isinstance(text_value, unicode):
            print("{}: {}".format(text_label, text_value.encode('utf-8')))
        else:
            print("{}: {}".format(text_label, text_value))
    except Exception as e:
        print("[Print error]:", e)
        print("{}: (could not print Unicode text)".format(text_label))


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


EXIT_WORDS = ["goodbye", "bye", "exit", "quit", "stop", "done", "that's all", "i'm done"]


def _detect_exit(text):
    if not text:
        return False
    t = text.lower()
    return any(word in t for word in EXIT_WORDS)


def detect_mood(qi_session, timeout=7.0):
    """Detect user mood with ALFaceDetection and ALMemory; ensure camera enabled."""
    try:
        memory = qi_session.service("ALMemory")
        face_detection = qi_session.service("ALFaceDetection")
        cam_video = qi_session.service("ALVideoDevice")
        # Ensure camera is active by subscribing to video stream
        subscriber = cam_video.subscribe("MoodDetectionTemp", 0, 11, 5)
        face_detection.subscribe("Therapist_MoodDetection")
        start_time = time.time()
        mood = None
        while time.time() - start_time < timeout:
            mood_data = memory.getData("FaceCharacteristics/MoodDetected")
            if mood_data and isinstance(mood_data, list) and len(mood_data) > 0:
                mood = mood_data[0].lower()
                if mood in ["happy", "sad", "angry", "neutral", "surprised", "confused", "disgusted", "fearful"]:
                    break
            time.sleep(0.1)
        face_detection.unsubscribe("Therapist_MoodDetection")
        cam_video.unsubscribe(subscriber)
        if mood:
            return mood
    except Exception as e:
        print("[Mood detection error]:", e)
    return "neutral"


def recognize_face(qi_session, timeout=10):
    """Try to recognize user face using ALFaceDetection"""
    try:
        memory = qi_session.service("ALMemory")
        face_detection = qi_session.service("ALFaceDetection")
        tts_local = qi_session.service("ALTextToSpeech")
        face_detection.subscribe("FaceRecognition")
        tts_local.say("Let me see who you are.")
        start = time.time()
        while time.time() - start < timeout:
            if memory.getData("FaceDetected") == 1:
                names = memory.getData("FaceDetectedPeople")
                if names and isinstance(names, list) and len(names) > 0:
                    recognized_name = names[0]
                    face_detection.unsubscribe("FaceRecognition")
                    tts_local.say("Welcome back, {}!".format(recognized_name))
                    return recognized_name
            time.sleep(0.2)
        face_detection.unsubscribe("FaceRecognition")
    except Exception as e:
        print("[Face recognition error]:", e)
    return None


def register_face(qi_session, name, timeout=20):
    """Register a user face with name, with timeout and forced registration after timeout"""
    try:
        face_detection = qi_session.service("ALFaceDetection")
        tts_local = qi_session.service("ALTextToSpeech")
        memory = qi_session.service("ALMemory")
        tts_local.say("Please look at the camera to register your face, {}.".format(name))
        face_detection.subscribe("FaceRegister")
        start_time = time.time()
        face_registered = False
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                if not face_registered:
                    tts_local.say("Face not detected visually but I'll take a picture now.")
                    # Forcing registration even if face_detected event didn't occur
                    face_detection.learnFace(name)
                    tts_local.say("Thank you, {}. Your face has been registered.".format(name))
                else:
                    tts_local.say("Sorry, I couldn't see your face. Please try again later.")
                print("[Face registration] Timeout reached")
                break
            try:
                face_detected = memory.getData("FaceDetected", 0) == 1
            except Exception:
                face_detected = False
            print("[Face registration] Face detected: {}".format(face_detected))
            if face_detected:
                face_detection.learnFace(name)
                tts_local.say("Thank you, {}. Your face has been registered.".format(name))
                face_registered = True
                break
            time.sleep(0.5)
    except Exception as e:
        print("[Face registration error]:", e)
        try:
            tts_local.say("An error occurred during face registration.")
        except:
            pass
    finally:
        face_detection.unsubscribe("FaceRegister")


def mood_personalized_response(mood, username):
    global tts, motion
    try:
        if mood == "sad":
            tts.say("I see you are sad, {}. How about I dance for you to cheer you up?".format(username))
            motion.moveInit()
            # Example: Taichi dance
            try:
                motion.runBehavior("taichi")
            except:
                tts.say("Sorry, I cannot do the dance right now.")
        elif mood == "happy":
            tts.say("You look happy, {}! Would you like to share why you're feeling great?".format(username))
        elif mood == "neutral":
            tts.say("Smile, {}! You look great today.".format(username))
        else:
            tts.say("I'm here with you, {}. Let's talk.".format(username))
    except Exception as e:
        print("[Mood response error]:", e)


def ask_name():
    tts.say("What's your first name?")
    time.sleep(0.3)
    wav = record_audio(NAO_IP)
    try:
        with open(wav, 'rb') as f:
            files = {'audio': f}
            resp = requests.post(SERVER_URL + "/transcribe", files=files, timeout=15)
            text = resp.json().get('text', '')
            m = re.search(r"(?:my name is|i am|i'm|call me|this is)?\s*([A-Za-z]+)", text or "", re.IGNORECASE)
            if m:
                return m.group(1).capitalize()
            elif text.strip():
                return text.strip().split()[0].capitalize()
    except Exception as e:
        print("[Name capture error]:", e)
    return "Friend"


def therapy_loop(username, mood):
    global tts, leds
    session_data = {
        'timestamp': time.time(),
        'mood': mood,
        'messages': []
    }
    history = []
    previous_sessions = get_user_sessions(username)

    if previous_sessions:
        tts.say("Welcome back, {}. I remember our last talk.".format(username))
    else:
        tts.say("Nice to meet you, {}.".format(username))

    try:
        leds.fadeRGB("FaceLeds", 0.0, 0.8, 1.0, 0.4)
    except:
        pass

    mood_personalized_response(mood, username)

    tts.say("I'm here to listen. How are you feeling today?")

    for turn in range(15):
        print("[THERAPIST] Turn {}/15".format(turn + 1))
        wav = record_audio(NAO_IP)
        print("[Audio recorded]", wav)
        if not wav or not os.path.exists(wav):
            tts.say("I didn't hear you. Please try again.")
            continue

        try:
            with open(wav, 'rb') as f:
                files = {'audio': f}
                data = {
                    'username': username,
                    'mood': mood,
                    'history': json.dumps(history),
                    'previous_sessions': json.dumps(previous_sessions[-3:])
                }
                print("[Sending audio to server...]")
                resp = requests.post(
                    SERVER_URL + "/therapist_chat",
                    files=files,
                    data=data,
                    timeout=45
                )
                if resp.status_code != 200:
                    print("[Server error]:", resp.status_code)
                    tts.say("Sorry, I had trouble hearing you. Please try again.")
                    continue
                result = resp.json()
                user_input = result.get('user_input', '')
                reply = result.get('reply', '')

                safe_print("[USER]", user_input)
                safe_print("[REPLY]", reply)

                if _detect_exit(user_input):
                    tts.say("Thank you for sharing with me today, {}. Take care.".format(username))
                    break

                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": reply})
                session_data['messages'].append({'user': user_input, 'assistant': reply})

                if reply:
                    try:
                        clean_reply = clean_unicode_for_tts(reply)
                        tts.say(clean_reply)
                    except Exception as e:
                        print("[TTS ERROR]:", e)
                        tts.say("I'm here listening.")
                else:
                    tts.say("Tell me more.")

        except requests.Timeout:
            print("[TIMEOUT] Server took too long.")
            tts.say("Sorry, that took too long. Let's try again.")
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
    global tts, posture, leds, motion, session

    print("[THERAPIST] Starting therapist mode...")

    session = qi.Session()
    try:
        session.connect("tcp://{}:{}".format(NAO_IP, NAO_PORT))
        tts = session.service("ALTextToSpeech")
        posture = session.service("ALRobotPosture")
        leds = session.service("ALLeds")
        motion = session.service("ALMotion")
    except Exception as e:
        print("[THERAPIST] Could not connect to qi session:", e)

    username = recognize_face(session)
    if not username:
        username = ask_name()
        register_face(session, username)

    mood = detect_mood(session)
    print("[THERAPIST] Detected mood:", mood)

    try:
        tts.say("Let's sit down together.")
        posture.goToPosture("Sit", 0.5)
    except:
        pass

    tts.say("Hi, {}.".format(username))
    therapy_loop(username, mood)
    print("[THERAPIST] Session complete")


if __name__ == "__main__":
    start_therapist_mode()
