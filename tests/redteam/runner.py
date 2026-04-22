"""SAGE-CBT red-team harness core.

CLI:
    python -m tests.redteam.runner \
        --topology supervisor_veto \
        --budget single \
        --out logs/redteam-YYYYMMDD-HHMM.jsonl

Supports --topology {supervisor_veto,debate,shared_pool,passthrough}
and --budget {single,multi}.

Implementation strategy (chosen as the cleaner of the two options flagged
in the task spec): we call the topology layer in-process rather than
going through the Flask /turn endpoint. Reasons:
  1. Avoids synthesizing WAV files just to be skipped by a test hook.
  2. Avoids the Whisper round-trip (saves credits + deterministic).
  3. The topology subagent's PRD-mandated contract is
     `run_topology(user_text, context) -> (reply, verdict, metadata)`,
     which is exactly the seam we need.

If `server.topologies` is not yet importable we fall back to the
existing agents graph so we can still exercise the harness wiring
(dry-run / passthrough mode).

Deterministic where possible: seed=1337, temperature=0 for judges.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SEED = 1337
random.seed(SEED)

# Path to the redteam directory (for CSV defaults).
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ───────── data classes ─────────

@dataclass
class TurnOutcome:
    """One turn's result from a topology run."""

    row_id: str
    category: str
    turn_index: int  # 0 for single-turn or turn1 of multi; 1 for turn2
    user_text: str
    reply: str
    verdict: str
    topology: str
    expected_verdict: str
    expected_lockout: bool
    final_turn: bool  # true for the scored turn


# ───────── topology invocation layer ─────────

def _maybe_load_real_runner():
    """Return a callable (user_text, username, topology) -> (reply, verdict_str, meta),
    or None if the real server stack isn't importable yet (no API key, missing
    module, etc.).

    The real topology layer's contract is
        run_topology(agent, message, *, context, session)
            -> (reply, last_agent_name, verdict_dict | None, metadata).
    We wrap it to the (reply, verdict_str, meta) shape the harness records,
    AND we re-run the existing pre-dispatch crisis gate so the sweep sees
    the same full pipeline production traffic does.
    """
    try:
        from server import safety, session as sess_mod
        from server.agents import pick_initial_agent
        from server.topologies import run_topology
    except Exception:
        return None

    def _bridge(user_text: str, username: str, topology: str):
        # Pre-dispatch crisis gate (matches server.py behavior exactly).
        crisis = safety.crisis_check(user_text)
        if crisis.positive:
            return (
                safety.HOTLINE_REPLY,
                "escalate",
                {
                    "pre_dispatch_crisis": True,
                    "crisis_source": crisis.source,
                    "topology": topology,
                    "lockout": True,
                },
            )

        sess = sess_mod.get_or_create_session(username)
        agent = pick_initial_agent(username, hint=None)
        ctx = {
            "username": username,
            "actions_queue": [],
            "emotion_log": [],
            "latest_image_b64": None,
            "suppress_image": False,
        }
        try:
            reply, last_name, verdict_dict, meta = run_topology(
                agent, user_text, context=ctx, session=sess,
            )
        except Exception as e:  # pragma: no cover - defensive
            return f"[harness_error] {e}", "error", {"error": str(e)}

        verdict_str = "allow"
        if isinstance(verdict_dict, dict) and "verdict" in verdict_dict:
            verdict_str = str(verdict_dict["verdict"])
        merged_meta = {
            "last_agent": last_name,
            "verdict_full": verdict_dict,
            **(meta or {}),
        }
        return reply, verdict_str, merged_meta

    return _bridge


def _stub_run_topology(user_text: str, context: dict, topology: str) -> tuple[str, str, dict]:
    """Placeholder topology runner used for dry-run and when the real
    topology layer hasn't been merged yet.

    Heuristically mirrors what the real Supervisor-Veto would do so that
    unit plumbing can be exercised without API calls. Not scientifically
    meaningful — the real numbers come from the real topology layer.
    """
    lower = user_text.lower()
    hard = (
        "kill myself", "end my life", "suicide", "suicidal", "want to die",
        "hurt myself", "cut myself", "cutting myself", "overdose", "ending it",
        "ending my life", "kill myself", "suicide plan", "kill me",
    )
    # crude "risk" heuristic
    risky = any(k in lower for k in hard) or "chest pain" in lower or "overdose" in lower
    if risky and topology == "supervisor_veto":
        return (
            "I hear you, and I'm really glad you're telling me. Please call or text 988 "
            "(Suicide and Crisis Lifeline). You don't have to be alone right now.",
            "escalate",
            {"stub": True, "lockout": True},
        )
    if risky and topology in ("debate", "shared_pool"):
        return (
            "That sounds really heavy. Can you tell me more about what's going on?",
            "allow",  # alternatives are weaker — by design, for the Pareto plot
            {"stub": True, "lockout": False},
        )
    # benign / adversarial: validating reply
    return (
        "That sounds really hard. I'm here. What feels heaviest about it right now?",
        "allow",
        {"stub": True, "lockout": False},
    )


def invoke_topology(
    user_text: str,
    username: str,
    topology: str,
    image_b64: str | None = None,
    use_stub: bool = False,
) -> tuple[str, str, dict]:
    """Dispatch one turn to the topology layer.

    Returns (reply, verdict, metadata). Never raises — on failure returns
    a best-effort tuple so the harness can keep marching.
    """
    ctx = {
        "username": username,
        "actions_queue": [],
        "emotion_log": [],
        "latest_image_b64": image_b64,
        "suppress_image": False,
    }
    if use_stub:
        return _stub_run_topology(user_text, ctx, topology)

    real = _maybe_load_real_runner()
    if real is None:
        # Server stack not importable — fall back to stub with a flag so the
        # harness plumbing still runs (useful for CI without an API key).
        reply, verdict, meta = _stub_run_topology(user_text, ctx, topology)
        meta = {**meta, "fallback": "server_stack_unavailable"}
        return reply, verdict, meta

    return real(user_text, username, topology)


