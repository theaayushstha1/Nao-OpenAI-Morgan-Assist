"""SAGE-CBT Pareto plotter.

Reads grade JSONs from a directory and produces a Pareto scatter plot:
  x-axis: unsafe_rate (lower is better, so right-lower is worse)
  y-axis: ctrs_proxy   (higher is better)

Expects 6 points in the full sweep (3 topologies × 2 budgets) but
works with however many it finds. Each point is labeled
`<topology>/<budget>`.

CLI:
    python tests/redteam/plot_pareto.py \
        --input logs/ \
        --out logs/pareto.png

Used by `run_all.sh` at the end of a full sweep. Kept dependency-light:
matplotlib only.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

# matplotlib is imported lazily so smoke tests that don't need plotting
# can still import the module.


_MARKER_BY_TOPOLOGY = {
    "supervisor_veto": "o",
    "debate": "s",
    "shared_pool": "^",
    "passthrough": "D",
}


def _load_grades(input_dir: Path) -> list[dict[str, Any]]:
    """Accept a directory (glob *.json) or a single JSON file."""
    if input_dir.is_file():
        return [json.loads(input_dir.read_text())]
    paths = sorted(glob.glob(str(input_dir / "*.json")))
    grades: list[dict[str, Any]] = []
    for p in paths:
        try:
            obj = json.loads(Path(p).read_text())
        except Exception as e:
            print(f"[plot] skipping {p}: {e}", file=sys.stderr)
            continue
        # A grade JSON must have unsafe_rate + ctrs_proxy + topology + budget.
        if all(k in obj for k in ("unsafe_rate", "ctrs_proxy", "topology", "budget")):
            grades.append(obj)
    return grades


def plot(grades: list[dict[str, Any]], out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.5, 5.5))

    for g in grades:
        topo = g["topology"]
        bud = g["budget"]
        x = g["unsafe_rate"]
        y = g["ctrs_proxy"]
        marker = _MARKER_BY_TOPOLOGY.get(topo, "x")
        # Single-turn = filled; multi-turn = hollow edge.
        if bud == "single":
            ax.scatter(x, y, s=140, marker=marker, label=f"{topo} (T=1)", edgecolors="black")
        else:
            ax.scatter(
                x, y, s=140, marker=marker, label=f"{topo} (T=2)",
                facecolors="white", edgecolors="black", linewidths=1.6,
            )
        ax.annotate(
            f"{topo}/{bud}",
            (x, y),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=8,
        )

    ax.set_xlabel("Unsafe-response rate  (lower is better →)")
    ax.set_ylabel("CTRS fidelity proxy  (↑ higher is better)")
    ax.set_title("SAGE-CBT Pareto: topology × adversary budget")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower left", fontsize=8, framealpha=0.9)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SAGE-CBT Pareto plot")
    p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Directory of grade JSONs (or a single JSON file).",
    )
    p.add_argument("--out", type=Path, default=Path("logs/pareto.png"))
    args = p.parse_args(argv)

    grades = _load_grades(args.input)
    if not grades:
        print("[plot] no grades found", file=sys.stderr)
        return 1

    plot(grades, args.out)
    print(f"[plot] wrote {args.out} from {len(grades)} grade files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
