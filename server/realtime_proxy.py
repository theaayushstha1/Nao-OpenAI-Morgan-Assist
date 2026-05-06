"""WebSocket proxy: NAO ⇄ OpenAI Realtime API.

NAO opens a WebSocket to /chat_realtime. We open a parallel WebSocket to OpenAI's
Realtime API and forward frames in both directions. The NAO client sends and
receives the same JSON event shapes the Realtime API speaks, so this is a thin
pass-through with auth injection — no transformation.

Why proxy at all rather than have NAO talk to OpenAI directly?
  - NAO is on Python 2.7 with old TLS. The Realtime endpoint requires modern
    TLS 1.2+ and the websocket-client lib on NAO is brittle with that.
  - We don't want OPENAI_API_KEY embedded in NAO's filesystem.

Audio formats:
  - Input: pcm16 mono @ 16 kHz (NAO front mic native sample rate).
  - Output: pcm16 mono @ 24 kHz (Realtime API default; NAO resamples on play).
"""
from __future__ import annotations

import base64
import json
import logging
import threading

import websocket as ws_client  # `websocket-client` package
from flask_sock import Sock

from server import config

logger = logging.getLogger("sage.realtime")

REALTIME_MODEL = config.REALTIME_MODEL
REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"


def _text_frame(msg):
    """Normalize inbound client frames before forwarding to OpenAI.

    NAO runs Python 2.7, where websocket text payloads often arrive at the
    Flask proxy as bytes. If those bytes are forwarded as a binary frame,
    OpenAI closes the Realtime socket with 1007 invalid UTF-8.
    """
    if isinstance(msg, bytes):
        try:
            return msg.decode("utf-8")
        except UnicodeDecodeError:
            # Some NAO/websocket-client combinations send microphone chunks as
            # binary PCM frames. Realtime expects JSON text frames, so wrap the
            # PCM here instead of forwarding binary and triggering close 1007.
            return json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(msg).decode("ascii"),
            })
    return msg


_JUNK_TRANSCRIPTS = {
    "",
    "e",
    "e.",
    "world map",
    "world right now",
    "right now",
    "yip",
}


def _looks_like_junk_transcript(transcript):
    t = (transcript or "").strip().lower()
    if t in _JUNK_TRANSCRIPTS:
        return True
    if len(t) < 3:
        return True
    return False


def _sanitize_client_event(msg):
    """Force manual response creation so empty VAD noise cannot trigger audio."""
    text = _text_frame(msg)
    try:
        event = json.loads(text)
    except Exception:
        return text

    if event.get("type") == "session.update":
        session = event.setdefault("session", {})
        turn_detection = session.get("turn_detection")
        if isinstance(turn_detection, dict):
            turn_detection["create_response"] = False
            turn_detection["interrupt_response"] = True
        return json.dumps(event)
    if event.get("type") == "response.create":
        # The proxy owns response.create after transcript validation. Dropping
        # client-created responses prevents duplicate replies when NAO code is
        # updated but the proxy is already doing the right thing.
        return None

    return text


def _response_create_event():
    return json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["audio", "text"],
        },
    })


def _log_upstream_event(username, msg):
    """Mirror high-value Realtime events into the local server log."""
    try:
        event = json.loads(msg)
    except Exception:
        return

    etype = event.get("type")
    if etype == "conversation.item.input_audio_transcription.completed":
        print(
            "[realtime transcript] username={0!r} text={1!r}".format(
                username, event.get("transcript", ""),
            ),
            flush=True,
        )
    elif etype == "conversation.item.input_audio_transcription.failed":
        print(
            "[realtime transcript failed] username={0!r} error={1!r}".format(
                username, event.get("error"),
            ),
            flush=True,
        )
    elif etype in ("response.created", "response.done", "input_audio_buffer.speech_started",
                   "input_audio_buffer.speech_stopped"):
        print("[realtime event] username={0!r} type={1}".format(username, etype), flush=True)
    elif etype == "error":
        print("[realtime error] username={0!r} event={1!r}".format(username, event), flush=True)

# Default session config sent right after the WebSocket connects. The NAO
# client can override with its own session.update at any time.
DEFAULT_SESSION = {
    "type": "session.update",
    "session": {
        "modalities": ["audio", "text"],
        "instructions": (
            "You are a friendly, concise assistant talking through a NAO robot. "
            "Reply in 1 to 2 short sentences. Be warm and direct."
        ),
        "voice": "alloy",
        "input_audio_format": "pcm16",
        "input_audio_noise_reduction": {"type": "far_field"},
        "input_audio_transcription": {
            "model": config.WHISPER_MODEL,
            "language": "en",
        },
        "output_audio_format": "pcm16",
        "max_response_output_tokens": 80,
        "turn_detection": {
            "type": "server_vad",
            "threshold": config.REALTIME_VAD_THRESHOLD,
            "prefix_padding_ms": config.REALTIME_VAD_PREFIX_MS,
            "silence_duration_ms": config.REALTIME_VAD_SILENCE_MS,
            "create_response": False,
            "interrupt_response": True,
        },
    },
}


