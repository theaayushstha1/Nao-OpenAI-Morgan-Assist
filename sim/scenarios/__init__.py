"""Scenario registry + CLI runner for the Phase 10.5 simulator.

Each scenario is a Python module under ``sim/scenarios/`` whose filename
starts with two digits and an underscore (e.g. ``01_face_wake.py``). The
module must expose:

    def run(driver, telemetry) -> dict:
        '''Returns: {"scenario": str, "outcome": str, "details": dict, "telemetry_rows": list}'''

CLI:
    python -m sim.scenarios                  → prints the discovered scenarios
    python -m sim.scenarios 01_face_wake     → runs a single scenario
    python -m sim.scenarios all              → runs every scenario sequentially
"""
from __future__ import annotations

import importlib
import logging
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_log = logging.getLogger("sim.scenarios")

# Match files that look like a scenario: two digits, underscore, identifier,
# .py extension. Anything else (helpers like _driver.py, audio dir) is skipped.
_SCENARIO_RX = re.compile(r"^(\d{2}_[A-Za-z0-9_]+)\.py$")


def _scenarios_dir() -> Path:
    return Path(__file__).resolve().parent


def list_scenarios() -> list[str]:
    """Return the sorted list of scenario names (no .py extension)."""
    out: list[str] = []
    for p in _scenarios_dir().iterdir():
        if not p.is_file():
            continue
        m = _SCENARIO_RX.match(p.name)
        if m:
            out.append(m.group(1))
    out.sort()
    return out


def load(name: str) -> ModuleType:
    """Import-and-return the scenario module by name (e.g. '01_face_wake').

    Raises ``ModuleNotFoundError`` if the file isn't on disk and
    ``AttributeError`` if it's missing the ``run`` callable.
    """
    if not _SCENARIO_RX.match(f"{name}.py"):
        raise ValueError(
            f"scenario name {name!r} must match 'NN_identifier' (e.g. '01_face_wake')"
        )
    mod = importlib.import_module(f"sim.scenarios.{name}")
    if not hasattr(mod, "run") or not callable(getattr(mod, "run")):
        raise AttributeError(
            f"scenario module {mod.__name__!r} is missing required `run(driver, telemetry)`"
        )
    return mod


def _run_one(name: str) -> int:
    """Run a single scenario, write its CSV, print the report. Returns exit code."""
    # Import locally so module-import doesn't drag in the driver during a
    # bare `python -m sim.scenarios` listing call.
    try:
        from sim.scenarios._driver import Driver, DriverUnavailable
        from sim.telemetry import Telemetry
    except Exception as e:  # pragma: no cover — runtime missing-dep path
        print(f"sim.scenarios: cannot run scenarios — {e!r}")
        return 2

    try:
        mod = load(name)
    except (ModuleNotFoundError, AttributeError, ValueError) as e:
        print(f"sim.scenarios: load({name!r}) failed — {e!r}")
        return 2

    telemetry = Telemetry()
    driver = Driver()
    print(f"=== running scenario: {name} ===")
    try:
        result = mod.run(driver, telemetry)
    except DriverUnavailable as e:
        print(f"SKIPPED ({name}): {e}")
        return 0
    except Exception as e:  # noqa: BLE001
        _log.exception("scenario crashed: %s", name)
        print(f"FAIL ({name}): unhandled exception {e!r}")
        return 1
    finally:
        try:
            driver.close()
        except Exception:
            pass

    outcome = (result or {}).get("outcome", "unknown")
    details = (result or {}).get("details", {})
    print(f"--- {name}: outcome={outcome}")
    if details:
        for k, v in details.items():
            print(f"    {k}: {v!r}")
    print()
    print(telemetry.report())
    print(f"\nlatency csv: {telemetry.csv_path}")
    return 0 if outcome == "ok" else 1


def _main(argv: list[str]) -> int:
    if len(argv) <= 1:
        names = list_scenarios()
        if not names:
            print("(no scenarios found in sim/scenarios/)")
            return 0
        print("Available scenarios:")
        for n in names:
            print(f"  {n}")
        print()
        print("Run one:        python -m sim.scenarios <name>")
        print("Run them all:   python -m sim.scenarios all")
        return 0

    target = argv[1]
    if target in {"all", "*"}:
        rc = 0
        for n in list_scenarios():
            r = _run_one(n)
            if r != 0:
                rc = r
        return rc
    return _run_one(target)


__all__ = ["list_scenarios", "load"]


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
