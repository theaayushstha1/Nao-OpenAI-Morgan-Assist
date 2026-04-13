from unittest.mock import patch, MagicMock

from server import safety


def test_keyword_match_returns_positive_without_llm():
    result = safety.crisis_check("i want to kill myself")
    assert result.positive is True
    assert result.source == "keyword"


def test_benign_phrase_is_negative():
    with patch("server.safety._llm_classify", return_value=False):
        result = safety.crisis_check("i'm really stressed about finals")
        assert result.positive is False


def test_ambiguous_phrase_uses_llm():
    with patch("server.safety._llm_classify", return_value=True) as m:
        result = safety.crisis_check("i don't want to be here anymore")
        assert result.positive is True
        assert result.source == "llm"
        assert m.called


def test_llm_unavailable_falls_back_to_keyword_only_failsafe():
    with patch("server.safety._llm_classify", side_effect=RuntimeError("api down")):
        result = safety.crisis_check("i'm done with everything")
        assert result.positive is True
        assert result.source == "failsafe"
