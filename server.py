# server.py
# -*- coding: utf-8 -*-
from __future__ import print_function
import os, json, traceback, re
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from flask import send_file

from dotenv import load_dotenv
load_dotenv()  # read .env into os.environ

# --- Paths ---
BASEDIR = os.path.abspath(os.path.dirname(__file__))
TEMP_DIR = os.path.join(BASEDIR, "tmp_audio")
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# --- OpenAI (legacy 0.x sdk) ---
import openai
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    print("‼️ OPENAI_API_KEY not set. Put it in .env or env vars.")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

# --- Face rec ---
import face_recognition
import numpy as np

# --- Local modules ---
import memory_manager
import gpt_handler     # must have get_reply(messages) -> {"reply": "...", "function_call": {...}}
import face_store

app = Flask(__name__)

# ----------------- helpers -----------------
def _extract_name(text):
    """Find a first name from phrases like 'my name is ...', 'call me ...', 'i am ...'."""
    if not text:
        return None
    lower = text.lower()
    m = re.search(r"(?:my name is|call me|i am)\s+([A-Za-z]+)", lower)
    if not m:
        return None
    name = m.group(1).strip().capitalize()
    return name or None

def _encode_face_from_file(path):
    img = face_recognition.load_image_file(path)
    boxes = face_recognition.face_locations(img, model="hog")  # CPU-friendly
    encs = face_recognition.face_encodings(img, boxes)
    if not encs:
        return None
    return encs[0]  # first face only

# ----------------- base routes -----------------
@app.route("/")
def home():
    return "🤖 NAO Server is up and running!"

@app.route("/test", methods=["GET"])
def test():
    return jsonify({"message": "Test route working!"})

# ----------------- text chat (no audio) -----------------
@app.route("/chat_text", methods=["POST"])
def chat_text():
    try:
        data = request.get_json(force=True) or {}
        username = (data.get("username") or "guest").strip().lower()
        user_input = (data.get("text") or "").strip()

        memory_manager.initialize_user(username)

        # Name extraction and persistence (with migration friend/guest -> real name)
        extracted = _extract_name(user_input)
        if extracted:
            if username in ("guest", "friend"):
                memory_manager.initialize_user(username)
                memory_manager.store_user_name(username, extracted)
                memory_manager.migrate_username(username, extracted)
                username = extracted  # continue this request under the real name
            else:
                memory_manager.store_user_name(username, extracted)

        # inject known name for continuity
        known = memory_manager.get_user_name(username)
        if known and ("my name is" not in user_input.lower()):
            user_input = "My name is {}. {}".format(known, user_input)

        past = memory_manager.get_chat_history(username)
        system_prompt = (
            "You are NAO, a polite, playful robot assistant helping students. "
            "You remember names and conversations. Speak naturally, like a caring friend."
        )
        messages = [{"role": "system", "content": system_prompt}] + past + [{"role": "user", "content": user_input}]

        result = gpt_handler.get_reply(messages)
        reply = (result.get("reply") or "").strip() or "Sorry, I didn’t quite get that."
        function_call = result.get("function_call") or {}

        memory_manager.add_user_message(username, user_input)
        memory_manager.add_bot_reply(username, reply if reply else json.dumps(function_call))
        memory_manager.save_chat_history(username)

        return jsonify({"username": username, "reply": reply, "function_call": function_call, "user_input": user_input})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ----------------- audio upload (NAO path) -----------------
