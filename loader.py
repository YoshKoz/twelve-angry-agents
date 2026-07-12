"""Load and validate juror cards + case file."""

import json
from pathlib import Path

REQUIRED_FIELDS = ("id", "seat", "name", "occupation", "temperament",
                   "biases", "speech_style")


def load_cards(path="data/jurors"):
    cards = []
    for p in sorted(Path(path).glob("juror_*.json")):
        card = json.loads(p.read_text())
        for field in REQUIRED_FIELDS:
            if not card.get(field):
                raise ValueError(f"{p.name}: missing field {field!r}")
        cards.append(card)
    if len(cards) != 12:
        raise ValueError(f"expected 12 juror cards, found {len(cards)}")
    cards.sort(key=lambda c: c["seat"])
    if [c["seat"] for c in cards] != list(range(1, 13)):
        raise ValueError("seats must be exactly 1..12")
    return cards


def load_case(path="data/case_file.md"):
    return Path(path).read_text()
