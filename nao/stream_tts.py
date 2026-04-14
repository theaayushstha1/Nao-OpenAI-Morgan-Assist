# -*- coding: utf-8 -*-
"""Consume an SSE stream of sentences/actions and speak/execute in order."""
from __future__ import print_function

import json
import threading
import requests


def consume(sse_url, files, data, tts, on_action, on_done, timeout=120):
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
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            ev = json.loads(raw[6:])
        except Exception:
            continue
        etype = ev.get("type")
        if etype == "sentence":
            try:
                tts.say(_sayable(ev.get("text", "")))
            except Exception as e:
                print("[stream_tts] say error:", e)
        elif etype == "action":
            try:
                on_action(ev.get("action") or {})
            except Exception as e:
                print("[stream_tts] action error:", e)
        elif etype == "done":
            final = ev
            break
        elif etype == "recognized":
            final["username"] = ev.get("username")
    on_done(final)
    return final


def _sayable(text):
    if isinstance(text, unicode):  # noqa: F821  (Py2.7)
        return text.encode("utf-8", "ignore")
    return str(text)
