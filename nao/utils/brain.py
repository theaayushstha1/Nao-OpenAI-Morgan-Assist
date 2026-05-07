# -*- coding: utf-8 -*-
"""Robot-side identity / preferences brain cache.

PHASE 7 (PRD v2). This is **not** a knowledge base mirror. It holds:

* ``users``                    -> per-face_id identity + recap state
* ``system_prompt_fragments``  -> short prompt strings the robot can render
                                  without the server (greetings, fallback
                                  identity blurbs)

Knowledge (Morgan CS pages, FAQ, etc.) stays server-side behind the CS
Navigator API. Trying to mirror it here would create staleness and disk
pressure with no UX win.

Hard contract surface (call sites in ``nao/conversation.py``,
``nao/main.py``, ``nao/ws_client.py`` after Phase 7 lands):

    BrainCache(path=None, max_bytes=64*1024)
    .load()                 # rehydrate; corrupt/version mismatch -> wipe
    .save()                 # atomic temp + rename
    .get_user(face_id)      # dict or None
    .upsert_user(face_id, **fields)
    .remove_user(face_id)
    .system_prompt_fragments() -> dict
    .summary() -> dict      # compact handshake payload for WS session_open
    .apply_updates(updates) # merge server-pushed brain_sync deltas
    .clear()                # wipe + re-init at v2

Robot side is **Python 2.7** (naoqi). No f-strings, no type hints, no
walrus, no ``pathlib``. Everything is best-effort; an I/O or permission
error degrades to in-memory only and the caller keeps running.
"""
from __future__ import print_function

import errno
import json
import os
import threading
import time


# ---------------------------------------------------------------------------
# Versioning + sizing
# ---------------------------------------------------------------------------

# The brain JSON schema version. Bump when fields change incompatibly. Any
# file we read with a different ``version`` field is wiped + re-initialised
# at the current version; the WS handshake will then re-pull state from the
# server. Wiping is safer than guessing because the cache is a derivative
# of server-authoritative state.
BRAIN_SCHEMA_VERSION = 2

# Hard upper bound on the on-disk JSON, enforced via LRU eviction in
# ``_enforce_cap``. Picked at 64 KB because that is comfortably below
# anything the robot's flash storage will choke on, while still holding
# tens of users with full preference + recap payloads.
DEFAULT_MAX_BYTES = 64 * 1024

# Default location. The PRD spec says ``~/nao_assist/brain.json`` -- note
# this directory is the rsync deploy target in run.sh and uses
# ``--delete``. See ``CONTRACT NOTE`` below; the env override exists so we
# can flip to ``/home/nao/.nao_assist_brain.json`` without touching the
# code if the deploy story changes. The current default keeps faith with
# the spec; the deploy script must add ``--exclude=brain.json`` (or move
# brain.json to ``/home/nao/.nao_assist/``) before this lands on a robot
# that gets redeployed.
DEFAULT_BRAIN_PATH = os.environ.get(
    "NAO_BRAIN_PATH",
    os.path.expanduser("~/nao_assist/brain.json"),
)

# Default system prompt fragments. These ship with the robot and let it
# greet a user (by face) even when the WS link is down. They must stay
# short -- they are summed into the 64 KB cap.
_DEFAULT_FRAGMENTS = {
    "robot_identity": "I'm NAO at Morgan State CS.",
    "session_greeting_template": "Welcome back, {name}.",
    "first_meeting_template": "Hi, I'm NAO. What's your name?",
}


# ---------------------------------------------------------------------------
# Logging shim. The robot-side logger module isn't always importable from
# this module's import order (utils/* loads early), so we fall back to
# print so a missing logger never silences an I/O error.
# ---------------------------------------------------------------------------

def _log(level, msg):
    try:
        # Lazy import: keeps utils.brain importable even when the
        # structured logger isn't on the path yet.
        from logger import get_logger  # noqa: PLC0415
        get_logger(component="brain").log(level, msg)
    except Exception:
        print("[brain:{0}] {1}".format(level, msg))


