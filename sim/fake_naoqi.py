# -*- coding: utf-8 -*-
"""
Comprehensive in-process NAOqi fake for Phase 10.5 sim.

What this module does
---------------------
Injects a fake ``naoqi`` and ``qi`` module into ``sys.modules`` so any
import of either by ``nao/*`` (Python 2.7 robot code) resolves against
in-process fakes. The fakes provide:

  * ``ALProxy(service_name, ip, port)`` -- looks up the service in a
    registry and returns a wrapper whose method calls are recorded and
    optionally surfaced via ``on_event`` callbacks.
  * ``ALModule(name)`` base class -- our fakes register the module so
    ``FakeALAudioDevice.subscribe(name)`` can dispatch ``processRemote``
    callbacks to it (matching the contract used by
    ``nao/audio_module.py``).
  * 13 service fakes covering every method called from ``nao/*``:
        ALTextToSpeech, ALAudioDevice, ALAudioPlayer, ALAudioRecorder,
        ALFaceDetection, ALMotion, ALRobotPosture, ALLeds, ALMemory,
        ALSpeechRecognition, ALSoundLocalization, ALAutonomousLife,
        ALBehaviorManager.
    Plus a couple extras the rest of ``nao/*`` reaches for: ALAnimatedSpeech,
    ALAutonomousMoves, ALBasicAwareness, ALSpeakingMovement, ALPhotoCapture,
    ALTracker.
  * ``FakeBroker`` -- stub for code that constructs an ALBroker (none of
    our targeted nao files do at present, but we keep the surface so an
    ALModule can register without failing).
  * Top-level ``install_into_sys_modules(echo_sim=None, leds_renderer=None,
    on_event=None)`` and ``uninstall()`` (idempotent).

The fakes are deliberately minimal; only methods actually called from
``nao/*`` exist. Each fake records calls to ``self.calls`` (a list of
``(method, args, kwargs)`` tuples) so scenarios can assert behaviour.

PCM sourcing
------------
``FakeALAudioDevice.subscribe(name)`` looks up the named ``ALModule`` in
the global registry, spawns a daemon thread, and pulls 20 ms PCM frames
from a callable supplied externally via ``set_pcm_source(callable)``. The
callable returns ``bytes`` (PCM16-LE @ 16 kHz mono, length = 640 by
default). When live_nao.py is driving the sim, the source is the real Mac
mic; when scenarios drive it, the source is a canned WAV reader.

If an ``EchoSimulator`` is registered, every PCM frame is passed through
``echo.apply_to_pcm(...)`` so scenarios can exercise the server's echo
guard.

Threading
---------
All fakes are designed to be called from any thread. ``calls`` is a list
guarded by a per-instance lock; the audio dispatcher runs on a daemon
thread; the ``inject`` helpers fire callbacks synchronously (in the
caller's thread) so tests can drive transitions deterministically.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
import types
import weakref
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Audio constants -- MUST match what nao/audio_module.py expects.
# --------------------------------------------------------------------------
SAMPLE_RATE_HZ = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1
CHUNK_MS = 20
BYTES_PER_CHUNK = SAMPLE_RATE_HZ * SAMPLE_WIDTH * CHANNELS * CHUNK_MS // 1000  # 640


# --------------------------------------------------------------------------
# Global state
# --------------------------------------------------------------------------
_REGISTRY_LOCK = threading.RLock()
_SERVICE_REGISTRY: Dict[str, Any] = {}      # service_name -> instance
_ALMODULE_REGISTRY: Dict[str, "ALModule"] = {}  # module_name -> ALModule

# External hooks set by install_into_sys_modules.
_ECHO_SIM: Optional[Any] = None
_LEDS_RENDERER: Optional[Any] = None
_EVENT_CALLBACK: Optional[Callable[[str, Dict[str, Any]], None]] = None

# PCM source callable. Returns bytes (PCM16-LE) or None to indicate "no
# data right now -- skip this tick".
_PCM_SOURCE: Optional[Callable[[], Optional[bytes]]] = None
_PCM_SOURCE_LOCK = threading.RLock()

# Backup of original sys.modules entries for clean uninstall. Maps
# "naoqi" / "qi" / "naoqi.*" -> (was_present, original_value).
_INSTALLED_MODULES: Dict[str, Tuple[bool, Any]] = {}
_INSTALL_LOCK = threading.RLock()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _now_ms() -> float:
    return time.time() * 1000.0


def _emit_event(kind: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Forward a structured event to the registered ``on_event`` callback.

    Wrapped in try/except so a buggy test callback can't break the sim.
    """
    cb = _EVENT_CALLBACK
    if cb is None:
        return
    try:
        cb(kind, dict(data) if data else {})
    except Exception:
        logger.exception("[fake_naoqi] on_event callback raised")


def _record_call(target: Any, method: str, args: tuple, kwargs: dict) -> None:
    """Append a ``(method, args, kwargs)`` tuple to ``target.calls``.

    All fakes opt into this via ``self._record(method, args, kwargs)``.
    """
    try:
        target.calls.append((method, tuple(args), dict(kwargs)))
    except Exception:
        # Defensive: never let a bookkeeping append crash the call.
        pass


def set_pcm_source(source: Optional[Callable[[], Optional[bytes]]]) -> None:
    """Set the global PCM source callable used by FakeALAudioDevice.

    The callable is invoked once per CHUNK_MS by the audio dispatcher
    thread when an ALModule is subscribed. It should return ``bytes`` of
    PCM16-LE (typically ``BYTES_PER_CHUNK`` bytes). Returning ``None`` is
    a no-op for that tick (skip).

    Pass ``None`` to clear the source (dispatcher pauses).
    """
    global _PCM_SOURCE
    with _PCM_SOURCE_LOCK:
        _PCM_SOURCE = source


def get_pcm_source() -> Optional[Callable[[], Optional[bytes]]]:
    with _PCM_SOURCE_LOCK:
        return _PCM_SOURCE


