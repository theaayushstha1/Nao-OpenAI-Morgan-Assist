# -*- coding: utf-8 -*-
from __future__ import print_function
import os, json, traceback, re, time, random
import wave, contextlib
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
load_dotenv()

import openai
from pinecone import Pinecone

# Load environment variables
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE")
PINECONE_ENV = os.getenv("PINECONE_ENV", "us-east-1")

pinecone_enabled = PINECONE_API_KEY and PINECONE_INDEX_NAME

# Initialize Pinecone if enabled
if pinecone_enabled:
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX_NAME)


BASEDIR = os.path.abspath(os.path.dirname(__file__))
TEMP_DIR = os.path.join(BASEDIR, "tmp_audio")
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# --- OpenAI (legacy 0.x sdk) ---
import openai
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    print("‼️ OPENAI_API_KEY not set. Put it in .env or env vars.", flush=True)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

# --- Face rec ---
import face_recognition
import numpy as np

# --- Local modules ---
import memory_manager
import gpt_handler     # must have get_reply(messages) -> {"reply": "...", "function_call": {...}}
import face_store

app = Flask(__name__)

# ----------------- Mode prompts (tight, mode-true) -----------------
MODE_PROMPTS = {
    "general": (
        "You are NAO, a smart, upbeat assistant. Be concise (1–3 sentences) and practical. "
        "Use plain language, offer clear steps, and ask at most one brief follow-up when helpful. "
        "Avoid medical/legal/financial advice."
    ),
    "study": (
        "You are NAO Study Coach. Teach step-by-step with tiny examples or analogies. "
        "Prefer compact bullets or short numbered steps. Highlight key formulas/definitions. "
        "Finish with one mini practice question and a short check-for-understanding."
    ),
    "therapist": (
        "You are NAO in Support Mode (not a clinician). Be warm, validating, and non-judgmental. "
        "Reflect feelings, then offer 1–2 concrete, actionable skills (e.g., 4-4-4 breathing, 5-4-3-2-1 grounding, "
        "thought reframing template, tiny action plan, compassionate self-talk). "
        "Keep replies short (2–4 sentences) and specific to what the user said. "
        "Do not diagnose. Only suggest crisis resources if the user states imminent self-harm."
    ),
    "broker": (
        "You are NAO Market Guide. Explain market concepts neutrally in plain language, 1–4 sentences. "
        "Give examples of diversification, time horizon, and risk management. "
        "No personalized financial advice; stay educational and time-agnostic."
    ),
}
VALID_MODES = set(MODE_PROMPTS.keys())

def _prompt_for_mode(mode):
    m = (mode or "general").lower()
    return MODE_PROMPTS.get(m, MODE_PROMPTS["general"])

def _followup_for_mode(mode):
    m = (mode or "general").lower()
    if m == "study":
        return "What topic should we study?"
    if m == "therapist":
        return "What’s been weighing on you lately?"
    if m == "broker":
        return "What market topic would you like to explore?"
    return "What’s on your mind?"

# ----------------- Mode NLP -----------------
KEYWORDS = {
    "general":   ["general","normal","default","assistant","regular","general mode","normal mode"],
    "study":     ["study","study mode","school","homework","learn","exam","class","test","assignment"],
    "therapist": ["therapist","therapy","therapist mode","therapy mode","mental","feelings","stress","anxious","depressed","mood","relax","calm"],
    "broker":    ["broker","broker mode","stock","stocks","market","markets","trading","finance"],
}
SWITCH_WORDS = ["switch mode","change mode","mode menu","set mode","choose mode","pick a mode",
                "switch to","change to","set to","go to","turn to","switch","change","set","go","turn"]

def _canonical_mode(s):
    if not s: return None
    s = s.strip().lower()
    if s in ("default","normal","general","assistant","regular","general mode","normal mode"): return "general"
    if s.startswith("study") or s in ("school","homework","exam","class","test","assignment","study mode"): return "study"
    if s in ("therapist","therapy","therapist mode","therapy mode","wellness","relax","calm","mental","mood","feelings"): return "therapist"
    if s in ("broker","stock","stocks","stock broker","market","markets","trading","finance","broker mode"): return "broker"
    return s if s in VALID_MODES else None

def _extract_mode_from_text(text):
    if not text: return None
    t = text.lower()
    for m, words in KEYWORDS.items():
        for w in words:
            if re.search(r"\b"+re.escape(w)+r"\b", t):
                return m
    return None

def _is_switch_request(text):
    if not text: return False
    t = text.lower()
    return any(kw in t for kw in SWITCH_WORDS)

def _resolve_mode(user_input, provided_mode):
    detected = _extract_mode_from_text(user_input)
    asked = _is_switch_request(user_input)
    if detected:
        return detected, True, False  # mode, changed, prompt?
    if asked:
        return (provided_mode or "general"), False, True
    return (provided_mode or "general"), False, False

