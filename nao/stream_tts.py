# -*- coding: utf-8 -*-
"""Consume an SSE stream of sentences/actions and speak/execute in order."""
from __future__ import print_function

import json
import sys
import threading
import time
import requests


try:
    unicode_type = unicode  # noqa: F821  (Py2.7 on NAO)
except NameError:
    unicode_type = str

_PY2 = sys.version_info[0] == 2


class BargeMonitor(object):
    """Watch for an interrupt signal while NAO is speaking.

    Two trigger paths:
      1. Head touch (reliable on this hardware) — any of the three head
         tactile sensors going high is an immediate stop.
      2. Front-mic energy (best-effort; useless without AEC, but kept as an
         opt-in fallback).
    """

    def __init__(self, audio_device, tts, threshold=3500.0, sustain_ms=350,
                 deadzone_ms=700, poll_ms=30, memory=None,
                 acoustic_enabled=True, touch_enabled=True):
        self.audio_device = audio_device
        self.tts = tts
        self.memory = memory
        self.acoustic_enabled = bool(acoustic_enabled)
        self.touch_enabled = bool(touch_enabled) and memory is not None
        self.threshold = float(threshold)
        self.sustain_ms = int(sustain_ms)
        self.deadzone_ms = int(deadzone_ms)
        self.poll_s = max(0.01, float(poll_ms) / 1000.0)
        self.interrupted = False
        self.interrupt_reason = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self.interrupted = False
        self.interrupt_reason = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)

    def _head_touched(self):
        """True if any of the three head tactile sensors is pressed."""
        if not self.touch_enabled or self.memory is None:
            return False
        for key in ("FrontTactilTouched", "MiddleTactilTouched", "RearTactilTouched"):
            try:
                v = self.memory.getData(key)
                if isinstance(v, (int, float)) and float(v) > 0.5:
                    return True
            except Exception:
                pass
        return False

    def _fire(self, reason):
        self.interrupted = True
        self.interrupt_reason = reason
        try:
            self.tts.stopAll()
        except Exception:
            pass

    def _run(self):
        started = time.time()
        high_since = None
        while not self._stop.is_set():
            # Touch is checked from t=0 — no deadzone, since it's a deliberate
            # human action and can't be confused with NAO's speaker output.
            if self._head_touched():
                self._fire("head_touch")
                return

            if self.acoustic_enabled:
                elapsed_ms = int((time.time() - started) * 1000)
                if elapsed_ms < self.deadzone_ms:
                    time.sleep(self.poll_s)
                    continue
                try:
                    energy = float(self.audio_device.getFrontMicEnergy())
                except Exception:
                    energy = 0.0
                if energy >= self.threshold:
                    if high_since is None:
                        high_since = time.time()
                    if int((time.time() - high_since) * 1000) >= self.sustain_ms:
                        self._fire("voice")
                        return
                else:
                    high_since = None
            time.sleep(self.poll_s)


