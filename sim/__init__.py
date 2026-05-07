"""Phase 10.5 Virtual NAO simulator package.

This package owns the ``sim/`` namespace (scenarios, telemetry, live driver,
fake naoqi). Phase 10.5 splits ownership across four worktrees; this
``__init__`` is intentionally minimal so any sibling module can land
independently without import-time circular dependencies.

Adding new scenarios? Drop a file into ``sim/scenarios/`` exposing a
``run(driver, telemetry) -> dict`` callable. The discovery in
``sim.scenarios.list_scenarios`` will pick it up automatically.
"""
