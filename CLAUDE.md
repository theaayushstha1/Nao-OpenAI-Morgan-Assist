# Nao-OpenAI-Morgan-Assist

## Project Overview

NAO humanoid robot assistant for Morgan State University, built on the **OpenAI Agents SDK** with multi-agent routing (router + chat, chatbot, skills, therapist with CBT/grounding sub-agents). Integrates Whisper (STT), GPT-4o (chat + vision), and Pinecone (RAG). Multimodal emotion detection via GPT-4o vision.

## Repo Layout

```
Nao-OpenAI-Morgan-Assist/
├── nao/         NAO-side Python 2.7 code — copy this to the robot
├── server/      Python 3.11+ Flask server + Agents SDK graph
├── docs/        Design specs, implementation plans, reference docs
├── README.md
├── CLAUDE.md
├── LICENSE
└── pytest.ini
```

## Architecture

```
NAO Robot (Python 2.7 / naoqi SDK) — everything under nao/
  nao/main.py           -> Wake loop
  nao/wake_listener.py  -> Wake phrase + optional hint (chat|morgan|therapy|skills)
  nao/conversation.py   -> ONE loop: record audio + snap JPEG -> POST /turn -> speak + execute

Flask Server (Python 3.11+) — everything under server/
  server/server.py        Single POST /turn endpoint
  server/safety.py        Pre-dispatch crisis gate (keyword + LLM, 988 hotline)
  server/session.py       SQLiteSession wrapper + camera consent + therapy recaps
  server/agents/          Agent graph (router, chat, chatbot, skills, therapist, cbt_coach, grounding_coach)
  server/tools/           Tool modules (nao_actions, pinecone_search, emotion, skills_tools)
```

## Key Files

### NAO side (Python 2.7, `nao/`)
| File | Purpose |
|------|---------|
| `nao/main.py` | Entry; wake -> conversation loop |
| `nao/wake_listener.py` | Wake phrase detection + `extract_hint()` |
| `nao/conversation.py` | Single mode loop (record, POST, speak, dispatch actions) |
| `nao/audio_handler.py` | Mic recording with VAD |
| `nao/processing_announcer.py` | Background "please wait" speaker |
| `nao/config.py` | Env-driven NAO IP/SERVER IP |
| `nao/utils/camera_capture.py` | JPEG capture including `snap_quick()` for per-turn vision |
| `nao/utils/nao_execute.py` | Dispatches `{name, args}` actions from server to naoqi calls |
| `nao/utils/face_naoqi.py` | Face recognition/learning via ALFaceDetection |
| `nao/utils/ask_name_utils.py` | Ask user for name via audio round-trip |
| `nao/utils/exit_detection.py` | Regex-based exit intent |
| `nao/utils/name_utils.py` | Extract name from speech |
| `nao/utils/speech.py` | Phrase pools + expressive TTS |

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

- **NAO-side** (everything in `nao/`): **Python 2.7 compatible**. `from __future__ import print_function`, `str.format()`, no f-strings, no type hints. On the robot, copy `nao/` contents to `/home/nao/nao_assist/` and run `python /home/nao/nao_assist/main.py`.
- **Server-side** (`server/`): **Python 3.11+**. Modern idioms fine.
- IPs/ports read from env or `config.py` — never hardcode.
- Agents SDK: `openai-agents>=0.0.5` (currently 0.13.6).
- `pytest.ini` at repo root pins rootdir so the SDK's `agents` module doesn't get shadowed by `server/agents/`.
- NAO action tools append `{name, args}` records to a context-scoped `actions_queue`; after `Runner.run()` returns, the queue is read out and sent to NAO in the response JSON. NAO-side `utils/nao_execute.py` dispatches them.
- Crisis gate runs **before** the agent sees the user message. Agent cannot override.
- Camera consent persists in `user_prefs` table; therapist tool `set_camera_consent` toggles it; NAO honors the `suppress_image` flag in responses.

## Obsidian Vault

Knowledge vault for this codebase at `~/Documents/Obsidian Vault/Nao-OpenAI-Morgan-Assist/wiki/`. Read `wiki/index.md` first for context. Pattern: `raw/` (immutable) + `wiki/` (LLM-maintained).

## NAO Robot — Connection

- **IP:** `172.20.95.121` (confirmed reachable 2026-04-14 on the CS network; may change if the lease drops — see below for making it static)
- **Hostname:** `nao.local` (mDNS fallback)
- **User:** `nao`
- **Password:** stored in `.env` as `NAO_PASSWORD` (do NOT commit the password; `.env` is gitignored)

### SSH

```bash
ssh nao@172.20.95.121     # on the CS network
ssh nao@nao.local         # elsewhere, if mDNS resolves
```

Recommended: set up passwordless SSH once with `ssh-copy-id nao@172.20.95.121` so you never type the password again. Then add to `~/.ssh/config`:

```
Host nao
  HostName 172.20.95.121
  User nao
```

VS Code Remote-SSH uses this config automatically — just pick "nao" from the host list.

### Making the IP static

Best path: file a ticket with Morgan IT giving them the NAO's WiFi MAC address (`ifconfig wlan0 | grep ether` on the robot) and request a DHCP reservation for `172.20.95.121`. That survives firmware updates and doesn't require touching the robot.

Fallback: configure a fixed IP via Choregraphe (Settings → Network → "Use a fixed IP address") or via `connmanctl` on the robot directly.

### Deploying code to the robot

```bash
rsync -avz --delete nao/ nao@172.20.95.121:/home/nao/nao_assist/
ssh nao@172.20.95.121 "python /home/nao/nao_assist/main.py"
```
