"""Prometheus metrics for NAO Morgan Assist v2.

Defines a dedicated `CollectorRegistry` (not the global default) so tests
can exercise this module without polluting other importers. Exposes a few
counters/gauges and a histogram with label `phase`, plus context managers
that time a code block and observe into the histogram.

Allowed phase labels (hardcoded — typos are rejected at the call site so
they're caught in dev rather than silently producing a phantom series):

    Phase 1 (originals):
        vad
        stt
        crisis_check
        motion_trigger
        agent_first_token
        agent_complete
        tts_synth_first_chunk
        tts_synth_total
        action_dispatch
        e2e_user_to_first_audio
        e2e_user_to_complete

    Phase 9 extension (deferred from Phases 2-8):
        vad_silero_decide
        eou_arbiter
        semantic_endpoint_call
        vision_call
        cs_navigator_call
        gesture_dispatch
        sound_localize_react
        face_detect
        wake_to_engaged
        engaged_to_first_audio
        wake_to_first_audio

Histogram buckets (ms): [50, 100, 200, 400, 800, 1500, 3000, 8000, +Inf]

Public API:
    PROM_REGISTRY                   prometheus_client.CollectorRegistry
    latency_ms                      Histogram(label=phase)
    turns_total                     Counter(labels=outcome, agent)
    crisis_blocks_total             Counter
    motion_short_circuits_total     Counter
    ws_connections_active           Gauge

    # Phase 9 additions:
    wake_events_total               Counter(labels=gate)
    camera_state_changes_total      Counter(labels=new_state)
    brain_sync_pushes_total         Counter(labels=direction)
    gesture_calls_total             Counter(labels=intent)

    phase_timer(phase) -> contextmanager  — sync timing wrapper
    aphase_timer(phase) -> async cm       — async-safe variant
    render_metrics() -> (bytes, content_type)  — for /metrics endpoint

If you call `phase_timer("vaad")` (typo), it raises ValueError immediately;
the metric is not silently created. This is deliberate: a phantom bucket
in production is worse than a noisy crash in dev.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


# -- Allowed phase labels ----------------------------------------------------
#
# Hardcoded so a typo at the call site raises rather than creating a phantom
# series in Prometheus. If a new phase legitimately needs to be tracked, add
# it here AND in the relevant phase task map (docs/PHASE_<N>_TASK_MAP.md) —
# the two MUST stay in sync.
#
# Phase 1 (original 11): the core voice loop — VAD/STT/agent/TTS/E2E.
# Phase 9 extension (11 more): labels deferred by Phases 2-8 because the
# call sites landed before this whitelist was extended. Adding them here
# unlocks `phase_timer("vision_call")` etc. without re-touching every site.
ALLOWED_PHASES: frozenset[str] = frozenset({
    # -- Phase 1 (originals) --
    "vad",
    "stt",
    "crisis_check",
    "motion_trigger",
    "agent_first_token",
    "agent_complete",
    "tts_synth_first_chunk",
    "tts_synth_total",
    "action_dispatch",
    "e2e_user_to_first_audio",
    "e2e_user_to_complete",
    # -- Phase 9 extension (deferred from Phases 2-8) --
    "vad_silero_decide",        # Phase 2: Silero VAD per-frame decision
    "eou_arbiter",              # Phase 2: end-of-utterance arbiter call
    "semantic_endpoint_call",   # Phase 2: semantic endpoint LLM check
    "vision_call",              # Phase 6: GPT-4o vision round-trip
    "cs_navigator_call",        # Phase 5: CS navigator tool call
    "gesture_dispatch",         # Phase 4: gesture dispatcher
    "sound_localize_react",     # Phase 4: sound localization reaction
    "face_detect",              # Phase 3: face detection pass
    "wake_to_engaged",          # E2E sub-window: wake -> engaged state
    "engaged_to_first_audio",   # E2E sub-window: engaged -> first TTS audio
    "wake_to_first_audio",      # E2E sub-window: wake -> first TTS audio
})

# Bucket edges in milliseconds. The last bucket (+Inf) is automatic.
_LATENCY_BUCKETS_MS: tuple[float, ...] = (50, 100, 200, 400, 800, 1500, 3000, 8000)


# -- Dedicated registry ------------------------------------------------------
#
# We use our own CollectorRegistry instead of prometheus_client.REGISTRY
# (the global default). Reasons:
#   1. Test isolation — pytest can construct fresh metrics per test without
#      hitting "Duplicated timeseries" errors from the global default.
#   2. Avoids polluting the default registry if some other component in this
#      process (a library, a scheduler) registers its own metrics there.
#   3. Makes /metrics a deliberate, scoped exposition: only what this module
#      registers is exported.
PROM_REGISTRY: CollectorRegistry = CollectorRegistry(auto_describe=True)


# -- Metric definitions ------------------------------------------------------

latency_ms: Histogram = Histogram(
    "nao_phase_latency_ms",
    "Per-phase latency in milliseconds for one voice turn",
    ["phase"],
    buckets=_LATENCY_BUCKETS_MS,
    registry=PROM_REGISTRY,
)

turns_total: Counter = Counter(
    "nao_turns_total",
    "Count of completed voice turns",
    ["outcome", "agent"],  # outcome ∈ {ok, rejected, crisis, motion_short_circuit, error}
    registry=PROM_REGISTRY,
)

crisis_blocks_total: Counter = Counter(
    "nao_crisis_blocks_total",
    "Number of turns blocked by safety.crisis_check before agent dispatch",
    registry=PROM_REGISTRY,
)

motion_short_circuits_total: Counter = Counter(
    "nao_motion_short_circuits_total",
    "Number of turns answered by motion_trigger.detect (no LLM)",
    registry=PROM_REGISTRY,
)

ws_connections_active: Gauge = Gauge(
    "nao_ws_connections_active",
    "Number of currently-open WebSocket sessions",
    registry=PROM_REGISTRY,
)


# -- Phase 9 additions -------------------------------------------------------
#
# These four counters land alongside the Phase 1 core so dashboards can
# track operational events that the histogram doesn't capture: wake-gate
# selection, camera consent flips, brain-sync direction, and per-intent
# gesture invocations. Labels are intentionally low-cardinality (each
# label set has < 10 values) so we don't blow up the Prometheus index.

wake_events_total: Counter = Counter(
    "nao_wake_events_total",
    "Wake events by gate",
    # gate ∈ {motion, voice, manual, ...} — Phase 3/4 wake sources
    labelnames=("gate",),
    registry=PROM_REGISTRY,
)

camera_state_changes_total: Counter = Counter(
    "nao_camera_state_changes_total",
    "Camera consent flips",
    # new_state ∈ {on, off} — every toggle of camera_consent in user_prefs
    labelnames=("new_state",),
    registry=PROM_REGISTRY,
)

brain_sync_pushes_total: Counter = Counter(
    "nao_brain_sync_pushes_total",
    "Brain sync pushes by direction",
    # direction ∈ {server_to_robot, robot_to_server} — Phase 7 brain cache
    labelnames=("direction",),
    registry=PROM_REGISTRY,
)

gesture_calls_total: Counter = Counter(
    "nao_gesture_calls_total",
    "Gesture tool calls",
    # intent ∈ {wave, nod, shake, clap, dance, point, ...} — Phase 4 dispatch
    labelnames=("intent",),
    registry=PROM_REGISTRY,
)


# -- Timing helpers ----------------------------------------------------------

def _validate_phase(phase: str) -> None:
    if phase not in ALLOWED_PHASES:
        raise ValueError(
            f"phase {phase!r} is not in the allowed set. "
            f"Use one of: {sorted(ALLOWED_PHASES)}"
        )


@contextmanager
def phase_timer(phase: str) -> Iterator[None]:
    """Synchronous: time the wrapped block, record into latency_ms{phase=phase}.

    Raises ValueError immediately if `phase` is not in ALLOWED_PHASES — we
    do this BEFORE entering the timed region so a typo never silently
    produces a phantom series.

    Example:
        with phase_timer("stt"):
            transcript = whisper(audio)
    """
    _validate_phase(phase)
    start_ns = time.perf_counter_ns()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        latency_ms.labels(phase=phase).observe(elapsed_ms)


@asynccontextmanager
async def aphase_timer(phase: str) -> AsyncIterator[None]:
    """Async-safe variant of phase_timer. Same validation, same observation.

    Example:
        async with aphase_timer("agent_first_token"):
            first_token = await stream.__anext__()
    """
    _validate_phase(phase)
    start_ns = time.perf_counter_ns()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        latency_ms.labels(phase=phase).observe(elapsed_ms)


# -- Exposition helper -------------------------------------------------------

def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint.

    FastAPI usage:
        @app.get("/metrics")
        def metrics():
            body, ct = render_metrics()
            return Response(content=body, media_type=ct)
    """
    body = generate_latest(PROM_REGISTRY)
    return body, CONTENT_TYPE_LATEST