def _now_iso():
    """ISO-8601 UTC timestamp (Z-suffixed) without subsecond precision.

    NAO doesn't ship ``datetime.datetime.utcnow().isoformat()`` consistently
    across robot images, and isoformat output varies. Hand-rolling keeps
    this stable.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _empty_brain():
    """Return a minimal valid brain dict at the current schema version."""
    return {
        "version": BRAIN_SCHEMA_VERSION,
        "users": {},
        "system_prompt_fragments": dict(_DEFAULT_FRAGMENTS),
    }


def _normalize_user(face_id, fields):
    """Return a canonical user dict for ``face_id`` populated from ``fields``.

    Unknown keys are dropped. ``last_seen_iso`` is stamped if absent so
    LRU eviction always has something to compare; ``session_count`` is
    coerced to int.
    """
    out = {
        "display_name": "",
        "last_seen_iso": _now_iso(),
        "session_count": 0,
        "preferences": {"likes": [], "dislikes": [], "favorite_color": ""},
        "ongoing_topics": [],
        "last_recap_summary": "",
    }
    if isinstance(fields, dict):
        for k in (
            "display_name", "last_seen_iso", "last_recap_summary",
        ):
            v = fields.get(k)
            if isinstance(v, (bytes, str)) or _is_unicode(v):
                out[k] = _to_text(v)
        sc = fields.get("session_count")
        if isinstance(sc, (int, float)):
            try:
                out["session_count"] = int(sc)
            except Exception:
                pass
        prefs = fields.get("preferences")
        if isinstance(prefs, dict):
            out["preferences"]["likes"] = _list_of_text(prefs.get("likes"))
            out["preferences"]["dislikes"] = _list_of_text(prefs.get("dislikes"))
            fav = prefs.get("favorite_color")
            if fav is not None:
                out["preferences"]["favorite_color"] = _to_text(fav)
            # Preserve any extra preference keys callers stash in here
            # (e.g. ``recognized`` from the user_cache shim, or
            # ``camera_consent`` from a future Phase 6 sync). We only
            # accept JSON-friendly scalar / list types so a buggy caller
            # can't drop a non-serialisable object into the cache.
            for k, v in prefs.items():
                if k in ("likes", "dislikes", "favorite_color"):
                    continue
                if isinstance(v, bool):
                    out["preferences"][k] = bool(v)
                elif isinstance(v, (int, float)):
                    out["preferences"][k] = v
                elif isinstance(v, (bytes, str)) or _is_unicode(v):
                    out["preferences"][k] = _to_text(v)
                elif isinstance(v, list):
                    out["preferences"][k] = _list_of_text(v)
        topics = fields.get("ongoing_topics")
        if isinstance(topics, list):
            # Per PRD: keep only the last 3 topic tags. Bounding here is
            # both a UX cap (topics older than ~3 sessions are stale) and
            # a hedge against the cache ballooning.
            out["ongoing_topics"] = _list_of_text(topics)[-3:]
    out["display_name"] = out["display_name"] or ""
    if not out["last_seen_iso"]:
        out["last_seen_iso"] = _now_iso()
    if isinstance(out["last_recap_summary"], (bytes, str)) or _is_unicode(
        out["last_recap_summary"]
    ):
        # Per PRD: cap recap summary at 300 chars.
        out["last_recap_summary"] = _to_text(out["last_recap_summary"])[:300]
    return out


def _is_unicode(v):
    """Py2/3 unicode check that doesn't blow up on Py3."""
    try:
        # ``unicode`` only exists on Py2; on Py3 it's ``str``.
        return isinstance(v, unicode)  # noqa: F821
    except NameError:
        return isinstance(v, str)


def _to_text(v):
    """Coerce bytes/unicode to ``unicode``-text safely on Py2 and Py3."""
    if v is None:
        return ""
    try:
        if isinstance(v, bytes):
            return v.decode("utf-8", "ignore")
    except Exception:
        return ""
    return v if _is_unicode(v) or isinstance(v, str) else str(v)


def _list_of_text(v):
    """Coerce a list-like value to ``[text, ...]``. Drops non-text entries."""
    if not isinstance(v, list):
        return []
    out = []
    for item in v:
        if item is None:
            continue
        out.append(_to_text(item))
    return out


