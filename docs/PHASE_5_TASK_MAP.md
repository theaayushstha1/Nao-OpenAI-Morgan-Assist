# Phase 5 — Task Map & Contracts

> **CS Navigator Integration.** Replace Pinecone with the operator's existing deployed Cloud Run FastAPI at `/Users/theaayushstha/Projects/cs chatbot/cs-chatbot`. The CS Navigator backend exposes `POST /chat`, `POST /chat/stream` (auth), and `POST /chat/guest` (no-auth). We delete the Pinecone tool, add a thin streaming HTTP proxy tool, rewire the chatbot agent.

PRD: PRD_v2.md Phase 5.

## Branch policy
Worktree per agent off `dev/architecture-rework`. Commit message: `[Phase 5] <slug>: <summary>`. Don't touch `requirements.txt` — declare deps in commit message.

## File ownership

| Slug | Files OWNED |
|------|-------------|
| `cs-navigator-tool` | `server/tools/cs_navigator.py` (NEW), `server/config.py` (extend env vars only) |
| `chatbot-rewire` | `server/agents/chatbot.py` (rewire to call cs_navigator_search instead of pinecone_search), `server/tools/pinecone_search.py` (mark as deprecated; do NOT delete yet — keep for fallback under feature flag for one phase) |
| `cs-nav-tests` | `server/tests/test_cs_navigator.py` (NEW) |

## Public APIs

### `server/tools/cs_navigator.py`
```python
@function_tool
async def cs_navigator_search(ctx: RunContextWrapper, query: str) -> str:
    """Look up Morgan State CS information via the deployed CS Navigator API.
    Use for any question about Morgan's CS curriculum, faculty, courses, schedule,
    advising, deadlines, or graduation requirements.

    The result is the assistant's full answer text (already RAG-enriched on the
    Cloud Run side). Do NOT cite raw chunks — CS Navigator returns a clean reply.
    """
```

Implementation:
- Read `CS_NAVIGATOR_URL` (Cloud Run base, e.g. `https://cs-chatbot-xxx-uc.a.run.app`) and `CS_NAVIGATOR_TOKEN` (optional bearer; when empty use `/chat/guest`).
- HTTP POST to `{URL}/chat/guest` (no token) or `{URL}/chat/stream` (with `Authorization: Bearer {TOKEN}`).
- Request body: `{ "message": query, "session_id": "<short hash>", "stream": False }` — adapt to whatever the actual CS Navigator route expects (you'll inspect the source at `/Users/theaayushstha/Projects/cs chatbot/cs-chatbot/backend/main.py` lines 2380, 2585, 2815 — those are the routes; read the request models).
- Streaming response: assemble full text, return as the tool result. (Future phase can pipe deltas to TTS — for Phase 5 just collect the full reply.)
- 30 s timeout; on failure log + return `f"CS Navigator unavailable: {error_short}"` (fail-soft so the chatbot agent can apologize gracefully).
- Time the call via `metrics.phase_timer("cs_navigator_call")` (defensive fallback).

### `server/agents/chatbot.py`
- Replace `pinecone_search` import with `cs_navigator_search`.
- Update tool list: `tools=[cs_navigator_search]` (only this one — CS Navigator is the comprehensive Morgan brain).
- Update instructions: emphasize that for ANY Morgan-CS question, call `cs_navigator_search(query)` first, then summarize the response in NAO's voice (warm, brief, conversational).

### `server/config.py` additions
```python
CS_NAVIGATOR_URL = os.environ.get("CS_NAVIGATOR_URL", "")
CS_NAVIGATOR_TOKEN = os.environ.get("CS_NAVIGATOR_TOKEN", "")
CS_NAVIGATOR_TIMEOUT_S = float(os.environ.get("CS_NAVIGATOR_TIMEOUT_S", "30"))
```

Add to `.env.example` (one line) — `cs-navigator-tool` agent owns this.

## Reused-as-is
- All other agents/tools.

## Latency phase labels (additions)
- `cs_navigator_call` — total time for the HTTP request + response assembly.

## Definition of done
1. `python -m py_compile` succeeds on all touched files.
2. `cs_navigator_search` works against a mocked CS Navigator response.
3. Chatbot agent compiles and tool list is correct.
4. Tests collect cleanly.
5. `requirements.txt` not touched (declare httpx is already there from Phase 1).
