"""Phase 7 - tests for the robot-side `BrainCache` (identity & preferences).

These tests guard the Phase 7 contract from `docs/PHASE_7_TASK_MAP.md`:

  - Schema v2 created on first load, with `users` and `system_prompt_fragments`
    sections.
  - Corrupt `brain.json` is wiped and re-initialized rather than crashing the
    NAO process. The robot's identity cache must NEVER be a fatal-on-boot
    surface.
  - Atomic `save()` (temp file + rename) so a crash mid-write doesn't leave
    a half-file that the next `load()` would treat as corrupt and wipe.
  - 64 KB hard cap with LRU eviction by `last_seen_iso`. The brain is meant
    to hold ~tens of users, not the world.
  - `summary()` returns the small dict shipped in the WS handshake so the
    server can compute a delta and push back via `brain_sync`.
  - `apply_updates()` merges server-pushed fields onto the local copy.
  - The legacy `user_cache.py` API (`load`, `save`, `clear`) keeps working
    so existing callers in `main.py`, `conversation.py`, `reset_identity.py`
    don't break the day Phase 7 lands.

The `brain-cache-robot` sibling worktree owns `nao/utils/brain.py`. Until
that lands on `dev/architecture-rework`, every test here is
`pytest.importorskip`-guarded so collection still passes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _import_brain():
    """Import nao.utils.brain or skip the calling test cleanly.

    Splits the import from the test bodies so each test reads as a single
    intent rather than a wall of importorskip boilerplate.
    """
    return pytest.importorskip("nao.utils.brain")


def _make_brain(tmp_path, max_bytes=64 * 1024):
    """Construct a BrainCache pointed at an isolated tmp file."""
    brain_mod = _import_brain()
    path = tmp_path / "brain.json"
    return brain_mod.BrainCache(path=str(path), max_bytes=max_bytes), path


def _iso(dt):
    """Render a UTC datetime in the same shape as the schema example."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fresh init
# ─────────────────────────────────────────────────────────────────────────────


def test_brain_init_creates_v2_schema_when_missing(tmp_path):
    """Loading from a tmp dir with no brain.json must materialize a v2 file.

    The robot ships fresh out of the box with no brain.json on disk - load()
    has to seed the schema, persist it, and end in a state where summary()
    reports version 2 with no users.
    """
    brain_mod = _import_brain()
    path = tmp_path / "brain.json"
    assert not path.exists(), "precondition: brain.json must not exist yet"

    brain = brain_mod.BrainCache(path=str(path), max_bytes=64 * 1024)
    brain.load()

    # File should now exist on disk with a v2 schema.
    assert path.exists(), "load() on missing file must create brain.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk.get("version") == 2, (
        "fresh brain must declare schema version 2"
    )
    assert isinstance(on_disk.get("users"), dict)
    # system_prompt_fragments is part of the schema example - the brain
    # ships baked-in identity strings so the robot can talk pre-WS.
    assert "system_prompt_fragments" in on_disk


# ─────────────────────────────────────────────────────────────────────────────
# 2. Corrupt file recovery
# ─────────────────────────────────────────────────────────────────────────────


def test_brain_corrupt_file_is_wiped_and_reinit(tmp_path):
    """Garbage in brain.json must not crash the NAO process on boot.

    A power-cut mid-save or a manual edit gone wrong should never brick
    the robot's identity layer. `load()` is required to wipe the file
    and re-seed the v2 schema.
    """
    brain_mod = _import_brain()
    path = tmp_path / "brain.json"
    path.write_text("not even close to json {{{ broken", encoding="utf-8")

    brain = brain_mod.BrainCache(path=str(path), max_bytes=64 * 1024)
    brain.load()  # must NOT raise

    # File should be re-initialized to a valid v2 schema.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk.get("version") == 2
    assert isinstance(on_disk.get("users"), dict)
    # Old garbage must be gone - no stray top-level keys leaking through.
    assert "broken" not in path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Persistence round-trip
# ─────────────────────────────────────────────────────────────────────────────


