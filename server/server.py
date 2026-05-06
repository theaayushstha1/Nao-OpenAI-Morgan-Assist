"""Flask app exposing POST /turn for NAO."""
from __future__ import annotations

import asyncio
import base64
import json as _json
import os
import re
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

# Realtime API proxy (WebSocket /chat_realtime). Optional — only loads if the
# extra deps are installed.
try:
    from server import realtime_proxy
    realtime_proxy.init_app(app)
except Exception as _e:
    import logging as _logging
    _logging.getLogger("sage.realtime").warning(
        "realtime proxy not loaded: %s", _e,
    )


# ───────── helpers ─────────

def _validate_wav(path: str) -> bool:
    """Reject obviously empty clips. The hallucination filter and self-echo
    detector handle noise rejection downstream."""
    if os.path.getsize(path) < 1500:
        return False
    try:
        with wave.open(path, "rb") as w:
            dur = w.getnframes() / float(w.getframerate() or 1)
            return dur >= 0.3
    except Exception:
        return False


# Voice-activity gate. Uses webrtcvad if available; otherwise falls back to a
# pure-energy heuristic. Returns True if the clip contains enough voiced
# frames to plausibly be human speech (not just room noise / NAO echo).
try:
    import webrtcvad  # type: ignore
    _VAD_AVAILABLE = True
except Exception:
    _VAD_AVAILABLE = False


def _has_voice(path: str, aggressiveness: int = 2, voiced_ratio_min: float = 0.18) -> bool:
    """True if at least `voiced_ratio_min` of 30ms frames are detected as speech."""
    if not _VAD_AVAILABLE:
        return True  # Fall back to other filters; don't block traffic.
    try:
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if ch != 1 or sw != 2 or sr not in (8000, 16000, 32000, 48000):
            return True  # Unsupported shape — let downstream filters handle it.
        vad = webrtcvad.Vad(aggressiveness)
        frame_ms = 30
        bytes_per_frame = int(sr * frame_ms / 1000) * sw
        if bytes_per_frame == 0 or len(raw) < bytes_per_frame:
            return False
        n_total = 0
        n_voiced = 0
        for i in range(0, len(raw) - bytes_per_frame + 1, bytes_per_frame):
            frame = raw[i:i + bytes_per_frame]
            n_total += 1
            try:
                if vad.is_speech(frame, sr):
                    n_voiced += 1
            except Exception:
                continue
        if n_total == 0:
            return False
        return (n_voiced / n_total) >= voiced_ratio_min
    except Exception:
        return True


# Common Whisper-on-silence hallucinations. If the transcript is one of these
# (after lowercase/strip), treat as no-speech and do not run the agent.
_WHISPER_HALLUCINATIONS = {
    "thanks for watching",
    "thanks for watching!",
    "thank you for watching",
    "thank you for watching.",
    "please subscribe",
    "subscribe to my channel",
    "i'm here to listen and support you",
    "i'm here to listen and support you.",
    "i am here to listen and support you",
    "how can i help you",
    "how can i help you?",
    "how can i help you today",
    "how can i help you today?",
    "how are you doing today",
    "how are you doing today?",
    "how's your day going",
    "how's your day going so far",
    "how's your day going so far?",
    "hello",
    "hello.",
    "hi.",
    "hey.",
    "bye.",
    "thank you.",
    "thanks.",
    "you",
    "you.",
    "okay",
    "okay.",
    "ok.",
    ".",
    "",
    "hey there it's great to see you again",
    "hey there, it's great to see you again.",
    "hey there it's great to see you again.",
    "hey there, it's great to see you again",
    "hey there, it's good to see you again.",
    "hey there it's good to see you again.",
    "i remember you were talking about your studies last time.",
    "hey there it's great to see you again how's your day going so far",
    "hey there it's great to see you again hows your day going so far",
    "hey there it's good to see you again i remember you were talking about your studies last time",
    "good afternoon",
    "good afternoon.",
    "good morning",
    "good morning.",
    "good evening",
    "good evening.",
    # Defensive: any "talking to a robot named NAO" Whisper echo
}

_ASR_NOISE_FRAGMENTS = {
    "e",
    "world map",
    "world right now",
    "right now",
    "yip",
}

_ROBOT_ECHO_PHRASES = (
    "hey there",
    "great to see you again",
    "good to see you again",
    "how's your day going",
    "hows your day going",
    "i remember you were talking",
    "i'm here to listen and support you",
    "i am here to listen and support you",
)


