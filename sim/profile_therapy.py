# -*- coding: utf-8 -*-
"""Profile a single therapist turn down to per-OpenAI-call timing.

Hooks ``openai.resources.chat.completions.Completions.create`` and
``openai.resources.responses.Responses.create`` (the SDK's two LLM
entry points) to log every call's model, token count, and elapsed
time. Then runs the live therapist pipeline once and prints the
attribution.
"""
from __future__ import annotations

import base64
import functools
import os
import sys
import time
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Hook OpenAI client *before* importing server modules
# ─────────────────────────────────────────────────────────────────────────────


_calls: list[dict] = []


def _patch_openai_for_timing():
    """Monkeypatch the LLM entry points the Agents SDK uses (sync + async)."""
    import openai
    import openai.resources.chat.completions as _chat_compl
    targets = [
        ("Completions.create",
         _chat_compl.Completions, "create"),
        ("AsyncCompletions.create",
         _chat_compl.AsyncCompletions, "create"),
    ]
    try:
        import openai.resources.responses as _resp
        targets.append(
            ("Responses.create", _resp.Responses, "create")
        )
        targets.append(
            ("AsyncResponses.create", _resp.AsyncResponses, "create")
        )
    except (ImportError, AttributeError):
        pass

    import asyncio as _asyncio

    def _record(label, model, messages, dt, res, err):
        out_tok = 0
        try:
            if res is not None:
                out_tok = getattr(getattr(res, "usage", None),
                                   "completion_tokens", 0) or getattr(
                    getattr(res, "usage", None), "output_tokens", 0) or 0
        except Exception:
            pass
        _calls.append({
            "label": label,
            "model": model,
            "in_tokens_est": _approx_tokens(messages),
            "out_tokens": out_tok,
            "elapsed_ms": dt,
            "error": err,
        })

    for label, cls, attr in targets:
        original = getattr(cls, attr)

        if _asyncio.iscoroutinefunction(original):
            @functools.wraps(original)
            async def wrapped_async(self, *args, _orig=original, _label=label, **kwargs):
                t0 = time.perf_counter()
                model = kwargs.get("model") or (args[0] if args else "?")
                messages = kwargs.get("messages") or kwargs.get("input") or []
                err = None; res = None
                try:
                    res = await _orig(self, *args, **kwargs)
                    return res
                except Exception as e:
                    err = repr(e); raise
                finally:
                    dt = (time.perf_counter() - t0) * 1000.0
                    _record(_label, model, messages, dt, res, err)
            setattr(cls, attr, wrapped_async)
        else:
            @functools.wraps(original)
            def wrapped_sync(self, *args, _orig=original, _label=label, **kwargs):
                t0 = time.perf_counter()
                model = kwargs.get("model") or (args[0] if args else "?")
                messages = kwargs.get("messages") or kwargs.get("input") or []
                err = None; res = None
                try:
                    res = _orig(self, *args, **kwargs)
                    return res
                except Exception as e:
                    err = repr(e); raise
                finally:
                    dt = (time.perf_counter() - t0) * 1000.0
                    _record(_label, model, messages, dt, res, err)
            setattr(cls, attr, wrapped_sync)
    print(f"[profile] hooked {len(targets)} OpenAI entry points (sync + async)")


def _approx_tokens(messages: Any) -> int:
    """Cheap token estimate: char count / 4. Good enough for ranking."""
    if isinstance(messages, str):
        return max(1, len(messages) // 4)
    if isinstance(messages, list):
        total = 0
        for m in messages:
            if isinstance(m, dict):
                c = m.get("content") or ""
                if isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict):
                            t = part.get("text") or part.get("input_text") or ""
                            total += len(str(t))
                else:
                    total += len(str(c))
            else:
                total += len(str(m))
        return max(1, total // 4)
    return 0


def _human_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms/1000:.2f} s"


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    _patch_openai_for_timing()

    # Import server modules *after* the hook is installed so the SDK
    # uses our wrapped methods.
    from server import _legacy_helpers as legacy

    img_path = os.environ.get("LIVE_PROOF_IMAGE", "/tmp/live_proof_face.jpg")
    image_b64 = None
    if os.path.exists(img_path):
        with open(img_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("ascii")
        print(f"[profile] using image: {img_path}")
    else:
        print(f"[profile] no image at {img_path} (text-only run)")

    transcript = os.environ.get(
        "PROFILE_TRANSCRIPT",
        "I'm feeling really anxious about my midterms",
    )
    print(f"[profile] transcript: {transcript!r}")
    print()

    t_total_0 = time.perf_counter()
    reply, agent, actions, _ = legacy.run_agent(
        username="profile_user",
        hint="therapy",
        transcript=transcript,
        image_b64=image_b64,
    )
    total_ms = (time.perf_counter() - t_total_0) * 1000.0

    # Print breakdown
    print()
    print("=" * 80)
    print(f"TOTAL run_agent elapsed: {_human_ms(total_ms)}")
    print(f"Final agent: {agent!r}    actions: {[a.get('name') for a in (actions or [])]}")
    print(f"Reply: {(reply or '').strip()[:200]}")
    print()
    print("OpenAI calls (chronological):")
    print()
    print("  #   Model                              In-tok  Out-tok  Elapsed")
    print("  --  ---------------------------------  ------  -------  --------")
    cum = 0.0
    for i, c in enumerate(_calls, 1):
        cum += c["elapsed_ms"]
        model = c["model"][:33]
        print(f"  {i:>2}  {model:<33}  {c['in_tokens_est']:>6}  "
              f"{c['out_tokens']:>7}  {_human_ms(c['elapsed_ms']):>8}")
        if c["error"]:
            print(f"      ! {c['error']}")
    print()
    overhead = total_ms - cum
    print(f"  Sum of OpenAI calls:  {_human_ms(cum):>8}")
    print(f"  Overhead (non-LLM):   {_human_ms(overhead):>8}")
    print(f"  Total wall-clock:     {_human_ms(total_ms):>8}")
    print()

    # By model summary
    by_model: dict[str, dict[str, float]] = {}
    for c in _calls:
        by_model.setdefault(c["model"], {"count": 0, "ms": 0.0, "out_tok": 0})
        by_model[c["model"]]["count"] += 1
        by_model[c["model"]]["ms"] += c["elapsed_ms"]
        by_model[c["model"]]["out_tok"] += c["out_tokens"]
    print("By model:")
    print()
    print("  Model                              Calls   Total time     Total out tok")
    print("  ---------------------------------  -----   ------------   -------------")
    for m, agg in sorted(by_model.items(), key=lambda kv: kv[1]["ms"], reverse=True):
        print(f"  {m[:33]:<33}  {agg['count']:>5}   {_human_ms(agg['ms']):>12}   "
              f"{int(agg['out_tok']):>13}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
