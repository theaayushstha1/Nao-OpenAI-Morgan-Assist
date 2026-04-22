# 🤖 Nao-OpenAI-Morgan-Assist

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](#-license)
[![Python](https://img.shields.io/badge/Python-2.7_|_3.11+-blue.svg)](#-requirements)
[![Platform](https://img.shields.io/badge/Robot-NAO-orange.svg)]()
[![OpenAI](https://img.shields.io/badge/API-OpenAI-black.svg)]()
[![Vertex AI](https://img.shields.io/badge/RAG-Vertex_AI_Search-4285F4.svg)]()
[![Research](https://img.shields.io/badge/Research-SAGE--CBT-purple.svg)](PRD.md)

A voice-driven assistant that connects the **NAO humanoid robot** to **OpenAI (Whisper + GPT-4o)** and a **Vertex AI Search** knowledge base for the **Morgan State University (MSU) Computer Science Department** — now with a live research branch (**SAGE-CBT**) building a Supervisor-Veto multi-agent CBT dialogue architecture with a runtime-monitorable safety invariant.

![nao](https://github.com/user-attachments/assets/826d4b7b-7c11-4712-8d5c-7a1b1829ccff)

---

## 📌 Overview
**Nao-OpenAI-Morgan-Assist** lets NAO:
- 🎤 **Listen** to users
- 📝 **Transcribe** speech with OpenAI **Whisper**
- 📂 **Retrieve** Morgan CS knowledge from **Vertex AI Search** (`csnavigator-kb-v7`)
- 💡 **Generate** answers with **GPT-4o** via the **OpenAI Agents SDK**
- 👁 **See** the user's face each turn and route on affect
- 🔊 **Speak** replies via NAO TTS

> Developed by **Aayush Shrestha** under the supervision of **Dr. Shuangbao "Paul" Wang**.

---

## ✨ Features
- 🧠 **OpenAI Agents SDK** – Multi-agent routing (router + chat, chatbot, skills, therapist with CBT/grounding sub-agents)
- 🗣 **Voice + Vision** – Whisper STT, GPT-4o multimodal (face emotion read from per-turn JPEG)
- 📚 **Morgan CS RAG** – Vertex AI Search retrieval for department knowledge
- 💙 **Therapy Mode** – Empathetic companion with CBT thought records, grounding exercises, per-session emotion logging, cross-session recaps
- 🛡 **Safety Gate** – Pre-dispatch crisis check (keyword + LLM) with 988 hotline fallback
- 👤 **Face Recognition** – On-robot naoqi ALFaceDetection for user recall
- 💾 **SQLiteSession** – SDK-managed conversation history + camera consent + therapy recaps + hierarchical memory (weekly themes, monthly personas)
- 🤖 **NAO Actions** – 18 tools (pose, gesture, move, dance, LEDs) captured into action queue for in-order execution
- 🧪 **SAGE-CBT research layer** *(optional, feature-flagged)* – Three pluggable orchestration topologies (Supervisor-Veto / Debate / SharedPool), a runtime-monitorable safety invariant, and a 70-prompt red-team harness. See [PRD.md](PRD.md).

---

## 🗂 Project Structure

```
Nao-OpenAI-Morgan-Assist/
├─ nao/                           # Python 2.7 — deploy this to the robot
│   ├─ main.py                    # Wake loop entry
│   ├─ wake_listener.py           # Wake phrase + hint extraction
│   ├─ conversation.py            # Single loop: record → POST /turn → speak + execute
│   ├─ audio_handler.py           # VAD + recording
│   ├─ processing_announcer.py    # Background "please wait"
│   ├─ config.py                  # IPs, ports
│   └─ utils/
│       ├─ camera_capture.py      # snap_quick() for per-turn JPEG
│       ├─ nao_execute.py         # Dispatches server actions to naoqi
│       ├─ face_naoqi.py          # Face reco/learning
│       ├─ ask_name_utils.py      # Name ask flow
│       ├─ exit_detection.py
│       ├─ name_utils.py
│       └─ speech.py              # Phrase pools + expressive TTS
├─ server/                        # Python 3.11+ Flask + OpenAI Agents SDK
│   ├─ server.py                  # POST /turn + /stream_turn + /greet + /health
│   ├─ safety.py                  # Pre-dispatch crisis gate
│   ├─ session.py                 # SQLiteSession + consent + recaps
│   ├─ invariant.py               # SAGE-CBT runtime safety monitor (research)
│   ├─ memory_rollup.py           # Weekly/monthly hierarchical memory
│   ├─ streaming.py               # Per-sentence SSE helpers
│   ├─ config.py
│   ├─ agents/                    # router, chat, chatbot, skills, therapist, cbt_coach, grounding_coach
│   ├─ tools/                     # nao_actions, vertex_search, emotion, skills_tools
│   ├─ topologies/                # SAGE-CBT: passthrough / supervisor_veto / debate / shared_pool (research)
│   ├─ tests/                     # pytest (60+ tests)
│   └─ requirements.txt
├─ tests/redteam/                 # SAGE-CBT red-team harness (50 single-turn + 20 multi-turn prompts)
├─ docs/                          # Design specs + implementation plans
├─ PRD.md                         # SAGE-CBT research thesis (v0.3)
├─ CLAUDE.md, README.md, LICENSE, pytest.ini
```

---

## ⚙️ Requirements
- **Python 2.7** (NAO side, NAOqi SDK only)
- **Python 3.11+** (server; `openai-agents`, `openai>=1.50`, `flask`, `google-cloud-discoveryengine`, optional `anthropic` for SAGE-CBT Claude ablation)

## 🚀 Quick Start

### 1) Server (Python 3.11+)
```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` at repo root (start from `.env.example`):
```
OPENAI_API_KEY=sk-your-key

# Vertex AI Search (Morgan CS RAG). For dev, run `gcloud auth application-default login` once.
GOOGLE_CLOUD_PROJECT=csnavigator-vertex-ai
VERTEX_LOCATION=us
VERTEX_DATASTORE_ID=csnavigator-kb-v7

NAO_IP=172.20.95.111
SERVER_IP=0.0.0.0

# SAGE-CBT research layer (optional; off by default)
# SAGE_TOPOLOGY=supervisor_veto          # passthrough | supervisor_veto | debate | shared_pool
# SAGE_SAFETY_PROVIDER=openai            # openai | claude
# ANTHROPIC_API_KEY=                     # required only when SAGE_SAFETY_PROVIDER=claude
```

Run the server:
```bash
python -m server.server        # dev
# or: gunicorn -w 1 -b 0.0.0.0:5000 server.server:app
```

### 2) NAO (Python 2.7)
Copy the `nao/` folder to the robot:
```bash
scp -r nao/ nao@<nao-ip>:/home/nao/nao_assist/
ssh nao@<nao-ip>
export SERVER_IP=<server-host>
python /home/nao/nao_assist/main.py
```

Wake phrases: "nao", "hey nao", or with hints: "morgan assist", "therapy", "mini nao".

### 3) Run tests
```bash
python -m pytest -q
# 60+ tests pass (53 core + 7 SAGE-CBT invariant)
```

### 4) (Research) Run the SAGE-CBT red-team sweep
```bash
# Dry-run (no OpenAI credits — validates harness plumbing only)
python -m tests.redteam.runner --topology supervisor_veto --budget single --dry-run

# Full sweep: 3 topologies × 2 adversary budgets → Pareto plot (~$1.20)
bash tests/redteam/run_all.sh
open logs/pareto.png
```

## 🔌 API

**POST `/turn`** (multipart):
- `audio` (WAV), `image` (JPEG, optional), `username`, `hint` (`chat`|`morgan`|`therapy`|`skills`), `end_session` (bool)

**Response JSON:**
```json
{ "username": "alice", "user_input": "...", "reply": "...",
  "active_agent": "therapist",
  "actions": [{"name":"change_eye_color","args":{"color":"blue"}}],
  "crisis": false, "suppress_image": false }
```

`actions[]` is the ordered list NAO executes. Router automatically hands off to the right specialist when `hint` is null.

## 🧭 Agent Graph

```mermaid
flowchart TD
    classDef entry fill:#0d47a1,stroke:#0d47a1,color:#fff,stroke-width:1px
    classDef spec fill:#1565c0,stroke:#1565c0,color:#fff
    classDef sub fill:#7b1fa2,stroke:#7b1fa2,color:#fff
    classDef tool fill:#2e7d32,stroke:#2e7d32,color:#fff
    classDef gate fill:#b71c1c,stroke:#b71c1c,color:#fff

    U([user turn + JPEG]) --> C{crisis_check<br/>pre-dispatch gate}:::gate
    C -- positive --> H[988 hotline script]:::gate
    C -- clean --> R[router]:::entry

    R -->|handoff| CH[chat]:::spec
    R -->|handoff| CB[chatbot]:::spec
    R -->|handoff| SK[skills]:::spec
    R -->|handoff| TH[therapist]:::spec

    TH -->|handoff| CBT[cbt_coach]:::sub
    TH -->|handoff| GR[grounding_coach]:::sub

    CH & TH & CBT -.tool.-> NA[(nao_actions<br/>18 tools)]:::tool
    CB -.tool.-> VS[(vertex_search)]:::tool
    SK -.tool.-> SKT[(skills_tools)]:::tool
    TH & CBT -.tool.-> EM[(emotion<br/>observe_face, log,<br/>identify_distortion,<br/>suggest_reframe)]:::tool
```

| Agent | Role | Model |
|---|---|---|
| **router** | triage + handoff | `gpt-4o-mini` |
| **chat** | general chat + NAO actions | `gpt-4o-mini` |
| **chatbot** | Morgan CS RAG via Vertex AI Search | `gpt-4o-mini` |
| **skills** | time, weather, timers, todos | `gpt-4o-mini` |
| **therapist** | empathy + CBT/grounding handoffs + vision | `gpt-4o` |
| **cbt_coach** | Beck 7-column thought record | `gpt-4o` |
| **grounding_coach** | 5-4-3-2-1, box breathing, body scan | `gpt-4o` |

---

## 🧪 SAGE-CBT Research Layer (optional, feature-flagged)

When `SAGE_TOPOLOGY != "passthrough"`, the therapist subgraph is wrapped by a pluggable orchestration topology. The research thesis ([PRD.md](PRD.md)) compares three on a 70-prompt red-team. The SafetyAgent provider is swappable at runtime.

```mermaid
flowchart LR
    classDef flag fill:#ef6c00,stroke:#ef6c00,color:#fff
    classDef topo fill:#6a1b9a,stroke:#6a1b9a,color:#fff
    classDef safe fill:#b71c1c,stroke:#b71c1c,color:#fff
    classDef mon fill:#00695c,stroke:#00695c,color:#fff

    IN[/turn<br/>request/] --> D{SAGE_TOPOLOGY}:::flag
    D -->|passthrough<br/>default| P[Runner.run → reply]:::topo
    D -->|supervisor_veto| SV[Runner.run<br/>proposed_reply]:::topo
    D -->|debate| DB[therapist ‖ cbt_coach<br/>→ judge picks]:::topo
    D -->|shared_pool| SP[3-agent scratchpad<br/>→ therapist synthesizes]:::topo

    SV --> G{{SafetyAgent<br/>verdict}}:::safe
    G -->|allow| OUT1[emit reply]
    G -->|revise| OUT2[emit rewrite]
    G -->|escalate| OUT3[HOTLINE_REPLY<br/>+ crisis_lockout]

    DB --> OBS1[(SafetyAgent<br/>observe only)]:::safe
    SP --> OBS2[(SafetyAgent<br/>observe only)]:::safe

    P & OUT1 & OUT2 & OUT3 & OBS1 & OBS2 --> IV[[invariant.record_turn]]:::mon
    IV --> SQL[(safety_events<br/>topology_trace)]
```

**Topologies**

| Name | Intervention | Role in paper |
|---|---|---|
| `passthrough` | none — existing behavior | legacy default |
| `supervisor_veto` | SafetyAgent **gates** every reply pre-emit | proposed contribution |
| `debate` | therapist + cbt_coach draft; judge picks; Safety observes | baseline |
| `shared_pool` | three agents draft into scratchpad; therapist synthesizes | baseline |

**SafetyAgent provider**

| `SAGE_SAFETY_PROVIDER` | Model | Notes |
|---|---|---|
| `openai` *(default)* | `gpt-4o` | Always available |
| `claude` | `claude-opus-4-7` | Requires `ANTHROPIC_API_KEY`; treated as ablation |

**Runtime safety invariant** (see [PRD §7.5](PRD.md)):

> ∀ t, `proposed_reply(t)` contains risk ⇒ `final_reply(t) ≠ proposed_reply(t)` ∧ crisis_lockout within 1 turn.

The monitor at `server/invariant.py` evaluates this over a sliding 5-turn window per user and logs violations to SQLite regardless of which topology is active. Supervisor-Veto *structurally* satisfies it; Debate and SharedPool observe-only — the gap is the experimental contrast.

## 🛠 Configuration Tips

Latency tuning (speech end detection): tweak in audio_handler.py

TRAIL_MS (silence tail), POLL_MS, ENERGY_MIN_START/KEEP

Interrupt while speaking (chat mode): user can say “stop / skip / next”; the client listens in a side thread and calls tts.stopAll()

Prevent self-hearing: during TTS, temporarily lower input sensitivity or gate by energy threshold; client already filters short clips and uses brief listen windows for interrupts.

# ❓ FAQ

**Why did chatbot not retrieve from the knowledge base?**
The chatbot agent queries Vertex AI Search. Without GCP auth it returns "I'm not sure" for factual questions; other agents still work fine. Fix: `gcloud auth application-default login` once, or set `GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json` for prod.

**Where do I add MSU CS docs?**
Ingest into the Vertex AI Search datastore identified by `GOOGLE_CLOUD_PROJECT` / `VERTEX_LOCATION` / `VERTEX_DATASTORE_ID`. The default datastore is CS Navigator's `csnavigator-kb-v7`.

**It stops recording too fast/too slow.**
Adjust TRAIL_MS, NO_SPEECH_TIMEOUT_S, and thresholds in audio_handler.py.

**What is SAGE-CBT?**
It's the active research thesis on this repo — a Supervisor-Veto multi-agent architecture for CBT dialogue with a runtime-monitorable safety invariant, benchmarked against Debate and SharedPool baselines. Full spec in [PRD.md](PRD.md). When `SAGE_TOPOLOGY` is unset, the server behaves exactly as before; the research layer is strictly additive.

---

## 🏗 System Architecture (C4 Container View)

```mermaid
flowchart LR
    classDef person fill:#08427b,stroke:#073b6f,color:#fff
    classDef robot fill:#1168bd,stroke:#0d5aa5,color:#fff
    classDef server fill:#2e7d32,stroke:#2e7d32,color:#fff
    classDef db fill:#6a1b9a,stroke:#6a1b9a,color:#fff
    classDef ext fill:#ef6c00,stroke:#ef6c00,color:#fff
    classDef opt stroke-dasharray: 5 5,fill:#b71c1c,color:#fff

    USER([👤 User<br/>Morgan CS community]):::person

    subgraph NAO[NAO Robot — Python 2.7 / naoqi]
        WAKE[wake_listener]:::robot
        CONV[conversation loop<br/>record + snap + POST]:::robot
        EXEC[nao_execute<br/>action dispatcher]:::robot
    end

    subgraph SRV[Flask Server — Python 3.11+]
        API["POST /turn · /stream_turn"]:::server
        GATE[safety.crisis_check]:::server
        TOPO[topology dispatcher<br/>SAGE-CBT]:::server
        RUN[Agents SDK Runner]:::server
        SESS[(SQLiteSession<br/>nao.db)]:::db
        INV[invariant monitor]:::server
    end

    OAI[(OpenAI<br/>Whisper + GPT-4o)]:::ext
    VAI[(Vertex AI Search<br/>csnavigator-kb-v7)]:::ext
    ANT[(Anthropic<br/>claude-opus-4-7)]:::opt

    USER -- voice --> WAKE
    WAKE --> CONV
    CONV -- multipart/form-data --> API
    API --> GATE --> TOPO --> RUN
    RUN --> SESS
    RUN --> OAI
    RUN --> VAI
    TOPO -. SAGE_SAFETY_PROVIDER=claude .-> ANT
    TOPO --> INV --> SESS
    API -- JSON reply + actions[] --> CONV
    CONV --> EXEC -- naoqi calls --> USER
```

## 🧱 Technology Stack

```mermaid
flowchart TB
    classDef ui fill:#1565c0,stroke:#0d47a1,color:#fff
    classDef orch fill:#2e7d32,stroke:#1b5e20,color:#fff
    classDef llm fill:#6a1b9a,stroke:#4a148c,color:#fff
    classDef data fill:#ef6c00,stroke:#e65100,color:#fff
    classDef infra fill:#455a64,stroke:#263238,color:#fff

    subgraph L1[Embodiment layer]
        direction LR
        N1[NAO H25<br/>naoqi 2.8]:::ui
        N2[Python 2.7<br/>NAO scripts]:::ui
        N3[ALFaceDetection<br/>ALTextToSpeech]:::ui
    end

    subgraph L2[Orchestration layer]
        direction LR
        O1[Flask 3.0]:::orch
        O2[openai-agents 0.13.6<br/>Runner + handoffs]:::orch
        O3[SQLiteSession]:::orch
        O4[SAGE-CBT topologies<br/>+ invariant monitor]:::orch
    end

    subgraph L3[Model layer]
        direction LR
        M1[Whisper-1<br/>STT]:::llm
        M2[GPT-4o<br/>chat + vision]:::llm
        M3[GPT-4o-mini<br/>router + workers]:::llm
        M4[Claude Opus 4.7<br/>optional ablation]:::llm
    end

    subgraph L4[Data layer]
        direction LR
        D1[Vertex AI Search<br/>Morgan CS KB]:::data
        D2[SQLite<br/>sessions + consent<br/>+ safety_events]:::data
    end

    subgraph L5[Tooling & eval]
        direction LR
        T1[pytest<br/>60+ tests]:::infra
        T2[red-team harness<br/>70 prompts]:::infra
        T3[matplotlib<br/>Pareto plot]:::infra
    end

    L1 --> L2 --> L3
    L2 --> L4
    L2 --> L5
```

## 🔁 Request Lifecycle — `/turn`

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant N as NAO (naoqi)
    participant F as Flask /turn
    participant G as crisis_check
    participant T as topology dispatcher
    participant R as Runner (Agents SDK)
    participant S as SafetyAgent
    participant I as invariant monitor
    participant DB as SQLiteSession

    U->>N: utterance + face visible
    N->>N: VAD record WAV + snap_quick JPEG
    N->>F: POST /turn (audio, image, username, hint)
    F->>F: Whisper transcribe
    F->>G: crisis_check(user_text)
    alt risk keywords or LLM positive
        G-->>F: HOTLINE_REPLY + crisis=true
        F-->>N: reply + suppress_image=true
        N->>U: 988 hotline TTS
    else clean
        G-->>F: ok
        F->>T: run_topology(agent, msg, ctx, sess)
        alt SAGE_TOPOLOGY=supervisor_veto
            T->>R: draft proposed_reply
            R-->>T: proposed_reply + tool calls
            T->>S: verdict(proposed_reply)
            alt allow
                S-->>T: allow
            else revise
                S-->>T: rewrite
            else escalate
                S-->>T: HOTLINE_REPLY + crisis_lockout
            end
        else passthrough / debate / shared_pool
            T->>R: Runner.run
            R-->>T: reply
        end
        T->>I: record_turn(turn_tuple)
        I->>DB: append safety_events + topology_trace
        T-->>F: reply + verdict + metadata
        F->>DB: session.append(history)
        F-->>N: JSON {reply, actions[], active_agent}
        N->>U: TTS + execute actions[] in order
    end
```

## 🧠 CBT Thought-Record State Machine

Beck's 7-column thought record as walked by `cbt_coach`. State persists on `SQLiteSession` so users can resume across turns.

```mermaid
stateDiagram-v2
    [*] --> situation : therapist handoff
    situation --> automatic_thought : describe event
    automatic_thought --> distortion_tag : identify_distortion
    distortion_tag --> emotion_rating : Burns taxonomy
    emotion_rating --> evidence_for : 0–100 intensity
    evidence_for --> evidence_against
    evidence_against --> balanced_thought : suggest_reframe
    balanced_thought --> re_rating
    re_rating --> [*] : log_emotion + recap
    re_rating --> situation : next record
```

## 🩺 Health Check

```bash
curl http://localhost:5000/health
# => {"ok":true}
```

## 📜 License

Released under the **MIT License**. See [LICENSE](LICENSE).

## 👨‍💻 Authors

- **Dr. Shuangbao "Paul" Wang – Faculty Advisor / Principal Investigator**  
  Chairperson, Department of Computer Science, Morgan State University

- **Aayush Shrestha – Lead Developer/ Research Assistant**  
  Morgan State University, Department of Computer Science  


