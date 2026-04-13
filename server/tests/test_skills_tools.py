from unittest.mock import patch, MagicMock

from server.tools import skills_tools


def test_get_time_returns_ny_tz():
    t = skills_tools._get_time_impl()
    assert "America/New_York" in t["timezone"]
    assert ":" in t["time"]


def test_get_weather_baltimore_shape():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "current": {"temperature_2m": 55.0, "weather_code": 3, "relative_humidity_2m": 60}
    }
    with patch("server.tools.skills_tools.requests.get", return_value=fake_resp):
        w = skills_tools._get_weather_impl()
    assert w["temperature_f"] == 55.0
    assert "condition" in w


def test_todo_add_list_complete_cycle():
    store = {"todos": []}
    skills_tools._add_todo_impl(store, "write spec")
    skills_tools._add_todo_impl(store, "ship code")
    assert len(skills_tools._list_todos_impl(store)) == 2
    skills_tools._complete_todo_impl(store, 1)
    remaining = skills_tools._list_todos_impl(store)
    assert len(remaining) == 1
    assert remaining[0]["text"] == "ship code"
