# -*- coding: utf-8 -*-
"""
NaoAudioStreamer — live PCM mic streaming on NAO V6.

This is the Phase 1 replacement for ``audio_handler.record_audio``'s
file-based ``ALAudioRecorder`` path. It opens a *live* stream of 16 kHz mono
PCM16 from the front microphone via the naoqi ``ALAudioDevice`` subscriber
protocol and hands 20 ms slices to a queue that the WS sender thread drains.

Why an ALModule
---------------
``ALAudioRecorder.startMicrophonesRecording`` writes a WAV to disk and hands
the *path* back when you ``stopMicrophonesRecording``. Live streaming
requires the **callback** path: a class that subclasses ``naoqi.ALModule``,
calls ``ALAudioDevice.subscribe(name)``, and exposes a method named
``processRemote(nbOfChannels, nbOfSamplesByChannel, timeStamp, inputBuffer)``.
The audio device pushes ~170 ms of audio per call (NAOqi default) which we
re-slice into 20 ms chunks before queuing.

Channel constants (per Aldebaran ``ALAudioDevice`` docs)
--------------------------------------------------------
``setClientPreferences(name, sampleRate, deinterleave_or_channels, deinterleave)``
The signature varies between NAOqi 2.5 (``(name, sampleRate, channels, deinterleave)``)
and earlier docs (``(name, sampleRate, deinterleave_mask, ...)``). Both
accept the **channel index constants**:

    ALL    = 0   (4 channels interleaved)
    LEFT   = 1
    RIGHT  = 2
    FRONT  = 3
    REAR   = 4

We use FRONT (3) because that mic is closest to the user when the robot is
oriented toward them and matches ``audio_handler.CHANNELS_MASK = (0,0,1,0)``.

Firmware fallback
-----------------
If ``ALAudioDevice.subscribe`` raises (some firmware revisions ship without
the public subscriber API enabled), we fall back to ``ALAudioRecorder``
short-fragment recording: 250 ms WAV files, read off disk and pushed
through the same queue. Latency is ~250 ms higher; documented in
``docs/spike_results.md``. This module exposes which mode is active via
``streamer.mode`` (``"alaudio_device"`` or ``"alaudio_recorder_fragment"``).

Public API
----------
    streamer = NaoAudioStreamer(broker_ip, broker_port, broker)
    streamer.start()                  # begin streaming
    for seq, ts_ms, b64 in streamer.read_chunks():
        ws.send_audio_chunk(seq, ts_ms, b64)
    streamer.gate(True)               # mute mic during TTS
    streamer.gate(False)              # un-mute when TTS done
    streamer.stop()                   # tear down, drain queue, reset seq

The class is broker-aware so the caller (``ws_client.py``) constructs the
broker once and threads it in.
"""
from __future__ import print_function

import base64
import logging
import os
import sys
import threading
import time
import traceback
import wave

# Standard library queue is a single module renamed in py3. Robot is py2.7.
try:
    import Queue as _queue
except ImportError:  # pragma: no cover — only here so py3 import doesn't crash
    import queue as _queue

# naoqi is only present on the robot. On a developer Mac running unit tests
# the import will fail; we guard so the module is at least importable for
# syntax / smoke checks.
try:
    import naoqi  # noqa: F401  — used for ALModule + ALProxy
    from naoqi import ALModule, ALProxy
    _NAOQI_AVAILABLE = True
except ImportError:
    naoqi = None
    _NAOQI_AVAILABLE = False

    class ALModule(object):  # noqa: D401
        """Stub for off-robot import. Real ALModule comes from naoqi."""
        def __init__(self, name):
            self.name = name

    def ALProxy(*args, **kwargs):  # noqa: D401
        raise RuntimeError("ALProxy unavailable: naoqi not importable here")


