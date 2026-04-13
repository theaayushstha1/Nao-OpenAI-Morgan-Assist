# Nao-OpenAI-Morgan-Assist

## Project Overview

NAO humanoid robot assistant for Morgan State University, built on the **OpenAI Agents SDK** with multi-agent routing (router + chat, chatbot, skills, therapist with CBT/grounding sub-agents). Integrates Whisper (STT), GPT-4o (chat + vision), and Pinecone (RAG). Multimodal emotion detection via GPT-4o vision.

## Architecture

```
NAO Robot (Python 2.7 / naoqi SDK)
  main.py           -> Wake loop
  wake_listener.py  -> Wake phrase + optional hint (chat|morgan|therapy|skills)
  conversation.py   -> ONE loop: record audio + snap JPEG -> POST /turn -> speak + execute

Flask Server (Python 3.11+ / server/)
  server/server.py        Single POST /turn endpoint
  server/safety.py        Pre-dispatch crisis gate (keyword + LLM, 988 hotline)
  server/session.py       SQLiteSession wrapper + camera consent + therapy recaps
  server/agents/          Agent graph (router, chat, chatbot, skills, therapist, cbt_coach, grounding_coach)
  server/tools/           Tool modules (nao_actions, pinecone_search, emotion, skills_tools)
```

## Key Files

### NAO side (Python 2.7)
| File | Purpose |
|------|---------|
| `main.py` | Entry; wake -> conversation loop |
| `wake_listener.py` | Wake phrase detection + `extract_hint()` |
| `conversation.py` | Single mode loop (record, POST, speak, dispatch actions) |
| `audio_handler.py` | Mic recording with VAD |
| `processing_announcer.py` | Background "please wait" speaker |
| `config.py` | Env-driven NAO IP/SERVER IP |
| `utils/camera_capture.py` | JPEG capture including `snap_quick()` for per-turn vision |
| `utils/nao_execute.py` | Dispatches `{name, args}` actions from server to naoqi calls |
| `utils/face_naoqi.py` | Face recognition/learning via ALFaceDetection |
| `utils/ask_name_utils.py` | Ask user for name via audio round-trip |
| `utils/exit_detection.py` | Regex-based exit intent |
| `utils/name_utils.py` | Extract name from speech |
| `utils/speech.py` | Phrase pools + expressive TTS |

### Server side (Python 3.11+)
| File | Purpose |
|------|---------|
| `server/server.py` | Flask `POST /turn` + `GET /health` |
| `server/config.py` | Env config (models, Pinecone, IPs, SQLite path) |
| `server/safety.py` | `crisis_check()` + hardcoded 988 hotline reply |
| `server/session.py` | SQLiteSession + `{get,set}_camera_consent` + `{save,load}_recap` |
| `server/agents/router.py` | Triage agent with handoffs |
| `server/agents/chat.py` | General chat + NAO actions |
| `server/agents/chatbot.py` | Morgan CS RAG |
| `server/agents/skills.py` | Time/weather/timers/todos |
| `server/agents/therapist.py` | Empathetic + CBT/grounding handoffs |
| `server/agents/cbt_coach.py` | Thought record walker |
| `server/agents/grounding_coach.py` | 5-4-3-2-1, box breathing, body scan |
| `server/tools/nao_actions.py` | 18 NAO action tools (append to `actions_queue`) |
| `server/tools/pinecone_search.py` | RAG tool |
| `server/tools/emotion.py` | `observe_face`, `log_emotion`, `identify_distortion`, `suggest_reframe`, `set_camera_consent`, `recap_session` |
| `server/tools/skills_tools.py` | Utility tools |

## Development Guidelines

- **NAO-side** (`main.py`, `wake_listener.py`, `conversation.py`, `audio_handler.py`, `processing_announcer.py`, `utils/`): **Python 2.7 compatible**. `from __future__ import print_function`, `str.format()`, no f-strings, no type hints.
- **Server-side** (`server/`): **Python 3.11+**. Modern idioms fine.
- IPs/ports read from env or `config.py` — never hardcode.
- Agents SDK: `openai-agents>=0.0.5` (currently 0.13.6).
- `pytest.ini` at repo root pins rootdir so the SDK's `agents` module doesn't get shadowed by `server/agents/`.
- NAO action tools append `{name, args}` records to a context-scoped `actions_queue`; after `Runner.run()` returns, the queue is read out and sent to NAO in the response JSON. NAO-side `utils/nao_execute.py` dispatches them.
- Crisis gate runs **before** the agent sees the user message. Agent cannot override.
- Camera consent persists in `user_prefs` table; therapist tool `set_camera_consent` toggles it; NAO honors the `suppress_image` flag in responses.

## Obsidian Vault

Knowledge vault for this codebase at `~/Documents/Obsidian Vault/Nao-OpenAI-Morgan-Assist/wiki/`. Read `wiki/index.md` first for context. Pattern: `raw/` (immutable) + `wiki/` (LLM-maintained).
