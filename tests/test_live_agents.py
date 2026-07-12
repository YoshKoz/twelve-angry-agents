from unittest.mock import patch, MagicMock

import live_agents

CARD = {"id": "juror_08", "seat": 8, "name": "James Davis",
        "occupation": "architect", "temperament": "t", "biases": "b",
        "speech_style": "s", "emoji": "\U0001F914"}


def _mock_detailed(parsed):
    return MagicMock(return_value=(parsed, 'raw out', 'sys', 'user'))


def test_live_juror_speak_wires_prompts_and_schema():
    mock = _mock_detailed({"speech": "x", "lean": "guilty", "confidence": 0.9})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_juror_fn(CARD, "CASE", [], "speak")
    assert out["speech"] == "x"
    assert out["_prompt"] == {"system": "sys", "user": "user"}
    assert out["_raw_output"] == "raw out"
    sys_prompt, user_prompt = mock.call_args[0][0], mock.call_args[0][1]
    assert "Juror #8" in sys_prompt
    assert "CASE" in user_prompt
    assert mock.call_args[0][2] == ["speech", "lean", "confidence"]


def test_live_juror_vote_requires_vote_key():
    with patch("live_agents.agent.ask_json_detailed",
               _mock_detailed({"vote": "not_guilty"})) as mock:
        out = live_agents.live_juror_fn(CARD, "CASE", [], "vote")
    assert out["vote"] == "not_guilty"
    assert out["_prompt"] is not None
    assert out["_raw_output"] == "raw out"


def test_live_foreman_requires_action_key():
    mock = _mock_detailed({"action": "call_vote"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_foreman_fn([], None, 3, 200)
    assert out["action"] == "call_vote"
    assert out["_prompt"] is not None
    assert "Turn 3 of max 200" in mock.call_args[0][1]