# ── Audio constants ─────────────────────────────────────────────────────────
SAMPLE_RATE_HZ = 16000
SAMPLE_WIDTH = 2                # 16-bit signed
CHANNELS = 1                    # mono (front mic only)
BYTES_PER_SECOND = SAMPLE_RATE_HZ * SAMPLE_WIDTH * CHANNELS  # 32_000
CHUNK_MS = 20
BYTES_PER_CHUNK = BYTES_PER_SECOND * CHUNK_MS // 1000        # 640

# Queue cap. 200 chunks * 20 ms = 4 000 ms = 4 s of audio buffered. Bigger
# than that means the WS sender is so backed up the conversation already
# fell apart; drop the oldest frames so we don't OOM and so the next
# successful flush is "now" not "5 seconds ago".
QUEUE_MAXSIZE = 200

# Fragment-mode (fallback) settings.
FRAGMENT_MS = 250
FRAGMENT_DIR = "/home/nao/recordings/_stream"
FRAGMENT_CHANNELS_MASK = (0, 0, 1, 0)   # front mic mono

# ALAudioDevice channel index constants (Aldebaran docs).
AL_CHANNEL_ALL = 0
AL_CHANNEL_LEFT = 1
AL_CHANNEL_RIGHT = 2
AL_CHANNEL_FRONT = 3
AL_CHANNEL_REAR = 4

DEFAULT_MODULE_NAME = "NaoAudioStreamer"

logger = logging.getLogger(__name__)


def _b64_text(raw_bytes):
    """Return a *text* base64 string suitable for JSON envelopes."""
    encoded = base64.b64encode(raw_bytes)
    if isinstance(encoded, bytes):
        try:
            encoded = encoded.decode("ascii")
        except Exception:
            encoded = str(encoded)
    return encoded


def _now_ms():
    """Wall-clock ms (float). Robot has no monotonic clock that returns ms."""
    return time.time() * 1000.0