def reset_state() -> None:
    """Clear registries, calls, hooks. Useful between scenarios."""
    global _ECHO_SIM, _LEDS_RENDERER, _EVENT_CALLBACK
    with _REGISTRY_LOCK:
        # Stop any subscribers before clearing.
        for inst in list(_SERVICE_REGISTRY.values()):
            stop = getattr(inst, "_stop_all_subscribers", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        _SERVICE_REGISTRY.clear()
        _ALMODULE_REGISTRY.clear()
    _ECHO_SIM = None
    _LEDS_RENDERER = None
    _EVENT_CALLBACK = None
    set_pcm_source(None)


# --------------------------------------------------------------------------
# ALModule base
# --------------------------------------------------------------------------
class ALModule(object):
    """Base class for ALModule subclasses (e.g., NaoAudioStreamer).

    The naoqi convention is: subclass ``ALModule``, call
    ``ALModule.__init__(self, name)`` to register the module under ``name``,
    then expose any number of remote-callable methods (``processRemote`` is
    the canonical one for audio).

    Our fake just stuffs the instance into a global registry keyed by name
    so ``FakeALAudioDevice.subscribe(name)`` can find it.
    """

    def __init__(self, name):
        self.name = name
        with _REGISTRY_LOCK:
            _ALMODULE_REGISTRY[str(name)] = self
        _emit_event("almodule_registered", {"name": name})

    # Real naoqi exposes a processRemote stub here. We don't override it
    # so subclasses can define theirs naturally; the dispatcher tolerates
    # missing methods.


def get_almodule(name):
    """Return the registered ALModule instance for ``name`` or None."""
    with _REGISTRY_LOCK:
        return _ALMODULE_REGISTRY.get(str(name))


# --------------------------------------------------------------------------
# Base class for service fakes
# --------------------------------------------------------------------------
class _BaseFakeService(object):
    """Common scaffolding for all FakeAL* services.

    Provides the ``calls`` list, an instance lock, and a ``_record``
    helper. Subclasses define their own methods.
    """

    SERVICE_NAME = ""  # set by subclasses

    def __init__(self):
        self.calls: List[Tuple[str, tuple, dict]] = []
        self._lock = threading.RLock()

    def _record(self, method: str, args: tuple = (), kwargs: dict = None) -> None:
        with self._lock:
            self.calls.append((method, tuple(args), dict(kwargs or {})))
        _emit_event("al_call", {
            "service": self.SERVICE_NAME,
            "method": method,
            "args": list(args),
            "kwargs": dict(kwargs or {}),
        })


# --------------------------------------------------------------------------
# ALMemory -- key-value store with subscription
# --------------------------------------------------------------------------
class FakeALMemory(_BaseFakeService):
    """In-memory ALMemory replacement.

    API:
      * ``getData(key)``                 -> last set value or None.
      * ``insertData(key, value)``       -> set value, fire subscribers.
      * ``subscribeToEvent(name, module_name, callback)`` -> register a
        callback that fires whenever ``insertData(name, ...)`` is called.
      * ``unsubscribeToEvent(name, module_name)`` -> reverse.
      * ``inject(key, value)``           -> test-only: same as
        ``insertData`` but emits an explicit ``almemory_inject`` event so
        scenarios can trace whether a given event came from production
        code or from a test injection.
    """

    SERVICE_NAME = "ALMemory"

    def __init__(self):
        super().__init__()
        self._data: Dict[str, Any] = {}
        # event_name -> list of (module_name, callback)
        self._subscribers: Dict[str, List[Tuple[str, Callable]]] = {}

    # --- public NAOqi API ----------------------------------------------
    def getData(self, key, *args, **kwargs):
        self._record("getData", (key,) + tuple(args), kwargs)
        with self._lock:
            return self._data.get(str(key))

    def insertData(self, key, value, *args, **kwargs):
        self._record("insertData", (key, value) + tuple(args), kwargs)
        self._set_and_fire(key, value)

    def declareEvent(self, name, *args, **kwargs):
        # Some firmware exposes this; in our fake it's a no-op (the data
        # dict allows arbitrary keys without prior declaration).
        self._record("declareEvent", (name,) + tuple(args), kwargs)

    def subscribeToEvent(self, event_name, module_name, callback_method):
        """Register ``callback_method`` (a name OR callable) for an event.

        On naoqi the callback is the *name* of a method on the ALModule
        instance registered as ``module_name``. We accept either:
          * a callable directly (preferred for tests), or
          * a string method name -- we resolve it against the registered
            ALModule and store the bound method.
        """
        self._record("subscribeToEvent",
                     (event_name, module_name, str(callback_method)), {})
        cb = self._resolve_callback(module_name, callback_method)
        if cb is None:
            return
        with self._lock:
            self._subscribers.setdefault(str(event_name), []).append(
                (str(module_name), cb)
            )

    def unsubscribeToEvent(self, event_name, module_name):
        self._record("unsubscribeToEvent", (event_name, module_name), {})
        with self._lock:
            subs = self._subscribers.get(str(event_name))
            if not subs:
                return
            self._subscribers[str(event_name)] = [
                (mn, cb) for mn, cb in subs if mn != str(module_name)
            ]

    # --- test helpers --------------------------------------------------
    def inject(self, key, value):
        """Test-only: set a value and fire subscribers.

        Functionally identical to ``insertData`` but emits an explicit
        event so scenarios distinguish test injections from production
        writes.
        """
        _emit_event("almemory_inject", {"key": key, "value": value})
        self._set_and_fire(key, value)

    def keys(self):
        with self._lock:
            return list(self._data.keys())

    # --- internals -----------------------------------------------------
    def _set_and_fire(self, key, value) -> None:
        with self._lock:
            self._data[str(key)] = value
            subs = list(self._subscribers.get(str(key), []))
        for module_name, cb in subs:
            try:
                # NAOqi callback signature: (event_name, value, msg).
                cb(str(key), value, "")
            except Exception:
                logger.exception(
                    "[fake_naoqi] ALMemory subscriber %s raised on %s",
                    module_name, key,
                )

    @staticmethod
    def _resolve_callback(module_name, callback_method):
        if callable(callback_method):
            return callback_method
        mod = get_almodule(module_name)
        if mod is None:
            return None
        cb = getattr(mod, str(callback_method), None)
        return cb if callable(cb) else None


# --------------------------------------------------------------------------
# ALTextToSpeech / ALAnimatedSpeech
# --------------------------------------------------------------------------
class FakeALTextToSpeech(_BaseFakeService):
    SERVICE_NAME = "ALTextToSpeech"

    def __init__(self):
        super().__init__()
        self.volume = 1.0
        self.language = "English"
        self.spoken_lines: List[str] = []
        # Async post namespace -- mirrors naoqi's proxy.post.<method>(...).
        self.post = _PostProxy(self)

    def say(self, text, *args, **kwargs):
        self._record("say", (text,) + tuple(args), kwargs)
        try:
            self.spoken_lines.append(text)
        except Exception:
            pass
        _emit_event("tts_say", {"text": str(text)})

    def setVolume(self, level, *args, **kwargs):
        self._record("setVolume", (level,) + tuple(args), kwargs)
        try:
            self.volume = float(level)
        except Exception:
            pass

    def getVolume(self, *args, **kwargs):
        self._record("getVolume", tuple(args), kwargs)
        return float(self.volume)

    def setLanguage(self, lang, *args, **kwargs):
        self._record("setLanguage", (lang,) + tuple(args), kwargs)
        self.language = str(lang)

    def setParameter(self, name, value, *args, **kwargs):
        self._record("setParameter", (name, value) + tuple(args), kwargs)


class FakeALAnimatedSpeech(_BaseFakeService):
    SERVICE_NAME = "ALAnimatedSpeech"

    def __init__(self):
        super().__init__()
        self.post = _PostProxy(self)

    def say(self, text, *args, **kwargs):
        self._record("say", (text,) + tuple(args), kwargs)
        _emit_event("tts_say", {"text": str(text), "animated": True})


# --------------------------------------------------------------------------
# ALAudioDevice -- the trickiest fake. Manages PCM dispatch to ALModule
# subscribers via a daemon thread.
# --------------------------------------------------------------------------
class FakeALAudioDevice(_BaseFakeService):
    """Fake ALAudioDevice. Dispatches PCM frames to registered ALModules.

    On ``subscribe(name)`` we spawn a daemon thread that:
      1. Pulls 20 ms of PCM from the global ``_PCM_SOURCE`` callable.
      2. Optionally runs it through the ``EchoSimulator``.
      3. Calls ``module.processRemote(channels, samples_per_channel,
         (sec, usec), pcm_bytes)`` on the ALModule registered under
         ``name``.
      4. Sleeps until the next ``CHUNK_MS`` boundary.

    Per ALAudioDevice's documented signature, ``processRemote`` receives:
        nbOfChannels         -- always 1 (we requested mono FRONT).
        nbOfSamplesByChannel -- BYTES_PER_CHUNK / 2 = 320.
        timeStamp            -- (seconds, microseconds) tuple.
        inputBuffer          -- PCM16-LE bytes (length 640).
    """

    SERVICE_NAME = "ALAudioDevice"

    def __init__(self):
        super().__init__()
        self.output_volume = 100
        self.front_mic_energy = 0.0
        self._client_prefs: Dict[str, dict] = {}
        # name -> _SubscriberThread
        self._subscribers: Dict[str, "_SubscriberThread"] = {}

    # -- volume / energy ------------------------------------------------
    def setOutputVolume(self, level, *args, **kwargs):
        self._record("setOutputVolume", (level,) + tuple(args), kwargs)
        try:
            self.output_volume = int(level)
        except Exception:
            pass

    def getOutputVolume(self, *args, **kwargs):
        self._record("getOutputVolume", tuple(args), kwargs)
        return int(self.output_volume)

    def getFrontMicEnergy(self, *args, **kwargs):
        self._record("getFrontMicEnergy", tuple(args), kwargs)
        # Test seam: scenarios can drive energy by writing to .front_mic_energy.
        return float(self.front_mic_energy)

    # -- subscriber lifecycle -------------------------------------------
    def setClientPreferences(self, name, sample_rate, channels, deinterleave,
                             *args, **kwargs):
        self._record("setClientPreferences",
                     (name, sample_rate, channels, deinterleave) + tuple(args),
                     kwargs)
        with self._lock:
            self._client_prefs[str(name)] = {
                "sample_rate": int(sample_rate),
                "channels": int(channels),
                "deinterleave": int(deinterleave),
            }

    def subscribe(self, name, *args, **kwargs):
        self._record("subscribe", (name,) + tuple(args), kwargs)
        with self._lock:
            existing = self._subscribers.get(str(name))
            if existing is not None and existing.alive:
                # Already subscribed; idempotent.
                return
            sub = _SubscriberThread(str(name))
            self._subscribers[str(name)] = sub
        sub.start()
        _emit_event("audio_subscribe", {"name": name})

    def unsubscribe(self, name, *args, **kwargs):
        self._record("unsubscribe", (name,) + tuple(args), kwargs)
        with self._lock:
            sub = self._subscribers.pop(str(name), None)
        if sub is not None:
            sub.stop()
        _emit_event("audio_unsubscribe", {"name": name})

    # -- private helpers used by reset_state ----------------------------
    def _stop_all_subscribers(self) -> None:
        with self._lock:
            subs = list(self._subscribers.values())
            self._subscribers.clear()
        for s in subs:
            s.stop()


class _SubscriberThread(threading.Thread):
    """Daemon thread that pulls PCM and feeds it into an ALModule."""

    def __init__(self, module_name: str):
        super().__init__(daemon=True, name="fake-audio-sub-" + module_name)
        self.module_name = module_name
        self._stop_event = threading.Event()
        self.alive = False

    def stop(self) -> None:
        self._stop_event.set()
        # Don't join here -- caller may already hold a lock the worker
        # needs to flush. Daemon thread won't outlive the process.

    def run(self) -> None:
        self.alive = True
        try:
            # Sleep granularity: 20 ms (one chunk).
            chunk_seconds = CHUNK_MS / 1000.0
            next_tick = time.time()
            while not self._stop_event.is_set():
                # Pull PCM from the global source.
                src = get_pcm_source()
                pcm: Optional[bytes] = None
                if src is not None:
                    try:
                        pcm = src()
                    except Exception:
                        logger.exception("[fake_naoqi] pcm_source raised")
                        pcm = None

                if pcm:
                    # Apply echo, if any.
                    if _ECHO_SIM is not None:
                        try:
                            pcm = _ECHO_SIM.apply_to_pcm(
                                pcm, _now_ms(), SAMPLE_RATE_HZ
                            )
                        except Exception:
                            logger.exception(
                                "[fake_naoqi] echo_sim.apply_to_pcm raised"
                            )

                    mod = get_almodule(self.module_name)
                    if mod is not None:
                        proc = getattr(mod, "processRemote", None)
                        if callable(proc):
                            samples = len(pcm) // SAMPLE_WIDTH
                            ts = time.time()
                            ts_pair = (int(ts), int((ts - int(ts)) * 1e6))
                            try:
                                proc(1, samples, ts_pair, pcm)
                            except Exception:
                                logger.exception(
                                    "[fake_naoqi] processRemote raised in %s",
                                    self.module_name,
                                )

                # Pace next tick. We use absolute deadlines so a slow
                # processRemote doesn't slip the cadence forever.
                next_tick += chunk_seconds
                sleep = next_tick - time.time()
                if sleep > 0:
                    if self._stop_event.wait(timeout=sleep):
                        break
                else:
                    # Behind schedule -- skip ahead so we don't busy-loop.
                    next_tick = time.time()
        finally:
            self.alive = False


# --------------------------------------------------------------------------
# ALAudioPlayer
# --------------------------------------------------------------------------
class FakeALAudioPlayer(_BaseFakeService):
    """Records playback intents and feeds them into the EchoSimulator.

    ``playFile(path)`` reads the file from disk so the simulator can mix
    real PCM/MP3 bytes back into the mic stream. Failures to read are
    swallowed (they can't break test scenarios).
    """

    SERVICE_NAME = "ALAudioPlayer"

    def __init__(self):
        super().__init__()
        self.master_volume = 1.0
        self._channels_in_use = 0
        self.played_files: List[str] = []
        self.post = _PostProxy(self)

    def playFile(self, path, *args, **kwargs):
        self._record("playFile", (path,) + tuple(args), kwargs)
        self.played_files.append(str(path))
        self._channels_in_use = 2
        _emit_event("audio_play_file", {"path": str(path)})
        # Best-effort: read the file into the echo simulator. We check
        # the magic bytes so PCM gets the right sample rate.
        if _ECHO_SIM is not None:
            try:
                with open(str(path), "rb") as fh:
                    data = fh.read()
                # If it's a WAV, strip the header to get raw PCM.
                if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
                    pcm = _strip_wav_header(data)
                    sr = _wav_sample_rate(data) or SAMPLE_RATE_HZ
                else:
                    pcm = data  # MP3/anything-else; sim will skip mixing.
                    sr = SAMPLE_RATE_HZ
                _ECHO_SIM.record_played(pcm, sample_rate=sr)
            except Exception:
                logger.debug(
                    "[fake_naoqi] could not feed echo from %s",
                    path, exc_info=True,
                )
        # Simulate playback finishing instantly. The streaming-tts code
        # polls getNumOfChannels() until it goes to 0 -- we drop it back
        # to 0 on the next call, so a tight poll loop sees one non-zero
        # then zeros forever. Good enough for tests.
        self._channels_in_use = 0
        return 1  # task id placeholder

    def stopAll(self, *args, **kwargs):
        self._record("stopAll", tuple(args), kwargs)
        self._channels_in_use = 0
        _emit_event("audio_stop_all", {})

    def stop(self, task_id, *args, **kwargs):
        self._record("stop", (task_id,) + tuple(args), kwargs)
        self._channels_in_use = 0

    def setMasterVolume(self, vol, *args, **kwargs):
        self._record("setMasterVolume", (vol,) + tuple(args), kwargs)
        try:
            self.master_volume = float(vol)
        except Exception:
            pass

    def getMasterVolume(self, *args, **kwargs):
        self._record("getMasterVolume", tuple(args), kwargs)
        return float(self.master_volume)

    def getNumOfChannels(self, *args, **kwargs):
        self._record("getNumOfChannels", tuple(args), kwargs)
        return int(self._channels_in_use)


def _strip_wav_header(data: bytes) -> bytes:
    """Return raw PCM bytes from a WAV file (best-effort).

    Walks the RIFF chunks until it finds 'data'. If parsing fails we
    return the whole buffer (caller's echo sim will treat as opaque).
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        try:
            chunk_size = int.from_bytes(data[pos + 4:pos + 8], "little")
        except Exception:
            return data
        if chunk_id == b"data":
            return data[pos + 8:pos + 8 + chunk_size]
        pos += 8 + chunk_size
    return data


def _wav_sample_rate(data: bytes) -> Optional[int]:
    """Return the sample rate from a WAV file's fmt chunk, or None."""
    if len(data) < 28 or data[:4] != b"RIFF":
        return None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        try:
            chunk_size = int.from_bytes(data[pos + 4:pos + 8], "little")
        except Exception:
            return None
        if chunk_id == b"fmt ":
            try:
                return int.from_bytes(data[pos + 12:pos + 16], "little")
            except Exception:
                return None
        pos += 8 + chunk_size
    return None


# --------------------------------------------------------------------------
# ALAudioRecorder
# --------------------------------------------------------------------------
class FakeALAudioRecorder(_BaseFakeService):
    SERVICE_NAME = "ALAudioRecorder"

    def __init__(self):
        super().__init__()
        self.recording = False
        self.last_path: Optional[str] = None

    def startMicrophonesRecording(self, path, fmt, sample_rate, channels_mask,
                                  *args, **kwargs):
        self._record("startMicrophonesRecording",
                     (path, fmt, sample_rate, channels_mask) + tuple(args),
                     kwargs)
        self.recording = True
        self.last_path = str(path)
        _emit_event("recorder_start",
                    {"path": str(path), "format": str(fmt),
                     "sample_rate": int(sample_rate)})
        # Write a minimal valid WAV so any subsequent wave.open() in the
        # caller doesn't crash. Best-effort.
        try:
            self._write_tiny_wav(str(path), int(sample_rate))
        except Exception:
            pass

    def stopMicrophonesRecording(self, *args, **kwargs):
        self._record("stopMicrophonesRecording", tuple(args), kwargs)
        self.recording = False
        _emit_event("recorder_stop", {})

    @staticmethod
    def _write_tiny_wav(path: str, sample_rate: int) -> None:
        """Write an 80 ms silent WAV file so callers can wave.open() it."""
        import wave
        import os
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        n = max(1, int(sample_rate * 0.080))
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * n)