def _strip_mode_words(text):
    if not text: return text
    t = text
    all_words = []
    for lst in KEYWORDS.values(): all_words += lst
    all_words += SWITCH_WORDS + ["to"]
    for w in sorted(all_words, key=len, reverse=True):
        t = re.sub(r"\b" + re.escape(w) + r"\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,.;!?").strip()
    return t

# ----------------- helpers -----------------
def _extract_name(text):
    """Robust first-name extraction."""
    if not text: return None
    t = text.strip()
    pats = [
        r"(?:\bmy\s+(?:first\s+)?name\s+is\s+)([A-Za-z]+)\b",
        r"(?:\bi\s*am\s+)([A-Za-z]+)\b",
        r"(?:\bi['’]m\s+)([A-Za-z]+)\b",
        r"(?:\bthis\s+is\s+)([A-Za-z]+)\b",
        r"(?:\bcall\s+me\s+)([A-Za-z]+)\b",
    ]
    for p in pats:
        m = re.search(p, t, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip().capitalize()
    return None

def _encode_face_from_file(path):
    img = face_recognition.load_image_file(path)
    boxes = face_recognition.face_locations(img, model="hog")
    encs = face_recognition.face_encodings(img, boxes)
    if not encs: return None
    return encs[0]

# --- audio validation + robust whisper with retries ---
def _is_valid_wav(path):
    try:
        with contextlib.closing(wave.open(path, 'rb')) as wf:
            frames = wf.getnframes()
            fr = wf.getframerate()
            return frames > 0 and fr > 0
    except Exception:
        return False

def _audio_duration_s(path):
    try:
        with contextlib.closing(wave.open(path, 'rb')) as wf:
            frames = wf.getnframes()
            fr = wf.getframerate()
            if fr <= 0: return 0.0
            return float(frames) / float(fr)
    except Exception:
        return 0.0

def _validate_audio_or_503(path, min_seconds=0.12, min_size_bytes=400):
    try:
        size = os.path.getsize(path)
        if size < min_size_bytes:
            raise ValueError("audio_too_small")
        ext = os.path.splitext(path)[1].lower()
        if ext == ".wav":
            if not _is_valid_wav(path):
                raise ValueError("bad_wav_header")
            if _audio_duration_s(path) < min_seconds:
                raise ValueError("audio_too_short")
    except Exception as e:
        # surface a gentle 503 so the robot can re-prompt
        raise RuntimeError(str(e))
    
def get_embedding(text):
    try:
        response = openai.Embedding.create(
            input=text,
            model="text-embedding-3-small"
        )
        return response["data"][0]["embedding"]
    except Exception as e:
        print("[Embedding Error]", e)
        return None

def query_pinecone(embedding, top_k=5):
    try:
        results = index.query(
            vector=embedding,
            top_k=top_k,
            namespace=PINECONE_NAMESPACE,
            include_metadata=True
        )
        return results["matches"]
    except Exception as e:
        print("[Pinecone Error]", e)
        return []


def transcribe_with_retry_path(path, model, max_tries=4, base_delay=0.8):
    last_err = None
    for i in range(max_tries):
        try:
            with open(path, "rb") as f:
                return openai.Audio.transcribe(model, f)
        except Exception as e:
            last_err = e
            wait = base_delay * (2 ** i) + random.random() * 0.2
            print("Whisper retry {}/{} in {:.2f}s due to: {}".format(i+1, max_tries, wait, e), flush=True)
            time.sleep(wait)
    raise last_err

# ----------------- base routes -----------------
@app.route("/")
def home():
    return "🤖 NAO Server is up and running!"

@app.route("/test", methods=["GET"])
def test():
    return jsonify({"message": "Test route working!"})

# ----------------- /chat_text -----------------
@app.route("/chat_text", methods=["POST"])
def chat_text():
    try:
        data = request.get_json(force=True) or {}
        username      = (data.get("username") or "friend").strip().lower()
        user_input    = (data.get("text") or "").strip()
        provided_raw  = (data.get("mode") or "").strip().lower() or None
        provided_mode = _canonical_mode(provided_raw)

        memory_manager.initialize_user(username)

        mode, mode_changed, mode_prompt = _resolve_mode(user_input, provided_mode)

        if mode_prompt:
            return jsonify({
                "username": username,
                "reply": "Which mode would you like: General, Study, Therapist, or Broker?",
                "function_call": {},
                "user_input": user_input,
                "active_mode": provided_mode or "general",
                "mode_changed": False,
                "mode_prompt": True
            })

        cleaned = _strip_mode_words(user_input) if mode_changed else user_input

        # If the user only said the mode, skip GPT and give a targeted follow-up
        if mode_changed and not cleaned.strip():
            reply = u"✅ Switched to {} mode. {}".format(mode.capitalize(), _followup_for_mode(mode))
            memory_manager.add_user_message(username, user_input)
            memory_manager.add_bot_reply(username, reply)
            memory_manager.save_chat_history(username)
            print(">> MODE(chat_text): active={} changed={} prompt={}".format(mode, True, False), flush=True)
            return jsonify({
                "username": username,
                "reply": reply,
                "function_call": {},
                "user_input": "",
                "active_mode": mode,
                "mode_changed": True,
                "mode_prompt": False
            })

        # Name extraction and persistence
        extracted = _extract_name(cleaned)
        if extracted:
            if username in ("guest","friend"):
                memory_manager.store_user_name(username, extracted)
                memory_manager.migrate_username(username, extracted)
                username = extracted
            else:
                memory_manager.store_user_name(username, extracted)

        known = memory_manager.get_user_name(username)
        if known and ("my name is" not in cleaned.lower()):
            cleaned = "My name is {}. {}".format(known, cleaned)

        past = memory_manager.get_chat_history(username)
        system_prompt = _prompt_for_mode(mode)
        messages = [{"role":"system","content":system_prompt}] + past + [{"role":"user","content":cleaned}]

        result = gpt_handler.get_reply(messages)
        raw_reply = (result.get("reply") or "").strip()
        function_call = result.get("function_call") or {}

        if mode_changed:
            confirm = u"✅ Switched to {} mode.".format(mode.capitalize())
            reply = (confirm + " " + raw_reply) if raw_reply else confirm
        else:
            reply = raw_reply or "Sorry, I didn't quite get that."

        memory_manager.add_user_message(username, cleaned)
        memory_manager.add_bot_reply(username, reply if reply else json.dumps(function_call))
        memory_manager.save_chat_history(username)

        print(">> MODE(chat_text): active={} changed={} prompt={}".format(mode, mode_changed, False), flush=True)

        return jsonify({
            "username": username,
            "reply": reply,
            "function_call": function_call,
            "user_input": cleaned,
            "active_mode": mode,
            "mode_changed": mode_changed,
            "mode_prompt": False
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ----------------- /upload (audio) -----------------
@app.route("/upload", methods=["POST"])
def upload_audio():
    if 'file' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    username      = (request.form.get("username") or "friend").strip().lower()
    provided_raw  = (request.form.get("mode") or "").strip().lower() or None
    provided_mode = _canonical_mode(provided_raw)

    raw_name  = request.files['file'].filename or "input.wav"
    filename  = secure_filename(raw_name)
    temp_path = os.path.join(TEMP_DIR, filename)

    try:
        memory_manager.initialize_user(username)

        # Save audio
        request.files['file'].save(temp_path)
        print("Saved temp file at {}".format(temp_path), flush=True)

        # Quick validation so we can return a friendly, actionable 503
        try:
            _validate_audio_or_503(temp_path)
        except RuntimeError as ve:
            detail = str(ve) or "audio_validation_failed"
            print("Audio validation failed: {}".format(detail), flush=True)
            return jsonify({'error': 'transcription_failed', 'detail': detail}), 503

        # Transcribe with robust retries (re-open file each attempt)
        transcript = transcribe_with_retry_path(temp_path, WHISPER_MODEL, max_tries=4)

        user_input = (
            getattr(transcript, "text", None)
            or getattr(transcript, "data", {}).get("text", "")
            or (transcript.get("text", "") if isinstance(transcript, dict) else "")
            or ""
        ).strip()
        print(u"📝 Transcribed: {}".format(user_input), flush=True)

        mode, mode_changed, mode_prompt = _resolve_mode(user_input, provided_mode)

        if mode_prompt:
            print(">> MODE(upload): prompt user to choose mode", flush=True)
            return jsonify({
                "username": username,
                "reply": "Which mode would you like: General, Study, Therapist, or Broker?",
                "user_input": user_input,
                "function_call": {},
                "active_mode": provided_mode or "general",
                "mode_changed": False,
                "mode_prompt": True
            })

        cleaned = _strip_mode_words(user_input) if mode_changed else user_input


        # ----------------- RAG response using Pinecone -----------------
        embedding = get_embedding(cleaned)
        matches = query_pinecone(embedding) if embedding else []

        if matches:
            context_text = "\n".join([m["metadata"].get("text", "") for m in matches])
            prompt = (
                "You are a helpful assistant answering based on Morgan State University Computer Science department info.\n"
                "Use the below context to answer the user's question:\n\n"
                f"Context:\n{context_text}\n\n"
                f"User Question: {cleaned}"
            )
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Answer using the context below."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=500
            )
            raw_reply = response["choices"][0]["message"]["content"]
            function_call = {}
        else:
            result = gpt_handler.get_reply([{"role":"user", "content": cleaned}])
            raw_reply = (result.get("reply") or "").strip()
            function_call = result.get("function_call") or {}


        # If only the mode was spoken, skip GPT and give mode-specific follow-up
        if mode_changed and not cleaned.strip():
            reply = u"✅ Switched to {} mode. {}".format(mode.capitalize(), _followup_for_mode(mode))
            print(u"🤖 GPT Reply: (skipped; mode switch follow-up)", flush=True)
            memory_manager.add_user_message(username, user_input)
            memory_manager.add_bot_reply(username, reply)
            memory_manager.save_chat_history(username)
            print(">> MODE(upload): active={} changed={} prompt={}".format(mode, True, False), flush=True)
            return jsonify({
                "username": username,
                "reply": reply,
                "user_input": "",
                "function_call": {},
                "active_mode": mode,
                "mode_changed": True,
                "mode_prompt": False
            })

        # Name extraction + persistence
        extracted = _extract_name(cleaned)
        if extracted:
            if username in ("guest","friend"):
                memory_manager.store_user_name(username, extracted)
                memory_manager.migrate_username(username, extracted)
                username = extracted
            else:
                memory_manager.store_user_name(username, extracted)

        known = memory_manager.get_user_name(username)
        if known and ("my name is" not in cleaned.lower()):
            cleaned = "My name is {}. {}".format(known, cleaned)



        if mode_changed:
            confirm = u"✅ Switched to {} mode.".format(mode.capitalize())
            reply = (confirm + " " + raw_reply) if raw_reply else confirm
        else:
            reply = raw_reply or "Sorry, I didn't quite get that."

        print(u"🤖 GPT Reply: {}".format(raw_reply), flush=True)
        if function_call:
            print(u"⚙️ Function call: {}".format(function_call), flush=True)

        memory_manager.add_user_message(username, cleaned)
        memory_manager.add_bot_reply(username, reply if reply else json.dumps(function_call))
        memory_manager.save_chat_history(username)

        print(">> MODE(upload): active={} changed={} prompt={}".format(mode, mode_changed, False), flush=True)

        return jsonify({
            "username": username,
            "reply": reply,
            "user_input": cleaned,
            "function_call": function_call,
            "active_mode": mode,
            "mode_changed": mode_changed,
            "mode_prompt": False
        })

    except Exception as e:
        print("Error in /upload: {}".format(e), flush=True)
        traceback.print_exc()
        # Return a graceful error so robot can re-prompt user
        return jsonify({'error': 'transcription_failed', 'detail': str(e)}), 503
    finally:
        try:
            if os.path.exists(temp_path): os.remove(temp_path)
        except Exception as e:
            print("Temp cleanup failed: {}".format(e), flush=True)

# ----------------- face APIs (unchanged) -----------------
@app.route("/face/recognize", methods=["POST"])
def face_recognize():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"}), 400
    try:
        tol = float((request.form.get("tolerance","0.60") or "0.60").strip())
    except Exception:
        tol = 0.60
    f = request.files["file"]
    filename = secure_filename(f.filename or "cap.jpg")
    path = os.path.join(TEMP_DIR, filename)
    f.save(path)
    try:
        img = face_recognition.load_image_file(path)
        boxes = face_recognition.face_locations(img, model="hog")
        encs = face_recognition.face_encodings(img, boxes)
        if not encs:
            return jsonify({"ok": True, "match": False, "reason": "no_face_detected"})
        target = encs[0]
        names, encs_db = face_store.get_all()
        if not encs_db:
            return jsonify({"ok": True, "match": False, "reason": "db_empty"})
        dists = [np.linalg.norm(e - target) for e in encs_db]
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
            if os.path.exists(path): os.remove(path)
        except: pass

@app.route("/face/enroll", methods=["POST"])
def face_enroll():
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
        img = face_recognition.load_image_file(path)
        boxes = face_recognition.face_locations(img, model="hog")
        encs  = face_recognition.face_encodings(img, boxes)
        if not encs:
            return jsonify({"ok": False, "error": "no_face_detected"}), 200
        face_store.add_encoding(name, encs[0].tolist())
        try:
            memory_manager.initialize_user(name)
            memory_manager.store_user_name(name, name)
            memory_manager.save_chat_history(name)
        except: pass
        return jsonify({"ok": True, "enrolled": name})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if os.path.exists(path): os.remove(path)
        except: pass

@app.route("/face/list", methods=["GET"])
def face_list():
    names, encs = face_store.get_all()
    counts = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return jsonify({"ok": True, "counts": counts, "total_encodings": len(encs)})

if __name__ == "__main__":
    print("Starting Flask server on http://0.0.0.0:5000/", flush=True)
    app.run(host="0.0.0.0", port=5000)