# ── Main class ──────────────────────────────────────────────────────────────
class NaoAudioStreamer(ALModule):
    """ALModule that subscribes to ALAudioDevice front-mic frames.

    Parameters
    ----------
    broker_ip : str
        IP that ``ALBroker`` is bound to. For an in-process broker started by
        the caller, pass the broker's IP (typically ``"0.0.0.0"`` for local).
    broker_port : int
        Port the broker listens on. The streamer uses this only when
        instantiating ``ALProxy``.
    nao_ip : str
        Address of the *NAO* ALMain broker (where ``ALAudioDevice`` lives).
        On the robot this is normally ``"127.0.0.1"`` or the value from
        ``config.NAO_IP``.
    nao_port : int, default 9559
    name : str, default ``"NaoAudioStreamer"``
        Naoqi module name. MUST be globally unique inside the broker process
        and match the variable name we expose into ``__main__`` (see
        ``_register_in_main``). Multiple instances must use distinct names.
    queue_maxsize : int, default 200
    chunk_ms : int, default 20
    """

    def __init__(self,
                 broker_ip="0.0.0.0",
                 broker_port=0,
                 nao_ip=None,
                 nao_port=9559,
                 name=DEFAULT_MODULE_NAME,
                 queue_maxsize=QUEUE_MAXSIZE,
                 chunk_ms=CHUNK_MS):
        # ALModule.__init__ registers the module name with the running
        # broker so the C++ audio device can dispatch processRemote() back
        # to this instance.
        ALModule.__init__(self, name)

        self.module_name = name
        self.broker_ip = broker_ip
        self.broker_port = broker_port
        # NAO_IP defaults — pull from env if caller didn't provide.
        self.nao_ip = nao_ip or os.environ.get("NAO_IP", "127.0.0.1")
        self.nao_port = int(nao_port)
        self.chunk_ms = int(chunk_ms)
        self.bytes_per_chunk = (
            SAMPLE_RATE_HZ * SAMPLE_WIDTH * CHANNELS * self.chunk_ms // 1000
        )
        if self.bytes_per_chunk <= 0:
            raise ValueError("chunk_ms produced zero bytes_per_chunk")

        # Queue holds (seq, ts_ms, b64_str) triples.
        self._queue = _queue.Queue(maxsize=int(queue_maxsize))
        self._queue_lock = threading.Lock()

        # State flags.
        self._streaming = False
        self._gate_closed = False
        self._subscribed = False
        self._seq = 0
        self._dropped = 0
        # PCM tail kept across processRemote() calls so a non-multiple-of-20ms
        # incoming buffer aligns cleanly to 20 ms chunks.
        self._tail = b""

        # Proxies — created lazily on start().
        self._audio_dev = None
        self._recorder = None

        # Mode set by start(). One of:
        #   "alaudio_device"            — preferred subscriber path
        #   "alaudio_recorder_fragment" — disk-fragment fallback
        #   None                        — not started
        self.mode = None

        # Fallback-mode worker.
        self._fragment_thread = None
        self._fragment_stop = threading.Event()

        # Register the instance into __main__ so the broker can resolve
        # ``self.module_name`` for processRemote dispatch. This is the
        # canonical naoqi ALModule pattern.
        self._register_in_main()

    # ── Lifecycle ───────────────────────────────────────────────────────────
    def _register_in_main(self):
        """Make the streamer reachable as ``__main__.<module_name>``.

        The naoqi ALBroker resolves an ALModule by looking up
        ``__main__.<name>`` when it dispatches a remote call. If the
        instance isn't there, ``processRemote`` will never be invoked.
        """
        try:
            main_module = sys.modules.get("__main__")
            if main_module is not None:
                setattr(main_module, self.module_name, self)
        except Exception:
            # Non-fatal — caller may have set this up themselves.
            pass

    def _connect_proxies(self):
        """Open ALAudioDevice + ALAudioRecorder proxies. Called on start()."""
        if not _NAOQI_AVAILABLE:
            raise RuntimeError(
                "naoqi import failed; cannot connect ALAudioDevice. "
                "This module is intended to run on the robot."
            )
        self._audio_dev = ALProxy("ALAudioDevice", self.nao_ip, self.nao_port)
        # Recorder proxy is only used in fallback mode but cheap to obtain.
        try:
            self._recorder = ALProxy(
                "ALAudioRecorder", self.nao_ip, self.nao_port
            )
        except Exception:
            self._recorder = None

    def start(self):
        """Begin streaming. Picks the best available capture mode.

        Returns the chosen mode string (also stored on ``self.mode``).
        """
        if self._streaming:
            return self.mode

        self._connect_proxies()
        self._seq = 0
        self._dropped = 0
        self._tail = b""
        self._gate_closed = False

        # Try the live subscriber path first.
        try:
            self._setup_subscriber()
            self._do_subscribe()
            self.mode = "alaudio_device"
            self._streaming = True
            logger.info(
                "[audio_module] subscribed to ALAudioDevice "
                "(rate=%d hz, chunk=%d ms, mode=%s)",
                SAMPLE_RATE_HZ, self.chunk_ms, self.mode,
            )
            return self.mode
        except Exception as exc:
            logger.warning(
                "[audio_module] ALAudioDevice subscribe failed (%s): %s — "
                "falling back to fragment recorder",
                type(exc).__name__, exc,
            )

        # Fallback: short-fragment recorder.
        try:
            self._start_fragment_recorder()
            self.mode = "alaudio_recorder_fragment"
            self._streaming = True
            logger.info(
                "[audio_module] running in FRAGMENT fallback mode "
                "(fragment_ms=%d)", FRAGMENT_MS,
            )
            return self.mode
        except Exception as exc:
            self._streaming = False
            self.mode = None
            raise RuntimeError(
                "Both ALAudioDevice subscribe and ALAudioRecorder fragment "
                "fallback failed: " + repr(exc)
            )

    def stop(self):
        """Stop streaming. Drains the queue and resets sequence numbers."""
        if not self._streaming:
            return

        self._streaming = False

        if self.mode == "alaudio_device":
            try:
                self._do_unsubscribe()
            except Exception as exc:
                logger.warning("[audio_module] unsubscribe on stop failed: %s", exc)
        elif self.mode == "alaudio_recorder_fragment":
            self._stop_fragment_recorder()

        # Drain queue.
        with self._queue_lock:
            try:
                while True:
                    self._queue.get_nowait()
            except _queue.Empty:
                pass

        self._seq = 0
        self._tail = b""
        self.mode = None
        logger.info(
            "[audio_module] stopped. dropped_frames=%d", self._dropped,
        )

    def gate(self, closed):
        """Mute / unmute the mic (idempotent).

        Called from the WS receive thread when TTS playback starts/ends.
        Closing the gate ``unsubscribe()``s so the audio device stops
        delivering frames at all (also stops feeding our process buffer);
        the next ALAudioDevice frame boundary is at most 20 ms away so
        worst-case leak after this call is ~20 ms. That comfortably beats
        the Phase 1 < 50 ms target.

        In fragment-mode the gate stops/starts the recorder; latency for
        the gate to take effect is up to one fragment (250 ms).
        """
        closed = bool(closed)
        if closed == self._gate_closed:
            return  # idempotent — already in requested state

        self._gate_closed = closed

        if not self._streaming:
            # Gate state stored, will apply on next start().
            return

        if self.mode == "alaudio_device":
            if closed:
                try:
                    self._do_unsubscribe()
                except Exception as exc:
                    logger.warning("[audio_module] gate-close unsubscribe failed: %s", exc)
            else:
                try:
                    self._do_subscribe()
                except Exception as exc:
                    logger.warning("[audio_module] gate-open subscribe failed: %s", exc)
                    # Try one re-setup in case prefs got dropped.
                    try:
                        self._setup_subscriber()
                        self._do_subscribe()
                    except Exception as exc2:
                        logger.error(
                            "[audio_module] gate-open recovery failed: %s", exc2
                        )

        elif self.mode == "alaudio_recorder_fragment":
            if closed:
                self._stop_fragment_recorder()
            else:
                # Re-arm the fragment loop. Reset the stop flag and spin a new
                # worker; the previous one has already exited.
                self._fragment_stop = threading.Event()
                try:
                    self._start_fragment_recorder()
                except Exception as exc:
                    logger.error(
                        "[audio_module] fragment re-arm after gate-open failed: %s",
                        exc,
                    )

    # ── ALAudioDevice subscriber path ───────────────────────────────────────
    def _setup_subscriber(self):
        """Tell ALAudioDevice what audio format we want."""
        # Some firmware exposes ``setClientPreferences`` with positional
        # ``(name, sampleRate, channels, deinterleave)``. Using channels=3
        # (FRONT mic) and deinterleave=0 to receive only that single channel
        # in the inputBuffer. We request 16 kHz mono.
        self._audio_dev.setClientPreferences(
            self.module_name,
            SAMPLE_RATE_HZ,
            AL_CHANNEL_FRONT,
            0,
        )

    def _do_subscribe(self):
        if self._subscribed:
            return
        self._audio_dev.subscribe(self.module_name)
        self._subscribed = True

    def _do_unsubscribe(self):
        if not self._subscribed:
            return
        try:
            self._audio_dev.unsubscribe(self.module_name)
        finally:
            # Even if unsubscribe raised, we don't want to keep believing
            # we're subscribed — the next start() will re-attempt.
            self._subscribed = False

    def processRemote(self, nbOfChannels, nbOfSamplesByChannel, timeStamp, inputBuffer):
        """ALAudioDevice callback. Slice into 20 ms chunks and enqueue.

        ``inputBuffer`` is a Python ``str`` (py2) of raw little-endian PCM16
        samples for one channel (we asked for FRONT only). Length =
        nbOfSamplesByChannel * 2 bytes.
        """
        if not self._streaming or self._gate_closed:
            return

        try:
            # On py2.7 ``inputBuffer`` is a str; on py3 it could be bytes.
            data = inputBuffer if isinstance(inputBuffer, (bytes, str)) else bytes(inputBuffer)
            if isinstance(data, str):
                # py2: str is bytes; py3: this branch shouldn't run on robot.
                pcm = data
            else:
                pcm = data

            # Some firmwares deliver multi-channel even when prefs ask for one.
            # Handle that defensively: pick the FRONT-equivalent channel.
            if nbOfChannels and nbOfChannels > 1:
                # Interleaved ch0..chN-1 samples. We requested FRONT (idx 3),
                # but if more than 1 channel arrived it's because firmware
                # ignored our prefs and we got ALL channels. Trust the order
                # documented by ALAudioDevice: 0=LEFT,1=RIGHT,2=FRONT,3=REAR.
                pcm = self._extract_channel(
                    pcm, nbOfChannels, channel_index=2  # 2 = FRONT in 4-ch ALL
                )

            # Concatenate with the previous tail so 20 ms boundaries align.
            buf = self._tail + pcm
            sliced = []
            offset = 0
            while offset + self.bytes_per_chunk <= len(buf):
                sliced.append(buf[offset:offset + self.bytes_per_chunk])
                offset += self.bytes_per_chunk
            self._tail = buf[offset:]

            # Build (seq, ts_ms, b64) triples and push.
            base_ts_ms = _now_ms()
            for i, chunk in enumerate(sliced):
                seq = self._seq
                self._seq = (self._seq + 1) & 0xFFFFFFFF  # 32-bit wrap-around
                # Per-chunk timestamp: best estimate, base_ts is "now" at
                # callback receipt; subtract back from end for chunk start.
                chunk_ts_ms = base_ts_ms + (i * self.chunk_ms)
                self._enqueue(seq, chunk_ts_ms, chunk)

        except Exception:
            # processRemote runs on a naoqi worker thread; any uncaught
            # exception will be eaten silently by the broker. Log explicitly.
            logger.error(
                "[audio_module] processRemote failed:\n%s",
                traceback.format_exc(),
            )

    def _extract_channel(self, interleaved_pcm, n_channels, channel_index):
        """Pull one channel out of an interleaved PCM16 buffer.

        Each frame is ``n_channels * 2`` bytes; the wanted channel sits at
        ``channel_index * 2 .. channel_index * 2 + 2`` within that frame.
        """
        if n_channels <= 0 or channel_index < 0 or channel_index >= n_channels:
            return interleaved_pcm
        frame_bytes = n_channels * 2
        if frame_bytes == 0 or len(interleaved_pcm) < frame_bytes:
            return interleaved_pcm
        # Slice every nth frame. Fast enough for ~170 ms (~5440 bytes) at
        # 20 Hz callback rate; no numpy on robot, so use bytes concat.
        # On py2.7 str is bytes so b"".join works on str slices identically.
        chan_start = channel_index * 2
        parts = []
        for i in range(0, len(interleaved_pcm) - frame_bytes + 1, frame_bytes):
            parts.append(interleaved_pcm[i + chan_start:i + chan_start + 2])
        if not parts:
            return interleaved_pcm
        return type(parts[0])().join(parts)

    # ── ALAudioRecorder fallback path ───────────────────────────────────────
    def _start_fragment_recorder(self):
        """Spin a thread that records 250 ms WAVs and slices them to 20 ms."""
        if self._recorder is None:
            raise RuntimeError("ALAudioRecorder proxy unavailable for fallback")
        try:
            os.makedirs(FRAGMENT_DIR)
        except OSError:
            pass  # already exists

        self._fragment_stop.clear()
        self._fragment_thread = threading.Thread(target=self._fragment_worker)
        self._fragment_thread.daemon = True
        self._fragment_thread.start()

    def _stop_fragment_recorder(self):
        self._fragment_stop.set()
        try:
            self._recorder.stopMicrophonesRecording()
        except Exception:
            pass
        if self._fragment_thread is not None:
            self._fragment_thread.join(timeout=1.0)
        self._fragment_thread = None

    def _fragment_worker(self):
        """Loop: record FRAGMENT_MS, read WAV, push 20 ms slices."""
        idx = 0
        while not self._fragment_stop.is_set() and self._streaming:
            if self._gate_closed:
                time.sleep(0.05)
                continue
            idx = (idx + 1) % 1000
            path = os.path.join(FRAGMENT_DIR, "frag_{0}.wav".format(idx))
            try:
                # Stop any prior recording first; idempotent if none running.
                try:
                    self._recorder.stopMicrophonesRecording()
                except Exception:
                    pass
                self._recorder.startMicrophonesRecording(
                    path, "wav", SAMPLE_RATE_HZ, FRAGMENT_CHANNELS_MASK,
                )
                # Sleep for exactly the fragment window; this is the dominant
                # latency contributor in fallback mode.
                time.sleep(FRAGMENT_MS / 1000.0)
                try:
                    self._recorder.stopMicrophonesRecording()
                except Exception:
                    pass

                pcm = self._read_wav_pcm(path)
                if pcm:
                    self._slice_and_enqueue(pcm)
            except Exception:
                logger.error(
                    "[audio_module] fragment worker error:\n%s",
                    traceback.format_exc(),
                )
                # Don't tight-loop on errors.
                time.sleep(0.05)
            finally:
                if os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception:
                        pass

    def _read_wav_pcm(self, path):
        """Read 16 kHz mono PCM16 bytes out of a WAV file."""
        try:
            wf = wave.open(path, "rb")
            try:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                    return b""
                return wf.readframes(wf.getnframes())
            finally:
                wf.close()
        except Exception:
            return b""

    def _slice_and_enqueue(self, pcm):
        """Slice raw PCM into 20 ms chunks and enqueue (used by fallback)."""
        buf = self._tail + pcm
        offset = 0
        base_ts_ms = _now_ms()
        chunk_idx = 0
        while offset + self.bytes_per_chunk <= len(buf):
            chunk = buf[offset:offset + self.bytes_per_chunk]
            offset += self.bytes_per_chunk
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            chunk_ts_ms = base_ts_ms + (chunk_idx * self.chunk_ms)
            self._enqueue(seq, chunk_ts_ms, chunk)
            chunk_idx += 1
        self._tail = buf[offset:]

    # ── Queue + drop policy ─────────────────────────────────────────────────
    def _enqueue(self, seq, ts_ms, raw_chunk):
        """Push (seq, ts_ms, b64) into the queue, dropping oldest on full."""
        b64 = _b64_text(raw_chunk)
        item = (seq, ts_ms, b64)
        try:
            self._queue.put_nowait(item)
        except _queue.Full:
            # Drop oldest. Best-effort — a concurrent reader may steal it
            # first; that's fine, the goal is to make space.
            try:
                _ = self._queue.get_nowait()
                self._dropped += 1
                if self._dropped == 1 or self._dropped % 50 == 0:
                    logger.warning(
                        "[audio_module] queue full; dropped %d frames so far",
                        self._dropped,
                    )
            except _queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except _queue.Full:
                # Reader is faster than us shedding; skip this item.
                self._dropped += 1

    def read_chunks(self, timeout=0.1):
        """Generator that yields (seq, ts_ms, b64) until ``stop()`` is called.

        ``timeout`` is the per-get blocking window in seconds. The generator
        loops indefinitely; the consumer should break out (e.g. on session
        close) by checking its own state. The generator exits cleanly when
        ``stop()`` has been called AND the queue is drained.
        """
        while True:
            if not self._streaming and self._queue.empty():
                return
            try:
                item = self._queue.get(timeout=timeout)
            except _queue.Empty:
                if not self._streaming:
                    return
                continue
            yield item

    # ── Introspection ───────────────────────────────────────────────────────
    @property
    def streaming(self):
        return self._streaming

    @property
    def gate_closed(self):
        return self._gate_closed

    @property
    def dropped_frames(self):
        return self._dropped

    @property
    def queue_depth(self):
        return self._queue.qsize()
