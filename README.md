<div align="center">

# Nao‑OpenAI‑Morgan‑Assist

**A real‑time, embodied, multi‑agent voice assistant for the NAO H25 humanoid, built for Morgan State University.**

<p>
  <img alt="License"   src="https://img.shields.io/badge/license-MIT-1f6feb?style=flat-square">
  <img alt="Python"    src="https://img.shields.io/badge/python-2.7%20%7C%203.11+-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Transport" src="https://img.shields.io/badge/transport-FastAPI%20%2B%20WebSocket-009688?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="Agents"    src="https://img.shields.io/badge/OpenAI-Agents%20SDK-000000?style=flat-square&logo=openai&logoColor=white">
  <img alt="STT"       src="https://img.shields.io/badge/STT-Deepgram%20%E2%80%A2%20Whisper%20%E2%80%A2%20Scribe-13EF93?style=flat-square">
  <img alt="TTS"       src="https://img.shields.io/badge/TTS-ElevenLabs%20Flash%20v2.5-000000?style=flat-square">
  <img alt="Vision"    src="https://img.shields.io/badge/vision-GPT--4o-412991?style=flat-square">
  <img alt="RAG"       src="https://img.shields.io/badge/RAG-CS%20Navigator%20API-4285F4?style=flat-square&logo=googlecloud&logoColor=white">
  <img alt="NAO"       src="https://img.shields.io/badge/Robot-NAO%20H25-FF6F00?style=flat-square">
  <img alt="Research"  src="https://img.shields.io/badge/Research-SAGE--CBT-7B1FA2?style=flat-square">
</p>

<sub>Aayush Shrestha · Advised by Dr. Shuangbao "Paul" Wang · Department of Computer Science, Morgan State University</sub>

<br/>

<img src="docs/img/nao.jpg" width="420" alt="NAO H25 robot used for the project">

</div>

---

## What it is

A NAO humanoid that **listens, sees, thinks, talks, and moves like a person.** A student walks up, the robot recognizes their face, greets them by name, and answers questions — about their course schedule, the weather, how they're feeling, or whatever's on their mind. It hands off between specialist agents (Morgan‑CS chatbot, therapist with CBT and grounding sub‑coaches, utilities, casual chat), turns its head toward whoever is speaking, gestures naturally while it talks, and runs a hardcoded crisis gate that bypasses the LLM entirely when a user expresses suicidal ideation.

Everything runs on the OpenAI Agents SDK with streaming TTS over a long‑lived WebSocket. End‑to‑end latency target: **< 800 ms p50** mouth‑close to first audio chunk.

---

## At a glance

```
                                                     OpenAI Agents SDK
              ┌──────────────────────────────────────────────────────────────┐
              │                                                              │
NAO H25       │            Router ──► Chat | Chatbot | Skills | Therapist    │
(naoqi 2.7)   │                                                  ├─ CBT      │
   │          │                                                  └─ Grounding│
   │          │                                                              │
   │          └──────────────────────────────────────────────────────────────┘
   │
   ▼
WebSocket  ──►  FastAPI server  ──►  Crisis gate  ──►  Motion trigger
                     │                 (988 hard)        (LLM bypass)
                     ▼                                          │
              Streaming STT          Sentence chunker  ──►  ElevenLabs Flash
                (Deepgram /                                       │
                 Whisper /                                        ▼
                 Scribe Realtime)                       Per‑sentence MP3
                     │                                            │
                     ▼                                            ▼
              GPT‑4o vision                              Robot ALAudioPlayer
              (lazy, on trigger)                         (parallel synth)
                     │
                     ▼
                CS Navigator API (Cloud Run, replaces Pinecone)
```

---

## Highlights

### Voice loop

- **Streaming TTS** with sentence‑level chunking (`server/streaming.py`) — first audio plays back before the model has finished generating
- **Three STT backends** (`server/deepgram_asr.py`, `server/elevenlabs_stt.py`, OpenAI Whisper) with hot‑swap A/B harness in `sim/stt_ab.py`
- **ElevenLabs Flash v2.5** TTS with four voice profiles (`girl` / `man` / `neutral` / cloned "my voice"), switched live via voice‑command short‑circuit ("switch to a man voice")
- **Adaptive VAD** with rolling‑ambient calibration + Silero ONNX as authoritative server‑side voice gate
- **Self‑echo defenses** in three layers: substring + bigram‑overlap text checks, server echo‑window, and `[NAO_VISION]`/`[USER]` prompt context so the LLM doesn't deny having ears
- **Post‑playback mic resume** — the mic only re‑arms after `tts_player.is_playing()` returns false **and** a settle grace, so NAO never records its own speaker output
- **Recorder restart on echo reject** — if the server flags self‑echo, the robot tears down the fragment recorder and spins a fresh `stream.wav` to drop the tail

