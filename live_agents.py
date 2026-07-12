"""Glue: injected-callable signatures -> real claude -p calls."""

import agent
import prompts


def live_juror_fn(card, case_text, transcript, mode):
    system = prompts.juror_system_prompt(card)
    if mode == "speak":
        parsed, raw, sys_p, user_p = agent.ask_json_detailed(
            system, prompts.juror_speak_prompt(case_text, transcript),
            ["speech", "lean", "confidence"])
        parsed["_prompt"] = {"system": sys_p, "user": user_p}
        parsed["_raw_output"] = raw
        return parsed
    parsed, raw, sys_p, user_p = agent.ask_json_detailed(
        system, prompts.juror_vote_prompt(case_text, transcript), ["vote"])
    parsed["_prompt"] = {"system": sys_p, "user": user_p}
    parsed["_raw_output"] = raw
    return parsed


def live_foreman_fn(transcript, last_tally, turn, turn_cap):
    parsed, raw, sys_p, user_p = agent.ask_json_detailed(
        prompts.foreman_system_prompt(),
        prompts.foreman_prompt(transcript, last_tally, turn, turn_cap),
        ["action"])
    parsed["_prompt"] = {"system": sys_p, "user": user_p}
    parsed["_raw_output"] = raw
    return parsed
