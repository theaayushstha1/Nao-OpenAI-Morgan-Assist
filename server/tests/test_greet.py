import io
from unittest.mock import patch
import pytest
from server.server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_greet_streams_greeting(client):
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    with patch("server.server._generate_greeting",
               return_value=iter(["Hey Alice!", "How's the week been?"])):
        r = client.post("/greet", data={
            "image": (io.BytesIO(fake_jpeg), "face.jpg"),
            "username": "alice",
        }, content_type="multipart/form-data")
        assert r.status_code == 200
        assert r.mimetype == "text/event-stream"
        body = r.get_data(as_text=True)
    assert "Hey Alice!" in body
    assert "alice" in body


def test_greet_with_proactive_disabled(client, monkeypatch):
    from server import session as s
    monkeypatch.setattr(s, "get_proactive_enabled", lambda u: False)
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    r = client.post("/greet", data={
        "image": (io.BytesIO(fake_jpeg), "face.jpg"),
        "username": "alice",
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "skipped" in body.lower()


def test_greet_missing_image_returns_400(client):
    r = client.post("/greet", data={"username": "alice"},
                    content_type="multipart/form-data")
    assert r.status_code == 400