### Multi‑agent graph

- **OpenAI Agents SDK** (`openai-agents>=0.13.6`) with formal handoffs
- **Router** (gpt‑4.1‑nano) reads intent and hands off to one of:
  - **Chat** — fast embodied chat lane (`chat_embodied`) and a tool‑less ultra‑fast lane (`pure_chat`, ~2 s first audio target) selected per‑turn by transcript content
  - **Chatbot** — Morgan State CS questions answered via the **CS Navigator** Cloud Run API (`/chat/stream` and `/chat/guest`)
  - **Skills** — current time, weather, timers, todos
  - **Therapist** (gpt‑4.1‑mini) with two sub‑coaches: **CBT** (thought‑record walker) and **Grounding** (5‑4‑3‑2‑1, box breathing, body scan)
- **MI Coach** (motivational interviewing) ready to promote when needed
- **Memory injection** — long‑term recaps + weekly themes + monthly persona prefixed to therapist + chat prompts
- **Per‑user voice profile** persisted in SQLite (`user_prefs`); cloned voice picks up from the next turn

### Embodiment (the 25 motors actually do things)

- **47 native gesture intents** (`gesture('nod' | 'wave' | 'salute' | 'kiss' | 'applause' | 'joy' | 'thinking' | …)`) wired directly to verified Choregraphe behavior paths in `_GESTURE_BEHAVIOR_MAP`. Falls back to custom `ALMotion.angleInterpolation` when behaviors are missing
- **35‑style dance map** (`taichi`, `kungfu`, `headbang`, `airguitar`, `bandmaster`, `mystic`, `monster`, `helicopter`, `spaceship`, `birthday`, `fitness`, `zombie`, …) all mapped to real installed paths
- **`follow_movement` Choregraphe pack** wired with stop‑phrases ("stop following me", "freeze", "stay there")
- **Sound‑source localization** auto‑tracks the head toward whoever just spoke (`SoundLocalizer`, NAOqi `ALSoundLocalization` poll, ~10 Hz, 300 ms turn‑to‑target target)
- **Face tracker** (`ALTracker` in Head mode, `Face` target) keeps eyes locked on the closest face during conversation
- **Speaking‑gesture loop** picks a random body‑language clip every ~2.5 s while TTS is playing — small motion = alive‑looking robot
- **Action worker thread** dequeues body actions off the WS recv loop so `goToPosture`, `moveTo`, `angleInterpolation`, `runBehavior` can never stall audio/control reception
- **`stopAllBehaviors` cancellation** on barge‑in, crisis lock, and disengage so a 10‑second dance can't keep playing after the user has interrupted
- **All behavior calls non‑blocking** — `startBehavior` everywhere, never `runBehavior`, so a long Choregraphe dance pack never freezes the dispatcher

### Vision

- **Lazy vision** — only fires when the transcript matches a visual trigger phrase ("can you see me", "what am I wearing", "describe me", "do you see", …). Non‑visual turns skip the API call entirely (saves ~1.5 s per turn and stops the model from leaking "I see a poster…" into unrelated answers)
- **Fresh per‑question** — no cache reuse, so a friend asking the same question minutes later in a different setting gets a description of the **current** scene
- **`[NAO_VISION]` block** prepended to user messages with `vision_status` + `vision_summary`; agent prompts have explicit Rule 0: reference visuals only when asked
- **GPT‑4o** model by default, configurable via `VISION_MODEL`

### Wake & onboarding

- **Hybrid wake** — face‑first (`ALFaceDetection` 30 fps) with engagement gates (mutual gaze ≥ 1.5 s, sustained proximity, sound onset, or "hey NAO" keyword) so passersby don't trigger
- **Wake state machine** (`nao/wake_state.py`) — `IDLE → AWARE → ENGAGED → LISTENING → SPEAKING` with LED state per phase
- **Touch‑sensor barge‑in** — head tactile sensors interrupt mid‑speech in any non‑idle state
- **Onboarding face learning** — first turn after engagement scans for known faces; if recognized, server greets by name; if unknown, chat agent introduces NAO and asks for a name; user says "remember me as Aayush" → `learn_face(name='Aayush')` tool writes to NAOqi face DB; persists across sessions
- **Camera consent** — default on with audible heads‑up announce on session open ("Heads up — my camera is on for this conversation. Say 'stop watching me' anytime."); pattern‑trigger `stop watching me` instantly disables for the rest of the session

