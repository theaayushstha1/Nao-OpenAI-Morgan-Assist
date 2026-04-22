#!/usr/bin/env bash
# SAGE-CBT full red-team sweep: 3 topologies × 2 budgets = 6 conditions.
# Produces:  logs/redteam-<topo>-<budget>-<ts>.jsonl  (raw runs)
#            logs/grade-<topo>-<budget>.json          (scored runs)
#            logs/pareto.png                          (the central artifact)
#
# Usage:  tests/redteam/run_all.sh          # full sweep, real OpenAI calls
#         DRY_RUN=1 tests/redteam/run_all.sh  # plumbing only, no credits
#
# No retry logic — if a step fails the operator re-runs that condition by
# hand. Kept deliberately simple.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

TOPOLOGIES=("supervisor_veto" "debate" "shared_pool")
BUDGETS=("single" "multi")

DRY_RUN_FLAG=""
NO_LLM_FLAG=""
if [[ "${DRY_RUN:-}" == "1" ]]; then
    DRY_RUN_FLAG="--dry-run"
    NO_LLM_FLAG="--no-llm"
    echo "[run_all] DRY_RUN=1 — using stub topology + heuristic judge."
fi

GRADES_DIR="logs/grades"
mkdir -p "$GRADES_DIR"

for topo in "${TOPOLOGIES[@]}"; do
    for budget in "${BUDGETS[@]}"; do
        stamp="$(date +%Y%m%d-%H%M%S)"
        run_path="logs/redteam-${topo}-${budget}-${stamp}.jsonl"
        grade_path="${GRADES_DIR}/grade-${topo}-${budget}.json"

        echo "[run_all] ▶ $topo / $budget"
        python -m tests.redteam.runner \
            --topology "$topo" \
            --budget "$budget" \
            --out "$run_path" \
            $DRY_RUN_FLAG

        echo "[run_all] grading $run_path"
        python -m tests.redteam.grade \
            --run "$run_path" \
            --out "$grade_path" \
            $NO_LLM_FLAG
    done
done

echo "[run_all] plotting Pareto"
python tests/redteam/plot_pareto.py \
    --input "$GRADES_DIR" \
    --out logs/pareto.png

echo "[run_all] done. artifact: logs/pareto.png"
