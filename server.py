# server.py
# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import json
import traceback
from dotenv import load_dotenv
from flask import Flask, request, jsonify

# ─── LOAD .env ──────────────────────────────────────────────────────────────
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))
# ──────────────────────────────────────────────────────────────────────────────

import openai
openai.api_key = os.getenv("OPENAI_API_KEY")

import gpt_handler
import memory_manager

# ─── CONFIG ──────────────────────────────────────────────────────────────────
TEMP_DIR = "/home/nao/recordings"
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)
# ──────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 NAO Server is up and running!"

@app.route("/test", methods=["GET"])
def test():
    return jsonify({"message": "Test route working!"})

@app.route("/upload", methods=["POST"])
def upload_audio():
    # 1) Ensure we got a file
    if 'file' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    # 2) Initialize or reload this user's memory
    username = request.form.get("username", "guest")
    try:
        memory_manager.initialize_user(username)
    except Exception as e:
        print(f"Warning: memory init failed for user '{username}': {e}")

    audio_file = request.files['file']
    temp_path  = os.path.join(TEMP_DIR, audio_file.filename)

    try:
        # 3) Save incoming audio
        audio_file.save(temp_path)
        print(f"Saved temp file at {temp_path}")

        # 4) Transcribe with Whisper
        with open(temp_path, "rb") as f:
            transcript = openai.audio.transcriptions.create(
                file=f,
                model="whisper-1"
            )
        user_input = transcript.text.strip()
        print(f"📝 Transcribed: {user_input}")

        # 5) Build GPT messages (system prompt + history + new turn)
        system_prompt = (
            "You are NAO, a friendly robot assistant who helps students. "
            "Keep replies short, fun, and a bit humorous—like chatting with a buddy."
        )
        past     = memory_manager.get_chat_history()[1:]
        messages = ([{"role": "system", "content": system_prompt}] +
                    past +
                    [{"role": "user", "content": user_input}])

        # 6) Call GPT
        result        = gpt_handler.get_reply(messages)
        reply         = result.get("reply", "").strip()
        function_call = result.get("function_call")

        print(f"🤖 GPT Reply: {reply}")
        if function_call:
            print(f"⚙️ Function call: {function_call}")

        # 7) Log and persist the conversation
        try:
            memory_manager.add_user_message(user_input)
            log_content = reply if reply else json.dumps(function_call or {})
            memory_manager.add_bot_reply(log_content)
            memory_manager.save_chat_history()
        except Exception as e:
            print("Warning: failed to save memory:", e)

        # 8) Return JSON to NAO
        return jsonify({
            "reply":         reply,
            "user_input":    user_input,
            "function_call": function_call or {}
        })

    except Exception as e:
        print(f"Error in /upload: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

    finally:
        # 9) Clean up
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    print("🔵 Starting Flask server on http://0.0.0.0:5000/")
    app.run(host="0.0.0.0", port=5000)
