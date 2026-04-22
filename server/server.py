"""Flask app exposing POST /turn for NAO."""
from __future__ import annotations

import asyncio
import base64
import json as _json
import os
import tempfile
import wave

from flask import Flask, jsonify, request
from openai import OpenAI

from agents import Runner

from server import config, safety, session
from server.agents import pick_initial_agent
from server.topologies import run_topology

app = Flask(__name__)
_client = OpenAI(api_key=config.OPENAI_API_KEY)


# ───────── helpers ─────────

def _validate_wav(path: str) -> bool:
    if os.path.getsize(path) < 400:
        return False
    try:
        with wave.open(path, "rb") as w:
            dur = w.getnframes() / float(w.getframerate() or 1)
            return dur >= 0.12
    except Exception:
        return False


def _transcribe(path: str) -> str:
    with open(path, "rb") as f:
        resp = _client.audio.transcriptions.create(
            model=config.WHISPER_MODEL, file=f,
        )
    return resp.text


def _build_user_message(transcript: str, image_b64: str | None):
    """Build a Runner input. For text-only, return a string. For multimodal,
    return the Responses-API shape: a single user message with typed content
    items (input_text / input_image)."""
    if not image_b64:
        return transcript
    return [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": transcript},
            {"type": "input_image",
             "image_url": f"data:image/jpeg;base64,{image_b64}"},
        ],
    }]


def _run_agent(username: str, hint: str | None, transcript: str,
               image_b64: str | None) -> tuple[str, str, list[dict], bool]:
    agent = pick_initial_agent(username, hint)
    sess = session.get_or_create_session(username)
    ctx = {
        "username": username,
        "actions_queue": [],
        "emotion_log": [],
        "latest_image_b64": image_b64,
        "suppress_image": False,
    }
    message = _build_user_message(transcript, image_b64)
    # SAGE-CBT topology layer. When SAGE_TOPOLOGY=passthrough (default) this
    # is functionally identical to the old direct Runner.run(...) call.
    reply, active, _verdict, _metadata = run_topology(
        agent, message, context=ctx, session=sess
    )
    return (
        reply,
        active,
        list(ctx["actions_queue"]),
        bool(ctx["suppress_image"]),
    )


def _run_recap(username: str) -> str:
    """End-session recap stub — persists a neutral recap."""
    from server.tools.emotion import _recap_session_impl
    ctx = {"username": username, "emotion_log": []}
    return _recap_session_impl(ctx)


# ───────── routes ─────────

@app.get("/health")
def health():
    return jsonify(ok=True)


@app.post("/turn")
def turn():
    username = request.form.get("username") or "guest"
    hint = request.form.get("hint") or None
    end_session = request.form.get("end_session", "").lower() == "true"

    if end_session:
        body = _run_recap(username)
        return jsonify(
            username=username, user_input="", reply=body,
            active_agent="therapist", actions=[], crisis=False,
            suppress_image=False,
        )

    audio = request.files.get("audio")
    image = request.files.get("image")
    if not audio:
        return jsonify(error="missing_audio"), 400

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio.save(tmp.name)
        wav_path = tmp.name
    try:
        if not _validate_wav(wav_path):
            return jsonify(error="invalid_audio"), 503
        transcript = _transcribe(wav_path)
    finally:
        os.unlink(wav_path)

    crisis = safety.crisis_check(transcript)
    if crisis.positive:
        return jsonify(
            username=username, user_input=transcript,
            reply=safety.HOTLINE_REPLY, active_agent="safety",
            actions=[{"name": "change_eye_color", "args": {"color": "white"}}],
            crisis=True, suppress_image=False,
        )

    consent = session.get_camera_consent(username)
    image_b64 = None
    if image and consent:
        image_b64 = base64.b64encode(image.read()).decode("ascii")

    reply, active, actions, suppress = _run_agent(username, hint, transcript, image_b64)

    return jsonify(
        username=username, user_input=transcript, reply=reply,
        active_agent=active, actions=actions, crisis=False,
        suppress_image=suppress,
    )


