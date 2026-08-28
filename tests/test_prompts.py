"""Prompt text.

Two things are load-bearing and tested hard here.

The action space: every judgment the room makes has to be offered to some
agent, so the schemas are asserted verbatim.

And compactness: a juror gets the case brief, the one exhibit in front of it,
the room's earlier findings and the last few remarks — never the full
narrative record. Only the court officer answers from that, and the tests
below pin exactly which prompts may contain it.
"""

import prompts

CARD = {
    "id": "juror_03", "seat": 3, "name": "Frank Della Rocca",
    "occupation": "messenger-service owner",
    "temperament": "loud, quick to anger",
    "biases": "takes disagreement personally",
    "speech_style": "blunt, jabbing",
}

NARRATIVE = (
    "THE FULL NARRATIVE RECORD: a sixteen-year-old boy is charged with "
    "stabbing his father, and every witness is described here at the length a "
    "real case file runs to. The storekeeper, the old man on the floor below, "
    "the woman across the tracks, the arresting officers and the medical "
    "examiner each get their own account, with the timings, the floor plan of "
    "the apartment, the schedule of the elevated line, and the boy's own "
    "statements to the police set out in full.")

EXHIBITS = [
    {"id": "the_knife", "name": "The switchblade",
     "prosecution_claim": "It is one of a kind and he bought it.",
     "record": "The knife had a carved handle and was wiped clean."},
    {"id": "the_old_man", "name": "The old man downstairs",
     "prosecution_claim": "He heard the shout and saw the boy run.",
     "record": "The witness drags one leg after a stroke."},
]

CASE = {"id": "the_stabbing", "title": "The State v. the Defendant",
        "charge": "First-degree murder, mandatory death sentence.",
        "narrative": NARRATIVE, "exhibits": EXHIBITS}

EXHIBIT = EXHIBITS[0]

FINDINGS = [{"name": "The switchblade",
             "summary": "3 for the prosecution, 8 doubting it, 1 unmoved"}]

TRANSCRIPT = [
    {"seat": 8, "name": "Davis", "speech": "I just want to talk."},
    {"seat": 3, "name": "Frank Della Rocca", "speech": "Talk about what?"},
]

RECORD_ENTRY = {"kind": "record", "seat": None, "name": "the court",
                "speech": "The knife was recovered at the scene."}

TALLY = {"guilty": 7, "not_guilty": 5, "undecided": 0, "abstain": 0}


# --- system prompts --------------------------------------------------------

def test_honesty_rule_in_every_deliberating_system_prompt():
    assert prompts.HONESTY_RULE in prompts.juror_system_prompt(CARD)
    assert prompts.HONESTY_RULE in prompts.foreman_system_prompt()


def test_reasonable_doubt_standard_reaches_jurors():
    assert "REASONABLE DOUBT" in prompts.juror_system_prompt(CARD)


def test_the_vote_prompt_does_not_re_argue_the_standard():
    """The standard belongs in the system prompt, stated once. Arguing it
    again at ballot time made every juror reason like a careful lawyer and the
    room voted 12-0 on a cold ballot — twelve personas, one voice."""
    p = prompts.juror_vote_prompt(CASE, TRANSCRIPT).lower()
    assert "reasonable doubt" not in p
    assert "reason it through as yourself" in p
    assert "do not follow the count" in p


def test_juror_system_prompt_grants_agency_over_the_room():
    sys = prompts.juror_system_prompt(CARD)
    assert prompts.JUROR_AGENCY in sys
    for power in ("demand", "exhibit", "changing your vote"):
        assert power in sys


def test_juror_system_prompt_includes_card_disposition_when_present():
    card = {**CARD, "conviction_lean": "you demand hard proof",
            "private_lens": "you watch the timeline"}
    sys = prompts.juror_system_prompt(card)
    assert "you demand hard proof" in sys
    assert "you watch the timeline" in sys
    assert "conviction_lean" not in prompts.juror_system_prompt(CARD)


def test_juror_system_prompt_has_card_fields_but_no_script_hints():
    sys = prompts.juror_system_prompt(CARD)
    for field in ("name", "occupation", "temperament", "biases",
                  "speech_style"):
        assert str(CARD[field]) in sys
    assert "Juror #3" in sys
    body = sys.replace(prompts.HONESTY_RULE, "")
    for word in ("dissent", "holdout", "fonda", "12 angry"):
        assert word.lower() not in body.lower()


