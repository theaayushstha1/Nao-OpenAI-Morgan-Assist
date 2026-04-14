from unittest.mock import patch, MagicMock

from server.tools import vertex_search


def test_search_returns_top_k_snippets():
    fake_doc = MagicMock()
    fake_doc.derived_struct_data = {"snippets": [{"snippet": "CS 341 covers data structures."}]}
    fake_doc.struct_data = {}
    fake_result = MagicMock(document=fake_doc)
    fake_resp = MagicMock(results=[fake_result])
    with patch.object(vertex_search, "_client", MagicMock()), \
         patch.object(vertex_search, "_serving_config", "ignored"), \
         patch.object(vertex_search._client, "search", return_value=fake_resp):
        results = vertex_search._search_impl("what is cs 341")
    assert "CS 341" in results[0]["text"]


def test_search_with_no_client_returns_empty():
    with patch.object(vertex_search, "_client", None):
        results = vertex_search._search_impl("anything")
    assert results == []


def test_search_falls_back_to_struct_data_text():
    fake_doc = MagicMock()
    fake_doc.derived_struct_data = {}
    fake_doc.struct_data = {"text": "Faculty list content"}
    fake_result = MagicMock(document=fake_doc)
    fake_resp = MagicMock(results=[fake_result])
    with patch.object(vertex_search, "_client", MagicMock()), \
         patch.object(vertex_search, "_serving_config", "ignored"), \
         patch.object(vertex_search._client, "search", return_value=fake_resp):
        results = vertex_search._search_impl("faculty")
    assert results[0]["text"] == "Faculty list content"


def test_search_exception_returns_empty():
    mock_client = MagicMock()
    mock_client.search.side_effect = RuntimeError("network")
    with patch.object(vertex_search, "_client", mock_client), \
         patch.object(vertex_search, "_serving_config", "ignored"):
        results = vertex_search._search_impl("anything")
    assert results == []
