"""Verbatim copies of private helpers from `server/server.py`.

`server/server.py` is FROZEN for the Phase 1 rework — `app_ws.py` (the new
FastAPI WebSocket transport) needs the same WAV validation, voice-gate,
hallucination filters, partial-buffer logic, and agent-runner glue that the
Flask app uses, but importing private (underscored) names directly couples us
to a frozen module. We copy them here so the new transport can evolve without
mutating the legacy module.

If `server/server.py` is ever unfrozen, the right move is to delete this file
and have both transports import from a shared helpers module — but that's a
post-Phase-9 cleanup.

Every symbol here mirrors the implementation in `server/server.py` at commit
f606534. Drift between them is a bug; if you change one, mirror the other.
"""
from __future__ import annotations

import asyncio
import os
import re
import wave

from openai import OpenAI

from server import config, memory, semantic_endpoint, session, vad_silero
from server.agents import pick_initial_agent
from server.topologies import run_topology

_client = OpenAI(api_key=config.OPENAI_API_KEY)


# ───────── active-session bookkeeping ─────────

# Active session id per user (face_id). Created lazily on the first turn after
# wake; closed on end_session=true or exit intent.
_ACTIVE_SESSIONS: dict[str, int] = {}


def ensure_active_session(username: str, hint: str | None) -> int:
    fid = (username or "").strip().lower()
    if not fid:
        return 0
    sid = _ACTIVE_SESSIONS.get(fid)
    if sid:
        return sid
    memory.ensure_user(fid, display_name=username)
    sid = memory.start_session(fid, mode=hint)
    _ACTIVE_SESSIONS[fid] = sid
    return sid


def close_active_session(username: str) -> None:
    """End the active session and kick off async summarization."""
    fid = (username or "").strip().lower()
    # Drop any buffered partial — the user is leaving, no point dragging
    # an unfinished sentence into the next session.
    _PARTIAL_BUFFER.pop(fid, None)
    sid = _ACTIVE_SESSIONS.pop(fid, None)
    if not sid:
        return
    lines: list[str] = []
    try:
        sess = session.get_or_create_session(fid)
        items = asyncio.run(sess.get_items())
        for it in items or []:
            role = it.get("role") if isinstance(it, dict) else None
            content = it.get("content") if isinstance(it, dict) else None
            if isinstance(content, list):
                txt = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict)
                    and p.get("type") in ("input_text", "output_text", "text")
                ).strip()
            elif isinstance(content, str):
                txt = content
            else:
                txt = ""
            if txt and role:
                lines.append("{0}: {1}".format(role, txt))
    except Exception:
        pass
    memory.end_session(sid, summary=None)
    if lines:
        memory.summarize_session_async(sid, lines)


# ───────── WAV validation ─────────

def validate_wav(path: str) -> bool:
    """Reject obviously empty clips. The hallucination filter and self-echo
    detector handle noise rejection downstream.
    """
    if os.path.getsize(path) < 1500:
        return False
    try:
        with wave.open(path, "rb") as w:
            dur = w.getnframes() / float(w.getframerate() or 1)
            return dur >= 0.3
    except Exception:
        return False


# ───────── voice-activity gate ─────────

try:
    import webrtcvad  # type: ignore
    _VAD_AVAILABLE = True
except Exception:
    _VAD_AVAILABLE = False


def has_voice(path: str, aggressiveness: int = 2,
              voiced_ratio_min: float = 0.18) -> bool:
    """True if at least `voiced_ratio_min` of 30 ms frames are detected as speech.

    Layered: Silero VAD is the authoritative gate when it loads. webrtcvad is
    the fallback. Either rejecting drops the clip — but both are permissive on
    internal errors (return True) so we never block traffic on a VAD bug.
    """
    try:
        if not vad_silero.has_voice(path):
            return False
    except Exception:
        pass
    if not _VAD_AVAILABLE:
        return True
    try:
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if ch != 1 or sw != 2 or sr not in (8000, 16000, 32000, 48000):
            return True
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


