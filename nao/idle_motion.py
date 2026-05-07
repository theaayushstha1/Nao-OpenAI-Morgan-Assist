# -*- coding: utf-8 -*-
"""Background subtle motion for IDLE / LISTENING states (Py 2.7).

Public API per ``docs/PHASE_4_TASK_MAP.md``::

    class IdleMotion(object):
        def __init__(self, nao_ip, nao_port=9559, motion=None, autonomous=None): ...
        def set_state(self, state):  # state in {"idle", "listening", "off"}
        def stop(self):              # idempotent

State semantics
---------------
``idle``      Breathing animation cycle (chest + shoulders) using
              ``ALMotion.setBreathEnabled("Body", True)``. If that endpoint
              is missing or fails, falls back to a custom slow chest /
              shoulder pitch cycle driven by ``angleInterpolation``.

``listening`` Stops breathing animation, then runs a gaze-aversion thread
              that every ~2.5s rotates ``HeadYaw`` by +/-8 degrees over
              0.5s, holds for 2s, returns to centre. Gaze direction
              alternates left / right.

``off``       Stops every background thread, disables breathing, restores a
              neutral head pose, and re-enables ``ALAutonomousLife``'s
              ``BackgroundMovement`` ability if it was previously toggled.

Thread safety
-------------
All state transitions are guarded by a single ``threading.RLock``.
Repeated calls with the same state are a no-op (idempotent). ``stop()``
joins worker threads within ``_JOIN_TIMEOUT_S`` seconds.

ALAutonomousLife
----------------
If an ``autonomous`` proxy (or ``ALProxy("ALAutonomousLife")``) is
available the module disables ``BackgroundMovement`` while either
``idle`` or ``listening`` are active so the autonomous-life subsystem
doesn't fight our motion cues, and re-enables it on ``set_state("off")``.
"""
from __future__ import print_function

import math
import threading
import time


# ---- Tunables -----------------------------------------------------------------

_BREATH_AMPLITUDE_RAD = 0.05      # ~3 deg, gentle chest pitch travel
_BREATH_INHALE_S      = 2.0
_BREATH_EXHALE_S      = 2.5

_GAZE_YAW_DEG         = 8.0
_GAZE_TRAVEL_S        = 0.5       # time HeadYaw takes to reach the target
_GAZE_HOLD_S          = 2.0       # how long to hold off-centre
_GAZE_INTERVAL_S      = 2.5       # gap between gaze-aversion bursts

_JOIN_TIMEOUT_S       = 1.0       # stop() must join within this
_LOOP_TICK_S          = 0.05      # sleep granularity for stop checks

_VALID_STATES = ("idle", "listening", "off")


def _deg2rad(d):
    return d * math.pi / 180.0


# ---- Optional naoqi import ---------------------------------------------------

try:
    from naoqi import ALProxy  # type: ignore
    _NAOQI_AVAILABLE = True
except Exception:  # pragma: no cover - naoqi only present on the robot
    ALProxy = None
    _NAOQI_AVAILABLE = False


def _try_proxy(service, ip, port):
    """Best-effort ALProxy. Returns ``None`` instead of raising."""
    if not _NAOQI_AVAILABLE or not ip:
        return None
    try:
        return ALProxy(service, ip, int(port))
    except Exception as e:  # pragma: no cover
        print("[idle_motion] ALProxy({0!r}) failed: {1}".format(service, e))
        return None


# ---- IdleMotion --------------------------------------------------------------