# --- the case, compressed --------------------------------------------------

def test_case_brief_is_the_charge_and_the_docket_not_the_record():
    brief = prompts.case_brief(CASE)
    assert CASE["title"] in brief
    assert CASE["charge"] in brief
    assert "The switchblade" in brief
    assert "The old man downstairs" in brief
    assert NARRATIVE not in brief
    assert len(brief) < len(NARRATIVE)


def test_case_brief_survives_a_case_with_nothing_filled_in():
    brief = prompts.case_brief({})
    assert "The State v. the Defendant" in brief
    assert "THE DOCKET:" in brief


def test_exhibit_block_carries_the_claim_and_the_record_entry():
    block = prompts.exhibit_block(EXHIBIT)
    assert "EXHIBIT BEFORE THE ROOM: The switchblade" in block
    assert EXHIBIT["prosecution_claim"] in block
    assert EXHIBIT["record"] in block


def test_findings_block_is_the_rooms_memory_of_closed_exhibits():
    block = prompts.findings_block(FINDINGS)
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in block
    assert "The switchblade" in block
    assert "3 for the prosecution, 8 doubting it, 1 unmoved" in block


def test_findings_block_is_nothing_at_all_before_the_first_exhibit_closes():
    assert prompts.findings_block(None) == ""
    assert prompts.findings_block([]) == ""


# --- format_transcript -----------------------------------------------------

def test_format_transcript_lines_and_empty():
    text = prompts.format_transcript(TRANSCRIPT)
    assert "Juror #8 (Davis): I just want to talk." in text
    assert prompts.format_transcript([]) == "(no one has spoken yet)"


def test_format_transcript_renders_record_entries_as_the_record():
    text = prompts.format_transcript(TRANSCRIPT + [RECORD_ENTRY])
    assert "[THE RECORD] The knife was recovered at the scene." in text
    # what the court read in is not attributed to a juror
    assert "Juror #None" not in text
    assert "the court" not in text


def test_format_transcript_shows_only_a_short_window_by_default():
    """A juror used to get forty remarks. The room's memory of what an exhibit
    settled lives in the findings block now, not in raw transcript."""
    long_transcript = [
        {"seat": (i % 12) + 1, "name": f"J{i}", "speech": f"remark {i}"}
        for i in range(60)
    ]
    text = prompts.format_transcript(long_transcript)
    assert prompts.RECENT_REMARKS == 8
    assert text.count("remark ") == 8
    assert "remark 59" in text
    assert "remark 52" in text
    assert "remark 51" not in text
    assert "(...52 earlier remarks omitted...)" in text


def test_format_transcript_takes_an_explicit_limit():
    long_transcript = [{"seat": 1, "name": "J", "speech": f"r{i}"}
                       for i in range(20)]
    text = prompts.format_transcript(long_transcript, limit=3)
    assert text.count("Juror #1 (J):") == 3
    assert "r19" in text and "r17" in text and "r16" not in text
    assert "17 earlier remarks omitted" in text


def test_format_transcript_says_nothing_about_omissions_when_nothing_is_cut():
    assert "omitted" not in prompts.format_transcript(TRANSCRIPT)


# --- assessing one exhibit -------------------------------------------------

def test_juror_assess_prompt_offers_exactly_the_three_positions():
    p = prompts.juror_assess_prompt(CASE, EXHIBIT)
    for position in prompts.POSITIONS:
        assert f'"{position}"' in p
    assert '"reasoning"' in p
    assert '"confidence"' in p


def test_juror_assess_prompt_puts_the_one_exhibit_in_front_of_the_juror():
    p = prompts.juror_assess_prompt(CASE, EXHIBIT)
    assert prompts.exhibit_block(EXHIBIT) in p
    assert prompts.case_brief(CASE) in p
    # the other exhibit's record is not smuggled in
    assert EXHIBITS[1]["record"] not in p


def test_juror_assess_prompt_says_nobody_has_spoken_yet():
    p = prompts.juror_assess_prompt(CASE, EXHIBIT)
    assert "nobody has spoken yet" in p
    assert "nobody is watching" in p


def test_juror_assess_prompt_carries_earlier_findings_when_there_are_any():
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in prompts.juror_assess_prompt(
        CASE, EXHIBIT, FINDINGS)
    assert "ALREADY SETTLED" not in prompts.juror_assess_prompt(CASE, EXHIBIT)


