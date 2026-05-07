"""Scenario 05 — Echo bleed (server-side cooldown verifies no double-turn).

Steps:
    1. Wake on a known face. The driver enables the post-TTS cooldown
       window with the production knobs (non-zero) so the server's echo
       guard is exercised.
    2. User asks a question. Reply is delivered.
    3. Immediately after the server's last audio_chunk, we send a fresh
       batch of audio_chunk frames carrying a transcript that substring-
       matches the prior reply (the simulator's stand-in for a real echo
       picked up by the mic).
    4. Assert: NO new transcript / agent_handoff / audio_chunk frames
       fire from the server — the cooldown should have eaten the echo
       audio, and the substring guard would catch any straggler that
       slipped through. We give the server a generous 1.5 s window to
       prove it stayed quiet.

Note on the EchoSimulator: the spec says "enable
``EchoSimulator(delay_ms=80, gain=0.15)``". That class lives in
``sim/echo_sim.py`` (owned by the ``fake-naoqi-mod`` worktree). When
that file lands, this scenario will route the playback bytes back into
the mic. Until then we simulate the bleed by sending a transcript that
matches the reply — the substring-guard outcome is identical from the
server's perspective.
"""
from __future__ import annotations

import time
from typing import Any

from sim.scenarios._driver import (
    Driver,
    DriverUnavailable,
    predicate_audio_chunk,
    predicate_control,
)


SCENARIO_NAME = "05_echo_bleed"
SCENARIO_TIMEOUT_S = 30.0


def _try_enable_echo_sim(driver: Driver, *, delay_ms: int, gain: float) -> bool:
    """Best-effort hook into ``sim.echo_sim.EchoSimulator``. Returns True if armed."""
    try:
        from sim.echo_sim import EchoSimulator  # type: ignore
    except Exception:
        return False
    try:
        sim = EchoSimulator(delay_ms=delay_ms, gain=gain)
        driver._echo_sim = sim  # noqa: SLF001 — store for cross-call inspection
        return True
    except Exception:
        return False


def run(driver: Driver, telemetry: Any) -> dict[str, Any]:
    deadline = time.monotonic() + SCENARIO_TIMEOUT_S
    details: dict[str, Any] = {}

    try:
        reply = "CS 491 is the senior capstone, a year-long team project."
        driver.install_mocks(
            transcript="what is CS 491?",
            reply=reply,
            actions=[],
            crisis=False,
            active_agent="chatbot",
        )
        # Re-enable the post-TTS cooldown — install_mocks zeroes it for
        # other scenarios; for this one we want it ON.
        try:
            from server import app_ws as _aws  # type: ignore
            driver._mp_setattr(_aws, "TTS_COOLDOWN_PADDING_MS", 400, raising=False)
            driver._mp_setattr(_aws.config, "MIC_GATE_GRACE_MS", 200, raising=False)
        except Exception:
            pass

        details["echo_sim_armed"] = _try_enable_echo_sim(
            driver, delay_ms=80, gain=0.15,
        )

        driver.connect_ws("ws://localhost/ws/aayush")

        telemetry.start_turn(0, "<wake>")
        driver.inject_face(face_id="aayush", confidence=0.91, distance_m=0.8)
        driver.expect(predicate_control("ready_to_listen"),
                      timeout_s=min(5.0, deadline - time.monotonic()))
        # Wait for the wake-greeting's tts_ended before continuing; otherwise
        # the post-TTS cooldown swallows the user's first audio_chunks and
        # the next turn never fires.
        try:
            driver.expect(predicate_control("tts_ended"),
                          timeout_s=min(3.0, deadline - time.monotonic()))
            # Cooldown is grace_ms (200) + padding (400) = 600 ms after
            # tts_ended. Sleep past it before driving the next turn.
            time.sleep(0.7)
        except TimeoutError:
            # No greeting (new user path) — proceed; no cooldown to wait on.
            pass
        telemetry.end_turn("ok", "wake handled")

        telemetry.start_turn(1, "what is CS 491?")
        t0 = time.perf_counter()
        # Anchor cursor so wake-greeting audio doesn't pollute first_audio.
        cursor = driver.cursor()
        driver.say("what is CS 491?")

        first_audio = driver.expect(predicate_audio_chunk(),
                                    timeout_s=min(5.0, deadline - time.monotonic()),
                                    since=cursor)
        details["reply_text"] = first_audio.get("text")

        # Wait for tts_ended so the cooldown is active.
        driver.expect(predicate_control("tts_ended"),
                      timeout_s=min(5.0, deadline - time.monotonic()))
        telemetry.mark("e2e_user_to_complete",
                       (time.perf_counter() - t0) * 1000.0)
        telemetry.end_turn("ok", first_audio.get("text") or "")

        # Now the "echo": flip the STT mock to return the reply text and
        # send another batch of audio. With the cooldown armed the audio
        # should be dropped. With the substring guard armed downstream,
        # any leak is caught. We watch for any new agent_handoff /
        # transcript / audio_chunk during a 1.5 s window.
        telemetry.start_turn(2, "<echo>")
        driver.install_mocks(
            transcript=reply,                # the "echo" the mic would hear
            reply="(should not be spoken)",
            actions=[],
            crisis=False,
            active_agent="chatbot",
        )

        baseline = len(driver.ws.frames_snapshot())
        driver.say(reply, audio_ms=400)

        echo_window_s = 1.5
        time.sleep(echo_window_s)

        new_frames = driver.ws.frames_snapshot()[baseline:]
        violators = [
            f for f in new_frames
            if (f.get("type") == "audio_chunk")
            or (f.get("type") == "control"
                and f.get("subtype") in {"transcript", "agent_handoff"})
        ]
        details["echo_window_s"] = echo_window_s
        details["new_frame_count"] = len(new_frames)
        details["violators"] = [
            {"type": f.get("type"), "subtype": f.get("subtype"),
             "text": f.get("text")} for f in violators
        ]
        details["frame_subtypes_seen"] = sorted({
            f.get("subtype") for f in new_frames
            if f.get("type") == "control"
        })
        echo_held = (len(violators) == 0)
        telemetry.end_turn("ok" if echo_held else "fail",
                           "no echo replay" if echo_held else "echo leaked")

        return {"scenario": SCENARIO_NAME,
                "outcome": "ok" if echo_held else "fail",
                "details": details, "telemetry_rows": telemetry.rows}
    except DriverUnavailable as e:
        return {"scenario": SCENARIO_NAME, "outcome": "skipped",
                "details": {"reason": str(e)}, "telemetry_rows": telemetry.rows}
    except TimeoutError as e:
        try:
            telemetry.end_turn("timeout", "")
        except Exception:
            pass
        return {"scenario": SCENARIO_NAME, "outcome": "timeout",
                "details": {"reason": str(e), **details},
                "telemetry_rows": telemetry.rows}
    except Exception as e:  # noqa: BLE001
        try:
            telemetry.end_turn("fail", "")
        except Exception:
            pass
        return {"scenario": SCENARIO_NAME, "outcome": "fail",
                "details": {"reason": repr(e), **details},
                "telemetry_rows": telemetry.rows}