def _is_robot_named_echo(text: str) -> bool:
    t = (text or "").strip().lower()
    return "talking to a robot named nao" in t or "to a robot named nao" in t


def _clean_asr_text(text: str) -> str:
    return re.sub(r"\s+", " ", _norm(text)).strip()


def _looks_like_robot_greeting_echo(text: str) -> bool:
    """Reject our own stock greetings when the mic hears NAO's speaker."""
    t = _clean_asr_text(text)
    if not t:
        return False
    hits = sum(1 for phrase in _ROBOT_ECHO_PHRASES if phrase in t)
    return hits >= 2 or (
        t.startswith("hey there")
        and any(phrase in t for phrase in ("how's your day", "hows your day", "i remember you were talking"))
    )


def _looks_like_hallucination(text: str) -> bool:
    """True if Whisper output matches a known silence-hallucination pattern."""
    t = (text or "").strip().lower()
    if not t:
        return True
    if t in _WHISPER_HALLUCINATIONS:
        return True
    nt = _clean_asr_text(t)
    if nt in _WHISPER_HALLUCINATIONS or nt in _ASR_NOISE_FRAGMENTS:
        return True
    if _looks_like_robot_greeting_echo(t):
        return True
    # Single short token (often "you", "yeah", "uh") — likely noise
    if len(t.split()) <= 1 and len(t) <= 4:
        return True
    return False


# Per-username last-reply cache for self-echo detection. When NAO's mic picks up
# the speaker output, Whisper transcribes our own reply back. We compare each
# new transcript against the last reply and reject if too similar.
_LAST_REPLY: dict[str, str] = {}