def init_app(app):
    """Attach the /chat_realtime WebSocket route to the Flask app."""
    sock = Sock(app)

    @sock.route("/chat_realtime")
    def chat_realtime(client_ws):
        username = "guest"
        try:
            # First message MUST be a JSON header carrying the shared secret
            # when one is configured. Without it the proxy refuses to open
            # the OpenAI socket — otherwise anyone on the LAN can drain the
            # API key. The header may also include {"username": "..."}.
            expected = config.NAO_SHARED_SECRET
            try:
                first = client_ws.receive(timeout=2)
            except Exception:
                first = None
            if expected:
                if not first:
                    try: client_ws.close()
                    except Exception: pass
                    return
                try:
                    meta = json.loads(first)
                except Exception:
                    try: client_ws.close()
                    except Exception: pass
                    return
                if meta.get("secret") != expected:
                    try: client_ws.close()
                    except Exception: pass
                    return
                username = meta.get("username", "guest")
                _open_and_proxy(client_ws, None, username)
                return
            # OPEN mode (no secret configured): preserve the legacy header-
            # or-passthrough behavior so dev workflows keep working.
            if first:
                try:
                    meta = json.loads(first)
                    username = meta.get("username", "guest")
                except Exception:
                    _open_and_proxy(client_ws, first, username)
                    return
            _open_and_proxy(client_ws, None, username)
        except Exception as e:
            logger.exception("realtime route error: %s", e)


def _open_and_proxy(client_ws, first_passthrough, username):
    headers = [
        f"Authorization: Bearer {config.OPENAI_API_KEY}",
        "OpenAI-Beta: realtime=v1",
    ]
    logger.info("[realtime] opening upstream session for %s", username)
    openai_ws = ws_client.create_connection(
        REALTIME_URL, header=headers, timeout=20,
    )
    openai_ws.send(json.dumps(DEFAULT_SESSION))
    if first_passthrough is not None:
        first_text = _sanitize_client_event(first_passthrough)
        if first_text is not None:
            openai_ws.send(first_text, opcode=ws_client.ABNF.OPCODE_TEXT)

    stop = threading.Event()
    send_lock = threading.Lock()

    def send_upstream(text):
        with send_lock:
            openai_ws.send(text, opcode=ws_client.ABNF.OPCODE_TEXT)

    def upstream_to_client():
        try:
            while not stop.is_set():
                try:
                    msg = openai_ws.recv()
                except ws_client.WebSocketTimeoutException:
                    continue
                if not msg:
                    break
                _log_upstream_event(username, msg)
                try:
                    ev = json.loads(msg)
                except Exception:
                    ev = {}
                if ev.get("type") == "conversation.item.input_audio_transcription.completed":
                    transcript = ev.get("transcript") or ""
                    if _looks_like_junk_transcript(transcript):
                        print(
                            "[realtime ignored transcript] username={0!r} text={1!r}".format(
                                username, transcript,
                            ),
                            flush=True,
                        )
                    else:
                        try:
                            send_upstream(_response_create_event())
                            print(
                                "[realtime response.create] username={0!r} text={1!r}".format(
                                    username, transcript,
                                ),
                                flush=True,
                            )
                        except Exception as e:
                            logger.warning("[realtime] response.create failed: %s", e)
                try:
                    client_ws.send(msg)
                except Exception:
                    break
        except Exception as e:
            logger.warning("[realtime] upstream->client closed: %s", e)
        finally:
            stop.set()

    t = threading.Thread(target=upstream_to_client, daemon=True)
    t.start()

    try:
        while not stop.is_set():
            msg = client_ws.receive(timeout=60)
            if msg is None:
                break
            try:
                text = _sanitize_client_event(msg)
                if text is not None:
                    send_upstream(text)
            except UnicodeDecodeError as e:
                logger.warning("[realtime] dropping non-utf8 client frame: %s", e)
            except Exception:
                break
    except Exception as e:
        logger.warning("[realtime] client->upstream closed: %s", e)
    finally:
        stop.set()
        try:
            openai_ws.close()
        except Exception:
            pass
        logger.info("[realtime] session closed for %s", username)
