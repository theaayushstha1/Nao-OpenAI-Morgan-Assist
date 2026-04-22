"""SAGE-CBT red-team harness (RQ1 central instrument).

Produces the Pareto plot (CTRS fidelity × unsafe-response rate) across
3 topologies × 2 adversary budgets. Also doubles as a regression test:
if Supervisor-Veto drops below its baseline, the build fails.

See PRD.md §7.3 and §8 for the experimental contract.
"""
from __future__ import annotations

__all__ = ["runner", "grade", "plot_pareto"]
