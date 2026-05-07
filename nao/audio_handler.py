# audio_handler.py
# -*- coding: utf-8 -*-
"""
Phase 2 role: VAD-ONLY watcher on the front-mic energy stream.

Under the Phase 1 architecture rework, audio CAPTURE is owned by
``nao/audio_module.py`` (``NaoAudioStreamer``), which subscribes to
``ALAudioDevice`` and streams 20 ms PCM16 chunks straight to the FastAPI
server over WebSocket. This module no longer captures, files, or processes
PCM bytes for transport.

What this module does (Phase 2):
    * Maintains a rolling 30-second window of front-mic energy values
      (one sample every 50 ms, deque maxlen=600).
    * Recomputes adaptive thresholds (start/keep/silent + ambient floor)
      once per second (not per-frame, to avoid jitter).
    * Runs a three-tier post-onset state machine
      (SPEECH / QUIET / SILENT) fed by those adaptive thresholds.
    * On end-of-utterance (energy below ``silent_th`` for ``trail_ms``),
      pushes a ``control`` frame to the WS via
      ``ws_client.push_control("end_of_utterance", payload)``.
    * Allows up to 60 s of continuous speech (NO 10 s hard cap on the
      utterance itself; only the silence trail ends a turn).

What this module does NOT do (Phase 2):
    * Open ``ALAudioRecorder`` or write WAVs.  (Phase 1 removed this.)
    * Send PCM frames anywhere.  (``audio_module.py`` does that.)
    * Modify ``ws_client``.  EoU frames go through its public
      ``push_control(subtype, data)`` method only.

Backward-compat helpers retained:
    * ``audioop`` compat shim, ``_pre_emphasis``, ``_agc_to_target_rms``,
      ``_trim_silence`` — wave-file post-processing utilities still used
      by the legacy Flask path (``conversation.py``).  These are wave-file
      processors and are NOT VAD logic.
    * ``record_audio(nao_ip, max_duration=None)`` — thin legacy shim that
      uses the new ``AdaptiveVad`` to drive a one-shot recorder for the
      legacy path.  Removed entirely once ``conversation.py`` is deleted.

Public API (the rest of the system uses):
    AdaptiveVad(ws_client=None, poll_interval_s=0.05, window_seconds=30,
                recompute_interval_s=1.0, trail_ms=300, max_utterance_s=60)
        .calibrate(audio_proxy, seconds=0.8) -> dict
        .run(audio_proxy, on_speech_start=None, on_speech_end=None) -> None
        .stop() -> None
        .thresholds() -> dict
    recompute_thresholds(window) -> dict   (module-level, pure)

Python 2.7 compatible.  ``from __future__ import print_function`` only.
No f-strings, no type hints, no walrus, no asyncio, no pathlib.
"""
from __future__ import print_function

import collections
import os
import struct
import sys
import threading
import time
import wave

# naoqi is only present on the robot.  On a developer Mac it's missing — we
# guard so this file imports cleanly during py_compile / AST checks and so
# the ``__main__`` self-test can run without naoqi.
try:
    from naoqi import ALProxy  # pragma: no cover - robot only
except ImportError:  # pragma: no cover - dev environment
    ALProxy = None

try:
    import audioop
except ImportError:
    # ``ModuleNotFoundError`` is py3.6+; ``ImportError`` catches both.
    class _AudioOpCompat(object):
        """Minimal audioop compat shim for builds where audioop is absent.

        Used by the wave-file post-processing helpers below
        (pre-emphasis, AGC, trim).  NOT used by the VAD path — VAD reads
        ``ALAudioDevice.getFrontMicEnergy()`` directly.
        """

        @staticmethod
        def _samples(fragment, width):
            if width != 2:
                raise ValueError("audioop fallback only supports 16-bit samples")
            count = len(fragment) // 2
            if count <= 0:
                return ()
            return struct.unpack("<{0}h".format(count), fragment[:count * 2])

        @classmethod
        def rms(cls, fragment, width):
            samples = cls._samples(fragment, width)
            if not samples:
                return 0
            return int((sum(s * s for s in samples) / float(len(samples))) ** 0.5)

        @classmethod
        def max(cls, fragment, width):
            samples = cls._samples(fragment, width)
            return max(abs(s) for s in samples) if samples else 0

        @classmethod
        def mul(cls, fragment, width, factor):
            out = []
            for sample in cls._samples(fragment, width):
                value = int(sample * factor)
                if value > 32767:
                    value = 32767
                if value < -32768:
                    value = -32768
                out.append(value)
            return struct.pack("<{0}h".format(len(out)), *out) if out else b""

    audioop = _AudioOpCompat()