class IdleMotion(object):
    """See module docstring."""

    def __init__(self, nao_ip, nao_port=9559, motion=None, autonomous=None):
        self._ip   = nao_ip
        self._port = int(nao_port) if nao_port else 9559

        # Resolve naoqi services lazily. If a caller already has live
        # proxies they pass them in (saves a roundtrip + ensures we share
        # state with the rest of the app); otherwise we try to construct
        # them ourselves and silently fall back to no-op mode if naoqi
        # isn't available (CI / dev workstation / py_compile sweep).
        self._motion     = motion if motion is not None else _try_proxy(
            "ALMotion", self._ip, self._port)
        self._autonomous = autonomous if autonomous is not None else _try_proxy(
            "ALAutonomousLife", self._ip, self._port)

        self._lock          = threading.RLock()
        self._state         = "off"          # active state
        self._stop_event    = threading.Event()
        self._breath_thread = None           # type: threading.Thread
        self._gaze_thread   = None           # type: threading.Thread

        # Track whether *we* disabled BackgroundMovement so we know to
        # re-enable it on set_state("off") — never toggle on/off blindly.
        self._suppressed_bg_movement = False

        # If ``setBreathEnabled`` is unavailable we use the manual cycle.
        self._use_native_breath = self._has_method(self._motion, "setBreathEnabled")

    # ---- state machine -------------------------------------------------------

    def set_state(self, state):
        """Switch into ``"idle"``, ``"listening"`` or ``"off"``.

        Idempotent: a repeated same-state call is a no-op.
        """
        if state not in _VALID_STATES:
            raise ValueError(
                "IdleMotion.set_state: bad state {0!r}, want one of {1}"
                .format(state, _VALID_STATES))

        with self._lock:
            if state == self._state:
                return

            # Tear down whatever's running first so transitions are clean.
            self._teardown_locked()

            if state == "idle":
                self._suppress_background_movement_locked()
                self._start_breath_locked()
            elif state == "listening":
                self._suppress_background_movement_locked()
                self._start_gaze_locked()
            else:  # "off"
                self._restore_background_movement_locked()
                self._restore_neutral_pose_locked()

            self._state = state

    def stop(self):
        """Stop everything. Idempotent. Joins worker threads in <= 1s."""
        with self._lock:
            if self._state == "off" and not self._any_thread_alive_locked():
                return
            self._teardown_locked()
            self._restore_background_movement_locked()
            self._restore_neutral_pose_locked()
            self._state = "off"

    # Pythonic / NAOqi-style alias used by other utility modules.
    close = stop

    # ---- breath -------------------------------------------------------------

    def _start_breath_locked(self):
        """Kick off breathing animation. Prefers ALMotion's native breath."""
        if self._motion is not None and self._use_native_breath:
            try:
                self._motion.setBreathEnabled("Body", True)
                # Native breath is engine-driven; no thread needed but we
                # still spin a tiny watchdog so set_state("off") can reset
                # it deterministically without inspecting NAOqi state.
                self._stop_event.clear()
                t = threading.Thread(target=self._native_breath_watchdog,
                                     name="IdleMotion-breath-native")
                t.daemon = True
                self._breath_thread = t
                t.start()
                return
            except Exception as e:
                print("[idle_motion] setBreathEnabled failed, falling back to "
                      "custom cycle: {0}".format(e))
                # Fall through to the custom cycle.

        # Custom cycle fallback: slow chest pitch (and shoulder mirror)
        # using angleInterpolation. Works on any NAOqi build that
        # supports angleInterpolation, which is required anyway for the
        # rest of the app.
        self._stop_event.clear()
        t = threading.Thread(target=self._custom_breath_loop,
                             name="IdleMotion-breath-custom")
        t.daemon = True
        self._breath_thread = t
        t.start()

    def _native_breath_watchdog(self):
        """Hold the thread alive while native breathing is on, exit on stop."""
        while not self._stop_event.is_set():
            time.sleep(_LOOP_TICK_S)

    def _custom_breath_loop(self):
        """Manual slow chest / shoulder pitch cycle.

        Joints used: ``LShoulderPitch`` and ``RShoulderPitch`` move in
        opposition to the chest, simulating a gentle inhale / exhale.
        Amplitude is small (~3 degrees) so the robot looks alive without
        wobbling visibly.
        """
        # Resting baselines — match StandInit posture so we don't yank
        # the arms when entering idle from a standing pose.
        rest_l = 1.55
        rest_r = 1.55

        while not self._stop_event.is_set():
            try:
                # Inhale: shoulders rise (pitch decreases).
                self._safe_angle_interp(
                    ["LShoulderPitch", "RShoulderPitch"],
                    [rest_l - _BREATH_AMPLITUDE_RAD,
                     rest_r - _BREATH_AMPLITUDE_RAD],
                    [_BREATH_INHALE_S, _BREATH_INHALE_S],
                )
                if self._stop_event.is_set():
                    break
                # Exhale: shoulders return (pitch increases back to rest).
                self._safe_angle_interp(
                    ["LShoulderPitch", "RShoulderPitch"],
                    [rest_l, rest_r],
                    [_BREATH_EXHALE_S, _BREATH_EXHALE_S],
                )
            except Exception as e:
                print("[idle_motion] breath cycle error: {0}".format(e))
                # Don't spin tight on a broken proxy; back off a bit.
                self._sleep_with_check(0.5)

    def _stop_breath_locked(self):
        """Stop breath thread + disable native breathing."""
        self._stop_event.set()
        thr = self._breath_thread
        self._breath_thread = None
        if thr is not None and thr.is_alive():
            thr.join(_JOIN_TIMEOUT_S)
        if self._motion is not None and self._use_native_breath:
            try:
                self._motion.setBreathEnabled("Body", False)
            except Exception as e:
                print("[idle_motion] disable native breath failed: {0}".format(e))

    # ---- gaze ---------------------------------------------------------------

    def _start_gaze_locked(self):
        self._stop_event.clear()
        t = threading.Thread(target=self._gaze_aversion_loop,
                             name="IdleMotion-gaze")
        t.daemon = True
        self._gaze_thread = t
        t.start()

    def _gaze_aversion_loop(self):
        """Every ``_GAZE_INTERVAL_S`` rotate HeadYaw +/-8deg, hold, return."""
        # Alternate directions so the robot doesn't always glance the
        # same way — feels more natural over a long listening window.
        direction = 1
        yaw_target = _deg2rad(_GAZE_YAW_DEG)
        while not self._stop_event.is_set():
            # Wait the inter-glance gap first; this prevents a glance
            # firing immediately on entry into LISTENING which would
            # collide with whatever head pose just preceded us.
            if not self._sleep_with_check(_GAZE_INTERVAL_S):
                return

            try:
                # Rotate to off-centre target.
                self._safe_angle_interp(
                    ["HeadYaw"],
                    [direction * yaw_target],
                    [_GAZE_TRAVEL_S],
                )
                if self._stop_event.is_set():
                    break

                # Hold off-centre.
                if not self._sleep_with_check(_GAZE_HOLD_S):
                    break

                # Return to centre.
                self._safe_angle_interp(
                    ["HeadYaw"],
                    [0.0],
                    [_GAZE_TRAVEL_S],
                )
            except Exception as e:
                print("[idle_motion] gaze cycle error: {0}".format(e))
                self._sleep_with_check(0.5)

            direction = -direction

    def _stop_gaze_locked(self):
        self._stop_event.set()
        thr = self._gaze_thread
        self._gaze_thread = None
        if thr is not None and thr.is_alive():
            thr.join(_JOIN_TIMEOUT_S)

    # ---- helpers ------------------------------------------------------------

    def _teardown_locked(self):
        """Stop both worker threads. Reset stop_event so the next state can
        re-arm immediately."""
        self._stop_breath_locked()
        self._stop_gaze_locked()
        # Clear *after* both joins so neither loop sees a stale clear.
        self._stop_event.clear()

    def _any_thread_alive_locked(self):
        for thr in (self._breath_thread, self._gaze_thread):
            if thr is not None and thr.is_alive():
                return True
        return False

    def _suppress_background_movement_locked(self):
        """Disable ALAutonomousLife's BackgroundMovement so it doesn't fight
        our motion. Tracked so we know to restore it on ``off``."""
        if self._autonomous is None or self._suppressed_bg_movement:
            return
        try:
            self._autonomous.setAutonomousAbilityEnabled(
                "BackgroundMovement", False)
            self._suppressed_bg_movement = True
        except Exception as e:
            print("[idle_motion] disable BackgroundMovement failed: {0}".format(e))

    def _restore_background_movement_locked(self):
        if self._autonomous is None or not self._suppressed_bg_movement:
            return
        try:
            self._autonomous.setAutonomousAbilityEnabled(
                "BackgroundMovement", True)
        except Exception as e:
            print("[idle_motion] restore BackgroundMovement failed: {0}".format(e))
        finally:
            self._suppressed_bg_movement = False

    def _restore_neutral_pose_locked(self):
        """Move HeadYaw + HeadPitch back to centre. Best-effort."""
        if self._motion is None:
            return
        try:
            self._safe_angle_interp(
                ["HeadYaw", "HeadPitch"],
                [0.0, 0.0],
                [_GAZE_TRAVEL_S, _GAZE_TRAVEL_S],
            )
        except Exception as e:
            print("[idle_motion] restore neutral pose failed: {0}".format(e))

    def _safe_angle_interp(self, joints, angles, times):
        """ALMotion.angleInterpolation wrapper that no-ops cleanly when
        the proxy is unavailable (dev mode / py_compile)."""
        if self._motion is None:
            # Simulate the duration so the loop pacing still feels right
            # in disabled-naoqi mode (smoke test in __main__).
            try:
                total = max(times) if times else 0.0
            except Exception:
                total = 0.0
            self._sleep_with_check(total)
            return
        # ALMotion.angleInterpolation(names, target_angles, times, isAbsolute)
        self._motion.angleInterpolation(joints, angles, times, True)

    def _sleep_with_check(self, seconds):
        """Sleep ``seconds`` but bail early if ``_stop_event`` is set.

        Returns ``True`` if the full duration elapsed, ``False`` if a
        stop was requested mid-sleep.
        """
        end = time.time() + max(0.0, float(seconds))
        while True:
            remaining = end - time.time()
            if remaining <= 0:
                return True
            if self._stop_event.is_set():
                return False
            time.sleep(min(_LOOP_TICK_S, remaining))

    @staticmethod
    def _has_method(obj, name):
        if obj is None:
            return False
        try:
            return callable(getattr(obj, name, None))
        except Exception:
            return False


# ---- Disabled-mode smoke test ------------------------------------------------

def _smoke_test():
    """Cycle ``idle -> listening -> off -> idle -> off`` with no naoqi.

    Run via ``python nao/idle_motion.py``. Works on any machine because
    every ALProxy call short-circuits when naoqi is missing.
    """
    print("[idle_motion] smoke test (naoqi available: {0})".format(_NAOQI_AVAILABLE))
    im = IdleMotion(nao_ip=None, nao_port=9559, motion=None, autonomous=None)
    sequence = ["idle", "listening", "off", "idle", "off"]
    for s in sequence:
        print("[idle_motion] -> set_state({0!r})".format(s))
        im.set_state(s)
        # Idempotency check: same-state call should return immediately.
        im.set_state(s)
        time.sleep(0.5)
    im.stop()
    im.stop()  # second stop is a no-op
    print("[idle_motion] smoke test done")


if __name__ == "__main__":
    _smoke_test()
