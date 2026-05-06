# -*- coding: utf-8 -*-
"""Realtime chat mode for NAO using the OpenAI Realtime API.

Architecture
------------
NAO ⇄ Flask server (/chat_realtime WS) ⇄ OpenAI Realtime API

The Flask server is a thin pass-through that injects the API key and forwards
JSON frames in both directions. Audio is base64 PCM16 mono — 16 kHz from NAO
to the server (mic native rate), 24 kHz from the server back to NAO.

Loop
----
  - Capture 250 ms of mic audio at a time via ALAudioRecorder (file-based; on
    Python 2.7 the streaming-callback ALModule pattern is fragile, and 250 ms
    chunks are short enough to keep latency under ~1 s end-to-end).
  - Send each chunk as `input_audio_buffer.append` (base64 pcm16).
  - Server-side VAD on the Realtime side decides when the user stopped; the
    proxy/client fires `response.create` only after a valid transcript arrives.
  - On `response.audio.delta` we accumulate pcm16 bytes and play once we have
    enough to write a WAV. We play each chunk via ALAudioPlayer.
  - Head-touch sends `response.cancel` and clears the playback queue, so the
    user can interrupt at any time.

Exit
----
  - User says one of the EXIT phrases (transcribed via the Realtime API's
    own built-in transcription that arrives as `conversation.item.input_audio
    _transcription.completed`) and we close the socket cleanly.
"""
from __future__ import print_function

import base64
import json
import os
import struct
import threading
import time
import traceback
import wave

try:
    import audioop
except Exception:
    audioop = None

from naoqi import ALProxy
import websocket as ws_client

import config
from utils import intent

try:
    unicode
except NameError:
    unicode = str


def _cfg(name, default):
    return getattr(config, name, default)


def _cfg_bool(name, default):
    val = getattr(config, name, default)
    if isinstance(val, str):
        return val == "1" or val.lower() in ("true", "yes", "on")
    return bool(val)


def _cfg_int(name, default):
    try:
        return int(getattr(config, name, default))
    except Exception:
        return int(default)


def _cfg_float(name, default):
    try:
        return float(getattr(config, name, default))
    except Exception:
        return float(default)


SERVER_WS = "ws://{0}:{1}/chat_realtime".format(
    _cfg("SERVER_IP", "172.20.95.118"),
    _cfg_int("SERVER_PORT", 5050),
)

# Capture settings. NAO records at 16 kHz; Realtime pcm16 input is 24 kHz, so
# chunks are resampled before being appended to the input audio buffer.
CAPTURE_SAMPLE_RATE = 16000           # NAO front mic
API_AUDIO_SAMPLE_RATE = 24000         # OpenAI Realtime pcm16 input
CAPTURE_CHUNK_MS    = 500             # 500ms is the sweet spot — 250ms gave
                                       # 0-byte files; 1000ms wastes too much
                                       # latency. Each chunk lags audio by up
                                       # to chunk_ms before reaching OpenAI.
CAPTURE_CHANNELS    = (0, 0, 1, 0)    # front mic only

# Playback settings — Realtime API outputs pcm16 24 kHz mono by default.
PLAYBACK_SAMPLE_RATE = 24000
PLAYBACK_FLUSH_BYTES = int(PLAYBACK_SAMPLE_RATE * 2 * 0.60)

# Local scratch path for capture/playback files.
SCRATCH_DIR = "/tmp/nao_realtime"


# ───────── helpers ─────────

def _ensure_dir(p):
    if not os.path.exists(p):
        try:
            os.makedirs(p)
        except Exception:
            pass


def _write_wav_pcm16(path, pcm_bytes, sample_rate, channels=1):
    # Py2.7 on NAO: wave.open does not implement context manager.
    w = wave.open(path, "wb")
    try:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)
    finally:
        w.close()


def _read_pcm_from_wav(path):
    w = wave.open(path, "rb")
    try:
        return w.readframes(w.getnframes())
    finally:
        w.close()


