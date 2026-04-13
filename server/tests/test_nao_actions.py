from server.tools import nao_actions


def test_wave_hand_enqueues():
    ctx = {"actions_queue": []}
    result = nao_actions._enqueue(ctx, "wave_hand", {"hand": "right", "speed": 0.6})
    assert result == "queued"
    assert ctx["actions_queue"] == [
        {"name": "wave_hand", "args": {"hand": "right", "speed": 0.6}}
    ]


def test_multiple_actions_preserve_order():
    ctx = {"actions_queue": []}
    nao_actions._enqueue(ctx, "change_eye_color", {"color": "blue"})
    nao_actions._enqueue(ctx, "nod_head", {"times": 2})
    assert [a["name"] for a in ctx["actions_queue"]] == ["change_eye_color", "nod_head"]


def test_all_expected_tools_exported():
    expected = {
        "stand_up", "sit_down", "kneel",
        "wave_hand", "wave_both_hands", "nod_head", "shake_head", "clap_hands",
        "move_forward", "move_backward", "turn_left", "turn_right", "spin",
        "dance", "change_eye_color", "follow_movement",
        "set_led_color",
    }
    assert expected.issubset(set(nao_actions.ALL_TOOL_NAMES))


def test_chat_actions_bundle_populated():
    assert len(nao_actions.CHAT_ACTIONS) >= 16


def test_therapist_actions_bundle_populated():
    assert len(nao_actions.THERAPIST_ACTIONS) >= 2
