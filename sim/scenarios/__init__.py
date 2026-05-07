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


def _run_one(name: str, *, collect: bool = False) -> tuple[int, Any]:
    """Run a single scenario, write its CSV, print the report.

    Returns ``(exit_code, ProofRecord_or_None)``. The ProofRecord is only
    populated when ``collect=True``; it captures prompt/agent/reply/timings
    so a downstream caller can build a summary report.
    """
    # Import locally so module-import doesn't drag in the driver during a
    # bare `python -m sim.scenarios` listing call.
    try:
        from sim.scenarios._driver import Driver, DriverUnavailable
        from sim.telemetry import Telemetry
    except Exception as e:  # pragma: no cover — runtime missing-dep path
        print(f"sim.scenarios: cannot run scenarios — {e!r}")
        return 2, None

    try:
        mod = load(name)
    except (ModuleNotFoundError, AttributeError, ValueError) as e:
        print(f"sim.scenarios: load({name!r}) failed — {e!r}")
        return 2, None

    telemetry = Telemetry()
    driver = Driver()
    print(f"=== running scenario: {name} ===")
    started = _datetime_utcnow()
    t_start = _monotonic()
    result: dict[str, Any] | None = None
    crashed_outcome: str | None = None
    try:
        result = mod.run(driver, telemetry)
    except DriverUnavailable as e:
        print(f"SKIPPED ({name}): {e}")
        crashed_outcome = "skipped"
        result = {"scenario": name, "outcome": "skipped",
                  "details": {"reason": str(e)},
                  "telemetry_rows": telemetry.rows}
    except Exception as e:  # noqa: BLE001
        _log.exception("scenario crashed: %s", name)
        print(f"FAIL ({name}): unhandled exception {e!r}")
        crashed_outcome = "fail"
        result = {"scenario": name, "outcome": "fail",
                  "details": {"reason": repr(e)},
                  "telemetry_rows": telemetry.rows}
    finally:
        try:
            driver.close()
        except Exception:
            pass
    duration_ms = (_monotonic() - t_start) * 1000.0

    outcome = (result or {}).get("outcome", "unknown")
    details = (result or {}).get("details", {})
    print(f"--- {name}: outcome={outcome}")
    if details:
        for k, v in details.items():
            print(f"    {k}: {v!r}")
    print()
    print(telemetry.report())
    print(f"\nlatency csv: {telemetry.csv_path}")

    record = None
    if collect:
        try:
            from sim.proof_report import collect_record
            record = collect_record(
                scenario=name,
                result=result,
                telemetry_rows=telemetry.rows,
                started_at=started,
                duration_ms=duration_ms,
            )
        except Exception as e:
            _log.warning("proof_report collect failed for %s: %r", name, e)

    rc = 0 if outcome in ("ok", "skipped") else 1
    if crashed_outcome == "skipped":
        rc = 0
    return rc, record


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
        print("Run + proof:    python -m sim.scenarios all --report")
        return 0

    target = argv[1]
    args = argv[2:]
    want_report = ("--report" in args) or ("-r" in args)

    if target in {"all", "*"}:
        rc = 0
        records: list[Any] = []
        for n in list_scenarios():
            r, rec = _run_one(n, collect=want_report)
            if r != 0:
                rc = r
            if rec is not None:
                records.append(rec)

        if want_report and records:
            try:
                from sim.proof_report import (
                    format_table, write_proof_files,
                )
            except Exception as e:
                print(f"\nproof report unavailable — {e!r}")
                return rc
            print()
            print("=" * 78)
            print("PROOF REPORT")
            print("=" * 78)
            print(format_table(records))
            paths = write_proof_files(records)
            print()
            print("proof JSON:     {}".format(paths["json"]))
            print("proof Markdown: {}".format(paths["markdown"]))
        return rc

    rc, rec = _run_one(target, collect=want_report)
    if want_report and rec is not None:
        try:
            from sim.proof_report import format_table, write_proof_files
            print()
            print(format_table([rec]))
            paths = write_proof_files([rec])
            print()
            print("proof JSON:     {}".format(paths["json"]))
            print("proof Markdown: {}".format(paths["markdown"]))
        except Exception as e:
            print(f"\nproof report write failed — {e!r}")
    return rc


# Imports kept down here so the bare ``list_scenarios()`` path stays
# import-cheap for callers that just want to enumerate.
from datetime import datetime as _datetime
from time import monotonic as _monotonic


def _datetime_utcnow():
    return _datetime.utcnow()


__all__ = ["list_scenarios", "load"]


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