# --------------------------------------------------------------------------
# ALFaceDetection
# --------------------------------------------------------------------------
class FakeALFaceDetection(_BaseFakeService):
    """Manages subscriber names + writes face events into ALMemory.

    ``inject_face(...)`` is the test-only entry point: scenarios call it
    to push an ALFaceDetection-shaped payload into ALMemory["FaceDetected"]
    and fire any subscribed callbacks.
    """

    SERVICE_NAME = "ALFaceDetection"

    def __init__(self, memory: Optional[FakeALMemory] = None):
        super().__init__()
        self._memory = memory
        self._subscribers: Dict[str, dict] = {}
        self._learned_faces: List[str] = []
        self._tracking_enabled = False
        self._recognition_enabled = True

    def attach_memory(self, memory: FakeALMemory) -> None:
        self._memory = memory

    # -- naoqi public API -----------------------------------------------
    def subscribe(self, name, *args, **kwargs):
        self._record("subscribe", (name,) + tuple(args), kwargs)
        with self._lock:
            self._subscribers[str(name)] = {"period_ms": args[0] if args else 100}

    def unsubscribe(self, name, *args, **kwargs):
        self._record("unsubscribe", (name,) + tuple(args), kwargs)
        with self._lock:
            self._subscribers.pop(str(name), None)

    def learnFace(self, name, *args, **kwargs):
        self._record("learnFace", (name,) + tuple(args), kwargs)
        if name not in self._learned_faces:
            self._learned_faces.append(str(name))
        return True

    def forgetPerson(self, name, *args, **kwargs):
        self._record("forgetPerson", (name,) + tuple(args), kwargs)
        if name in self._learned_faces:
            self._learned_faces.remove(str(name))
        return True

    def clearDatabase(self, *args, **kwargs):
        self._record("clearDatabase", tuple(args), kwargs)
        self._learned_faces = []

    def getLearnedFacesList(self, *args, **kwargs):
        self._record("getLearnedFacesList", tuple(args), kwargs)
        return list(self._learned_faces)

    def setRecognitionEnabled(self, enabled, *args, **kwargs):
        self._record("setRecognitionEnabled", (enabled,) + tuple(args), kwargs)
        self._recognition_enabled = bool(enabled)

    def setTrackingEnabled(self, enabled, *args, **kwargs):
        self._record("setTrackingEnabled", (enabled,) + tuple(args), kwargs)
        self._tracking_enabled = bool(enabled)

    # -- test helpers ---------------------------------------------------
    def inject_face(self,
                    face_id: str = "test_face",
                    name: str = "",
                    confidence: float = 0.6,
                    distance_m: float = 0.7,
                    yaw_deg: float = 0.0,
                    pitch_deg: float = 0.0) -> None:
        """Push a synthetic ALFaceDetection event into ALMemory.

        The payload follows the documented Aldebaran shape:
            [timestamp, [face_info, ...], camera_pose, torso_pose, cam_id]
        with face_info = [shape_info, extra_info].
        """
        # Inverse of detect_faces_with_geometry's distance estimator.
        # Aim for size_x_norm such that:
        #   distance_m = (0.16 / 2) / tan(size_x * 60.97 deg / 2)
        import math
        hfov_rad = math.radians(60.97)
        if distance_m > 0:
            half = math.atan((0.16 / 2.0) / max(0.05, distance_m))
            size_x_norm = max(0.02, min(0.6, (2.0 * half) / hfov_rad))
        else:
            size_x_norm = 0.1
        # ALFaceDetection shape_info: [alpha, beta, size_x, size_y].
        alpha = math.radians(yaw_deg)
        beta = math.radians(pitch_deg)
        shape_info = [alpha, beta, size_x_norm, size_x_norm]
        extra_info = [str(face_id), float(confidence), str(name)]
        payload = [
            [int(time.time()), int(time.time() * 1e6) % int(1e6)],
            [[shape_info, extra_info]],
            [], [], 0,
        ]
        if self._memory is not None:
            # Use insertData so subscribers fire.
            self._memory.insertData("FaceDetected", payload)
        _emit_event("face_inject", {
            "face_id": face_id, "name": name, "confidence": confidence,
            "distance_m": distance_m,
            "yaw_deg": yaw_deg, "pitch_deg": pitch_deg,
        })

    def clear_face(self) -> None:
        """Clear the FaceDetected ALMemory entry."""
        if self._memory is not None:
            self._memory.insertData("FaceDetected", [])