def _norm(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


def _is_self_echo(username: str, transcript: str) -> bool:
    """Reject transcripts that look like our own previous reply (mic feedback)."""
    if not transcript:
        return False
    last = _LAST_REPLY.get(username, "")
    if not last:
        return False
    nt = _norm(transcript)
    nl = _norm(last)
    if not nt or not nl:
        return False
    # Substring containment in either direction (tolerant of small Whisper drifts)
    if nt in nl or nl in nt:
        return True
    # Token Jaccard similarity over short windows
    tt = set(nt.split())
    tl = set(nl.split())
    if not tt or not tl:
        return False
    inter = len(tt & tl)
    union = len(tt | tl)
    return (inter / union) >= 0.6


def _transcript_reject_reason(username: str, transcript: str) -> str | None:
    if _looks_like_hallucination(transcript):
        return "hallucination_or_noise"
    if _is_self_echo(username, transcript):
        return "self_echo"
    if _is_robot_named_echo(transcript):
        return "robot_named_echo"
    return None


def _reject_silence_json(username: str, reason: str, transcript: str):
    print(
        "[transcript rejected] username={0!r} reason={1} text={2!r}".format(
            username, reason, transcript,
        ),
        flush=True,
    )
    return jsonify(
        username=username, user_input="",
        reply="", active_agent="silence",
        actions=[], crisis=False, suppress_image=False,
    )


def _reject_silence_sse(username: str, reason: str, transcript: str):
    from flask import Response

    print(
        "[transcript rejected] username={0!r} reason={1} text={2!r}".format(
            username, reason, transcript,
        ),
        flush=True,
    )

    def silent():
        yield _sse({"type": "done", "active_agent": "silence", "crisis": False,
                    "suppress_image": False, "user_input": ""})

    return Response(silent(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _transcribe(path: str) -> str:
    if config.USE_DEEPGRAM:
        from server import deepgram_asr
        text = deepgram_asr.transcribe(path)
        if text:
            return text
        # Empty/failure -> fall through to Whisper so we never silently drop a turn.
        print("[transcribe] deepgram returned empty; falling back to whisper", flush=True)
    with open(path, "rb") as f:
        resp = _client.audio.transcriptions.create(
            model=config.WHISPER_MODEL, file=f,
            # English-only + temperature 0 reduces hallucinations on silence.
            # No `prompt`: Whisper echoes the prompt verbatim on near-silent audio.
            language="en", temperature=0,
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


@app.post("/tts")
def tts_clone():
    """Synthesize arbitrary text with the configured ElevenLabs voice clone.
    NAO uses this for system speech (greetings, prompts, confirmations) so
    everything sounds like the user, not the onboard robot voice.
    Returns MP3 bytes; 503 if ElevenLabs isn't configured / synth fails.
    """
    text = (request.form.get("text") or request.json and request.json.get("text") or "").strip() if request.is_json else (request.form.get("text") or "").strip()
    if not text:
        return jsonify(error="missing_text"), 400
    if not config.USE_ELEVENLABS:
        return jsonify(error="elevenlabs_not_configured"), 503
    from server.elevenlabs_tts import synthesize as _el_synth
    mp3 = _el_synth(text)
    if not mp3:
        return jsonify(error="synth_failed"), 503
    from flask import Response as _FResp
    return _FResp(mp3, mimetype="audio/mpeg")


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
        if not _has_voice(wav_path):
            return jsonify(
                username=username, user_input="",
                reply="", active_agent="silence",
                actions=[], crisis=False, suppress_image=False,
            )
        transcript = _transcribe(wav_path)
    finally:
        os.unlink(wav_path)

    reason = _transcript_reject_reason(username, transcript)
    if reason:
        return _reject_silence_json(username, reason, transcript)

    print(
        "[transcript accepted] username={0!r} hint={1!r} text={2!r}".format(
            username, hint, transcript,
        ),
        flush=True,
    )

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
    _LAST_REPLY[username] = reply

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
        if not _has_voice(wav_path):
            def silent_vad():
                yield _sse({"type": "done", "active_agent": "silence", "crisis": False,
                            "suppress_image": False, "user_input": ""})
            return Response(silent_vad(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        transcript = _transcribe(wav_path)
    finally:
        os.unlink(wav_path)

    reason = _transcript_reject_reason(username, transcript)
    if reason:
        return _reject_silence_sse(username, reason, transcript)

    print(
        "[transcript accepted] username={0!r} hint={1!r} text={2!r}".format(
            username, hint, transcript,
        ),
        flush=True,
    )

    crisis = safety.crisis_check(transcript)
    consent = session.get_camera_consent(username)
    image_b64 = base64.b64encode(image.read()).decode("ascii") if image and consent else None

    use_true_streaming = (config.SAGE_TOPOLOGY or "passthrough").strip().lower() == "passthrough"

    def generate():
        if crisis.positive:
            yield _sse({"type": "sentence", "text": safety.HOTLINE_REPLY})
            yield _sse({"type": "action", "action": {"name": "change_eye_color", "args": {"color": "white"}}})
            yield _sse({"type": "done", "active_agent": "safety", "crisis": True,
                        "suppress_image": False, "user_input": transcript})
            return

        agent = pick_initial_agent(username, hint)
        sess = session.get_or_create_session(username)
        ctx = {
            "username": username, "actions_queue": [], "emotion_log": [],
            "latest_image_b64": image_b64, "suppress_image": False,
        }
        message = _build_user_message(transcript, image_b64)

        if use_true_streaming:
            # True token-level streaming: yield each sentence the moment it's
            # complete, so NAO starts speaking within ~1s instead of waiting
            # for the whole reply.
            yield from _stream_passthrough(agent, message, ctx, sess, transcript, username)
            return

        # SAGE-CBT topology path (e.g., supervisor_veto): runs the agent fully
        # so SafetyAgent can review the proposal before we emit it.
        reply, active, _verdict, _metadata = run_topology(
            agent, message, context=ctx, session=sess
        )
        _LAST_REPLY[username] = reply
        for sent in iter_sentences(iter([reply])):
            mp3 = None
            if config.USE_ELEVENLABS:
                from server.elevenlabs_tts import synthesize as _el_synth
                mp3 = _el_synth(sent)
            if mp3:
                # Voice-cloned MP3 — NAO plays via ALAudioPlayer.
                yield _sse({
                    "type": "audio",
                    "format": "mp3",
                    "text": sent,  # transcript for logs / barge resume
                    "b64": base64.b64encode(mp3).decode("ascii"),
                })
            else:
                # Onboard TTS fallback (no ElevenLabs, or synth failed).
                yield _sse({"type": "sentence", "text": sent})
        for action in ctx["actions_queue"]:
            yield _sse({"type": "action", "action": action})
        yield _sse({"type": "done", "active_agent": active, "crisis": False,
                    "suppress_image": bool(ctx["suppress_image"]), "user_input": transcript})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_passthrough(agent, message, ctx, sess, transcript, username):
    """Bridge agents-SDK streaming (async) into our sync SSE generator.

    Strategy: spawn a background thread that drives the asyncio loop; it
    pushes text deltas into a queue. Main thread pulls deltas, splits into
    sentences via iter_sentences, and yields SSE events. When the loop ends,
    a sentinel flushes any tail and we emit `done`.
    """
    import queue
    import threading
    from agents import Runner
    from openai.types.responses import ResponseTextDeltaEvent
    from server.streaming import iter_sentences

    q: "queue.Queue" = queue.Queue()
    SENTINEL = object()
    state = {"final_text": "", "active_agent": getattr(agent, "name", "agent"), "error": None}

    async def driver():
        try:
            run = Runner.run_streamed(agent, message, context=ctx, session=sess)
            async for ev in run.stream_events():
                if ev.type == "raw_response_event":
                    data = ev.data
                    if isinstance(data, ResponseTextDeltaEvent):
                        q.put(data.delta)
                elif ev.type == "agent_updated_stream_event":
                    new_agent = getattr(ev, "new_agent", None)
                    if new_agent is not None:
                        state["active_agent"] = getattr(new_agent, "name", state["active_agent"])
            try:
                state["final_text"] = run.final_output_as(str)
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            state["error"] = repr(e)
        finally:
            q.put(SENTINEL)

    def thread_target():
        import asyncio
        asyncio.run(driver())

    t = threading.Thread(target=thread_target, daemon=True)
    t.start()

    def chunks():
        while True:
            item = q.get()
            if item is SENTINEL:
                return
            yield item

    sent_count = 0
    for sent in iter_sentences(chunks()):
        sent_count += 1
        print("[stream sentence] username={0!r} text={1!r}".format(username, sent), flush=True)
        mp3 = None
        if config.USE_ELEVENLABS:
            from server.elevenlabs_tts import synthesize as _el_synth
            mp3 = _el_synth(sent)
        if mp3:
            yield _sse({
                "type": "audio", "format": "mp3", "text": sent,
                "b64": base64.b64encode(mp3).decode("ascii"),
            })
        else:
            yield _sse({"type": "sentence", "text": sent})

    t.join(timeout=2.0)
    final = state["final_text"]
    if final:
        _LAST_REPLY[username] = final
    if sent_count == 0:
        if state["error"]:
            print("[stream error] username={0!r} error={1}".format(username, state["error"]), flush=True)
            try:
                reply, active, _verdict, _metadata = run_topology(
                    agent, message, context=ctx, session=sess
                )
                state["active_agent"] = active
                final = reply
                _LAST_REPLY[username] = reply
            except Exception as e:  # noqa: BLE001
                print("[stream fallback error] username={0!r} error={1!r}".format(username, repr(e)), flush=True)
                final = "I hit a server error. Please try again."
        if final:
            for sent in iter_sentences(iter([final])):
                print("[stream fallback sentence] username={0!r} text={1!r}".format(username, sent), flush=True)
                mp3 = None
                if config.USE_ELEVENLABS:
                    from server.elevenlabs_tts import synthesize as _el_synth
                    mp3 = _el_synth(sent)
                if mp3:
                    yield _sse({
                        "type": "audio", "format": "mp3", "text": sent,
                        "b64": base64.b64encode(mp3).decode("ascii"),
                    })
                else:
                    yield _sse({"type": "sentence", "text": sent})
    for action in ctx["actions_queue"]:
        yield _sse({"type": "action", "action": action})
    yield _sse({"type": "done", "active_agent": state["active_agent"], "crisis": False,
                "suppress_image": bool(ctx["suppress_image"]), "user_input": transcript})


def _maybe_emit_voiceclone_audio(text: str):
    """If ElevenLabs is configured, generate a voice-cloned MP3 for the
    sentence and yield an SSE 'audio' event with base64-encoded bytes.
    NAO plays this through ALAudioPlayer instead of its onboard TTS.

    Returns nothing — this is a side-effect helper used inside SSE generators.
    Caller does `yield from _maybe_emit_voiceclone_audio(sent)` if it's a
    generator helper, otherwise `yield _sse(...)` directly. We use the
    direct-yield pattern below since we already yield the sentence first.
    """
    pass  # placeholder, real impl is the generator below


def _sse(obj) -> str:
    return f"data: {_json.dumps(obj)}\n\n"


@app.post("/greet")
def greet():
    from flask import Response

    def skipped(username: str, reason: str):
        def generate():
            yield _sse({"type": "done", "active_agent": "none",
                        "username": username, "skipped": True,
                        "reason": reason})
        return Response(generate(), mimetype="text/event-stream")

    image = request.files.get("image")
    if not image:
        return jsonify(error="missing_image"), 400

    image_bytes = image.read()
    # v1: trust the form-provided username; server-side face reco is a follow-up.
    username = request.form.get("username") or "guest"

    if not config.PROACTIVE_GREET_ENABLED:
        return skipped(username, "proactive_disabled")

    if not session.get_proactive_enabled(username):
        return skipped(username, "user_disabled")

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
