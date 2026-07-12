import subprocess
from unittest.mock import patch, MagicMock

import pytest

import agent


def ok(stdout):
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


def fail(stderr="boom", code=1):
    m = MagicMock()
    m.returncode = code
    m.stdout = ""
    m.stderr = stderr
    return m


def test_ask_builds_claude_p_command():
    with patch("agent.subprocess.run", return_value=ok("hello")) as run:
        out = agent.ask("SYS", "USER")
    assert out == "hello"
    cmd = run.call_args[0][0]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "USER" in cmd
    assert "--system-prompt" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"


def test_ask_strips_output():
    with patch("agent.subprocess.run", return_value=ok("  text \n")):
        assert agent.ask("s", "u") == "text"


def test_ask_retries_once_on_nonzero_exit_then_succeeds():
    with patch("agent.subprocess.run", side_effect=[fail(), ok("second")]) as run:
        assert agent.ask("s", "u") == "second"
    assert run.call_count == 2


def test_ask_raises_after_two_failures_with_stderr():
    with patch("agent.subprocess.run", side_effect=[fail("err msg"), fail("err msg")]):
        with pytest.raises(agent.AgentError, match="err msg"):
            agent.ask("s", "u")


def test_ask_retries_once_on_timeout_then_raises():
    exc = subprocess.TimeoutExpired(cmd="claude", timeout=5)
    with patch("agent.subprocess.run", side_effect=[exc, exc]) as run:
        with pytest.raises(agent.AgentError, match="timed out"):
            agent.ask("s", "u", timeout=5)
    assert run.call_count == 2


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
