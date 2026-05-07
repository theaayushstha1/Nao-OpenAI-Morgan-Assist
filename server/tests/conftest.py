import os
import tempfile
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
# Force OPEN auth mode for the default suite. The real value lives in .env
# and would otherwise 401 every test that doesn't send X-NAO-Secret.
# test_security.py monkeypatches the config attribute when it needs auth on.
os.environ["NAO_SHARED_SECRET"] = ""
# Tempfile rather than ":memory:" because memory.py opens a new sqlite
# connection per call; ":memory:" gives each connection its own empty DB
# and FK inserts across helpers fail.
_db_fd, _db_path = tempfile.mkstemp(suffix=".db", prefix="nao-test-")
os.close(_db_fd)
os.environ.setdefault("SESSION_DB", _db_path)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 WebSocket-test fixtures (added by the `tests` agent).
# These are reused by test_ws_smoke.py and test_echo_regression.py. They are
# intentionally inert for the rest of the suite — nothing imports them
# implicitly, so existing tests are unaffected.
# ─────────────────────────────────────────────────────────────────────────────

import pytest


# A tiny but valid-looking MP3 header followed by zero-padded frame bytes.
# 0xFF 0xFB 0x90 0x00 is a real MPEG-1 Layer 3 frame header — sufficient for
# any code that just sniffs the magic bytes. The rest is filler so total
# length matches what a short TTS clip would feel like to a test consumer.
_FAKE_MP3_BYTES = b"\xff\xfb\x90\x00" + b"\x00" * 96


@pytest.fixture
def fake_mp3_bytes() -> bytes:
    """Stable MP3-shaped bytes used by mock TTS fixtures across WS tests."""
    return _FAKE_MP3_BYTES


@pytest.fixture
def mock_openai_tts(monkeypatch, fake_mp3_bytes):
    """Replace `server.openai_tts.synthesize` with a deterministic stub.

    Returns the same bytes every call so tests can assert exact equality on
    the audio payload that flows back over the WS. Patches both the canonical
    module and any new-WS-app re-export site (best-effort, raising=False).
    """
    from server import openai_tts

    def _fake_synth(text: str) -> bytes:
        # Mimic the real signature: short empty input -> None, otherwise bytes.
        if not text or not str(text).strip():
            return None
        return fake_mp3_bytes

    monkeypatch.setattr(openai_tts, "synthesize", _fake_synth)
    # If the new WS app does `from server.openai_tts import synthesize`, the
    # name is bound at import time. Patch the binding too, ignoring missing.
    try:
        import server.app_ws as app_ws  # noqa: F401
        monkeypatch.setattr("server.app_ws.synthesize", _fake_synth, raising=False)
    except Exception:
        pass
    return _fake_synth


@pytest.fixture
def mock_agent_runner(monkeypatch):
    """Factory fixture: returns a callable that wires a fixed (reply, actions).

    Usage:
        mock_agent_runner("hello world", [{"name": "wave_hand", "args": {}}])

    The factory patches every plausible runner entry point so whichever the
    new `server/app_ws.py` chose still flows through the mock. We patch:
      - `server.server._run_agent` (existing helper; new app may import it)
      - `server.app_ws._run_agent` (if the new app defined its own)
      - `server.app_ws.Runner.run` (if it calls the SDK directly)

    All `setattr` calls use `raising=False` so a missing target is a no-op.
    """
    def _install(reply_text: str, actions_list: list[dict]):
        actions_copy = list(actions_list or [])

        def _fake_run_agent(username, hint, transcript, image_b64):
            return (reply_text, "chat", list(actions_copy), False)

        # Existing helper in the legacy server module — patch unconditionally
        # because the new WS app may import it directly.
        try:
            from server import server as _legacy
            monkeypatch.setattr(_legacy, "_run_agent", _fake_run_agent, raising=False)
        except Exception:
            pass

        # New WS app — best-effort. Skip silently if not present yet.
        try:
            import server.app_ws as _ws  # noqa: F401
            monkeypatch.setattr("server.app_ws._run_agent", _fake_run_agent, raising=False)
        except Exception:
            pass

        # Agents-SDK Runner.run — last-line interception in case the new app
        # bypasses the helpers and calls Runner directly.
        try:
            from agents import Runner

            class _FakeResult:
                def __init__(self, text):
                    self.final_output = text
                    self.actions_queue = list(actions_copy)

                def final_output_as(self, _typ):
                    return reply_text

            async def _fake_run(agent, message, **kwargs):
                ctx = kwargs.get("context") or {}
                if isinstance(ctx, dict):
                    queue = ctx.get("actions_queue")
                    if isinstance(queue, list):
                        queue.extend(actions_copy)
                return _FakeResult(reply_text)

            monkeypatch.setattr(Runner, "run", _fake_run, raising=False)
        except Exception:
            pass

        return _fake_run_agent

    return _install


@pytest.fixture
def ws_client(monkeypatch):
    """Yield a FastAPI TestClient wired to `server.app_ws.app` if available.

    Skips the test when `server.app_ws` doesn't exist yet — that file is
    owned by the `fastapi-app` agent and may land in a separate worktree.
    Pre-populates the auth header onto the client default headers so the
    caller doesn't have to repeat it.
    """
    pytest.importorskip("server.app_ws")
    from fastapi.testclient import TestClient
    from server import app_ws

    # Force OPEN auth in the WS test context so `X-NAO-Secret` is optional.
    # The shared-secret enforcement is exercised in test_security.py.
    from server import config as _cfg
    monkeypatch.setattr(_cfg, "NAO_SHARED_SECRET", "", raising=False)
    monkeypatch.setattr(app_ws, "NAO_SHARED_SECRET", "", raising=False)

    client = TestClient(app_ws.app, headers={"X-NAO-Secret": ""})
    try:
        yield client
    finally:
        client.close()
