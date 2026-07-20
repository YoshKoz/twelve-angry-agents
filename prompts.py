"""All prompt text lives here. No I/O, no LLM calls."""

HONESTY_RULE = (
    "Reason ONLY from the evidence and statements in this room. Do NOT use any "
    "outside knowledge, and do NOT draw on recognition of any book, film, or "
    "story. You are this person, deciding this case now, for the first time."
)

# A long deliberation (up to TURN_CAP turns) resends the whole transcript on
# every call; past this many remarks, only the most recent are kept so the
# prompt doesn't grow without bound.
MAX_TRANSCRIPT_ENTRIES = 40


def format_transcript(transcript):
    if not transcript:
        return "(no one has spoken yet)"
    omitted = len(transcript) - MAX_TRANSCRIPT_ENTRIES
    recent = transcript[-MAX_TRANSCRIPT_ENTRIES:] if omitted > 0 else transcript
    lines = [f"Juror #{e['seat']} ({e['name']}): {e['speech']}" for e in recent]
    if omitted > 0:
        lines.insert(0, f"(...{omitted} earlier remarks omitted...)")
    return "\n".join(lines)


REASONABLE_DOUBT = (
    "The legal standard is guilt BEYOND A REASONABLE DOUBT. The burden is "
    "entirely on the prosecution; the defendant proves nothing. A guilty vote "
    "means the evidence leaves you no reasonable doubt. If a genuine, "
    "articulable doubt remains for you — not a fanciful one — the honest vote "
    "is not guilty, even if you privately suspect he did it. Weigh this "
    "through your own character; jurors of good faith can land differently."
)


def juror_system_prompt(card):
    lean = card.get("conviction_lean")
    lens = card.get("private_lens")
    extra = ""
    if lean:
        extra += f"How you weigh the evidence: {lean}\n"
    if lens:
        extra += ("Something you personally notice that others may miss "
                  f"(keep it yours unless it's worth raising): {lens}\n")
    return (
        f"You are Juror #{card['seat']}, {card['name']}, "
        f"a {card['occupation']}, on the jury of a first-degree murder trial. "
        "A guilty verdict carries a mandatory death sentence.\n"
        f"Temperament: {card['temperament']}\n"
        f"Biases: {card['biases']}\n"
        f"Speech style: {card['speech_style']}\n"
        + extra +
        "Stay in character at all times. Form your own honest view of the "
        "case. Change your mind only if the discussion genuinely moves you.\n"
        + REASONABLE_DOUBT + "\n"
        + HONESTY_RULE
    )


def _tally_line(last_tally):
    return (f"guilty {last_tally['guilty']}, "
            f"not guilty {last_tally['not_guilty']}, "
            f"undecided {last_tally['undecided']}"
            if last_tally else "(no vote taken yet)")


def juror_speak_prompt(case_text, transcript, seat, last_tally=None):
    own_remarks = [e["speech"] for e in transcript if e["seat"] == seat]
    own_block = (
        "\n\nWHAT YOU'VE ALREADY SAID IN THIS ROOM:\n"
        + "\n".join(f"- {s}" for s in own_remarks)
        + "\nDo not just repeat these points. React to what's been said "
        "since, add a new fact or doubt, or explain what would change your "
        "mind — or pass the moment along by saying so briefly."
        if own_remarks else ""
    )
    return (
        "CASE FILE:\n" + case_text + "\n\n"
        "DELIBERATION SO FAR:\n" + format_transcript(transcript) + own_block + "\n\n"
        f"Last public vote: {_tally_line(last_tally)}\n\n"
        "The foreman has called on you to speak. React to the discussion and "
        "the current vote count, and give your current thinking, in "
        "character, in 2-5 sentences. Do not restate points already made by "
        "others — either challenge a specific claim someone made, raise a new "
        "doubt or fact, or say plainly what would change your vote.\n"
        'Return ONLY JSON: {"speech": "<what you say aloud>", '
        '"lean": "guilty" | "not_guilty" | "undecided", '
        '"confidence": <number 0-1>}'
    )


def juror_vote_prompt(case_text, transcript, last_tally=None):
    return (
        "CASE FILE:\n" + case_text + "\n\n"
        "DELIBERATION SO FAR:\n" + format_transcript(transcript) + "\n\n"
        f"Last public vote: {_tally_line(last_tally)}\n\n"
        "The foreman has called a public vote. You must commit to guilty or "
        "not guilty — there is no abstaining. First reason it through in "
        "character, weighing the evidence and what's been said against the "
        "beyond-a-reasonable-doubt standard, then cast the vote your reasoning "
        "leads to. Do not just follow the count — a lopsided tally is not "
        "proof. Vote guilty only if the evidence leaves you no reasonable "
        "doubt; if real doubt remains for you, vote not guilty.\n"
        'Return ONLY JSON: {"reasoning": "<your private reasoning, 1-3 '
        'sentences>", "vote": "guilty" | "not_guilty"}'
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


def _recent_speakers(transcript, n=4):
    seen = []
    for e in reversed(transcript):
        if e["seat"] not in seen:
            seen.append(e["seat"])
        if len(seen) >= n:
            break
    return seen


def foreman_prompt(transcript, last_tally, turn, turn_cap):
    recent = _recent_speakers(transcript)
    recent_line = (", ".join(f"#{s}" for s in recent)
                   if recent else "(no one yet)")
    return (
        "DELIBERATION SO FAR:\n" + format_transcript(transcript) + "\n\n"
        f"Last public vote: {_tally_line(last_tally)}\n"
        f"Turn {turn} of max {turn_cap}.\n"
        f"Jurors who spoke most recently: {recent_line}. Do NOT call on any of "
        "them again yet — draw out someone who has been quiet, and especially "
        "press jurors in the minority to explain their reasoning so the room "
        "actually tests it. Let doubt get a real hearing before you vote.\n"
        "Choose exactly one action.\n"
        "Return ONLY JSON, one of:\n"
        '{"action": "call_on", "target": <seat number 1-12>}\n'
        '{"action": "call_vote"}\n'
        '{"action": "declare", "verdict": "guilty" | "not_guilty" | "hung", '
        '"reason": "<one sentence>"}'
    )
