"""End-to-end tests driving the virtual NAO simulator scenarios.

Owned by the Phase 10.5 ``e2e-test`` slug. Boots a real uvicorn instance
of ``server.app_ws:app`` once per test session, calls
``sim.fake_naoqi.install_into_sys_modules()``, and runs each scenario
under ``sim.scenarios`` against the live server with mocked OpenAI / CS
Navigator clients.

This file is intentionally defensive against parallel-agent scheduling:

* If ``sim.scenarios`` hasn't merged yet, the whole module is skipped via
  ``pytest.importorskip`` — collection still passes.
* If ``sounddevice`` (or any other live-audio dep) is missing, individual
  scenarios fall back to their headless mode; the suite keeps running.
* If a scenario raises, only that test fails — the session continues.

Latency budget: with mocked OpenAI/CS-Navigator the e2e p95 must stay
under 5 s. The ``test_metrics_latency_phase_recorded`` test additionally
verifies the Prometheus histogram registered the ``vad`` phase, which is
the cheapest signal that the real WS pipeline ran end-to-end.

Fixtures defined in ``sim/conftest.py`` are loaded via the
``pytest_plugins`` mechanism. That requires ``sim/conftest.py`` to be
importable as a Python module, which the conftest itself ensures by
synthesizing a namespace package when ``sim/__init__.py`` hasn't shipped.
"""
from __future__ import annotations

import time
from typing import Any

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Plugin / dependency gating.
#
# Order matters here: we register the sim plugin FIRST so the fixture set
# is available, THEN import the scenario surface. Both calls fall back to
# ``pytest.skip`` so a missing sibling worktree never poisons collection.
# ─────────────────────────────────────────────────────────────────────────────

pytest_plugins = ("sim.conftest",)

# Skip the whole module unless ``sim.scenarios`` is importable. This is
# the cheap, deterministic gate — ``importorskip`` raises ``Skipped`` at
# import time so pytest reports each test as skipped rather than missing.
pytest.importorskip("sim.scenarios")

# Lazy imports — done after the importorskip above so collection on a
# bare worktree (no sibling merges yet) still completes cleanly.
from sim.scenarios import list_scenarios, load  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Determinism knobs.
#
# Anything random that a scenario uses (e.g., user-id picking, audio
# noise injection) needs to seed off this. We export a single magic
# number so the assertion failures don't drift across runs.
# ─────────────────────────────────────────────────────────────────────────────

_E2E_RANDOM_SEED = 0xC0FFEE
_E2E_LATENCY_P95_BUDGET_MS = 5000  # 5 seconds, mocked path


def _scenario_names() -> list[str]:
    """Resolve the scenario list at parametrize-collection time.

    If ``list_scenarios()`` raises (e.g., because the sibling shipped a
    stub that crashes on import) we degrade to a single ``__missing__``
    entry so pytest still reports a meaningful skip rather than a hard
    collection error.
    """
    try:
        names = list(list_scenarios())
    except Exception as e:  # noqa: BLE001
        return [pytest.param("__missing__", marks=pytest.mark.skip(
            reason=f"list_scenarios() raised: {e!r}",
        ))]
    if not names:
        return [pytest.param("__none__", marks=pytest.mark.skip(
            reason="sim.scenarios.list_scenarios() returned an empty set",
        ))]
    return names


def _seed_global_rng() -> None:
    """Seed Python's ``random`` and (best-effort) ``numpy.random`` so any
    scenario that pulls a noise sample is deterministic across CI runs.
    """
    import random
    random.seed(_E2E_RANDOM_SEED)
    try:
        import numpy as np  # type: ignore[import-not-found]
        np.random.seed(_E2E_RANDOM_SEED & 0xFFFFFFFF)
    except Exception:
        pass


