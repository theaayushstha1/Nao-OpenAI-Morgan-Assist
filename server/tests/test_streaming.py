from server.streaming import iter_sentences


def test_splits_on_period():
    chunks = ["Hel", "lo there", ". How are you", "?"]
    out = list(iter_sentences(iter(chunks)))
    assert out == ["Hello there.", "How are you?"]


def test_preserves_abbreviations():
    chunks = ["Dr. Wang is great. See you."]
    out = list(iter_sentences(iter(chunks)))
    assert out == ["Dr. Wang is great.", "See you."]


def test_flushes_trailing_without_terminator():
    chunks = ["no terminator"]
    out = list(iter_sentences(iter(chunks)))
    assert out == ["no terminator"]


import io
from unittest.mock import patch, MagicMock
import pytest
from server.server import app


@pytest.fixture
def streaming_client():
    app.config["TESTING"] = True
    return app.test_client()


def test_stream_turn_crisis_path(streaming_client):
    fake_wav = open("server/tests/fixtures/sample.wav", "rb").read()
    with patch("server.server._transcribe", return_value="i want to kill myself"):
        r = streaming_client.post("/stream_turn", data={
            "audio": (io.BytesIO(fake_wav), "sample.wav"),
            "username": "test",
        }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    body = r.get_data(as_text=True)
    assert "\"crisis\": true" in body
    assert "988" in body