# --- juror speak -----------------------------------------------------------

def test_juror_speak_prompt_contains_the_brief_transcript_and_schema():
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3)
    assert CASE["charge"] in p
    assert "Talk about what?" in p
    for key in ('"speech"', '"lean"', '"confidence"', '"action"'):
        assert key in p


def test_juror_speak_prompt_offers_the_full_action_schema():
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3)
    for action in ("none", "demand_vote", "request_evidence",
                   "propose_experiment", "change_vote", "challenge"):
        assert action in p


def test_juror_speak_prompt_focuses_on_the_open_exhibit():
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3, exhibit=EXHIBIT)
    assert prompts.exhibit_block(EXHIBIT) in p
    assert "EXHIBIT BEFORE THE ROOM" not in prompts.juror_speak_prompt(
        CASE, TRANSCRIPT, seat=3)


def test_juror_speak_prompt_carries_earlier_findings():
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3,
                                   findings=FINDINGS)
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in p


def test_juror_speak_prompt_flags_own_prior_remarks():
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3)
    assert "WHAT YOU'VE ALREADY SAID" in p
    assert "Talk about what?" in p.split("WHAT YOU'VE ALREADY SAID")[1]
    assert "Do not repeat these points" in p


def test_juror_speak_prompt_no_repeat_warning_before_first_speech():
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=9)
    assert "WHAT YOU'VE ALREADY SAID" not in p


def test_juror_speak_prompt_never_claims_the_record_as_the_jurors_own_words():
    p = prompts.juror_speak_prompt(CASE, [RECORD_ENTRY], seat=3)
    assert "WHAT YOU'VE ALREADY SAID" not in p


def test_juror_speak_prompt_carries_a_floor_note():
    note = "Juror #5 has put a direct question to you."
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3, floor_note=note)
    assert note in p
    assert note not in prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3)


def test_juror_speak_prompt_includes_last_tally():
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3, last_tally=TALLY)
    assert "guilty 7, not guilty 5, undecided 0" in p


def test_juror_speak_prompt_no_tally_before_first_vote():
    assert "no vote taken yet" in prompts.juror_speak_prompt(
        CASE, TRANSCRIPT, seat=3)


def test_tally_line_reports_abstentions_only_when_there_are_some():
    p = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3,
                                   last_tally={**TALLY, "abstain": 2})
    assert "abstained 2" in p
    assert "abstained" not in prompts.juror_speak_prompt(
        CASE, TRANSCRIPT, seat=3, last_tally=TALLY)


# --- juror vote ------------------------------------------------------------

def test_juror_vote_prompt_asks_only_for_reasoning_and_vote():
    p = prompts.juror_vote_prompt(CASE, TRANSCRIPT)
    assert '"reasoning"' in p
    assert '"vote"' in p
    assert '"confidence"' not in p
    assert '"undecided"' not in p


def test_show_of_hands_is_public_and_offers_no_abstention():
    p = prompts.juror_vote_prompt(CASE, TRANSCRIPT, method="hands")
    assert "SHOW OF HANDS" in p
    assert "abstain" not in p.lower()


def test_secret_ballot_is_private_and_offers_abstention():
    p = prompts.juror_vote_prompt(CASE, TRANSCRIPT, method="secret")
    assert "SECRET WRITTEN BALLOT" in p
    assert '"abstain"' in p
    assert "cannot end the case" in p


def test_juror_vote_prompt_defaults_to_a_show_of_hands():
    assert prompts.juror_vote_prompt(CASE, TRANSCRIPT) == \
        prompts.juror_vote_prompt(CASE, TRANSCRIPT, method="hands")


def test_juror_vote_prompt_includes_last_tally_and_findings():
    p = prompts.juror_vote_prompt(CASE, TRANSCRIPT,
                                  last_tally={"guilty": 3, "not_guilty": 9,
                                              "undecided": 0},
                                  findings=FINDINGS)
    assert "guilty 3, not guilty 9, undecided 0" in p
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in p


# --- foreman ---------------------------------------------------------------

def test_foreman_prompt_lists_the_actions_and_the_turn():
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 12, 200)
    for action in ("call_on", "call_vote", "rule_on_request", "declare"):
        assert action in p
    assert "Turn 12 of at most 200" in p