### Safety

- **Hardcoded crisis gate** (`server/safety.py`) runs **before** any agent sees the user message. Hard keywords ("kill myself", "hurt myself", "suicide", …) force the LLM out of the loop and reply with a fixed 988 message
- **Soft triggers** ("hopeless", "give up", …) are LLM‑classified by a separate gpt‑4.1 prompt
- **Hotline reply** wording validated for tone — never congratulates the user for opening up, validates without praising, always names 988 + "any time, day or night"
- **Camera privacy** — visible green ear‑LED while capturing + first‑turn audible announce + `stop watching me` instant‑off pattern trigger

### Observability

- **Structured JSON logging** via `structlog` (`server/logging_setup.py`) on day one — every turn carries phase timings: `vad`, `stt`, `crisis_check`, `motion_trigger`, `vision_call`, `tts_synth_first_chunk`, `e2e_user_to_first_audio`, `tts_synth_total`, `e2e_user_to_complete`, `action_dispatch`
- **Prometheus exporter** at `/metrics` with 22 latency labels
- **Grafana dashboard** (`server/dashboards/grafana_voice.json`) — 10 panels covering latency p50/p95 per phase, tool‑call frequency, wake transitions, crisis triggers, camera‑off events
- **Robot‑side rotating JSONL log** (`~/nao_assist/logs/`, 50 MB cap)

### Robot‑side brain (optional)

- **64 KB local cache** (`nao/utils/brain.py` + `user_cache.py`) with identity, preferences, last‑seen, last recap summary
- **Sync handshake** — robot sends `{face_id, brain_version}` on session_open; server pushes deltas if newer
- **LRU eviction** when approaching the 64 KB cap (top‑10 most recent users)
- **Limited offline mode** — if the WS fails, robot can still wake, greet by cached name, and explain it can't reach its brain

---

## Quick start

