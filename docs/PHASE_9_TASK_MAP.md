<!--
title: Phase 9 — Task Map & Contracts
tags: [phase-9, task-map, testing, prometheus, grafana, observability]
related: [DECISIONS, PRD_v2, PHASE_1_TASK_MAP]
phase: "9"
status: shipped
-->

# Phase 9 — Task Map & Contracts (FINAL)

> **Test Hardening + Dashboards.** Whitelist all phase labels that prior phases deferred. Build a Grafana dashboard. Add concurrent-user tests, motion_trigger unit tests, extend the WS smoke test. Final stretch.

PRD: PRD_v2.md Phase 9.

## Branch policy
Worktree per agent off `dev/architecture-rework`. Commit message: `[Phase 9] <slug>: <summary>`.

## File ownership

| Slug | Files OWNED |
|------|-------------|
| `metrics-whitelist-extend` | `server/metrics.py` (extend ALLOWED_PHASES + add new metrics) |
| `grafana-dashboard` | `server/dashboards/grafana_voice.json` (NEW), `server/dashboards/README.md` (NEW) |
| `test-hardening` | `server/tests/test_motion_trigger.py` (NEW), `server/tests/test_concurrent_users.py` (NEW), `server/tests/test_ws_smoke.py` (extend with regression scenarios) |

## Phase labels to whitelist (added to `ALLOWED_PHASES`)

Add to existing 11:
- `vad_silero_decide`
- `eou_arbiter`
- `semantic_endpoint_call`
- `vision_call`
- `cs_navigator_call`
- `gesture_dispatch`
- `sound_localize_react`
- `face_detect`
- `wake_to_engaged`
- `engaged_to_first_audio`
- `wake_to_first_audio`

## New metrics to add

```python
wake_events_total = Counter("nao_wake_events_total", "Wake events by gate",
                             labelnames=("gate",), registry=PROM_REGISTRY)
camera_state_changes_total = Counter("nao_camera_state_changes_total",
                                      "Camera consent flips",
                                      labelnames=("new_state",), registry=PROM_REGISTRY)
brain_sync_pushes_total = Counter("nao_brain_sync_pushes_total",
                                   "Brain sync pushes by direction",
                                   labelnames=("direction",), registry=PROM_REGISTRY)
gesture_calls_total = Counter("nao_gesture_calls_total", "Gesture tool calls",
                               labelnames=("intent",), registry=PROM_REGISTRY)
```

## Grafana dashboard panels (`server/dashboards/grafana_voice.json`)

1. Latency p50/p95 per phase (histograms over `latency_ms`).
2. Turns per minute, by `outcome`.
3. Wake events per gate (rate per minute).
4. Crisis blocks total (counter; alert if > 0).
5. Echo cooldown drops (counter rate).
6. Camera state changes timeline.
7. Gesture intents histogram.
8. Brain sync pushes (server→robot direction).
9. CS Navigator call latency (p50/p95).
10. Vision call latency.

Plus an `alerts.yml` excerpt or one-line alert rule definitions in the README.

## Tests

### `test_motion_trigger.py` (NEW)
- Test every category of trigger:
  - posture (stand_up, sit_down, kneel)
  - gestures (wave, nod, shake, clap)
  - locomotion (forward, back, turn, spin)
  - performance (dance, follow)
  - LEDs (eyes_red, etc.)
  - camera (Phase 6: stop_watching_me, enable_camera)
- Plus negative tests for similar-sounding non-triggers.
- 20+ tests.

### `test_concurrent_users.py` (NEW)
- 5 simultaneous WS clients with different usernames; assert no state crosstalk (e.g., `_LAST_REPLY_CHUNKS[user_a]` not visible in user_b's session).
- 50 turns total spread across 5 sessions; latency stable.

### `test_ws_smoke.py` (extend)
- Add the camera-announce scenario (Phase 6) — server emits announce frame on first turn for new camera_consent=1 user.
- Add the brain_sync scenario (Phase 7) — server pushes brain_sync after session_open if updates exist.

## Definition of done
1. `python -m py_compile server/metrics.py`.
2. All deferred phase labels accepted by `phase_timer`.
3. Grafana JSON imports cleanly into a Grafana 11+ instance (validate via JSON schema or Grafana CLI if available; otherwise verify against the "datasource UID required" pattern).
4. Tests collect cleanly.
5. README documents how to run the dashboard locally.
