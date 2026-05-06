"""Silero VAD wrapper — server-side voice activity detection.

NAO captures audio with loose energy gating; this module performs the final
endpoint check. Silero is a tiny (~1.5MB) torch model that runs comfortably
on CPU. We load it once at module import and reuse for every call.

Industry-standard params (Silero v4/v5 defaults):
- Sample rate: 16000 Hz (we resample if needed)
- Frame size: 512 samples (32 ms at 16 kHz)
- Threshold: 0.5 default; 0.3 = more sensitive (fewer false rejects).
  We pick 0.4 — slightly more sensitive than default, since NAO already
  pre-filters by energy and we'd rather over-accept than cut users off.
- min_silence_duration_ms: 500 — pauses shorter than this stay inside one
  speech segment (handles natural breath pauses).
- speech_pad_ms: 100 — pad each side of detected segments so we don't clip
  the leading consonant or trailing fricative.

Public API:
    has_voice(wav_path)   -> bool      True if file contains any speech.
    trim_silence(wav_path) -> str      Path to trimmed WAV (head/tail silence
                                       stripped). Falls back to original on
                                       any error.
"""
from __future__ import annotations

import logging
import os
import tempfile
import wave

log = logging.getLogger("sage.vad_silero")

# Silero defaults documented above.
SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # 32 ms @ 16 kHz
THRESHOLD = 0.4
MIN_SILENCE_MS = 500
SPEECH_PAD_MS = 100

_model = None
_get_speech_timestamps = None
_torch = None
_load_error: str | None = None


def _try_load() -> bool:
    """Lazy load Silero. Returns True if model is ready."""
    global _model, _get_speech_timestamps, _torch, _load_error
    if _model is not None:
        return True
    if _load_error is not None:
        return False
    try:
        import torch  # type: ignore
        _torch = torch
        # Use the standalone `silero-vad` pip package, period. The previous
        # torch.hub.load fallback pulled `snakers4/silero-vad` HEAD with
        # trust_repo=True at runtime — supply-chain risk if that repo is
        # ever compromised. The pip package is pinned in requirements.txt
        # and ships the same model weights, so dropping the fallback
        # reduces attack surface without losing capability.
        from silero_vad import load_silero_vad, get_speech_timestamps  # type: ignore
        _model = load_silero_vad()
        _get_speech_timestamps = get_speech_timestamps
        return True
    except Exception as e:  # noqa: BLE001
        _load_error = repr(e)
        log.warning(
            "silero VAD failed to load: %s. Install `silero-vad` from "
            "requirements.txt; the unsafe torch.hub fallback was removed.",
            _load_error,
        )
        return False


def _read_wav_mono16k(path: str):
    """Read a WAV and return a float32 torch tensor at 16 kHz, mono.

    Returns None on unreadable / unsupported files.
    """
    try:
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            n = w.getnframes()
            raw = w.readframes(n)
        if sw != 2 or ch != 1 or n == 0:
            return None
        import torch  # type: ignore
        import struct
        samples = struct.unpack("<{}h".format(n), raw)
        t = torch.tensor(samples, dtype=torch.float32) / 32768.0
        if sr != SAMPLE_RATE:
            # Cheap linear resample. Silero is robust to this for VAD.
            try:
                import torchaudio.functional as F  # type: ignore
                t = F.resample(t, sr, SAMPLE_RATE)
            except Exception:
                # Fallback: skip files at unexpected SR rather than crash.
                ratio = float(SAMPLE_RATE) / float(sr)
                new_len = int(len(t) * ratio)
                if new_len <= 0:
                    return None
                idx = torch.linspace(0, len(t) - 1, new_len)
                idx = idx.long().clamp(0, len(t) - 1)
                t = t[idx]
        return t
    except Exception as e:  # noqa: BLE001
        log.warning("vad_silero: read_wav failed: %s", e)
        return None


def has_voice(wav_path: str) -> bool:
    """Return True if `wav_path` contains at least one speech segment.

    Permissive on errors: if Silero can't be loaded or audio can't be parsed,
    return True so we don't drop legitimate clips.
    """
    if not _try_load():
        return True  # don't block traffic — energy gate already passed
    audio = _read_wav_mono16k(wav_path)
    if audio is None:
        return True
    try:
        ts = _get_speech_timestamps(
            audio, _model,
            sampling_rate=SAMPLE_RATE,
            threshold=THRESHOLD,
            min_silence_duration_ms=MIN_SILENCE_MS,
            speech_pad_ms=SPEECH_PAD_MS,
        )
        return bool(ts)
    except Exception as e:  # noqa: BLE001
        log.warning("vad_silero: get_speech_timestamps failed: %s", e)
        return True


def trim_silence(wav_path: str) -> str:
    """Trim leading/trailing silence from `wav_path`. Returns path to a new
    WAV. On any error, returns the original path unchanged.
    """
    if not _try_load():
        return wav_path
    audio = _read_wav_mono16k(wav_path)
    if audio is None:
        return wav_path
    try:
        ts = _get_speech_timestamps(
            audio, _model,
            sampling_rate=SAMPLE_RATE,
            threshold=THRESHOLD,
            min_silence_duration_ms=MIN_SILENCE_MS,
            speech_pad_ms=SPEECH_PAD_MS,
        )
        if not ts:
            return wav_path
        start = int(ts[0]["start"])
        end = int(ts[-1]["end"])
        if end <= start:
            return wav_path
        clipped = audio[start:end]
        # Write out as 16-bit mono @ 16 kHz.
        import struct
        pcm = (clipped.clamp(-1.0, 1.0) * 32767.0).to(_torch.int16).tolist()
        out_path = tempfile.NamedTemporaryFile(
            suffix="_silero.wav", delete=False
        ).name
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(struct.pack("<{}h".format(len(pcm)), *pcm))
        return out_path
    except Exception as e:  # noqa: BLE001
        log.warning("vad_silero: trim_silence failed: %s", e)
        return wav_path
