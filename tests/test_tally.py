from tally import tally, unanimous


def test_tally_counts_each_category():
    votes = {1: "guilty", 2: "guilty", 3: "not_guilty", 4: "undecided"}
    assert tally(votes) == {"guilty": 2, "not_guilty": 1, "undecided": 1}


def test_tally_empty():
    assert tally({}) == {"guilty": 0, "not_guilty": 0, "undecided": 0}


def test_unanimous_guilty():
    assert unanimous({"guilty": 12, "not_guilty": 0, "undecided": 0}) == "guilty"


def test_unanimous_not_guilty():
    assert unanimous({"guilty": 0, "not_guilty": 12, "undecided": 0}) == "not_guilty"


def test_not_unanimous():
    assert unanimous({"guilty": 11, "not_guilty": 1, "undecided": 0}) is None
    assert unanimous({"guilty": 0, "not_guilty": 11, "undecided": 1}) is None