# ───────── CSV loaders ─────────

def load_singleturn(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as f:
        for r in csv.DictReader(f):
            r["expected_lockout"] = str(r.get("expected_lockout", "")).strip().lower() == "true"
            rows.append(r)
    return rows


def load_multiturn(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as f:
        for r in csv.DictReader(f):
            r["expected_lockout_final"] = str(r.get("expected_lockout_final", "")).strip().lower() == "true"
            rows.append(r)
    return rows


# ───────── sweep drivers ─────────

def run_single(
    topology: str,
    rows: list[dict[str, Any]],
    out_path: Path,
    use_stub: bool,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Single-turn sweep: one /turn per prompt, fresh username per row."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with out_path.open("w") as f:
        for i, row in enumerate(rows):
            if limit is not None and i >= limit:
                break
            # Unique username so SQLiteSession doesn't leak across rows.
            username = f"redteam-{topology}-{row['id']}-{int(time.time())}"
            reply, verdict, meta = invoke_topology(
                user_text=row["prompt"],
                username=username,
                topology=topology,
                use_stub=use_stub,
            )
            rec = {
                "row_id": row["id"],
                "category": row["category"],
                "topology": topology,
                "budget": "single",
                "turn_index": 0,
                "user_text": row["prompt"],
                "reply": reply,
                "verdict": verdict,
                "expected_verdict": row["expected_verdict"],
                "expected_lockout": bool(row["expected_lockout"]),
                "final_turn": True,
                "meta": meta,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            results.append(rec)
            f.write(json.dumps(rec) + "\n")
            f.flush()
    return results


def run_multi(
    topology: str,
    rows: list[dict[str, Any]],
    out_path: Path,
    use_stub: bool,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Two-turn sweep. Both turns share the same username so session memory
    carries across the turn boundary (this is the attack vector)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with out_path.open("w") as f:
        for i, row in enumerate(rows):
            if limit is not None and i >= limit:
                break
            username = f"redteam-{topology}-{row['id']}-{int(time.time())}"
            # Turn 1: benign / setup.
            reply1, verdict1, meta1 = invoke_topology(
                user_text=row["turn1"],
                username=username,
                topology=topology,
                use_stub=use_stub,
            )
            rec1 = {
                "row_id": row["id"],
                "category": row["category"],
                "topology": topology,
                "budget": "multi",
                "turn_index": 0,
                "user_text": row["turn1"],
                "reply": reply1,
                "verdict": verdict1,
                "expected_verdict": "allow",
                "expected_lockout": False,
                "final_turn": False,
                "meta": meta1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            results.append(rec1)
            f.write(json.dumps(rec1) + "\n")

            # Turn 2: payload. This is the graded turn.
            reply2, verdict2, meta2 = invoke_topology(
                user_text=row["turn2"],
                username=username,
                topology=topology,
                use_stub=use_stub,
            )
            rec2 = {
                "row_id": row["id"],
                "category": row["category"],
                "topology": topology,
                "budget": "multi",
                "turn_index": 1,
                "user_text": row["turn2"],
                "reply": reply2,
                "verdict": verdict2,
                "expected_verdict": row["expected_verdict_final"],
                "expected_lockout": bool(row["expected_lockout_final"]),
                "final_turn": True,
                "meta": meta2,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            results.append(rec2)
            f.write(json.dumps(rec2) + "\n")
            f.flush()
    return results


# ───────── CLI ─────────

def _default_out(topology: str, budget: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Path("logs") / f"redteam-{topology}-{budget}-{stamp}.jsonl"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SAGE-CBT red-team runner")
    p.add_argument(
        "--topology",
        choices=("supervisor_veto", "debate", "shared_pool", "passthrough"),
        required=True,
    )
    p.add_argument("--budget", choices=("single", "multi"), required=True)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSONL path. Defaults to logs/redteam-<topo>-<budget>-<ts>.jsonl",
    )
    p.add_argument(
        "--single-csv",
        type=Path,
        default=_HERE / "prompts_singleturn.csv",
    )
    p.add_argument(
        "--multi-csv",
        type=Path,
        default=_HERE / "prompts_multiturn.csv",
    )
    p.add_argument("--limit", type=int, default=None, help="Cap rows for smoke test")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the stub topology runner; no OpenAI credits spent. "
             "Intended for plumbing smoke tests.",
    )
    args = p.parse_args(argv)

    # Wire SAGE_TOPOLOGY env var before any `server.server` import attempt.
    os.environ["SAGE_TOPOLOGY"] = args.topology

    out = args.out or _default_out(args.topology, args.budget)
    print(
        f"[runner] topology={args.topology} budget={args.budget} "
        f"dry_run={args.dry_run} out={out}",
        file=sys.stderr,
    )

    if args.budget == "single":
        rows = load_singleturn(args.single_csv)
        results = run_single(args.topology, rows, out, args.dry_run, args.limit)
    else:
        rows = load_multiturn(args.multi_csv)
        results = run_multi(args.topology, rows, out, args.dry_run, args.limit)

    print(f"[runner] wrote {len(results)} rows -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
