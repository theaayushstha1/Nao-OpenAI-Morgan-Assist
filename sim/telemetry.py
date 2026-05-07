"""Per-turn latency telemetry for the Phase 10.5 Virtual NAO simulator.

The ``Telemetry`` class accumulates per-phase timings for one voice turn,
appends a CSV row when the turn ends, and renders an ASCII summary table
on demand. Phase keys are validated against the 22 labels exposed by
``server.metrics.ALLOWED_PHASES`` — typos are warned-about (not raised)
because the simulator must keep running even when a scenario is sloppy.

The CSV is opened in append mode and protected by a lock so multiple
scenarios that share a Telemetry instance cannot interleave rows. A
header row is written exactly once, the first time the file is created.

CSV layout (24 columns):

    timestamp_iso, turn_idx, outcome, user_text, reply_preview,
    <22 phase columns, in ALLOWED_PHASES sort order>

Each phase column is a millisecond integer, or empty string if the
scenario didn't time that phase for this turn. Sorting the columns means
adding a new phase later won't reorder existing CSVs (we just get a new
trailing column whose absence reads cleanly as empty for old rows).
"""
from __future__ import annotations

import csv
import datetime as _dt
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Iterable

# ─────────────────────────────────────────────────────────────────────────────
# Resolve the canonical phase whitelist from server.metrics. We import lazily
# inside a try block: if the server module isn't importable (e.g. the sim is
# being smoke-tested in isolation), we fall back to an embedded copy that
# matches the PHASE_10_5_TASK_MAP.md spec at the time of writing. The
# embedded copy is the documented contract — the runtime fetch is a
# convenience so adding a phase to metrics.py auto-flows here.
# ─────────────────────────────────────────────────────────────────────────────


_FALLBACK_PHASES: frozenset[str] = frozenset({
    # Phase 1 originals
    "vad",
    "stt",
    "crisis_check",
    "motion_trigger",
    "agent_first_token",
    "agent_complete",
    "tts_synth_first_chunk",
    "tts_synth_total",
    "action_dispatch",
    "e2e_user_to_first_audio",
    "e2e_user_to_complete",
    # Phase 9 extension
    "vad_silero_decide",
    "eou_arbiter",
    "semantic_endpoint_call",
    "vision_call",
    "cs_navigator_call",
    "gesture_dispatch",
    "sound_localize_react",
    "face_detect",
    "wake_to_engaged",
    "engaged_to_first_audio",
    "wake_to_first_audio",
})


def _resolve_allowed_phases() -> frozenset[str]:
    try:  # pragma: no cover — exercised when server.metrics is on the path
        from server.metrics import ALLOWED_PHASES as _AP  # type: ignore
        return frozenset(_AP)
    except Exception:
        return _FALLBACK_PHASES


ALLOWED_PHASES: frozenset[str] = _resolve_allowed_phases()
_PHASE_COLUMNS: tuple[str, ...] = tuple(sorted(ALLOWED_PHASES))

_BASE_COLUMNS: tuple[str, ...] = (
    "timestamp_iso",
    "turn_idx",
    "outcome",
    "user_text",
    "reply_preview",
)
_HEADER: tuple[str, ...] = _BASE_COLUMNS + _PHASE_COLUMNS

_DEFAULT_CSV_PATH = "~/nao_assist/sim_latency.csv"

_log = logging.getLogger("sim.telemetry")


# ─────────────────────────────────────────────────────────────────────────────


