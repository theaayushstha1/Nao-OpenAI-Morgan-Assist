"""Smoke test for server.memory. Not pytest — run directly:

    python -m server.test_memory_smoke
"""
from __future__ import annotations

import os
import sys
import tempfile

# Use a throwaway DB so we don't touch the real one.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SESSION_DB"] = _tmp.name

from server import memory  # noqa: E402


def main() -> int:
    print("DB:", _tmp.name)

    # 1. ensure_user
    memory.ensure_user("aayush", "Aayush")
    print("[1] ensure_user('aayush', 'Aayush') OK")

    # 2. start_session + end_session
    sid = memory.start_session("aayush", mode="therapy")
    print("[2a] start_session ->", sid)
    memory.end_session(sid, summary="Discussed exam stress and practiced box breathing.")
    print("[2b] end_session OK")

    # 3. recent_sessions returns 1
    rs = memory.recent_sessions("aayush", n=3)
    print("[3] recent_sessions ->", len(rs), "entries")
    assert len(rs) == 1, "expected exactly 1 session"
    assert rs[0]["summary"].startswith("Discussed"), rs

    # 4. update_profile
    profile = memory.update_profile("aayush", {"interests": ["robotics"]})
    print("[4] update_profile ->", profile)

    # 5. get_profile shows the interest
    p = memory.get_profile("aayush")
    print("[5] get_profile ->", p)
    assert p.get("interests") == ["robotics"], p

    # 5b. build_context_preamble works
    pre = memory.build_context_preamble("aayush")
    print("[5b] build_context_preamble ->", pre)
    assert pre and "Aayush" in pre and "Discussed" in pre

    # 6. forget_user wipes everything
    memory.forget_user("aayush")
    print("[6] forget_user OK")
    assert memory.get_profile("aayush") == {}

    # 7. recent_sessions returns []
    rs = memory.recent_sessions("aayush", n=3)
    print("[7] recent_sessions ->", rs)
    assert rs == [], rs

    # bonus: build_context_preamble for nonexistent user returns ""
    assert memory.build_context_preamble("does-not-exist") == ""
    print("[bonus] build_context_preamble('does-not-exist') -> '' OK")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        try:
            os.unlink(_tmp.name)
        except Exception:
            pass
