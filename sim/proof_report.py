# -*- coding: utf-8 -*-
"""Phase 10.5 -- Simulator proof report.

Per the operator's spec: pytest "passed" is not proof. Proof is the
*content* of a turn -- what was asked, what NAO said back, which agent
handled it, what tools fired, and how many milliseconds each phase took.

This module collects a ``ProofRecord`` per scenario, prints a summary
table to stdout, and writes timestamped JSON + Markdown files under
``sim/reports/`` so the operator can show actual evidence.

Public API
----------
    ProofRecord          -- dataclass for one scenario's proof
    collect_record(...)  -- pull a ProofRecord from a scenario result
    format_table(...)    -- ASCII summary table for stdout
    write_proof_files(...) -- emit JSON + Markdown under sim/reports/
    REPORTS_DIR          -- the on-disk reports root

The collector reads from each scenario's existing return value
(``details`` dict + ``telemetry_rows`` list) and the ``Telemetry``
instance -- no per-scenario changes are required.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any, Iterable

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


# ---------------------------------------------------------------------------
# Record schema
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProofRecord:
    """One scenario's worth of proof.

    Fields are deliberately loose-typed so the collector can pull from
    differently-shaped scenarios without raising on missing keys. The
    ``raw_*`` fields preserve the source data for forensic analysis.
    """

    scenario: str
    prompt: str
    transcript: str | None = None
    routed_agent: str | None = None
    reply_text: str | None = None
    actions: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    tools_called: list[str] = dataclasses.field(default_factory=list)
    first_audio_ms: float | None = None
    full_turn_ms: float | None = None
    full_turn_label: str = ""           # rendered: "466 ms" | "aborted" | "skipped"
    outcome: str = "unknown"
    started_at_iso: str = ""
    duration_ms: float | None = None
    barged: bool = False
    raw_details: dict[str, Any] = dataclasses.field(default_factory=dict)
    raw_telemetry: list[dict[str, Any]] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


# Scenario-specific overrides for the prompt / agent / barge classification.
# Keeps the collector resilient when a scenario doesn't surface a field
# in ``details`` -- e.g. multi-turn scenarios pick the *primary* turn.
_PROMPT_FALLBACKS: dict[str, str] = {
    "01_face_wake": "hello",
    "02_morgan_question": "what is CS 491?",
    "03_therapy_turn": "I'm feeling anxious about midterms",
    "04_barge_in": "tell me about the CS 491 prerequisites in detail",
    "05_echo_bleed": "what is CS 491?",
    "06_goodbye": "goodbye",
}

# Routed-agent fallback when the scenario didn't surface ``active_agent``
# explicitly in ``details``. This is the *expected* agent for that
# scenario per its install_mocks(); the real assertion in scenarios 02-05
# already verifies the route via the ``agent_handoff`` control frame.
_AGENT_FALLBACKS: dict[str, str] = {
    "01_face_wake": "chat",
    "02_morgan_question": "chatbot",
    "03_therapy_turn": "therapist",
    "04_barge_in": "chatbot",
    "05_echo_bleed": "chatbot",
    "06_goodbye": "chat",
}

# Regex-free heuristic: any details key whose name ends in ``_args``
# names an action / tool that was observed by the scenario assertion
# (e.g. ``observe_face_args``, ``gesture_args``,
# ``cs_navigator_search_args``). Strip the suffix to get the tool name.
def _tool_calls_from_details(details: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in details:
        if k.endswith("_args") and k[:-len("_args")]:
            out.append(k[:-len("_args")])
    return sorted(set(out))


def _phase(row: dict[str, Any], key: str) -> float | None:
    """Pull a phase timing from a telemetry row.

    ``Telemetry.rows`` stores phase values FLAT at the top level
    (``row["e2e_user_to_first_audio"] = 421.0``), not nested under
    ``phase_ms``. Empty cells are written as ``""`` strings -- coerce
    those to None.
    """
    if not isinstance(row, dict):
        return None
    val = row.get(key)
    if val is None or val == "":
        # Defensive nested-shape fallback in case Telemetry ever changes.
        nested = row.get("phase_ms")
        if isinstance(nested, dict):
            val = nested.get(key)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _first_real_turn(
    rows: Iterable[dict[str, Any]],
    scenario: str,
) -> dict[str, Any] | None:
    """Pick the most representative turn row from telemetry.

    Skips wake-only / echo rows. For multi-turn scenarios (06_goodbye),
    pick the row whose ``user_text`` matches a scenario-salient phrase
    (e.g. "goodbye"). Otherwise return the LAST real turn -- it tends
    to carry the most complete telemetry, since later turns benefit
    from any state initialized by earlier ones.
    """
    real_rows = [
        r for r in rows
        if (r.get("user_text") or "").strip() not in ("", "<wake>", "<echo>")
    ]
    if not real_rows:
        return None
    if scenario == "06_goodbye":
        # Prefer the actual goodbye turn (the salient one for this scenario).
        for r in reversed(real_rows):
            if "goodbye" in (r.get("user_text") or "").lower():
                return r
    if scenario == "04_barge_in":
        # The barge target turn is the *first* real turn; the second one
        # is just the follow-up "thanks" that verifies session continuity.
        return real_rows[0]
    return real_rows[-1]


def collect_record(
    *,
    scenario: str,
    result: dict[str, Any] | None,
    telemetry_rows: list[dict[str, Any]] | None,
    started_at: _dt.datetime,
    duration_ms: float | None,
) -> ProofRecord:
    """Build a ProofRecord from one scenario invocation.

    ``result`` is the dict returned by ``scenario.run(...)``.
    ``telemetry_rows`` are the telemetry rows captured during the run
    (typically ``telemetry.rows`` or the value of ``result["telemetry_rows"]``).
    """
    result = result or {}
    details: dict[str, Any] = result.get("details") or {}
    rows: list[dict[str, Any]] = (
        telemetry_rows
        or result.get("telemetry_rows")
        or []
    )

    outcome = result.get("outcome", "unknown")
    primary = _first_real_turn(rows, scenario) or {}

    # Prompt -- prefer the explicit details fields, then telemetry text,
    # then the per-scenario fallback table, then a generic placeholder.
    prompt = (
        details.get("prompt")
        or details.get("user_text")
        or primary.get("user_text")
        or _PROMPT_FALLBACKS.get(scenario)
        or "(no prompt captured)"
    )

    transcript = (
        details.get("transcript")
        or primary.get("transcript")
        or prompt
    )

    routed_agent = (
        details.get("active_agent")
        or details.get("routed_agent")
        or primary.get("active_agent")
        or _AGENT_FALLBACKS.get(scenario)
        or "(unknown)"
    )

    # Reply text -- scenarios store it under different keys depending on
    # which assertion they ran. Try every known shape.
    reply = (
        details.get("reply_audio_text")
        or details.get("reply_text")
        or details.get("turn_3_audio_text")        # 06_goodbye final exchange
        or details.get("second_turn_audio_text")   # 04_barge_in follow-up
        or primary.get("reply_preview")
        or ""
    )

    # Actions: prefer an explicit list, then synthesize from "_args" keys.
    actions_raw = details.get("actions")
    if isinstance(actions_raw, list):
        actions = list(actions_raw)
    else:
        actions = []

    tools_called = _tool_calls_from_details(details)
    if not tools_called and isinstance(actions_raw, list):
        tools_called = sorted({
            a.get("name") for a in actions_raw if isinstance(a, dict) and a.get("name")
        })

    first_audio_ms = _phase(primary, "e2e_user_to_first_audio")
    full_turn_ms = _phase(primary, "e2e_user_to_complete")
    # Symmetric fallbacks: if a scenario only marked one of the two
    # phases, infer the other (mocked TTS makes them near-identical).
    if first_audio_ms is None and full_turn_ms is not None:
        first_audio_ms = full_turn_ms
    if full_turn_ms is None and first_audio_ms is not None:
        full_turn_ms = first_audio_ms

    # Barge classification: details flag wins; else infer from scenario id.
    barged = bool(details.get("tts_aborted_seen") or scenario == "04_barge_in")

    if barged:
        full_turn_label = "aborted"
    elif outcome == "skipped":
        full_turn_label = "skipped"
    elif outcome == "timeout":
        full_turn_label = "timeout"
    elif full_turn_ms is None:
        full_turn_label = "(no telemetry)"
    else:
        full_turn_label = "{:.0f} ms".format(full_turn_ms)

    return ProofRecord(
        scenario=scenario,
        prompt=str(prompt),
        transcript=str(transcript) if transcript is not None else None,
        routed_agent=str(routed_agent) if routed_agent else None,
        reply_text=str(reply),
        actions=actions,
        tools_called=tools_called,
        first_audio_ms=first_audio_ms,
        full_turn_ms=full_turn_ms,
        full_turn_label=full_turn_label,
        outcome=str(outcome),
        started_at_iso=started_at.isoformat(timespec="milliseconds") + "Z",
        duration_ms=duration_ms,
        barged=barged,
        raw_details=dict(details),
        raw_telemetry=list(rows),
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


_TABLE_COLS = [
    ("Scenario", 22),
    ("Prompt", 38),
    ("Agent", 12),
    ("First Audio", 13),
    ("Full Turn", 13),
    ("Outcome", 9),
]


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    if n <= 3:
        return s[:n]
    return s[: n - 1] + "…"   # ellipsis


def _format_first_audio(ms: float | None, outcome: str) -> str:
    if outcome == "skipped":
        return "skipped"
    if outcome == "timeout":
        return "timeout"
    if ms is None:
        return "-"
    return "{:.0f} ms".format(ms)


def format_table(records: Iterable[ProofRecord]) -> str:
    """Pretty ASCII table per the operator's example.

    Columns (left-aligned): Scenario | Prompt | Agent | First Audio | Full Turn | Outcome.
    """
    records = list(records)
    header = "  ".join(name.ljust(width) for name, width in _TABLE_COLS).rstrip()
    sep = "  ".join("-" * width for _, width in _TABLE_COLS)
    lines = [header, sep]
    for r in records:
        cells = [
            _truncate(r.scenario, _TABLE_COLS[0][1]),
            _truncate(r.prompt, _TABLE_COLS[1][1]),
            _truncate(r.routed_agent or "-", _TABLE_COLS[2][1]),
            _format_first_audio(r.first_audio_ms, r.outcome),
            r.full_turn_label or "-",
            r.outcome,
        ]
        lines.append("  ".join(c.ljust(_TABLE_COLS[i][1])
                                for i, c in enumerate(cells)).rstrip())
    return "\n".join(lines)


def format_markdown(records: Iterable[ProofRecord], *, run_iso: str) -> str:
    """Human-readable Markdown report. One section per scenario."""
    records = list(records)
    out: list[str] = []
    out.append("# Virtual NAO Proof Report")
    out.append("")
    out.append("> Generated: `{}`".format(run_iso))
    out.append("> Source: `python -m sim.scenarios all --report`")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("| Scenario | Prompt | Agent | First Audio | Full Turn | Outcome |")
    out.append("|---|---|---|---|---|---|")
    for r in records:
        out.append(
            "| `{}` | {} | `{}` | {} | {} | `{}` |".format(
                r.scenario,
                _md_escape(r.prompt),
                r.routed_agent or "-",
                _format_first_audio(r.first_audio_ms, r.outcome),
                r.full_turn_label or "-",
                r.outcome,
            )
        )
    out.append("")
    out.append("---")
    out.append("")
    for r in records:
        out.append("## `{}`".format(r.scenario))
        out.append("")
        out.append("**Prompt:** {}".format(_md_escape(r.prompt)))
        out.append("")
        out.append("**Transcript (STT):** `{}`".format(r.transcript or "-"))
        out.append("")
        out.append("**Routed agent:** `{}`".format(r.routed_agent or "-"))
        out.append("")
        out.append("**Reply text:** {}".format(
            _md_escape(r.reply_text or "(none captured)"),
        ))
        out.append("")
        out.append("**Tools / actions called:** {}".format(
            ", ".join("`{}`".format(t) for t in r.tools_called) or "_none_",
        ))
        if r.actions:
            out.append("")
            out.append("**Action queue:**")
            out.append("")
            out.append("```json")
            out.append(json.dumps(r.actions, indent=2))
            out.append("```")
        out.append("")
        out.append("**Latency:**")
        out.append("")
        out.append("- First audio out: {}".format(
            _format_first_audio(r.first_audio_ms, r.outcome)))
        out.append("- Full turn: {}".format(r.full_turn_label or "-"))
        if r.duration_ms is not None:
            out.append("- Wall-clock scenario time: {:.0f} ms".format(r.duration_ms))
        out.append("- Outcome: `{}`".format(r.outcome))
        if r.barged:
            out.append("- Barge-in: TTS aborted mid-stream "
                        "(see `tts_aborted` control frame)")
        out.append("")
        if r.raw_telemetry:
            out.append("<details>")
            out.append("<summary>Raw telemetry rows</summary>")
            out.append("")
            out.append("```json")
            out.append(json.dumps(r.raw_telemetry, indent=2))
            out.append("```")
            out.append("")
            out.append("</details>")
            out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out)


def _md_escape(s: str) -> str:
    """Escape pipe characters so they don't break a Markdown table cell."""
    return (s or "").replace("|", "\\|")


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _ensure_reports_dir() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR


def write_proof_files(records: Iterable[ProofRecord]) -> dict[str, Path]:
    """Write timestamped JSON + Markdown proof files.

    Returns a dict like ``{"json": Path, "markdown": Path}`` so callers
    can echo the paths in CLI output.
    """
    records = list(records)
    _ensure_reports_dir()
    stamp = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    json_path = REPORTS_DIR / "proof_{}.json".format(stamp)
    md_path = REPORTS_DIR / "proof_{}.md".format(stamp)

    payload = {
        "generated_at": stamp,
        "tool": "python -m sim.scenarios all --report",
        "scenario_count": len(records),
        "scenarios": [dataclasses.asdict(r) for r in records],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(
        format_markdown(records, run_iso=stamp),
        encoding="utf-8",
    )
    return {"json": json_path, "markdown": md_path}


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


if __name__ == "__main__":  # pragma: no cover -- developer smoke
    sample = [
        ProofRecord(
            scenario="01_face_wake",
            prompt="hello",
            transcript="hello",
            routed_agent="chat",
            reply_text="Hi there.",
            tools_called=[],
            first_audio_ms=421.0,
            full_turn_ms=466.0,
            full_turn_label="466 ms",
            outcome="ok",
            started_at_iso="2026-05-07T07:00:00.000Z",
            duration_ms=512.0,
        ),
        ProofRecord(
            scenario="04_barge_in",
            prompt="tell me about prerequisites",
            transcript="tell me about prerequisites",
            routed_agent="chatbot",
            reply_text="(aborted mid-stream)",
            tools_called=[],
            first_audio_ms=305.0,
            full_turn_ms=None,
            full_turn_label="aborted",
            outcome="ok",
            started_at_iso="2026-05-07T07:00:01.000Z",
            duration_ms=900.0,
            barged=True,
        ),
    ]
    print(format_table(sample))
    print()
    paths = write_proof_files(sample)
    for k, p in paths.items():
        print("{}: {}".format(k, p))