# --------------------------------------------------------------------------
# ALMotion + ALRobotPosture
# --------------------------------------------------------------------------
class FakeALMotion(_BaseFakeService):
    SERVICE_NAME = "ALMotion"

    def __init__(self):
        super().__init__()
        self.stiffness: Dict[str, float] = {}
        self.angles: Dict[str, float] = {}
        self.breath_enabled: Dict[str, bool] = {}

    def setStiffnesses(self, names, values, *args, **kwargs):
        self._record("setStiffnesses", (names, values) + tuple(args), kwargs)

    def angleInterpolation(self, names, angles, times, isAbsolute,
                           *args, **kwargs):
        self._record("angleInterpolation",
                     (names, angles, times, isAbsolute) + tuple(args), kwargs)

    def angleInterpolationWithSpeed(self, names, angles, max_speed,
                                    *args, **kwargs):
        self._record("angleInterpolationWithSpeed",
                     (names, angles, max_speed) + tuple(args), kwargs)
        # Track the last setpoint so tests can assert pose state.
        try:
            if isinstance(names, str):
                self.angles[names] = float(angles)
            else:
                for n, a in zip(names, angles):
                    self.angles[str(n)] = float(a)
        except Exception:
            pass

    def setAngles(self, names, angles, fraction_max_speed, *args, **kwargs):
        self._record("setAngles",
                     (names, angles, fraction_max_speed) + tuple(args), kwargs)

    def setBreathEnabled(self, name, enabled, *args, **kwargs):
        self._record("setBreathEnabled",
                     (name, enabled) + tuple(args), kwargs)
        self.breath_enabled[str(name)] = bool(enabled)

    def stopMove(self, *args, **kwargs):
        self._record("stopMove", tuple(args), kwargs)

    def rest(self, *args, **kwargs):
        self._record("rest", tuple(args), kwargs)
        _emit_event("motion_rest", {})


