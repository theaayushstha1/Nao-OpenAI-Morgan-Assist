# -*- coding: utf-8 -*-
"""
EchoSimulator -- mix delayed speaker output back into mic frames.

Used by Phase 10.5 scenarios that exercise the server-side echo guard.
The simulator keeps a small ring buffer of "what the speaker just played"
and, when ``apply_to_pcm`` is called, mixes a delayed, attenuated copy of
that audio into the live mic frame. With ``enabled=False`` (the default)
the simulator is inert and ``apply_to_pcm`` is the identity function.

Why a separate class
--------------------
The fake_naoqi module owns mic capture and TTS playback indirection. The
echo path is conceptually orthogonal: a scenario can decide to enable
echo per-test without touching anything else. Live-mode ``live_nao.py``
also keeps echo OFF by default (the Mac's hardware speaker -> mic loop
already injects real echo) and turns it on only when reproducing the
canned-WAV scenarios.

Public API
----------
    sim = EchoSimulator(delay_ms=80, gain=0.10)
    sim.enabled = True                       # default: False
    sim.record_played(mp3_or_pcm_bytes,
                      sample_rate=16000,
                      now_ms=None)           # appends to history
    out = sim.apply_to_pcm(pcm_chunk_bytes,
                           ts_ms,
                           sample_rate=16000)  # mixed PCM (PCM16-LE)
    sim.reset()                              # forget all history

Notes
-----
The simulator works on PCM16-LE only. ``record_played`` accepts arbitrary
bytes (the caller can pass an MP3 if it likes) but the *delayed echo* is
generated solely from the PCM history. If you call ``record_played`` with
non-PCM bytes the bytes are kept around for trace purposes but never
mixed into the mic stream.

The `pcm_source` callable that ``FakeALAudioDevice.subscribe`` consumes
should call ``apply_to_pcm`` on each 20 ms chunk, then forward the
returned bytes to the registered ALModule.
"""
from __future__ import annotations

import struct
import time
from collections import deque
from threading import RLock
from typing import Deque, Tuple


# --------------------------------------------------------------------------
# small PCM utilities. Pure-Python so the simulator stays dependency-free
# (numpy would be faster but we operate on 20 ms = 320 samples chunks at
# 16 kHz, which is fast enough in Python).
# --------------------------------------------------------------------------


def _now_ms() -> float:
    return time.time() * 1000.0


def _is_pcm16_payload(buf: bytes) -> bool:
    """Heuristic: bytes are PCM16 if length is even AND not an MP3.

    We don't try to be clever here -- a caller that wants strict PCM
    semantics should pass PCM directly. The MP3 magic check just avoids
    the most common confusion (passing an entire MP3 chunk through and
    getting noise back).
    """
    if not buf or len(buf) % 2 != 0:
        return False
    head = buf[:4]
    if head[:3] == b"ID3":
        return False
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        # MPEG audio frame sync.
        return False
    return True


def _mix_pcm16(a: bytes, b: bytes, attenuation_when_clip: float = 0.85) -> bytes:
    """Sum two equal-length PCM16-LE buffers, clamping with attenuation on
    overflow rather than hard-clipping.

    If `a` and `b` differ in length, the shorter one is zero-padded on the
    right; the result has ``max(len(a), len(b))`` bytes.
    """
    if not a and not b:
        return b""
    if not a:
        return b
    if not b:
        return a

    if len(a) < len(b):
        a = a + b"\x00" * (len(b) - len(a))
    elif len(b) < len(a):
        b = b + b"\x00" * (len(a) - len(b))

    n = len(a) // 2
    a_samples = struct.unpack("<%dh" % n, a[:n * 2])
    b_samples = struct.unpack("<%dh" % n, b[:n * 2])
    out = []
    clip = False
    for x, y in zip(a_samples, b_samples):
        s = x + y
        if s > 32767 or s < -32768:
            clip = True
        out.append(s)
    if clip:
        scale = float(attenuation_when_clip)
        out = [int(s * scale) for s in out]
        out = [max(-32768, min(32767, s)) for s in out]
    else:
        # Already in range; just clamp defensively.
        out = [max(-32768, min(32767, s)) for s in out]
    return struct.pack("<%dh" % n, *out)


