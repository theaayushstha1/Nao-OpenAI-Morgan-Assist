# -*- coding: utf-8 -*-
"""
LedsConsoleRenderer -- ANSI terminal renderer for ALLeds.fadeRGB calls.

Phase 10.5 sims drive the entire wake/state machine in-process. Without a
physical robot the LED state is invisible, so this renderer prints one
line per ``fadeRGB`` call with a colored bar matching the actual RGB.
The bar uses 24-bit ANSI background-color escapes (``\x1b[48;2;R;G;Bm``).
Most modern terminals (iTerm2, macOS Terminal, VS Code, GNOME, Konsole,
Alacritty, Windows Terminal) support that. On older / piped terminals
the bar will appear as plain whitespace, which is still readable.

Both ``fadeRGB`` signatures used by NAO firmware are supported:

    fadeRGB(group_name, packed_int_rgb, duration_s)   # 0xRRGGBB int
    fadeRGB(group_name, r, g, b, duration_s)          # floats 0..1

A scenario can hold a reference to ``current_state`` and assert what color
each group landed on after a sequence of state machine transitions:

    leds = LedsConsoleRenderer()
    fake_naoqi.install_into_sys_modules(leds_renderer=leds)
    ...
    assert leds.current_state["FaceLeds"]["rgb"] == (0.20, 0.50, 1.00)
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any, Dict, Optional, Tuple


# --- color label table -----------------------------------------------------
# Keep this small; it's only for human-readable hints in the line output.
# We compare RGB tuples in linear space against a few canonical anchors
# and pick the closest. Off-anchor colors render as "rgb" (the precise
# values are still printed alongside).
_COLOR_LABELS: Tuple[Tuple[str, Tuple[float, float, float]], ...] = (
    ("off",       (0.00, 0.00, 0.00)),
    ("gray",      (0.10, 0.10, 0.12)),
    ("white",     (1.00, 1.00, 1.00)),
    ("red",       (1.00, 0.00, 0.00)),
    ("orange",    (1.00, 0.50, 0.00)),
    ("yellow",    (1.00, 0.80, 0.10)),
    ("amber",     (1.00, 0.65, 0.10)),
    ("green",     (0.10, 0.90, 0.30)),
    ("cyan",      (0.10, 0.80, 0.95)),
    ("light blue", (0.10, 0.30, 0.70)),
    ("blue",      (0.20, 0.50, 1.00)),
    ("purple",    (0.50, 0.20, 0.80)),
    ("pink",      (1.00, 0.40, 0.70)),
    ("magenta",   (1.00, 0.00, 1.00)),
)


def _packed_int_to_rgb01(packed: int) -> Tuple[float, float, float]:
    """Convert ALLeds packed 0xRRGGBB int to ``(r, g, b)`` floats in 0..1."""
    p = int(packed) & 0xFFFFFF
    r = ((p >> 16) & 0xFF) / 255.0
    g = ((p >> 8) & 0xFF) / 255.0
    b = (p & 0xFF) / 255.0
    return (r, g, b)


def _label_for_rgb(rgb: Tuple[float, float, float]) -> str:
    """Pick the closest pre-defined human label for an RGB tuple."""
    r, g, b = rgb
    best_label = "rgb"
    best_dist = 0.18  # tolerance: anything farther falls back to "rgb"
    for name, (rr, gg, bb) in _COLOR_LABELS:
        d = ((r - rr) ** 2 + (g - gg) ** 2 + (b - bb) ** 2) ** 0.5
        if d < best_dist:
            best_dist = d
            best_label = name
    return best_label


def _supports_color() -> bool:
    """Heuristic: stdout is a tty AND TERM is not 'dumb'."""
    if not hasattr(sys.stdout, "isatty"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "").lower()
    if term == "dumb":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return True


def _make_bar(rgb: Tuple[float, float, float], width: int = 5,
              color: bool = True) -> str:
    """Build a ``width``-wide bar string colored to match RGB.

    With color=True we emit a 24-bit ANSI background sequence so the bar
    looks like the LED. With color=False we just emit ``"#" * width``
    so the line is still useful in logs / non-color terminals.
    """
    bar = "█" * width  # full block
    if not color:
        return bar
    r = max(0, min(255, int(round(rgb[0] * 255.0))))
    g = max(0, min(255, int(round(rgb[1] * 255.0))))
    b = max(0, min(255, int(round(rgb[2] * 255.0))))
    return "\x1b[38;2;{0};{1};{2}m{3}\x1b[0m".format(r, g, b, bar)


class LedsConsoleRenderer(object):
    """Receive ``fadeRGB`` calls and pretty-print one line per call.

    Maintains an in-memory ``current_state`` dict so scenarios can assert
    the LED groups' final colors without intercepting print output.

    Parameters
    ----------
    out : file-like, default sys.stdout
        Where to write the colored lines. Tests can pass ``io.StringIO``.
    color : bool or None, default None
        Force-enable / force-disable ANSI colors. ``None`` auto-detects.
    bar_width : int, default 5
        Width of the colored block. Wider = more visible, narrower =
        denser logs.
    """

    def __init__(self,
                 out=None,
                 color: Optional[bool] = None,
                 bar_width: int = 5):
        self._out = out if out is not None else sys.stdout
        if color is None:
            color = _supports_color()
        self._color = bool(color)
        self._bar_width = max(1, int(bar_width))

        # ``current_state[group_name] = {"rgb": (r, g, b), "duration_s": float}``
        # Public; scenarios read it directly. Guarded by the lock for
        # thread-safe writes (the fake AL classes may emit from worker
        # threads).
        self.current_state: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

        # Tail of every line emitted, keyed by call order. Useful for
        # post-mortem when a scenario fails and we want to dump exactly
        # what happened. Kept small (last 256 lines) so memory stays bounded.
        self._tail: list = []
        self._tail_max = 256

    # ----------------------------------------------------------------
    # public API
    # ----------------------------------------------------------------
    def fadeRGB(self, group_name, *args) -> None:
        """Receive a ``fadeRGB`` call and render one line.

        Supports both naoqi signatures:
          * ``fadeRGB(group_name, packed_int, duration_s)``      [3 args]
          * ``fadeRGB(group_name, r, g, b, duration_s)``          [5 args]

        Robust to ``packed_int`` arriving as a Python ``int`` or as a
        ``float`` (some firmware paths float-cast). Also tolerates a
        missing ``duration_s`` (defaults to 0.0).
        """
        rgb, dur = self._normalize_args(args)
        self._record(group_name, rgb, dur)
        self._emit(group_name, rgb, dur)

    # Alias -- mirrors ALLeds.setRGB which is occasionally used elsewhere.
    setRGB = fadeRGB

    def get_state(self, group_name: str) -> Optional[Dict[str, Any]]:
        """Return a copy of the latest fade state for a group, or None."""
        with self._lock:
            entry = self.current_state.get(group_name)
            if entry is None:
                return None
            return dict(entry)

    def reset(self) -> None:
        """Forget all recorded state. Useful between scenarios."""
        with self._lock:
            self.current_state.clear()
            self._tail = []

    def tail(self, n: int = 20) -> list:
        """Return the last ``n`` rendered lines (most recent last)."""
        with self._lock:
            return list(self._tail[-int(n):])

    # ----------------------------------------------------------------
    # internals
    # ----------------------------------------------------------------
    def _normalize_args(self, args) -> Tuple[Tuple[float, float, float], float]:
        """Decode the variadic arg block into ``((r,g,b), duration_s)``.

        Mirrors NAOqi's two ``fadeRGB`` signatures:
          * ``(packed_int, duration)``
          * ``(r, g, b, duration)``

        Tolerates a ``packed_int`` passed as a Python ``int`` *or* a
        ``float`` (``float(packed)`` is safely converted back). Also
        tolerates missing duration -> defaults to 0.
        """
        # 1-arg: just a packed int (no duration). Treat duration = 0.
        if len(args) == 1:
            return _packed_int_to_rgb01(int(args[0])), 0.0

        # 2-arg: (packed_int, duration) OR (some_int, some_float). Both
        # parse the same way: first is packed.
        if len(args) == 2:
            return _packed_int_to_rgb01(int(args[0])), float(args[1])

        # 3-arg: (r, g, b) without duration. Treat duration = 0.
        if len(args) == 3:
            return self._floats_or_packed(args[0], args[1], args[2]), 0.0

        # 4-arg: (r, g, b, duration). The standard float-tuple signature.
        if len(args) >= 4:
            return self._floats_or_packed(args[0], args[1], args[2]), float(args[3])

        # 0-arg: defensive default.
        return (0.0, 0.0, 0.0), 0.0

    @staticmethod
    def _floats_or_packed(a, b, c) -> Tuple[float, float, float]:
        """Treat (a, b, c) as floats. We don't unpack a packed int here
        because the 3-or-4-arg form unambiguously means tuple-of-floats
        per ALLeds documentation.
        """
        return (
            max(0.0, min(1.0, float(a))),
            max(0.0, min(1.0, float(b))),
            max(0.0, min(1.0, float(c))),
        )

    def _record(self,
                group_name: str,
                rgb: Tuple[float, float, float],
                duration_s: float) -> None:
        with self._lock:
            self.current_state[group_name] = {
                "rgb": rgb,
                "duration_s": float(duration_s),
            }

    def _emit(self,
              group_name: str,
              rgb: Tuple[float, float, float],
              duration_s: float) -> None:
        bar = _make_bar(rgb, self._bar_width, color=self._color)
        label = _label_for_rgb(rgb)
        line = (
            "[leds] {group:14s}  -> {bar}  "
            "{label:11s} ({r:.2f}, {g:.2f}, {b:.2f})  "
            "(over {dur:.2f}s)".format(
                group=group_name[:14],
                bar=bar,
                label=label,
                r=rgb[0], g=rgb[1], b=rgb[2],
                dur=float(duration_s),
            )
        )
        try:
            self._out.write(line + "\n")
            try:
                self._out.flush()
            except Exception:
                pass
        except Exception:
            # Never let a console-write failure escape into the wake
            # state machine.
            pass
        with self._lock:
            self._tail.append(line)
            if len(self._tail) > self._tail_max:
                # Drop a chunk so we don't pop one-by-one.
                drop = len(self._tail) - self._tail_max
                del self._tail[:drop]


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import io
    buf = io.StringIO()
    r = LedsConsoleRenderer(out=buf, color=False, bar_width=4)
    r.fadeRGB("FaceLeds", 0.20, 0.50, 1.00, 0.4)
    r.fadeRGB("ChestLeds", 0x00FF55, 0.2)
    r.fadeRGB("RightEarLeds", 0.10, 0.90, 0.30, 0.05)
    out = buf.getvalue()
    assert "FaceLeds" in out
    assert "ChestLeds" in out
    assert "RightEarLeds" in out
    assert r.current_state["FaceLeds"]["rgb"] == (0.20, 0.50, 1.00)
    # 0x00FF55 -> (0, 255, 85) / 255 = (0.0, 1.0, 0.333...)
    cs = r.current_state["ChestLeds"]["rgb"]
    assert abs(cs[0] - 0.0) < 1e-6
    assert abs(cs[1] - 1.0) < 1e-6
    assert abs(cs[2] - (85 / 255.0)) < 1e-3
    print("[leds_console self-test] OK")
    # Print lines for visual inspection if user runs directly.
    print(out, end="")
