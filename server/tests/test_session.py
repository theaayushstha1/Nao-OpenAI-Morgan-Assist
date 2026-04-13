import pytest
from server import session as s


def test_get_or_create_returns_session_with_username():
    sess = s.get_or_create_session("alice")
    assert sess.session_id == "user:alice"


def test_migrate_username_preserves_history(tmp_path, monkeypatch):
    db = tmp_path / "nao.db"
    monkeypatch.setattr(s, "_DB_PATH", str(db))
    sess = s.get_or_create_session("guest")
    import asyncio
    asyncio.run(sess.add_items([{"role": "user", "content": "hi"}]))
    s.migrate_username("guest", "alice")
    new_sess = s.get_or_create_session("alice")
    items = asyncio.run(new_sess.get_items())
    assert any("hi" in str(i.get("content", "")) for i in items)


def test_camera_consent_defaults_true_and_persists(tmp_path, monkeypatch):
    db = tmp_path / "nao.db"
    monkeypatch.setattr(s, "_DB_PATH", str(db))
    assert s.get_camera_consent("bob") is True
    s.set_camera_consent("bob", False)
    assert s.get_camera_consent("bob") is False


def test_recap_save_and_load(tmp_path, monkeypatch):
    db = tmp_path / "nao.db"
    monkeypatch.setattr(s, "_DB_PATH", str(db))
    s.save_recap("alice", "Talked about finals stress, practiced reframing catastrophizing.")
    s.save_recap("alice", "Checked in, better mood, discussed advisor meeting.")
    recaps = s.load_recent_recaps("alice", n=3)
    assert len(recaps) == 2
    assert "advisor" in recaps[0]  # newest first
