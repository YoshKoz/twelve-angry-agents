from unittest.mock import patch, MagicMock

import httpx
import pytest

import agent


def _ok_response(content):
    """Build a mock httpx.Response that returns JSON with the given message content."""
    m = MagicMock(spec=httpx.Response)
    m.raise_for_status = MagicMock()
    m.json = MagicMock(return_value={"message": {"content": content}})
    return m


def _fail_response():
    """Build a mock httpx.Response whose raise_for_status raises HTTPStatusError."""
    m = MagicMock(spec=httpx.Response)
    m.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("fail", request=MagicMock(), response=MagicMock()))
    return m


def test_ask_returns_stripped_content():
    with patch.object(agent._CLIENT, "post", return_value=_ok_response("hello")):
        assert agent.ask("SYS", "USER") == "hello"


def test_ask_strips_output():
    with patch.object(agent._CLIENT, "post", return_value=_ok_response("  text \n")):
        assert agent.ask("s", "u") == "text"


def test_ask_posts_correct_url_and_body():
    with patch.object(agent._CLIENT, "post", return_value=_ok_response("out")) as mock_post:
        agent.ask("SYS", "USER")
    mock_post.assert_called_once()
    url = mock_post.call_args[0][0]
    assert url.endswith("/api/chat")
    body = mock_post.call_args[1]["json"]
    assert body["model"] == agent.OLLAMA_MODEL
    assert body["format"] == "json"
    assert body["think"] is True
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]


def test_ask_retries_once_on_http_error_then_succeeds():
    with patch.object(agent._CLIENT, "post", side_effect=[_fail_response(), _ok_response("second")]) as mock_post:
        assert agent.ask("s", "u") == "second"
    assert mock_post.call_count == 2


def test_ask_raises_after_two_failures():
    with patch.object(agent._CLIENT, "post", return_value=_fail_response()):
        with pytest.raises(agent.AgentError):
            agent.ask("s", "u")


def test_ask_retries_once_on_timeout_then_raises():
    def _timeout(*_args, **_kwargs):
        raise httpx.TimeoutException("timed out")
    with patch.object(agent._CLIENT, "post", side_effect=_timeout) as mock_post:
        with pytest.raises(agent.AgentError, match="timed out"):
            agent.ask("s", "u", timeout=5)
    assert mock_post.call_count == 2


def test_ask_json_parses_clean_json():
    with patch("agent.ask", return_value='{"speech": "hi", "lean": "guilty"}'):
        obj = agent.ask_json("s", "u", ["speech", "lean"])
    assert obj == {"speech": "hi", "lean": "guilty"}


def test_ask_json_extracts_json_from_surrounding_prose():
    reply = 'Here is my answer:\n{"vote": "not_guilty"}\nThanks!'
    with patch("agent.ask", return_value=reply):
        assert agent.ask_json("s", "u", ["vote"]) == {"vote": "not_guilty"}


def test_ask_json_retries_on_malformed_with_schema_reminder():
    with patch("agent.ask", side_effect=["not json at all",
                                         '{"vote": "guilty"}']) as ask:
        obj = agent.ask_json("s", "u", ["vote"])
    assert obj == {"vote": "guilty"}
    assert ask.call_count == 2
    retry_prompt = ask.call_args_list[1][0][1]
    assert "ONLY valid JSON" in retry_prompt
    assert "vote" in retry_prompt


def test_ask_json_retries_on_missing_required_key():
    with patch("agent.ask", side_effect=['{"wrong": 1}', '{"vote": "guilty"}']):
        assert agent.ask_json("s", "u", ["vote"]) == {"vote": "guilty"}


def test_ask_json_raises_malformed_after_retries():
    with patch("agent.ask", side_effect=["bad", "bad", "bad"]) as ask:
        with pytest.raises(agent.MalformedReply):
            agent.ask_json("s", "u", ["vote"], retries=3)
    assert ask.call_count == 3