@app.post("/stream_turn")
def stream_turn():
    """Same inputs as /turn, responds as Server-Sent Events with per-sentence chunks."""
    from flask import Response
    from server.streaming import iter_sentences

    username = request.form.get("username") or "guest"
    hint = request.form.get("hint") or None
    audio = request.files.get("audio")
    image = request.files.get("image")

    if not audio:
        return jsonify(error="missing_audio"), 400

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio.save(tmp.name)
        wav_path = tmp.name
    try:
        if not _validate_wav(wav_path):
            return jsonify(error="invalid_audio"), 503
        transcript = _transcribe(wav_path)
    finally:
        os.unlink(wav_path)

    crisis = safety.crisis_check(transcript)
    consent = session.get_camera_consent(username)
    image_b64 = base64.b64encode(image.read()).decode("ascii") if image and consent else None

    def generate():
        if crisis.positive:
            yield _sse({"type": "sentence", "text": safety.HOTLINE_REPLY})
            yield _sse({"type": "action", "action": {"name": "change_eye_color", "args": {"color": "white"}}})
            yield _sse({"type": "done", "active_agent": "safety", "crisis": True,
                        "suppress_image": False, "user_input": transcript})
            return

        # Run the agent synchronously (non-streaming path is simpler + reliable)
        # and split the final_output into sentences for streaming.
        # Note: Runner.run_streamed() is available but less battle-tested across
        # SDK versions. Using Runner.run() + post-hoc sentence splitting via
        # iter_sentences() is more reliable and still gives NAO sentence-by-sentence
        # playback, because each SSE event is flushed as the generator yields.
        agent = pick_initial_agent(username, hint)
        sess = session.get_or_create_session(username)
        ctx = {
            "username": username, "actions_queue": [], "emotion_log": [],
            "latest_image_b64": image_b64, "suppress_image": False,
        }
        message = _build_user_message(transcript, image_b64)
        # Route through the SAGE-CBT topology layer. The dispatcher still runs
        # synchronously end-to-end; we then split its reply into sentences below
        # to preserve the existing SSE-per-sentence behavior NAO depends on.
        reply, active, _verdict, _metadata = run_topology(
            agent, message, context=ctx, session=sess
        )

        for sent in iter_sentences(iter([reply])):
            yield _sse({"type": "sentence", "text": sent})
        for action in ctx["actions_queue"]:
            yield _sse({"type": "action", "action": action})
        yield _sse({"type": "done", "active_agent": active, "crisis": False,
                    "suppress_image": bool(ctx["suppress_image"]), "user_input": transcript})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(obj) -> str:
    return f"data: {_json.dumps(obj)}\n\n"


@app.post("/greet")
def greet():
    from flask import Response
    image = request.files.get("image")
    if not image:
        return jsonify(error="missing_image"), 400

    image_bytes = image.read()
    # v1: trust the form-provided username; server-side face reco is a follow-up.
    username = request.form.get("username") or "guest"

    if not session.get_proactive_enabled(username):
        def skipped():
            yield _sse({"type": "done", "active_agent": "none",
                        "username": username, "skipped": True})
        return Response(skipped(), mimetype="text/event-stream")

    def generate():
        yield _sse({"type": "recognized", "username": username})
        for sent in _generate_greeting(username, image_bytes):
            yield _sse({"type": "sentence", "text": sent})
        yield _sse({"type": "done", "active_agent": "therapist", "username": username})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _generate_greeting(username: str, image_bytes: bytes):
    """Yield 1-2 sentences of personalized greeting for the user."""
    from server.agents.therapist import build_therapist_agent
    from server.streaming import iter_sentences
    import asyncio, base64

    agent = build_therapist_agent(username)
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    ctx = {
        "username": username, "actions_queue": [], "emotion_log": [],
        "latest_image_b64": image_b64, "suppress_image": False,
    }
    prompt_msg = [{
        "role": "user",
        "content": [
            {"type": "input_text",
             "text": ("The user just walked up. You can see their face. Greet them "
                      "in ONE short sentence, using their name and any relevant "
                      "memory from recent sessions. Do not ask a deep question yet.")},
            {"type": "input_image",
             "image_url": f"data:image/jpeg;base64,{image_b64}"},
        ],
    }]
    result = asyncio.run(Runner.run(agent, prompt_msg, context=ctx))
    yield from iter_sentences(iter([result.final_output]))


if __name__ == "__main__":
    app.run(host=config.SERVER_IP, port=config.SERVER_PORT, debug=False)
