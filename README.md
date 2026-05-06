<div align="center">

# Nao‑OpenAI‑Morgan‑Assist

**A voice‑driven multi‑agent assistant for the NAO humanoid robot, built for Morgan State University.**

<p>
  <img alt="License"   src="https://img.shields.io/badge/license-MIT-1f6feb?style=flat-square">
  <img alt="Python"    src="https://img.shields.io/badge/python-2.7%20%7C%203.11+-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="OpenAI"    src="https://img.shields.io/badge/OpenAI-Agents%20SDK-000000?style=flat-square&logo=openai&logoColor=white">
  <img alt="Deepgram"  src="https://img.shields.io/badge/Deepgram-Nova--2-13EF93?style=flat-square">
  <img alt="ElevenLabs" src="https://img.shields.io/badge/ElevenLabs-Flash%20v2.5-000000?style=flat-square">
  <img alt="Vertex"    src="https://img.shields.io/badge/Vertex%20AI-Search-4285F4?style=flat-square&logo=googlecloud&logoColor=white">
  <img alt="NAO"       src="https://img.shields.io/badge/Robot-NAO%20H25-FF6F00?style=flat-square">
  <img alt="Research"  src="https://img.shields.io/badge/Research-SAGE--CBT-7B1FA2?style=flat-square">
</p>

<sub>Aayush Shrestha · Advised by Dr. Shuangbao "Paul" Wang · Department of Computer Science, Morgan State University</sub>

<br/>

<img src="https://github.com/user-attachments/assets/826d4b7b-7c11-4712-8d5c-7a1b1829ccff" width="640" alt="NAO robot">

</div>

---

## Overview

NAO listens, sees, remembers, and replies in the user's own cloned voice. The robot streams audio to a Flask server that routes the turn through a graph of specialized agents (chat, RAG over a Morgan CS knowledge base, skills, therapy with CBT and motivational interviewing). A pre‑dispatch crisis gate, a runtime safety invariant, and per‑face memory give the system clinical and operational guardrails.

| Capability        | Implementation                                                                 |
|-------------------|---------------------------------------------------------------------------------|
| Speech‑to‑text    | **Deepgram Nova‑2** streaming ASR with keyword boosting (~150 ms)             |
| Endpointing       | **Silero VAD** + semantic "is the user done?" gate via `gpt-4.1-nano`         |
| Reasoning         | **OpenAI Agents SDK** — router → chat / chatbot / skills / therapist          |
| Therapy           | CBT thought records + grounding + Motivational Interviewing (OARS)            |
| Vision            | GPT‑4.1 multimodal — per‑turn JPEG, affect routing                             |
| Knowledge base    | **Vertex AI Search** (`csnavigator-kb-v7`)                                     |
| Voice             | **ElevenLabs Flash v2.5** voice cloning, falls back to NAO TTS                 |
| Memory            | SQLite — per‑face users, sessions, rolling LLM summaries, profile JSON        |
| Safety            | Pre‑dispatch crisis gate (988 hotline) + SAGE‑CBT supervisor‑veto topology    |
| Embodiment        | 18 NAO action tools (pose, gesture, dance, LEDs) executed in order            |

---

## Architecture

```mermaid
flowchart LR
    classDef person fill:#0d47a1,stroke:#0d47a1,color:#fff
    classDef robot  fill:#1565c0,stroke:#1565c0,color:#fff
    classDef srv    fill:#2e7d32,stroke:#2e7d32,color:#fff
    classDef ext    fill:#ef6c00,stroke:#ef6c00,color:#fff
    classDef db     fill:#6a1b9a,stroke:#6a1b9a,color:#fff

    U([User]):::person

    subgraph NAO["NAO H25 · Python 2.7 / naoqi"]
        WAKE[wake_listener]:::robot
        CONV[conversation loop]:::robot
        EXEC[action dispatcher]:::robot
    end

    subgraph SRV["Flask Server · Python 3.11+"]
        API["/turn · /stream_turn · /tts"]:::srv
        GATE[crisis gate]:::srv
        TOPO[topology dispatcher]:::srv
        RUN[Agents SDK Runner]:::srv
        MEM[(per-face memory)]:::db
    end

    DG[(Deepgram Nova-2)]:::ext
    OAI[(OpenAI · gpt-4.1)]:::ext
    EL[(ElevenLabs · clone)]:::ext
    VAI[(Vertex AI Search)]:::ext

    U -- voice --> WAKE --> CONV --> API
    API --> DG --> GATE --> TOPO --> RUN
    RUN --> OAI
    RUN --> VAI
    RUN --> MEM
    API --> EL
    API -- reply + actions[] + audio --> CONV --> EXEC --> U
```