class FakeALRobotPosture(_BaseFakeService):
    SERVICE_NAME = "ALRobotPosture"

    def __init__(self):
        super().__init__()
        self.posture = "Stand"

    def goToPosture(self, name, fraction, *args, **kwargs):
        self._record("goToPosture", (name, fraction) + tuple(args), kwargs)
        self.posture = str(name)
        _emit_event("posture_change", {"name": str(name)})
        return True


# --------------------------------------------------------------------------
# ALLeds
# --------------------------------------------------------------------------
class FakeALLeds(_BaseFakeService):
    """Forwards fadeRGB calls to the renderer (if any)."""

    SERVICE_NAME = "ALLeds"

    def fadeRGB(self, group, *args, **kwargs):
        self._record("fadeRGB", (group,) + tuple(args), kwargs)
        if _LEDS_RENDERER is not None:
            try:
                _LEDS_RENDERER.fadeRGB(group, *args)
            except Exception:
                logger.exception("[fake_naoqi] leds renderer raised")
        _emit_event("leds_fade", {"group": str(group), "args": list(args)})

    def setIntensity(self, name, intensity, *args, **kwargs):
        self._record("setIntensity", (name, intensity) + tuple(args), kwargs)

    def fade(self, name, intensity, duration, *args, **kwargs):
        self._record("fade",
                     (name, intensity, duration) + tuple(args), kwargs)

    def on(self, name, *args, **kwargs):
        self._record("on", (name,) + tuple(args), kwargs)

    def off(self, name, *args, **kwargs):
        self._record("off", (name,) + tuple(args), kwargs)

    def reset(self, name, *args, **kwargs):
        self._record("reset", (name,) + tuple(args), kwargs)


# --------------------------------------------------------------------------
# ALSpeechRecognition
# --------------------------------------------------------------------------
class FakeALSpeechRecognition(_BaseFakeService):
    """Subscribe + setVocabulary + manages the WordRecognized memory key.

    ``inject_word(word, conf)`` simulates a recognition event by writing
    to ALMemory["WordRecognized"]. wake_listener.py polls that key.
    """

    SERVICE_NAME = "ALSpeechRecognition"

    def __init__(self, memory: Optional[FakeALMemory] = None):
        super().__init__()
        self._memory = memory
        self.vocabulary: List[str] = []
        self.spotting = False
        self.language = "English"
        self.sensitivity = 0.5
        self.paused = False
        self.subscribers: List[str] = []

    def attach_memory(self, memory: FakeALMemory) -> None:
        self._memory = memory

    def setLanguage(self, lang, *args, **kwargs):
        self._record("setLanguage", (lang,) + tuple(args), kwargs)
        self.language = str(lang)

    def setVocabulary(self, vocab, spotting, *args, **kwargs):
        self._record("setVocabulary", (vocab, spotting) + tuple(args), kwargs)
        self.vocabulary = list(vocab) if vocab else []
        self.spotting = bool(spotting)

    def setParameter(self, name, value, *args, **kwargs):
        self._record("setParameter", (name, value) + tuple(args), kwargs)
        if str(name) == "Sensitivity":
            try:
                self.sensitivity = float(value)
            except Exception:
                pass

    def pause(self, paused, *args, **kwargs):
        self._record("pause", (paused,) + tuple(args), kwargs)
        self.paused = bool(paused)

    def subscribe(self, name, *args, **kwargs):
        self._record("subscribe", (name,) + tuple(args), kwargs)
        if str(name) not in self.subscribers:
            self.subscribers.append(str(name))

    def unsubscribe(self, name, *args, **kwargs):
        self._record("unsubscribe", (name,) + tuple(args), kwargs)
        try:
            self.subscribers.remove(str(name))
        except ValueError:
            pass

    # -- test helper ----------------------------------------------------
    def inject_word(self, word: str, conf: float = 0.9) -> None:
        if self._memory is not None:
            self._memory.insertData("WordRecognized", [str(word), float(conf)])
        _emit_event("asr_inject_word", {"word": word, "conf": conf})


# --------------------------------------------------------------------------
# ALSoundLocalization
# --------------------------------------------------------------------------
class FakeALSoundLocalization(_BaseFakeService):
    SERVICE_NAME = "ALSoundLocalization"

    def __init__(self, memory: Optional[FakeALMemory] = None):
        super().__init__()
        self._memory = memory
        self._subscribers: List[str] = []

    def attach_memory(self, memory: FakeALMemory) -> None:
        self._memory = memory

    def subscribe(self, name, *args, **kwargs):
        self._record("subscribe", (name,) + tuple(args), kwargs)
        if str(name) not in self._subscribers:
            self._subscribers.append(str(name))

    def unsubscribe(self, name, *args, **kwargs):
        self._record("unsubscribe", (name,) + tuple(args), kwargs)
        try:
            self._subscribers.remove(str(name))
        except ValueError:
            pass

    def setParameter(self, name, value, *args, **kwargs):
        self._record("setParameter", (name, value) + tuple(args), kwargs)

    def inject_direction(self,
                         azimuth_deg: float,
                         elevation_deg: float = 0.0,
                         confidence: float = 0.7,
                         energy: float = 0.5) -> None:
        """Push a SoundLocated event into ALMemory.

        Payload shape (per Aldebaran docs)::

            [[ts_sec, ts_usec], [confidence, energy],
             [azimuth_rad, elevation_rad, head_x, head_y]]
        """
        import math
        ts = time.time()
        payload = [
            [int(ts), int((ts - int(ts)) * 1e6)],
            [float(confidence), float(energy)],
            [math.radians(azimuth_deg), math.radians(elevation_deg), 0.0, 0.0],
        ]
        if self._memory is not None:
            self._memory.insertData("ALSoundLocalization/SoundLocated", payload)
        _emit_event("sound_inject", {
            "azimuth_deg": azimuth_deg,
            "elevation_deg": elevation_deg,
            "confidence": confidence,
        })


