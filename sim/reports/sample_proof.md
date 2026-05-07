# Virtual NAO Proof Report

> Generated: `20260507T112003Z`
> Source: `python -m sim.scenarios all --report`

## Summary

| Scenario | Prompt | Agent | First Audio | Full Turn | Outcome |
|---|---|---|---|---|---|
| `01_face_wake` | hello | `chat` | 439 ms | 439 ms | `ok` |
| `02_morgan_question` | what is CS 491? | `chatbot` | 19 ms | 19 ms | `ok` |
| `03_therapy_turn` | I'm feeling anxious about midterms | `therapist` | 19 ms | 19 ms | `ok` |
| `04_barge_in` | tell me about the CS 491 prerequisites in detail | `chatbot` | 326 ms | aborted | `ok` |
| `05_echo_bleed` | what is CS 491? | `chatbot` | 17 ms | 17 ms | `ok` |
| `06_goodbye` | goodbye | `chat` | 19 ms | 19 ms | `ok` |

---

## `01_face_wake`

**Prompt:** hello

**Transcript (STT):** `hello`

**Routed agent:** `chat`

**Reply text:** Hi there.

**Tools / actions called:** _none_

**Latency:**

- First audio out: 439 ms
- Full turn: 439 ms
- Wall-clock scenario time: 1113 ms
- Outcome: `ok`

<details>
<summary>Raw telemetry rows</summary>

```json
[
  {
    "timestamp_iso": "2026-05-07T07:19:58",
    "turn_idx": 0,
    "outcome": "ok",
    "user_text": "<wake>",
    "reply_preview": "wake handled",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": 15.217292006127536,
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:19:58",
    "turn_idx": 1,
    "outcome": "ok",
    "user_text": "hello",
    "reply_preview": "Hi there.",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": 439.0314169577323,
    "e2e_user_to_first_audio": 439.0287919668481,
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": 439.0147919766605,
    "tts_synth_first_chunk": 0.013999990187585354,
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  }
]
```

</details>

---

## `02_morgan_question`

**Prompt:** what is CS 491?

**Transcript (STT):** `what is CS 491?`

**Routed agent:** `chatbot`

**Reply text:** CS 491 is the senior capstone — a year-long team project.

**Tools / actions called:** `cs_navigator_search`

**Latency:**

- First audio out: 19 ms
- Full turn: 19 ms
- Wall-clock scenario time: 51 ms
- Outcome: `ok`

<details>
<summary>Raw telemetry rows</summary>

```json
[
  {
    "timestamp_iso": "2026-05-07T07:19:59",
    "turn_idx": 0,
    "outcome": "ok",
    "user_text": "<wake>",
    "reply_preview": "wake handled",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:19:59",
    "turn_idx": 1,
    "outcome": "ok",
    "user_text": "what is CS 491?",
    "reply_preview": "CS 491 is the senior capstone \u2014 a year-long team project.",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": 18.669000011868775,
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": 18.67758302250877,
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  }
]
```

</details>

---

## `03_therapy_turn`

**Prompt:** I'm feeling anxious about midterms

**Transcript (STT):** `I'm feeling anxious about midterms`

**Routed agent:** `therapist`

**Reply text:** That sounds heavy.

**Tools / actions called:** `gesture`, `observe_face`

**Latency:**

- First audio out: 19 ms
- Full turn: 19 ms
- Wall-clock scenario time: 51 ms
- Outcome: `ok`

<details>
<summary>Raw telemetry rows</summary>

```json
[
  {
    "timestamp_iso": "2026-05-07T07:19:59",
    "turn_idx": 0,
    "outcome": "ok",
    "user_text": "<wake>",
    "reply_preview": "wake handled",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:19:59",
    "turn_idx": 1,
    "outcome": "ok",
    "user_text": "I'm feeling anxious about midterms",
    "reply_preview": "That sounds heavy.",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": 18.989750009495765,
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": 18.986832990776747,
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  }
]
```

</details>

---

## `04_barge_in`

**Prompt:** tell me about the CS 491 prerequisites in detail

**Transcript (STT):** `tell me about the CS 491 prerequisites in detail`

