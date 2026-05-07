"""Phase 5 tests for the CS Navigator HTTP proxy tool.

The `server.tools.cs_navigator` module may not be merged into this worktree yet,
so each test guards with `pytest.importorskip`. That way `pytest --collect-only`
always passes; once the module lands the tests light up.

We use `httpx.MockTransport` so no real network calls leave the box. The CS
Navigator backend exposes:
  POST /chat/guest      (no auth)              when CS_NAVIGATOR_TOKEN is empty
  POST /chat/stream     (Authorization: Bearer) when CS_NAVIGATOR_TOKEN is set

The tool returns the assembled assistant text from the streaming reply, fail-soft
on errors with a string containing "couldn't reach" so the chatbot agent can
apologize naturally instead of crashing the run.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Iterable

import pytest


# ─── Helpers ────────────────────────────────────────────────────────────────

def _run(coro):
    """Tiny sync wrapper that always uses a fresh loop — keeps tests deterministic."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sse_body(chunks: Iterable[str]) -> bytes:
    """Encode chunks as a Server-Sent-Events stream the way the CS Navigator
    backend emits them: one `data: {...}\\n\\n` event per token chunk, then
    a final `data: [DONE]\\n\\n`. Tools that read either the SSE format or a
    plain newline-delimited JSON stream should both pass these tests, so we
    also expose a JSONL fallback below.
    """
    parts = []
    for c in chunks:
        parts.append(f"data: {json.dumps({'delta': c})}\n\n".encode())
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def _jsonl_body(chunks: Iterable[str]) -> bytes:
    """Newline-delimited JSON: `{"delta": "..."}\\n` per line."""
    return b"".join(
        (json.dumps({"delta": c}) + "\n").encode() for c in chunks
    )


def _streaming_body(chunks: Iterable[str]) -> bytes:
    """Body the tool can parse however it likes. We send SSE since the real
    Cloud Run backend uses SSE; the tool implementation should handle it.
    """
    return _sse_body(chunks)


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def fake_ctx():
    """A minimal RunContext-shaped dict that the tool can hash for session_id.
    Real runtime hands the tool a `RunContextWrapper`; tests pass a dict and
    the tool's `_enqueue`-style guard normalizes."""
    return {"username": "aayush", "session_id": "abc123", "actions_queue": []}


# ─── 1. Guest endpoint when no token ─────────────────────────────────────────

def test_guest_endpoint_used_when_no_token(monkeypatch):
    cs_navigator = pytest.importorskip("server.tools.cs_navigator")
    httpx = pytest.importorskip("httpx")

    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_TOKEN", "", raising=False)
    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_URL",
                        "https://cs-chatbot.example.com", raising=False)

    captured: dict[str, Any] = {}

    def handler(request: "httpx.Request") -> "httpx.Response":
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, content=_streaming_body(["ok"]))

    monkeypatch.setattr(
        cs_navigator,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
        raising=False,
    )

    ctx = {"username": "aayush", "actions_queue": []}
    out = _run(cs_navigator._search_impl(ctx, "what's the cs department phone?"))

    assert "/chat/guest" in captured["url"]
    assert "/chat/stream" not in captured["url"]
    # Guest endpoint should not send Authorization
    auth = captured["headers"].get("authorization", "")
    assert not auth.lower().startswith("bearer ")
    assert isinstance(out, str)


# ─── 2. Stream endpoint + bearer header when token set ───────────────────────

def test_stream_endpoint_used_when_token_set(monkeypatch):
    cs_navigator = pytest.importorskip("server.tools.cs_navigator")
    httpx = pytest.importorskip("httpx")

    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_TOKEN",
                        "secret-token-xyz", raising=False)
    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_URL",
                        "https://cs-chatbot.example.com", raising=False)

    captured: dict[str, Any] = {}

    def handler(request: "httpx.Request") -> "httpx.Response":
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, content=_streaming_body(["fine"]))

    monkeypatch.setattr(
        cs_navigator,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
        raising=False,
    )

    ctx = {"username": "aayush", "actions_queue": []}
    _run(cs_navigator._search_impl(ctx, "graduation requirements?"))

    assert "/chat/stream" in captured["url"]
    assert captured["headers"].get("authorization", "") == "Bearer secret-token-xyz"


