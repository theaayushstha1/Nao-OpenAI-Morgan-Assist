# Phase 8 — Task Map & Contracts

> **Onboarding Polish.** Apply HRI research findings. Replace "say chat mode / therapy / skills" with content-inferred mode + minimal name onboarding. Multi-person disambiguation.

PRD: PRD_v2.md Phase 8.

## Branch policy
Worktree per agent off `dev/architecture-rework`. Commit message: `[Phase 8] <slug>: <summary>`.

## File ownership

| Slug | Files OWNED |
|------|-------------|
| `onboarding-flow` | `nao/utils/ask_name_utils.py` (rewrite — single combined prompt), `nao/wake_state.py` (extend — multi-person greeting + onboarding callback hooks), `nao/conversation.py` (legacy compat — onboarding helper used by main.py if WS hasn't fully replaced it) |
| `router-prompt-cleanup` | `server/agents/router.py` (prompt: emphasize content-based routing; remove "explicit mode keyword" requirement; multi-person scenario hint) |
| `onboarding-tests` | `server/tests/test_onboarding.py` (NEW) |

## Onboarding flow (HRI-research-driven)

### First-time user (no face match)
1. Face detected → AWARE → engagement gate fires → ENGAGED → soft chime.
2. After 1 s pause: TTS "Hi, I'm NAO. I haven't met you yet — what should I call you?"
3. Robot listens (LISTENING). User says name (any form).
4. `name_utils.extract_name` parses; if confidence low: "Sorry, did you say _X_?" — confirm; on yes proceed; on no re-ask.
5. Background thread learns face via `face_naoqi.learn_new_face_naoqi(name)` — silent, no extra prompt.
6. After confirmation + face-learn done: TTS "Got it, [name]. Pleasure to meet you."
7. Mode inference begins on first real conversational turn.

### Returning user (face match)
1. Face detected → AWARE → engaged → chime.
2. TTS "Welcome back, [name]." (text from server-side greeting).
3. Wait for speech.

### Group scenario (>1 face within 1.5 m)
1. Wake fires on closest face.
2. TTS "Hi everyone — who'd like to chat first?" (replaces solo greeting).
3. Listen normally; whoever speaks first wins the conversation.

### Mode inference (replaces "say chat / therapy")
- Router agent (server-side) picks the mode from the first user turn's CONTENT.
- Power-user shortcut retained: "switch to therapy" mid-conversation triggers handoff.

## Public API

### `nao/utils/ask_name_utils.py`
```python
def ask_name_combined(audio_streamer, ws_client, tts_player, on_name) -> None:
    """Single combined onboarding prompt + name extraction + face learn.

    Plays the heads-up + name prompt as one TTS call (locally on the robot
    if WS not connected; via TTS chunk if connected). Records once. Extracts
    name. Confirms once if confidence low. Calls on_name(name) when settled.

    Designed to fit inside a single ENGAGED→LISTENING transition in
    WakeStateMachine.
    """
```

### `nao/wake_state.py` extension
- Add `multi_person_callback` constructor kwarg — called when ≥ 2 faces detected within 1.5 m. Default impl logs.
- Expose `current_face_id` so onboarding flow can call `learn_new_face_naoqi`.
- New on_engaged signature: `(face_id, gate, confidence, distance_m, returning_user_hint=None)` — populated by main.py from brain cache check.

### `server/agents/router.py` — prompt cleanup
- Update SYSTEM prompt to:
  - "Decide which sub-agent should handle this — based on the CONTENT of the user's message, NOT on a mode keyword."
  - Examples: "What classes does Morgan offer?" → chatbot. "I'm feeling anxious." → therapist. "What time is it?" → skills. "Hi how are you?" → chat.
  - Mid-conversation: if user says "switch to therapy" / "let me ask a question" / etc., handoff.

## Reused-as-is
- All emotion/safety modules.

## Definition of done
1. Compile checks.
2. `ask_name_combined` works against synthetic audio + face stub.
3. Multi-person greeting fires on >=2 faces.
4. Router prompt no longer mentions "mode keyword".
5. Tests collect.