# ── Audio format / paths (kept for backward-compat helpers) ──────────────────
SAVE_DIR = "/home/nao/recordings"
SAMPLE_RATE = 16000
CHANNELS_MASK = (0, 0, 1, 0)   # front mic mono
SAMPLE_WIDTH = 2               # S16_LE

# ── VAD timing knobs (Phase 2) ───────────────────────────────────────────────
POLL_INTERVAL_S = 0.05         # 50 ms between energy samples
WINDOW_SECONDS = 30            # rolling ambient window length
RECOMPUTE_INTERVAL_S = 1.0     # recompute thresholds once a second
TRAIL_MS = 300                 # ms below silent_th required to call EoU
MAX_UTTERANCE_S = 60.0         # ceiling on a single utterance (was 10 s)

# Adaptive threshold floor constants (per Phase 2 task map):
#   ambient_floor = percentile(window, 25)
#   start_th  = max(ambient_floor + 380, 700)
#   keep_th   = max(ambient_floor + 250, 420)
#   silent_th = max(ambient_floor +  30, 260)
START_OFFSET = 380
START_FLOOR = 700
KEEP_OFFSET = 250
KEEP_FLOOR = 420
SILENT_OFFSET = 30
SILENT_FLOOR = 260
AMBIENT_PERCENTILE = 25        # robust to occasional speech inside the window

# Post-FX defaults (used by record_audio legacy shim).
PREEMPH_ENABLED = True
PREEMPH_COEFF = 0.97
AGC_ENABLED = True
AGC_TARGET_RMS = 4500
AGC_MAX_GAIN = 6.0
TRIM_FRACTION = 0.25
TRIM_CHUNK_BYTES = 1024


# ── Generic helpers (preserved from the prior file) ──────────────────────────
def _ensure_dir(p):
    if not os.path.exists(p):
        try:
            os.makedirs(p)
        except Exception:
            pass


def _ts_path():
    return os.path.join(SAVE_DIR, "nao_rec_{0}.wav".format(
        time.strftime("%Y%m%d_%H%M%S")))


def _fade_leds(ip, r, g, b, t=0.08):
    if ALProxy is None:
        return
    try:
        ALProxy("ALLeds", ip, 9559).fadeRGB(
            "FaceLeds", float(r), float(g), float(b), float(t))
    except Exception:
        pass


def _robot_noise_quiet(ip):
    if ALProxy is None:
        return None
    almoves = None
    try:
        almoves = ALProxy("ALAutonomousMoves", ip, 9559)
        try:
            almoves.setExpressiveListeningEnabled(False)
        except Exception:
            pass
        try:
            almoves.setBackgroundStrategy("none")
        except Exception:
            pass
    except Exception:
        pass
    return almoves


def _robot_noise_restore(almoves):
    if almoves is None:
        return
    try:
        almoves.setExpressiveListeningEnabled(True)
    except Exception:
        pass
    try:
        almoves.setBackgroundStrategy("backToNeutral")
    except Exception:
        pass


def _delete_if_exists(path):
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except Exception:
            pass


# ── Adaptive thresholds — pure function, no state ────────────────────────────
def _percentile(sorted_vals, pct):
    """Linear-interpolation percentile of an ALREADY-SORTED list.

    Module-private; ``recompute_thresholds`` calls it on a sorted copy of
    the rolling window.  Returns 0.0 for empty input.
    """
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    if pct <= 0:
        return float(sorted_vals[0])
    if pct >= 100:
        return float(sorted_vals[-1])
    # NumPy "linear" method: rank = (n - 1) * pct / 100
    rank = (n - 1) * (pct / 100.0)
    lo = int(rank)
    hi = lo + 1 if lo + 1 < n else lo
    frac = rank - lo
    return float(sorted_vals[lo]) + (float(sorted_vals[hi]) - float(sorted_vals[lo])) * frac