---

## Agent graph

```mermaid
flowchart TD
    classDef gate  fill:#b71c1c,stroke:#b71c1c,color:#fff
    classDef route fill:#0d47a1,stroke:#0d47a1,color:#fff
    classDef spec  fill:#1565c0,stroke:#1565c0,color:#fff
    classDef sub   fill:#7b1fa2,stroke:#7b1fa2,color:#fff
    classDef tool  fill:#2e7d32,stroke:#2e7d32,color:#fff

    U([turn]) --> C{crisis_check}:::gate
    C -- positive --> H[988 hotline]:::gate
    C -- clean --> R[router]:::route

    R --> CH[chat]:::spec
    R --> CB[chatbot]:::spec
    R --> SK[skills]:::spec
    R --> TH[therapist]:::spec

    TH --> CBT[cbt_coach]:::sub
    TH --> GR[grounding_coach]:::sub
    TH --> MI[mi_coach]:::sub

    CH & TH & CBT -.-> NA[(nao_actions)]:::tool
    CB -.-> VS[(vertex_search)]:::tool
    SK -.-> ST[(skills_tools)]:::tool
    TH & CBT -.-> EM[(emotion + memory)]:::tool
```

| Agent | Role | Default model |
|---|---|---|
| **router** | triage + handoff | `gpt-4.1-nano` |
| **chat** | general chat + NAO actions | `gpt-4.1-nano` |
| **chatbot** | Morgan CS RAG via Vertex AI | `gpt-4.1-mini` |
| **skills** | time, weather, timers, todos | `gpt-4.1-nano` |
| **therapist** | empathy + handoffs + vision | `gpt-4.1-mini` |
| **cbt_coach** | Beck thought record (one step per turn) | `gpt-4.1-mini` |
| **grounding_coach** | 5‑4‑3‑2‑1, box breathing, body scan | `gpt-4.1-mini` |
| **mi_coach** | Motivational Interviewing (OARS) | `gpt-4.1-mini` |
| **crisis** | safety classifier | `gpt-4.1` |

---

## Repository

```
Nao-OpenAI-Morgan-Assist/
├─ nao/                    Python 2.7 — copy to /home/nao/nao_assist/
│  ├─ main.py              wake loop entry
│  ├─ conversation.py      record · POST · speak · execute
│  ├─ audio_handler.py     loose energy gate (server VAD finalizes)
│  ├─ stream_tts.py        SSE consumer + ElevenLabs MP3 playback
│  └─ utils/               face_naoqi · voice_clone · nao_execute · …
├─ server/                 Python 3.11+
│  ├─ server.py            Flask app
│  ├─ deepgram_asr.py      Nova-2 transcription
│  ├─ vad_silero.py        Silero VAD wrapper
│  ├─ semantic_endpoint.py LLM "is user done?" gate
│  ├─ elevenlabs_tts.py    Flash v2.5 voice clone
│  ├─ memory.py            users · sessions · rolling summaries
│  ├─ session.py           SQLiteSession (Agents SDK) + consent
│  ├─ safety.py            pre-dispatch crisis gate
│  ├─ invariant.py         SAGE-CBT runtime safety monitor
│  ├─ topologies/          passthrough · supervisor_veto · debate · shared_pool
│  ├─ agents/              router · chat · chatbot · skills · therapist · …
│  └─ tools/               nao_actions · vertex_search · emotion · memory_tools
├─ tests/redteam/          70-prompt SAGE-CBT red-team harness
├─ docs/                   design specs · plans
└─ PRD.md                  SAGE-CBT research thesis
```

---

## Quick start

### Server

```bash
cd server
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` at the repo root:

```env
OPENAI_API_KEY=sk-...
DEEPGRAM_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...

GOOGLE_CLOUD_PROJECT=csnavigator-vertex-ai
VERTEX_DATASTORE_ID=csnavigator-kb-v7

NAO_IP=172.20.95.127
SERVER_IP=0.0.0.0
SERVER_PORT=5050

# Optional research layer (off by default)
# SAGE_TOPOLOGY=supervisor_veto
```

```bash
python -m server.server
```

### NAO

```bash
rsync -avz --delete nao/ nao@<robot-ip>:/home/nao/nao_assist/
ssh nao@<robot-ip> "python /home/nao/nao_assist/main.py"
```

Wake phrase: **"nao"** (optionally followed by a hint: *chat*, *morgan*, *therapy*, *skills*).

### Tests

```bash
python -m pytest -q
```

---

## HTTP API