def test_close_exhibit_is_offered_only_while_an_exhibit_is_open():
    with_exhibit = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 4, 200,
                                          exhibit=EXHIBIT)
    assert '"action": "close_exhibit"' in with_exhibit
    assert '"finding"' in with_exhibit
    # once the docket is worked through there is nothing left to close
    assert "close_exhibit" not in prompts.foreman_prompt(CASE, TRANSCRIPT,
                                                         TALLY, 4, 200)


def test_foreman_prompt_reports_how_long_this_exhibit_has_run():
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 4, 200,
                               exhibit=EXHIBIT, exhibit_turns=3, remaining=5)
    assert prompts.exhibit_block(EXHIBIT) in p
    assert "spent 3 turns on this exhibit" in p
    assert "5 exhibits remain on the docket" in p


def test_foreman_prompt_says_so_when_the_docket_is_finished():
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 40, 200)
    assert "The docket is finished" in p
    assert "what remains is the verdict" in p


def test_foreman_prompt_carries_earlier_findings():
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 4, 200,
                               findings=FINDINGS)
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in p


def test_foreman_prompt_offers_both_ballot_methods_and_binding_choice():
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, None, 1, 200)
    assert '"hands"' in p and '"secret"' in p
    assert '"binding"' in p
    assert "straw poll" in p


def test_foreman_prompt_lists_open_requests_from_the_floor():
    pending = [{"seat": 5, "summary": "demands a vote by secret"},
               {"seat": 9, "summary": "sends out for: the knife"}]
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 4, 200,
                               pending=pending)
    assert "OPEN REQUESTS FROM THE FLOOR" in p
    assert "Juror #5: demands a vote by secret" in p
    assert "Juror #9: sends out for: the knife" in p


def test_foreman_prompt_omits_the_requests_block_when_nothing_is_open():
    for pending in (None, []):
        assert "OPEN REQUESTS" not in prompts.foreman_prompt(
            CASE, TRANSCRIPT, TALLY, 4, 200, pending=pending)


def test_foreman_prompt_shows_who_has_been_quiet():
    counts = {s: 0 for s in range(1, 13)}
    counts.update({8: 3, 3: 1})
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 4, 200,
                               speech_counts=counts)
    assert "#8:3" in p
    assert "#3:1" in p
    assert "#7:0" in p          # a juror who has not spoken is still listed
    assert "no roster available" in prompts.foreman_prompt(
        CASE, TRANSCRIPT, TALLY, 4, 200)


def test_foreman_prompt_names_only_the_seats_actually_in_the_room():
    """The counts are the roster: a foreman told 1-12 in a smaller room calls
    on empty chairs, and the room cannot carry that out."""
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 4, 200,
                               speech_counts={3: 0, 8: 2, 9: 0})
    assert "#3, #8, #9" in p
    assert "call on those seats and no others" in p.lower()
    assert "1-12" not in p


def test_foreman_prompt_relays_the_judges_instruction():
    p = prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 30, 200,
                               judge_note="Keep deliberating.")
    assert "THE JUDGE SENT YOU BACK: Keep deliberating." in p
    assert "SENT YOU BACK" not in prompts.foreman_prompt(CASE, TRANSCRIPT,
                                                         TALLY, 30, 200)


def test_foreman_system_prompt_leaves_the_room_to_him():
    sys = prompts.foreman_system_prompt()
    assert "unanimous" in sys
    assert "Nothing forces your hand" in sys
    assert "you do not vote" in sys
    assert "one exhibit at a time" in sys


# --- the record goes to exactly one agent ----------------------------------

def _juror_and_foreman_prompts():
    return {
        "assess": prompts.juror_assess_prompt(CASE, EXHIBIT, FINDINGS),
        "speak": prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=3,
                                            last_tally=TALLY,
                                            floor_note="answer him",
                                            exhibit=EXHIBIT,
                                            findings=FINDINGS),
        "vote_hands": prompts.juror_vote_prompt(CASE, TRANSCRIPT, TALLY,
                                                "hands", FINDINGS),
        "vote_secret": prompts.juror_vote_prompt(CASE, TRANSCRIPT, TALLY,
                                                 "secret", FINDINGS),
        "foreman_open": prompts.foreman_prompt(
            CASE, TRANSCRIPT, TALLY, 4, 200,
            pending=[{"seat": 5, "summary": "x"}], speech_counts={3: 1},
            judge_note="back you go", exhibit=EXHIBIT, findings=FINDINGS,
            exhibit_turns=2, remaining=1),
        "foreman_closed": prompts.foreman_prompt(CASE, TRANSCRIPT, TALLY, 40,
                                                 200, findings=FINDINGS),
        "system": prompts.juror_system_prompt(CARD),
        "foreman_system": prompts.foreman_system_prompt(),
    }


