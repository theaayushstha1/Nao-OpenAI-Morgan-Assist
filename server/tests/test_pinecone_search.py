from unittest.mock import patch, MagicMock

from server.tools import pinecone_search


def test_search_returns_top_k_texts():
    fake_match = MagicMock(metadata={"text": "CS 341 covers data structures."}, score=0.9)
    fake_index = MagicMock()
    fake_index.query.return_value = MagicMock(matches=[fake_match])
    with patch.object(pinecone_search, "_index", fake_index), \
         patch.object(pinecone_search, "_embed", return_value=[0.1] * 1536):
        results = pinecone_search._search_impl("what is cs 341")
    assert "CS 341" in results[0]["text"]
    assert results[0]["score"] == 0.9


def test_search_with_no_index_returns_empty():
    with patch.object(pinecone_search, "_index", None):
        results = pinecone_search._search_impl("anything")
    assert results == []