| Method | Path             | Purpose                                        |
|--------|------------------|------------------------------------------------|
| `POST` | `/turn`          | one‑shot reply (JSON)                          |
| `POST` | `/stream_turn`   | streaming reply (SSE: sentence + audio + done) |
| `POST` | `/tts`           | ElevenLabs voice clone synthesis (MP3)         |
| `GET`  | `/health`        | liveness probe                                 |

**Multipart form fields:** `audio` (WAV), `image` (JPEG, optional), `username`, `hint`, `end_session`.

```jsonc
// /turn response
{
  "username":     "aayush",
  "user_input":   "how do i declare a cs major",
  "reply":        "You'll fill out the change-of-major form with the CS office...",
  "active_agent": "chatbot",
  "actions":      [{ "name": "change_eye_color", "args": { "color": "blue" } }],
  "crisis":       false,
  "suppress_image": false
}
```

---

## Request lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant N as NAO
    participant F as Flask
    participant D as Deepgram
    participant V as Silero VAD
    participant S as Semantic gate
    participant G as Crisis gate
    participant R as Agents Runner
    participant E as ElevenLabs
    participant DB as SQLite

    U->>N: utterance
    N->>F: POST /stream_turn (audio, image)
    F->>V: has_voice?
    V-->>F: yes
    F->>D: transcribe
    D-->>F: text
    F->>S: is_complete_thought?
    S-->>F: yes
    F->>G: crisis_check
    alt risk
        G-->>F: hotline + crisis=true
    else clean
        G-->>F: ok
        F->>R: run_topology
        R-->>F: reply (per-sentence)
        loop each sentence
            F->>E: synthesize
            E-->>F: mp3
            F-->>N: SSE { sentence, audio_b64 }
        end
    end
    F->>DB: append history + start/end session
    N->>U: cloned-voice playback + actions[]
```

---

## Memory model

Two layers, one SQLite file (`config.SESSION_DB`).

| Layer                   | Owner               | Stores                                                           |
|-------------------------|---------------------|------------------------------------------------------------------|
| `SQLiteSession`         | OpenAI Agents SDK   | turn-by-turn message history per face_id                         |
| `users` · `sessions`    | `server/memory.py`  | display name, profile JSON, session summaries, started/ended_at  |

```mermaid
erDiagram
    USERS ||--o{ SESSIONS : has
    USERS {
        text  face_id PK
        text  display_name
        text  profile_json
        real  created_at
        real  updated_at
    }
    SESSIONS {
        int   id PK
        text  face_id FK
        text  mode
        real  started_at
        real  ended_at
        text  summary
    }
```

A returning user's last three session summaries are injected as a system preamble on every turn. `forget_user(face_id)` wipes the row, the session log, and the SDK chat history.

---

## SAGE‑CBT research layer

When `SAGE_TOPOLOGY` is set, the therapist subgraph is wrapped by a pluggable orchestration topology with a runtime‑monitorable safety invariant. Default behavior is unchanged when the flag is unset. See [PRD.md](PRD.md).

| Topology           | Intervention                                               | Role            |
|--------------------|------------------------------------------------------------|-----------------|
| `passthrough`      | none — vanilla Runner                                       | legacy default  |
| `supervisor_veto`  | SafetyAgent gates every reply pre‑emit                      | proposed        |
| `debate`           | therapist + cbt_coach draft; judge picks; safety observes   | baseline        |
| `shared_pool`      | three agents draft into scratchpad; therapist synthesizes   | baseline        |

> ∀ t, `proposed_reply(t)` contains risk ⇒ `final_reply(t) ≠ proposed_reply(t)` ∧ crisis_lockout within 1 turn.

---

## Configuration tips

- **Endpointing:** `nao/audio_handler.py` is intentionally permissive; final cut is in `server/vad_silero.py` + `server/semantic_endpoint.py`.
- **Latency budget:** target p50 < 1.5 s end‑to‑end. Deepgram ~150 ms, router (`gpt-4.1-nano`) ~150 ms first token, ElevenLabs ~240 ms per sentence.
- **Barge‑in:** head touch on NAO calls `tts.stopAll()` instantly. Acoustic barge is off by default to avoid self‑echo.
- **Vertex AI:** run `gcloud auth application-default login` once; without it `chatbot` returns "I'm not sure".

---

## License

Released under the [MIT License](LICENSE).

## Authors

- **Dr. Shuangbao "Paul" Wang** — Faculty Advisor / Principal Investigator. Chairperson, Department of Computer Science, Morgan State University.
- **Aayush Shrestha** — Lead Developer / Research Assistant. Department of Computer Science, Morgan State University.
