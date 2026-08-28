"""agent.py backend tests.

Nothing here starts a real `claude` process or opens a socket: the claude_cli
backend is exercised by monkeypatching subprocess.run, the ollama backend by
patching the httpx client.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import httpx
import pytest

import agent


# --- helpers ---------------------------------------------------------------

def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _result_json(text, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error,
                       "result": text})


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(agent, "BACKEND", "claude_cli")


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setattr(agent, "BACKEND", "ollama")


def _ok_response(content):
    m = MagicMock(spec=httpx.Response)
    m.raise_for_status = MagicMock()
    m.json = MagicMock(return_value={"message": {"content": content}})
    return m


def _fail_response():
    m = MagicMock(spec=httpx.Response)
    m.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
        "fail", request=MagicMock(), response=MagicMock()))
    return m


# --- claude_cli backend ----------------------------------------------------

def test_cli_ask_returns_stripped_result_text(cli):
    with patch("agent.subprocess.run",
               return_value=_completed(_result_json("  hello \n"))) as run:
        assert agent.ask("SYS", "USER") == "hello"
    run.assert_called_once()


def test_cli_ask_accepts_a_stream_event_list_and_uses_the_last_entry(cli):
    payload = json.dumps([
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": "thinking"}},
        {"type": "result", "is_error": False, "result": "final answer"},
    ])
    with patch("agent.subprocess.run", return_value=_completed(payload)):
        assert agent.ask("s", "u") == "final answer"


def test_cli_ask_builds_isolated_headless_command(cli):
    with patch("agent.subprocess.run",
               return_value=_completed(_result_json("out"))) as run:
        agent.ask("SYS", "USER")
    cmd = run.call_args[0][0]
    assert cmd[0] == agent.CLAUDE_BIN
    assert cmd[1] == "-p" and cmd[2] == "USER"
    assert "--system-prompt" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    # isolation: no repo CLAUDE.md, no hooks, no MCP servers, no tools
    assert "--exclude-dynamic-system-prompt-sections" in cmd
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--allowed-tools") + 1] == ""
    assert json.loads(cmd[cmd.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert json.loads(cmd[cmd.index("--settings") + 1])["hooks"] == {}
    kwargs = run.call_args[1]
    assert kwargs["cwd"] == agent._WORKDIR      # empty scratch dir, not the repo
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_cli_ask_uses_default_model_and_honors_an_override(cli):
    with patch("agent.subprocess.run",
               return_value=_completed(_result_json("x"))) as run:
        agent.ask("s", "u")
    assert run.call_args[0][0][run.call_args[0][0].index("--model") + 1] \
        == agent.CLAUDE_MODEL
    with patch("agent.subprocess.run",
               return_value=_completed(_result_json("x"))) as run:
        agent.ask("s", "u", model="opus")
    cmd = run.call_args[0][0]
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_cli_ask_passes_the_timeout_through(cli):
    with patch("agent.subprocess.run",
               return_value=_completed(_result_json("x"))) as run:
        agent.ask("s", "u", timeout=7)
    assert run.call_args[1]["timeout"] == 7


def test_cli_ask_raises_on_nonzero_exit_with_stderr_detail(cli):
    with patch("agent.subprocess.run",
               return_value=_completed("", "boom happened", returncode=2)):
        with pytest.raises(agent.AgentError, match="exited 2"):
            agent.ask("s", "u")


def test_cli_ask_raises_on_timeout(cli):
    with patch("agent.subprocess.run",
               side_effect=subprocess.TimeoutExpired("claude", 5)):
        with pytest.raises(agent.AgentError, match="timed out"):
            agent.ask("s", "u", timeout=5)


def test_cli_ask_raises_when_the_binary_is_missing(cli):
    with patch("agent.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(agent.AgentError, match="not found"):
            agent.ask("s", "u")


def test_cli_ask_raises_on_non_json_stdout(cli):
    # a login prompt or rate-limit notice comes back as bare text
    with patch("agent.subprocess.run",
               return_value=_completed("Please run /login first")):
        with pytest.raises(agent.AgentError, match="non-JSON"):
            agent.ask("s", "u")


def test_cli_ask_raises_when_claude_reports_an_error(cli):
    with patch("agent.subprocess.run",
               return_value=_completed(_result_json("rate limited",
                                                    is_error=True))):
        with pytest.raises(agent.AgentError, match="reported an error"):
            agent.ask("s", "u")


def test_cli_ask_raises_on_an_empty_event_list(cli):
    with patch("agent.subprocess.run", return_value=_completed("[]")):
        with pytest.raises(agent.AgentError, match="empty event list"):
            agent.ask("s", "u")


def test_cli_ask_raises_on_an_unexpected_payload_shape(cli):
    with patch("agent.subprocess.run", return_value=_completed('"just a string"')):
        with pytest.raises(agent.AgentError, match="unexpected payload"):
            agent.ask("s", "u")


def test_cli_ask_raises_when_there_is_no_result_text(cli):
    with patch("agent.subprocess.run",
               return_value=_completed('{"type": "result", "result": null}')):
        with pytest.raises(agent.AgentError, match="no result text"):
            agent.ask("s", "u")


# --- ollama backend --------------------------------------------------------

def test_ollama_ask_returns_stripped_content(ollama):
    with patch.object(agent._CLIENT, "post",
                      return_value=_ok_response("  text \n")):
        assert agent.ask("s", "u") == "text"


def test_ollama_ask_posts_correct_url_and_body(ollama):
    with patch.object(agent._CLIENT, "post",
                      return_value=_ok_response("out")) as post:
        agent.ask("SYS", "USER")
    assert post.call_args[0][0].endswith("/api/chat")
    body = post.call_args[1]["json"]
    assert body["model"] == agent.OLLAMA_MODEL
    assert body["format"] == "json"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]


def test_ollama_ask_honors_a_model_override(ollama):
    with patch.object(agent._CLIENT, "post",
                      return_value=_ok_response("out")) as post:
        agent.ask("s", "u", model="llama9")
    assert post.call_args[1]["json"]["model"] == "llama9"


def test_ollama_ask_retries_once_on_http_error_then_succeeds(ollama):
    with patch.object(agent._CLIENT, "post",
                      side_effect=[_fail_response(),
                                   _ok_response("second")]) as post:
        assert agent.ask("s", "u") == "second"
    assert post.call_count == 2


def test_ollama_ask_raises_after_two_failures(ollama):
    with patch.object(agent._CLIENT, "post", return_value=_fail_response()):
        with pytest.raises(agent.AgentError):
            agent.ask("s", "u")


def test_ollama_ask_retries_once_on_timeout_then_raises(ollama):
    with patch.object(agent._CLIENT, "post",
                      side_effect=httpx.TimeoutException("nope")) as post:
        with pytest.raises(agent.AgentError, match="timed out"):
            agent.ask("s", "u", timeout=5)
    assert post.call_count == 2


# --- backend dispatch ------------------------------------------------------

def test_unknown_backend_raises_agent_error(monkeypatch):
    monkeypatch.setattr(agent, "BACKEND", "telepathy")
    with pytest.raises(agent.AgentError, match="unknown AGENT_BACKEND"):
        agent.ask("s", "u")


def test_backend_registry_holds_exactly_the_two_backends():
    assert sorted(agent._BACKENDS) == ["claude_cli", "ollama"]


# --- model_for -------------------------------------------------------------

def test_model_for_gives_the_structural_roles_the_foreman_model(cli,
                                                                monkeypatch):
    monkeypatch.setattr(agent, "CLAUDE_MODEL", "haiku")
    monkeypatch.setattr(agent, "CLAUDE_MODEL_FOREMAN", "sonnet")
    for role in ("foreman", "judge", "bailiff"):
        assert agent.model_for(role) == "sonnet"


def test_model_for_gives_everyone_else_the_cheap_model(cli, monkeypatch):
    monkeypatch.setattr(agent, "CLAUDE_MODEL", "haiku")
    monkeypatch.setattr(agent, "CLAUDE_MODEL_FOREMAN", "sonnet")
    for role in ("juror", "", None, "narrator"):
        assert agent.model_for(role) == "haiku"


def test_model_for_is_none_on_a_single_model_backend(ollama):
    for role in ("foreman", "juror", "judge", "bailiff"):
        assert agent.model_for(role) is None


# --- JSON handling ---------------------------------------------------------

def test_ask_json_parses_clean_json():
    with patch("agent.ask", return_value='{"speech": "hi", "lean": "guilty"}'):
        assert agent.ask_json("s", "u", ["speech", "lean"]) == {
            "speech": "hi", "lean": "guilty"}


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


def test_ask_json_forwards_model_and_timeout_to_ask():
    with patch("agent.ask", return_value='{"vote": "guilty"}') as ask:
        agent.ask_json("s", "u", ["vote"], timeout=9, model="opus")
    assert ask.call_args[1]["model"] == "opus"
    assert ask.call_args[1]["timeout"] == 9


def test_ask_json_detailed_returns_raw_text_and_prompts_used():
    with patch("agent.ask", return_value='prose {"vote": "guilty"} more'):
        obj, raw, sys_p, user_p = agent.ask_json_detailed("SYS", "USER",
                                                          ["vote"])
    assert obj == {"vote": "guilty"}
    assert raw == 'prose {"vote": "guilty"} more'
    assert sys_p == "SYS"
    assert user_p == "USER"


def test_ask_json_detailed_reports_the_retried_prompt_not_the_original():
    with patch("agent.ask", side_effect=["junk", '{"vote": "guilty"}']):
        _, _, _, user_p = agent.ask_json_detailed("SYS", "USER", ["vote"])
    assert user_p.startswith("USER")
    assert "ONLY valid JSON" in user_p


def test_extract_json_rejects_non_objects():
    assert agent._extract_json("no braces here") is None
    assert agent._extract_json("{not json}") is None
    assert agent._extract_json('{"a": 1}') == {"a": 1}