# ─── 3. Streaming chunks assemble into final string ──────────────────────────

def test_returns_assembled_text_from_streaming_response(monkeypatch):
    cs_navigator = pytest.importorskip("server.tools.cs_navigator")
    httpx = pytest.importorskip("httpx")

    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_TOKEN", "", raising=False)
    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_URL",
                        "https://cs-chatbot.example.com", raising=False)

    def handler(request: "httpx.Request") -> "httpx.Response":
        return httpx.Response(200, content=_streaming_body(["Hello", " ", "world"]))

    monkeypatch.setattr(
        cs_navigator,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
        raising=False,
    )

    ctx = {"username": "aayush", "actions_queue": []}
    out = _run(cs_navigator._search_impl(ctx, "say hi"))
    assert out == "Hello world"


# ─── 4. Fail-soft on timeout ─────────────────────────────────────────────────

def test_fail_soft_on_timeout(monkeypatch):
    cs_navigator = pytest.importorskip("server.tools.cs_navigator")
    httpx = pytest.importorskip("httpx")

    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_TOKEN", "", raising=False)
    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_URL",
                        "https://cs-chatbot.example.com", raising=False)

    def handler(request: "httpx.Request") -> "httpx.Response":
        raise httpx.TimeoutException("timeout!", request=request)

    monkeypatch.setattr(
        cs_navigator,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
        raising=False,
    )

    ctx = {"username": "aayush", "actions_queue": []}
    out = _run(cs_navigator._search_impl(ctx, "anything"))
    assert isinstance(out, str)
    assert "couldn't reach" in out.lower()


# ─── 5. Fail-soft on 5xx ─────────────────────────────────────────────────────

def test_fail_soft_on_5xx(monkeypatch):
    cs_navigator = pytest.importorskip("server.tools.cs_navigator")
    httpx = pytest.importorskip("httpx")

    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_TOKEN", "", raising=False)
    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_URL",
                        "https://cs-chatbot.example.com", raising=False)

    def handler(request: "httpx.Request") -> "httpx.Response":
        return httpx.Response(500, content=b"internal error")

    monkeypatch.setattr(
        cs_navigator,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
        raising=False,
    )

    ctx = {"username": "aayush", "actions_queue": []}
    out = _run(cs_navigator._search_impl(ctx, "anything"))
    assert isinstance(out, str)
    assert "couldn't reach" in out.lower()


# ─── 6. Same RunContext → stable session_id ─────────────────────────────────

def test_session_id_stable_across_calls_same_user(monkeypatch):
    cs_navigator = pytest.importorskip("server.tools.cs_navigator")
    httpx = pytest.importorskip("httpx")

    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_TOKEN", "", raising=False)
    monkeypatch.setattr(cs_navigator.config, "CS_NAVIGATOR_URL",
                        "https://cs-chatbot.example.com", raising=False)

    seen_session_ids: list[str] = []

    def handler(request: "httpx.Request") -> "httpx.Response":
        try:
            body = json.loads(request.content.decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        sid = body.get("session_id") or request.headers.get("x-session-id", "")
        seen_session_ids.append(sid)
        return httpx.Response(200, content=_streaming_body(["ok"]))

    monkeypatch.setattr(
        cs_navigator,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
        raising=False,
    )

    ctx = {"username": "aayush", "actions_queue": []}
    _run(cs_navigator._search_impl(ctx, "first call"))
    _run(cs_navigator._search_impl(ctx, "second call"))

    assert len(seen_session_ids) == 2
    assert seen_session_ids[0] == seen_session_ids[1]
    assert seen_session_ids[0]  # non-empty


# ─── 7. Chatbot agent re-wired to cs_navigator_search ────────────────────────

def test_chatbot_agent_tool_list_uses_cs_navigator():
    """After the rewire, the chatbot agent should list `cs_navigator_search`
    among its tools. Skip cleanly if either module isn't merged yet."""
    pytest.importorskip("server.tools.cs_navigator")
    chatbot = pytest.importorskip("server.agents.chatbot")

    tool_names = {getattr(t, "name", getattr(t, "__name__", "")) for t in chatbot.chatbot_agent.tools}
    assert "cs_navigator_search" in tool_names, (
        f"chatbot_agent.tools should include cs_navigator_search, got: {tool_names}"
    )
