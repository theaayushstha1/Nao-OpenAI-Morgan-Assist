# server.py
# -*- coding: utf-8 -*-
from __future__ import print_function
import os, json, traceback, re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
load_dotenv()  # keep .env

# dirs
BASEDIR = os.path.abspath(os.path.dirname(__file__))
TEMP_DIR = os.path.join(BASEDIR, "tmp_audio")
if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)

# OpenAI (legacy 0.x)
import openai
openai.api_key = os.getenv("OPENAI_API_KEY")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

# config from .env (with safe defaults)
TIMEZONE_LABEL   = os.getenv("TIMEZONE", "US/Eastern")   # label only
WEATHER_LOCATION = os.getenv("WEATHER_LOCATION", "Baltimore,US")

# face rec
import face_recognition
import numpy as np

# app modules
import memory_manager
import gpt_handler
import face_store

# stdlib HTTP (no requests)
try:
    from urllib.request import urlopen  # py3
except Exception:
    from urllib2 import urlopen         # py2

app = Flask(__name__)

# ---------- helpers ----------
def _extract_name(text):
    if not text: return None
    m = re.search(r"(?:my name is|call me|i am)\s+([A-Za-z]+)", (text or "").lower())
    return m.group(1).strip().capitalize() if m else None

def _encode_face_from_file(path):
    img = face_recognition.load_image_file(path)
    boxes = face_recognition.face_locations(img, model="hog")
    encs = face_recognition.face_encodings(img, boxes)
    if not encs: return None
    return encs[0]

def _soft_trim(s, max_len=1200):
    s = (s or "").strip()
    return s if len(s) <= max_len else s[:max_len]

# ---------- Eastern time (no pytz/zoneinfo) ----------
# US DST: starts 2nd Sun in Mar 02:00; ends 1st Sun in Nov 02:00 (local)
def _nth_sunday(year, month, n):
    first = datetime(year, month, 1)
    offset = (6 - first.weekday()) % 7  # Mon=0..Sun=6
    first_sun = first + timedelta(days=offset)
    return first_sun + timedelta(weeks=n-1)

def _is_us_eastern_dst(utc_now):
    local_guess = utc_now - timedelta(hours=5)  # EST base
    y = local_guess.year
    dst_start = datetime(y, 3,  _nth_sunday(y, 3,  2).day, 2, 0, 0)
    dst_end   = datetime(y, 11, _nth_sunday(y, 11, 1).day, 2, 0, 0)
    return dst_start <= local_guess < dst_end

def _now_eastern():
    now_utc = datetime.utcnow()
    offset = -4 if _is_us_eastern_dst(now_utc) else -5
    return now_utc + timedelta(hours=offset)

def _format_time(dt):
    try:
        return dt.strftime("%-I:%M %p") if os.name != "nt" else dt.strftime("%#I:%M %p")
    except Exception:
        return dt.strftime("%I:%M %p")

# ---------- built-in intents ----------
def _http_get_json(url, timeout=6):
    resp = urlopen(url, timeout=timeout)
    raw = resp.read()
    if isinstance(raw, bytes):
        try: raw = raw.decode('utf-8', 'ignore')
        except Exception: pass
    return json.loads(raw)

def _get_weather(loc):
    try:
        j = _http_get_json("https://wttr.in/{}?format=j1".format(loc), timeout=6)
        cur = (j.get("current_condition") or [{}])[0]
        desc = ((cur.get("weatherDesc") or [{}])[0].get("value") or "").strip()
        temp_f = cur.get("temp_F") or cur.get("FeelsLikeF") or ""
        temp_c = cur.get("temp_C") or cur.get("FeelsLikeC") or ""
        hourly = ((j.get("weather") or [{}])[0]).get("hourly") or []
        cor = None
        for h in hourly[:4]:
            v = h.get("chanceofrain")
            if v is not None:
                cor = max(int(v), int(cor)) if cor is not None else int(v)
        parts = []
        if desc: parts.append(desc)
        if temp_f: parts.append(u"{}°F".format(temp_f))
        elif temp_c: parts.append(u"{}°C".format(temp_c))
        if cor is not None: parts.append(u"rain chance {}%".format(cor))
        return " ".join(parts) if parts else "I couldn’t fetch the weather right now."
    except Exception:
        return "I couldn’t fetch the weather right now."

def _handle_time_intent(text):
    t = (text or "").lower()
    if any(k in t for k in [
        "what time", "current time", "time is it", "tell me the time",
        "time now", "what's the time", "whats the time"
    ]):
        dt = _now_eastern()
        return "The time is {}.".format(_format_time(dt))
    return None

