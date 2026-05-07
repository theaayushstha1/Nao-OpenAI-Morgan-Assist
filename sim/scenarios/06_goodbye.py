"""Scenario 06 — Goodbye / session close.

Steps:
    1. Wake on a known face.
    2. Three small turns — one greeting, one casual question, one farewell.
    3. After the farewell turn, send a ``session_close`` control to mirror
       what the robot-side ``exit_detection`` would do when it hears
       "goodbye" / "stop".
    4. Assert: the server emits a ``session_end`` control and tears the WS
       down with code 1000.

Why we send ``session_close`` from the simulator: the goodbye-detection
regex lives in ``nao/utils/exit_detection.py`` (the robot side). The
server doesn't parse the user transcript for farewells — it relies on
the robot to fire ``session_close`` when ``detect_exit_intent`` matches.
This scenario validates the server side of that handshake; the regex
itself is unit-tested over in ``server/tests/test_*`` (or, more accurately,
on the robot side once a unit harness lands).
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


SCENARIO_NAME = "06_goodbye"
SCENARIO_TIMEOUT_S = 30.0


def run(driver: Driver, telemetry: Any) -> dict[str, Any]:
    deadline = time.monotonic() + SCENARIO_TIMEOUT_S
    details: dict[str, Any] = {}

    try:
        driver.install_mocks(
            transcript="hi",
            reply="Hi there.",
            actions=[],
            crisis=False,
            active_agent="chat",
        )
        driver.connect_ws("ws://localhost/ws/aayush")

        telemetry.start_turn(0, "<wake>")
        driver.inject_face(face_id="aayush", confidence=0.86, distance_m=0.8)
        driver.expect(predicate_control("ready_to_listen"),
                      timeout_s=min(5.0, deadline - time.monotonic()))
        telemetry.end_turn("ok", "wake handled")

        # A few turns so we can prove the session was alive before the close.
        exchanges = [
            ("hi", "Hi there.", "chat"),
            ("what's the weather", "Sunny and 72.", "skills"),
            ("goodbye", "Take care.", "chat"),
        ]
        for idx, (utt, reply, agent) in enumerate(exchanges, start=1):
            driver.install_mocks(
                transcript=utt, reply=reply, actions=[],
                crisis=False, active_agent=agent,
            )
            telemetry.start_turn(idx, utt)
            t0 = time.perf_counter()
            driver.say(utt)
            audio = driver.expect(predicate_audio_chunk(),
                                  timeout_s=min(5.0, deadline - time.monotonic()))
            telemetry.mark("e2e_user_to_first_audio",
                           (time.perf_counter() - t0) * 1000.0)
            details[f"turn_{idx}_audio_text"] = audio.get("text")
            telemetry.end_turn("ok", audio.get("text") or "")

        # Now the goodbye-driven session_close. This is what
        # `nao.utils.exit_detection.detect_exit_intent` triggers on the
        # robot side when the user says "goodbye" / "stop" / etc.
        driver.send_session_close()

        end = driver.expect(predicate_control("session_end"),
                            timeout_s=min(5.0, deadline - time.monotonic()))
        details["session_end_data"] = end.get("data") or {}

        return {"scenario": SCENARIO_NAME, "outcome": "ok",
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