def recompute_thresholds(window):
    """Compute adaptive VAD thresholds from a rolling-energy window.

    Parameters
    ----------
    window : iterable of float
        Recent front-mic energy samples (any iterable; typically a
        ``collections.deque`` of length up to ``WINDOW_SECONDS / POLL_INTERVAL_S``).

    Returns
    -------
    dict
        Keys: ``"start_th"``, ``"keep_th"``, ``"silent_th"``,
        ``"ambient_floor"``.  All floats.

    Formulas (per ``docs/PHASE_2_TASK_MAP.md``):
        ambient_floor = percentile(window, 25)
        start_th  = max(ambient_floor + 380, 700)
        keep_th   = max(ambient_floor + 250, 420)
        silent_th = max(ambient_floor +  30, 260)

    The 25th percentile is robust to occasional speech samples that leak
    into the window; the lower quartile reliably reflects the room floor.
    """
    vals = sorted(float(v) for v in window if v is not None)
    if not vals:
        ambient = 0.0
    else:
        ambient = _percentile(vals, AMBIENT_PERCENTILE)
    start_th = float(max(ambient + START_OFFSET, START_FLOOR))
    keep_th = float(max(ambient + KEEP_OFFSET, KEEP_FLOOR))
    silent_th = float(max(ambient + SILENT_OFFSET, SILENT_FLOOR))
    # Defensive ordering: in pathological cases (e.g. SILENT_FLOOR raised
    # above KEEP_FLOOR by a future tweak) silent_th could meet/exceed
    # keep_th and erase the QUIET band.  Keep them ordered so the state
    # machine stays well-defined.
    if silent_th >= keep_th:
        silent_th = keep_th * 0.85
    if keep_th >= start_th:
        keep_th = start_th * 0.85
    return {
        "start_th": start_th,
        "keep_th": keep_th,
        "silent_th": silent_th,
        "ambient_floor": float(ambient),
    }


# ── State machine constants ──────────────────────────────────────────────────
STATE_IDLE = "idle"            # waiting for onset
STATE_SPEECH = "speech"        # confident voice, e >= keep_th
STATE_QUIET = "quiet"          # soft sound, silent_th <= e < keep_th
STATE_SILENT = "silent"        # below silent_th — trail accumulates


def _read_energy(audio_proxy):
    """Pull one front-mic energy sample.  Tolerant of proxy errors."""
    if audio_proxy is None:
        return 0.0
    try:
        return float(audio_proxy.getFrontMicEnergy())
    except Exception:
        return 0.0