# --------------------------------------------------------------------------
# ALAutonomousLife / ALAutonomousMoves / ALBasicAwareness / ALSpeakingMovement
# --------------------------------------------------------------------------
class FakeALAutonomousLife(_BaseFakeService):
    SERVICE_NAME = "ALAutonomousLife"

    def __init__(self):
        super().__init__()
        self.state = "interactive"
        self.abilities: Dict[str, bool] = {}

    def setState(self, state, *args, **kwargs):
        self._record("setState", (state,) + tuple(args), kwargs)
        self.state = str(state)

    def getState(self, *args, **kwargs):
        self._record("getState", tuple(args), kwargs)
        return str(self.state)

    def setAutonomousAbilityEnabled(self, ability, enabled, *args, **kwargs):
        self._record("setAutonomousAbilityEnabled",
                     (ability, enabled) + tuple(args), kwargs)
        self.abilities[str(ability)] = bool(enabled)

    def getAutonomousAbilityEnabled(self, ability, *args, **kwargs):
        self._record("getAutonomousAbilityEnabled",
                     (ability,) + tuple(args), kwargs)
        return bool(self.abilities.get(str(ability), True))


class FakeALAutonomousMoves(_BaseFakeService):
    SERVICE_NAME = "ALAutonomousMoves"

    def setExpressiveListeningEnabled(self, enabled, *args, **kwargs):
        self._record("setExpressiveListeningEnabled",
                     (enabled,) + tuple(args), kwargs)

    def setBackgroundStrategy(self, strategy, *args, **kwargs):
        self._record("setBackgroundStrategy",
                     (strategy,) + tuple(args), kwargs)


class FakeALBasicAwareness(_BaseFakeService):
    SERVICE_NAME = "ALBasicAwareness"

    def stopAwareness(self, *args, **kwargs):
        self._record("stopAwareness", tuple(args), kwargs)

    def startAwareness(self, *args, **kwargs):
        self._record("startAwareness", tuple(args), kwargs)


class FakeALSpeakingMovement(_BaseFakeService):
    SERVICE_NAME = "ALSpeakingMovement"

    def setEnabled(self, enabled, *args, **kwargs):
        self._record("setEnabled", (enabled,) + tuple(args), kwargs)


# --------------------------------------------------------------------------
# ALBehaviorManager
# --------------------------------------------------------------------------
class FakeALBehaviorManager(_BaseFakeService):
    SERVICE_NAME = "ALBehaviorManager"

    def __init__(self):
        super().__init__()
        self.installed_behaviors: List[str] = []
        self.default_behaviors: List[str] = []
        self.running_behaviors: List[str] = []

    def getInstalledBehaviors(self, *args, **kwargs):
        self._record("getInstalledBehaviors", tuple(args), kwargs)
        return list(self.installed_behaviors)

    def getDefaultBehaviors(self, *args, **kwargs):
        self._record("getDefaultBehaviors", tuple(args), kwargs)
        return list(self.default_behaviors)

    def isBehaviorRunning(self, name, *args, **kwargs):
        self._record("isBehaviorRunning", (name,) + tuple(args), kwargs)
        return name in self.running_behaviors

    def startBehavior(self, name, *args, **kwargs):
        self._record("startBehavior", (name,) + tuple(args), kwargs)
        if name not in self.running_behaviors:
            self.running_behaviors.append(str(name))

    def stopBehavior(self, name, *args, **kwargs):
        self._record("stopBehavior", (name,) + tuple(args), kwargs)
        try:
            self.running_behaviors.remove(str(name))
        except ValueError:
            pass


# --------------------------------------------------------------------------
# ALPhotoCapture / ALTracker -- minimal stubs (used by camera/wake_listener)
# --------------------------------------------------------------------------
class FakeALPhotoCapture(_BaseFakeService):
    SERVICE_NAME = "ALPhotoCapture"

    def __init__(self):
        super().__init__()
        self.last_path: Optional[str] = None

    def setPictureFormat(self, fmt, *args, **kwargs):
        self._record("setPictureFormat", (fmt,) + tuple(args), kwargs)

    def setResolution(self, res, *args, **kwargs):
        self._record("setResolution", (res,) + tuple(args), kwargs)

    def setCameraID(self, cid, *args, **kwargs):
        self._record("setCameraID", (cid,) + tuple(args), kwargs)

    def takePicture(self, folder, name, *args, **kwargs):
        self._record("takePicture", (folder, name) + tuple(args), kwargs)
        path = "{}/{}.jpg".format(folder, name)
        self.last_path = path
        # Write a 1-byte file so the caller's open() doesn't crash.
        try:
            import os
            os.makedirs(folder, exist_ok=True)
            with open(path, "wb") as fh:
                # Minimal "JPEG" magic; not a real image but enough for
                # callers that just upload bytes to the server.
                fh.write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x00\xff\xd9")
        except Exception:
            pass
        return [folder, name + ".jpg"]


class FakeALTracker(_BaseFakeService):
    SERVICE_NAME = "ALTracker"

    def setEffector(self, name, *args, **kwargs):
        self._record("setEffector", (name,) + tuple(args), kwargs)

    def registerTarget(self, name, dimensions, *args, **kwargs):
        self._record("registerTarget",
                     (name, dimensions) + tuple(args), kwargs)

    def setMode(self, mode, *args, **kwargs):
        self._record("setMode", (mode,) + tuple(args), kwargs)

    def track(self, target, *args, **kwargs):
        self._record("track", (target,) + tuple(args), kwargs)

    def stopTracker(self, *args, **kwargs):
        self._record("stopTracker", tuple(args), kwargs)

    def unregisterAllTargets(self, *args, **kwargs):
        self._record("unregisterAllTargets", tuple(args), kwargs)


# --------------------------------------------------------------------------
# Async post namespace -- mirrors naoqi proxy.post.<method>(...)
# --------------------------------------------------------------------------
class _PostProxy(object):
    """Mimics the ``proxy.post.<method>(...)`` async call namespace.

    On real naoqi, ``proxy.post.foo(args)`` schedules the call and returns
    a task id. Our fake just calls the method synchronously and returns a
    fixed task id (1). Test code that polls for completion via
    ``getNumOfChannels()`` already gets the right answer because
    FakeALAudioPlayer flips the channel count back to 0 immediately.
    """

    def __init__(self, target):
        self._target = weakref.ref(target)

    def __getattr__(self, name):
        target = self._target()
        if target is None:
            return lambda *a, **kw: None
        method = getattr(target, name, None)
        if not callable(method):
            return lambda *a, **kw: None

        def _async_wrapper(*args, **kwargs):
            try:
                method(*args, **kwargs)
            except Exception:
                logger.exception(
                    "[fake_naoqi] post.%s raised", name,
                )
            return 1  # fake task id
        return _async_wrapper


# --------------------------------------------------------------------------
# FakeBroker -- placeholder for code that asks for one
# --------------------------------------------------------------------------
class FakeBroker(object):
    """Stub ALBroker so module-registration code that builds a broker
    doesn't crash.

    Most ``nao/*`` files don't construct a broker themselves -- naoqi
    starts one for them when launched on the robot. The few code paths
    that do (e.g., a stand-alone ALModule subscriber test) need at least
    these shims to import successfully.
    """

    def __init__(self,
                 name="FakeBroker",
                 ip="0.0.0.0",
                 port=0,
                 nao_ip="127.0.0.1",
                 nao_port=9559,
                 *args,
                 **kwargs):
        self.name = name
        self.ip = ip
        self.port = int(port)
        self.nao_ip = nao_ip
        self.nao_port = int(nao_port)

    def getServiceName(self):
        return self.name

    def shutdown(self):
        pass

    def getName(self):
        return self.name


