import pytest

from loader import load_cards, load_case

REQUIRED = ("id", "seat", "name", "occupation", "temperament",
            "biases", "speech_style")


def test_loads_exactly_12_cards_sorted_by_seat():
    cards = load_cards()
    assert len(cards) == 12
    assert [c["seat"] for c in cards] == list(range(1, 13))


def test_every_card_has_all_fields_nonempty():
    for card in load_cards():
        for field in REQUIRED:
            assert card.get(field), f"seat {card.get('seat')}: missing {field}"


def test_no_card_leaks_script_knowledge():
    for card in load_cards():
        blob = " ".join(str(v) for v in card.values()).lower()
        for word in ("dissent", "holdout", "film", "movie", "fonda",
                     "12 angry", "acquit", "convict"):
            assert word not in blob, f"seat {card['seat']} leaks: {word}"


def test_case_file_loads_and_mentions_key_evidence():
    case = load_case()
    for term in ("knife", "old man", "woman", "el train", "alibi"):
        assert term in case.lower()