### Server side (Python 3.11+)

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env       # then fill in OPENAI_API_KEY etc.
```

`.env` essentials (full list in `.env.example`):

```ini
OPENAI_API_KEY=sk-...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_GIRL=21m00Tcm4TlvDq8ikWAM
ELEVENLABS_VOICE_MAN=...
ELEVENLABS_VOICE_NEUTRAL=...
ELEVENLABS_VOICE_MY=...                    # cloned "my voice" (optional)
DEEPGRAM_API_KEY=...                        # optional, fastest STT
NAO_IP=172.20.95.127                        # robot LAN IP
NAO_PASSWORD=...                            # only used by run.sh for SSH deploy
NAO_SHARED_SECRET=...                       # WS auth between robot and server
SERVER_IP=192.168.x.x                       # this machine's LAN IP
USE_WS=1                                    # FastAPI + WebSocket transport
```

### One‑shot run

```bash
./run.sh             # deploys nao/ to robot via rsync, boots uvicorn, launches main.py on robot
./run.sh stop        # tears it all down
RAW_LOGS=1 ./run.sh  # disable the signal-only log filter
```

`run.sh` filters server + robot logs down to a clean per‑turn signal stream: transcripts, replies, gestures, vision decisions, voice profile changes, errors. Set `RAW_LOGS=1` to see everything.

### Run the agents only (no robot)

```bash
USE_WS=1 uvicorn server.app_ws:app --host 0.0.0.0 --port 5050 --log-level info
```

Hit `WS /ws/{username}` from any WS client. Frame format: `{type, subtype, seq, data}` — see `server/app_ws.py:_send_json` and `_ingest_control` for the contract.

---

## Talking to NAO

| Say | Triggers |
|---|---|
| "Hey NAO" / step into camera view | Wake (face‑first hybrid) |
| "Wave at my friend" | `wave_hand` action |
| "Do the kung fu" | `dance(style='kungfu')` → `KungFu_1` Choregraphe pack |
| "Follow me" / "Track me" | `follow_movement` (Choregraphe `follow-me` pack) |
| "Stop following me" / "Freeze" | `stop_follow` |
| "What am I wearing?" / "Can you see me?" | Lazy GPT‑4o vision call |
| "Switch to a man voice" / "Use my voice" | Voice profile flip (per‑user persisted) |
| "Remember me as Aayush" | `learn_face(name='Aayush')` |
| "Stop watching me" | Camera off for session |
| "Set a 10‑minute timer" | Skills agent |
| "I'm anxious about finals" | Therapist agent |
| "What classes does Morgan offer in spring?" | CS Navigator (Chatbot agent) |

---

## Performance

Targets verified end‑to‑end on the physical NAO at `172.20.95.127`:

| Metric | Target | Notes |
|---|---|---|
| `e2e_user_to_first_audio` p50 | < 800 ms | streaming TTS, first sentence chunk |
| `e2e_user_to_first_audio` p95 | < 1.2 s | tail latency under tool calls |
| `tts_synth_first_chunk` | < 500 ms | ElevenLabs Flash v2.5 streaming |
| `vision_call` | ~1.5 s | only fires on visual triggers |
| Barge‑in stop time | < 200 ms | `tts_player.stop()` + `stopAllBehaviors` |

`/metrics` exposes histograms per phase. Run `./run.sh` and watch the live `phase_ms={...}` block in `turn_complete` log lines.

---

## Repo layout

```
Nao-OpenAI-Morgan-Assist/
├── nao/                      Python 2.7 — runs on the robot
│   ├── main.py               Entry: wake state machine + session controller
│   ├── ws_client.py          Long-lived WebSocket session
│   ├── audio_module.py       ALAudioRecorder fragment streamer
│   ├── stream_tts.py         MP3 → WAV → ALAudioPlayer with ffmpeg loudness chain
│   ├── wake_state.py         IDLE → AWARE → ENGAGED → LISTENING → SPEAKING
│   ├── sound_localize.py     ALSoundLocalization → head turn (auto-track)
│   ├── leds.py               Eye LED helpers (state cues)
│   ├── idle_motion.py        Background breathing + gaze drift
│   └── utils/
│       ├── nao_execute.py    Action dispatcher: gestures, dances, motors
│       ├── face_naoqi.py     Face recognition + learning
│       ├── brain.py          64 KB local identity/preferences cache
│       └── camera_capture.py Per-turn JPEG snap helpers
│
├── server/                   Python 3.11+ — runs on dev machine / cloud
│   ├── app_ws.py             FastAPI + WebSocket — main entry
│   ├── safety.py             Pre-dispatch crisis gate (988)
│   ├── motion_trigger.py     Regex short-circuit for body commands
│   ├── streaming.py          Sentence chunker for streaming TTS
│   ├── elevenlabs_tts.py     Flash v2.5 streaming with voice profiles
│   ├── deepgram_asr.py       Streaming Nova-2 STT
│   ├── elevenlabs_stt.py     Scribe v2 Realtime STT
│   ├── vad_silero.py         Authoritative server-side voice gate
│   ├── semantic_endpoint.py  End-of-utterance arbiter (LLM-assisted)
│   ├── memory.py             Recaps, weekly themes, monthly personas
│   ├── session.py            SQLiteSession + per-user prefs + camera consent
│   ├── logging_setup.py      structlog JSON logs
│   ├── metrics.py            Prometheus exporter
│   ├── dashboards/           Grafana JSON
│   ├── agents/
│   │   ├── router.py         Triage + handoffs
│   │   ├── chat.py           pure_chat + chat_embodied lanes
│   │   ├── chatbot.py        Morgan CS RAG via CS Navigator
│   │   ├── skills.py         Time/weather/timers/todos
│   │   ├── therapist.py      Empathic + handoffs to CBT/Grounding
│   │   ├── cbt_coach.py      Thought-record walker
│   │   ├── grounding_coach.py 5-4-3-2-1, box breathing, body scan
│   │   └── mi_coach.py       Motivational interviewing (experimental)
│   └── tools/
│       ├── nao_actions.py    18 body action tools + gesture + learn_face
│       ├── cs_navigator.py   Morgan CS knowledge API client
│       ├── emotion.py        observe_face, log_emotion, recap_session
│       └── skills_tools.py   Time, weather, timer, todo
│
├── sim/                      Python 3.11+ — load test + benchmarks
│   ├── live_nao.py           Headless robot stand-in for soak tests
│   ├── stt_ab.py             A/B harness for STT backends
│   ├── chat_model_bench.py   Per-model latency benchmark
│   ├── fast_chat_bench.py    pure_chat lane benchmark
│   └── vision_battery.py     Vision regression suite
│
├── docs/
│   ├── PRD_v2.md             600-line spec, 9 phases
│   ├── PHASE_*_TASK_MAP.md   Per-phase delivery plans
│   ├── spike_results.md      Phase 0.5 transport benchmark
│   └── Nao_Morgan_Assist_Walkthrough.pdf
│
└── run.sh                    One-shot deploy + boot + log tail
```

---

## Configuration reference

| Env var | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | (required) | OpenAI Agents SDK + Whisper + GPT‑4o vision |
| `ROUTER_MODEL` | `gpt-4.1-nano` | Triage agent |
| `CHAT_MODEL` | `gpt-4.1-nano` | Casual chat (both lanes) |
| `CHATBOT_MODEL` | `gpt-4.1-mini` | Morgan CS responder |
| `THERAPIST_MODEL` | `gpt-4.1-mini` | Therapist + sub‑coaches |
| `SKILLS_MODEL` | `gpt-4.1-nano` | Time/weather/timers |
| `CRISIS_MODEL` | `gpt-4.1` | Crisis classifier (soft triggers) |
| `WHISPER_MODEL` | `gpt-4o-mini-transcribe` | Fallback STT |
| `VISION_MODEL` | `gpt-4o` | Per‑turn camera summary |
| `ELEVENLABS_API_KEY` | (required for TTS) | Flash v2.5 |
| `ELEVENLABS_MODEL` | `eleven_flash_v2_5` | TTS model |
| `ELEVENLABS_VOICE_{GIRL,MAN,NEUTRAL,MY}` | (required) | Voice profile IDs |
| `ELEVENLABS_DEFAULT_PROFILE` | `girl` | Default voice |
| `USE_ELEVENLABS_STT` | `0` | Enable Scribe v2 Realtime STT |
| `DEEPGRAM_API_KEY` | (optional) | Fastest STT path |
| `USE_DEEPGRAM` | `1` if key set | Toggle Deepgram |
| `MIC_GATE_GRACE_MS` | `800` | Post‑playback grace before mic re‑arms |
| `TTS_COOLDOWN_PADDING_MS` | `600` | Server‑side echo window |
| `NAO_IP`, `NAO_PORT` | `127.0.0.1`, `9559` | NAOqi proxy target |
| `NAO_PASSWORD` | (gitignored) | SSH password for `run.sh` deploy |
| `NAO_SHARED_SECRET` | (required) | WS auth header `X-NAO-Secret` |
| `SERVER_IP`, `SERVER_PORT` | auto, `5050` | What the robot WS‑connects to |
| `USE_WS` | `1` | FastAPI + WebSocket transport (`0` = legacy Flask) |

`.env` is gitignored. Never commit it.

---

## Testing

```bash
pytest -q               # 30 unit + integration tests
sim/live_proof.py       # full pipeline soak test against a fake naoqi
sim/stt_ab.py           # A/B Deepgram vs Whisper vs Scribe
sim/chat_model_bench.py # per-model latency
```

`pytest.ini` pins rootdir so the OpenAI Agents SDK's `agents` module isn't shadowed by `server/agents/`.

---

## NAO connection cheatsheet

```bash
ssh nao@$NAO_IP                           # CS network
rsync -avz --delete nao/ nao@$NAO_IP:/home/nao/nao_assist/
ssh nao@$NAO_IP "python /home/nao/nao_assist/main.py"

qicli call ALBehaviorManager.getInstalledBehaviors  # 915 behaviors on this NAO
qicli call ALBehaviorManager.runBehavior "follow-me"
qicli call ALBehaviorManager.stopAllBehaviors
```

VS Code Remote‑SSH config:

```
Host nao
  HostName 172.20.95.127
  User nao
```

---

## Acknowledgments

- **Dr. Shuangbao "Paul" Wang** for advising the SAGE‑CBT research direction
- **Morgan State Department of Computer Science** for the NAO and lab access
- **OpenAI**, **Anthropic Claude Code**, **ElevenLabs**, **Deepgram**, **Aldebaran/SoftBank Robotics**

## License

MIT — see `LICENSE`.
