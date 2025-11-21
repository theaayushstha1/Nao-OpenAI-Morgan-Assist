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

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE")

pinecone_enabled = PINECONE_API_KEY and PINECONE_INDEX_NAME

if pinecone_enabled:
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX_NAME)

BASEDIR = os.path.abspath(os.path.dirname(__file__))
TEMP_DIR = os.path.join(BASEDIR, "tmp_audio")
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    print("‼️ OPENAI_API_KEY not set. Put it in .env or env vars.", flush=True)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

import memory_manager
import gpt_handler

app = Flask(__name__)

MODE_PROMPTS = {
    "general": (
        "You are NAO, a friendly and helpful assistant. Be conversational, concise (2-3 sentences), "
        "and natural. Help with questions, give practical advice, and keep things light and engaging."
    ),
    "therapist": (
        "You are NAO in therapy mode. Be warm, empathetic, and non-judgmental. "
        "Listen actively, validate feelings, and offer gentle guidance. "
        "Keep responses short (2-4 sentences) and supportive. "
        "You are not a licensed therapist - you're a supportive companion."
    ),
    "chatbot": (
        "You are the Morgan State University Computer Science Department assistant. "
        "Answer questions about MSU CS programs, courses, faculty, and resources. "
        "If you don't know, say so politely."
    )
}
VALID_MODES = set(MODE_PROMPTS.keys())

def _prompt_for_mode(mode):
    m = (mode or "general").lower()
    return MODE_PROMPTS.get(m, MODE_PROMPTS["general"])

KEYWORDS = {
    "general": ["general", "normal", "default", "chat", "regular"],
    "therapist": ["therapist", "therapy", "mental", "feelings", "stress", "anxious", "support"],
    "chatbot": ["chatbot", "morgan", "morgan state", "msu", "computer science", "cs department"]
}

def _canonical_mode(s):
    if not s:
        return None
    s = s.strip().lower()
    if s in ("default", "normal", "general", "chat", "regular"):
        return "general"
    if "therap" in s or s in ("mental", "feelings", "stress", "support"):
        return "therapist"
    if "morgan" in s or "chatbot" in s or "msu" in s:
        return "chatbot"
    return s if s in VALID_MODES else None

def _extract_mode_from_text(text):
    if not text:
        return None
    t = text.lower()
    for m, words in KEYWORDS.items():
        for w in words:
            if re.search(r"\b" + re.escape(w) + r"\b", t):
                return m
    return None

def _resolve_mode(user_input, provided_mode):
    base_mode = provided_mode or "general"
    if provided_mode:
        return base_mode, False, False
    detected = _extract_mode_from_text(user_input)
    if detected:
        return detected, (detected != base_mode), False
    return base_mode, False, False