# --------------------------------------------------------------------------
# ALProxy -- factory that hands out registered service instances
# --------------------------------------------------------------------------
class ALProxy(object):
    """Constructor-style: ``ALProxy(service_name, ip, port)`` returns an
    *instance* (we cheat by overriding ``__new__``) so callers can do::

        leds = ALProxy("ALLeds", "127.0.0.1", 9559)
        leds.fadeRGB("FaceLeds", 0xFF0000, 0.5)

    The returned object is the registered fake service (singleton per
    service name). If the service hasn't been registered, we build a
    default instance and stash it.
    """

    # Map service name -> (default factory, optional setup callable). Some
    # services (ALSpeechRecognition, ALFaceDetection, ALSoundLocalization)
    # need a reference to ALMemory, so we wire them in lazily.
    _DEFAULT_FACTORIES: Dict[str, Callable] = {
        "ALMemory": FakeALMemory,
        "ALTextToSpeech": FakeALTextToSpeech,
        "ALAnimatedSpeech": FakeALAnimatedSpeech,
        "ALAudioDevice": FakeALAudioDevice,
        "ALAudioPlayer": FakeALAudioPlayer,
        "ALAudioRecorder": FakeALAudioRecorder,
        "ALFaceDetection": FakeALFaceDetection,
        "ALMotion": FakeALMotion,
        "ALRobotPosture": FakeALRobotPosture,
        "ALLeds": FakeALLeds,
        "ALSpeechRecognition": FakeALSpeechRecognition,
        "ALSoundLocalization": FakeALSoundLocalization,
        "ALAutonomousLife": FakeALAutonomousLife,
        "ALAutonomousMoves": FakeALAutonomousMoves,
        "ALBasicAwareness": FakeALBasicAwareness,
        "ALSpeakingMovement": FakeALSpeakingMovement,
        "ALBehaviorManager": FakeALBehaviorManager,
        "ALPhotoCapture": FakeALPhotoCapture,
        "ALTracker": FakeALTracker,
    }

    def __new__(cls, service_name, ip="127.0.0.1", port=9559, *args, **kwargs):
        return _get_or_create_service(service_name)


def _get_or_create_service(service_name: str):
    """Return the registered fake for ``service_name`` (creating if absent)."""
    name = str(service_name)
    with _REGISTRY_LOCK:
        inst = _SERVICE_REGISTRY.get(name)
        if inst is not None:
            return inst
        factory = ALProxy._DEFAULT_FACTORIES.get(name)
        if factory is None:
            # Unknown service -- hand back a permissive stub so any method
            # call returns a no-op-ish value rather than AttributeError.
            inst = _PermissiveStub(name)
        else:
            inst = factory()
        # Wire memory references where applicable.
        memory = _SERVICE_REGISTRY.get("ALMemory")
        if memory is None and name == "ALMemory":
            # We're creating ALMemory itself; nothing to wire.
            memory = inst
        if memory is None:
            # Create one preemptively so other services can attach.
            memory = FakeALMemory()
            _SERVICE_REGISTRY["ALMemory"] = memory
        for attr in ("attach_memory",):
            method = getattr(inst, attr, None)
            if callable(method) and inst is not memory:
                try:
                    method(memory)
                except Exception:
                    pass
        _SERVICE_REGISTRY[name] = inst
        return inst


class _PermissiveStub(_BaseFakeService):
    """Catches calls to unrecognised services by returning sensible
    defaults from any method.

    ``calls`` is still populated so a test can assert on the method name
    even if we didn't pre-build a fake for that service.
    """

    def __init__(self, service_name: str):
        super().__init__()
        self.SERVICE_NAME = service_name

    def __getattr__(self, item):
        # Don't intercept dunders or private attrs.
        if item.startswith("_") or item == "calls":
            raise AttributeError(item)

        def _dynamic(*args, **kwargs):
            self._record(item, args, kwargs)
            return None
        return _dynamic


# --------------------------------------------------------------------------
# qi.Session / qi.Application shims
# --------------------------------------------------------------------------
class _QiSession(object):
    """Mimics ``qi.Session``. Used by face_naoqi.py et al.

    ``service(name)`` returns the same instance ALProxy would.
    """

    def __init__(self):
        self._connected = False

    def connect(self, url):
        self._connected = True
        _emit_event("qi_session_connect", {"url": url})

    def close(self):
        self._connected = False

    def isConnected(self):
        return self._connected

    def service(self, name):
        return _get_or_create_service(name)


class _QiApplication(object):
    """Mimics ``qi.Application``. ``session`` is a ``_QiSession``."""

    def __init__(self, *args, **kwargs):
        self.session = _QiSession()
        self.session.connect("tcp://127.0.0.1:9559")

    def start(self):
        return None

    def run(self):
        return None

    def stop(self):
        return None


# --------------------------------------------------------------------------
# Module installation / uninstallation
# --------------------------------------------------------------------------
def install_into_sys_modules(echo_sim=None,
                             leds_renderer=None,
                             on_event=None) -> None:
    """Install the fakes into ``sys.modules`` under ``naoqi`` and ``qi``.

    Parameters
    ----------
    echo_sim : EchoSimulator | None
        Registered as the global echo source. If ``None``, no echo is
        injected into mic frames.
    leds_renderer : LedsConsoleRenderer | None
        Registered to receive ``fadeRGB`` calls from FakeALLeds.
    on_event : callable(kind: str, data: dict) | None
        Optional global event sink. Every fake AL call invokes this with
        a structured payload so scenarios can assert / observe.

    Idempotent -- calling twice is safe; the second call updates the
    hooks but doesn't re-register modules. Call ``uninstall()`` to fully
    revert.
    """
    global _ECHO_SIM, _LEDS_RENDERER, _EVENT_CALLBACK
    _ECHO_SIM = echo_sim
    _LEDS_RENDERER = leds_renderer
    _EVENT_CALLBACK = on_event

    with _INSTALL_LOCK:
        if "naoqi" not in sys.modules or not getattr(
            sys.modules["naoqi"], "__fake_naoqi__", False
        ):
            naoqi_mod = _build_naoqi_module()
            qi_mod = _build_qi_module()

            for mod_name, mod in (("naoqi", naoqi_mod), ("qi", qi_mod)):
                _INSTALLED_MODULES.setdefault(
                    mod_name,
                    (mod_name in sys.modules,
                     sys.modules.get(mod_name)),
                )
                sys.modules[mod_name] = mod
        # Always (re)wire the hooks even when modules were installed.

    _emit_event("fake_naoqi_installed", {})


def uninstall() -> None:
    """Revert sys.modules. Idempotent."""
    global _ECHO_SIM, _LEDS_RENDERER, _EVENT_CALLBACK
    with _INSTALL_LOCK:
        for mod_name, (was_present, original) in list(_INSTALLED_MODULES.items()):
            if was_present and original is not None:
                sys.modules[mod_name] = original
            else:
                sys.modules.pop(mod_name, None)
        _INSTALLED_MODULES.clear()
    _ECHO_SIM = None
    _LEDS_RENDERER = None
    _EVENT_CALLBACK = None
    reset_state()


def _build_naoqi_module():
    """Construct a fake ``naoqi`` module exposing ALProxy + ALModule."""
    mod = types.ModuleType("naoqi")
    mod.__fake_naoqi__ = True
    mod.ALProxy = ALProxy
    mod.ALModule = ALModule
    mod.ALBroker = FakeBroker
    return mod