# ── AdaptiveVad — public class ───────────────────────────────────────────────
class AdaptiveVad(object):
    """Adaptive ambient-floor VAD that emits end-of-utterance hints.

    The Phase 1 ``ws_client.NaoWsClient`` is passed in via ``ws_client``.
    On EoU detection (energy < ``silent_th`` for ``trail_ms``), this class
    calls ``ws_client.push_control("end_of_utterance", payload)`` where
    payload follows the Phase 1 frame envelope:

        {"robot_eou_hint": True,
         "energy_floor": int(ambient_floor),
         "trail_ms": int(trail_ms),
         "duration_ms": int((t_end - t_start) * 1000)}

    If ``ws_client`` is None the EoU hint is logged only (handy for
    smoke tests / dev runs without a connected server).

    Threading
    ---------
    ``run()`` is BLOCKING.  Callers spin a dedicated thread:

        vad = AdaptiveVad(ws_client=ws)
        threading.Thread(target=vad.run, args=(audio_proxy,)).start()
        ...
        vad.stop()        # idempotent

    Why blocking?  The VAD owns the polling cadence (50 ms).  Folding it
    into another thread's loop introduces jitter; a dedicated thread is
    cheap and keeps the math centralized.
    """

    # Default kwargs — kept here for documentation.  Don't override these
    # in __init__'s body; the caller's positional defaults win.
    def __init__(self, ws_client=None,
                 poll_interval_s=POLL_INTERVAL_S,
                 window_seconds=WINDOW_SECONDS,
                 recompute_interval_s=RECOMPUTE_INTERVAL_S,
                 trail_ms=TRAIL_MS,
                 max_utterance_s=MAX_UTTERANCE_S):
        self.ws_client = ws_client
        self.poll_interval_s = float(poll_interval_s)
        self.window_seconds = int(window_seconds)
        self.recompute_interval_s = float(recompute_interval_s)
        self.trail_ms = int(trail_ms)
        self.max_utterance_s = float(max_utterance_s)

        # Rolling window — deque sized so the worst-case memory footprint
        # is bounded.  600 samples * 8 bytes/float = ~5 KB.
        max_samples = max(1, int(self.window_seconds / self.poll_interval_s))
        self._window = collections.deque(maxlen=max_samples)

        self.shutdown_event = threading.Event()

        # Latest threshold snapshot — start with conservative defaults
        # so callers asking ``thresholds()`` before ``run()`` get sensible
        # values (e.g. unit tests, logging).
        self._th_lock = threading.Lock()
        self._thresholds = {
            "start_th": float(START_FLOOR),
            "keep_th": float(KEEP_FLOOR),
            "silent_th": float(SILENT_FLOOR),
            "ambient_floor": 0.0,
        }

    # ------------------------------------------------------------------
    # Public introspection
    # ------------------------------------------------------------------
    def thresholds(self):
        """Return a snapshot dict of the current adaptive thresholds.

        Safe to call from any thread.  Returns a fresh dict so the caller
        can't accidentally mutate internal state.
        """
        with self._th_lock:
            return dict(self._thresholds)

    def stop(self):
        """Signal ``run()`` to exit at the next poll boundary.  Idempotent."""
        self.shutdown_event.set()

    # ------------------------------------------------------------------
    # Calibration — kept for backward-compat with main.py's boot path
    # ------------------------------------------------------------------
    def calibrate(self, audio_proxy, seconds=0.8):
        """Seed the rolling window with an initial floor estimate.

        Pre-Phase 2 the calibration produced once-per-session thresholds.
        Now it just primes the deque so ``run()``'s first second of
        operation already has a reasonable ambient floor; the rolling
        window does the heavy lifting from there on.

        Returns the computed threshold dict for logging.
        """
        n_samples = max(1, int(float(seconds) / self.poll_interval_s))
        for _ in range(n_samples):
            if self.shutdown_event.is_set():
                break
            e = _read_energy(audio_proxy)
            self._window.append(e)
            time.sleep(self.poll_interval_s)
        thresholds = recompute_thresholds(self._window)
        with self._th_lock:
            self._thresholds = thresholds
        return thresholds

    # ------------------------------------------------------------------
    # Internal: emit the EoU hint to the server (or log if no client)
    # ------------------------------------------------------------------
    def _emit_eou(self, t_start, t_end):
        ths = self.thresholds()
        payload = {
            "robot_eou_hint": True,
            "energy_floor": int(ths.get("ambient_floor", 0.0)),
            "trail_ms": int(self.trail_ms),
            "duration_ms": int((t_end - t_start) * 1000),
        }
        if self.ws_client is not None:
            try:
                self.ws_client.push_control("end_of_utterance", payload)
            except Exception as exc:
                # Never let a comms error kill the VAD loop — the next
                # turn's EoU may still succeed once the WS reconnects.
                print("[VAD] push_control failed: {0}".format(exc))
        else:
            print("[VAD] eou (no ws_client) {0}".format(payload))
        return payload

    # ------------------------------------------------------------------
    # Internal: classify an energy sample into one of three states
    # ------------------------------------------------------------------
    def _classify(self, e, thresholds):
        if e >= thresholds["keep_th"]:
            return STATE_SPEECH
        if e >= thresholds["silent_th"]:
            return STATE_QUIET
        return STATE_SILENT

    # ------------------------------------------------------------------
    # Public: blocking run loop
    # ------------------------------------------------------------------
    def run(self, audio_proxy, on_speech_start=None, on_speech_end=None):
        """Poll mic energy on a fixed cadence and emit EoU hints.

        Parameters
        ----------
        audio_proxy : ALProxy("ALAudioDevice", ...)
            Provides ``.getFrontMicEnergy()``.  May be a stub during
            testing — anything with that method works.
        on_speech_start : callable() or None
            Optional callback fired at speech onset (transition
            IDLE -> SPEECH).  Synchronous; keep it short.
        on_speech_end : callable(payload_dict) or None
            Optional callback fired right after EoU emit.  Receives the
            same dict that was pushed to the server.

        Exits when ``shutdown_event.is_set()`` (set by ``stop()``).
        """
        last_recompute_t = 0.0
        utterance_active = False
        utterance_start_t = 0.0
        last_speech_t = 0.0           # last sample classified SPEECH
        silence_streak_t0 = None      # start of current contiguous SILENT run

        # Prime the window & thresholds — main.py may already have called
        # calibrate(); calling it twice is harmless (just refreshes).
        if not self._window:
            self.calibrate(audio_proxy, seconds=0.4)

        while not self.shutdown_event.is_set():
            now = time.time()

            # 1) Sample mic energy and append to rolling window.
            e = _read_energy(audio_proxy)
            self._window.append(e)

            # 2) Recompute thresholds once per recompute_interval_s.
            if (now - last_recompute_t) >= self.recompute_interval_s:
                ths = recompute_thresholds(self._window)
                with self._th_lock:
                    self._thresholds = ths
                last_recompute_t = now
            else:
                ths = self.thresholds()

            # 3) State classification.
            state = self._classify(e, ths)

            if not utterance_active:
                # -- Pre-onset: wait for SPEECH-band energy to start --
                if state == STATE_SPEECH or e >= ths["start_th"]:
                    utterance_active = True
                    utterance_start_t = now
                    last_speech_t = now
                    silence_streak_t0 = None
                    if callable(on_speech_start):
                        try:
                            on_speech_start()
                        except Exception as exc:
                            print("[VAD] on_speech_start error: {0}".format(exc))
            else:
                # -- Post-onset: SPEECH / QUIET / SILENT machine --
                if state == STATE_SPEECH:
                    last_speech_t = now
                    silence_streak_t0 = None
                elif state == STATE_QUIET:
                    # Consonants, breath, soft syllables — do NOT advance
                    # the trail.  This is what eliminates mid-sentence
                    # cutoffs on fricatives in the Phase 1 architecture.
                    silence_streak_t0 = None
                else:
                    # STATE_SILENT — trail accumulates.
                    if silence_streak_t0 is None:
                        silence_streak_t0 = now
                    if (now - silence_streak_t0) * 1000.0 >= self.trail_ms:
                        # Trail elapsed: end the utterance and emit EoU.
                        payload = self._emit_eou(utterance_start_t, now)
                        if callable(on_speech_end):
                            try:
                                on_speech_end(payload)
                            except Exception as exc:
                                print("[VAD] on_speech_end error: {0}".format(exc))
                        utterance_active = False
                        utterance_start_t = 0.0
                        last_speech_t = 0.0
                        silence_streak_t0 = None

                # 60 s hard ceiling on the utterance itself — only kicks
                # in if someone literally never stops talking.  We still
                # flush an EoU at the cap so downstream STT can run.
                if utterance_active and (now - utterance_start_t) >= self.max_utterance_s:
                    payload = self._emit_eou(utterance_start_t, now)
                    if callable(on_speech_end):
                        try:
                            on_speech_end(payload)
                        except Exception as exc:
                            print("[VAD] on_speech_end error: {0}".format(exc))
                    utterance_active = False
                    utterance_start_t = 0.0
                    last_speech_t = 0.0
                    silence_streak_t0 = None

            # 4) Sleep until next poll boundary (drift-tolerant).
            elapsed = time.time() - now
            slack = self.poll_interval_s - elapsed
            if slack > 0:
                time.sleep(slack)