# ───────── hallucination / echo filters ─────────

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


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


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
        and any(phrase in t for phrase in (
            "how's your day", "hows your day", "i remember you were talking",
        ))
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
    if len(t.split()) <= 1 and len(t) <= 4:
        return True
    return False


# Per-username last-reply cache for self-echo detection. When NAO's mic picks
# up the speaker output, Whisper transcribes our own reply back. We compare
# each new transcript against the last reply and reject if too similar.
LAST_REPLY: dict[str, str] = {}


def _is_self_echo(username: str, transcript: str) -> bool:
    """Reject transcripts that look like our own previous reply (mic feedback)."""
    if not transcript:
        return False
    last = LAST_REPLY.get(username, "")
    if not last:
        return False
    nt = _norm(transcript)
    nl = _norm(last)
    if not nt or not nl:
        return False
    if nt in nl or nl in nt:
        return True
    tt = set(nt.split())
    tl = set(nl.split())
    if not tt or not tl:
        return False
    inter = len(tt & tl)
    union = len(tt | tl)
    return (inter / union) >= 0.6


def transcript_reject_reason(username: str, transcript: str,
                             asking_name: bool = False) -> str | None:
    if asking_name:
        t = (transcript or "").strip()
        if not t:
            return "hallucination_or_noise"
        return None
    if _looks_like_hallucination(transcript):
        return "hallucination_or_noise"
    if _is_self_echo(username, transcript):
        return "self_echo"
    if _is_robot_named_echo(transcript):
        return "robot_named_echo"
    return None


# ───────── partial-transcript buffer ─────────

_PARTIAL_BUFFER: dict[str, str] = {}
_PARTIAL_WAIT_COUNT: dict[str, int] = {}
_PARTIAL_MAX_CHARS = 500
_PARTIAL_MAX_WAIT = 4


def consume_partial(username: str, current: str) -> str:
    """Prepend any buffered partial to the current transcript and clear it."""
    _PARTIAL_WAIT_COUNT.pop(username, None)
    head = _PARTIAL_BUFFER.pop(username, "")
    if not head:
        return current
    joined = (head + " " + current).strip()
    return joined[-_PARTIAL_MAX_CHARS:]


def stash_partial(username: str, text: str) -> None:
    """Stash a partial transcript so the next turn can resume it."""
    if not text or not text.strip():
        return
    _PARTIAL_WAIT_COUNT[username] = _PARTIAL_WAIT_COUNT.get(username, 0) + 1
    _PARTIAL_BUFFER[username] = text.strip()[-_PARTIAL_MAX_CHARS:]


def partial_wait_limit_hit(username: str) -> bool:
    return _PARTIAL_WAIT_COUNT.get(username, 0) >= _PARTIAL_MAX_WAIT


# ───────── ASR ─────────

def transcribe(path: str) -> str:
    if config.USE_DEEPGRAM:
        from server import deepgram_asr
        text = deepgram_asr.transcribe(path)
        if text:
            return text
        print("[transcribe] deepgram returned empty; falling back to whisper",
              flush=True)
    with open(path, "rb") as f:
        resp = _client.audio.transcriptions.create(
            model=config.WHISPER_MODEL, file=f,
            language="en", temperature=0,
        )
    return resp.text


# ───────── agent runner ─────────

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
             "image_url": "data:image/jpeg;base64,{0}".format(image_b64)},
        ],
    }]


def run_agent(username: str, hint: str | None, transcript: str,
              image_b64: str | None) -> tuple[str, str, list[dict], bool]:
    """Run the agent graph synchronously and return
    (reply, active_agent_name, actions_queue, suppress_image).

    Identical semantics to `server.server._run_agent`.
    """
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
    reply, active, _verdict, _metadata = run_topology(
        agent, message, context=ctx, session=sess,
    )
    return (
        reply,
        active,
        list(ctx["actions_queue"]),
        bool(ctx["suppress_image"]),
    )


__all__ = [
    "ensure_active_session",
    "close_active_session",
    "validate_wav",
    "has_voice",
    "transcript_reject_reason",
    "consume_partial",
    "stash_partial",
    "partial_wait_limit_hit",
    "transcribe",
    "run_agent",
    "LAST_REPLY",
]
