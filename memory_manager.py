# memory_manager.py  (Python 3)
import os, json

_BASE = os.path.dirname(os.path.abspath(__file__))
_STORE = os.path.join(_BASE, "memory.json")

def _load():
    if not os.path.exists(_STORE):
        return {}
    try:
        with open(_STORE, "r") as f:
            txt = f.read().strip()
            if not txt:
                return {}
            return json.loads(txt)
    except Exception:
        # backup bad file and reset
        try:
            os.rename(_STORE, _STORE + ".bak")
        except Exception:
            pass
        return {}

def _save(data):
    tmp = _STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _STORE)

def initialize_user(username):
    u = (username or "guest").strip().lower()
    data = _load()
    if u not in data:
        data[u] = {"name": None, "history": []}
        _save(data)

def store_user_name(username, name):
    u = (username or "guest").strip().lower()
    data = _load()
    if u not in data:
        data[u] = {"name": None, "history": []}
    data[u]["name"] = name
    _save(data)

def get_user_name(username):
    u = (username or "guest").strip().lower()
    data = _load()
    return (data.get(u) or {}).get("name")

def get_chat_history(username):
    u = (username or "guest").strip().lower()
    data = _load()
    return (data.get(u) or {}).get("history", [])

def add_user_message(username, text):
    u = (username or "guest").strip().lower()
    data = _load()
    if u not in data:
        data[u] = {"name": None, "history": []}
    data[u]["history"].append({"role":"user","content":text})
    _save(data)

def add_bot_reply(username, text):
    u = (username or "guest").strip().lower()
    data = _load()
    if u not in data:
        data[u] = {"name": None, "history": []}
    data[u]["history"].append({"role":"assistant","content":text})
    _save(data)

def save_chat_history(username):
    # already persisted in add_*; keep for API compatibility
    pass

def migrate_username(old_u, new_u):
    old_u = (old_u or "guest").strip().lower()
    new_u = (new_u or old_u).strip().lower()
    if old_u == new_u:
        return
    data = _load()
    if old_u not in data:
        return
    if new_u not in data:
        data[new_u] = {"name": None, "history": []}
    # move name if missing
    if not data[new_u].get("name") and data[old_u].get("name"):
        data[new_u]["name"] = data[old_u]["name"]
    # merge history
    data[new_u]["history"].extend(data[old_u].get("history", []))
    del data[old_u]
    _save(data)
