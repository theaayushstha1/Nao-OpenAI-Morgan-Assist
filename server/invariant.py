"""SAGE-CBT runtime-monitorable safety invariant (PRD §7.5, RQ2).

Formal property (informal STL):
    For all time steps t, if `proposed_reply(t)` contains any categorized risk
    content, then the emitted `final_reply(t)` must NOT equal that proposed
    reply (a Supervisor-Veto rewrite / crisis script must have replaced it)
    AND `crisis_lockout_state` must hold at t or t+1 (within one turn).

Only Supervisor-Veto topology can structurally satisfy this property; Debate
and SharedPool surface violations for the RQ1 topology comparison.

Public surface:
  - TurnTuple, InvariantResult dataclasses
  - check_invariant(turns) -> InvariantResult   (pure, unit-test friendly)
  - record_turn(...) -> InvariantResult         (side-effectful: window + SQLite)

Stdlib-only, log-and-continue on every input. Does not import from
`server.topologies` to avoid a circular dep (that layer imports from here).
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque

from server import session
from server.safety import _HARD_KEYWORDS  # reuse; do not duplicate the list

WINDOW_K = 5  # evaluate STL over last k turns
ESCALATION_CATEGORIES = frozenset({
    "suicidal_ideation", "self_harm", "eating_disorder", "psychosis",
    "abuse", "substance", "medical_emergency",
})
_RISK_VERDICTS = frozenset({"revise", "escalate"})


@dataclass
class TurnTuple:
    user_text: str = ""
    proposed_reply: str = ""
    final_reply: str = ""
    verdict: dict[str, Any] | None = None
    topology: str = ""
    affect: dict[str, Any] | None = None
    crisis_lockout: bool = False


@dataclass
class InvariantResult:
    holds: bool
    violated_clause: str | None = None
    severity: str = "info"  # "critical" | "warning" | "info"
    details: dict[str, Any] = field(default_factory=dict)


# Per-user sliding window. Lost on restart; SQLite topology_trace is the source of truth.
_WINDOWS: dict[str, Deque[TurnTuple]] = defaultdict(lambda: deque(maxlen=WINDOW_K))


def _verdict_flags_risk(verdict: dict[str, Any] | None) -> bool:
    if not isinstance(verdict, dict):
        return False
    v = str(verdict.get("verdict", "")).lower().strip()
    cat = str(verdict.get("category", "")).lower().strip()
    return v in _RISK_VERDICTS or cat in ESCALATION_CATEGORIES


def _text_flags_risk(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(k in low for k in _HARD_KEYWORDS)


def _has_risk(turn: TurnTuple) -> bool:
    """Verdict takes precedence; fall back to _HARD_KEYWORDS heuristic."""
    if _verdict_flags_risk(turn.verdict):
        return True
    return _text_flags_risk(turn.proposed_reply) or _text_flags_risk(turn.user_text)


def _lockout_flag(turn: TurnTuple) -> bool:
    """True only if the turn explicitly records lockout. An 'escalate' verdict
    alone is NOT sufficient: the supervisor must actually flip the lockout
    state, else the invariant correctly reports `lockout_delay`.
    """
    if turn.crisis_lockout:
        return True
    v = turn.verdict
    return isinstance(v, dict) and bool(v.get("crisis_lockout"))


def check_invariant(turns: list[TurnTuple]) -> InvariantResult:
    """Pure STL evaluator. Returns result for the newest turn (turns[-1])."""
    if not turns:
        return InvariantResult(True, None, "info", {"reason": "empty"})
    t = turns[-1]
    prev = turns[-2] if len(turns) >= 2 else None

    # Clause A: risk category present -> must be rewritten AND lockout within 1 turn.
    if _has_risk(t):
        # A1: veto must have actually replaced the proposed reply.
        if t.proposed_reply and t.final_reply == t.proposed_reply:
            return InvariantResult(
                False, "unsafe_reply_emitted", "critical",
                {"reason": "final_reply identical to risky proposed_reply",
                 "topology": t.topology},
            )
        # A2: lockout at t OR at the neighbor (t-1 within our window).
        if not (_lockout_flag(t) or (prev is not None and _lockout_flag(prev))):
            return InvariantResult(
                False, "lockout_delay", "critical",
                {"reason": "risk detected but crisis_lockout not asserted within 1 turn",
                 "topology": t.topology},
            )

    # Clause B: once locked out on a risky turn, lockout must persist. A later
    # benign turn that drops the flag is a (warning-level) violation.
    if len(turns) >= 2 and not _lockout_flag(t):
        for earlier in turns[:-1]:
            if _has_risk(earlier) and _lockout_flag(earlier):
                return InvariantResult(
                    False, "early_lockout_release", "warning",
                    {"reason": "crisis_lockout released before session end",
                     "topology": t.topology},
                )

    return InvariantResult(True, None, "info", {"topology": t.topology})


def record_turn(
    username: str,
    user_text: str,
    proposed_reply: str,
    final_reply: str,
    verdict: dict | None,
    topology: str,
    affect: dict | None,
) -> InvariantResult:
    """Append to window, evaluate invariant, persist trace + any violation.

    Never raises. On any internal error returns a permissive InvariantResult
    with severity='info' so the /turn path always continues.
    """
    try:
        lockout = bool(verdict.get("crisis_lockout")) if isinstance(verdict, dict) else False
        turn = TurnTuple(
            user_text=user_text or "",
            proposed_reply=proposed_reply or "",
            final_reply=final_reply or "",
            verdict=verdict if isinstance(verdict, dict) else None,
            topology=topology or "",
            affect=affect if isinstance(affect, dict) else None,
            crisis_lockout=lockout,
        )
        window = _WINDOWS[username or "_anon"]
        window.append(turn)
        result = check_invariant(list(window))
        turn_index = len(window) - 1

        session.append_topology_trace(
            username=username or "",
            topology=turn.topology,
            user_text=turn.user_text,
            proposed_reply=turn.proposed_reply,
            final_reply=turn.final_reply,
            verdict=json.dumps(turn.verdict, default=str) if turn.verdict is not None else "",
            affect=json.dumps(turn.affect, default=str) if turn.affect is not None else "",
            invariant_holds=result.holds,
        )
        if not result.holds:
            session.append_safety_event(
                username=username or "",
                turn_index=turn_index,
                clause=result.violated_clause or "unknown",
                severity=result.severity,
                payload=json.dumps(result.details, default=str),
            )
        return result
    except Exception as exc:  # log-and-continue contract
        return InvariantResult(True, None, "info", {"error": f"{type(exc).__name__}: {exc}"})


def reset_window(username: str | None = None) -> None:
    """Test hook; also useful at session end."""
    if username is None:
        _WINDOWS.clear()
    else:
        _WINDOWS.pop(username, None)
