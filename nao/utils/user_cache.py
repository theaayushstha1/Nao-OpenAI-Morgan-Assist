# -*- coding: utf-8 -*-
"""Identity cache shim. Now backed by ``utils.brain.BrainCache``.

PHASE 7: this module USED to be a hand-rolled JSON file at
``/home/nao/.nao_assist_user.json``. It now delegates to the unified
brain cache (``utils.brain``) so identity, preferences, prompt fragments,
and per-face_id state all live in one ``brain.json``.

Why a shim instead of a hard rewrite of every caller? Two reasons:

1. ``conversation.py``, ``main.py``, ``reset_identity.py`` already call
   ``user_cache.{load, save, clear}``. Touching those is a separate
   change with its own tests. The shim keeps the cutover atomic.
2. The Phase 7 task map explicitly says "preserve existing API."

What changes vs. the old user_cache:

* The persisted record now lives keyed by ``face_id`` inside ``brain.json``
  rather than as the entire payload of ``.nao_assist_user.json``.
* Because the legacy file was untyped (just username + recognized), we
  synthesise a ``face_id`` of ``"local_user"`` for shim writes that don't
  carry a face_id. Real per-face writes go through the brain cache
  directly via ``BrainCache.upsert_user(face_id=...)``.
* Legacy on-disk migration is preserved one-shot: if ``brain.json`` does
  not yet contain ``"local_user"`` and the old ``.nao_assist_user.json``
  exists, we fold it forward and delete the legacy file.

The ``load()/save()/clear()`` signatures are byte-for-byte the same as the
previous module so the existing call sites keep working unchanged.
"""
from __future__ import print_function

import json
import os

from . import brain as _brain


# ``"local_user"`` is the synthetic face_id we route legacy callers through.
# When a caller asks for the cache (no face_id context), we return /
# upsert this entry. Real face-aware code paths (greeter, WS handshake)
# should call brain.get_default() directly with a real face_id.
_LEGACY_FACE_ID = "local_user"

# Old in-tree / out-of-tree cache paths. We migrate forward once: if
# ``brain.json`` is missing the legacy local_user entry but a legacy file
# exists, we copy username/recognized into local_user, save the brain,
# and unlink the legacy file so a future redeploy can't resurrect a
# stale identity.
_LEGACY_PATHS = (
    os.environ.get(
        "NAO_USER_CACHE_PATH", "/home/nao/.nao_assist_user.json"),
    "/home/nao/nao_assist/.last_user.json",  # very old in-tree path
)


try:
    unicode_type = unicode  # noqa: F821  (Py2.7 on NAO)
except NameError:
    unicode_type = str

try:
    bytes_type = bytes
except NameError:
    bytes_type = str

_TEXT_TYPES = (str, unicode_type)


def _read_legacy_one(p):
    """Parse one legacy single-user JSON file. Returns ``{}`` on any
    failure -- this is the same defensive read the old user_cache.py
    used, kept verbatim because we still hit the file at the same time
    in the boot path.
    """
    try:
        if not os.path.exists(p):
            return {}
        f = open(p, "rb")
        try:
            data = json.loads(f.read().decode("utf-8"))
        finally:
            f.close()
        if not isinstance(data, dict):
            return {}
        username = data.get("username")
        if isinstance(username, bytes_type) and not isinstance(
                username, unicode_type):
            username = username.decode("utf-8", "ignore")
        if not isinstance(username, _TEXT_TYPES) or not username:
            return {}
        return {
            "username": username.lower(),
            "recognized": bool(data.get("recognized", False)),
        }
    except Exception as exc:
        print("[user_cache] read error at {0}:".format(p), exc)
        return {}


def _migrate_legacy_if_needed(b):
    """If brain has no ``local_user`` but a legacy file does, fold it in.

    Runs once per process (the brain singleton caches the migration).
    Best-effort: a failing migration just leaves the legacy file alone
    so a later run can retry.
    """
    if b.get_user(_LEGACY_FACE_ID) is not None:
        return False
    for p in _LEGACY_PATHS:
        snap = _read_legacy_one(p)
        if not snap:
            continue
        b.upsert_user(
            _LEGACY_FACE_ID,
            display_name=snap["username"],
            preferences={"recognized": bool(snap.get("recognized", False))},
        )
        b.save()
        # Remove the legacy file so the next ``rsync --delete`` round
        # can't drop it back in front of us mid-session, AND so a fresh
        # checkout doesn't see two competing cache files.
        try:
            os.unlink(p)
            print("[user_cache] migrated legacy {0} -> brain.json".format(p))
        except Exception:
            # If we can't delete it, that's fine -- the shim now reads
            # from brain.json first, so the legacy file is just dead
            # weight we'll clean up on the next deploy.
            pass
        return True
    return False


