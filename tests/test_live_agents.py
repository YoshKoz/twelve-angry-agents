from unittest.mock import patch

import live_agents

CARD = {"id": "juror_08", "seat": 8, "name": "James Davis",
        "occupation": "architect", "temperament": "t", "biases": "b",
        "speech_style": "s"}


def test_live_juror_speak_wires_prompts_and_schema():
    with patch("live_agents.agent.ask_json",
               return_value={"speech": "x", "lean": "guilty",
                             "confidence": 0.9}) as ask:
        out = live_agents.live_juror_fn(CARD, "CASE", [], "speak")
    assert out["speech"] == "x"
    sys_prompt, user_prompt = ask.call_args[0][0], ask.call_args[0][1]
    assert "Juror #8" in sys_prompt
    assert "CASE" in user_prompt
    assert ask.call_args[0][2] == ["speech", "lean", "confidence"]


def test_live_juror_vote_requires_vote_key():
    with patch("live_agents.agent.ask_json",
               return_value={"vote": "not_guilty"}) as ask:
        out = live_agents.live_juror_fn(CARD, "CASE", [], "vote")
    assert out == {"vote": "not_guilty"}
    assert ask.call_args[0][2] == ["vote"]


def test_live_foreman_requires_action_key():
    with patch("live_agents.agent.ask_json",
               return_value={"action": "call_vote"}) as ask:
        out = live_agents.live_foreman_fn([], None, 3, 200)
    assert out == {"action": "call_vote"}
    assert ask.call_args[0][2] == ["action"]
    assert "Turn 3 of max 200" in ask.call_args[0][1]
