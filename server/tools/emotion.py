"""Emotion tools for the therapist + CBT + grounding agents."""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from agents import RunContextWrapper, function_tool
from server import config, session

_client = OpenAI(api_key=config.OPENAI_API_KEY)

_DISTORTIONS = (
    "catastrophizing", "all-or-nothing", "mind reading", "personalization",
    "fortune-telling", "emotional reasoning", "shoulds", "labeling",
    "magnification/minimization", "filtering",
)


def _unwrap(ctx) -> dict:
    return ctx.context if isinstance(ctx, RunContextWrapper) else ctx


# ────────── log_emotion ──────────

def _log_emotion_impl(ctx, mood: str, intensity: int, trigger: str) -> str:
    store = _unwrap(ctx)
    store.setdefault("emotion_log", []).append(
        {"mood": mood, "intensity": intensity, "trigger": trigger}
    )
    return "logged"


@function_tool
def log_emotion(ctx: RunContextWrapper, mood: str, intensity: int, trigger: str) -> str:
    """Log a per-turn emotion read (mood, intensity 1-10, trigger) for session recap."""
    return _log_emotion_impl(ctx, mood, intensity, trigger)


# ────────── identify_distortion / suggest_reframe ──────────

def _classify_distortion(thought: str) -> dict:
    prompt = (
        "Classify the cognitive distortion in the user's thought. Choose exactly "
        "ONE from: " + ", ".join(_DISTORTIONS) + ". Respond as JSON: "
        '{"distortion": "<name>", "explanation": "<one sentence, gentle tone>"}'
    )
    resp = _client.chat.completions.create(
        model=config.CRISIS_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": thought},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(resp.choices[0].message.content)


def _identify_distortion_impl(thought: str) -> dict:
    return _classify_distortion(thought)


@function_tool
def identify_distortion(thought: str) -> dict:
    """Identify one CBT cognitive distortion in the user's thought with a gentle explanation."""
    return _identify_distortion_impl(thought)


def _reframe_impl(thought: str, distortion: str) -> list[str]:
    prompt = (
        f"The user has a thought exhibiting {distortion}. Offer 2 balanced, "
        "compassionate alternative thoughts they could consider. Reply as a JSON "
        'list of 2 strings: {"reframes": ["...", "..."]}'
    )
    resp = _client.chat.completions.create(
        model=config.CRISIS_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": thought},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    return json.loads(resp.choices[0].message.content)["reframes"]


@function_tool
def suggest_reframe(thought: str, distortion: str) -> list[str]:
    """Return two balanced reframes for a thought exhibiting the given distortion."""
    return _reframe_impl(thought, distortion)


# ────────── observe_face ──────────

def _vision_classify(image_b64: str) -> dict:
    data_uri = f"data:image/jpeg;base64,{image_b64}"
    resp = _client.chat.completions.create(
        model=config.THERAPIST_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You read facial expressions for a supportive robot companion. "
                    "Return JSON: "
                    '{"dominant_emotion": "...", "secondary": "...", "notes": "..."} '
                    "where emotions are one of: happy, sad, angry, fearful, "
                    "surprised, disgusted, neutral, tired, stressed."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What do you see?"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(resp.choices[0].message.content)


def _observe_face_impl(ctx) -> dict:
    store = _unwrap(ctx)
    b64 = store.get("latest_image_b64")
    if not b64:
        return {"error": "no_image"}
    return _vision_classify(b64)


@function_tool
def observe_face(ctx: RunContextWrapper) -> dict:
    """Read the user's face from the current turn's image. Returns {error:'no_image'} if none attached."""
    return _observe_face_impl(ctx)


# ────────── camera consent ──────────

def _set_camera_consent_impl(ctx, enabled: bool) -> str:
    store = _unwrap(ctx)
    username = store.get("username", "guest")
    session.set_camera_consent(username, enabled)
    if not enabled:
        store["suppress_image"] = True
    else:
        store["suppress_image"] = False
    return f"camera_consent={enabled}"


@function_tool
def set_camera_consent(ctx: RunContextWrapper, enabled: bool) -> str:
    """Set the user's camera consent. When False, NAO stops uploading images this session and next visits."""
    return _set_camera_consent_impl(ctx, enabled)


# ────────── recap_session ──────────

def _recap_session_impl(ctx) -> str:
    store = _unwrap(ctx)
    username = store.get("username", "guest")
    log = store.get("emotion_log", [])
    if not log:
        body = "Brief check-in; no notable thoughts logged."
    else:
        moods = ", ".join(f"{e['mood']}({e['intensity']})" for e in log[-5:])
        body = f"Emotions: {moods}. Triggers: {'; '.join(e['trigger'] for e in log[-5:])}."
    session.save_recap(username, body)
    return body


@function_tool
def recap_session(ctx: RunContextWrapper) -> str:
    """Summarize this therapy session and persist it to the user's history."""
    return _recap_session_impl(ctx)
