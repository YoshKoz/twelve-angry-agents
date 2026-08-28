"""Load and validate juror cards + cases.

A case is a narrative record (`<id>.md`, what the clerk reads and what the
court answers from) plus a docket of exhibits (`<id>.exhibits.json`) that the
room takes one at a time.
"""

import json
from pathlib import Path

REQUIRED_FIELDS = ("id", "seat", "emoji", "name", "occupation", "temperament",
                   "biases", "speech_style")
EXHIBIT_FIELDS = ("id", "name", "prosecution_claim", "record")

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
    """Load a case: its narrative record and its docket of exhibits.

    Returns {id, title, charge, narrative, exhibits}. `film_finding` is
    stripped from every exhibit here — it is what the film concluded, kept for
    the UI to compare against, and no agent may ever see it (HONESTY_RULE).
    """
    case_id = case_id or DEFAULT_CASE
    path = Path(cases_dir) / f"{case_id}.md"
    if not path.exists():
        raise ValueError(f"unknown case {case_id!r}")

    docket_path = Path(cases_dir) / f"{case_id}.exhibits.json"
    if not docket_path.exists():
        raise ValueError(f"case {case_id!r} has no exhibit docket")
    docket = json.loads(docket_path.read_text())

    exhibits = docket.get("exhibits") or []
    if not exhibits:
        raise ValueError(f"case {case_id!r} has an empty docket")
    seen = set()
    for ex in exhibits:
        for field in EXHIBIT_FIELDS:
            if not ex.get(field):
                raise ValueError(
                    f"{docket_path.name}: exhibit missing field {field!r}")
        if ex["id"] in seen:
            raise ValueError(
                f"{docket_path.name}: duplicate exhibit id {ex['id']!r}")
        seen.add(ex["id"])

    return {
        "id": case_id,
        "title": docket.get("title", "The State v. the Defendant"),
        "charge": docket.get("charge", ""),
        "narrative": path.read_text(),
        "exhibits": [{k: v for k, v in ex.items() if k != "film_finding"}
                     for ex in exhibits],
    }


def load_film_findings(case_id=None, cases_dir="data/cases"):
    """What the film concluded about each exhibit, for the UI's comparison
    column. Deliberately a separate call from load_case so this text has no
    path into a prompt."""
    path = Path(cases_dir) / f"{case_id or DEFAULT_CASE}.exhibits.json"
    if not path.exists():
        return {}
    docket = json.loads(path.read_text())
    return {ex["id"]: ex["film_finding"]
            for ex in docket.get("exhibits", []) if ex.get("film_finding")}
