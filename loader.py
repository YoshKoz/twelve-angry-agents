"""Load and validate juror cards + case file."""

import json
from pathlib import Path

REQUIRED_FIELDS = ("id", "seat", "emoji", "name", "occupation", "temperament",
                   "biases", "speech_style")

DEFAULT_CASE = "the_stabbing"


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


def list_cases(cases_dir="data/cases"):
    """Return available case ids, sorted."""
    return sorted(p.stem for p in Path(cases_dir).glob("*.md"))


def load_case(case_id=None, cases_dir="data/cases"):
    """Load a case file's text by id (filename without .md)."""
    path = Path(cases_dir) / f"{case_id or DEFAULT_CASE}.md"
    if not path.exists():
        raise ValueError(f"unknown case {case_id!r}")
    return path.read_text()
