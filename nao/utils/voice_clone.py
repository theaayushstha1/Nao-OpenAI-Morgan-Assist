# -*- coding: utf-8 -*-
"""OpenAI TTS say() for NAO.

Routes all NAO speech (greetings, prompts, confirmations) through the
server's /tts endpoint, which returns an MP3 synthesized with OpenAI TTS
(nova voice). Plays via ALAudioPlayer.

Falls back to the provided ALTextToSpeech proxy on any failure so the
robot is never silent because of a network blip.
"""
from __future__ import print_function

import os
import time
import requests

from naoqi import ALProxy

import config


_SCRATCH = "/tmp/nao_tts"
_counter = [0]
_player = [None]


def _ensure_player():
    if _player[0] is None:
        _player[0] = ALProxy("ALAudioPlayer", config.NAO_IP, config.NAO_PORT)
    return _player[0]


def _ensure_dir():
    if not os.path.exists(_SCRATCH):
        try:
            os.makedirs(_SCRATCH)
        except Exception:
            pass


def clone_say(tts_proxy, text, fallback_voice=True):
    """Speak `text` via the server's /tts endpoint (OpenAI TTS, nova voice).

    `tts_proxy` is the ALTextToSpeech proxy used as fallback if the request
    fails. Pass None to skip fallback. Blocks until playback finishes.
    """
    if not text or not text.strip():
        return
    text = text.strip()
    url = "http://{0}:{1}/tts".format(config.SERVER_IP, config.SERVER_PORT)
    headers = {"X-NAO-Secret": config.NAO_SHARED_SECRET} if config.NAO_SHARED_SECRET else {}
    try:
        r = requests.post(url, data={"text": text}, headers=headers, timeout=10)
        if r.status_code == 200 and r.content:
            _ensure_dir()
            _counter[0] = (_counter[0] + 1) % 1000
            path = os.path.join(_SCRATCH, "say_{0}.mp3".format(_counter[0]))
            with open(path, "wb") as f:
                f.write(r.content)
            _ensure_player().playFile(path)
            return
        print("[tts_say] HTTP {0}, falling back".format(r.status_code))
    except Exception as e:
        print("[tts_say] error, falling back:", e)
    if fallback_voice and tts_proxy is not None:
        try:
            tts_proxy.say(text)
        except Exception as e:
            print("[tts_say] fallback also failed:", e)