def _extract_name(text):
    if not text:
        return None
    t = text.strip()
    pats = [
        r"(?:\bmy\s+(?:first\s+)?name\s+is\s+)([A-Za-z]+)\b",
        r"(?:\bi\s*am\s+)([A-Za-z]+)\b",
        r"(?:\bi['']m\s+)([A-Za-z]+)\b",
        r"(?:\bthis\s+is\s+)([A-Za-z]+)\b",
        r"(?:\bcall\s+me\s+)([A-Za-z]+)\b",
    ]
    for p in pats:
        m = re.search(p, t, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip().capitalize()
    return None

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
            if fr <= 0:
                return 0.0
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
            print("Whisper retry {}/{} in {:.2f}s due to: {}".format(i + 1, max_tries, wait, e), flush=True)
            time.sleep(wait)
    raise last_err

@app.route("/")
def home():
    return "🤖 NAO Server is up and running!"

@app.route("/test", methods=["GET"])
def test():
    return jsonify({"message": "Test route working!"})

@app.route("/upload", methods=["POST"])
def upload_audio():
    if 'file' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    username = (request.form.get("username") or "friend").strip().lower()
    provided_raw = (request.form.get("mode") or "").strip().lower() or None
    provided_mode = _canonical_mode(provided_raw)

    raw_name = request.files['file'].filename or "input.wav"
    filename = secure_filename(raw_name)
    temp_path = os.path.join(TEMP_DIR, filename)

    try:
        memory_manager.initialize_user(username)
        request.files['file'].save(temp_path)
        print("Saved temp file at {}".format(temp_path), flush=True)

        try:
            _validate_audio_or_503(temp_path)
        except RuntimeError as ve:
            detail = str(ve) or "audio_validation_failed"
            print("Audio validation failed: {}".format(detail), flush=True)
            return jsonify({'error': 'transcription_failed', 'detail': detail}), 503

        transcript = transcribe_with_retry_path(temp_path, WHISPER_MODEL, max_tries=4)

        user_input = (
            getattr(transcript, "text", None)
            or getattr(transcript, "data", {}).get("text", "")
            or (transcript.get("text", "") if isinstance(transcript, dict) else "")
            or ""
        ).strip()
        print(u"📝 Transcribed: {}".format(user_input), flush=True)

        mode, mode_changed, mode_prompt = _resolve_mode(user_input, provided_mode)

        extracted = _extract_name(user_input)
        if extracted:
            if username in ("guest", "friend"):
                memory_manager.store_user_name(username, extracted)
                memory_manager.migrate_username(username, extracted)
                username = extracted
            else:
                memory_manager.store_user_name(username, extracted)

        known = memory_manager.get_user_name(username)
        if known and ("my name is" not in user_input.lower()):
            user_input = "My name is {}. {}".format(known, user_input)

        if mode == "chatbot":
            embedding = get_embedding(user_input)
            matches = query_pinecone(embedding) if embedding else []
            if matches:
                context_text = "\n".join([m["metadata"].get("text", "") for m in matches])
                prompt = (
                    "You are a helpful assistant answering based on Morgan State University Computer Science department info.\n"
                    "Use the below context to answer the user's question:\n\n"
                    f"Context:\n{context_text}\n\n"
                    f"User Question: {user_input}"
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
                result = gpt_handler.get_reply([{"role": "user", "content": user_input}])
                raw_reply = (result.get("reply") or "").strip()
                function_call = result.get("function_call") or {}
        else:
            past = memory_manager.get_chat_history(username)
            system_prompt = _prompt_for_mode(mode)
            messages = [{"role": "system", "content": system_prompt}] + past + [{"role": "user", "content": user_input}]
            result = gpt_handler.get_reply(messages)
            raw_reply = (result.get("reply") or "").strip()
            function_call = result.get("function_call") or {}

        reply = raw_reply or "Sorry, I didn't quite get that."

        print(u"🤖 GPT Reply: {}".format(raw_reply), flush=True)

        memory_manager.add_user_message(username, user_input)
        memory_manager.add_bot_reply(username, reply if reply else json.dumps(function_call))
        memory_manager.save_chat_history(username)

        return jsonify({
            "username": username,
            "reply": reply,
            "user_input": user_input,
            "function_call": function_call,
            "active_mode": mode,
            "mode_changed": mode_changed,
            "mode_prompt": False
        })

    except Exception as e:
        print("Error in /upload: {}".format(e), flush=True)
        traceback.print_exc()
        return jsonify({'error': 'transcription_failed', 'detail': str(e)}), 503
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception as e:
            print("Temp cleanup failed: {}".format(e), flush=True)

@app.route('/therapist_chat', methods=['POST'])
def therapist_chat():
    if 'audio' not in request.files:
        return jsonify({"error": "no audio file"}), 400

    try:
        audio_file = request.files['audio']
        username = (request.form.get('username') or 'Friend').strip()
        mood = (request.form.get('mood') or 'neutral').strip()
        history_json = request.form.get('history', '[]')

        try:
            history = json.loads(history_json)
        except:
            history = []

        filename = secure_filename(audio_file.filename or "therapy_input.wav")
        temp_path = os.path.join(TEMP_DIR, filename)
        audio_file.save(temp_path)

        print(u"[THERAPIST] Processing for {}".format(username), flush=True)

        try:
            _validate_audio_or_503(temp_path)
        except RuntimeError as ve:
            return jsonify({'error': 'audio_invalid', 'detail': str(ve)}), 503

        transcript = transcribe_with_retry_path(temp_path, WHISPER_MODEL, max_tries=3)
        user_input = (
            getattr(transcript, "text", None)
            or getattr(transcript, "data", {}).get("text", "")
            or (transcript.get("text", "") if isinstance(transcript, dict) else "")
            or ""
        ).strip()

        print(u"[THERAPIST] User: {}".format(user_input), flush=True)

        system_prompt = """You are NAO, a caring therapy assistant. Be warm and supportive.

Current user: {username} (mood: {mood})

Guidelines:
- Listen and validate feelings
- Ask gentle follow-up questions
- Offer practical coping strategies when appropriate
- Keep responses conversational (2-3 sentences)
- Be present and empathetic

Respond naturally and supportively.""".format(username=username, mood=mood)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history[-6:])
        messages.append({"role": "user", "content": user_input})

        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=150,
            temperature=0.8
        )

        reply = response['choices'][0]['message']['content'].strip()

        print(u"[THERAPIST] Reply: {}".format(reply), flush=True)

        return jsonify({
            "user_input": user_input,
            "reply": reply,
            "mood": mood,
            "username": username
        })

    except Exception as e:
        print("[THERAPIST] Error: {}".format(e), flush=True)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except:
            pass

if __name__ == "__main__":
    print("Starting Flask server on http://0.0.0.0:5000/", flush=True)
    app.run(host="0.0.0.0", port=5000)
