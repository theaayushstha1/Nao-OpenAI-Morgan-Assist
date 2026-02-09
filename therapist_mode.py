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
from utils.exit_detection import detect_exit_intent
from utils.name_utils import extract_name
from utils.face_naoqi import recognize_face_naoqi, learn_new_face_naoqi
from utils.speech import (random_phrase, time_of_day_greeting, expressive_say,
                          add_filler, listening_cue)
from config import NAO_IP, NAO_PORT

SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.105")
SERVER_URL = "http://{}:5000".format(SERVER_IP)
USER_DATA_FILE = "/data/home/nao/therapist_users.json"

tts = posture = leds = motion = None
session_obj = None
SESSION = requests.Session()

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
        data[username] = {'name': username, 'sessions': [], 'milestones': [], 'preferred_skills': []}
    data[username]['sessions'].append(session_data)
    data[username]['sessions'] = data[username]['sessions'][-10:]
    milestone = session_data.get('milestone')
    if milestone:
        if 'milestones' not in data[username]:
            data[username]['milestones'] = []
        data[username]['milestones'].append(milestone)
    preferred = session_data.get('preferred_skill')
    if preferred and preferred not in data[username].get('preferred_skills', []):
        data[username]['preferred_skills'].append(preferred)
    save_user_data(data)

def extract_last_summaries(username, num=3):
    sessions = get_user_sessions(username)
    return [s.get('summary', '') for s in sessions[-num:] if s.get('summary')]

def ask_name_therapist():
    global tts
    expressive_say(tts, random_phrase("greeting_unknown"), "warm")
    time.sleep(0.5)
    for attempt in range(2):
        wav = record_audio(NAO_IP)
        if not wav or not os.path.exists(wav):
            if attempt == 0:
                expressive_say(tts, random_phrase("ask_name_retry"), "warm")
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
                    expressive_say(tts, random_phrase("ask_name_retry"), "warm")
                    time.sleep(0.3)
        except Exception as e:
            print("[Name capture error]:", e)
            if attempt == 0:
                expressive_say(tts, random_phrase("ask_name_retry"), "warm")
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
    mood_to_pool = {
        "sad": ("mood_sad", (0.5, 0.5, 1)),
        "happy": ("mood_happy", (0, 1, 0)),
        "angry": ("mood_angry", (1, 0.5, 0)),
        "stressed": ("mood_stressed", (1, 0, 0)),
        "calm": ("mood_calm", (0, 1, 1)),
    }
    try:
        entry = mood_to_pool.get(mood)
        if entry:
            pool_key, rgb = entry
            expressive_say(tts, random_phrase(pool_key, name=username), "empathetic")
            leds.fadeRGB("FaceLeds", rgb[0], rgb[1], rgb[2], 0.5)
    except Exception as e:
        print("[Mood response error]:", e)

def summarize_session(history):
    key_points = []
    for msg in history[-8:]:
        if msg['role'] == 'user':
            mood = detect_mood_from_speech(msg['content'])
            if mood != 'neutral':
                key_points.append("expressed {}".format(mood))
        elif msg['role'] == 'assistant' and 'breathing' in msg['content'].lower():
            key_points.append("did a breathing exercise")
        elif msg['role'] == 'assistant' and 'grounding' in msg['content'].lower():
            key_points.append("did a grounding exercise")
    return ", ".join(set(key_points)) if key_points else None

def therapy_loop(username, mood):
    global tts, leds
    session_data = {'timestamp': time.time(), 'mood': mood, 'messages': []}
    history = []
    previous_sessions = get_user_sessions(username)
    session_num = len(previous_sessions) + 1

    if previous_sessions:
        expressive_say(tts, "{} Welcome back, {}. This is our session number {}.".format(
            time_of_day_greeting(), username, session_num), "warm")
        last_summary = previous_sessions[-1].get('summary')
        if last_summary:
            expressive_say(tts, "Last time, we focused on {}.".format(last_summary), "calm")
        else:
            expressive_say(tts, "It's good to have you back.", "warm")
    else:
        expressive_say(tts, "{} Nice to meet you, {}.".format(
            time_of_day_greeting(), username), "warm")

    expressive_say(tts, "To begin, how are you feeling today?", "calm")
    first_input = ""
    for turn in range(1, 1000):
        wav = record_audio(NAO_IP)
        if not wav or not os.path.exists(wav):
            expressive_say(tts, random_phrase("error_not_heard"), "calm")
            continue

        try:
            with open(wav, 'rb') as f:
                files = {'audio': f}
                data = {'username': username, 'mood': mood, 'history': json.dumps(history), 'previous_sessions': json.dumps(previous_sessions[-3:])}
                resp = requests.post(SERVER_URL + "/therapist_chat", files=files, data=data, timeout=45)
                if resp.status_code != 200:
                    expressive_say(tts, random_phrase("error_connection"), "thinking")
                    continue
                result = resp.json()
                user_input = result.get('user_input', '')
                reply = result.get('reply', '')
                if turn == 1:
                    first_input = user_input
                    expressive_say(tts, random_phrase("acknowledgment"), "empathetic")
                if detect_exit_intent(user_input):
                    expressive_say(tts, random_phrase("farewell_therapist", name=username), "warm")
                    break
                speech_mood = detect_mood_from_speech(user_input)
                if speech_mood != "neutral" and speech_mood != mood:
                    mood = speech_mood
                    mood_personalized_response(mood, username)
                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": reply})
                session_data['messages'].append({'user': user_input, 'assistant': reply})
                listening_cue(tts, probability=0.15)
                if reply:
                    clean_reply = clean_unicode_for_tts(reply)
                    expressive_say(tts, add_filler(clean_reply, probability=0.2), "empathetic")
                else:
                    expressive_say(tts, "Tell me more.", "calm")
        except Exception as e:
            print("[THERAPIST ERROR]:", e)
            expressive_say(tts, random_phrase("error_general"), "thinking")
            continue

    session_data['summary'] = summarize_session(history)
    session_data['milestone'] = None
    if session_num % 5 == 0:
        session_data['milestone'] = "Reached {} sessions".format(session_num)
        expressive_say(tts,
            "Wow, {}, we've had {} sessions together now. That's a real commitment to yourself, and I'm honored to support you on this journey.".format(username, session_num),
            "excited")
        time.sleep(0.5)
        expressive_say(tts,
            "Celebrating progress, no matter how small, is important. Thank you for trusting me along the way.",
            "warm")
    add_user_session(username, session_data)
    leds.fadeRGB("FaceLeds", 1.0, 1.0, 1.0, 0.3)

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
    expressive_say(tts, random_phrase("entering_therapist"), "calm")
    time.sleep(0.5)
    username = recognize_face_naoqi(session_obj, tts, subscriber_name="TherapistFaceReco", timeout=10)
    if not username:
        username = ask_name_therapist()
        learn_new_face_naoqi(session_obj, tts, username, subscriber_name="TherapistFaceLearn")
    else:
        expressive_say(tts, random_phrase("greeting_known", name=username), "warm")
    try:
        posture.goToPosture("Sit", 0.5)
        leds.fadeRGB("FaceLeds", 0.2, 0.6, 1.0, 0.4)
    except:
        pass
    therapy_loop(username, "neutral")

if __name__ == "__main__":
    start_therapist_mode()
