# -*- coding: utf-8 -*-
"""Chat-model A/B for the pure fast-chat lane.

Times each candidate model on 20 short chat prompts using the
``pure_chat_agent`` (no tools, tool_choice=none). Reports min / p50 /
p90 / p95 / max so the operator can pick by tail latency, not just
the average.

Skips STT and TTS — measures the MODEL phase only, since that's the
remaining variance source after Phase 11.7 + 11.8.

Usage:
    set -a; source .env; set +a
    python -m sim.chat_model_bench
    python -m sim.chat_model_bench --models gpt-4.1-nano,gpt-4o-mini,gpt-4.1-mini
    python -m sim.chat_model_bench --runs 1   # one pass per prompt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPORTS = Path(__file__).resolve().parent / "reports" / "chat_model_bench"

PROMPTS_20 = [
    "hi nao what's up",
    "tell me a joke",
    "what's your favorite color",
    "say something nice",
    "what's the weather like",
    "i'm feeling a little tired today",
    "do you like music",
    "what's two plus two",
    "what do you think about coffee",
    "tell me a fun fact",
    "what's your name",
    "where are you from",
    "good morning",
    "good afternoon",
    "thanks nao",
    "i'm bored",
    "what should i do today",
    "are you a robot",
    "do you like students",
    "what's something interesting",
]


async def measure_one(model_id: str, prompt: str) -> dict:
    """Run pure_chat_agent against one prompt with the given model.
    Returns a dict of timing fields."""
    from server.agents.chat import PURE_SYSTEM
    from agents import Agent, ModelSettings, Runner
    from openai.types.responses import ResponseTextDeltaEvent

    # Build a fresh agent per call so we can swap the model id without
    # mutating module-global state.
    agent = Agent(
        name="pure_chat_bench",
        instructions=PURE_SYSTEM,
        model=model_id,
        model_settings=ModelSettings(max_tokens=60, tool_choice="none"),
        tools=[],
    )

    t0 = time.perf_counter()
    t_first_token = None
    reply = []
    try:
        run = Runner.run_streamed(agent, prompt)
        async for ev in run.stream_events():
            if ev.type == "raw_response_event" and \
               isinstance(ev.data, ResponseTextDeltaEvent):
                if t_first_token is None:
                    t_first_token = time.perf_counter()
                reply.append(ev.data.delta or "")
        try:
            final_text = run.final_output_as(str)
        except Exception:
            final_text = "".join(reply)
        t_done = time.perf_counter()
        ok = True
        err = None
    except Exception as e:  # noqa: BLE001
        final_text = ""
        t_done = time.perf_counter()
        ok = False
        err = repr(e)

    return {
        "model": model_id,
        "prompt": prompt,
        "first_token_ms": round((t_first_token or t_done) * 1000.0
                                  - t0 * 1000.0, 1),
        "done_ms": round((t_done - t0) * 1000.0, 1),
        "reply": (final_text or "").strip(),
        "ok": ok,
        "error": err,
    }


def _stats(label: str, values: list[float]) -> str:
    if not values:
        return f"  {label:<22}  (no data)"
    s = sorted(values)
    p = lambda q: s[max(0, int(len(s) * q) - 1)]
    return (
        f"  {label:<22}  min={s[0]:>5.0f}  p50={statistics.median(s):>5.0f}"
        f"  p90={p(0.90):>5.0f}  p95={p(0.95):>5.0f}  max={s[-1]:>5.0f}"
        f"  mean={sum(s)/len(s):>5.0f}  n={len(s)}"
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--models",
        default="gpt-4.1-nano,gpt-4o-mini,gpt-4.1-mini",
    )
    p.add_argument("--runs", type=int, default=2,
                   help="repeats per prompt per model")
    p.add_argument("--prompts", default="",
                   help="semicolon-separated override list")
    args = p.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY required", file=sys.stderr)
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    prompts = (
        [s.strip() for s in args.prompts.split(";") if s.strip()]
        or PROMPTS_20
    )

    import datetime as _dt
    stamp = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = REPORTS / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Chat-model A/B @ {stamp} ===")
    print(f"    models:  {', '.join(models)}")
    print(f"    prompts: {len(prompts)}")
    print(f"    runs/prompt: {args.runs}")
    print()

    rows: list[dict] = []
    for prompt in prompts:
        for model in models:
            for _ in range(args.runs):
                r = await measure_one(model, prompt)
                rows.append(r)
                short = (r["reply"][:60] + "…") if len(r["reply"]) > 60 else r["reply"]
                marker = "✓" if r["ok"] else "✗"
                print(f"  {marker} [{model:<14}] {prompt!r:<40}  "
                      f"first={r['first_token_ms']:>5.0f}  "
                      f"done={r['done_ms']:>5.0f}  → {short!r}")

    # Aggregate
    print()
    print("=" * 90)
    print("PER-MODEL AGGREGATE (model-only latency, no STT, no TTS)")
    print("=" * 90)
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
    for model, batch in by_model.items():
        ok_rows = [r for r in batch if r["ok"]]
        firsts = [r["first_token_ms"] for r in ok_rows]
        dones = [r["done_ms"] for r in ok_rows]
        errors = len(batch) - len(ok_rows)
        print()
        print(f"  Model: {model}    samples: {len(batch)}    errors: {errors}")
        print(_stats("first_token_ms", firsts))
        print(_stats("done_ms",        dones))

    # Persist
    payload = {"generated_at": stamp, "models": models,
                "prompts": prompts, "runs_per_prompt": args.runs,
                "rows": rows}
    (run_dir / "results.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )
    print()
    print(f"json: {run_dir/'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
