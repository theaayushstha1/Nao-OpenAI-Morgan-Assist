# Phase 7 — Task Map & Contracts

> **Robot-Side Brain (identity & preferences cache, NOT a knowledge base).** Extends `user_cache.py`. Capped at 64 KB. Knowledge stays on CS Navigator API. Robot can do limited offline (presence + greeting) when WS fails.

PRD: PRD_v2.md Phase 7.

## Branch policy
Worktree per agent off `dev/architecture-rework`. Commit message: `[Phase 7] <slug>: <summary>`.

## File ownership

| Slug | Files OWNED |
|------|-------------|
| `brain-cache-robot` | `nao/utils/brain.py` (NEW), `nao/utils/user_cache.py` (preserve existing API; brain.py imports it for back-compat) |
| `ws-handshake-update` | `nao/ws_client.py` (extend session_open handshake to include face_id + brain_version + brain_summary) |
| `server-cache-sync` | `server/app_ws.py` (handle brain_sync control frame), `server/session.py` (helper `pull_brain_updates(face_id, since_version)`) |
| `brain-tests` | `server/tests/test_brain_cache.py` (NEW) |

## Schema (`~/nao_assist/brain.json`)
```json
{
  "version": 2,
  "users": {
    "<face_id>": {
      "display_name": "...",
      "last_seen_iso": "2026-05-06T22:00:00Z",
      "session_count": 12,
      "preferences": {"likes": [], "dislikes": [], "favorite_color": ""},
      "ongoing_topics": ["midterm_anxiety", "career_path"],
      "last_recap_summary": "(<=300 chars)"
    }
  },
  "system_prompt_fragments": {
    "robot_identity": "I'm NAO at Morgan State CS...",
    "session_greeting_template": "Welcome back, {name}.",
    "first_meeting_template": "Hi, I'm NAO. What's your name?"
  }
}
```

Hard cap: 64 KB total file size. LRU evict oldest user entries to enforce.

## Public APIs

### `nao/utils/brain.py`
```python
class BrainCache(object):
    def __init__(self, path="~/nao_assist/brain.json", max_bytes=64*1024): ...
    def load(self): ...                         # rehydrate from disk; corrupt → wipe + re-init
    def save(self): ...                         # atomic write (temp file + rename)
    def get_user(self, face_id) -> dict: ...    # returns user dict or None
    def upsert_user(self, face_id, **fields): ...
    def remove_user(self, face_id): ...
    def system_prompt_fragments(self) -> dict: ...
    def summary(self) -> dict:
        """Returns the small handshake summary the robot sends to server:
        {face_id_count, version, last_seen_iso_per_face, brain_size_bytes}"""
    def apply_updates(self, updates: dict) -> None:
        """Apply updates from server's brain_sync push."""
```

LRU eviction: when `self._size_bytes() + new_entry_size > max_bytes`, drop oldest by `last_seen_iso` until under cap.

`user_cache.py` becomes a thin shim that delegates to BrainCache for `get_face_id_for_user` / `set_face_id_for_user` (backwards compat for any existing callers).

### `nao/ws_client.py` handshake change
On `session_open`, include:
```json
{ "subtype": "session_open",
  "data": { "face_id": "abc", "brain_version": 2,
            "brain_summary": { "users": ["abc"], "last_seen_iso": {...}, "size_bytes": 4123 },
            "hint": "chat" } }
```

### `server/app_ws.py` — brain sync flow
On receiving `session_open`:
- Call `session.pull_brain_updates(face_id=data["face_id"], since_version=data["brain_version"])`.
- If non-empty updates: emit `control { subtype: "brain_sync", data: {updates: {...}} }` BEFORE the greeting.
- Robot client (in this slug — added to ws_client) handles `brain_sync` → calls `brain.apply_updates(...)` → save.

### `server/session.py` — `pull_brain_updates(face_id, since_version) -> dict`
- Returns updates derived from server-side state since `since_version`.
- Schema: `{ "users": {face_id: {fields_to_update}}, "system_prompt_fragments": {...} }`.
- For Phase 7 minimum, just sync `last_seen_iso`, `last_recap_summary` (from recaps table), and `display_name` if changed.

## Reused-as-is
- All other modules.

## Definition of done
1. Compile checks.
2. brain.json schema validation; corrupt → wipe + re-init.
3. 64 KB cap enforced via LRU.
4. WS handshake new fields work end-to-end.
5. brain_sync push → apply_updates → save.
6. Tests collect.
