"""Prometheus metrics for Phase 1 of NAO Morgan Assist v2.

Defines a dedicated `CollectorRegistry` (not the global default) so tests
can exercise this module without polluting other importers. Exposes a few
counters/gauges and a histogram with label `phase`, plus context managers
that time a code block and observe into the histogram.

Allowed phase labels (hardcoded — typos are rejected at the call site so
they're caught in dev rather than silently producing a phantom series):

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

Histogram buckets (ms): [50, 100, 200, 400, 800, 1500, 3000, 8000, +Inf]

Public API:
    PROM_REGISTRY                   prometheus_client.CollectorRegistry
    latency_ms                      Histogram(label=phase)
    turns_total                     Counter(labels=outcome, agent)
    crisis_blocks_total             Counter
    motion_short_circuits_total     Counter
    ws_connections_active           Gauge

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
# it here AND in docs/PHASE_1_TASK_MAP.md — the two MUST stay in sync.
ALLOWED_PHASES: frozenset[str] = frozenset({
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

    print("OK")
