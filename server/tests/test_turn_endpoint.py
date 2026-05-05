import io
from unittest.mock import patch, MagicMock

import pytest

from server.server import app, _looks_like_hallucination


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_turn_happy_path_general(client):
    with open("server/tests/fixtures/sample.wav", "rb") as f:
        audio_bytes = f.read()
    with patch("server.server._validate_wav", return_value=True), \
         patch("server.server._transcribe", return_value="tell me a fun fact"), \
         patch("server.server.safety.crisis_check") as crisis, \
         patch("server.server._run_agent") as runner:
        crisis.return_value = MagicMock(positive=False, source="clean")
        runner.return_value = ("Hello back!", "chat", [
            {"name": "wave_hand", "args": {"hand": "right"}}
        ], False)
        r = client.post("/turn", data={
            "audio": (io.BytesIO(audio_bytes), "sample.wav"),
            "username": "alice",
            "hint": "chat",
        }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["reply"] == "Hello back!"
    assert body["active_agent"] == "chat"
    assert body["actions"][0]["name"] == "wave_hand"
    assert body["crisis"] is False


def test_turn_crisis_bypasses_agent(client):
    with open("server/tests/fixtures/sample.wav", "rb") as f:
        audio_bytes = f.read()
    with patch("server.server._validate_wav", return_value=True), \
         patch("server.server._transcribe", return_value="i want to kill myself"), \
         patch("server.server._run_agent") as runner:
        r = client.post("/turn", data={
            "audio": (io.BytesIO(audio_bytes), "sample.wav"),
            "username": "bob",
        }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["crisis"] is True
    assert "988" in body["reply"]
    assert not runner.called


def test_turn_end_session_triggers_recap(client):
    with patch("server.server._run_recap") as recap:
        recap.return_value = "recap saved"
        r = client.post("/turn", data={
            "username": "alice",
            "end_session": "true",
        }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("reply") == "recap saved"
    assert recap.called


def test_turn_rejects_robot_greeting_echo(client):
    with open("server/tests/fixtures/sample.wav", "rb") as f:
        audio_bytes = f.read()
    transcript = "Hey there, it's great to see you again. How's your day going so far?"
    with patch("server.server._validate_wav", return_value=True), \
         patch("server.server._has_voice", return_value=True), \
         patch("server.server._transcribe", return_value=transcript), \
         patch("server.server._run_agent") as runner:
        r = client.post("/turn", data={
            "audio": (io.BytesIO(audio_bytes), "sample.wav"),
            "username": "alice",
            "hint": "chat",
        }, content_type="multipart/form-data")

    assert r.status_code == 200
    body = r.get_json()
    assert body["active_agent"] == "silence"
    assert body["user_input"] == ""
    assert not runner.called


def test_hallucination_filter_rejects_short_world_fragment():
    assert _looks_like_hallucination("world right now")
    assert not _looks_like_hallucination("tell me about AI")