def _make_parent_dir(path):
    """``mkdir -p`` for the parent of ``path``. Best-effort, never raises."""
    parent = os.path.dirname(path) or "."
    if not parent:
        return True
    try:
        if not os.path.exists(parent):
            os.makedirs(parent)
        return True
    except OSError as exc:
        # EEXIST can race when two processes both try to create the same
        # dir. Treat that as success; everything else is a real failure.
        if exc.errno == errno.EEXIST:
            return True
        _log("warning", "mkdir parent failed for {0}: {1}".format(path, exc))
        return False
    except Exception as exc:
        _log("warning", "mkdir parent failed for {0}: {1}".format(path, exc))
        return False


def _safe_serialize(state):
    """Serialise ``state`` to UTF-8 bytes. Returns ``None`` on failure."""
    try:
        # ``sort_keys`` so the on-disk byte layout is deterministic; this
        # makes diffs in incident logs readable. ``ensure_ascii=False``
        # keeps unicode names ("Aayush") readable without ``\u`` escaping.
        return json.dumps(
            state, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except Exception as exc:
        _log("error", "serialize failed: {0}".format(exc))
        return None


def _atomic_write(path, payload_bytes):
    """Write ``payload_bytes`` to ``path`` atomically.

    Strategy: write to ``<path>.tmp`` then ``os.rename`` over the target.
    POSIX guarantees rename atomicity on the same filesystem, so a crashed
    write can never leave a half-written ``brain.json`` that the next
    ``load()`` would reject and wipe. The temp file is unlinked on failure
    so we don't leak ``.tmp`` debris on the robot.
    """
    if payload_bytes is None:
        return False
    if not _make_parent_dir(path):
        return False
    tmp = path + ".tmp"
    try:
        f = open(tmp, "wb")
        try:
            f.write(payload_bytes)
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                # fsync isn't available on every NAO image / mounted FS.
                # The rename below still gives us atomicity; durability
                # across power loss is best-effort by design.
                pass
        finally:
            f.close()
        os.rename(tmp, path)
        return True
    except Exception as exc:
        _log("error", "atomic write failed for {0}: {1}".format(path, exc))
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# BrainCache
# ---------------------------------------------------------------------------

class BrainCache(object):
    """In-memory + on-disk cache for robot-side identity / preferences.

    Construction is cheap. ``load()`` does the disk hit; callers that
    expect a populated cache must call it explicitly (the constructor
    does NOT call load to keep ``__init__`` side-effect free for tests).

    Thread safety: a single lock guards ``self._state`` because the WS
    handshake thread, the conversation loop, and the brain_sync handler
    can all touch the cache concurrently on the robot.
    """

    def __init__(self, path=None, max_bytes=DEFAULT_MAX_BYTES):
        self.path = path or DEFAULT_BRAIN_PATH
        self.max_bytes = int(max_bytes) if max_bytes else DEFAULT_MAX_BYTES
        self._state = _empty_brain()
        self._lock = threading.RLock()
        # Tracks whether the most recent ``save()`` call made it to disk.
        # Used by ``summary()`` so the server can tell whether the robot
        # is degraded (in-memory only) without having to ask.
        self._on_disk = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def load(self):
        """Rehydrate state from disk. Wipes and re-inits on any defect.

        Defects we wipe on:
          * file missing                       -> initialise fresh + save
          * read I/O failure                   -> in-memory empty brain
          * JSON parse failure                 -> wipe + reinit
          * top-level not a dict               -> wipe + reinit
          * ``version`` missing or != current  -> wipe + reinit
          * ``users`` not a dict               -> wipe + reinit

        Returns ``True`` if state was loaded from disk (any non-default
        content), ``False`` otherwise. The return value is informational;
        the cache is always usable after this call.
        """
        with self._lock:
            if not os.path.exists(self.path):
                # First boot: initialise and try to persist. If save fails
                # (permissions etc.) we still have an in-memory brain.
                self._state = _empty_brain()
                self._on_disk = self.save()
                return False

            try:
                f = open(self.path, "rb")
                try:
                    raw = f.read()
                finally:
                    f.close()
            except Exception as exc:
                _log("error",
                     "read failed at {0}: {1} -- degrading to in-memory".format(
                         self.path, exc))
                self._state = _empty_brain()
                self._on_disk = False
                return False

            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                _log("warning",
                     "corrupt brain at {0}: {1}; wiping".format(self.path, exc))
                self._state = _empty_brain()
                self._on_disk = self.save()
                return False

            if not isinstance(data, dict):
                _log("warning", "brain not a dict; wiping")
                self._state = _empty_brain()
                self._on_disk = self.save()
                return False

            if data.get("version") != BRAIN_SCHEMA_VERSION:
                _log("info",
                     "brain version mismatch ({0} != {1}); wiping".format(
                         data.get("version"), BRAIN_SCHEMA_VERSION))
                self._state = _empty_brain()
                self._on_disk = self.save()
                return False

            users = data.get("users")
            if not isinstance(users, dict):
                _log("warning", "brain users not a dict; wiping")
                self._state = _empty_brain()
                self._on_disk = self.save()
                return False

            # Re-normalise each user so an old field shape can't crash
            # later code paths even after a clean version bump.
            normalised_users = {}
            for face_id, fields in users.items():
                fid = _to_text(face_id)
                if not fid:
                    continue
                normalised_users[fid] = _normalize_user(fid, fields)

            fragments = data.get("system_prompt_fragments")
            if not isinstance(fragments, dict):
                fragments = dict(_DEFAULT_FRAGMENTS)
            else:
                merged = dict(_DEFAULT_FRAGMENTS)
                for k, v in fragments.items():
                    if isinstance(v, (bytes, str)) or _is_unicode(v):
                        merged[_to_text(k)] = _to_text(v)
                fragments = merged

            self._state = {
                "version": BRAIN_SCHEMA_VERSION,
                "users": normalised_users,
                "system_prompt_fragments": fragments,
            }
            self._on_disk = True
            return bool(normalised_users) or bool(fragments)

    def save(self):
        """Persist state to disk. Enforces 64 KB cap via LRU first.

        Returns ``True`` on disk write success, ``False`` if the cache is
        usable in-memory only.
        """
        with self._lock:
            self._enforce_cap()
            payload = _safe_serialize(self._state)
            ok = _atomic_write(self.path, payload)
            self._on_disk = ok
            return ok

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------
    def get_user(self, face_id):
        """Return a deep-ish copy of the user dict for ``face_id``, or
        ``None`` if no such user. The copy ensures callers can't mutate
        the cache by accident."""
        if not face_id:
            return None
        with self._lock:
            user = self._state["users"].get(_to_text(face_id))
            if user is None:
                return None
            # Shallow copy each level we expose; the conversation loop
            # mutates ``preferences``/``ongoing_topics`` lists in
            # transient turn handlers and we don't want those leaking
            # back into the cache without an explicit upsert.
            out = dict(user)
            out["preferences"] = dict(user.get("preferences") or {})
            out["preferences"]["likes"] = list(
                out["preferences"].get("likes") or [])
            out["preferences"]["dislikes"] = list(
                out["preferences"].get("dislikes") or [])
            out["ongoing_topics"] = list(user.get("ongoing_topics") or [])
            return out

    def upsert_user(self, face_id, **fields):
        """Insert-or-update the user record for ``face_id``.

        Fields are merged shallowly into the existing record (so passing
        only ``last_seen_iso=...`` won't clobber preferences). The merged
        record goes through ``_normalize_user`` so callers can't smuggle
        invalid types into the cache.

        Returns the merged user dict.
        """
        if not face_id:
            return None
        fid = _to_text(face_id)
        with self._lock:
            existing = self._state["users"].get(fid, {})
            merged = dict(existing) if isinstance(existing, dict) else {}
            for k, v in fields.items():
                if k == "preferences" and isinstance(v, dict):
                    merged_prefs = dict(merged.get("preferences") or {})
                    merged_prefs.update(v)
                    merged["preferences"] = merged_prefs
                else:
                    merged[k] = v
            # Always refresh last_seen_iso on upsert -- this is the LRU
            # signal. Caller can override by passing it explicitly.
            if "last_seen_iso" not in fields:
                merged["last_seen_iso"] = _now_iso()
            normalised = _normalize_user(fid, merged)
            self._state["users"][fid] = normalised
            return dict(normalised)

    def remove_user(self, face_id):
        """Delete the record for ``face_id``. Returns ``True`` if a record
        was actually removed."""
        if not face_id:
            return False
        fid = _to_text(face_id)
        with self._lock:
            return self._state["users"].pop(fid, None) is not None

    # ------------------------------------------------------------------
    # Server-side surface
    # ------------------------------------------------------------------
    def system_prompt_fragments(self):
        """Return a copy of the prompt fragments dict.

        Copy is intentional: callers (greeter, fallback path) compose
        these into rendered strings and we don't want a join going wrong
        and overwriting the canonical default.
        """
        with self._lock:
            return dict(self._state.get("system_prompt_fragments") or {})

    def summary(self):
        """Compact handshake payload for ``session_open``.

        Schema (per Phase 7 task map)::

            {
              "version": 2,
              "face_id_count": <int>,
              "users": [<face_id>, ...],
              "last_seen_iso": {face_id: iso_str},
              "size_bytes": <int>,
              "on_disk": <bool>,
            }

        ``size_bytes`` reflects the most recent serialised payload (or
        live recompute) so the server can decide whether to push a
        ``brain_sync`` update. ``on_disk`` lets the server know if we're
        running degraded (e.g. permissions error wiped persistence).
        """
        with self._lock:
            users = self._state.get("users") or {}
            last_seen = {}
            for fid, rec in users.items():
                last_seen[fid] = (rec or {}).get("last_seen_iso") or ""
            payload = _safe_serialize(self._state) or b""
            return {
                "version": self._state.get("version", BRAIN_SCHEMA_VERSION),
                "face_id_count": len(users),
                "users": sorted(list(users.keys())),
                "last_seen_iso": last_seen,
                "size_bytes": len(payload),
                "on_disk": bool(self._on_disk),
            }

    def apply_updates(self, updates):
        """Merge an updates dict pushed from the server's ``brain_sync``.

        Expected schema::

            {
              "users": {face_id: {fields_to_update_or_None_to_remove}, ...},
              "system_prompt_fragments": {key: value, ...},
              "remove_users": [face_id, ...],     # optional explicit removes
            }

        A user value of ``None`` (or the ``remove_users`` list) is treated
        as "delete this record" so the server can prune stale entries.

        Returns ``True`` if any change was applied.
        """
        if not isinstance(updates, dict):
            return False
        changed = False
        with self._lock:
            users_update = updates.get("users")
            if isinstance(users_update, dict):
                for fid, payload in users_update.items():
                    fid_text = _to_text(fid)
                    if not fid_text:
                        continue
                    if payload is None:
                        if self._state["users"].pop(fid_text, None) is not None:
                            changed = True
                    elif isinstance(payload, dict):
                        existing = self._state["users"].get(fid_text, {})
                        merged = dict(existing) if isinstance(
                            existing, dict) else {}
                        for k, v in payload.items():
                            if k == "preferences" and isinstance(v, dict):
                                merged_prefs = dict(
                                    merged.get("preferences") or {})
                                merged_prefs.update(v)
                                merged["preferences"] = merged_prefs
                            else:
                                merged[k] = v
                        self._state["users"][fid_text] = _normalize_user(
                            fid_text, merged)
                        changed = True

            removes = updates.get("remove_users")
            if isinstance(removes, list):
                for fid in removes:
                    fid_text = _to_text(fid)
                    if fid_text and self._state["users"].pop(
                            fid_text, None) is not None:
                        changed = True

            fragments_update = updates.get("system_prompt_fragments")
            if isinstance(fragments_update, dict):
                fragments = dict(
                    self._state.get("system_prompt_fragments") or {})
                for k, v in fragments_update.items():
                    if v is None:
                        # Allow server to delete a fragment.
                        if _to_text(k) in fragments:
                            fragments.pop(_to_text(k), None)
                            changed = True
                    elif isinstance(v, (bytes, str)) or _is_unicode(v):
                        fragments[_to_text(k)] = _to_text(v)
                        changed = True
                self._state["system_prompt_fragments"] = fragments

        return changed

    def clear(self):
        """Wipe in-memory state and remove the on-disk file (best-effort).

        ``user_cache.clear()`` historically nuked legacy paths too. We do
        the equivalent by re-initialising and saving an empty brain so
        the next ``load()`` finds a clean v2 file.
        """
        with self._lock:
            self._state = _empty_brain()
            try:
                if os.path.exists(self.path):
                    os.unlink(self.path)
            except Exception as exc:
                _log("warning",
                     "clear unlink failed for {0}: {1}".format(self.path, exc))
            return self.save()

    # ------------------------------------------------------------------
    # LRU eviction
    # ------------------------------------------------------------------
    def _size_bytes(self):
        """Return the serialised size of the current state in bytes.

        ``None`` from ``_safe_serialize`` (very rare -- non-JSON payload)
        is treated as "infinite size" so the cap enforcer leans toward
        evicting rather than letting an unsizable blob grow unchecked.
        """
        payload = _safe_serialize(self._state)
        if payload is None:
            return self.max_bytes + 1
        return len(payload)

    def _enforce_cap(self):
        """Evict oldest users by ``last_seen_iso`` until we are under
        ``self.max_bytes``.

        Sort key: tuple of ``(last_seen_iso, face_id)`` so ties resolve
        deterministically. Oldest first. We never evict from
        ``system_prompt_fragments`` because they are the offline UX
        fallback -- if you blow the budget purely on fragments, that's a
        config bug worth seeing in the logs, not an LRU concern.

        After eviction, a single user too large to fit on its own is
        truncated by stripping its recap summary, then its ongoing
        topics, then its preferences.likes/dislikes lists. This keeps
        the most recent face_id queryable even in the pathological case.
        """
        if self._size_bytes() <= self.max_bytes:
            return

        users = self._state.get("users") or {}
        # Sort oldest -> newest. Empty / missing last_seen sorts to the
        # very front so unused records get evicted first.
        ordered = sorted(
            users.items(),
            key=lambda kv: (kv[1].get("last_seen_iso") or "", kv[0]),
        )
        evicted = []
        # Stop with at least one user kept (the youngest); the trimmer
        # branch below handles the pathological "single user too big"
        # case. Without this guard, a fat upsert could wipe the whole
        # cache, including the very record the caller just inserted.
        for fid, _rec in ordered[:-1] if len(ordered) > 1 else []:
            if self._size_bytes() <= self.max_bytes:
                break
            users.pop(fid, None)
            evicted.append(fid)
        if evicted:
            _log("info",
                 "brain LRU evicted {0} users: {1}".format(
                     len(evicted), evicted))

        # Pathological: a single record still too big. Trim that record.
        if self._size_bytes() > self.max_bytes and users:
            youngest_fid = sorted(
                users.items(),
                key=lambda kv: (kv[1].get("last_seen_iso") or "", kv[0]),
                reverse=True,
            )[0][0]
            user = users[youngest_fid]
            for trim_field, default in (
                ("last_recap_summary", ""),
                ("ongoing_topics", []),
            ):
                if self._size_bytes() <= self.max_bytes:
                    break
                user[trim_field] = default
            if self._size_bytes() > self.max_bytes:
                prefs = user.get("preferences") or {}
                prefs["likes"] = []
                prefs["dislikes"] = []
                user["preferences"] = prefs


# ---------------------------------------------------------------------------
# Module-level singleton helper. Most call sites just want "the" brain.
# Tests inject their own instance via the BrainCache(...) constructor and
# never touch this singleton.
# ---------------------------------------------------------------------------

_DEFAULT_INSTANCE_LOCK = threading.Lock()
_DEFAULT_INSTANCE = None


def get_default():
    """Return the lazily-built default BrainCache, calling ``load()`` once.

    The first caller pays the disk read; subsequent callers see the
    populated cache. ``user_cache.py`` uses this so its load/save shims
    delegate to the same instance the rest of the robot uses.
    """
    global _DEFAULT_INSTANCE
    if _DEFAULT_INSTANCE is not None:
        return _DEFAULT_INSTANCE
    with _DEFAULT_INSTANCE_LOCK:
        if _DEFAULT_INSTANCE is None:
            inst = BrainCache()
            try:
                inst.load()
            except Exception as exc:
                _log("error",
                     "default brain load raised: {0}; using in-memory".format(
                         exc))
            _DEFAULT_INSTANCE = inst
    return _DEFAULT_INSTANCE


def reset_default():
    """Drop the cached singleton. Test/utility hook only."""
    global _DEFAULT_INSTANCE
    with _DEFAULT_INSTANCE_LOCK:
        _DEFAULT_INSTANCE = None


# ---------------------------------------------------------------------------
# Self-test. Runs when invoked directly with ``python brain.py``.
# ---------------------------------------------------------------------------

def _selftest():
    """Smoke test: temp dir, three users, an LRU eviction, reload check."""
    import shutil
    import tempfile

    workdir = tempfile.mkdtemp(prefix="brain_selftest_")
    try:
        path = os.path.join(workdir, "brain.json")
        # Tight cap so a third upsert with a big payload triggers eviction.
        cap = 1024
        b = BrainCache(path=path, max_bytes=cap)
        loaded = b.load()
        assert loaded is False, "fresh cache should not report loaded data"
        assert os.path.exists(path), "load() should init on-disk file"

        # Two small users.
        b.upsert_user("alice", display_name="Alice", session_count=1,
                      last_seen_iso="2026-05-01T00:00:00Z",
                      preferences={"favorite_color": "blue"})
        b.upsert_user("bob", display_name="Bob", session_count=2,
                      last_seen_iso="2026-05-02T00:00:00Z",
                      ongoing_topics=["midterm", "career", "thesis"])
        assert b.save() is True

        # Big user designed to crowd the cap so LRU has to fire.
        big_summary = "x" * 600
        b.upsert_user("carol", display_name="Carol", session_count=3,
                      last_seen_iso="2026-05-03T00:00:00Z",
                      last_recap_summary=big_summary,
                      preferences={"likes": ["robotics"] * 20,
                                   "dislikes": ["traffic"] * 20})
        assert b.save() is True

        # Cache must be under cap.
        size_after = b._size_bytes()
        assert size_after <= cap, (
            "cap not enforced: size={0} cap={1}".format(size_after, cap))

        # Alice (oldest) should be the first evicted; carol (youngest) should
        # still be present even if her record was trimmed in the pathological
        # branch. This asserts LRU correctness, not field preservation.
        assert b.get_user("alice") is None, "alice should be LRU-evicted"
        assert b.get_user("carol") is not None, \
            "carol (youngest) must survive eviction"

        # Reload from disk and re-verify integrity. Because we save()'d
        # after the upserts, the on-disk file must round-trip cleanly.
        b2 = BrainCache(path=path, max_bytes=cap)
        loaded2 = b2.load()
        assert loaded2 is True, "second load should report data present"
        assert b2.get_user("alice") is None
        assert b2.get_user("carol") is not None

        # Summary() shape sanity.
        s = b2.summary()
        assert s["version"] == BRAIN_SCHEMA_VERSION
        assert s["face_id_count"] == len(s["users"])
        assert isinstance(s["last_seen_iso"], dict)
        assert s["size_bytes"] >= 0

        # Apply server-pushed updates.
        applied = b2.apply_updates({
            "users": {
                "carol": {"display_name": "Carol M.",
                          "last_recap_summary": "midterm review good"},
                "dave": {"display_name": "Dave",
                         "last_seen_iso": "2026-05-04T00:00:00Z",
                         "session_count": 1},
                "ghost": None,  # no-op delete (didn't exist)
            },
            "system_prompt_fragments": {
                "robot_identity": "I'm NAO at Morgan State CS, Phase 7.",
            },
            "remove_users": ["nonexistent"],
        })
        assert applied is True
        carol = b2.get_user("carol")
        assert carol["display_name"] == "Carol M."
        assert carol["last_recap_summary"] == "midterm review good"
        dave = b2.get_user("dave")
        assert dave is not None and dave["display_name"] == "Dave"
        frags = b2.system_prompt_fragments()
        assert "Phase 7" in frags["robot_identity"]

        # Corrupt-file recovery: rewrite the file with garbage and ensure
        # load() wipes + re-inits without raising.
        with open(path, "wb") as f:
            f.write(b"not valid json {{{")
        b3 = BrainCache(path=path, max_bytes=cap)
        loaded3 = b3.load()
        assert loaded3 is False, "corrupt file should reset cache"
        assert b3.get_user("carol") is None, \
            "corrupt recovery must not leak old data"

        # Wrong-version file: should also wipe.
        with open(path, "wb") as f:
            f.write(json.dumps({"version": 999, "users": {}}).encode("utf-8"))
        b4 = BrainCache(path=path, max_bytes=cap)
        b4.load()
        assert b4._state["version"] == BRAIN_SCHEMA_VERSION

        # remove_user
        b4.upsert_user("erin", display_name="Erin")
        assert b4.remove_user("erin") is True
        assert b4.remove_user("erin") is False

        # clear()
        assert b4.clear() is True
        assert b4.get_user("anyone") is None

        print("[brain selftest] OK")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
