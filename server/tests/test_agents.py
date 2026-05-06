from server.agents import pick_initial_agent


def test_hint_chat_picks_chat_agent():
    assert pick_initial_agent("alice", "chat").name == "chat"


def test_hint_morgan_picks_chatbot():
    assert pick_initial_agent("alice", "morgan").name == "chatbot"


def test_hint_therapy_picks_therapist():
    assert pick_initial_agent("alice", "therapy").name == "therapist"


def test_hint_skills_picks_skills():
    assert pick_initial_agent("alice", "skills").name == "skills"


def test_no_hint_returns_router():
    assert pick_initial_agent("alice", None).name == "router"


def test_therapist_injects_recaps(monkeypatch):
    from server.agents import therapist as t
    monkeypatch.setattr(t.session, "load_recent_recaps", lambda u, n=3: ["past talk"])
    a = t.build_therapist_agent("alice")
    # Instructions are now a callable so memory updates land per turn.
    rendered = a.instructions(None, a) if callable(a.instructions) else a.instructions
    assert "past talk" in rendered
