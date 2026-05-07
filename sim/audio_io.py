"""Mac-side audio I/O for the Virtual NAO simulator.

Two classes:

* ``MicCapture`` — opens the default Mac mic at 16 kHz mono PCM16, yields
  20 ms frames (640 bytes each) via ``iter_frames()``. Drives
  ``FakeALAudioDevice.processRemote`` so the rest of the pipeline thinks
  it's getting NAO front-mic audio.

* ``SpeakerOut`` — plays MP3 (or WAV) bytes returned by the server's TTS.
  Background playback so the caller (``FakeALAudioPlayer.playFile``) can
  return immediately the same way the real ALAudioPlayer does.

Both classes degrade gracefully when the optional ``sounddevice`` /
``numpy`` deps are missing: they log once and become no-ops. That keeps
``python -m py_compile`` and ``python sim/live_nao.py --dry-run`` working
on bare Python installs (CI, fresh laptops, etc.) and lets the rest of the
sim continue with synthetic / scripted audio.

Threading model
---------------
* ``MicCapture.start()`` spawns a daemon InputStream callback that pushes
  frames into a bounded queue. ``iter_frames()`` is the consumer; if the
  consumer falls behind we drop the oldest frame so memory stays bounded
  and "now" stays roughly "now" rather than 5 s ago.
* ``SpeakerOut.play()`` returns the moment the bytes are accepted. A
  background daemon thread does the actual decode + write so the WS
  receiver loop never blocks on speaker latency.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from typing import Iterator, Optional

logger = logging.getLogger("sim.audio_io")

# ── Optional imports ─────────────────────────────────────────────────────────
# We never fail at import time. Anything that may not be installed gets a
# try/except + a flag so callers can still construct the class and get a
# warning + no-op behaviour at runtime.
try:  # pragma: no cover — exercised only when the dep is installed
    import sounddevice as _sd  # type: ignore
    _HAS_SOUNDDEVICE = True
except Exception as _exc:  # noqa: BLE001 — could be ImportError or OSError
    _sd = None
    _HAS_SOUNDDEVICE = False
    _SOUNDDEVICE_IMPORT_ERROR: Optional[BaseException] = _exc
else:
    _SOUNDDEVICE_IMPORT_ERROR = None

try:  # pragma: no cover
    import numpy as _np  # type: ignore
    _HAS_NUMPY = True
except Exception:
    _np = None
    _HAS_NUMPY = False

# pyaudio is the documented fallback. Almost never present on macOS but
# we'll try it if sounddevice is missing.
try:  # pragma: no cover
    import pyaudio as _pa  # type: ignore
    _HAS_PYAUDIO = True
except Exception:
    _pa = None
    _HAS_PYAUDIO = False

# pydub gives us in-process MP3 → WAV decode if it's installed. If it's
# not, we shell out to ffplay/afplay which both support MP3 directly.
try:  # pragma: no cover
    from pydub import AudioSegment as _AudioSegment  # type: ignore
    _HAS_PYDUB = True
except Exception:
    _AudioSegment = None
    _HAS_PYDUB = False


# ── Constants ───────────────────────────────────────────────────────────────
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_FRAME_MS = 20
DEFAULT_CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2  # int16
DEFAULT_QUEUE_MAXSIZE = 200  # 200 * 20 ms = 4 s of mic buffer


def _bytes_per_frame(sample_rate_hz: int, frame_ms: int) -> int:
    return sample_rate_hz * SAMPLE_WIDTH_BYTES * DEFAULT_CHANNELS * frame_ms // 1000


def _have_sounddevice() -> bool:
    return _HAS_SOUNDDEVICE and _HAS_NUMPY


def degraded_reason() -> Optional[str]:
    """Return a short human-readable reason if audio I/O is degraded.

    ``None`` means full functionality. Used by ``live_nao.py`` to print a
    one-line warning at boot rather than per-call spam.
    """
    if not _HAS_SOUNDDEVICE:
        return "sounddevice not installed; sim is degraded mode"
    if not _HAS_NUMPY:
        return "numpy not installed; sim is degraded mode"
    return None


# ── MicCapture ──────────────────────────────────────────────────────────────


class MicCapture:
    """Default-mic → 20 ms PCM16 frames.

    Parameters
    ----------
    sample_rate : int, default 16000
        Output sample rate. Aldebaran's ALAudioDevice gives 16 kHz mono
        front-mic audio so we match that exactly.
    frame_ms : int, default 20
        Frame length. ``audio_module.NaoAudioStreamer`` re-slices NAOqi's
        ~170 ms callbacks to 20 ms frames; we generate 20 ms directly.
    queue_maxsize : int, default 200
        Bounded backlog. We drop the oldest frame on overflow rather than
        OOM the process when the consumer falls behind.
    device : int | str | None
        Optional sounddevice device index/name. ``None`` = default input.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_ms: int = DEFAULT_FRAME_MS,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        device: Optional[object] = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.frame_ms = int(frame_ms)
        self.bytes_per_frame = _bytes_per_frame(self.sample_rate, self.frame_ms)
        self.samples_per_frame = self.bytes_per_frame // SAMPLE_WIDTH_BYTES
        self.device = device

        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=int(queue_maxsize))
        self._stream = None  # sounddevice.InputStream | pyaudio stream | None
        self._pa_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._started = False
        self._lock = threading.Lock()
        self._dropped = 0
        # Tail kept across callback boundaries so non-aligned chunk sizes
        # still produce 20 ms frames cleanly.
        self._tail = b""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Open the mic. Idempotent. Logs and no-ops if no backend is
        available — the caller can keep iterating ``iter_frames`` and just
        get nothing.
        """
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._tail = b""

            if _have_sounddevice():
                try:
                    self._start_sounddevice()
                    self._started = True
                    logger.info(
                        "MicCapture started (sounddevice, sr=%d, frame=%d ms)",
                        self.sample_rate, self.frame_ms,
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "sounddevice mic open failed (%s: %s); "
                        "falling back to pyaudio if available",
                        type(exc).__name__, exc,
                    )

            if _HAS_PYAUDIO:
                try:
                    self._start_pyaudio()
                    self._started = True
                    logger.info(
                        "MicCapture started (pyaudio, sr=%d, frame=%d ms)",
                        self.sample_rate, self.frame_ms,
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "pyaudio mic open failed (%s: %s); "
                        "MicCapture is in no-op mode",
                        type(exc).__name__, exc,
                    )

            # No backend → no-op. Keep "started" False so callers can
            # check via .is_active() and degrade gracefully.
            logger.warning(
                "MicCapture: no audio backend available "
                "(sounddevice=%s, pyaudio=%s); iter_frames() will block forever",
                _HAS_SOUNDDEVICE, _HAS_PYAUDIO,
            )

    def stop(self) -> None:
        """Close the mic and drain the queue. Idempotent."""
        with self._lock:
            if not self._started and not self._stop.is_set():
                # Never started or already stopped.
                self._stop.set()
                return
            self._stop.set()
            self._started = False

            if self._stream is not None:
                # sounddevice.InputStream and pyaudio.Stream both have
                # .stop() and .close() but with different semantics. Try
                # both and ignore errors so cleanup never raises.
                for method in ("stop", "close"):
                    try:
                        fn = getattr(self._stream, method, None)
                        if callable(fn):
                            fn()
                    except Exception:
                        pass
                self._stream = None

            if self._pa_thread is not None and self._pa_thread.is_alive():
                # Worker checks _stop on each loop tick.
                self._pa_thread.join(timeout=1.0)
            self._pa_thread = None

            # Drain queue.
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass

            logger.info(
                "MicCapture stopped (dropped=%d)", self._dropped,
            )

    def is_active(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------
    def iter_frames(self, poll_timeout_s: float = 0.1) -> Iterator[bytes]:
        """Yield 20 ms PCM16 mono bytes (length = ``bytes_per_frame``).

        Blocks until the next frame is available (with ``poll_timeout_s``
        granularity so ``stop()`` can wake the iterator). Exits cleanly
        when ``stop()`` is called.
        """
        while not self._stop.is_set():
            try:
                frame = self._queue.get(timeout=poll_timeout_s)
            except queue.Empty:
                continue
            if frame is None:
                # Sentinel for "we're done" — used by the pyaudio worker.
                break
            yield frame

    # ------------------------------------------------------------------
    # sounddevice backend
    # ------------------------------------------------------------------
    def _start_sounddevice(self) -> None:
        if not _have_sounddevice():
            raise RuntimeError("sounddevice/numpy unavailable")

        # blocksize in *samples*; sounddevice happily delivers our exact
        # frame size every callback, eliminating tail bookkeeping in the
        # common case.
        blocksize = self.samples_per_frame

        def _cb(indata, frames, time_info, status):
            # ``indata`` is a numpy array of shape (frames, channels).
            if status:
                # XRuns etc. — log but keep going.
                logger.debug("sounddevice status: %s", status)
            try:
                # Force int16 mono. sounddevice may give float32 or int16
                # depending on dtype; we pinned dtype below so we expect
                # int16 already, but defensively recoerce.
                if indata.dtype != _np.int16:
                    pcm = (
                        _np.clip(indata[:, 0] * 32767.0, -32768, 32767)
                        .astype(_np.int16)
                    )
                else:
                    pcm = indata[:, 0]
                raw = pcm.tobytes()
            except Exception:
                # Don't let a malformed frame crash the InputStream
                # callback — it lives on a CoreAudio thread and would
                # tear the whole process down on uncaught exception.
                return
            self._enqueue_pcm(raw)

        self._stream = _sd.InputStream(
            samplerate=self.sample_rate,
            channels=DEFAULT_CHANNELS,
            dtype="int16",
            blocksize=blocksize,
            device=self.device,
            callback=_cb,
        )
        self._stream.start()

    # ------------------------------------------------------------------
    # pyaudio backend (fallback)
    # ------------------------------------------------------------------
    def _start_pyaudio(self) -> None:
        if not _HAS_PYAUDIO:
            raise RuntimeError("pyaudio unavailable")

        pa = _pa.PyAudio()
        try:
            stream = pa.open(
                format=_pa.paInt16,
                channels=DEFAULT_CHANNELS,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.samples_per_frame,
            )
        except Exception:
            pa.terminate()
            raise

        self._stream = stream

        def _worker():
            try:
                while not self._stop.is_set():
                    try:
                        raw = stream.read(
                            self.samples_per_frame, exception_on_overflow=False,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("pyaudio read failed: %s", exc)
                        break
                    self._enqueue_pcm(raw)
            finally:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
                try:
                    pa.terminate()
                except Exception:
                    pass
                # Wake any consumer blocked on the queue.
                try:
                    self._queue.put_nowait(None)
                except Exception:
                    pass

        self._pa_thread = threading.Thread(
            target=_worker, name="MicCapture-pyaudio", daemon=True,
        )
        self._pa_thread.start()

    # ------------------------------------------------------------------
    # Frame slicing + bounded queue
    # ------------------------------------------------------------------
    def _enqueue_pcm(self, raw: bytes) -> None:
        """Push raw PCM into the queue, slicing to ``bytes_per_frame``.

        Handles non-aligned chunks by keeping a tail across calls. Drops
        the oldest queued frame on overflow rather than blocking the
        callback (which would XRun the audio device).
        """
        if not raw:
            return
        if self._tail:
            buf = self._tail + raw
        else:
            buf = raw
        offset = 0
        n = len(buf)
        bpf = self.bytes_per_frame
        while offset + bpf <= n:
            frame = buf[offset:offset + bpf]
            offset += bpf
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                # Drop oldest, push newest. Best-effort.
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(frame)
                except queue.Full:
                    self._dropped += 1
        self._tail = buf[offset:]


# ── SpeakerOut ──────────────────────────────────────────────────────────────


class SpeakerOut:
    """Plays MP3 / WAV bytes via sounddevice or a system command.

    Decode order:
    1. ``sounddevice`` + ``pydub`` (in-process MP3 decode → numpy → device).
    2. ``afplay`` (macOS built-in).
    3. ``ffplay`` (cross-platform, needs ffmpeg installed).

    All paths spawn a background thread / subprocess so ``play()``
    returns immediately. If none of the backends are available, we log
    once and become a no-op.
    """

    def __init__(self, sample_rate: int = 24_000) -> None:
        # OpenAI TTS streams 24 kHz MP3; we pin that for the
        # sounddevice fast path. WAV bytes get re-derived from the file
        # header so this default doesn't matter for WAV.
        self.sample_rate = int(sample_rate)
        self._lock = threading.Lock()
        self._active_threads: list[threading.Thread] = []
        self._active_procs: list[subprocess.Popen] = []
        self._mode = self._detect_mode()
        self._warned = False

    # ------------------------------------------------------------------
    @staticmethod
    def _detect_mode() -> str:
        """Pick the first usable playback backend and return its name."""
        if _have_sounddevice() and _HAS_PYDUB:
            return "sounddevice+pydub"
        if shutil.which("afplay"):
            return "afplay"
        if shutil.which("ffplay"):
            return "ffplay"
        return "noop"

    @property
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------
    def play(self, audio_bytes: bytes, format: str = "mp3") -> None:
        """Play ``audio_bytes`` in the background.

        Returns immediately. The actual playback happens on a daemon
        thread (or subprocess) so the WS receive loop never blocks on
        speaker latency.
        """
        if not audio_bytes:
            return
        fmt = (format or "mp3").lower()

        if self._mode == "sounddevice+pydub" and fmt in ("mp3", "wav"):
            t = threading.Thread(
                target=self._play_sounddevice,
                args=(audio_bytes, fmt),
                name="SpeakerOut-sd",
                daemon=True,
            )
            with self._lock:
                self._active_threads.append(t)
            t.start()
            return

        if self._mode in ("afplay", "ffplay"):
            t = threading.Thread(
                target=self._play_subprocess,
                args=(audio_bytes, fmt),
                name="SpeakerOut-{0}".format(self._mode),
                daemon=True,
            )
            with self._lock:
                self._active_threads.append(t)
            t.start()
            return

        # Noop branch.
        if not self._warned:
            self._warned = True
            logger.warning(
                "SpeakerOut: no playback backend available; play() is no-op",
            )

    def stop(self) -> None:
        """Best-effort stop of all current playbacks. Used on Ctrl-C and
        on barge-in. Idempotent.
        """
        with self._lock:
            procs, self._active_procs = self._active_procs, []
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        # We don't join sounddevice playback threads — they're tied to
        # the process audio engine; the next play() call will overwrite
        # the buffer. For a hard stop, sounddevice.stop() suffices.
        if _have_sounddevice():
            try:
                _sd.stop()  # type: ignore[union-attr]
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _play_sounddevice(self, audio_bytes: bytes, fmt: str) -> None:
        try:
            seg = _AudioSegment.from_file(  # type: ignore[union-attr]
                _BytesIO(audio_bytes), format=fmt,
            )
            sr = seg.frame_rate
            channels = seg.channels
            samples = _np.array(seg.get_array_of_samples())  # type: ignore[union-attr]
            if channels > 1:
                samples = samples.reshape((-1, channels))
            # sounddevice plays int16 fine if dtype matches.
            _sd.play(samples, samplerate=sr, blocking=False)  # type: ignore[union-attr]
            # Don't block — caller asked for fire-and-forget. sounddevice
            # holds the buffer for us.
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sounddevice playback failed (%s: %s); "
                "trying subprocess fallback once",
                type(exc).__name__, exc,
            )
            try:
                self._play_subprocess(audio_bytes, fmt)
            except Exception:
                pass

    def _play_subprocess(self, audio_bytes: bytes, fmt: str) -> None:
        """Write a temp file and shell out. afplay on Mac, ffplay otherwise."""
        suffix = "." + (fmt if fmt in ("mp3", "wav") else "mp3")
        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False,
            ) as tmp:
                tmp.write(audio_bytes)
                path = tmp.name
        except Exception as exc:  # noqa: BLE001
            logger.warning("SpeakerOut: temp file write failed: %s", exc)
            return

        try:
            cmd: list[str]
            if shutil.which("afplay"):
                cmd = ["afplay", path]
            elif shutil.which("ffplay"):
                cmd = [
                    "ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet",
                    path,
                ]
            else:
                logger.warning(
                    "SpeakerOut: no system audio command found; dropping clip",
                )
                return

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("SpeakerOut subprocess failed: %s", exc)
                return

            with self._lock:
                self._active_procs.append(proc)
            try:
                proc.wait()
            except Exception:
                pass
            finally:
                with self._lock:
                    if proc in self._active_procs:
                        self._active_procs.remove(proc)
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass


# Tiny BytesIO shim so the pydub path doesn't need an extra import line at
# the top of the file (and so we can swap for io.BytesIO below). Kept
# private to mark it as an implementation detail.
from io import BytesIO as _BytesIO  # noqa: E402


__all__ = ["MicCapture", "SpeakerOut", "degraded_reason"]
