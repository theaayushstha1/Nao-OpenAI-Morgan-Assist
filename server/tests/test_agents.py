"""Agent routing tests.

`nao-therapy` branch: this build is therapy-only — `pick_initial_agent`
always returns the therapist regardless of the legacy `hint` field.
The router, chatbot, skills, and chat agents are intentionally NOT
imported in `server/agents/__init__.py` (their files remain in tree
for reactivation in any future multi-mode branch).

The pre-`nao-therapy` versions of these tests asserted hint-based
routing (chat -> chat_agent, morgan -> chatbot_agent, etc.). Those
contracts no longer exist on this branch. Tests below match the
single-agent contract.
"""
from server.agents import pick_initial_agent


def test_no_hint_returns_therapist():
    """nao-therapy: default initial agent is the therapist."""
    assert pick_initial_agent("alice", None).name == "therapist"


def test_hint_therapy_returns_therapist():
    assert pick_initial_agent("alice", "therapy").name == "therapist"


def test_legacy_chat_hint_still_returns_therapist():
    """Legacy `hint='chat'` requests are routed to therapist on this
    branch. The hint is preserved on the WS frame contract for
    backward compatibility with tooling but has no routing effect.
    """
    assert pick_initial_agent("alice", "chat").name == "therapist"


def test_legacy_morgan_hint_still_returns_therapist():
    assert pick_initial_agent("alice", "morgan").name == "therapist"


def test_legacy_skills_hint_still_returns_therapist():
    assert pick_initial_agent("alice", "skills").name == "therapist"


def test_therapist_injects_recaps(monkeypatch):
    from server.agents import therapist as t
    monkeypatch.setattr(t.session, "load_recent_recaps", lambda u, n=3: ["past talk"])
    a = t.build_therapist_agent("alice")
    # Instructions are now a callable so memory updates land per turn.
    rendered = a.instructions(None, a) if callable(a.instructions) else a.instructions
    assert "past talk" in rendered
