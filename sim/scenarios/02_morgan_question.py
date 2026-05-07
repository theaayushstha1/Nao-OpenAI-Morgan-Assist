"""Scenario 02 — Morgan State CS question routes to the chatbot agent.

Steps:
    1. Wake on a known face.
    2. User says: "what is CS 491?".
    3. Expect ``agent_handoff`` control with ``active_agent == "chatbot"``.
    4. Expect the agent runner to have invoked ``cs_navigator_search`` once.
    5. Reply audio_chunk arrives.

Why we mock cs_navigator: in the simulator we replace ``run_agent`` with a
fixed ``(reply, active_agent, actions, ...)`` tuple. The tool-call
assertion is therefore that ``actions`` includes a ``cs_navigator_search``
record (the real chatbot would enqueue one as part of the turn). This
keeps the scenario deterministic without invoking Pinecone.
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


SCENARIO_NAME = "02_morgan_question"
SCENARIO_TIMEOUT_S = 30.0


def run(driver: Driver, telemetry: Any) -> dict[str, Any]:
    deadline = time.monotonic() + SCENARIO_TIMEOUT_S
    details: dict[str, Any] = {}

    try:
        # The chatbot agent stub returns a known reply and pretends to have
        # called the cs_navigator_search tool. Its action shape mirrors what
        # ``server.tools.cs_navigator`` enqueues today.
        cs_call = {
            "name": "cs_navigator_search",
            "args": {"query": "what is CS 491?"},
        }
        driver.install_mocks(
            transcript="what is CS 491?",
            reply="CS 491 is the senior capstone — a year-long team project.",
            actions=[cs_call],
            crisis=False,
            active_agent="chatbot",
        )
        driver.connect_ws("ws://localhost/ws/aayush")

        # Wake
        telemetry.start_turn(0, "<wake>")
        driver.inject_face(face_id="aayush", confidence=0.85, distance_m=1.0)
        driver.expect(predicate_control("ready_to_listen"),
                      timeout_s=min(5.0, deadline - time.monotonic()))
        telemetry.end_turn("ok", "wake handled")

        # Question. Anchor a cursor BEFORE we speak so any wake-greeting
        # audio_chunk emitted by the returning-user path doesn't get
        # mistaken for this turn's reply (see scenario 01 cursor pattern).
        telemetry.start_turn(1, "what is CS 491?")
        t0 = time.perf_counter()
        cursor = driver.cursor()
        driver.say("what is CS 491?")

        handoff = driver.expect(
            lambda f: (f.get("type") == "control"
                       and f.get("subtype") == "agent_handoff"),
            timeout_s=min(5.0, deadline - time.monotonic()),
            since=cursor,
        )
        active = (handoff.get("data") or {}).get("active_agent")
        details["active_agent"] = active
        if active != "chatbot":
            telemetry.end_turn("fail", "")
            return {"scenario": SCENARIO_NAME, "outcome": "fail",
                    "details": {"reason": f"expected chatbot, got {active!r}",
                                **details},
                    "telemetry_rows": telemetry.rows}

        # cs_navigator_search action should have been emitted (action frames
        # are sent BEFORE the first audio chunk per app_ws.py contract).
        action = driver.expect(
            lambda f: (f.get("type") == "action"
                       and f.get("name") == "cs_navigator_search"),
            timeout_s=min(5.0, deadline - time.monotonic()),
            since=cursor,
        )
        details["cs_navigator_search_args"] = action.get("args")
        telemetry.mark("cs_navigator_call", (time.perf_counter() - t0) * 1000.0)

        audio = driver.expect(predicate_audio_chunk(),
                              timeout_s=min(5.0, deadline - time.monotonic()),
                              since=cursor)
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