def consume(sse_url, files, data, tts, on_action, on_done, timeout=120,
            audio_device=None, barge_config=None, memory=None):
    """POST to sse_url, stream SSE events, speak sentences, execute actions.

    tts: ALTextToSpeech proxy.
    on_action(action_dict): called per action event.
    on_done(info_dict): called once with final info.
    Returns the final info dict (also passed to on_done).
    """
    headers = {"Accept": "text/event-stream"}
    resp = requests.post(sse_url, files=files, data=data, headers=headers,
                         stream=True, timeout=timeout)
    if resp.status_code != 200:
        return {"error": "http_{0}".format(resp.status_code)}

    final = {}
    barge_config = barge_config or {}
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            ev = json.loads(raw[6:])
        except Exception:
            continue
        etype = ev.get("type")
        if etype == "sentence":
            monitor = None
            try:
                print("[stream_tts] sentence:", ev.get("text", ""))
                touch_on = barge_config.get("touch_enabled", True) and memory is not None
                acoustic_on = audio_device is not None and barge_config.get("enabled", True)
                if touch_on or acoustic_on:
                    monitor = BargeMonitor(
                        audio_device, tts,
                        threshold=barge_config.get("threshold", 3500),
                        sustain_ms=barge_config.get("sustain_ms", 350),
                        deadzone_ms=barge_config.get("deadzone_ms", 700),
                        poll_ms=barge_config.get("poll_ms", 30),
                        memory=memory,
                        acoustic_enabled=acoustic_on,
                        touch_enabled=touch_on,
                    )
                    monitor.start()
                tts.say(_sayable(ev.get("text", "")))
            except Exception as e:
                print("[stream_tts] say error:", e)
            finally:
                if monitor is not None:
                    monitor.stop()
                    if monitor.interrupted:
                        final = {"type": "done", "active_agent": "barge",
                                 "crisis": False, "suppress_image": False,
                                 "user_input": "", "barge_in": True}
                        try:
                            resp.close()
                        except Exception:
                            pass
                        break
        elif etype == "audio":
            # Voice-cloned MP3 from ElevenLabs. Same barge semantics as a
            # sentence event — head-touch interrupt + tail window.
            monitor = None
            try:
                print("[stream_tts] audio:", ev.get("text", "")[:60])
                touch_on = barge_config.get("touch_enabled", True) and memory is not None
                acoustic_on = audio_device is not None and barge_config.get("enabled", True)
                if touch_on or acoustic_on:
                    monitor = BargeMonitor(
                        audio_device, tts,
                        threshold=barge_config.get("threshold", 3500),
                        sustain_ms=barge_config.get("sustain_ms", 350),
                        deadzone_ms=barge_config.get("deadzone_ms", 700),
                        poll_ms=barge_config.get("poll_ms", 30),
                        memory=memory,
                        acoustic_enabled=acoustic_on,
                        touch_enabled=touch_on,
                    )
                    monitor.start()
                _play_mp3_b64(ev.get("b64", ""))
            except Exception as e:
                print("[stream_tts] mp3 play error:", e)
            finally:
                if monitor is not None:
                    monitor.stop()
                    if monitor.interrupted:
                        final = {"type": "done", "active_agent": "barge",
                                 "crisis": False, "suppress_image": False,
                                 "user_input": "", "barge_in": True}
                        try: resp.close()
                        except Exception: pass
                        break
        elif etype == "action":
            try:
                on_action(ev.get("action") or {})
            except Exception as e:
                print("[stream_tts] action error:", e)
        elif etype == "wait":
            # Server's semantic endpoint thinks the user trailed off. Surface
            # the partial transcript for logs; the next "done" event with
            # active_agent="wait" lets conversation.py loop back to listen
            # without speaking, clearing the hint, or treating this as a turn.
            print("[stream_tts] wait (incomplete thought):", ev.get("user_input", ""))
        elif etype == "done":
            final = ev
            break
        elif etype == "recognized":
            final["username"] = ev.get("username")
    on_done(final)
    return final


# MP3 playback for ElevenLabs voice clone
_MP3_DIR = "/tmp/nao_voice"
_mp3_counter = [0]
_player_proxy = [None]


def _play_mp3_b64(b64):
    """Decode base64 MP3 and play through NAO's ALAudioPlayer (blocking)."""
    if not b64:
        return
    try:
        import os, base64
        from naoqi import ALProxy
        if _player_proxy[0] is None:
            import sys
            sys.path.insert(0, "/home/nao/nao_assist")
            import config as _cfg
            _player_proxy[0] = ALProxy("ALAudioPlayer", _cfg.NAO_IP, _cfg.NAO_PORT)
        if not os.path.exists(_MP3_DIR):
            os.makedirs(_MP3_DIR)
        _mp3_counter[0] = (_mp3_counter[0] + 1) % 1000
        path = os.path.join(_MP3_DIR, "tts_{0}.mp3".format(_mp3_counter[0]))
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        _player_proxy[0].playFile(path)
    except Exception as e:
        print("[stream_tts] _play_mp3_b64 error:", e)


def _sayable(text):
    if text is None:
        return ""
    if _PY2 and isinstance(text, unicode_type):
        return text.encode("utf-8", "ignore")
    return text if isinstance(text, str) else str(text)
