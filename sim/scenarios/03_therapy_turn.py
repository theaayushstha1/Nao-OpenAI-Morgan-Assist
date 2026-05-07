"""Scenario 03 — Therapy turn with vision + body language.

Steps:
    1. Wake on a known face.
    2. User says: "I'm feeling anxious about midterms".
    3. Expect ``agent_handoff`` with ``active_agent == "therapist"``.
    4. Expect ``observe_face`` action emitted (camera consent default-on
       per Phase 6 — we override the camera_consent stub to return True).
    5. Expect a ``gesture`` action with ``intent == "nod"``.
    6. Reply audio_chunk arrives.
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


SCENARIO_NAME = "03_therapy_turn"
SCENARIO_TIMEOUT_S = 30.0


def run(driver: Driver, telemetry: Any) -> dict[str, Any]:
    deadline = time.monotonic() + SCENARIO_TIMEOUT_S
    details: dict[str, Any] = {}

    try:
        # Therapist agent stub: returns an empathetic line, marks itself as
        # `therapist`, and enqueues two of its canonical actions:
        #   - observe_face → camera tool (Phase 6, consent-gated)
        #   - gesture { intent: "nod" } → Phase 4 body-language gesture
        therapy_actions = [
            {"name": "observe_face", "args": {"reason": "check_in"}},
            {"name": "gesture", "args": {"intent": "nod"}},
        ]
        driver.install_mocks(
            transcript="I'm feeling anxious about midterms",
            reply="That sounds heavy. Let's slow down for a moment together.",
            actions=therapy_actions,
            crisis=False,
            active_agent="therapist",
        )
        driver.connect_ws("ws://localhost/ws/aayush")

        # Wake
        telemetry.start_turn(0, "<wake>")
        driver.inject_face(face_id="aayush", confidence=0.92, distance_m=0.7)
        driver.expect(predicate_control("ready_to_listen"),
                      timeout_s=min(5.0, deadline - time.monotonic()))
        telemetry.end_turn("ok", "wake handled")

        # Therapy turn
        telemetry.start_turn(1, "I'm feeling anxious about midterms")
        t0 = time.perf_counter()
        driver.say("I'm feeling anxious about midterms")

        handoff = driver.expect(
            lambda f: (f.get("type") == "control"
                       and f.get("subtype") == "agent_handoff"),
            timeout_s=min(5.0, deadline - time.monotonic()),
        )
        active = (handoff.get("data") or {}).get("active_agent")
        details["active_agent"] = active
        if active != "therapist":
            telemetry.end_turn("fail", "")
            return {"scenario": SCENARIO_NAME, "outcome": "fail",
                    "details": {"reason": f"expected therapist, got {active!r}",
                                **details},
                    "telemetry_rows": telemetry.rows}

        observe = driver.expect(
            lambda f: f.get("type") == "action" and f.get("name") == "observe_face",
            timeout_s=min(5.0, deadline - time.monotonic()),
        )
        details["observe_face_args"] = observe.get("args")

        gesture = driver.expect(
            lambda f: (f.get("type") == "action"
                       and f.get("name") == "gesture"
                       and (f.get("args") or {}).get("intent") == "nod"),
            timeout_s=min(5.0, deadline - time.monotonic()),
        )
        details["gesture_args"] = gesture.get("args")
        telemetry.mark("gesture_dispatch", (time.perf_counter() - t0) * 1000.0)

        audio = driver.expect(predicate_audio_chunk(),
                              timeout_s=min(5.0, deadline - time.monotonic()))
        telemetry.mark("e2e_user_to_first_audio",
                       (time.perf_counter() - t0) * 1000.0)
        details["reply_audio_text"] = audio.get("text")
        telemetry.end_turn("ok", audio.get("text") or "")

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
