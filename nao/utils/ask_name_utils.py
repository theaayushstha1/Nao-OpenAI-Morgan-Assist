# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import time
import requests

from utils.name_utils import extract_name
from utils.speech import random_phrase, expressive_say


def ask_name(tts, nao_ip, server_url, session, record_audio_func, should_abort=None):
    """Ask the user their name via audio, transcribe, and extract.

    Args:
        tts: ALTextToSpeech proxy or qi service.
        nao_ip: NAO robot IP address.
        server_url: URL of the /upload endpoint.
        session: requests.Session for HTTP calls.
        record_audio_func: Callable that takes nao_ip and returns a wav path.

    Returns:
        Extracted name string, or "Guest" as fallback.
    """
    expressive_say(tts, random_phrase("ask_name"), "warm")
    time.sleep(0.5)
    for attempt in range(2):
        if should_abort is not None:
            try:
                if should_abort():
                    return None
            except Exception:
                pass
        wav = record_audio_func(nao_ip)
        if should_abort is not None:
            try:
                if should_abort():
                    return None
            except Exception:
                pass
        if not wav or not os.path.exists(wav):
            if attempt == 0:
                expressive_say(tts, random_phrase("ask_name_retry"), "warm")
            continue
        try:
            try:
                import config as _cfg
                _hdr = {"X-NAO-Secret": _cfg.NAO_SHARED_SECRET} if getattr(_cfg, "NAO_SHARED_SECRET", "") else {}
            except Exception:
                _hdr = {}
            with open(wav, 'rb') as f:
                r = requests.post(server_url + "/turn", files={"audio": f},
                                  data={"username": "guest", "asking_name": "true"},
                                  headers=_hdr, timeout=30)
            spoken = (r.json() or {}).get("user_input", "")
            print("[Heard]: '{}'".format(spoken))
            name = extract_name(spoken)
            if name:
                print("[Extracted name]: {}".format(name))
                return name
            elif attempt == 0:
                expressive_say(tts, random_phrase("ask_name_retry"), "warm")
                time.sleep(0.3)
        except Exception as e:
            print("[Name error]:", e)
            if attempt == 0:
                expressive_say(tts, random_phrase("ask_name_retry"), "warm")
    return "Guest"
