"""Juror cards, and a case as {narrative + docket of exhibits}.

The load-bearing rule here is the split between `load_case` and
`load_film_findings`: what the film concluded about an exhibit is display-only
and must have no path into a prompt, so `load_case` strips it and a separate
call hands it to the UI.
"""

import json

import pytest

from loader import (DEFAULT_CASE, list_cases, load_cards, load_case,
                    load_film_findings)

REQUIRED = ("id", "seat", "emoji", "name", "occupation", "temperament",
            "biases", "speech_style")

EXHIBIT = {"id": "the_knife", "name": "The switchblade",
           "prosecution_claim": "one of a kind",
           "record": "bought at 8:45 p.m."}


def write_case(tmp_path, case_id="a_case", docket=None, narrative="THE RECORD"):
    """Lay out a case on disk the way loader expects to find one."""
    (tmp_path / f"{case_id}.md").write_text(narrative)
    if docket is not None:
        (tmp_path / f"{case_id}.exhibits.json").write_text(json.dumps(docket))
    return case_id


# --- juror cards -----------------------------------------------------------

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
        for word in ("dissent", "holdout", "film", "movie",
                     "12 angry", "acquit", "convict"):
            assert word not in blob, f"seat {card['seat']} leaks: {word}"


# --- what a case is now ----------------------------------------------------

def test_list_cases_includes_both_shipped_cases():
    cases = list_cases()
    assert "the_stabbing" in cases
    assert "pier7_arson" in cases


def test_load_case_returns_the_record_and_the_docket():
    case = load_case()
    assert case["id"] == DEFAULT_CASE
    assert set(case) == {"id", "title", "charge", "narrative", "exhibits"}
    assert case["title"]
    assert case["charge"]
    # the narrative is the full record the court officer answers from
    for term in ("knife", "old man", "woman", "el train", "alibi"):
        assert term in case["narrative"].lower()
    assert len(case["exhibits"]) >= 2
    for ex in case["exhibits"]:
        assert set(ex) == {"id", "name", "prosecution_claim", "record"}


def test_load_case_by_id():
    case = load_case("pier7_arson")
    assert case["id"] == "pier7_arson"
    assert "arson" in case["narrative"].lower()
    assert "insurance" in case["narrative"].lower()
    assert [ex["id"] for ex in case["exhibits"]]


def test_load_case_unknown_id_raises():
    with pytest.raises(ValueError):
        load_case("no_such_case")


def test_exhibit_ids_are_unique_within_a_shipped_case():
    for case_id in list_cases():
        ids = [ex["id"] for ex in load_case(case_id)["exhibits"]]
        assert len(ids) == len(set(ids)), case_id


# --- the film's answer never travels with the case -------------------------

def _all_strings(value):
    """Every string anywhere in a nested structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _all_strings(k)
            yield from _all_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _all_strings(v)


def test_load_case_strips_the_film_finding_from_every_exhibit():
    raw = json.loads(open("data/cases/the_stabbing.exhibits.json").read())
    assert any("film_finding" in ex for ex in raw["exhibits"]), \
        "fixture no longer exercises the stripping"
    for ex in load_case("the_stabbing")["exhibits"]:
        assert "film_finding" not in ex


def test_nothing_load_case_returns_contains_any_film_finding_text():
    """The one thing an agent must never be handed: the answer."""
    findings = load_film_findings("the_stabbing")
    assert findings, "fixture no longer exercises this"
    case_text = "\n".join(_all_strings(load_case("the_stabbing")))
    for exhibit_id, finding in findings.items():
        assert finding not in case_text, exhibit_id
        # and not smuggled in as a fragment either
        for sentence in (s.strip() for s in finding.split(".") if
                         len(s.strip()) > 25):
            assert sentence not in case_text, f"{exhibit_id}: {sentence!r}"


def test_load_film_findings_is_keyed_by_exhibit_id():
    findings = load_film_findings("the_stabbing")
    docket_ids = {ex["id"] for ex in load_case("the_stabbing")["exhibits"]}
    assert set(findings) <= docket_ids
    assert all(isinstance(v, str) and v for v in findings.values())


def test_load_film_findings_is_empty_for_a_case_that_has_none():
    assert load_film_findings("pier7_arson") == {}


def test_load_film_findings_is_empty_for_an_unknown_case():
    assert load_film_findings("no_such_case") == {}


def test_load_film_findings_skips_exhibits_without_one(tmp_path):
    case_id = write_case(tmp_path, docket={"exhibits": [
        {**EXHIBIT, "film_finding": "the knife was not unique"},
        {**EXHIBIT, "id": "the_alibi"}]})
    assert load_film_findings(case_id, cases_dir=tmp_path) == {
        "the_knife": "the knife was not unique"}


# --- docket validation -----------------------------------------------------

def test_a_case_without_a_docket_is_rejected(tmp_path):
    case_id = write_case(tmp_path, docket=None)
    with pytest.raises(ValueError, match="no exhibit docket"):
        load_case(case_id, cases_dir=tmp_path)


def test_an_empty_docket_is_rejected(tmp_path):
    for docket in ({"exhibits": []}, {}):
        case_id = write_case(tmp_path, docket=docket)
        with pytest.raises(ValueError, match="empty docket"):
            load_case(case_id, cases_dir=tmp_path)


@pytest.mark.parametrize("field",
                         ["id", "name", "prosecution_claim", "record"])
def test_an_exhibit_missing_a_field_is_rejected(field, tmp_path):
    case_id = write_case(tmp_path,
                         docket={"exhibits": [{k: v for k, v in EXHIBIT.items()
                                               if k != field}]})
    with pytest.raises(ValueError, match=field):
        load_case(case_id, cases_dir=tmp_path)


def test_duplicate_exhibit_ids_are_rejected(tmp_path):
    case_id = write_case(tmp_path, docket={"exhibits": [
        EXHIBIT, {**EXHIBIT, "name": "The same knife again"}]})
    with pytest.raises(ValueError, match="duplicate exhibit id"):
        load_case(case_id, cases_dir=tmp_path)


def test_title_and_charge_fall_back_when_the_docket_omits_them(tmp_path):
    case_id = write_case(tmp_path, docket={"exhibits": [EXHIBIT]})
    case = load_case(case_id, cases_dir=tmp_path)
    assert case["title"] == "The State v. the Defendant"
    assert case["charge"] == ""
    assert case["narrative"] == "THE RECORD"