def _attenuate_pcm16(pcm: bytes, gain: float) -> bytes:
    """Multiply each sample of a PCM16-LE buffer by gain."""
    if not pcm:
        return b""
    n = len(pcm) // 2
    samples = struct.unpack("<%dh" % n, pcm[:n * 2])
    out = []
    for s in samples:
        v = int(s * gain)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out.append(v)
    return struct.pack("<%dh" % n, *out)


# --------------------------------------------------------------------------
# main class
# --------------------------------------------------------------------------


class EchoSimulator(object):
    """Inject a delayed, attenuated copy of speaker output into mic frames.

    Parameters
    ----------
    delay_ms : int, default 80
        How long after a chunk of audio is "played" via ``record_played``
        it begins to bleed into the mic. A real classroom hardware loop
        is typically 60-120 ms; 80 ms is a sensible mid-point.
    gain : float, default 0.10
        Linear attenuation applied to the speaker history before mixing.
        At 0.10 (~ -20 dBFS relative to the original) the echo is loud
        enough to challenge the server's echo guard but soft enough that
        a real user voice still dominates.
    history_seconds : float, default 5.0
        How much of the recent speaker history to keep around. Older
        samples are discarded; we only need ``delay_ms + chunk_ms`` worth
        in practice but keep extra for debug-replay.
    enabled : bool, default False
        When False, ``apply_to_pcm`` is the identity. live_nao.py keeps
        this off by default (real speaker -> mic acoustic loop already
        adds echo); scenarios flip it on for the echo-bleed test.
    """

    def __init__(self,
                 delay_ms: int = 80,
                 gain: float = 0.10,
                 history_seconds: float = 5.0,
                 enabled: bool = False):
        self.delay_ms = int(delay_ms)
        self.gain = float(gain)
        self.history_seconds = float(history_seconds)
        self.enabled = bool(enabled)

        # History entries: (ts_ms, sample_rate, pcm_bytes).
        # Non-PCM payloads are still stored (so callers can inspect what
        # was played) but won't be mixed back; we mark them with sr=0.
        self._history: Deque[Tuple[float, int, bytes]] = deque()
        self._lock = RLock()

    # --------------------------------------------------------------------
    # public API
    # --------------------------------------------------------------------
    def reset(self) -> None:
        """Forget all recorded speaker history."""
        with self._lock:
            self._history.clear()

    def record_played(self,
                      audio_bytes: bytes,
                      sample_rate: int = 16000,
                      now_ms: float | None = None) -> None:
        """Record a chunk that was just played by the (fake) speaker.

        ``audio_bytes`` may be PCM16-LE *or* MP3. Only PCM is mixed back;
        MP3 is stored for trace purposes but never delayed-injected.
        """
        if not audio_bytes:
            return
        ts = float(now_ms) if now_ms is not None else _now_ms()
        sr = int(sample_rate) if _is_pcm16_payload(audio_bytes) else 0
        with self._lock:
            self._history.append((ts, sr, bytes(audio_bytes)))
            self._evict_old(ts)

    def apply_to_pcm(self,
                     pcm_chunk: bytes,
                     ts_ms: float | None = None,
                     sample_rate: int = 16000) -> bytes:
        """Mix recorded speaker echo into the live PCM chunk.

        Parameters
        ----------
        pcm_chunk : bytes
            PCM16-LE bytes from the (virtual) microphone. Length should be
            a multiple of 2; non-conforming buffers are returned unchanged.
        ts_ms : float, optional
            Wall-clock millisecond timestamp of this chunk's *start*. If
            omitted, ``time.time()*1000`` is used.
        sample_rate : int, default 16000
            Mic sample rate. Must match the rate of any PCM history we
            mix in; mismatched-rate history is skipped.

        Returns
        -------
        bytes
            The chunk plus delayed-and-attenuated speaker history. Same
            length as ``pcm_chunk``.
        """
        if not self.enabled or not pcm_chunk:
            return pcm_chunk
        if len(pcm_chunk) % 2 != 0:
            return pcm_chunk

        ts = float(ts_ms) if ts_ms is not None else _now_ms()
        # The chunk we want to mix in is the slice of speaker history that
        # *played* at (ts - delay_ms) for the duration of this mic chunk.
        target_start = ts - float(self.delay_ms)
        # mic chunk duration in ms.
        chunk_ms = (len(pcm_chunk) // 2) * 1000.0 / float(sample_rate)
        target_end = target_start + chunk_ms

        echo = self._extract_history_window(target_start, target_end,
                                            sample_rate, len(pcm_chunk))
        if not echo:
            return pcm_chunk

        echo = _attenuate_pcm16(echo, self.gain)
        return _mix_pcm16(pcm_chunk, echo)

    # --------------------------------------------------------------------
    # internals
    # --------------------------------------------------------------------
    def _evict_old(self, now_ms: float) -> None:
        """Drop history entries older than ``history_seconds`` from now."""
        cutoff = now_ms - (self.history_seconds * 1000.0)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def _extract_history_window(self,
                                start_ms: float,
                                end_ms: float,
                                sample_rate: int,
                                desired_bytes: int) -> bytes:
        """Pull a [start_ms, end_ms) slice out of recorded PCM history.

        We concatenate the matching portions of each stored chunk.
        Non-PCM (sr=0) entries are skipped, as are entries whose
        sample_rate doesn't match the caller's. If the window has no
        coverage, returns ``b""``; if partial coverage, the result is
        zero-padded to ``desired_bytes`` so the caller can mix without a
        length-mismatch shim.
        """
        bytes_per_sample = 2  # PCM16
        chunk_collected = bytearray()
        with self._lock:
            entries = list(self._history)

        for ts, sr, pcm in entries:
            if sr <= 0 or sr != int(sample_rate):
                continue
            chunk_dur_ms = (len(pcm) // bytes_per_sample) * 1000.0 / float(sr)
            chunk_start = ts
            chunk_end = ts + chunk_dur_ms
            if chunk_end <= start_ms or chunk_start >= end_ms:
                continue
            # Trim to overlap.
            overlap_start = max(start_ms, chunk_start)
            overlap_end = min(end_ms, chunk_end)
            byte_offset = int(
                (overlap_start - chunk_start) / 1000.0
                * float(sr) * bytes_per_sample
            )
            byte_count = int(
                (overlap_end - overlap_start) / 1000.0
                * float(sr) * bytes_per_sample
            )
            # Round to even byte boundary.
            byte_offset -= byte_offset % 2
            byte_count -= byte_count % 2
            if byte_count <= 0:
                continue
            byte_offset = max(0, min(byte_offset, len(pcm)))
            byte_count = max(0, min(byte_count, len(pcm) - byte_offset))
            chunk_collected.extend(pcm[byte_offset:byte_offset + byte_count])

        # Pad to desired length so the mixer doesn't have to short-pad.
        if not chunk_collected:
            return b""
        if len(chunk_collected) < desired_bytes:
            chunk_collected.extend(b"\x00" * (desired_bytes - len(chunk_collected)))
        elif len(chunk_collected) > desired_bytes:
            del chunk_collected[desired_bytes:]
        return bytes(chunk_collected)


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import math

    # Build a 200 ms tone at 16 kHz, full-scale 1 kHz sine, as the speaker
    # output history.
    sr = 16000
    dur = 0.2
    n = int(sr * dur)
    speaker = struct.pack(
        "<%dh" % n,
        *[int(20000 * math.sin(2 * math.pi * 1000.0 * i / sr)) for i in range(n)],
    )
    sim = EchoSimulator(delay_ms=80, gain=0.5, enabled=True)
    sim.record_played(speaker, sample_rate=sr, now_ms=0.0)

    # 20 ms of silence as mic input, 100 ms after the speaker started.
    chunk_n = int(sr * 0.020)
    silent_chunk = b"\x00\x00" * chunk_n
    out = sim.apply_to_pcm(silent_chunk, ts_ms=100.0, sample_rate=sr)
    assert len(out) == len(silent_chunk), (len(out), len(silent_chunk))
    # The output should NOT be all zeros -- the echo bled in.
    assert any(b != 0 for b in out), "echo not mixed"

    # Disable -> identity.
    sim.enabled = False
    out = sim.apply_to_pcm(silent_chunk, ts_ms=100.0, sample_rate=sr)
    assert out == silent_chunk, "disabled mode should be identity"

    # Reset -> empty history -> no echo even when enabled.
    sim.enabled = True
    sim.reset()
    out = sim.apply_to_pcm(silent_chunk, ts_ms=100.0, sample_rate=sr)
    assert out == silent_chunk, "reset cleared history but echo appeared"

    print("[echo_sim self-test] OK")