def _resolve_brain():
    """Get the brain singleton, performing one-shot legacy migration."""
    b = _brain.get_default()
    try:
        _migrate_legacy_if_needed(b)
    except Exception as exc:
        # Migration failure must not break the cache surface. Log + fall
        # through; brain.json itself is still readable and writable.
        print("[user_cache] migration error:", exc)
    return b


def load(path=None):
    """Return ``{'username': str|None, 'recognized': bool}``.

    Argument ``path`` is accepted for backwards compat with callers that
    still pass it (notably the old reset_identity.py). When set, we use
    ``BrainCache(path=path)`` for that one read instead of the singleton
    -- this preserves the legacy "load this specific file" behaviour.

    Never raises. Empty dict ``{}`` on miss / error.
    """
    if path is None:
        b = _resolve_brain()
    else:
        b = _brain.BrainCache(path=path)
        b.load()

    user = b.get_user(_LEGACY_FACE_ID)
    if user is None:
        return {}
    name = user.get("display_name") or ""
    if not name:
        return {}
    prefs = user.get("preferences") or {}
    return {
        "username": name.lower(),
        "recognized": bool(prefs.get("recognized", False)),
    }


def save(username, recognized, path=None):
    """Persist identity. Best-effort; never raises.

    Same signature as the old user_cache.save() so call sites in
    conversation.py keep working. Writes through the brain cache so the
    identity is unified with face_id-keyed records.
    """
    if not username:
        return False
    if isinstance(username, bytes_type) and not isinstance(
            username, unicode_type):
        try:
            username = username.decode("utf-8", "ignore")
        except Exception:
            return False
    if path is None:
        b = _resolve_brain()
    else:
        b = _brain.BrainCache(path=path)
        b.load()
    try:
        b.upsert_user(
            _LEGACY_FACE_ID,
            display_name=(
                username.lower()
                if isinstance(username, _TEXT_TYPES)
                else str(username).lower()
            ),
            preferences={"recognized": bool(recognized)},
        )
        return b.save()
    except Exception as exc:
        print("[user_cache] save error:", exc)
        return False


def clear(path=None):
    """Wipe the legacy local_user record. Used by ``reset_identity.py``.

    Note: this does NOT wipe the entire brain (face_id-keyed records for
    other users are still valuable). To wipe the whole cache, call
    ``brain.get_default().clear()`` directly.
    """
    if path is None:
        b = _resolve_brain()
    else:
        b = _brain.BrainCache(path=path)
        b.load()
    ok = True
    try:
        b.remove_user(_LEGACY_FACE_ID)
        if not b.save():
            ok = False
    except Exception as exc:
        print("[user_cache] clear error:", exc)
        ok = False

    # Also zap any leftover legacy files. Failures here don't fail the
    # whole clear; the brain file is the source of truth now.
    for p in _LEGACY_PATHS:
        try:
            if os.path.exists(p):
                os.unlink(p)
        except Exception as exc:
            print("[user_cache] legacy unlink error at {0}:".format(p), exc)
            ok = False
    return ok


# ---------------------------------------------------------------------------
# Optional face_id-aware helpers. The Phase 7 task map mentions
# ``get_face_id_for_user`` / ``set_face_id_for_user`` as examples of the
# shim surface. Existing call sites don't import them today (verified
# with grep on the worktree), but the names are listed in the task map
# so we expose them here for forward-compat. They are thin wrappers over
# the brain cache; tests live in ``server/tests/test_brain_cache.py``.
# ---------------------------------------------------------------------------

def get_face_id_for_user(display_name):
    """Return the first face_id whose record matches ``display_name``.

    Linear scan over users (cap is small). Returns ``None`` if no match.
    """
    if not display_name:
        return None
    target = display_name.lower() if isinstance(
        display_name, _TEXT_TYPES) else str(display_name).lower()
    b = _resolve_brain()
    summary = b.summary()
    for fid in summary.get("users") or []:
        rec = b.get_user(fid)
        if rec and (rec.get("display_name") or "").lower() == target:
            return fid
    return None


def set_face_id_for_user(face_id, display_name, recognized=True):
    """Upsert ``face_id`` -> ``display_name`` and persist.

    The shim equivalent of "remember this person's face under this name."
    Returns True on success, False on save failure (cache is still
    updated in-memory).
    """
    if not face_id:
        return False
    b = _resolve_brain()
    try:
        b.upsert_user(
            face_id,
            display_name=display_name or "",
            preferences={"recognized": bool(recognized)},
        )
        return b.save()
    except Exception as exc:
        print("[user_cache] set_face_id error:", exc)
        return False
