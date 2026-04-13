"""Pre-dispatch crisis gate. Runs before any agent sees the user message."""
from __future__ import annotations

from dataclasses import dataclass
from openai import OpenAI

from server import config

_CLIENT = OpenAI(api_key=config.OPENAI_API_KEY)

# Hard-fail keywords — any match returns positive immediately, skip LLM.
_HARD_KEYWORDS = (
    "kill myself", "end my life", "suicide", "suicidal",
    "want to die", "going to die tonight",
    "hurt myself", "cutting myself",
)

# Soft triggers — require LLM confirmation. These often appear in benign contexts.
_SOFT_TRIGGERS = (
    "don't want to be here", "can't go on", "no point", "tired of living",
    "done with everything", "give up", "hopeless",
)

HOTLINE_REPLY = (
    "I hear you, and I'm really glad you're telling me. What you're feeling sounds "
    "heavy. I want you to reach out to someone who can be with you right now — you "
    "can call or text 988 in the US for the Suicide and Crisis Lifeline. They're "
    "open 24/7. Is there someone nearby you can talk to too?"
)


@dataclass(frozen=True)
class CrisisResult:
    positive: bool
    source: str  # "keyword" | "llm" | "failsafe" | "clean"


def crisis_check(text: str) -> CrisisResult:
    lower = text.lower()
    if any(k in lower for k in _HARD_KEYWORDS):
        return CrisisResult(True, "keyword")
    soft_hit = any(t in lower for t in _SOFT_TRIGGERS)
    if not soft_hit:
        return CrisisResult(False, "clean")
    try:
        if _llm_classify(text):
            return CrisisResult(True, "llm")
        return CrisisResult(False, "clean")
    except Exception:
        return CrisisResult(True, "failsafe")


def _llm_classify(text: str) -> bool:
    resp = _CLIENT.chat.completions.create(
        model=config.CRISIS_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a suicide/self-harm risk classifier. Reply with only "
                    "the word YES or NO. YES if the user is expressing active "
                    "suicidal ideation, intent to self-harm, or immediate crisis. "
                    "NO for general stress, sadness, venting, or hypothetical talk."
                ),
            },
            {"role": "user", "content": text},
        ],
        max_tokens=4,
        temperature=0,
    )
    return resp.choices[0].message.content.strip().upper().startswith("Y")
