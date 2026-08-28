from tally import VOTE_VALUES, tally, unanimous

EMPTY = {"guilty": 0, "not_guilty": 0, "undecided": 0, "abstain": 0}


def test_vote_values_include_abstain():
    assert set(VOTE_VALUES) == {"guilty", "not_guilty", "undecided", "abstain"}


def test_tally_counts_each_category():
    votes = {1: "guilty", 2: "guilty", 3: "not_guilty", 4: "undecided",
             5: "abstain"}
    assert tally(votes) == {"guilty": 2, "not_guilty": 1, "undecided": 1,
                            "abstain": 1}


def test_tally_empty():
    assert tally({}) == EMPTY


def test_tally_always_reports_every_category():
    assert set(tally({1: "guilty"})) == set(VOTE_VALUES)


def test_unanimous_guilty():
    assert unanimous({**EMPTY, "guilty": 12}) == "guilty"


def test_unanimous_not_guilty():
    assert unanimous({**EMPTY, "not_guilty": 12}) == "not_guilty"


def test_not_unanimous():
    assert unanimous({**EMPTY, "guilty": 11, "not_guilty": 1}) is None
    assert unanimous({**EMPTY, "not_guilty": 11, "undecided": 1}) is None


def test_unanimous_counts_against_votes_cast_not_a_hardcoded_twelve():
    # a smaller room agreeing is still unanimous
    assert unanimous({**EMPTY, "guilty": 5}) == "guilty"
    assert unanimous({**EMPTY, "not_guilty": 1}) == "not_guilty"


def test_an_abstention_can_never_be_unanimous():
    assert unanimous({**EMPTY, "not_guilty": 11, "abstain": 1}) is None
    assert unanimous({**EMPTY, "guilty": 11, "abstain": 1}) is None
    assert unanimous({**EMPTY, "abstain": 12}) is None


def test_unanimous_of_an_empty_tally_is_none():
    assert unanimous(EMPTY) is None
    assert unanimous(tally({})) is None
