# -*- coding: utf-8 -*-
# face_store.py (Python 3)

import os, json
import numpy as np

_BASE = os.path.dirname(os.path.abspath(__file__))
_STORE = os.path.join(_BASE, "face_store.json")

def _load():
    if not os.path.exists(_STORE):
        return {"people": []}
    try:
        with open(_STORE, "r") as f:
            txt = f.read().strip()
            if not txt:
                return {"people": []}
            data = json.loads(txt)
            return data
    except Exception:
        return {"people": []}

def _save(data):
    tmp = _STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _STORE)

def add_encoding(name, encoding_list):
    data = _load()
    name = (name or "").strip()
    if not name:
        return
    # store as list of floats
    for p in data["people"]:
        if p["name"].lower() == name.lower():
            p["encodings"].append(encoding_list)
            _save(data)
            return
    data["people"].append({"name": name, "encodings": [encoding_list]})
    _save(data)

def get_all():
    data = _load()
    names, encs = [], []
    for p in data["people"]:
        for e in p.get("encodings", []):
            names.append(p["name"])
            encs.append(np.array(e, dtype=np.float32))
    return names, encs