**Routed agent:** `chatbot`

**Reply text:** You got it.

**Tools / actions called:** _none_

**Latency:**

- First audio out: 326 ms
- Full turn: aborted
- Wall-clock scenario time: 2031 ms
- Outcome: `ok`
- Barge-in: TTS aborted mid-stream (see `tts_aborted` control frame)

<details>
<summary>Raw telemetry rows</summary>

```json
[
  {
    "timestamp_iso": "2026-05-07T07:20:00",
    "turn_idx": 0,
    "outcome": "ok",
    "user_text": "<wake>",
    "reply_preview": "wake handled",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:20:01",
    "turn_idx": 1,
    "outcome": "ok",
    "user_text": "tell me about the CS 491 prerequisites in detail",
    "reply_preview": "barge_in observed",
    "action_dispatch": 646.2337080156431,
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": 326.33554097265005,
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:20:01",
    "turn_idx": 2,
    "outcome": "ok",
    "user_text": "thanks",
    "reply_preview": "You got it.",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": 23.34420901024714,
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  }
]
```

</details>

---

## `05_echo_bleed`

**Prompt:** what is CS 491?

**Transcript (STT):** `what is CS 491?`

**Routed agent:** `chatbot`

**Reply text:** CS 491 is the senior capstone, a year-long team project.

**Tools / actions called:** _none_

**Latency:**

- First audio out: 17 ms
- Full turn: 17 ms
- Wall-clock scenario time: 2264 ms
- Outcome: `ok`

<details>
<summary>Raw telemetry rows</summary>

```json
[
  {
    "timestamp_iso": "2026-05-07T07:20:01",
    "turn_idx": 0,
    "outcome": "ok",
    "user_text": "<wake>",
    "reply_preview": "wake handled",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:20:01",
    "turn_idx": 1,
    "outcome": "ok",
    "user_text": "what is CS 491?",
    "reply_preview": "CS 491 is the senior capstone, a year-long team project.",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": 16.511459019966424,
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:20:03",
    "turn_idx": 2,
    "outcome": "ok",
    "user_text": "<echo>",
    "reply_preview": "no echo replay",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  }
]
```

</details>

---

## `06_goodbye`

**Prompt:** goodbye

**Transcript (STT):** `goodbye`

**Routed agent:** `chat`

**Reply text:** Take care.

**Tools / actions called:** _none_

**Latency:**

- First audio out: 19 ms
- Full turn: 19 ms
- Wall-clock scenario time: 106 ms
- Outcome: `ok`

<details>
<summary>Raw telemetry rows</summary>

```json
[
  {
    "timestamp_iso": "2026-05-07T07:20:03",
    "turn_idx": 0,
    "outcome": "ok",
    "user_text": "<wake>",
    "reply_preview": "wake handled",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": "",
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:20:03",
    "turn_idx": 1,
    "outcome": "ok",
    "user_text": "hi",
    "reply_preview": "Hi there.",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": 21.580542030278593,
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:20:03",
    "turn_idx": 2,
    "outcome": "ok",
    "user_text": "what's the weather",
    "reply_preview": "Sunny and 72.",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": 20.39525000145659,
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  },
  {
    "timestamp_iso": "2026-05-07T07:20:03",
    "turn_idx": 3,
    "outcome": "ok",
    "user_text": "goodbye",
    "reply_preview": "Take care.",
    "action_dispatch": "",
    "agent_complete": "",
    "agent_first_token": "",
    "crisis_check": "",
    "cs_navigator_call": "",
    "e2e_user_to_complete": "",
    "e2e_user_to_first_audio": 19.014916964806616,
    "engaged_to_first_audio": "",
    "eou_arbiter": "",
    "face_detect": "",
    "gesture_dispatch": "",
    "motion_trigger": "",
    "semantic_endpoint_call": "",
    "sound_localize_react": "",
    "stt": "",
    "tts_synth_first_chunk": "",
    "tts_synth_total": "",
    "vad": "",
    "vad_silero_decide": "",
    "vision_call": "",
    "wake_to_engaged": "",
    "wake_to_first_audio": "",
    "_unknown_phases": {}
  }
]
```

</details>

---
