"""Phase 4 — embodiment tests for the NAO-side SoundLocalizer.

`nao/sound_localize.py` is owned by the `robot-sound-localize` worktree which
wraps NAOqi's `ALSoundLocalization` events into a Python-friendly tracker:

  - `SoundLocalizer(nao_ip, nao_port=9559, motion=None, max_yaw_deg=60.0,
                    max_pitch_deg=20.0, turn_speed_dps=30.0, confidence_min=0.4)`
  - `.start()`, `.stop()` — idempotent subscribe/unsubscribe.
  - `.get_last_direction()` — `{azimuth_deg, elevation_deg, ts_ms, confidence}`
    or `None` if nothing has been heard yet.
  - `.turn_head_toward(azimuth_deg, elevation_deg=0.0)` — drives ALMotion,
    clamped to (max_yaw_deg, max_pitch_deg).

Because the module isn't merged into this branch yet (it lives on
`robot-sound-localize`), every test starts with `pytest.importorskip` so
collection still succeeds. The robot-side module must also be importable
on the server's Python 3.11 — naoqi imports must be optional + guarded.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest


# ───────────────────────────── helpers ─────────────────────────────


def _import_sound_localize():
    return pytest.importorskip("nao.sound_localize")


def _make_localizer(motion=None, **overrides):
    """Construct a SoundLocalizer with a fake motion proxy."""
    sl_mod = _import_sound_localize()
    SoundLocalizer = getattr(sl_mod, "SoundLocalizer", None)
    if SoundLocalizer is None:
        pytest.skip("SoundLocalizer class not yet defined in nao.sound_localize")
    if motion is None:
        motion = MagicMock(name="ALMotion")
    kwargs = dict(motion=motion)
    kwargs.update(overrides)
    # The first arg in the task-map signature is `nao_ip`.
    return SoundLocalizer("127.0.0.1", **kwargs)


def _push_event(localizer, azimuth_rad: float, elevation_rad: float,
                confidence: float, energy: float = 1.0):
    """Push a synthetic ALSoundLocalization event into the localizer.

    NAOqi's raw event payload is:
        [time, [confidence, energy], [azimuth, elevation, _, _]]

    We try a few likely entry points so the test stays resilient to whichever
    name the sibling worktree picks. Skip if none of them exist."""
    payload = [time.time(), [confidence, energy], [azimuth_rad, elevation_rad, 0.0, 0.0]]

    for attr in ("_handle_event", "_on_event", "_consume_event",
                 "_on_sound_event", "process_event", "feed_event"):
        if hasattr(localizer, attr):
            getattr(localizer, attr)("ALSoundLocalization/SoundLocated", payload, "subscriber")
            return

    pytest.skip(
        "SoundLocalizer has no recognized test-injection hook; "
        "expected one of _handle_event / _on_event / _consume_event / process_event / feed_event"
    )


# ──────────────────────────── tests ────────────────────────────────


def test_sound_localizer_starts_disabled_when_no_naoqi():
    """If naoqi isn't importable on this host (always true on the server-side
    test runner), `start()` and `stop()` must no-op rather than raising."""
    localizer = _make_localizer()

    # Both should be safely callable; no exception.
    localizer.start()
    localizer.stop()
    # Calling stop twice in a row stays idempotent.
    localizer.stop()


def test_get_last_direction_returns_none_pre_start():
    """Before any event has been received, the direction must be `None`."""
    localizer = _make_localizer()
    assert localizer.get_last_direction() is None


def test_get_last_direction_after_event():
    """Feed a high-confidence fake event into the localizer and assert it
    surfaces through `get_last_direction()`. The localizer should convert
    NAOqi's radians payload to degrees on the way out."""
    localizer = _make_localizer(confidence_min=0.4)
    # ALSoundLocalization fires events in radians. 0.5236 rad ≈ 30°.
    _push_event(localizer, azimuth_rad=0.5236, elevation_rad=0.0, confidence=0.85)

    out = localizer.get_last_direction()
    assert out is not None, "get_last_direction returned None after feeding an event"
    # Accept either dict-style or attribute-style access for resilience.
    az = out.get("azimuth_deg") if isinstance(out, dict) else getattr(out, "azimuth_deg")
    conf = out.get("confidence") if isinstance(out, dict) else getattr(out, "confidence")

    assert az == pytest.approx(30.0, abs=0.5), (
        f"expected ~30° azimuth, got {az}"
    )
    assert conf == pytest.approx(0.85, abs=0.01)


def test_low_confidence_event_ignored():
    """An event below `confidence_min` must not overwrite the prior reading
    (or, if no prior reading exists, must leave `get_last_direction` as None)."""
    localizer = _make_localizer(confidence_min=0.4)

    # First, seed with a strong reading.
    _push_event(localizer, azimuth_rad=0.5236, elevation_rad=0.0, confidence=0.9)
    seeded = localizer.get_last_direction()
    assert seeded is not None

    seeded_az = (seeded.get("azimuth_deg") if isinstance(seeded, dict)
                 else getattr(seeded, "azimuth_deg"))

    # Then a low-confidence event from a different direction.
    _push_event(localizer, azimuth_rad=-1.0, elevation_rad=0.0, confidence=0.1)

    after = localizer.get_last_direction()
    after_az = (after.get("azimuth_deg") if isinstance(after, dict)
                else getattr(after, "azimuth_deg"))
    after_conf = (after.get("confidence") if isinstance(after, dict)
                  else getattr(after, "confidence"))

    # The strong reading must still be the surfaced one.
    assert after_az == pytest.approx(seeded_az, abs=0.5), (
        f"low-confidence event should not overwrite prior direction "
        f"(was {seeded_az}°, now {after_az}°)"
    )
    assert after_conf == pytest.approx(0.9, abs=0.01)


def test_turn_head_toward_clamps_to_max_yaw():
    """`turn_head_toward(120)` must clamp to `max_yaw_deg=60` before driving
    ALMotion. We assert by inspecting the angle the motion proxy was asked
    to interpolate to (in radians)."""
    motion = MagicMock(name="ALMotion")
    localizer = _make_localizer(motion=motion, max_yaw_deg=60.0)

    localizer.turn_head_toward(120.0, elevation_deg=0.0)

    # The motion proxy must have been driven. Pull every angle the impl
    # sent and assert the maximum magnitude is bounded by 60° in radians
    # (~1.0472 rad). We accept both angleInterpolation- and setAngles-style
    # calls because the sibling worktree owns the API choice.
    import math
    max_rad = math.radians(60.0)
    seen_angles: list[float] = []
    for call in motion.method_calls:
        # call = (name, args, kwargs)
        for a in call.args:
            if isinstance(a, (list, tuple)):
                for elem in a:
                    if isinstance(elem, (int, float)):
                        seen_angles.append(float(elem))
                    elif isinstance(elem, (list, tuple)):
                        for inner in elem:
                            if isinstance(inner, (int, float)):
                                seen_angles.append(float(inner))
            elif isinstance(a, (int, float)):
                seen_angles.append(float(a))

    assert seen_angles, (
        "turn_head_toward did not invoke any ALMotion API on the proxy"
    )
    # The yaw command we care about should be clamped to ~max_rad.
    yaw_candidates = [abs(a) for a in seen_angles if 0.5 < abs(a) <= 2 * math.pi]
    if yaw_candidates:
        assert max(yaw_candidates) <= max_rad + 1e-3, (
            f"yaw command not clamped: saw magnitudes {yaw_candidates}, "
            f"expected <= {max_rad:.4f} rad ({60.0}°)"
        )
