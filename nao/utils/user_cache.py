# -*- coding: utf-8 -*-
"""Persistent identity cache for the NAO-side runtime.

Why this exists: `_USER_CACHE` in conversation.py is module-level Python state.
It's lost on every NAO process restart, which is why the robot kept asking the
same user for their name on cold boot even though server-side memory already
knew them. We persist {username, recognized} to a small JSON file under
/home/nao/nao_assist/ so the next process boot can skip the face scan + name
ask entirely.

The file is intentionally tiny and best-effort: any I/O error degrades to "no
cached user" and the caller falls back to the live face/ask flow.
"""
from __future__ import print_function

import json
import os

try:
    unicode_type = unicode  # noqa: F821  (Py2.7 on NAO)
except NameError:
    unicode_type = str

try:
    bytes_type = bytes
except NameError:
    bytes_type = str

_TEXT_TYPES = (str, unicode_type)


# IMPORTANT: must live OUTSIDE the deploy tree.
#
# run.sh uses `rsync -az --delete nao/ -> /home/nao/nao_assist/`. Anything
# inside /home/nao/nao_assist/ that isn't in the local repo gets WIPED on
# every redeploy — which is exactly what was happening to the old cache
# path .last_user.json: written by main.py, deleted by next rsync, NAO
# forgets the user every time we ship code. The new default path is a
# hidden file in nao's home, which rsync never touches.
_DEFAULT_PATH = os.environ.get(
    "NAO_USER_CACHE_PATH",
    "/home/nao/.nao_assist_user.json",
)

# Old in-tree location, kept for one-time migration. If we boot up and the
# new path is empty but the old path has data, copy it forward before that
# old file gets deleted by the next deploy.
_LEGACY_PATHS = (
    "/home/nao/nao_assist/.last_user.json",
)


def _read_one(p):
    """Internal: parse a single cache file. Returns {} on any failure."""
    try:
        if not os.path.exists(p):
            return {}
        with open(p, "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
        if not isinstance(data, dict):
            return {}
        username = data.get("username")
        if isinstance(username, bytes_type) and not isinstance(username, unicode_type):
            username = username.decode("utf-8", "ignore")
        if not isinstance(username, _TEXT_TYPES) or not username:
            return {}
        return {
            "username": username.lower(),
            "recognized": bool(data.get("recognized", False)),
        }
    except Exception as e:
        print("[user_cache] read error at {0}:".format(p), e)
        return {}


def load(path=None):
    """Return {'username': str|None, 'recognized': bool} from disk.

    Never raises. Returns empty dict if the file is missing, unreadable, or
    malformed. On first run after the cache-path migration, falls back to
    the legacy in-tree path and writes the data forward to the new safe
    path so the next redeploy doesn't lose it.
    """
    p = path or _DEFAULT_PATH
    found = _read_one(p)
    if found:
        return found
    # New path empty — try the legacy in-tree path and migrate forward.
    if path is None:
        for legacy in _LEGACY_PATHS:
            legacy_data = _read_one(legacy)
            if legacy_data:
                save(legacy_data["username"], legacy_data["recognized"], path=p)
                print("[user_cache] migrated from {0} -> {1}".format(legacy, p))
                return legacy_data
    return {}


def save(username, recognized, path=None):
    """Persist identity to disk. Best-effort; never raises.

    Creates parent directory if missing. Writes atomically via temp + rename so
    a crashed write doesn't leave a half-file that load() then rejects.
    """
    if not username:
        return False
    p = path or _DEFAULT_PATH
    try:
        parent = os.path.dirname(p) or "."
        if not os.path.exists(parent):
            try:
                os.makedirs(parent)
            except Exception:
                pass
        tmp = p + ".tmp"
        if isinstance(username, bytes_type) and not isinstance(username, unicode_type):
            username = username.decode("utf-8", "ignore")
        payload = json.dumps({
            "username": username.lower() if isinstance(username, _TEXT_TYPES) else str(username).lower(),
            "recognized": bool(recognized),
        })
        with open(tmp, "wb") as f:
            f.write(payload.encode("utf-8"))
        os.rename(tmp, p)
        return True
    except Exception as e:
        print("[user_cache] save error:", e)
        return False


def clear(path=None):
    """Remove the cache file. Used by 'forget me' style commands.

    Also wipes legacy paths so a stale in-tree file can't reappear after a
    redeploy and silently re-identify the user with old data.
    """
    paths = [path] if path else [_DEFAULT_PATH] + list(_LEGACY_PATHS)
    ok = True
    for p in paths:
        try:
            if os.path.exists(p):
                os.unlink(p)
        except Exception as e:
            print("[user_cache] clear error at {0}:".format(p), e)
            ok = False
    return ok