def _handle_weather_intent(text):
    t = (text or "").lower()
    if any(k in t for k in ["weather", "temperature", "rain", "forecast"]):
        m = re.search(r"(?:in|for)\s+([a-zA-Z\s]+)$", t)  # optional override
        loc = WEATHER_LOCATION
        if m:
            guess = m.group(1).strip()
            if 2 <= len(guess) <= 40:
                loc = guess
        return u"The weather is {}.".format(_get_weather(loc))
    return None

def _maybe_builtin_reply(text):
    ans = _handle_time_intent(text)
    if ans: return ans
    ans = _handle_weather_intent(text)
    if ans: return ans
    return None

# ---------- mode prompts ----------
BASE_TONE = (
    "You are NAO, a friendly campus robot. Be clear, warm, and brief. "
    "Speak out loud; keep responses concise and easy to hear. Avoid lists unless asked."
)
MODE_SYSTEMS = {
    "assistant":   "Mode: Assistant. Be practical and helpful. Short, direct answers with quick tips.",
    "study":       "Mode: Study. Teach step by step. Use small chunks, simple examples, and quick checks. If math or code, outline steps first, then solve.",
    "therapy":     "Mode: Therapy-style support. Be empathetic, validating, and non-judgmental. Offer gentle questions and coping ideas (breathing, grounding, small next steps). Do NOT give medical diagnoses. Do NOT say 'call a hotline' unless the user clearly says they are in immediate danger—then calmly suggest seeking urgent local help.",
    "humor":       "Mode: Humor. Light jokes and playful tone. Keep it kind. No insults or edgy topics. One quick joke at a time unless asked for more.",
    "coach":       "Mode: Coach. Focus on goals, small actions, and accountability. Ask brief clarifying questions, then suggest a simple plan with next steps.",
    "storyteller": "Mode: Storyteller. Tell short, vivid stories. 4–8 sentences unless asked for longer. Match genre if the user names one.",
    "translator":  "Mode: Translator. Translate user text faithfully. Keep meaning and tone. If target language is not specified, translate into clear English.",
}
def _system_for_mode(mode):
    part = MODE_SYSTEMS.get((mode or "").strip().lower(), MODE_SYSTEMS["assistant"])
    return part + " " + BASE_TONE

def _inject_known_name(username, user_input):
    known = memory_manager.get_user_name(username)
    if known and ("my name is" not in (user_input or "").lower()):
        return "My name is {}. {}".format(known, user_input or "")
    return user_input or ""

def _build_messages(username, user_input, mode):
    past = memory_manager.get_chat_history(username)
    sys = _system_for_mode(mode)
    return [{"role": "system", "content": sys}] + past + [{"role": "user", "content": user_input}]

# ---------- routes ----------
@app.route("/")
def home():
    return "🤖 NAO Server is up and running!"

@app.route("/test", methods=["GET"])
def test():
    return jsonify({"message": "Test route working!"})

@app.route("/chat_text", methods=["POST"])
def chat_text():
    try:
        data = request.get_json(force=True) or {}
        username   = (data.get("username") or "guest").strip().lower()
        user_input = _soft_trim((data.get("text") or "").strip())
        mode       = (data.get("mode") or "assistant").strip().lower()
        print(u"[chat_text] user={}, mode={}".format(username, mode))

        memory_manager.initialize_user(username)

        # built-ins first
        builtin = _maybe_builtin_reply(user_input)
        if builtin:
            memory_manager.add_user_message(username, user_input)
            memory_manager.add_bot_reply(username, builtin)
            memory_manager.save_chat_history(username)
            return jsonify({"username": username, "mode": mode, "reply": builtin, "function_call": {}, "user_input": user_input})

        # names
        extracted = _extract_name(user_input)
        if extracted:
            if username in ("guest", "friend"):
                memory_manager.initialize_user(username)
                memory_manager.store_user_name(username, extracted)
                memory_manager.migrate_username(username, extracted)
                username = extracted
            else:
                memory_manager.store_user_name(username, extracted)

        user_input = _inject_known_name(username, user_input)

        # GPT
        messages = _build_messages(username, user_input, mode)
        result = gpt_handler.get_reply(messages)
        reply = (result.get("reply") or "").strip() or "Sorry, I didn’t catch that."
        function_call = result.get("function_call") or {}

        memory_manager.add_user_message(username, user_input)
        memory_manager.add_bot_reply(username, reply if reply else json.dumps(function_call))
        memory_manager.save_chat_history(username)

        return jsonify({"username": username, "mode": mode, "reply": reply, "function_call": function_call, "user_input": user_input})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/upload", methods=["POST"])