def test_upsert_user_persists_after_save_and_reload(tmp_path):
    """upsert_user -> save -> new BrainCache instance -> get_user must match.

    This is the round-trip that the boot-up greeting depends on: the robot
    learned someone last week, restarted, and needs to remember them today.
    """
    brain_mod = _import_brain()
    path = tmp_path / "brain.json"

    brain = brain_mod.BrainCache(path=str(path), max_bytes=64 * 1024)
    brain.load()
    brain.upsert_user(
        "face_aayush",
        display_name="Aayush",
        last_seen_iso=_iso(datetime.now(timezone.utc)),
        session_count=3,
        preferences={"likes": ["coffee"], "dislikes": [], "favorite_color": "blue"},
        ongoing_topics=["midterm_anxiety"],
        last_recap_summary="Talked about finals stress.",
    )
    brain.save()

    # Brand-new instance, same path - simulating a fresh process boot.
    fresh = brain_mod.BrainCache(path=str(path), max_bytes=64 * 1024)
    fresh.load()
    user = fresh.get_user("face_aayush")
    assert user is not None, "user must survive save->reload"
    assert user.get("display_name") == "Aayush"
    assert user.get("session_count") == 3
    assert user.get("preferences", {}).get("favorite_color") == "blue"
    assert user.get("last_recap_summary") == "Talked about finals stress."


# ─────────────────────────────────────────────────────────────────────────────
# 4. 64 KB cap + LRU eviction
# ─────────────────────────────────────────────────────────────────────────────