# ── Wave-file post-processing helpers (UNCHANGED — non-VAD) ──────────────────
# These are used by the legacy ``record_audio`` path to massage WAV files
# before they're posted to the Flask /turn endpoint.  Phase 2 keeps them
# verbatim because the legacy path still ships during the Phase 1->Phase 9
# transition.
def _trim_silence(wav_path, rms_th, chunk_bytes):
    try:
        wf = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw = wf.readframes(nframes)
        wf.close()
        if nchan != 1 or width != SAMPLE_WIDTH or not raw:
            return None
        step = max(SAMPLE_WIDTH, int(chunk_bytes / SAMPLE_WIDTH) * SAMPLE_WIDTH)
        start = 0
        end = len(raw)
        while start + step <= end and audioop.rms(raw[start:start + step], width) <= rms_th:
            start += step
        while end - step >= start and audioop.rms(raw[end - step:end], width) <= rms_th:
            end -= step
        if end <= start:
            return None
        out = raw[start:end]
        out_path = wav_path.replace(".wav", "_trim.wav")
        wf2 = wave.open(out_path, "wb")
        wf2.setparams((1, width, rate, len(out) // (width * 1), comp, name))
        wf2.writeframes(out)
        wf2.close()
        return out_path
    except Exception as e:
        print("trim_silence error:", e)
        return None


def _pre_emphasis(wav_path, a):
    try:
        wf = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw = wf.readframes(nframes)
        wf.close()
        if nchan != 1 or width != SAMPLE_WIDTH or not raw or nframes <= 0:
            return None
        samples = struct.unpack("<{0}h".format(nframes), raw)
        out = []
        prev = 0
        for x in samples:
            y = int(x - a * prev)
            if y > 32767:
                y = 32767
            if y < -32768:
                y = -32768
            out.append(y)
            prev = x
        out_bytes = struct.pack("<{0}h".format(len(out)), *out)
        out_path = wav_path.replace(".wav", "_pre.wav")
        wf2 = wave.open(out_path, "wb")
        wf2.setparams((1, width, rate, len(out), comp, name))
        wf2.writeframes(out_bytes)
        wf2.close()
        return out_path
    except Exception:
        return None


def _agc_to_target_rms(wav_path, target_rms, max_gain):
    try:
        wf = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw = wf.readframes(nframes)
        wf.close()
        if nchan != 1 or width != SAMPLE_WIDTH or not raw:
            return None
        current = audioop.rms(raw, width)
        if current <= 0:
            return None
        gain = min(max_gain, float(target_rms) / float(current))
        out = audioop.mul(raw, width, gain)
        peak = audioop.max(out, width)
        if peak > 32767:
            out = audioop.mul(out, width, 32767.0 / peak)
        out_path = wav_path.replace(".wav", "_agc.wav")
        wf2 = wave.open(out_path, "wb")
        wf2.setparams((1, width, rate, len(out) // (width * 1), comp, name))
        wf2.writeframes(out)
        wf2.close()
        return out_path
    except Exception as e:
        print("agc error:", e)
        return None


# ── Legacy record_audio shim ─────────────────────────────────────────────────
def record_audio(nao_ip, max_duration=None):
    """Legacy one-shot WAV capture used by the Flask path (``conversation.py``).

    Phase 2 replaces the bespoke energy-VAD body with an ``AdaptiveVad``
    instance.  When the new WS client / ``audio_module`` path is the only
    transport (i.e. ``USE_WS=1`` everywhere), this function and the entire
    legacy ``conversation.py`` are deleted.

    Returns the post-processed WAV path or None on no-speech / error.
    Returns None when naoqi (``ALProxy``) isn't available — the legacy
    path is robot-only and shouldn't run in dev tests.
    """
    if ALProxy is None:
        return None

    _ensure_dir(SAVE_DIR)
    out_path = _ts_path()

    rec = ALProxy("ALAudioRecorder", nao_ip, 9559)
    almoves = _robot_noise_quiet(nao_ip)
    _fade_leds(nao_ip, 0.0, 1.0, 0.0)  # listening cue

    try:
        rec.stopMicrophonesRecording()
    except Exception:
        pass
    time.sleep(0.15)

    rec.startMicrophonesRecording(out_path, "wav", SAMPLE_RATE, CHANNELS_MASK)

    try:
        audio_dev = ALProxy("ALAudioDevice", nao_ip, 9559)
    except Exception:
        audio_dev = None

    vad = AdaptiveVad(ws_client=None,
                      max_utterance_s=float(max_duration) if max_duration else MAX_UTTERANCE_S)
    vad.calibrate(audio_dev, seconds=0.4)

    # Run vad.run() in this thread, but stop it on first EoU so we
    # produce a single utterance (legacy turn semantics).
    eou_event = threading.Event()

    def _end_cb(payload):
        eou_event.set()

    runner = threading.Thread(
        target=vad.run, args=(audio_dev,),
        kwargs={"on_speech_end": _end_cb},
        name="legacy-record_audio-vad")
    runner.daemon = True
    runner.start()

    eou_event.wait(timeout=float(max_duration) if max_duration else MAX_UTTERANCE_S + 5.0)
    vad.stop()
    runner.join(timeout=1.0)

    try:
        rec.stopMicrophonesRecording()
    except Exception:
        pass
    _fade_leds(nao_ip, 1.0, 1.0, 1.0)
    _robot_noise_restore(almoves)

    if not eou_event.is_set():
        # No speech detected within the budget; clean up.
        _delete_if_exists(out_path)
        return None

    if PREEMPH_ENABLED:
        pre = _pre_emphasis(out_path, PREEMPH_COEFF) or out_path
    else:
        pre = out_path
    if AGC_ENABLED:
        agc = _agc_to_target_rms(pre, AGC_TARGET_RMS, AGC_MAX_GAIN) or pre
    else:
        agc = pre
    return agc


# ── Self-test: synthesize 5 s of fake energy, log transitions ────────────────
class _FakeAudioProxy(object):
    """Replays a precomputed energy stream at ``getFrontMicEnergy()``.

    Used only by ``__main__`` so the file can be smoke-tested without
    naoqi installed.  The streamed values exercise: (1) the rolling
    ambient window, (2) the speech-onset detection path, (3) the
    silence-trail EoU path.  Returned values are floats matching the
    real proxy's behaviour.
    """

    def __init__(self, samples):
        self._samples = list(samples)
        self._idx = 0

    def getFrontMicEnergy(self):
        if not self._samples:
            return 0.0
        if self._idx >= len(self._samples):
            return self._samples[-1]
        v = self._samples[self._idx]
        self._idx += 1
        return float(v)


def _build_fake_stream(total_seconds, poll_s, ambient_seconds=2.0):
    """Return a list of energy values that simulates an utterance.

    Layout (when ``total_seconds=5.0`` and ``ambient_seconds=2.0``):
        0.0 - 2.0 s : ambient silence  (energy ~150 + jitter)
        2.0 - 3.8 s : speech            (~1500 with QUIET-band dips at 380)
        3.8 - 5.0 s : ambient silence   (back to ~150)

    Exercises: rolling ambient floor, speech onset, QUIET-band hold,
    SILENT-trail EoU emit.  ``ambient_seconds`` controls how much head-
    room the floor has to settle before speech starts.
    """
    n_total = int(total_seconds / poll_s)
    speech_start_t = float(ambient_seconds)
    speech_end_t = speech_start_t + 1.8
    out = []
    for i in range(n_total):
        t = i * poll_s
        if t < speech_start_t or t >= speech_end_t:
            v = 150.0 + (i % 5) * 4.0
        else:
            # speech segment with occasional QUIET dips (consonants)
            v = 1500.0 if (i % 8) != 7 else 380.0
        out.append(v)
    return out


class _CapturingWsClient(object):
    """Minimal stand-in for NaoWsClient that records push_control calls."""

    def __init__(self):
        self.calls = []

    def push_control(self, subtype, data=None):
        self.calls.append((subtype, dict(data or {})))
        print("[FAKE_WS] push_control({0}) {1}".format(subtype, data))


def _self_test():
    print("=== AdaptiveVad self-test (no naoqi) ===")
    poll_s = 0.05
    duration = 5.0
    # 2 s of leading silence gives the rolling window enough headroom
    # that, by the time the trail fires near t=4.1 s, the 25th-percentile
    # ambient floor reflects the silence segments rather than the loud
    # speech segment.  In production the window is 30 s, so this isn't a
    # concern; the test just makes the EoU payload realistic.
    samples = _build_fake_stream(duration, poll_s, ambient_seconds=2.0)

    audio = _FakeAudioProxy(samples)
    ws = _CapturingWsClient()
    vad = AdaptiveVad(
        ws_client=ws,
        poll_interval_s=poll_s,
        window_seconds=4,        # smaller window so the test populates it fast
        recompute_interval_s=0.2,
        trail_ms=300,
        max_utterance_s=10,
    )

    starts = []
    ends = []

    def _start():
        starts.append(time.time())
        print("[VAD-self-test] speech START")

    def _end(payload):
        ends.append((time.time(), payload))
        print("[VAD-self-test] speech END payload={0}".format(payload))

    # Run in a thread so we can stop it once the fake stream exhausts.
    t = threading.Thread(target=vad.run, args=(audio,),
                         kwargs={"on_speech_start": _start, "on_speech_end": _end})
    t.daemon = True
    t.start()

    # Real-time-ish wait equivalent to the synthesized stream length.
    deadline = time.time() + duration + 1.0
    while time.time() < deadline:
        if audio._idx >= len(samples):
            # Give the trail one more cycle to fire then exit.
            time.sleep(0.5)
            break
        time.sleep(0.05)
    vad.stop()
    t.join(timeout=2.0)

    print("--- summary ---")
    print("thresholds at exit: {0}".format(vad.thresholds()))
    print("speech_starts: {0}".format(len(starts)))
    print("speech_ends:   {0}".format(len(ends)))
    print("ws push_control calls: {0}".format(len(ws.calls)))
    for sub, data in ws.calls:
        print("  -> {0}: {1}".format(sub, data))
    print("=== self-test done ===")
    # Crude pass criterion for a human reader: at least one EoU emission.
    return 0 if ws.calls else 1


if __name__ == "__main__":
    sys.exit(_self_test())