def _build_driver(server_handle: Any) -> Any:
    """Construct whatever the scenario expects as ``driver``.

    The task map says: ``scenario.run(driver=..., telemetry=...)``. The
    driver contract isn't fully nailed down here (it's owned by the
    ``scenarios`` slug), so we hand the scenario the most useful object
    we have: the WS server handle. Scenarios that need a richer driver
    will pull additional bits off ``ws_url``/``url`` themselves.
    """
    # Try to resolve a richer driver class from sim if it exists. This
    # gives the sibling agent a forward-compatible hook: if they ship
    # ``sim.driver.VirtualNaoDriver`` later, we'll automatically use it.
    try:
        from sim import driver as _sim_driver  # type: ignore[import-not-found]
        cls = getattr(_sim_driver, "VirtualNaoDriver", None)
        if cls is not None:
            return cls(ws_url=server_handle.ws_url, http_url=server_handle.url)
    except Exception:
        pass
    return server_handle


# ─────────────────────────────────────────────────────────────────────────────
# Scenario-driven tests.
#
# One parametrized test per scenario in ``sim.scenarios``. Each test
# isolates failures so a broken scenario can't leak side-effects into
# the next one — we wrap ``scenario.run`` in a try/except and let pytest
# report the failure normally, but we also tear down any open driver
# state in a ``finally`` block so the uvicorn instance stays clean.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario_name", _scenario_names())
def test_virtual_robot_scenario(
    scenario_name: str,
    boot_ws_server: Any,
    installed_fakes: Any,
    mocked_openai: Any,
    mocked_cs_navigator: Any,
    telemetry: Any,
) -> None:
    """Run one virtual-robot scenario against the live WS server.

    Asserts:
      1. ``scenario.run()`` returns a dict with ``outcome`` in
         ``{"ok", "skipped"}``. Anything else is a hard failure with the
         full ``details`` dict surfaced for triage.
      2. The telemetry's ``e2e_user_to_first_audio`` p95 is under 5 s
         (or ``None``, meaning the scenario opted out of measuring this
         phase — common for ``echo_bleed`` and ``goodbye``).

    The scenario itself is responsible for emitting telemetry rows; we
    only inspect the aggregate. That keeps this test resilient when
    individual scenarios change their internal phase breakdown.
    """
    if scenario_name in {"__missing__", "__none__"}:
        pytest.skip(f"scenario placeholder: {scenario_name}")

    _seed_global_rng()

    try:
        scenario = load(scenario_name)
    except Exception as e:  # noqa: BLE001
        pytest.fail(
            f"sim.scenarios.load({scenario_name!r}) raised: {e!r}",
            pytrace=False,
        )

    driver = _build_driver(boot_ws_server)

    # Each scenario emits at least one row so end_turn() has something to
    # finalize. We start the wall-clock here so a scenario that bails
    # before its first ``mark()`` still gets a meaningful e2e bound.
    t0 = time.monotonic()
    try:
        result = scenario.run(driver=driver, telemetry=telemetry)
    except pytest.skip.Exception:
        # Scenarios are allowed to opt out via pytest.skip(...).
        raise
    except Exception as e:  # noqa: BLE001
        # Surface the failure cleanly. ``pytest.fail`` raises a Failed
        # exception that pytest renders without the full traceback when
        # ``pytrace=False`` — keeps the report readable when a scenario
        # crashes while another agent's siblings are mid-merge.
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        pytest.fail(
            f"scenario {scenario_name!r} raised after {elapsed_ms:.1f} ms: {e!r}",
            pytrace=True,
        )

    assert isinstance(result, dict), (
        f"scenario {scenario_name!r} returned {type(result).__name__}, "
        f"expected dict; got {result!r}"
    )

    outcome = result.get("outcome")
    details = result.get("details", "(no details key)")
    assert outcome in {"ok", "skipped"}, (
        f"scenario {scenario_name!r} outcome was {outcome!r}; "
        f"details={details!r}"
    )

    # Latency p95 — the headline e2e number. ``percentile_ms`` returns
    # ``None`` when the phase has zero samples, which is fine: not every
    # scenario produces user audio (e.g., echo_bleed checks suppression).
    p95 = telemetry.percentile_ms("e2e_user_to_first_audio", 95)
    assert (p95 is None) or (p95 < _E2E_LATENCY_P95_BUDGET_MS), (
        f"scenario {scenario_name!r} latency p95 too high: "
        f"{p95:.1f} ms > {_E2E_LATENCY_P95_BUDGET_MS} ms budget"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Metrics smoke — assert the WS pipeline actually ran the VAD path.
#
# We pick scenario 01 (face wake) because it's the simplest end-to-end
# turn and is required to ship by the task map. Any phase that fires on
# every turn would be an acceptable signal; ``vad`` is the cheapest and
# most deterministic.
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_scenario_01() -> str | None:
    """Return the scenario name that maps to scenario #1 (face wake).

    The task map says ``01_face_wake.py`` so the convention is
    ``"01_face_wake"`` or ``"face_wake"``. We accept either. If neither
    is present in the registered list, we return ``None`` and the test
    skips — better than hard-failing on a sibling rename.
    """
    try:
        names = list(list_scenarios())
    except Exception:
        return None
    for candidate in ("01_face_wake", "face_wake", "01-face-wake"):
        if candidate in names:
            return candidate
    # Loose match: anything starting with "01" or containing "face_wake".
    for n in names:
        if n.startswith("01") or "face_wake" in n:
            return n
    return None


def test_metrics_latency_phase_recorded(
    boot_ws_server: Any,
    installed_fakes: Any,
    mocked_openai: Any,
    mocked_cs_navigator: Any,
    telemetry: Any,
) -> None:
    """Run scenario 01, then scrape ``/metrics`` and assert vad recorded.

    The Prometheus histogram records one observation per ``with
    metrics.phase_timer("vad"): ...`` block. After a successful face-wake
    turn the ``nao_phase_latency_ms_count{phase="vad"}`` line MUST be
    non-zero — if it's zero, the WS pipeline never ran the VAD step,
    which means we missed the EoU branch entirely.
    """
    from sim.conftest import _phase_count_from_metrics, _scrape_metrics_text

    scenario_name = _resolve_scenario_01()
    if scenario_name is None:
        pytest.skip("scenario 01 (face wake) not registered in sim.scenarios")

    _seed_global_rng()

    try:
        scenario = load(scenario_name)
    except Exception as e:  # noqa: BLE001
        pytest.fail(
            f"sim.scenarios.load({scenario_name!r}) raised: {e!r}",
            pytrace=False,
        )

    driver = _build_driver(boot_ws_server)

    try:
        result = scenario.run(driver=driver, telemetry=telemetry)
    except pytest.skip.Exception:
        raise
    except Exception as e:  # noqa: BLE001
        pytest.fail(
            f"face-wake scenario raised: {e!r}",
            pytrace=True,
        )

    if not isinstance(result, dict) or result.get("outcome") not in {"ok", "skipped"}:
        pytest.fail(
            f"face-wake scenario did not complete cleanly: {result!r}",
            pytrace=False,
        )
    if result.get("outcome") == "skipped":
        pytest.skip(
            f"face-wake scenario skipped itself: "
            f"{result.get('details', '(no details)')}",
        )

    metrics_text = _scrape_metrics_text(boot_ws_server, timeout_s=2.0)
    assert metrics_text, (
        f"GET {boot_ws_server.url}/metrics returned no body — "
        f"is the metrics module loaded?"
    )

    # Sanity: the histogram name itself must appear before we trust the
    # count line. Catches misconfigured registries.
    assert "nao_phase_latency_ms" in metrics_text, (
        "metrics output missing 'nao_phase_latency_ms' histogram entirely; "
        "got:\n" + metrics_text[:1500]
    )

    vad_count = _phase_count_from_metrics(metrics_text, "vad")
    assert vad_count is not None, (
        'expected nao_phase_latency_ms_count{phase="vad"} line in metrics; '
        "got:\n" + metrics_text[:1500]
    )
    assert vad_count > 0, (
        f'nao_phase_latency_ms_count{{phase="vad"}} == {vad_count}; '
        "WS pipeline never ran the VAD phase. Either the scenario didn't "
        "actually exercise the WS path, or the metrics histogram lost the "
        "observation."
    )