def _pcm_rms(pcm_bytes):
    if not pcm_bytes:
        return 0
    if audioop is not None:
        try:
            return int(audioop.rms(pcm_bytes, 2))
        except Exception:
            return 0
    try:
        sample_count = min(len(pcm_bytes) // 2, 8000)
        if sample_count <= 0:
            return 0
        vals = struct.unpack("<{0}h".format(sample_count), pcm_bytes[:sample_count * 2])
        total = sum(v * v for v in vals)
        return int((total / float(sample_count)) ** 0.5)
    except Exception:
        return 0


def _send_json(conn, payload):
    text = json.dumps(payload)
    if not isinstance(text, unicode):
        text = text.decode("utf-8")
    conn.send(text)


def _recoverable_realtime_error(ev):
    err = ev.get("error") or {}
    code = str(err.get("code") or "").lower()
    message = str(err.get("message") or "").lower()
    text = code + " " + message
    # These happen during local interrupts/echo cleanup and should not exit
    # chat mode. They usually mean there was nothing active left to cancel or
    # the input buffer was already empty.
    recoverable_bits = (
        "cancel", "no active response", "response already done",
        "input_audio_buffer", "input buffer", "buffer is empty",
        "already cleared",
    )
    return any(bit in text for bit in recoverable_bits)


class _EchoGate(object):
    """Suppress NAO speaker bleed from being sent back as user speech."""

    def __init__(self, enabled=True, acoustic_barge_enabled=False,
                 threshold=9500, sustain_ms=600, deadzone_ms=1200, tail_ms=900):
        self.enabled = bool(enabled)
        self.acoustic_barge_enabled = bool(acoustic_barge_enabled)
        self.threshold = float(threshold)
        self.sustain_ms = int(sustain_ms)
        self.deadzone_ms = int(deadzone_ms)
        self.tail_s = float(tail_ms) / 1000.0
        self.above_since = None

    def check(self, player, rms, now=None, assistant_active=False):
        """Return (allow_mic, barge_accepted)."""
        now = time.time() if now is None else now
        if not self.enabled:
            self.above_since = None
            return True, False
        if not assistant_active and not player.is_playing_or_recent(self.tail_s, now=now):
            self.above_since = None
            return True, False
        if not self.acoustic_barge_enabled:
            self.above_since = None
            return False, False
        if player.playback_age_ms(now=now) < self.deadzone_ms:
            self.above_since = None
            return False, False
        if rms >= self.threshold:
            if self.above_since is None:
                self.above_since = now
            if (now - self.above_since) * 1000.0 >= self.sustain_ms:
                self.above_since = None
                return True, True
            return False, False
        self.above_since = None
        return False, False


# ───────── playback queue ─────────

class _PlayerThread(threading.Thread):
    """Pop pcm16 chunks off a queue and play them sequentially via ALAudioPlayer."""

    def __init__(self, nao_ip, port):
        threading.Thread.__init__(self)
        self.daemon = True
        self.player = ALProxy("ALAudioPlayer", nao_ip, port)
        self.queue = []
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.stopped = False
        self.cleared = threading.Event()
        self.playing = False
        self.playback_started_at = 0.0
        self.playback_finished_at = 0.0
        _ensure_dir(SCRATCH_DIR)

    def enqueue(self, pcm_bytes):
        if not pcm_bytes:
            return
        print("[realtime] queue playback bytes={0}".format(len(pcm_bytes)))
        with self.cv:
            self.queue.append(pcm_bytes)
            if self.playback_finished_at <= 0.0:
                self.playback_finished_at = time.time()
            self.cv.notify()

    def clear(self):
        """Drop pending chunks and stop whatever is playing now (interrupt)."""
        with self.cv:
            self.queue = []
            self.cv.notify()
        try:
            self.player.stopAll()
        except Exception:
            pass
        with self.cv:
            self.playing = False
            self.playback_finished_at = time.time()

    def stop(self):
        with self.cv:
            self.stopped = True
            self.cv.notify()

    def is_playing_or_recent(self, tail_s, now=None):
        now = time.time() if now is None else now
        with self.cv:
            if self.playing or self.queue:
                return True
            if self.playback_finished_at <= 0.0:
                return False
            return (now - self.playback_finished_at) < tail_s

    def playback_age_ms(self, now=None):
        now = time.time() if now is None else now
        with self.cv:
            if self.playback_started_at <= 0.0:
                return 0
            return int((now - self.playback_started_at) * 1000.0)

    def run(self):
        i = 0
        while True:
            with self.cv:
                while not self.queue and not self.stopped:
                    self.cv.wait(timeout=0.5)
                if self.stopped and not self.queue:
                    return
                pcm = self.queue.pop(0) if self.queue else None
            if pcm is None:
                continue
            path = os.path.join(SCRATCH_DIR, "play_{0}.wav".format(i))
            i = (i + 1) % 1000
            try:
                _write_wav_pcm16(path, pcm, PLAYBACK_SAMPLE_RATE)
                print("[realtime] playFile {0} bytes={1}".format(path, len(pcm)))
                with self.cv:
                    self.playing = True
                    self.playback_started_at = time.time()
                try:
                    self.player.playFile(path)
                finally:
                    with self.cv:
                        self.playing = False
                        self.playback_finished_at = time.time()
            except Exception as e:
                print("[realtime] playback error:", e)
                with self.cv:
                    self.playing = False
                    self.playback_finished_at = time.time()


# ───────── recorder thread ─────────

class _RecorderThread(threading.Thread):
    """Record short chunks from the NAO front mic and push them to a callback."""

    def __init__(self, nao_ip, port, on_chunk, stop_event):
        threading.Thread.__init__(self)
        self.daemon = True
        self.nao_ip = nao_ip
        self.port = port
        self.on_chunk = on_chunk
        self.stop_event = stop_event
        self.rec = ALProxy("ALAudioRecorder", nao_ip, port)
        _ensure_dir(SCRATCH_DIR)

    def run(self):
        print("[recorder] started")
        chunk_seconds = CAPTURE_CHUNK_MS / 1000.0
        i = 0
        try:
            self.rec.stopMicrophonesRecording()
        except Exception as e:
            print("[recorder] initial stop error:", e)

        first_iter = True
        while not self.stop_event.is_set():
            path = os.path.join(SCRATCH_DIR, "cap_{0}.wav".format(i))
            i = (i + 1) % 1000
            try:
                self.rec.startMicrophonesRecording(
                    path, "wav", CAPTURE_SAMPLE_RATE, CAPTURE_CHANNELS,
                )
                time.sleep(chunk_seconds)
                self.rec.stopMicrophonesRecording()
                pcm = _read_pcm_from_wav(path)
                if first_iter:
                    print("[recorder] first chunk: {0} bytes from {1}".format(
                        len(pcm) if pcm else 0, path))
                    first_iter = False
                if pcm:
                    self.on_chunk(pcm)
                else:
                    if i < 3:
                        print("[recorder] empty pcm from {0}".format(path))
            except Exception as e:
                print("[recorder] capture error:", e)
                time.sleep(0.1)
            finally:
                try:
                    os.unlink(path)
                except Exception:
                    pass
        print("[recorder] stopped")


# ───────── main loop ─────────

EXIT_PHRASES = (
    "exit realtime", "stop realtime", "quit realtime",
    "exit chat", "goodbye", "bye nao",
)


# Mode-specific instructions sent right after the proxy's default session.
_MODE_INSTRUCTIONS = {
    "chat": (
        "You are a friendly, casual assistant talking through a NAO robot. "
        "Keep replies very short — one or two sentences. Be warm and direct. Do NOT greet the user, do NOT say 'hello' or 'how can I help you' — just answer their actual question directly. If you don't know the answer, say so honestly. Always respond in English regardless of how the user's audio is transcribed; if the transcription looks non-English, ask the user to repeat in English."
    ),
    "morgan": (
        "You are a helpful Morgan State University assistant talking through "
        "a NAO robot. Help with questions about the CS department, courses, "
        "campus life, and student resources. Keep replies concise — one or "
        "two sentences. If you don't know something specific, say so. Do NOT greet the user, do NOT say 'hello' or 'how can I help you' — just answer their actual question directly. If you don't know the answer, say so honestly. Always respond in English regardless of how the user's audio is transcribed; if the transcription looks non-English, ask the user to repeat in English."
    ),
    "therapy": (
        "You are an empathetic therapist talking through a NAO robot, using "
        "CBT (Cognitive Behavioral Therapy) techniques. First mirror the "
        "user's emotion in one sentence. Then ask one open question or gently "
        "name a thinking pattern (catastrophizing, all-or-nothing, etc.). "
        "Never give advice without empathy first. Keep each turn under three "
        "sentences. If the user mentions self-harm, suicide, or wanting to "
        "die, stop the therapy frame and tell them to call or text 988."
    ),
    "skills": (
        "You are a helpful utility assistant talking through a NAO robot. "
        "Help with time, weather, reminders, and quick facts. Keep replies "
        "to one short sentence."
    ),
}


def _instructions_for(hint):
    return _MODE_INSTRUCTIONS.get(hint or "chat", _MODE_INSTRUCTIONS["chat"])


def run(qi_session, initial_hint=None):
    """Open the proxy session, stream audio both ways, exit on cue."""
    raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
    memory = ALProxy("ALMemory", config.NAO_IP, config.NAO_PORT)
    leds = ALProxy("ALLeds", config.NAO_IP, config.NAO_PORT)

    print("[realtime] connecting to", SERVER_WS)
    try:
        # No socket timeout — recv() should block indefinitely until a frame
        # arrives. We close cleanly via stop_event/conn.close() in finally.
        conn = ws_client.create_connection(SERVER_WS, timeout=None)
    except Exception as e:
        print("[realtime] connect failed:", e)
        try:
            raw_tts.say("I can't reach the realtime server.")
        except Exception:
            pass
        return

    print("[realtime] connected; hint={0}".format(initial_hint))
    try:
        leds.fadeRGB("FaceLeds", 0.0, 0.8, 1.0, 0.1)  # cyan = realtime socket up
    except Exception:
        pass

    # First frame must carry the shared secret when the server enforces it,
    # otherwise the proxy closes the socket before opening the OpenAI side.
    # See server/realtime_proxy.py:_require_shared_secret.
    _hdr = {"username": "guest"}
    try:
        import config as _cfg
        if getattr(_cfg, "NAO_SHARED_SECRET", ""):
            _hdr["secret"] = _cfg.NAO_SHARED_SECRET
    except Exception:
        pass
    try:
        _send_json(conn, _hdr)
        print("[realtime] header sent")
    except Exception as e:
        print("[realtime] header send failed:", e)

    # Override the proxy's default with mode-specific instructions and repeat
    # the critical audio/VAD fields so the NAO client remains self-contained if
    # the server default changes.
    try:
        _send_json(conn, {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "instructions": _instructions_for(initial_hint),
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "input_audio_noise_reduction": {"type": "far_field"},
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gpt-4o-mini-transcribe",
                    "language": "en",
                },
                "max_response_output_tokens": 80,
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": _cfg_float("REALTIME_VAD_THRESHOLD", 0.30),
                    "prefix_padding_ms": _cfg_int("REALTIME_VAD_PREFIX_MS", 200),
                    "silence_duration_ms": _cfg_int("REALTIME_VAD_SILENCE_MS", 200),
                    "create_response": False,
                    "interrupt_response": True,
                },
            },
        })
        print("[realtime] session.update sent (full config)")
    except Exception as e:
        print("[realtime] session.update failed:", e)

    player = _PlayerThread(config.NAO_IP, config.NAO_PORT)
    player.start()
    echo_gate = _EchoGate(
        enabled=_cfg_bool("REALTIME_ECHO_GATE_ENABLED", True),
        acoustic_barge_enabled=_cfg_bool("REALTIME_ACOUSTIC_BARGE_ENABLED", False),
        threshold=_cfg_float("REALTIME_BARGE_THRESHOLD", 9500),
        sustain_ms=_cfg_int("REALTIME_BARGE_SUSTAIN_MS", 600),
        deadzone_ms=_cfg_int("REALTIME_BARGE_DEADZONE_MS", 1200),
        tail_ms=_cfg_int("REALTIME_ECHO_TAIL_MS", 1400),
    )

    stop_event = threading.Event()

    # State for ratecv: must persist across calls so resample doesn't glitch.
    resample_state = [None]
    chunk_stats = {
        "count": 0,
        "bytes": 0,
        "last_rms": 0,
        "max_rms": 0,
        "last_log": time.time(),
        "suppressed_echo": 0,
        "last_echo_log": time.time(),
        "last_input_clear": 0.0,
    }
    assistant_active_until = [0.0]
    suppress_echo_response = [False]
    response_active = [False]
    interrupt_requested = [False]
    last_interrupt_at = [0.0]  # any error within 2s of this is treated recoverable

    def mark_assistant_active(extra_s=None):
        extra = echo_gate.tail_s if extra_s is None else float(extra_s)
        until = time.time() + extra
        if until > assistant_active_until[0]:
            assistant_active_until[0] = until

    def assistant_is_active(now=None):
        now = time.time() if now is None else now
        return now < assistant_active_until[0] or player.is_playing_or_recent(echo_gate.tail_s, now=now)

    def clear_input_buffer(reason):
        try:
            _send_json(conn, {"type": "input_audio_buffer.clear"})
            print("[realtime] cleared input buffer ({0})".format(reason))
        except Exception as e:
            print("[realtime] input clear failed ({0}): {1}".format(reason, e))

    def interrupt_current_response(reason):
        print("[realtime] interrupt current response ({0})".format(reason))
        interrupt_requested[0] = True
        suppress_echo_response[0] = False
        last_interrupt_at[0] = time.time()
        player.clear()
        clear_input_buffer(reason)
        mark_assistant_active()
        if response_active[0]:
            try:
                _send_json(conn, {"type": "response.cancel"})
                print("[realtime] sent response.cancel ({0})".format(reason))
            except Exception as e:
                print("[realtime] response.cancel failed ({0}): {1}".format(reason, e))
        else:
            print("[realtime] no active response to cancel ({0})".format(reason))

    def send_chunk(pcm):
        if stop_event.is_set() or not pcm:
            return
        rms = _pcm_rms(pcm)
        chunk_stats["count"] += 1
        chunk_stats["bytes"] += len(pcm)
        chunk_stats["last_rms"] = rms
        if rms > chunk_stats["max_rms"]:
            chunk_stats["max_rms"] = rms
        now = time.time()
        if now - chunk_stats["last_log"] >= 2.0:
            print("[realtime] mic chunks={0} bytes={1} last_rms={2} max_rms={3}".format(
                chunk_stats["count"], chunk_stats["bytes"],
                chunk_stats["last_rms"], chunk_stats["max_rms"],
            ))
            chunk_stats["last_log"] = now
        allow_mic, barge_accepted = echo_gate.check(
            player, rms, now=now, assistant_active=assistant_is_active(now),
        )
        if not allow_mic:
            chunk_stats["suppressed_echo"] += 1
            if now - chunk_stats["last_echo_log"] >= 2.0:
                print("[realtime] echo gate suppressed chunks={0} last_rms={1} max_rms={2}".format(
                    chunk_stats["suppressed_echo"], rms, chunk_stats["max_rms"],
                ))
                chunk_stats["last_echo_log"] = now
            if now - chunk_stats["last_input_clear"] >= 0.8:
                clear_input_buffer("local echo gate")
                chunk_stats["last_input_clear"] = now
            return
        if barge_accepted:
            print("[realtime] acoustic barge accepted rms={0}; stopping playback".format(rms))
            interrupt_current_response("acoustic barge")
        # OpenAI Realtime expects pcm16 @ 24 kHz mono. NAO records at 16 kHz,
        # so we resample 16k → 24k here. ratecv keeps a small filter state
        # across calls so chunk boundaries don't click.
        if audioop is None:
            print("[realtime] audioop unavailable; sending {0} Hz audio".format(CAPTURE_SAMPLE_RATE))
            up = pcm
        else:
            try:
                up, resample_state[0] = audioop.ratecv(
                    pcm, 2, 1, CAPTURE_SAMPLE_RATE, API_AUDIO_SAMPLE_RATE, resample_state[0],
                )
            except Exception as e:
                print("[realtime] resample error:", e)
                return
        try:
            b64 = base64.b64encode(up)
            if not isinstance(b64, str):
                b64 = b64.decode("ascii")
            _send_json(conn, {
                "type": "input_audio_buffer.append",
                "audio": b64,
            })
        except Exception:
            stop_event.set()

    recorder = _RecorderThread(config.NAO_IP, config.NAO_PORT, send_chunk, stop_event)
    recorder.start()

    # Head-touch monitor — sends response.cancel + clears playback to interrupt.
    def watch_touch():
        last = 0.0
        while not stop_event.is_set():
            try:
                hit = False
                for k in ("FrontTactilTouched", "MiddleTactilTouched", "RearTactilTouched"):
                    v = memory.getData(k)
                    if isinstance(v, (int, float)) and float(v) > 0.5:
                        hit = True
                        break
                if hit and (time.time() - last) > 0.8:  # debounce
                    last = time.time()
                    print("[realtime] head-touch interrupt")
                    interrupt_current_response("head touch")
                    try:
                        leds.fadeRGB("FaceLeds", 1.0, 0.5, 0.0, 0.08)
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(0.05)

    threading.Thread(target=watch_touch).start()

    # Indicate we're live.
    try:
        leds.fadeRGB("FaceLeds", 0.0, 1.0, 0.3, 0.1)  # green-cyan = realtime listening
    except Exception:
        pass

    audio_chunks = []  # accumulator for the current response audio.
    audio_chunk_bytes = 0
    next_mode = None   # set if user requested an exit or mode switch
    first_audio_logged = False

    print("[realtime] entering recv loop")
    msg_count = 0
    try:
        while not stop_event.is_set():
            try:
                msg = conn.recv()
            except ws_client.WebSocketTimeoutException:
                # Shouldn't happen with timeout=None, but guard anyway.
                continue
            except Exception as e:
                print("[realtime] recv exception:", e)
                break
            if not msg:
                print("[realtime] recv returned empty (msg_count={0})".format(msg_count))
                break
            msg_count += 1
            try:
                ev = json.loads(msg)
            except Exception:
                continue

            etype = ev.get("type", "")

            if etype in ("response.audio.delta", "response.output_audio.delta"):
                # Base64-encoded pcm16 mono @ 24 kHz.
                response_active[0] = True
                b64 = ev.get("delta", "")
                if b64:
                    try:
                        if interrupt_requested[0]:
                            audio_chunks = []
                            audio_chunk_bytes = 0
                            continue
                        chunk = base64.b64decode(b64)
                        mark_assistant_active()
                        if not first_audio_logged:
                            first_audio_logged = True
                            print("[realtime] first audio chunk bytes={0} event={1}".format(
                                len(chunk), etype,
                            ))
                        audio_chunks.append(chunk)
                        audio_chunk_bytes += len(chunk)
                        if audio_chunk_bytes >= PLAYBACK_FLUSH_BYTES:
                            joined = b"".join(audio_chunks)
                            print("[realtime] audio partial bytes={0}; enqueue playback".format(
                                len(joined),
                            ))
                            player.enqueue(joined)
                            audio_chunks = []
                            audio_chunk_bytes = 0
                    except Exception:
                        pass

            elif etype in ("response.audio.done", "response.output_audio.done"):
                mark_assistant_active()
                if interrupt_requested[0]:
                    audio_chunks = []
                    audio_chunk_bytes = 0
                    continue
                if audio_chunks:
                    joined = b"".join(audio_chunks)
                    print("[realtime] audio done bytes={0}; enqueue playback".format(len(joined)))
                    player.enqueue(joined)
                    audio_chunks = []
                    audio_chunk_bytes = 0

            elif etype == "input_audio_buffer.speech_started":
                if assistant_is_active():
                    suppress_echo_response[0] = True
                    print("[realtime] ignored speech_started during assistant echo gate")
                    clear_input_buffer("echo speech_started")
                else:
                    # User started talking -> cut off any current playback.
                    print("[realtime] speech started")
                    suppress_echo_response[0] = False
                    player.clear()

            elif etype == "input_audio_buffer.speech_stopped":
                print("[realtime] speech stopped")
                if suppress_echo_response[0] or assistant_is_active():
                    clear_input_buffer("echo speech_stopped")

            elif etype == "conversation.item.input_audio_transcription.completed":
                transcript = (ev.get("transcript") or "").strip()
                print("[realtime] you said:", transcript)
                if not transcript or len(transcript) < 3:
                    print("[realtime] empty/junk transcript; cancelling auto-response")
                    interrupt_current_response("empty transcript")
                    continue
                if suppress_echo_response[0]:
                    print("[realtime] ignored transcript flagged as echo")
                    clear_input_buffer("echo transcript")
                    continue
                action = intent.detect(transcript, current_mode=initial_hint or "chat")
                if action == "exit":
                    print("[realtime] exit intent")
                    next_mode = None
                    break
                if action and action.startswith("switch:"):
                    next_mode = action.split(":", 1)[1]
                    print("[realtime] switch intent:", next_mode)
                    break
                try:
                    _send_json(conn, {"type": "response.create", "response": {"modalities": ["audio", "text"]}})
                    print("[realtime] response.create sent after valid transcript")
                except Exception as e:
                    print("[realtime] response.create failed:", e)

            elif etype == "error":
                print("[realtime] server error:", ev)
                # Errors within 2s of a deliberate interrupt (head touch, echo
                # gate, mode switch) are nearly always 'no active response' or
                # 'buffer empty' races. Always swallow them — never tear down
                # the session for a side-effect of our own action.
                if (time.time() - last_interrupt_at[0]) < 2.0:
                    print("[realtime] error within interrupt window; staying in mode")
                    response_active[0] = False
                    continue
                if _recoverable_realtime_error(ev):
                    print("[realtime] recoverable realtime error; staying in mode")
                    response_active[0] = False
                    continue
                break
            elif etype == "response.created":
                response_active[0] = True
                if suppress_echo_response[0]:
                    print("[realtime] cancelling echo-generated response")
                    try:
                        _send_json(conn, {"type": "response.cancel"})
                    except Exception:
                        pass
                    clear_input_buffer("echo response.created")
                    audio_chunks = []
                    audio_chunk_bytes = 0
                    first_audio_logged = False
                    suppress_echo_response[0] = False
                    mark_assistant_active()
                    continue
                print("[realtime] response.created")
                mark_assistant_active(3.0)
                audio_chunks = []
                audio_chunk_bytes = 0
                first_audio_logged = False
            elif etype == "response.done":
                response_active[0] = False
                if interrupt_requested[0]:
                    print("[realtime] interrupted response done; staying in mode")
                    interrupt_requested[0] = False
                    suppress_echo_response[0] = False
                    audio_chunks = []
                    audio_chunk_bytes = 0
                    first_audio_logged = False
                    clear_input_buffer("interrupt complete")
                    continue
                mark_assistant_active()
                clear_input_buffer("assistant response done")
                # Dump the full event so we can see what modalities/output the
                # model actually produced. If audio is missing here, audio is
                # not being generated server-side.
                print("[realtime] response.done payload:", json.dumps(ev)[:400])
            elif etype == "session.created":
                print("[realtime] session.created (model live)")
            elif etype == "session.updated":
                print("[realtime] session.updated (mode applied)")
            else:
                # Catch-all so we can see any new event types the GA API uses.
                print("[realtime] unhandled event:", etype)

    finally:
        stop_event.set()
        try:
            conn.close()
        except Exception:
            pass
        player.stop()
        try:
            leds.fadeRGB("FaceLeds", 1.0, 1.0, 1.0, 0.1)  # back to white
        except Exception:
            pass
        print("[realtime] session ended")
    return next_mode
