"""Glue: injected-callable signatures -> real LLM calls."""

import agent
import prompts
import tts
import logging

log = logging.getLogger(__name__)

# A stuck/garbage-outputting backend should surface as a failure quickly —
# ask_json_detailed's own defaults (120s x 3 retries) can stall one call for
# 6 minutes, and FAIL_CAP failures in a row for ~30 minutes before the
# orchestrator aborts. These caps keep that bounded to a few minutes.
# Reasoning mode (agent.py's think:True) makes single calls legitimately
# slower (~10-20s typical), so the timeout allows more headroom than the
# non-reasoning setup did.
TIMEOUT = 90
RETRIES = 2


def live_juror_fn(card, case_text, transcript, mode, last_tally=None):
    system = prompts.juror_system_prompt(card)
    if mode == "speak":
        parsed, raw, sys_p, user_p = agent.ask_json_detailed(
            system,
            prompts.juror_speak_prompt(case_text, transcript, card["seat"],
                                       last_tally),
            ["speech", "lean", "confidence"],
            timeout=TIMEOUT, retries=RETRIES)
        parsed["_prompt"] = {"system": sys_p, "user": user_p}
        parsed["_raw_output"] = raw
        try:
            parsed["audio"] = tts.generate(parsed["speech"], card["seat"])
        except Exception as e:
            log.warning("TTS failed for seat %s: %s", card["seat"], e)
        return parsed
    parsed, raw, sys_p, user_p = agent.ask_json_detailed(
        system, prompts.juror_vote_prompt(case_text, transcript, last_tally),
        ["reasoning", "vote"], timeout=TIMEOUT, retries=RETRIES)
    parsed["_prompt"] = {"system": sys_p, "user": user_p}
    parsed["_raw_output"] = raw
    return parsed


def live_foreman_fn(transcript, last_tally, turn, turn_cap):
    parsed, raw, sys_p, user_p = agent.ask_json_detailed(
        prompts.foreman_system_prompt(),
        prompts.foreman_prompt(transcript, last_tally, turn, turn_cap),
        ["action"], timeout=TIMEOUT, retries=RETRIES)
    parsed["_prompt"] = {"system": sys_p, "user": user_p}
    parsed["_raw_output"] = raw
    return parsed