class Telemetry:
    """Threadsafe per-turn latency recorder.

    Usage:
        t = Telemetry()
        t.start_turn(0, "what time is it")
        t.mark("stt", 312.4)
        t.mark("agent_first_token", 580.1)
        t.end_turn("ok", "It is 3 PM.")
        print(t.report())

    Notes:
      * ``start_turn`` is idempotent within a turn — you can call it again
        to reset (e.g. on a retry); the prior accumulator is dropped.
      * Phases not in ``ALLOWED_PHASES`` log a warning and are still
        accepted into the in-memory row, but are NOT written to CSV
        (CSV columns are fixed by the whitelist). This keeps the CSV
        schema stable while allowing operators to grep the warning if
        a typo silently degrades coverage.
      * ``end_turn`` flushes immediately so a hard kill mid-scenario
        still leaves a partial trace on disk.
    """

    def __init__(self, out_csv: str = _DEFAULT_CSV_PATH) -> None:
        # Resolve ~ early so the recorded path is unambiguous.
        self._csv_path: Path = Path(os.path.expanduser(out_csv))
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._rows: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._start_time: float | None = None  # not used for math; kept for API parity
        self._wrote_header = self._csv_path.exists() and self._csv_path.stat().st_size > 0

        if not self._wrote_header:
            with self._csv_path.open("a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(_HEADER)
            self._wrote_header = True

    # ── per-turn API ────────────────────────────────────────────────────

    def start_turn(self, turn_idx: int, user_text: str | None = None) -> None:
        """Begin a new turn record. Resets the per-turn phase accumulator."""
        with self._lock:
            self._current = {
                "turn_idx": int(turn_idx),
                "user_text": (user_text or "")[:200],
                "phases": {},
            }
            # Wall-clock-based start so a scenario can mark `e2e_*` itself.
            try:
                import time
                self._start_time = time.perf_counter()
            except Exception:
                self._start_time = None

    def mark(self, phase: str, ms: float) -> None:
        """Record a phase timing for the in-flight turn.

        If ``phase`` is not in ``ALLOWED_PHASES`` we warn and keep the
        sample in memory (so ``report()`` still surfaces it) but skip
        the CSV column write. Negative or non-finite values are coerced
        to 0.0 — these are programmer errors, not data we want to lose.
        """
        try:
            ms_val = float(ms)
            if ms_val < 0 or ms_val != ms_val:  # NaN check
                ms_val = 0.0
        except (TypeError, ValueError):
            ms_val = 0.0

        if phase not in ALLOWED_PHASES:
            _log.warning(
                "telemetry.unknown_phase phase=%r allowed_count=%d",
                phase, len(ALLOWED_PHASES),
            )

        with self._lock:
            if self._current is None:
                # Tolerant: a stray mark before start_turn just gets dropped
                # so a scenario crash doesn't poison the next turn.
                _log.warning("telemetry.mark_without_start phase=%r", phase)
                return
            self._current["phases"][phase] = ms_val

    def end_turn(self, outcome: str, reply_preview: str | None = None) -> None:
        """Finalize the current turn: append a row to CSV and the in-memory log."""
        with self._lock:
            if self._current is None:
                _log.warning("telemetry.end_without_start outcome=%r", outcome)
                return

            row: dict[str, Any] = {
                "timestamp_iso": _dt.datetime.now().isoformat(timespec="seconds"),
                "turn_idx": self._current["turn_idx"],
                "outcome": str(outcome),
                "user_text": self._current["user_text"],
                "reply_preview": (reply_preview or "")[:200],
                **{p: self._current["phases"].get(p, "") for p in _PHASE_COLUMNS},
            }
            # Keep the unknown-phase samples on the in-memory row so report()
            # can show them, but the CSV write only emits the whitelisted
            # columns.
            row["_unknown_phases"] = {
                k: v for k, v in self._current["phases"].items()
                if k not in ALLOWED_PHASES
            }
            self._rows.append(row)

            try:
                with self._csv_path.open("a", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerow([row.get(c, "") for c in _HEADER])
            except Exception as e:  # noqa: BLE001 — CSV failures must never crash a scenario
                _log.warning("telemetry.csv_write_failed error=%r path=%s", e, self._csv_path)

            self._current = None
            self._start_time = None

    # ── reporting ───────────────────────────────────────────────────────

    def report(self, last_n: int = 20, phases: Iterable[str] | None = None) -> str:
        """Return an ASCII table of the most recent ``last_n`` turns.

        Default columns: turn_idx, outcome, plus the six "core" phases the
        task map calls out as always-populated. ``phases`` overrides the
        column set when callers want to focus on a different slice.
        """
        if phases is None:
            phases = (
                "stt",
                "agent_first_token",
                "agent_complete",
                "tts_synth_first_chunk",
                "e2e_user_to_first_audio",
                "e2e_user_to_complete",
            )
        phases = list(phases)

        with self._lock:
            window = self._rows[-last_n:] if last_n > 0 else list(self._rows)

        if not window:
            return "(no telemetry rows yet)"

        # Build header
        cols = ["turn", "outcome"] + phases
        widths = [max(len(c), 5) for c in cols]
        # Column widths: max(header, longest data cell)
        for row in window:
            cells = [
                str(row["turn_idx"]),
                row["outcome"][:18],
            ] + [self._fmt_cell(row.get(p, "")) for p in phases]
            for i, c in enumerate(cells):
                widths[i] = max(widths[i], len(c))

        sep = "  "
        out_lines = [sep.join(c.ljust(w) for c, w in zip(cols, widths))]
        out_lines.append(sep.join("-" * w for w in widths))
        for row in window:
            cells = [
                str(row["turn_idx"]),
                row["outcome"][:18],
            ] + [self._fmt_cell(row.get(p, "")) for p in phases]
            out_lines.append(sep.join(c.ljust(w) for c, w in zip(cells, widths)))
        return "\n".join(out_lines)

    @staticmethod
    def _fmt_cell(v: Any) -> str:
        if v == "" or v is None:
            return "."
        try:
            return f"{float(v):.0f}"
        except Exception:
            return str(v)

    # ── introspection helpers (used by tests + scenarios) ───────────────

    @property
    def csv_path(self) -> Path:
        return self._csv_path

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Snapshot of recorded rows (a shallow copy — thread-safe to iterate)."""
        with self._lock:
            return list(self._rows)


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — `python -m sim.telemetry` writes a row, reads it back, prints.
# Used by the verification step in the task brief.
# ─────────────────────────────────────────────────────────────────────────────

def _smoke_test() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "smoke.csv"
        t = Telemetry(out_csv=str(csv_path))

        t.start_turn(0, "what time is it")
        t.mark("stt", 312.4)
        t.mark("agent_first_token", 580.1)
        t.mark("agent_complete", 845.7)
        t.mark("tts_synth_first_chunk", 220.3)
        t.mark("e2e_user_to_first_audio", 1102.0)
        t.mark("e2e_user_to_complete", 1840.2)
        t.end_turn("ok", "It is 3 PM.")

        # Unknown-phase warning should fire but not crash.
        t.start_turn(1, "...")
        t.mark("definitely_not_a_phase", 9.0)
        t.end_turn("ok", "")

        # Read it back
        with csv_path.open("r", encoding="utf-8") as fh:
            content = fh.read()
        if "turn_idx" not in content:
            print("FAIL: header missing")
            return 1
        if "what time is it" not in content:
            print("FAIL: row missing")
            return 1
        if "312" not in content:
            print("FAIL: stt timing missing")
            return 1
        report = t.report()
        if "stt" not in report:
            print("FAIL: report missing column")
            return 1
        print("=== smoke csv ===")
        print(content)
        print("=== smoke report ===")
        print(report)
        print("OK")
        return 0


if __name__ == "__main__":
    sys.exit(_smoke_test())
