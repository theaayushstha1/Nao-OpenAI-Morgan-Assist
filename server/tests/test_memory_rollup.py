from unittest.mock import patch
from datetime import datetime, timedelta
from server import session as s
from server import memory_rollup as r


def test_weekly_rollup_fires_on_third_recap(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_DB_PATH", str(tmp_path / "db"))
    with patch.object(r, "_summarize_to_theme", return_value="Stress about finals this week."):
        s.save_recap("alice", "session 1")
        s.save_recap("alice", "session 2")
        s.save_recap("alice", "session 3")
        r.maybe_rollup_week("alice")
    themes = r.load_week_themes("alice")
    assert len(themes) == 1
    assert "finals" in themes[0]


def test_weekly_rollup_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_DB_PATH", str(tmp_path / "db"))
    with patch.object(r, "_summarize_to_theme", return_value="theme"):
        for i in range(5):
            s.save_recap("bob", f"session {i}")
            r.maybe_rollup_week("bob")
    themes = r.load_week_themes("bob")
    assert len(themes) == 1  # still only one theme for the week


def test_monthly_rollup_fires_on_second_weekly_theme(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_DB_PATH", str(tmp_path / "db"))
    r._save_theme("alice", datetime.now(), "week 1 theme")
    r._save_theme("alice", datetime.now() + timedelta(days=7), "week 2 theme")
    with patch.object(r, "_summarize_to_persona", return_value="Growing through finals stress."):
        r.maybe_rollup_month("alice")
    personas = r.load_month_personas("alice")
    assert len(personas) == 1
    assert "Growing" in personas[0]
