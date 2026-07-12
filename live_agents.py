"""Glue: injected-callable signatures -> real claude -p calls."""

import agent
import prompts


def live_juror_fn(card, case_text, transcript, mode):
    system = prompts.juror_system_prompt(card)
    if mode == "speak":
        return agent.ask_json(
            system, prompts.juror_speak_prompt(case_text, transcript),
            ["speech", "lean", "confidence"])
    return agent.ask_json(
        system, prompts.juror_vote_prompt(case_text, transcript), ["vote"])


def live_foreman_fn(transcript, last_tally, turn, turn_cap):
    return agent.ask_json(
        prompts.foreman_system_prompt(),
        prompts.foreman_prompt(transcript, last_tally, turn, turn_cap),
        ["action"])
