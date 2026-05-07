"""CS Navigator — thin HTTP proxy onto the operator's deployed Morgan-CS RAG.

This tool replaces the bundled Pinecone/Vertex RAG with a single call out to
the operator's already-deployed Cloud Run FastAPI ("CS Navigator").  All
ingestion, embeddings, citation logic, and the actual answer-shaping live on
the Cloud Run side; we just relay the user query and stream-assemble the
response back into one string the chatbot agent can speak.

================================================================================
Upstream contract (verified against /Users/theaayushstha/Projects/cs chatbot/
cs-chatbot/backend/main.py at lines 2380, 2585, 2815)
================================================================================

POST /chat                  — auth required, JSON in / JSON out
POST /chat/stream           — auth required, JSON in / SSE out (text/event-stream)
POST /chat/guest            — NO auth, JSON in / JSON out (rate-limited per IP)

Pydantic request models (main.py:576 and main.py:589):

    class QueryRequest(BaseModel):       # /chat and /chat/stream
        query: str                       # NOTE: the field is `query`, not `message`
        session_id: str = "default"
        skip_cache: bool = False
        model: str = ""                  # "" | "inav-1.0" | "inav-1.1"

    class GuestQueryRequest(BaseModel):  # /chat/guest
        query: str
        guestProfile: Optional[dict] = None

Response shapes:

    /chat            -> {"response": "<full assistant answer>"}
    /chat/guest      -> {"response": "<answer>", "cached"?: bool}
    /chat/stream     -> Server-Sent Events, one frame per `\\n\\n`-terminated
                        line.  Each frame is `data: <json>\\n\\n`.  The JSON
                        carries a `type` and a `content` string.  Observed
                        types: "status", "chunk", "done", "error".
                        Final assembled text == concatenation of every
                        "chunk".content; the final "done" event repeats the
                        full string as `content` (canonical answer).

Endpoint selection in this tool:

    CS_NAVIGATOR_TOKEN == ""    -> POST {URL}/chat/guest          (anonymous demo)
    CS_NAVIGATOR_TOKEN != ""    -> POST {URL}/chat/stream         (auth, SSE)
                                   with header `Authorization: Bearer {TOKEN}`

Both paths return the assembled string directly to the agent.  CS Navigator
already returns a clean, NAO-friendly reply — we do NOT re-summarize, cite,
or post-process.

================================================================================
Failure mode (fail-soft, never raise)
================================================================================

`httpx.TimeoutException`, `httpx.RequestError`, or any 5xx response collapses
to one fixed sentence the chatbot agent reads aloud.  Anything else (4xx with
a JSON body, malformed SSE, etc.) also collapses to the same sentence so the
agent never sees a stack trace.

================================================================================
Latency metric
================================================================================

We try `metrics.phase_timer("cs_navigator_call")`.  As of Phase 1 the metrics
module hardcodes ALLOWED_PHASES and rejects unknown labels with ValueError;
Phase 5 has not extended that whitelist yet.  We catch ValueError and fall
back to a no-op contextmanager so the tool stays callable end-to-end.  When
the whitelist gets extended, this file needs no change.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from typing import Any, AsyncIterator, Iterator

import httpx

from agents import RunContextWrapper, function_tool

from server import config, metrics

logger = logging.getLogger("server.tools.cs_navigator")

# Fixed NAO-voice fallback for ANY upstream failure. Kept short so TTS doesn't
# stall the demo, and friendly enough that the user feels the robot is "there"
# even when the brain is unreachable.
_FALLBACK_REPLY = (
    "I couldn't reach the CS Navigator just now — give me a moment and try again."
)


# ────────────────────────── helpers ──────────────────────────

def _unwrap_context(ctx: Any) -> dict:
    """Extract the raw context dict from either a RunContextWrapper or a bare dict."""
    if isinstance(ctx, RunContextWrapper):
        inner = ctx.context
    else:
        inner = ctx
    if isinstance(inner, dict):
        return inner
    return {}


def _session_id_for(ctx: Any) -> str:
    """Stable, short session id derived from the user identifier on the run context.

    CS Navigator stores chat history per `(user_id, session_id)`. We don't have
    a user_id (the chatbot agent runs anonymously), so we hash the username on
    the run context and prefix it with `nao_` so logs on the CS-Navigator side
    stay distinguishable from real students.

    Falls back to ``nao_default`` when no username is present (e.g. unit tests
    that pass a bare dict).
    """
    store = _unwrap_context(ctx)
    raw = str(store.get("username") or "default")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"nao_{digest}"


@contextlib.contextmanager
def _safe_phase_timer(label: str) -> Iterator[None]:
    """Wrap `metrics.phase_timer` with a defensive fallback.

    `metrics.phase_timer` rejects labels not in `ALLOWED_PHASES` by raising
    ValueError. Phase 5 introduces a new label that hasn't been added to the
    whitelist yet — we catch the ValueError and fall through to a no-op so
    the tool keeps working until the metric is officially adopted.
    """
    try:
        with metrics.phase_timer(label):
            yield
        return
    except ValueError:
        # Label not yet whitelisted in metrics.ALLOWED_PHASES. Run uninstrumented.
        yield
    except Exception:
        # Any other metrics-side failure (registry collision, etc.) shouldn't
        # take down the tool. Log once at debug and keep going.
        logger.debug("phase_timer setup failed for %s; running uninstrumented", label)
        yield


def _is_5xx(status: int) -> bool:
    return 500 <= status < 600


# ────────────────────── HTTP / SSE plumbing ──────────────────────

def _build_request(query: str, session_id: str) -> tuple[str, dict, dict, bool]:
    """Decide the endpoint, headers, and JSON body for one upstream call.

    Returns ``(url, headers, body, expect_sse)``. When `CS_NAVIGATOR_URL` is
    blank, callers will short-circuit to the fallback reply — but we still
    return a sane tuple for the no-url case so tests can monkeypatch httpx
    against it without env vars.
    """
    base = (config.CS_NAVIGATOR_URL or "").rstrip("/")
    token = (config.CS_NAVIGATOR_TOKEN or "").strip()

    if token:
        url = f"{base}/chat/stream"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        # /chat/stream still uses QueryRequest. session_id is ours; skip_cache
        # stays default (False) so repeated demo questions hit the cache.
        body = {"query": query, "session_id": session_id}
        expect_sse = True
    else:
        url = f"{base}/chat/guest"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # GuestQueryRequest only takes `query` (and optional guestProfile);
        # session_id is not part of the model, so we drop it.
        body = {"query": query}
        expect_sse = False

    return url, headers, body, expect_sse


async def _read_sse_response(resp: httpx.Response) -> str:
    """Concatenate `chunk.content` events from an SSE stream into one string.

    The CS Navigator stream emits frames like ``data: {"type": "...", "content":
    "..."}\\n\\n``. We:
      * accumulate every ``type=="chunk"`` content into ``buf``
      * remember the final ``type=="done"`` content as ``done_content``
      * surface ``type=="error"`` content immediately (rare, treated as the body)

    The canonical reply is the ``done`` event when present, falling back to the
    accumulated chunks when the stream terminates without a done frame.
    """
    chunks: list[str] = []
    done_content: str | None = None
    error_content: str | None = None

    async for line in resp.aiter_lines():
        if not line:
            continue
        if line.startswith(":"):
            # SSE comment / heartbeat
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            # Malformed frame — skip and keep reading; the whole answer
            # rarely depends on a single frame.
            continue
        ev_type = event.get("type")
        ev_content = event.get("content", "")
        if ev_type == "chunk":
            chunks.append(str(ev_content))
        elif ev_type == "done":
            done_content = str(ev_content)
        elif ev_type == "error":
            error_content = str(ev_content)
            break

    if error_content:
        # Upstream told us it failed mid-stream. Treat as a soft failure.
        raise RuntimeError(f"CS Navigator upstream error: {error_content[:200]}")

    if done_content:
        return done_content
    return "".join(chunks)


def _read_json_response(resp: httpx.Response) -> str:
    """Pull `response` (or `detail`) out of a /chat-style JSON body."""
    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        return ""
    if isinstance(body, dict):
        if "response" in body and isinstance(body["response"], str):
            return body["response"]
        # /chat/guest returns 429 with {"detail": "..."} — surface that as the
        # answer text so the user gets context about the rate limit.
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
    return ""


# ─────────────────────────── core impl ───────────────────────────

async def _cs_navigator_search_impl(ctx: Any, query: str) -> str:
    """The real implementation. Kept module-level so tests can call it directly
    without going through the Agents SDK function-tool wrapper.
    """
    query = (query or "").strip()
    if not query:
        return _FALLBACK_REPLY

    if not config.CS_NAVIGATOR_URL:
        # Misconfigured deployment — surface the same NAO-voice apology rather
        # than crashing the chatbot agent.
        logger.warning("CS_NAVIGATOR_URL is empty; returning fallback reply")
        return _FALLBACK_REPLY

    session_id = _session_id_for(ctx)
    url, headers, body, expect_sse = _build_request(query, session_id)
    timeout = config.CS_NAVIGATOR_TIMEOUT_S

    try:
        with _safe_phase_timer("cs_navigator_call"):
            async with httpx.AsyncClient(timeout=timeout) as client:
                if expect_sse:
                    async with client.stream(
                        "POST", url, headers=headers, json=body
                    ) as resp:
                        if _is_5xx(resp.status_code):
                            logger.warning(
                                "CS Navigator 5xx on /chat/stream: %s", resp.status_code
                            )
                            return _FALLBACK_REPLY
                        if resp.status_code >= 400:
                            # 4xx — read body for the message but still degrade
                            # to the fallback so the agent doesn't relay a raw
                            # auth/validation error to the user.
                            await resp.aread()
                            logger.warning(
                                "CS Navigator 4xx on /chat/stream: %s",
                                resp.status_code,
                            )
                            return _FALLBACK_REPLY
                        text = await _read_sse_response(resp)
                else:
                    resp = await client.post(url, headers=headers, json=body)
                    if _is_5xx(resp.status_code):
                        logger.warning(
                            "CS Navigator 5xx on /chat/guest: %s", resp.status_code
                        )
                        return _FALLBACK_REPLY
                    if resp.status_code >= 400:
                        # /chat/guest emits 429 (rate-limited) and 401 (auth)
                        # with a JSON `detail`. Pass that detail through so
                        # the user understands they hit a rate limit, but
                        # still keep it NAO-voice short.
                        text = _read_json_response(resp) or _FALLBACK_REPLY
                        return text
                    text = _read_json_response(resp)
    except httpx.TimeoutException as e:
        logger.warning("CS Navigator timeout (%ss): %s", timeout, e)
        return _FALLBACK_REPLY
    except httpx.RequestError as e:
        logger.warning("CS Navigator request error: %s", e)
        return _FALLBACK_REPLY
    except RuntimeError as e:
        # Raised by _read_sse_response when an `error` SSE frame arrives.
        logger.warning("CS Navigator stream error: %s", e)
        return _FALLBACK_REPLY
    except Exception as e:  # noqa: BLE001 — last line of defense
        logger.warning("CS Navigator unexpected error: %s: %s", type(e).__name__, e)
        return _FALLBACK_REPLY

    text = (text or "").strip()
    if not text:
        return _FALLBACK_REPLY
    return text


@function_tool
async def cs_navigator_search(ctx: RunContextWrapper, query: str) -> str:
    """Look up Morgan State CS information via the deployed CS Navigator API.

    Use for any question about Morgan's CS curriculum, faculty, courses,
    schedule, advising, deadlines, or graduation requirements.

    The result is the assistant's full answer text (already RAG-enriched on the
    Cloud Run side). Do NOT cite raw chunks — CS Navigator returns a clean
    reply ready to be spoken in NAO's voice.
    """
    return await _cs_navigator_search_impl(ctx, query)


__all__ = [
    "cs_navigator_search",
    "_cs_navigator_search_impl",
    "_FALLBACK_REPLY",
]


# ─────────────────────────── self-check ───────────────────────────

if __name__ == "__main__":
    """Self-test: monkeypatch httpx with a fake transport and assert the
    assembled text matches what we feed the SSE stream.

    Covers three paths:
      1. Guest JSON path (no token) -> {"response": "..."}
      2. Streaming SSE path (with token) -> chunked answer reconstruction
      3. Fallback path on httpx.TimeoutException
    """
    import os
    import sys

    # We need to re-stamp config because the module-level imports above already
    # captured the (likely empty) values from the real environment.
    os.environ["CS_NAVIGATOR_URL"] = "https://cs-navigator-test.invalid"

    # Force a re-read so config picks up the env override for this self-test.
    import importlib

    importlib.reload(config)

    # --- 1. Guest path (no token, JSON response) ------------------------------
    os.environ["CS_NAVIGATOR_TOKEN"] = ""
    importlib.reload(config)

    expected_guest = "CS 491 is the senior capstone for Morgan State CS majors."

    def _guest_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/guest", request.url.path
        body = json.loads(request.content.decode("utf-8"))
        assert body == {"query": "what is CS 491?"}, body
        return httpx.Response(200, json={"response": expected_guest})

    transport = httpx.MockTransport(_guest_handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    real_async_client = httpx.AsyncClient
    httpx.AsyncClient = _PatchedAsyncClient  # type: ignore[misc]
    try:
        ctx = {"username": "demo_user"}
        result = asyncio.run(_cs_navigator_search_impl(ctx, "what is CS 491?"))
        assert result == expected_guest, f"guest path: {result!r}"
        print("OK guest path:", result)
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc]

    # --- 2. Stream path (token set, SSE response) -----------------------------
    os.environ["CS_NAVIGATOR_TOKEN"] = "test-bearer-token"
    importlib.reload(config)

    sse_chunks = ["CS 491 ", "is the senior ", "capstone."]
    sse_done = "CS 491 is the senior capstone."

    def _stream_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/stream", request.url.path
        assert request.headers["Authorization"] == "Bearer test-bearer-token"
        assert request.headers["Accept"] == "text/event-stream"
        body = json.loads(request.content.decode("utf-8"))
        assert body["query"] == "what is CS 491?"
        assert body["session_id"].startswith("nao_"), body["session_id"]
        # Build SSE bytes
        frames = []
        frames.append(f"data: {json.dumps({'type':'status','content':'thinking'})}\n\n")
        for ch in sse_chunks:
            frames.append(f"data: {json.dumps({'type':'chunk','content':ch})}\n\n")
        frames.append(f"data: {json.dumps({'type':'done','content':sse_done})}\n\n")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(frames).encode("utf-8"),
        )

    stream_transport = httpx.MockTransport(_stream_handler)

    class _StreamingClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = stream_transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = _StreamingClient  # type: ignore[misc]
    try:
        ctx = {"username": "demo_user"}
        result = asyncio.run(_cs_navigator_search_impl(ctx, "what is CS 491?"))
        assert result == sse_done, f"stream path: {result!r}"
        print("OK stream path:", result)
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc]

    # --- 3. Fallback on TimeoutException --------------------------------------
    os.environ["CS_NAVIGATOR_TOKEN"] = ""
    importlib.reload(config)

    def _timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    timeout_transport = httpx.MockTransport(_timeout_handler)

    class _TimeoutClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = timeout_transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = _TimeoutClient  # type: ignore[misc]
    try:
        ctx = {"username": "demo_user"}
        result = asyncio.run(_cs_navigator_search_impl(ctx, "anything"))
        assert result == _FALLBACK_REPLY, f"timeout path: {result!r}"
        print("OK timeout path:", result)
    finally:
        httpx.AsyncClient = real_async_client  # type: ignore[misc]

    # --- 4. session_id stability + uniqueness ---------------------------------
    sid_a = _session_id_for({"username": "alice"})
    sid_b = _session_id_for({"username": "alice"})
    sid_c = _session_id_for({"username": "bob"})
    assert sid_a == sid_b and sid_a != sid_c, (sid_a, sid_b, sid_c)
    print("OK session_id stable+unique:", sid_a, "vs", sid_c)

    print("ALL OK")
    sys.exit(0)
