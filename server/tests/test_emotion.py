from unittest.mock import patch

from server.tools import emotion


def test_log_emotion_appends_to_context():
    ctx = {"emotion_log": []}
    result = emotion._log_emotion_impl(ctx, mood="sad", intensity=7, trigger="exam stress")
    assert result == "logged"
    assert ctx["emotion_log"][0]["mood"] == "sad"


def test_identify_distortion_returns_known_label():
    with patch("server.tools.emotion._classify_distortion",
               return_value={"distortion": "catastrophizing", "explanation": "..."}):
        out = emotion._identify_distortion_impl("everything is ruined forever")
        assert out["distortion"] == "catastrophizing"


def test_observe_face_with_no_image_returns_error():
    ctx = {"latest_image_b64": None}
    out = emotion._observe_face_impl(ctx)
    assert out == {"error": "no_image"}


def test_observe_face_with_image_returns_emotions(monkeypatch):
    ctx = {"latest_image_b64": "fakebytes"}
    monkeypatch.setattr(
        emotion, "_vision_classify",
        lambda b64: {"dominant_emotion": "sad", "secondary": "tired", "notes": "..."},
    )
    out = emotion._observe_face_impl(ctx)
    assert out["dominant_emotion"] == "sad"


def test_set_camera_consent_persists_and_sets_suppress(monkeypatch):
    from server import session
    calls = []
    monkeypatch.setattr(session, "set_camera_consent", lambda u, e: calls.append((u, e)))
    ctx = {"username": "alice"}
    emotion._set_camera_consent_impl(ctx, False)
    assert calls == [("alice", False)]
    assert ctx["suppress_image"] is True


def test_recap_session_with_no_log_persists_neutral(monkeypatch):
    from server import session
    saved = []
    monkeypatch.setattr(session, "save_recap", lambda u, body: saved.append((u, body)))
    ctx = {"username": "bob", "emotion_log": []}
    result = emotion._recap_session_impl(ctx)
    assert "check-in" in result.lower()
    assert saved[0][0] == "bob"