__all__ = [
    "PROM_REGISTRY",
    "ALLOWED_PHASES",
    "latency_ms",
    "turns_total",
    "crisis_blocks_total",
    "motion_short_circuits_total",
    "ws_connections_active",
    # Phase 9 additions
    "wake_events_total",
    "camera_state_changes_total",
    "brain_sync_pushes_total",
    "gesture_calls_total",
    "phase_timer",
    "aphase_timer",
    "render_metrics",
]


# -- Self-check --------------------------------------------------------------

if __name__ == "__main__":
    # 1. phase_timer with a valid phase works.
    with phase_timer("vad"):
        time.sleep(0.001)

    # 2. phase_timer with a typo raises ValueError.
    try:
        with phase_timer("vaad"):
            pass
    except ValueError:
        pass
    else:
        raise SystemExit("FAIL: phase_timer('vaad') should have raised ValueError")

    # 2b. async variant validates too — exercise the path.
    import asyncio

    async def _async_check() -> None:
        async with aphase_timer("stt"):
            await asyncio.sleep(0.001)
        try:
            async with aphase_timer("nope"):
                pass
        except ValueError:
            return
        raise SystemExit("FAIL: aphase_timer('nope') should have raised ValueError")

    asyncio.run(_async_check())

    # 3. render_metrics returns non-empty bytes and the right content-type.
    body, content_type = render_metrics()
    if not isinstance(body, (bytes, bytearray)) or len(body) == 0:
        raise SystemExit("FAIL: render_metrics body empty / wrong type")
    if not content_type or "text/plain" not in content_type:
        raise SystemExit(f"FAIL: unexpected content_type {content_type!r}")
    if b"nao_phase_latency_ms_bucket" not in body:
        raise SystemExit("FAIL: rendered body did not include the latency histogram")

    # Counters too — bump one and verify it's exported.
    crisis_blocks_total.inc()
    body2, _ = render_metrics()
    if b"nao_crisis_blocks_total" not in body2:
        raise SystemExit("FAIL: crisis_blocks_total not exported")

    # 4. Phase 9 extension: every newly whitelisted phase label must work
    #    through phase_timer (no ValueError) and produce a histogram series.
    phase_9_labels = (
        "vad_silero_decide",
        "eou_arbiter",
        "semantic_endpoint_call",
        "vision_call",
        "cs_navigator_call",
        "gesture_dispatch",
        "sound_localize_react",
        "face_detect",
        "wake_to_engaged",
        "engaged_to_first_audio",
        "wake_to_first_audio",
    )
    for label in phase_9_labels:
        with phase_timer(label):
            pass  # observation alone is enough — buckets get a 0-ish sample

    body3, _ = render_metrics()
    for label in phase_9_labels:
        # Each label should appear at least once in the rendered histogram —
        # the simplest check is that the phase=<label> dimension shows up.
        needle = f'phase="{label}"'.encode("ascii")
        if needle not in body3:
            raise SystemExit(
                f"FAIL: phase {label!r} did not produce a latency_ms series"
            )

    # 5. Phase 9 counters: bump each with a representative label, then verify
    #    that both the metric name AND the labeled series are exported.
    wake_events_total.labels(gate="motion").inc()
    camera_state_changes_total.labels(new_state="on").inc()
    brain_sync_pushes_total.labels(direction="server_to_robot").inc()
    gesture_calls_total.labels(intent="wave").inc()

    body4, _ = render_metrics()
    expected = [
        (b"nao_wake_events_total", b'gate="motion"'),
        (b"nao_camera_state_changes_total", b'new_state="on"'),
        (b"nao_brain_sync_pushes_total", b'direction="server_to_robot"'),
        (b"nao_gesture_calls_total", b'intent="wave"'),
    ]
    for metric_name, label_marker in expected:
        if metric_name not in body4:
            raise SystemExit(f"FAIL: {metric_name!r} not exported")
        if label_marker not in body4:
            raise SystemExit(
                f"FAIL: {metric_name!r} missing expected label {label_marker!r}"
            )

    print("OK")
