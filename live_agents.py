"""Glue: injected-callable signatures -> real LLM calls.

Five roles, all of them agents: twelve jurors (who both assess exhibits and
argue about them), the foreman who runs the room, the court officer who
answers from the record, and the judge who takes the verdict or refuses it.
"""

import logging

import agent
import prompts
import tts

log = logging.getLogger(__name__)

# A stuck backend should surface as a failure quickly. Assessments are short
# and run twelve-wide, so they get a tighter budget than a speaking turn.
TIMEOUT = 120
ASSESS_TIMEOUT = 90
RETRIES = 2


def _trace(parsed, raw, sys_p, user_p):
    parsed["_prompt"] = {"system": sys_p, "user": user_p}
    parsed["_raw_output"] = raw
    return parsed


def live_assess_fn(card, case, exhibit, findings=None):
    """One juror's independent read of one exhibit. Twelve of these run
    concurrently, which is what makes a docket affordable."""
    return _trace(*agent.ask_json_detailed(
        prompts.juror_system_prompt(card),
        prompts.juror_assess_prompt(case, exhibit, findings),
        ["position", "reasoning"],
        timeout=ASSESS_TIMEOUT, retries=RETRIES,
        model=agent.model_for("juror")))


def live_juror_fn(card, case, transcript, mode, last_tally=None,
                  floor_note=None, vote_method=None, exhibit=None,
                  findings=None):
    system = prompts.juror_system_prompt(card)
    model = agent.model_for("juror")
    if mode == "speak":
        parsed = _trace(*agent.ask_json_detailed(
            system,
            prompts.juror_speak_prompt(case, transcript, card["seat"],
                                       last_tally, floor_note, exhibit,
                                       findings),
            ["speech", "lean"],
            timeout=TIMEOUT, retries=RETRIES, model=model))
        try:
            parsed["audio"] = tts.generate(parsed["speech"], card["seat"])
        except Exception as e:
            log.warning("TTS failed for seat %s: %s", card["seat"], e)
        return parsed
    return _trace(*agent.ask_json_detailed(
        system,
        prompts.juror_vote_prompt(case, transcript, last_tally,
                                  vote_method or "hands", findings),
        ["reasoning", "vote"],
        timeout=TIMEOUT, retries=RETRIES, model=model))


def live_foreman_fn(case, transcript, last_tally, turn, turn_cap,
                    pending=None, speech_counts=None, judge_note=None,
                    exhibit=None, findings=None, exhibit_turns=0,
                    remaining=0):
    return _trace(*agent.ask_json_detailed(
        prompts.foreman_system_prompt(),
        prompts.foreman_prompt(case, transcript, last_tally, turn, turn_cap,
                               pending, speech_counts, judge_note, exhibit,
                               findings, exhibit_turns, remaining),
        ["action"],
        timeout=TIMEOUT, retries=RETRIES, model=agent.model_for("foreman")))


def live_bailiff_fn(kind, request, seat, case, transcript):
    """kind is "evidence" (send out for an exhibit) or "experiment" (the room
    tests something and reports what it observes)."""
    system = prompts.bailiff_system_prompt()
    model = agent.model_for("bailiff")
    if kind == "evidence":
        user = prompts.bailiff_evidence_prompt(
            case, request.get("item", ""), seat)
        keys = ["granted", "record"]
    else:
        user = prompts.bailiff_experiment_prompt(
            case, request.get("description", ""), seat, transcript)
        keys = ["possible", "result"]
    return _trace(*agent.ask_json_detailed(
        system, user, keys, timeout=TIMEOUT, retries=RETRIES, model=model))


def live_judge_fn(verdict, reason, last_tally, turn, transcript):
    return _trace(*agent.ask_json_detailed(
        prompts.judge_system_prompt(),
        prompts.judge_prompt(verdict, reason, last_tally, turn, transcript),
        ["accept"],
        timeout=TIMEOUT, retries=RETRIES, model=agent.model_for("judge")))
