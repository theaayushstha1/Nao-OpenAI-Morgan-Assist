# -*- coding: utf-8 -*-
"""ElevenLabs Scribe v2 Realtime STT — feature-flagged candidate.

NOT wired into the production STT path. The current ``_transcribe`` in
``server.server`` (used by the legacy Flask) and the WS handler's
``legacy.transcribe`` keep their existing routing (Deepgram + OpenAI
Whisper fallback). This module provides:

  - ``transcribe_file(path)`` — sync, batches the whole file through
    the realtime WS and returns the final transcript. Drop-in shape
    for the existing Whisper/Deepgram bench harness.

  - ``async transcribe_stream(pcm_iter)`` — yields partial /
    committed transcripts as they arrive. For future integration
    once the A/B benchmark says to swap.

  - ``is_available()`` — env check.

A/B benchmark in ``sim/stt_ab.py`` calls ``transcribe_file`` on a fixed
clip set and compares to the existing providers.

Refs: ElevenLabs Speech-to-Text (Scribe v2 Realtime) docs.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
import wave
from typing import AsyncIterator

from server import config

_log = logging.getLogger(__name__)


def is_available() -> bool:
    """True iff EL STT is enabled + key set."""
    if not getattr(config, "USE_ELEVENLABS_STT", False):
        return False
    if not getattr(config, "ELEVENLABS_API_KEY", ""):
        return False
    return True


def _read_wav_pcm(path: str) -> tuple[bytes, int]:
    """Return (raw PCM16 mono, sample_rate). Caller resamples if needed."""
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        n_channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if sampwidth != 2:
        raise ValueError(f"WAV must be 16-bit, got {sampwidth*8}-bit")
    if n_channels == 2:
        # Cheap stereo->mono mixdown for benching.
        import struct
        s = struct.unpack(f"<{len(frames)//2}h", frames)
        mono = [(s[i] + s[i + 1]) // 2 for i in range(0, len(s), 2)]
        frames = struct.pack(f"<{len(mono)}h", *mono)
    return frames, rate


def transcribe_file(path: str, timeout_s: float = 30.0) -> str:
    """Sync wrapper for the bench harness. Reads a WAV, streams its PCM
    into the EL Scribe Realtime WS, returns the final committed text.

    Returns "" on any failure (so the bench can attribute failures
    cleanly without raising).
    """
    if not is_available():
        return ""
    try:
        pcm, rate = _read_wav_pcm(path)
    except Exception as e:  # noqa: BLE001
        _log.warning("EL STT: bad WAV %s: %r", path, e)
        return ""

    async def _go() -> str:
        chunks_text: list[str] = []
        # Frame the PCM into ~100ms chunks so we look like a live stream.
        frame_ms = 100
        frame_bytes = int(rate * 2 * (frame_ms / 1000.0))

        async def _pcm_iter() -> AsyncIterator[bytes]:
            for i in range(0, len(pcm), frame_bytes):
                yield pcm[i:i + frame_bytes]
                await asyncio.sleep(frame_ms / 1000.0 * 0.05)  # mild pacing

        try:
            async for ev in transcribe_stream(_pcm_iter(), sample_rate=rate):
                if ev.get("is_final") and ev.get("text"):
                    chunks_text.append(ev["text"])
        except Exception as e:  # noqa: BLE001
            _log.warning("EL STT stream error: %r", e)
            return ""
        return " ".join(chunks_text).strip()

    try:
        return asyncio.run(asyncio.wait_for(_go(), timeout=timeout_s))
    except asyncio.TimeoutError:
        _log.warning("EL STT: timed out after %s s", timeout_s)
        return ""
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(asyncio.wait_for(_go(),
                                                              timeout=timeout_s))
        except Exception:
            return ""
        finally:
            loop.close()


async def transcribe_stream(pcm_iter: AsyncIterator[bytes],
                              sample_rate: int = 16000,
                              language: str = "en"
                              ) -> AsyncIterator[dict]:
    """Yield {is_final: bool, text: str, t_ms: float} events as the
    Scribe v2 Realtime endpoint emits partial / committed transcripts.

    PCM input must be 16-bit signed LE mono. ``sample_rate`` declares
    the rate — EL accepts 8/16/22.05/24/44.1/48 kHz; we default to 16
    kHz to match the robot stream.
    """
    if not is_available():
        return

    api_key = config.ELEVENLABS_API_KEY
    model = getattr(config, "ELEVENLABS_STT_MODEL", "scribe_v2_realtime")

    # ElevenLabs Speech-to-Text WebSocket (Scribe Realtime). Endpoint
    # path/params follow their public docs; if these change, update
    # here.
    url = (
        "wss://api.elevenlabs.io/v1/speech-to-text/stream"
        f"?model_id={model}"
        f"&language_code={language}"
        f"&sample_rate={sample_rate}"
        "&encoding=pcm_s16le"
    )
    headers = [("xi-api-key", api_key)]

    try:
        import websockets  # type: ignore
    except ImportError:
        _log.warning("EL STT: `websockets` package missing")
        return

    try:
        try:
            ws = await websockets.connect(url, additional_headers=headers)
        except TypeError:
            ws = await websockets.connect(url, extra_headers=headers)
    except Exception as e:  # noqa: BLE001
        _log.warning("EL STT WS connect failed: %r", e)
        return

    t_open = time.perf_counter()

    async def _sender():
        try:
            async for chunk in pcm_iter:
                if chunk:
                    await ws.send(chunk)
            # End-of-audio sentinel per EL Realtime contract.
            await ws.send(json.dumps({"type": "end_of_audio"}))
        except Exception:
            pass

    sender_task = asyncio.create_task(_sender())
    try:
        async for raw in ws:
            t_ms = (time.perf_counter() - t_open) * 1000.0
            if isinstance(raw, bytes):
                continue  # EL sends JSON text; bytes shouldn't appear
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type") or msg.get("event") or ""
            text = (msg.get("text") or msg.get("transcript")
                    or msg.get("delta") or "")
            if not text:
                continue
            is_final = bool(
                msg.get("is_final")
                or "final" in mtype.lower()
                or msg.get("type") == "transcript_final"
            )
            yield {"is_final": is_final, "text": text, "t_ms": t_ms}
            if is_final and msg.get("type") == "session_end":
                break
    except Exception as e:  # noqa: BLE001
        _log.warning("EL STT recv error: %r", e)
    finally:
        try:
            await ws.close()
        except Exception:
            pass
        sender_task.cancel()