def test_64kb_cap_lru_evicts_oldest(tmp_path):
    """When the file would exceed max_bytes, oldest by last_seen_iso evicts.

    We pin max_bytes to a small value so we don't need to actually cram in
    multi-KB entries to trigger eviction. The cap+LRU contract is the same
    regardless of the absolute number.
    """
    brain_mod = _import_brain()
    path = tmp_path / "brain.json"

    # Tiny cap so a few users are enough to trigger eviction. A single user
    # entry with name + ISO + session count + preferences easily hits 200B.
    brain = brain_mod.BrainCache(path=str(path), max_bytes=2_000)
    brain.load()

    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Insert users with increasing last_seen_iso so user_0 is oldest.
    for i in range(20):
        brain.upsert_user(
            "face_{0:02d}".format(i),
            display_name="User {0:02d}".format(i),
            last_seen_iso=_iso(base + timedelta(hours=i)),
            session_count=i,
            preferences={
                "likes": ["thing_a", "thing_b"],
                "dislikes": ["x"],
                "favorite_color": "color_{0}".format(i),
            },
            ongoing_topics=["topic_{0}".format(i)],
            last_recap_summary="recap " * 10,
        )
        brain.save()

    # File must be under cap.
    size = os.path.getsize(str(path))
    assert size <= 2_000, (
        "brain.json size {0}B exceeds max_bytes 2000B; LRU eviction is "
        "not enforcing the cap".format(size)
    )

    # Newest user must still be present.
    newest = brain.get_user("face_19")
    assert newest is not None, (
        "newest user evicted - LRU must drop oldest first, not newest"
    )

    # Some old user must have been evicted (we can't pin exactly which one
    # because that depends on per-entry size, but face_00 is the strongest
    # candidate and at least one of the early users must be gone).
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    surviving = set(on_disk.get("users", {}).keys())
    early = {"face_{0:02d}".format(i) for i in range(5)}
    assert early - surviving, (
        "no early users were evicted despite hitting cap; surviving={0}"
        .format(sorted(surviving))
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. summary() shape (the WS handshake payload)
# ─────────────────────────────────────────────────────────────────────────────


def test_summary_format_correct(tmp_path):
    """summary() must return the exact dict shape the ws_client ships.

    Per PHASE_7_TASK_MAP `brain_summary` example:
      { "users": ["abc"], "last_seen_iso": {...}, "size_bytes": 4123 }
    plus a `version` int.
    """
    brain, _path = _make_brain(tmp_path)
    brain.load()
    brain.upsert_user(
        "face_a",
        display_name="A",
        last_seen_iso=_iso(datetime(2026, 5, 1, tzinfo=timezone.utc)),
    )
    brain.upsert_user(
        "face_b",
        display_name="B",
        last_seen_iso=_iso(datetime(2026, 5, 2, tzinfo=timezone.utc)),
    )
    brain.save()

    summary = brain.summary()
    assert isinstance(summary, dict)
    # All four keys must be present.
    for key in ("version", "users", "last_seen_iso", "size_bytes"):
        assert key in summary, "summary() missing key {0!r}".format(key)

    assert summary["version"] == 2
    # `users` is a list of face_id strings (per the handshake JSON example).
    assert isinstance(summary["users"], list)
    assert set(summary["users"]) == {"face_a", "face_b"}
    # `last_seen_iso` is a dict mapping face_id -> ISO string.
    assert isinstance(summary["last_seen_iso"], dict)
    assert summary["last_seen_iso"].keys() == {"face_a", "face_b"}
    for v in summary["last_seen_iso"].values():
        assert isinstance(v, str) and v.endswith("Z")
    # `size_bytes` is the on-disk file size, an int >0.
    assert isinstance(summary["size_bytes"], int)
    assert summary["size_bytes"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. apply_updates merges existing user
# ─────────────────────────────────────────────────────────────────────────────


def test_apply_updates_merges_user_fields(tmp_path):
    """apply_updates must shallow-merge into an existing user.

    The server pushes deltas via `brain_sync`. The robot needs to honor the
    new fields without dropping ones the server didn't mention - otherwise
    we'd lose preferences every time the server only sent a recap update.
    """
    brain, _path = _make_brain(tmp_path)
    brain.load()
    brain.upsert_user(
        "face_x",
        display_name="X",
        last_seen_iso=_iso(datetime(2026, 5, 1, tzinfo=timezone.utc)),
        session_count=1,
        preferences={"likes": ["coffee"], "dislikes": [], "favorite_color": "red"},
    )
    brain.save()

    brain.apply_updates({
        "users": {
            "face_x": {
                "session_count": 5,
                "last_recap_summary": "Worked through reframing.",
                "last_seen_iso": _iso(datetime(2026, 5, 6, tzinfo=timezone.utc)),
            }
        }
    })

    user = brain.get_user("face_x")
    assert user is not None
    # Updated fields applied.
    assert user.get("session_count") == 5
    assert user.get("last_recap_summary") == "Worked through reframing."
    # Pre-existing fields not overwritten by the partial update.
    assert user.get("display_name") == "X", (
        "apply_updates blew away display_name not in the delta - merge is "
        "supposed to be additive on the user object"
    )
    assert user.get("preferences", {}).get("favorite_color") == "red"


# ─────────────────────────────────────────────────────────────────────────────
# 7. apply_updates creates a missing user
# ─────────────────────────────────────────────────────────────────────────────


def test_apply_updates_creates_new_user(tmp_path):
    """A face_id the robot has never seen must be created via apply_updates.

    This is how a new user the server already knows about (e.g. learned on
    a different robot, or restored from a wipe) shows up locally.
    """
    brain, _path = _make_brain(tmp_path)
    brain.load()
    assert brain.get_user("face_new") is None

    brain.apply_updates({
        "users": {
            "face_new": {
                "display_name": "Newcomer",
                "last_seen_iso": _iso(datetime(2026, 5, 6, tzinfo=timezone.utc)),
                "session_count": 1,
                "last_recap_summary": "First session.",
            }
        }
    })

    user = brain.get_user("face_new")
    assert user is not None, "apply_updates failed to create absent user"
    assert user.get("display_name") == "Newcomer"
    assert user.get("session_count") == 1


# ─────────────────────────────────────────────────────────────────────────────
# 8. Atomic write (no half-file on crash)
# ─────────────────────────────────────────────────────────────────────────────


def test_atomic_write_no_partial_file_on_crash(tmp_path, monkeypatch):
    """If save() crashes mid-write, the original brain.json must be intact.

    The PHASE_7 spec says save() must use temp file + rename. Renames are
    atomic on POSIX, so even a kill -9 halfway through writing the temp
    file leaves the real path untouched. We simulate the crash by making
    the underlying open() raise after the first successful save.
    """
    brain_mod = _import_brain()
    path = tmp_path / "brain.json"

    brain = brain_mod.BrainCache(path=str(path), max_bytes=64 * 1024)
    brain.load()
    brain.upsert_user(
        "face_known",
        display_name="Known",
        last_seen_iso=_iso(datetime(2026, 5, 1, tzinfo=timezone.utc)),
        session_count=42,
    )
    brain.save()

    pre_crash = path.read_text(encoding="utf-8")
    pre_crash_bytes = path.read_bytes()

    # Sanity: known user present in the pre-crash file.
    assert "face_known" in pre_crash
    assert json.loads(pre_crash).get("version") == 2

    # Simulate a crash mid-save. We patch builtins.open so that ANY write
    # mode call inside save() blows up, but reads still work. This stops
    # the temp-file write before the rename can happen.
    real_open = open

    def _exploding_open(file, mode="r", *args, **kwargs):
        # Detect any write to a path under the brain dir (incl. .tmp / .bak).
        # We accept the test-side read of pre_crash that already happened.
        try:
            target = os.fspath(file)
        except TypeError:
            target = str(file)
        write_mode = any(c in mode for c in ("w", "a", "x", "+"))
        if write_mode and str(path) in target:
            raise OSError("simulated crash mid-save")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _exploding_open)

    # Mutate state and try to save - must raise (or swallow) without
    # corrupting the on-disk file.
    brain.upsert_user(
        "face_known",
        display_name="ShouldNotBePersisted",
        last_seen_iso=_iso(datetime(2026, 5, 6, tzinfo=timezone.utc)),
        session_count=999,
    )
    try:
        brain.save()
    except OSError:
        # Acceptable - the spec doesn't require save() to swallow IO errors,
        # only that the file on disk must not be half-written.
        pass

    # Restore real open so we can read the file back.
    monkeypatch.setattr("builtins.open", real_open)

    # Original file must be byte-identical to pre-crash state. Atomic
    # rename means the .tmp file (if any) never overwrote the real path.
    post_crash_bytes = path.read_bytes()
    assert post_crash_bytes == pre_crash_bytes, (
        "brain.json was modified during a crashed save - atomic write "
        "(temp + rename) is not in place"
    )

    # And the file must still parse cleanly.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk.get("version") == 2
    user = on_disk.get("users", {}).get("face_known", {})
    assert user.get("session_count") == 42, (
        "old session_count was overwritten despite the crashed save"
    )
    assert user.get("display_name") == "Known"

    # No leftover .tmp file blocking the next legit save.
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert not leftovers, (
        "temp file left over after crashed save: {0}".format(leftovers)
    )


# ─────────────────────────────────────────────────────────────────────────────
# 9. Backwards-compat shim (legacy user_cache.py)
# ─────────────────────────────────────────────────────────────────────────────


def test_user_cache_shim_backwards_compat(tmp_path, monkeypatch):
    """Existing callers of user_cache.load/save/clear must still work.

    main.py, conversation.py, and reset_identity.py all import the legacy
    `user_cache` module today. The Phase 7 PR is allowed to swap the
    backend to BrainCache, but the public surface MUST continue to behave
    the same so we don't end up shipping a breaking change to nao-side.
    """
    # Skip cleanly if brain hasn't landed yet - the shim depends on it.
    _import_brain()

    # Force the user_cache module to a tmp path so we don't touch the real
    # /home/nao/ filesystem during tests.
    cache_path = tmp_path / "legacy_user.json"
    monkeypatch.setenv("NAO_USER_CACHE_PATH", str(cache_path))

    # Re-import to pick up the env override - the module reads the env at
    # import time into _DEFAULT_PATH.
    import importlib
    import nao.utils.user_cache as user_cache
    user_cache = importlib.reload(user_cache)

    # Initial load: nothing on disk yet -> empty dict.
    assert user_cache.load() == {}

    # Save + reload round-trip.
    assert user_cache.save("Aayush", True) is True
    snap = user_cache.load()
    assert snap.get("username") == "aayush"  # canonical lower-case
    assert snap.get("recognized") is True

    # Clear wipes the cache.
    assert user_cache.clear() is True
    assert user_cache.load() == {}