@app.route("/upload", methods=["POST"])
def upload_audio():
    if 'file' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    username = (request.form.get("username") or "guest").strip().lower()
    raw_name = request.files['file'].filename or "input.wav"
    filename = secure_filename(raw_name)
    temp_path = os.path.join(TEMP_DIR, filename)

    try:
        memory_manager.initialize_user(username)

        # Save audio
        audio_file = request.files['file']
        audio_file.save(temp_path)
        print("Saved temp file at {}".format(temp_path))

        # Transcribe
        with open(temp_path, "rb") as f:
            transcript = openai.Audio.transcribe(WHISPER_MODEL, f)

        user_input = getattr(transcript, "text", None) or transcript.get("text", "") or ""
        user_input = user_input.strip()
        print(u"📝 Transcribed: {}".format(user_input))

        # Name extraction and persistence (with migration friend/guest -> real name)
        extracted = _extract_name(user_input)
        if extracted:
            if username in ("guest", "friend"):
                memory_manager.initialize_user(username)
                memory_manager.store_user_name(username, extracted)
                memory_manager.migrate_username(username, extracted)
                username = extracted  # continue this request under the real name
            else:
                memory_manager.store_user_name(username, extracted)

        # Inject known name for better continuity
        known = memory_manager.get_user_name(username)
        if known and ("my name is" not in user_input.lower()):
            user_input = "My name is {}. {}".format(known, user_input)

        # Build messages
        past = memory_manager.get_chat_history(username)
        system_prompt = (
            "You are NAO, a polite, playful robot assistant helping students. "
            "You remember names and conversations. Speak naturally, like a caring friend."
        )
        messages = [{"role": "system", "content": system_prompt}] + past + [{"role": "user", "content": user_input}]

        # GPT reply
        result = gpt_handler.get_reply(messages)
        reply = (result.get("reply") or "").strip() or "Sorry, I didn’t quite get that."
        function_call = result.get("function_call") or {}

        print(u"🤖 GPT Reply: {}".format(reply))
        if function_call:
            print(u"⚙️ Function call: {}".format(function_call))

        # Persist convo
        memory_manager.add_user_message(username, user_input)
        memory_manager.add_bot_reply(username, reply if reply else json.dumps(function_call))
        memory_manager.save_chat_history(username)

        return jsonify({
            "username": username,
            "reply": reply,
            "user_input": user_input,
            "function_call": function_call
        })

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

# ----------------- face recognition -----------------
@app.route("/face/recognize", methods=["POST"])
def face_recognize():
    """
    Form-data:
      file: image/jpeg or image/png
      tolerance (optional): float, default 0.5 (try 0.55–0.6 if needed)
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"}), 400

    try:
        tol_str = request.form.get("tolerance", "0.60").strip()
        tol = float(tol_str)
    except Exception:
        tol = 0.5

    f = request.files["file"]
    filename = secure_filename(f.filename or "cap.jpg")
    path = os.path.join(TEMP_DIR, filename)
    f.save(path)

    try:
        target = _encode_face_from_file(path)
        if target is None:
            return jsonify({"ok": True, "match": False, "reason": "no_face_detected"})

        names, encs = face_store.get_all()
        if not encs:
            return jsonify({"ok": True, "match": False, "reason": "db_empty"})

        dists = [np.linalg.norm(e - target) for e in encs]
        best_idx = int(np.argmin(dists))
        best_dist = float(dists[best_idx])

        if best_dist <= tol:
            return jsonify({"ok": True, "match": True, "name": names[best_idx], "distance": best_dist})
        else:
            return jsonify({"ok": True, "match": False, "best_distance": best_dist})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if os.path.exists(path):
                os.remove(path)
        except:
            pass

@app.route("/face/enroll", methods=["POST"])
def face_enroll():
    """
    Form-data:
      file: image
      name: target display name (e.g., Max)
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"}), 400
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "missing name"}), 400

    f = request.files["file"]
    filename = secure_filename(f.filename or "cap.jpg")
    path = os.path.join(TEMP_DIR, filename)
    f.save(path)

    try:
        enc = _encode_face_from_file(path)
        if enc is None:
            return jsonify({"ok": False, "error": "no_face_detected"}), 200

        # save face vector
        face_store.add_encoding(name, enc.tolist())

        # also store name in chat memory so GPT uses it 
        try:
            memory_manager.initialize_user(name)
            memory_manager.store_user_name(name, name)
            memory_manager.save_chat_history(name)
        except:
            pass
        return jsonify({"ok": True, "enrolled": name})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if os.path.exists(path):
                os.remove(path)
        except:
            pass

@app.route("/face/list", methods=["GET"])
def face_list():
    names, encs = face_store.get_all()
    # counts per name
    counts = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return jsonify({"ok": True, "counts": counts, "total_encodings": len(encs)})

# --------------- main ---------------
if __name__ == "__main__":
    print("🔵 Starting Flask server on http://0.0.0.0:5000/")
    app.run(host="0.0.0.0", port=5000)