def test_no_juror_or_foreman_prompt_carries_the_full_narrative():
    """The whole point of the rewrite: the record stays with the court
    officer, and everyone else works from the brief and one exhibit."""
    for name, text in _juror_and_foreman_prompts().items():
        assert NARRATIVE not in text, name
        assert "every witness is described here" not in text, name


def test_a_juror_turn_is_far_smaller_than_the_case_file():
    """Compactness is a behavior, not an aesthetic — it is what makes a full
    docket affordable to run."""
    big_case = {**CASE, "narrative": NARRATIVE * 200}
    speak = prompts.juror_speak_prompt(big_case, TRANSCRIPT, seat=3,
                                       exhibit=EXHIBIT, findings=FINDINGS)
    assert len(speak) < len(big_case["narrative"]) / 10


def test_a_juror_prompt_does_not_grow_with_the_transcript():
    short = prompts.juror_speak_prompt(CASE, TRANSCRIPT, seat=99)
    long_transcript = [{"seat": 99, "name": "X", "speech": "y" * 200}
                       for _ in range(200)]
    long = prompts.juror_speak_prompt(CASE, long_transcript, seat=99)
    # bounded by RECENT_REMARKS plus the juror's own last three remarks
    assert len(long) < len(short) + 200 * (prompts.RECENT_REMARKS + 3) + 500


# --- bailiff ---------------------------------------------------------------

def test_bailiff_system_prompt_is_neutral_and_never_argues():
    sys = prompts.bailiff_system_prompt()
    assert "neutral" in sys
    assert "never invent a fact" in sys
    assert "the record does not say" in sys
    assert "JSON only" in sys


def test_bailiff_evidence_prompt_is_the_one_prompt_holding_the_record():
    p = prompts.bailiff_evidence_prompt(CASE, "the murder weapon", 8)
    assert NARRATIVE in p
    assert "Juror #8 has sent out for: the murder weapon" in p
    assert '"granted"' in p and '"record"' in p


def test_bailiff_experiment_prompt_carries_the_record_and_the_room():
    p = prompts.bailiff_experiment_prompt(CASE, "time the old man's walk", 5,
                                          TRANSCRIPT)
    assert NARRATIVE in p
    assert "Talk about what?" in p            # the room's discussion so far
    assert "time the old man's walk" in p
    assert "Juror #5" in p
    assert '"possible"' in p and '"result"' in p


def test_bailiff_experiment_prompt_forbids_interpreting_the_result():
    p = prompts.bailiff_experiment_prompt(CASE, "x", 5, TRANSCRIPT)
    assert "Do NOT say what it proves" in p
    assert "inconclusive" in p


# --- judge -----------------------------------------------------------------

def test_judge_system_prompt_rules_on_form_not_merits():
    sys = prompts.judge_system_prompt()
    assert "unanimous" in sys
    assert "never on the merits" in sys
    assert "Do not tell the jury what to decide" in sys
    assert "JSON only" in sys


def test_judge_prompt_carries_the_finding_the_ballot_and_the_schema():
    p = prompts.judge_prompt("guilty", "we all agree", TALLY, 44, TRANSCRIPT)
    assert "Verdict announced: guilty" in p
    assert "we all agree" in p
    assert "guilty 7, not guilty 5, undecided 0" in p
    assert "deliberated 44 turns" in p
    assert '"accept"' in p and '"instruction"' in p


def test_judge_prompt_only_shows_the_tail_of_the_proceedings():
    long_transcript = [
        {"seat": (i % 12) + 1, "name": f"J{i}", "speech": f"remark {i}"}
        for i in range(40)
    ]
    p = prompts.judge_prompt("hung", "deadlock", None, 90, long_transcript)
    assert "remark 39" in p
    assert "remark 30" in p
    assert "remark 29" not in p     # the judge gets a slightly wider window
    assert "no vote taken yet" in p