def _build_qi_module():
    """Construct a fake ``qi`` module with Session + Application."""
    mod = types.ModuleType("qi")
    mod.__fake_naoqi__ = True
    mod.Session = _QiSession
    mod.Application = _QiApplication
    return mod


# --------------------------------------------------------------------------
# Convenience getters for tests
# --------------------------------------------------------------------------
def get_service(name: str):
    """Return the registered fake instance for service ``name`` (or None)."""
    with _REGISTRY_LOCK:
        return _SERVICE_REGISTRY.get(str(name))


def list_services() -> List[str]:
    """List all currently registered service names."""
    with _REGISTRY_LOCK:
        return list(_SERVICE_REGISTRY.keys())


# --------------------------------------------------------------------------
# Self-test (runs as `python sim/fake_naoqi.py`)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import math
    import struct

    print("[fake_naoqi self-test] starting")
    # Build a renderer + echo sim and install with hooks.
    from leds_console import LedsConsoleRenderer  # local import in self-test
    from echo_sim import EchoSimulator

    events: List[Tuple[str, Dict[str, Any]]] = []

    def _on_event(kind: str, data: Dict[str, Any]) -> None:
        events.append((kind, data))

    leds_render = LedsConsoleRenderer(color=False)
    echo = EchoSimulator(delay_ms=80, gain=0.5, enabled=False)

    install_into_sys_modules(
        echo_sim=echo,
        leds_renderer=leds_render,
        on_event=_on_event,
    )

    import naoqi  # noqa: E402
    import qi     # noqa: E402

    assert sys.modules["naoqi"] is naoqi
    assert sys.modules["qi"] is qi
    assert "naoqi" in sys.modules and "qi" in sys.modules

    # 1. ALProxy + a few methods on ALMotion.
    motion = naoqi.ALProxy("ALMotion", "127.0.0.1", 9559)
    motion.setStiffnesses("Body", 1.0)
    motion.angleInterpolationWithSpeed(["HeadYaw"], [0.0], 0.3)
    motion.setBreathEnabled("Body", True)
    motion.stopMove()
    assert any(c[0] == "setStiffnesses" for c in motion.calls)
    assert motion.breath_enabled["Body"] is True

    # 2. ALProxy on ALMemory + inject test.
    memory = naoqi.ALProxy("ALMemory", "127.0.0.1", 9559)
    memory.insertData("foo", 42)
    assert memory.getData("foo") == 42

    # 3. ALModule subscriber pattern.
    received: List[Tuple[int, int, tuple, bytes]] = []

    class _MyMod(naoqi.ALModule):
        def __init__(self, name):
            naoqi.ALModule.__init__(self, name)

        def processRemote(self, channels, samples, ts_pair, raw):
            received.append((channels, samples, ts_pair, raw))

    mod = _MyMod("TestMod")

    # Synthetic PCM source: yields 20 ms of full-scale 1 kHz tone.
    sr = SAMPLE_RATE_HZ
    chunk_n = int(sr * 0.020)
    tone = struct.pack(
        "<%dh" % chunk_n,
        *[int(20000 * math.sin(2 * math.pi * 1000.0 * i / sr))
          for i in range(chunk_n)],
    )

    set_pcm_source(lambda: tone)

    audio = naoqi.ALProxy("ALAudioDevice", "127.0.0.1", 9559)
    audio.setClientPreferences("TestMod", sr, 3, 0)
    audio.subscribe("TestMod")

    # Wait up to 200 ms for a few callbacks.
    deadline = time.time() + 0.5
    while time.time() < deadline and len(received) < 3:
        time.sleep(0.01)

    audio.unsubscribe("TestMod")
    assert len(received) >= 1, "processRemote was never invoked"
    print("[fake_naoqi self-test] processRemote got",
          len(received), "callbacks")

    # 4. ALLeds.fadeRGB hits the renderer.
    leds = naoqi.ALProxy("ALLeds", "127.0.0.1", 9559)
    leds.fadeRGB("FaceLeds", 0.2, 0.5, 1.0, 0.4)
    assert leds_render.current_state["FaceLeds"]["rgb"] == (0.2, 0.5, 1.0)
    leds.fadeRGB("ChestLeds", 0x00FF00, 0.5)  # packed-int form
    cs = leds_render.current_state["ChestLeds"]["rgb"]
    assert abs(cs[0]) < 1e-6 and abs(cs[1] - 1.0) < 1e-6 and abs(cs[2]) < 1e-6

    # 5. EchoSimulator round-trip. We "play" 200 ms of speaker audio at
    # ts=0, then query the mic at ts=100 with delay_ms=80 -> the simulator
    # should mix in the slice of speaker history at [20 ms, 40 ms).
    echo.enabled = True
    speaker_n = int(sr * 0.200)  # 200 ms
    speaker = struct.pack(
        "<%dh" % speaker_n,
        *[int(15000 * math.sin(2 * math.pi * 1000.0 * i / sr))
          for i in range(speaker_n)],
    )
    echo.record_played(speaker, sample_rate=sr, now_ms=0.0)
    silent = b"\x00\x00" * chunk_n
    out = echo.apply_to_pcm(silent, ts_ms=100.0, sample_rate=sr)
    assert len(out) == len(silent)
    assert any(b != 0 for b in out), "echo not mixed back"

    # 6. ALModule registered, FaceDetection inject fires subscribers.
    face = naoqi.ALProxy("ALFaceDetection", "127.0.0.1", 9559)
    face.subscribe("MyFaceSub", 100, 0.0)

    seen_callbacks: List[Tuple[str, Any, str]] = []
    memory.subscribeToEvent("FaceDetected", "TestMod",
                            lambda ev, val, msg: seen_callbacks.append(
                                (ev, val, msg)
                            ))
    face.inject_face(face_id="aayush", name="Aayush", confidence=0.7,
                     distance_m=0.7, yaw_deg=0.0, pitch_deg=0.0)
    assert seen_callbacks, "FaceDetected subscribers did not fire"
    payload = memory.getData("FaceDetected")
    assert payload and isinstance(payload, list)

    # 7. qi.Session.service() returns the same instance.
    session = qi.Session()
    sm = session.service("ALMemory")
    assert sm is memory

    # 8. Sound localizer inject populates the right ALMemory key.
    sl = naoqi.ALProxy("ALSoundLocalization", "127.0.0.1", 9559)
    sl.inject_direction(azimuth_deg=15.0, confidence=0.8)
    sound_payload = memory.getData("ALSoundLocalization/SoundLocated")
    assert sound_payload, "SoundLocated payload missing"

    # 9. ASR inject populates WordRecognized.
    asr = naoqi.ALProxy("ALSpeechRecognition", "127.0.0.1", 9559)
    asr.inject_word("nao", 0.9)
    word_payload = memory.getData("WordRecognized")
    assert word_payload == ["nao", 0.9]

    # 10. on_event got called for various kinds.
    kinds = {k for k, _ in events}
    assert "audio_subscribe" in kinds
    assert "leds_fade" in kinds
    assert "fake_naoqi_installed" in kinds
    assert "almemory_inject" not in kinds  # we used insertData/inject_face

    # 11. uninstall reverses sys.modules.
    uninstall()
    # naoqi/qi may or may not still be present depending on interpreter
    # state; if we're the one who installed them, they should be removed.
    assert "naoqi" not in sys.modules or not getattr(
        sys.modules.get("naoqi"), "__fake_naoqi__", False,
    )

    print("[fake_naoqi self-test] OK -- all 11 cases passed")
