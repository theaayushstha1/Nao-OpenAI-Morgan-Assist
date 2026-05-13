from __future__ import annotations

import json

import pytest

pytest.importorskip("server.app_ws")

from server import app_ws  # noqa: E402


class _FakeWs:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.frames.append(json.loads(payload))


@pytest.mark.asyncio
async def test_recognized_identity_ignores_late_unknown_scan(monkeypatch):
    """A late unknown face scan must not re-open name onboarding.

    Robot-side face recognition can send a confident match and then a later
    unknown result as lighting/angle changes. Once a session is recognized,
    identity is sticky for that session.
    """
    app_ws._IDENTIFIED_USERS.clear()
    sess = app_ws._Session("guest")
    ws = _FakeWs()
    calls: list[tuple[str, str | None]] = []

    async def _fake_greet(_ws, _sess, display_name, *, reason):
        calls.append(("greet", display_name))

    async def _fake_prompt(_ws, _sess, *, reason):
        calls.append(("prompt", reason))
        _sess.asking_name = True

    monkeypatch.setattr(app_ws, "_emit_returning_identity_greeting", _fake_greet)
    monkeypatch.setattr(app_ws, "_emit_onboarding_name_prompt", _fake_prompt)

    await app_ws._ingest_control(ws, sess, {
        "subtype": "user_identified",
        "data": {
            "name": "Aayush",
            "recognized": True,
            "face_visible": True,
            "source": "face",
        },
    })
    await app_ws._ingest_control(ws, sess, {
        "subtype": "user_identified",
        "data": {
            "name": None,
            "recognized": False,
            "face_visible": True,
            "source": "late_unknown_face",
        },
    })

    assert calls == [("greet", "Aayush")]
    assert sess.username == "aayush"
    assert sess.asking_name is False
    assert app_ws._IDENTIFIED_USERS[sess.session_id]["recognized"] is True
    assert app_ws._IDENTIFIED_USERS[sess.session_id]["name"] == "Aayush"


@pytest.mark.asyncio
async def test_onboarding_prompt_skips_when_identity_already_recognized():
    app_ws._IDENTIFIED_USERS.clear()
    sess = app_ws._Session("aayush")
    ws = _FakeWs()
    sess.asking_name = True
    app_ws._IDENTIFIED_USERS[sess.session_id] = {
        "name": "Aayush",
        "recognized": True,
        "face_visible": True,
        "greeted": True,
        "prompted": False,
    }

    await app_ws._emit_onboarding_name_prompt(
        ws,
        sess,
        reason="late_unknown_face",
    )

    assert ws.frames == []
    assert sess.asking_name is False


def test_onboarding_prompt_echo_is_detected():
    transcript = (
        "Heads up. My camera is on for this conversation. "
        "Hi, I'm NAO. What should I call you?"
    )

    assert app_ws._is_onboarding_prompt_echo(transcript) is True


def test_onboarding_prompt_echo_does_not_match_real_name():
    assert app_ws._is_onboarding_prompt_echo("you can call me Aayush") is False


@pytest.mark.asyncio
async def test_wake_event_binds_returning_username_before_session_resume(monkeypatch):
    app_ws._IDENTIFIED_USERS.clear()
    sess = app_ws._Session("guest")
    ws = _FakeWs()
    ensure_calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        app_ws,
        "_lookup_returning_user",
        lambda face_id: (True, "Aayush"),
    )
    monkeypatch.setattr(app_ws, "_last_recap_line", lambda username: None)
    monkeypatch.setattr(app_ws, "_synth_for", lambda username, text: b"mp3")

    def _fake_ensure(username, hint):
        ensure_calls.append((username, hint))
        return 1

    monkeypatch.setattr(app_ws.legacy, "ensure_active_session", _fake_ensure)

    await app_ws._handle_wake_event(ws, sess, {
        "face_id": "Aayush",
        "gate": "face",
        "confidence": 0.91,
        "distance_m": 0.8,
    })

    assert sess.username == "aayush"
    assert ensure_calls == [("aayush", None)]
    assert app_ws._IDENTIFIED_USERS[sess.session_id]["recognized"] is True
    assert app_ws._IDENTIFIED_USERS[sess.session_id]["prompted"] is False
