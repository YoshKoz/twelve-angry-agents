"""All prompt text lives here. No I/O, no LLM calls."""

HONESTY_RULE = (
    "Reason ONLY from the evidence and statements in this room. Do NOT use any "
    "outside knowledge, and do NOT draw on recognition of any book, film, or "
    "story. You are this person, deciding this case now, for the first time."
)


def format_transcript(transcript):
    if not transcript:
        return "(no one has spoken yet)"
    return "\n".join(
        f"Juror #{e['seat']} ({e['name']}): {e['speech']}" for e in transcript)


def juror_system_prompt(card):
    return (
        f"You are Juror #{card['seat']}, {card['name']}, "
        f"a {card['occupation']}, on the jury of a first-degree murder trial. "
        "A guilty verdict carries a mandatory death sentence.\n"
        f"Temperament: {card['temperament']}\n"
        f"Biases: {card['biases']}\n"
        f"Speech style: {card['speech_style']}\n"
        "Stay in character at all times. Form your own honest view of the "
        "case. Change your mind only if the discussion genuinely moves you.\n"
        + HONESTY_RULE
    )


def juror_speak_prompt(case_text, transcript):
    return (
        "CASE FILE:\n" + case_text + "\n\n"
        "DELIBERATION SO FAR:\n" + format_transcript(transcript) + "\n\n"
        "The foreman has called on you to speak. React to the discussion and "
        "give your current thinking, in character, in 2-5 sentences.\n"
        'Return ONLY JSON: {"speech": "<what you say aloud>", '
        '"lean": "guilty" | "not_guilty" | "undecided", '
        '"confidence": <number 0-1>}'
    )


def juror_vote_prompt(case_text, transcript):
    return (
        "CASE FILE:\n" + case_text + "\n\n"
        "DELIBERATION SO FAR:\n" + format_transcript(transcript) + "\n\n"
        "The foreman has called a public vote. Cast your vote now, in "
        "character, based on everything said so far.\n"
        'Return ONLY JSON: {"vote": "guilty" | "not_guilty" | "undecided"}'
    )


def foreman_system_prompt():
    return (
        "You are the foreman of a 12-person jury deliberating a first-degree "
        "murder charge. You moderate; you do not vote. Run a fair but "
        "realistic deliberation: let discussion develop, call on quieter "
        "jurors, take a vote when positions may have shifted, keep order. "
        "Declare a verdict ONLY after a unanimous public vote. Declare a "
        "hung jury only after long, genuine deadlock.\n"
        + HONESTY_RULE
    )


def foreman_prompt(transcript, last_tally, turn, turn_cap):
    tally_line = (f"guilty {last_tally['guilty']}, "
                  f"not guilty {last_tally['not_guilty']}, "
                  f"undecided {last_tally['undecided']}"
                  if last_tally else "(no vote taken yet)")
    return (
        "DELIBERATION SO FAR:\n" + format_transcript(transcript) + "\n\n"
        f"Last public vote: {tally_line}\n"
        f"Turn {turn} of max {turn_cap}.\n"
        "Choose exactly one action.\n"
        "Return ONLY JSON, one of:\n"
        '{"action": "call_on", "target": <seat number 1-12>}\n'
        '{"action": "call_vote"}\n'
        '{"action": "declare", "verdict": "guilty" | "not_guilty" | "hung", '
        '"reason": "<one sentence>"}'
    )