def upload_audio():
    if 'file' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    username = (request.form.get("username") or "guest").strip().lower()
    mode     = (request.form.get("mode") or "assistant").strip().lower()
    raw_name = request.files['file'].filename or "input.wav"
    filename = secure_filename(raw_name)
    temp_path = os.path.join(TEMP_DIR, filename)

    try:
        memory_manager.initialize_user(username)
        print(u"[upload] user={}, mode={}".format(username, mode))

        # save audio
        request.files['file'].save(temp_path)
        print("Saved temp file at {}".format(temp_path))

        # transcribe
        with open(temp_path, "rb") as f:
            transcript = openai.Audio.transcribe(WHISPER_MODEL, f)
        user_input = _soft_trim(getattr(transcript, "text", None) or transcript.get("text", "") or "")
        print(u"📝 Transcribed: {}".format(user_input))

        # built-ins first
        builtin = _maybe_builtin_reply(user_input)
        if builtin:
            memory_manager.add_user_message(username, user_input)
            memory_manager.add_bot_reply(username, builtin)
            memory_manager.save_chat_history(username)
            return jsonify({"username": username, "mode": mode, "reply": builtin, "user_input": user_input, "function_call": {}})

        # names
        extracted = _extract_name(user_input)
        if extracted:
            if username in ("guest", "friend"):
                memory_manager.initialize_user(username)
                memory_manager.store_user_name(username, extracted)
                memory_manager.migrate_username(username, extracted)
                username = extracted
            else:
                memory_manager.store_user_name(username, extracted)

        user_input = _inject_known_name(username, user_input)

        # GPT
        messages = _build_messages(username, user_input, mode)
        result = gpt_handler.get_reply(messages)
        reply = (result.get("reply") or "").strip() or "Sorry, I didn’t quite get that."
        function_call = result.get("function_call") or {}

        print(u"🤖 GPT Reply [{}]: {}".format(mode, reply))
        if function_call:
            print(u"⚙️ Function call: {}".format(function_call))

        memory_manager.add_user_message(username, user_input)
        memory_manager.add_bot_reply(username, reply if reply else json.dumps(function_call))
        memory_manager.save_chat_history(username)

        return jsonify({"username": username, "mode": mode, "reply": reply, "user_input": user_input, "function_call": function_call})
    except Exception as e:
        print("Error in /upload: {}".format(e))
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception as e:
            print("Temp cleanup failed: {}".format(e))

# face endpoints
@app.route("/face/recognize", methods=["POST"])
def face_recognize():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"}), 400
    try:
        tol = float((request.form.get("tolerance", "0.60") or "0.60").strip())
    except Exception:
        tol = 0.5
    f = request.files["file"]; filename = secure_filename(f.filename or "cap.jpg")
    path = os.path.join(TEMP_DIR, filename); f.save(path)
    try:
        target = _encode_face_from_file(path)
        if target is None:
            return jsonify({"ok": True, "match": False, "reason": "no_face_detected"})
        names, encs = face_store.get_all()
        if not encs:
            return jsonify({"ok": True, "match": False, "reason": "db_empty"})
        dists = [np.linalg.norm(e - target) for e in encs]
        best_idx = int(np.argmin(dists)); best_dist = float(dists[best_idx])
        if best_dist <= tol:
            return jsonify({"ok": True, "match": True, "name": names[best_idx], "distance": best_dist})
        else:
            return jsonify({"ok": True, "match": False, "best_distance": best_dist})
    except Exception as e:
        traceback.print_exc(); return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if os.path.exists(path): os.remove(path)
        except: pass

@app.route("/face/enroll", methods=["POST"])
def face_enroll():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"}), 400
    name = (request.form.get("name") or "").strip()
    if not name: return jsonify({"ok": False, "error": "missing name"}), 400
    f = request.files["file"]; filename = secure_filename(f.filename or "cap.jpg")
    path = os.path.join(TEMP_DIR, filename); f.save(path)
    try:
        enc = _encode_face_from_file(path)
        if enc is None:
            return jsonify({"ok": False, "error": "no_face_detected"}), 200
        face_store.add_encoding(name, enc.tolist())
        try:
            memory_manager.initialize_user(name)
            memory_manager.store_user_name(name, name)
            memory_manager.save_chat_history(name)
        except: pass
        return jsonify({"ok": True, "enrolled": name})
    except Exception as e:
        traceback.print_exc(); return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if os.path.exists(path): os.remove(path)
        except: pass

@app.route("/face/list", methods=["GET"])
def face_list():
    names, encs = face_store.get_all()
    counts = {}
    for n in names: counts[n] = counts.get(n, 0) + 1
    return jsonify({"ok": True, "counts": counts, "total_encodings": len(encs)})

if __name__ == "__main__":
    print("🔵 Starting Flask server on http://0.0.0.0:5000/")
    app.run(host="0.0.0.0", port=5000)
