# Twelve Angry Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Multi-agent re-enactment of *12 Angry Men*: 12 LLM jurors + 1 foreman agent deliberate the original case to an emergent verdict, watched live in the browser as a visual novel.

**Architecture:** A pure-Python orchestrator sequences stateless `claude -p` calls (one per agent turn), holds all state (transcript, private leans, tallies), and emits a flat event stream. FastAPI streams those events over SSE to a static visual-novel page that is a pure renderer. The event log doubles as a replayable transcript.

**Tech Stack:** Python 3.12+, FastAPI, uvicorn, pytest, `claude` CLI (print mode), vanilla ES-module JS (no build step), `node --test` for the UI reducer.

## Global Constraints

- LLM calls go through `claude -p` subprocess ONLY. Never the Anthropic SDK, never `ANTHROPIC_API_KEY` (user is on Claude Max).
- Never suppress errors (`|| true`, silent retries without cap, swallowed stderr).
- Every agent system prompt ends with the honesty rule (verbatim, defined in `prompts.py` as `HONESTY_RULE`).
- Private juror `lean`/`confidence` must NEVER appear in any emitted event — only public votes.
- Hard turn cap default 200 → forced `hung`.
- No two `call_vote`s without intervening speech.
- Project root: `~/code/mine/twelve-angry-agents/`. All paths below relative to it.
- Run Python tests with `python -m pytest tests/ -v` from project root.
- v1 out of scope: alternate cases, AI portrait art, batch stats, fast/round-robin mode toggle.

---

### Task 1: Scaffold + tally functions

**Files:**
- Create: `tally.py`
- Create: `tests/test_tally.py`
- Create: `requirements.txt`
- Create: `.gitignore` (append if exists)

**Interfaces:**
- Produces: `tally(votes: dict[int, str]) -> dict` with keys `guilty`, `not_guilty`, `undecided` (int counts); `unanimous(counts: dict) -> str | None` returning `"guilty"`, `"not_guilty"`, or `None`. Used by Task 6.

- [ ] **Step 1: Write requirements.txt and .gitignore**

`requirements.txt`:
```
fastapi
uvicorn
pytest
```

`.gitignore` (append these lines if the file exists):
```
__pycache__/
transcripts/
.pytest_cache/
```

Install: `pip install -r requirements.txt` (or `pacman`-provided python-fastapi/uvicorn/pytest if already system-installed — check `python -c "import fastapi, uvicorn, pytest"` first).

- [ ] **Step 2: Write the failing test**

`tests/test_tally.py`:
```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_tally.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tally'`

- [ ] **Step 4: Write minimal implementation**

`tally.py`:
```python
"""Pure vote-counting helpers. No I/O, no LLM."""

VOTE_VALUES = ("guilty", "not_guilty", "undecided")


def tally(votes):
    """votes: {seat: vote_string} -> counts per category."""
    counts = {v: 0 for v in VOTE_VALUES}
    for v in votes.values():
        counts[v] += 1
    return counts


def unanimous(counts):
    """Return the verdict string if all 12 agree, else None."""
    if counts["guilty"] == 12:
        return "guilty"
    if counts["not_guilty"] == 12:
        return "not_guilty"
    return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_tally.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add tally.py tests/test_tally.py requirements.txt .gitignore
git commit -m "feat: vote tally pure functions + project scaffold"
```

---

### Task 2: agent.py — `ask()` subprocess wrapper

**Files:**
- Create: `agent.py`
- Create: `tests/test_agent.py`

**Interfaces:**
- Produces: `ask(system_prompt: str, user_prompt: str, timeout: int = 120) -> str` (stripped stdout); raises `AgentError` on repeated failure. Exception classes `AgentError(Exception)`, `MalformedReply(AgentError)`.
- Consumed by Task 3 (`ask_json`) and Task 8 (live adapters).

- [ ] **Step 1: Write the failing tests**

`tests/test_agent.py`:
```python
import subprocess
from unittest.mock import patch, MagicMock

import pytest

import agent


def ok(stdout):
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


def fail(stderr="boom", code=1):
    m = MagicMock()
    m.returncode = code
    m.stdout = ""
    m.stderr = stderr
    return m


def test_ask_builds_claude_p_command():
    with patch("agent.subprocess.run", return_value=ok("hello")) as run:
        out = agent.ask("SYS", "USER")
    assert out == "hello"
    cmd = run.call_args[0][0]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "USER" in cmd
    assert "--system-prompt" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"


def test_ask_strips_output():
    with patch("agent.subprocess.run", return_value=ok("  text \n")):
        assert agent.ask("s", "u") == "text"


def test_ask_retries_once_on_nonzero_exit_then_succeeds():
    with patch("agent.subprocess.run", side_effect=[fail(), ok("second")]) as run:
        assert agent.ask("s", "u") == "second"
    assert run.call_count == 2


def test_ask_raises_after_two_failures_with_stderr():
    with patch("agent.subprocess.run", side_effect=[fail("err msg"), fail("err msg")]):
        with pytest.raises(agent.AgentError, match="err msg"):
            agent.ask("s", "u")


def test_ask_retries_once_on_timeout_then_raises():
    exc = subprocess.TimeoutExpired(cmd="claude", timeout=5)
    with patch("agent.subprocess.run", side_effect=[exc, exc]) as run:
        with pytest.raises(agent.AgentError, match="timed out"):
            agent.ask("s", "u", timeout=5)
    assert run.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent'`

- [ ] **Step 3: Write minimal implementation**

`agent.py`:
```python
"""Stateless wrapper around `claude -p`. One call = one agent turn.

NEVER use the Anthropic SDK or an API key here — user is on Claude Max,
all LLM calls go through the claude CLI subprocess.
"""

import json
import subprocess


class AgentError(Exception):
    """claude -p failed after retries."""


class MalformedReply(AgentError):
    """Agent kept returning invalid JSON."""


def ask(system_prompt, user_prompt, timeout=120):
    """Spawn `claude -p`, return stripped stdout. Retries once on failure."""
    cmd = ["claude", "-p", user_prompt, "--system-prompt", system_prompt]
    last_err = None
    for attempt in range(2):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            last_err = f"claude -p timed out after {timeout}s"
            continue
        if proc.returncode == 0:
            return proc.stdout.strip()
        last_err = f"claude -p exit {proc.returncode}: {proc.stderr.strip()}"
    raise AgentError(last_err)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_agent.py
git commit -m "feat: agent.ask claude -p wrapper with retry + timeout"
```

---

### Task 3: agent.py — `ask_json()` structured output with retry

**Files:**
- Modify: `agent.py` (append)
- Modify: `tests/test_agent.py` (append)

**Interfaces:**
- Produces: `ask_json(system_prompt, user_prompt, required_keys: list[str], timeout=120, retries=3) -> dict`; raises `MalformedReply` after `retries` bad replies. Consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent.py`:
```python
def test_ask_json_parses_clean_json():
    with patch("agent.ask", return_value='{"speech": "hi", "lean": "guilty"}'):
        obj = agent.ask_json("s", "u", ["speech", "lean"])
    assert obj == {"speech": "hi", "lean": "guilty"}


def test_ask_json_extracts_json_from_surrounding_prose():
    reply = 'Here is my answer:\n{"vote": "not_guilty"}\nThanks!'
    with patch("agent.ask", return_value=reply):
        assert agent.ask_json("s", "u", ["vote"]) == {"vote": "not_guilty"}


def test_ask_json_retries_on_malformed_with_schema_reminder():
    with patch("agent.ask", side_effect=["not json at all",
                                         '{"vote": "guilty"}']) as ask:
        obj = agent.ask_json("s", "u", ["vote"])
    assert obj == {"vote": "guilty"}
    assert ask.call_count == 2
    retry_prompt = ask.call_args_list[1][0][1]
    assert "ONLY valid JSON" in retry_prompt
    assert "vote" in retry_prompt


def test_ask_json_retries_on_missing_required_key():
    with patch("agent.ask", side_effect=['{"wrong": 1}', '{"vote": "guilty"}']):
        assert agent.ask_json("s", "u", ["vote"]) == {"vote": "guilty"}


def test_ask_json_raises_malformed_after_retries():
    with patch("agent.ask", side_effect=["bad", "bad", "bad"]) as ask:
        with pytest.raises(agent.MalformedReply):
            agent.ask_json("s", "u", ["vote"], retries=3)
    assert ask.call_count == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent.py -v`
Expected: new tests FAIL with `AttributeError: module 'agent' has no attribute 'ask_json'`

- [ ] **Step 3: Write minimal implementation**

Append to `agent.py`:
```python
def _extract_json(text):
    """Best-effort: parse the outermost {...} block. Returns dict or None."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def ask_json(system_prompt, user_prompt, required_keys, timeout=120, retries=3):
    """ask(), then parse + validate JSON. Retries with a schema reminder."""
    schema_hint = json.dumps({k: "..." for k in required_keys})
    prompt = user_prompt
    for _ in range(retries):
        text = ask(system_prompt, prompt, timeout=timeout)
        obj = _extract_json(text)
        if obj is not None and all(k in obj for k in required_keys):
            return obj
        prompt = (user_prompt
                  + "\n\nReturn ONLY valid JSON matching this schema, "
                  + "no prose: " + schema_hint)
    raise MalformedReply(
        f"no valid JSON with keys {required_keys} after {retries} attempts")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_agent.py
git commit -m "feat: agent.ask_json with JSON extraction and schema-reminder retry"
```

---

### Task 4: prompts.py — prompt assembly

**Files:**
- Create: `prompts.py`
- Create: `tests/test_prompts.py`

**Interfaces:**
- Produces (all return `str`):
  - `HONESTY_RULE` (module constant)
  - `format_transcript(transcript: list[dict]) -> str` — transcript entries are `{"seat": int, "name": str, "speech": str}`
  - `juror_system_prompt(card: dict) -> str`
  - `juror_speak_prompt(case_text: str, transcript: list) -> str`
  - `juror_vote_prompt(case_text: str, transcript: list) -> str`
  - `foreman_system_prompt() -> str`
  - `foreman_prompt(transcript: list, last_tally: dict | None, turn: int, turn_cap: int) -> str`
- Card shape (from Task 5): `{id, seat, name, occupation, temperament, biases, speech_style}`.
- Consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

`tests/test_prompts.py`:
```python
import prompts

CARD = {
    "id": "juror_03", "seat": 3, "name": "Frank Della Rocca",
    "occupation": "messenger-service owner",
    "temperament": "loud, quick to anger",
    "biases": "takes disagreement personally",
    "speech_style": "blunt, jabbing",
}

TRANSCRIPT = [
    {"seat": 8, "name": "Davis", "speech": "I just want to talk."},
    {"seat": 3, "name": "Frank Della Rocca", "speech": "Talk about what?"},
]


def test_honesty_rule_in_every_system_prompt():
    assert prompts.HONESTY_RULE in prompts.juror_system_prompt(CARD)
    assert prompts.HONESTY_RULE in prompts.foreman_system_prompt()


def test_juror_system_prompt_has_card_fields_but_no_script_hints():
    sys = prompts.juror_system_prompt(CARD)
    for field in ("seat", "name", "occupation", "temperament",
                  "biases", "speech_style"):
        assert str(CARD[field]) in sys or field == "seat"
    assert "Juror #3" in sys
    # blind flip: no dissent/plot hints anywhere
    for word in ("dissent", "film", "movie", "holdout", "12 Angry"):
        assert word.lower() not in sys.lower()


def test_format_transcript_lines_and_empty():
    text = prompts.format_transcript(TRANSCRIPT)
    assert "Juror #8 (Davis): I just want to talk." in text
    assert prompts.format_transcript([]) == "(no one has spoken yet)"


def test_juror_speak_prompt_contains_case_transcript_and_schema():
    p = prompts.juror_speak_prompt("THE CASE", TRANSCRIPT)
    assert "THE CASE" in p
    assert "Talk about what?" in p
    for key in ('"speech"', '"lean"', '"confidence"'):
        assert key in p


def test_juror_vote_prompt_asks_only_for_vote():
    p = prompts.juror_vote_prompt("THE CASE", TRANSCRIPT)
    assert '"vote"' in p
    assert '"confidence"' not in p


def test_foreman_prompt_lists_all_three_actions():
    p = prompts.foreman_prompt(TRANSCRIPT, {"guilty": 7, "not_guilty": 5,
                                            "undecided": 0}, 12, 200)
    for action in ("call_on", "call_vote", "declare"):
        assert action in p
    assert "Turn 12 of max 200" in p
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prompts'`

- [ ] **Step 3: Write minimal implementation**

`prompts.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add prompts.py tests/test_prompts.py
git commit -m "feat: prompt assembly for jurors and foreman with honesty rule"
```

---

### Task 5: Data — case file + 12 juror cards

**Files:**
- Create: `data/case_file.md`
- Create: `data/jurors/juror_01.json` … `data/jurors/juror_12.json`
- Create: `loader.py`
- Create: `tests/test_loader.py`

**Interfaces:**
- Produces: `load_cards(path="data/jurors") -> list[dict]` (sorted by seat, validated); `load_case(path="data/case_file.md") -> str`. Consumed by Task 8.
- Card schema: `{id: str, seat: int 1-12, name: str, occupation: str, temperament: str, biases: str, speech_style: str}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_loader.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loader'`

- [ ] **Step 3: Write loader.py**

`loader.py`:
```python
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
```

- [ ] **Step 4: Write the case file**

`data/case_file.md`:
```markdown
# The State v. the Defendant — Case File

## The Charge

The defendant, an 18-year-old man raised in a tenement slum, is charged with
first-degree murder: stabbing his father to death in their apartment shortly
after midnight. A guilty verdict carries a mandatory death sentence. The jury
must be unanimous either way.

## Undisputed Facts

- The father was killed by a single stab wound to the chest, inflicted with a
  switchblade knife, at approximately 12:10 a.m.
- The defendant and his father were heard arguing around 8:00 p.m. that
  evening; neighbors say the father struck the boy twice.
- The defendant left the apartment around 8:00 p.m. and says he returned about
  3:10 a.m. to find the police there.
- The murder weapon was a switchblade with an unusual carved handle. The
  defendant had bought an identical knife earlier that evening; he says he lost
  it through a hole in his pocket. The knife found in the father's chest had
  been wiped clean of fingerprints.
- The defendant has a record: arrested at 15 for knife-fighting, plus car theft
  and mugging.

## Prosecution Testimony

**The old man downstairs** lives directly below the apartment. He testified he
heard the boy yell "I'm going to kill you!", heard a body hit the floor a
second later, ran to his front door, and saw the boy running down the stairs
and out of the house. He reached his door in about 15 seconds, he says. He
walks with a limp — he dragged one leg after a stroke last year.

**The woman across the street** lives on the other side of the elevated train
tracks, directly opposite the boy's window. She testified she was in bed,
looked out her window, and saw the boy stab his father — through the windows
of a passing el train. She identified the boy in court. She wore no glasses in
court; she has marks on the sides of her nose.

**The el train**: a six-car elevated train was passing the window at the time
of the killing. A passing el takes about ten seconds to clear a given point,
and it is very loud.

**The storekeeper** testified he sold the defendant the identical switchblade
at about 8:45 p.m. and that it was the only one of its kind he had in stock.

## Defense Testimony

**The defendant** testified he was at the movies from 11:30 p.m. to 3:10 a.m.
Under police questioning hours after his father's death, he could not remember
the names of the films he saw or who starred in them. No one at the theater
remembered seeing him.

**On the argument**: the defendant admits the fight at 8:00 p.m. and that his
father hit him. He says that is why he left — to cool off.

**On the knife**: he says he bought it as a present for a friend and lost it
before midnight.

## Points Raised at Trial

- The defense cross-examination was widely seen as weak; the court-appointed
  attorney seemed disengaged.
- The wound angled downward into the chest. The defendant is 5'7"; his father
  was 6'2".
- The old man's apartment is a 12-metre hallway from bedroom to front door.
- The woman said the killing happened the instant she looked out; the last two
  cars of the el were passing.
```

- [ ] **Step 5: Write the 12 juror cards**

Each card seeds a film character's *personality only* — no plot, no dissent
roles, no verdict hints (blind flips).

`data/jurors/juror_01.json`:
```json
{
  "id": "juror_01",
  "seat": 1,
  "name": "Walt Novak",
  "occupation": "assistant high-school football coach",
  "temperament": "earnest, orderly, uncomfortable with open conflict; wants everyone to get a fair say",
  "biases": "defers to procedure; can mistake keeping the peace for making progress",
  "speech_style": "polite, coach-like encouragement, short sentences"
}
```

`data/jurors/juror_02.json`:
```json
{
  "id": "juror_02",
  "seat": 2,
  "name": "Arthur Bell",
  "occupation": "bank teller",
  "temperament": "meek, eager to please, flustered when challenged; genuinely wants to do right",
  "biases": "tends to adopt the opinion of the last confident speaker",
  "speech_style": "hesitant, apologetic, trails off, occasionally surprises with a sharp observation"
}
```

`data/jurors/juror_03.json`:
```json
{
  "id": "juror_03",
  "seat": 3,
  "name": "Frank Della Rocca",
  "occupation": "owner of a messenger service he built from nothing",
  "temperament": "loud, domineering, quick to anger; painful estrangement from his own grown son colors everything about fathers and sons",
  "biases": "believes kids today have no respect; takes disagreement as personal insult",
  "speech_style": "blunt, jabbing, interrupts, pounds the table"
}
```

`data/jurors/juror_04.json`:
```json
{
  "id": "juror_04",
  "seat": 4,
  "name": "Charles Whitmore",
  "occupation": "stockbroker",
  "temperament": "cool, unemotional, rigorously logical; never sweats, never raises his voice",
  "biases": "trusts facts and testimony over sentiment; slightly condescending toward emotional argument",
  "speech_style": "precise, measured, cites specifics from memory"
}
```

`data/jurors/juror_05.json`:
```json
{
  "id": "juror_05",
  "seat": 5,
  "name": "Sal Mercado",
  "occupation": "hospital orderly who grew up in a tenement slum",
  "temperament": "quiet, watchful, defensive about his origins; slow to speak but firm when he does",
  "biases": "bristles when people generalize about slum kids; first-hand knowledge of street life and knife fights",
  "speech_style": "plain, direct, flares up only when his background is insulted"
}
```

`data/jurors/juror_06.json`:
```json
{
  "id": "juror_06",
  "seat": 6,
  "name": "Stan Kowalski",
  "occupation": "house painter",
  "temperament": "steady, physical, unpretentious; protective of older people and of fair play",
  "biases": "distrusts fast talkers; values a man's right to finish his sentence",
  "speech_style": "working-man's plain talk, short and sincere"
}
```

`data/jurors/juror_07.json`:
```json
{
  "id": "juror_07",
  "seat": 7,
  "name": "Eddie Marchetti",
  "occupation": "marmalade salesman",
  "temperament": "impatient, wisecracking, allergic to being stuck in a hot room; has tickets to tonight's ball game burning a hole in his pocket",
  "biases": "wants this over with; treats the whole thing as an inconvenience",
  "speech_style": "flippant, jokey, sales-patter, checks his watch"
}
```

`data/jurors/juror_08.json`:
```json
{
  "id": "juror_08",
  "seat": 8,
  "name": "James Davis",
  "occupation": "architect",
  "temperament": "thoughtful, patient, conscience-driven; takes the weight of the decision seriously and hates rushing it",
  "biases": "instinctively suspicious of easy certainty; insists questions get asked even when unpopular",
  "speech_style": "calm, probing questions, appeals to what is at stake"
}
```

`data/jurors/juror_09.json`:
```json
{
  "id": "juror_09",
  "seat": 9,
  "name": "Elias McCardle",
  "occupation": "retired, elderly",
  "temperament": "gentle, sharply observant of small human details others miss; feels invisible because of his age",
  "biases": "empathy for old people who want to matter; patience with the overlooked",
  "speech_style": "soft-spoken, courteous, builds observations carefully"
}
```

`data/jurors/juror_10.json`:
```json
{
  "id": "juror_10",
  "seat": 10,
  "name": "Vic Harmon",
  "occupation": "garage owner",
  "temperament": "loud, coarse, perpetually aggrieved; nursing a head cold and a grudge against the world",
  "biases": "openly prejudiced against people from the slums — 'you know how those people are'; generalizes from anecdote",
  "speech_style": "ranting, sneering, talks over others"
}
```

`data/jurors/juror_11.json`:
```json
{
  "id": "juror_11",
  "seat": 11,
  "name": "Milos Novotny",
  "occupation": "watchmaker, European immigrant",
  "temperament": "precise, formal, courteous; reveres the jury system as a privilege of his adopted country",
  "biases": "intolerant of jurors who treat the duty lightly; attentive to exact details of testimony",
  "speech_style": "careful accented English, formal phrasing, polite but firm"
}
```

`data/jurors/juror_12.json`:
```json
{
  "id": "juror_12",
  "seat": 12,
  "name": "Brad Sherwood",
  "occupation": "advertising executive",
  "temperament": "glib, sociable, chronically indecisive under pressure; doodles when bored",
  "biases": "swayed by whoever framed the argument best last; thinks in slogans and pitches",
  "speech_style": "breezy ad-speak, 'let's run it up the flagpole' idioms"
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_loader.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add loader.py tests/test_loader.py data/
git commit -m "feat: case file, 12 blind juror cards, validating loader"
```

---

### Task 6: Orchestrator core — juror turns, votes, declare, transcript

**Files:**
- Create: `orchestrator.py`
- Create: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `tally`, `unanimous` from `tally.py` (Task 1).
- Produces: class `Deliberation` with:
  - `__init__(self, case_text, cards, juror_fn, foreman_fn, emit, turn_cap=200, transcript_dir="transcripts", run_id=None)`
  - `juror_fn(card: dict, case_text: str, transcript: list, mode: str) -> dict` — `mode="speak"` returns `{"speech", "lean", "confidence"}`; `mode="vote"` returns `{"vote"}`. May raise; orchestrator handles.
  - `foreman_fn(transcript: list, last_tally: dict | None, turn: int, turn_cap: int) -> dict` — one of the three action dicts. May raise.
  - `emit(event: dict)` — callback, called for every event in order.
  - Methods used by Task 7: `_call_on(seat)`, `_call_vote()`, `_declare(verdict, reason)`, `_write_transcript() -> Path`, attributes `transcript`, `events`, `leans`, `last_tally`, `verdict`, `spoke_since_vote`.
- Event shapes (the full v1 event vocabulary):
  - `{"type": "case", "text": str}`
  - `{"type": "roster", "jurors": [{"seat", "name", "occupation"}]}`
  - `{"type": "speaker", "seat": int}`
  - `{"type": "speech", "seat": int, "name": str, "speech": str}`
  - `{"type": "vote_called"}`
  - `{"type": "vote_result", "votes": {seat: vote}, "tally": {counts}}`
  - `{"type": "verdict", "verdict": str, "reason": str}`
  - `{"type": "error", "message": str}` (emitted by server, Task 8)

- [ ] **Step 1: Write the failing tests**

`tests/test_orchestrator.py`:
```python
import json

import pytest

from orchestrator import Deliberation

CARDS = [
    {"id": f"juror_{n:02d}", "seat": n, "name": f"J{n}", "occupation": f"job{n}",
     "temperament": "t", "biases": "b", "speech_style": "s"}
    for n in range(1, 13)
]


def collect():
    events = []
    return events, events.append


def make_juror(speech="I think...", lean="undecided", vote="guilty"):
    def fn(card, case_text, transcript, mode):
        if mode == "speak":
            return {"speech": speech, "lean": lean, "confidence": 0.5}
        return {"vote": vote}
    return fn


def scripted_foreman(actions):
    it = iter(actions)
    def fn(transcript, last_tally, turn, turn_cap):
        return next(it)
    return fn


def make_delib(juror_fn, foreman_fn, events_emit, tmp_path, cap=200):
    return Deliberation("CASE", CARDS, juror_fn, foreman_fn, events_emit,
                        turn_cap=cap, transcript_dir=tmp_path, run_id="test")


def test_call_on_records_speech_and_private_lean(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(speech="hello", lean="not_guilty"),
                   scripted_foreman([]), emit, tmp_path)
    d._call_on(5)
    assert [e["type"] for e in events] == ["speaker", "speech"]
    assert events[1] == {"type": "speech", "seat": 5, "name": "J5",
                         "speech": "hello"}
    assert d.leans[5] == {"lean": "not_guilty", "confidence": 0.5}
    assert d.transcript == [{"seat": 5, "name": "J5", "speech": "hello"}]
    assert d.spoke_since_vote is True


def test_call_on_juror_failure_becomes_pass(tmp_path):
    def broken(card, case_text, transcript, mode):
        raise RuntimeError("llm died")
    events, emit = collect()
    d = make_delib(broken, scripted_foreman([]), emit, tmp_path)
    d._call_on(2)
    assert events[-1]["type"] == "speech"
    assert "passes" in events[-1]["speech"]
    assert d.transcript == []          # a pass is not part of the record


def test_call_vote_polls_all_12_and_tallies(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(vote="guilty"), scripted_foreman([]), emit,
                   tmp_path)
    d._call_vote()
    types = [e["type"] for e in events]
    assert types[:2] == ["vote_called", "vote_result"]
    result = events[1]
    assert len(result["votes"]) == 12
    assert result["tally"]["guilty"] == 12
    # unanimous vote declares immediately
    assert events[2]["type"] == "verdict"
    assert d.verdict == "guilty"
    assert d.spoke_since_vote is False


def test_call_vote_invalid_or_failing_vote_counts_undecided(tmp_path):
    def flaky(card, case_text, transcript, mode):
        if card["seat"] == 1:
            raise RuntimeError("dead")
        if card["seat"] == 2:
            return {"vote": "banana"}
        return {"vote": "guilty"}
    events, emit = collect()
    d = make_delib(flaky, scripted_foreman([]), emit, tmp_path)
    d._call_vote()
    tally = events[1]["tally"]
    assert tally == {"guilty": 10, "not_guilty": 0, "undecided": 2}
    assert d.verdict is None


def test_private_leans_never_emitted(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(lean="guilty"), scripted_foreman([]), emit,
                   tmp_path)
    d._call_on(1)
    d._call_vote()
    blob = json.dumps(events)
    assert "lean" not in blob
    assert "confidence" not in blob


def test_write_transcript_dumps_all_events(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(), scripted_foreman([]), emit, tmp_path)
    d._call_on(1)
    path = d._write_transcript()
    saved = json.loads(path.read_text())
    assert saved == events
    assert path.name == "test.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator'`

- [ ] **Step 3: Write the implementation**

`orchestrator.py`:
```python
"""Deliberation state machine. Sequences agent calls, owns all state,
emits events. Contains NO LLM logic of its own — agents are injected
callables, so tests drive it with deterministic fakes."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tally import tally, unanimous, VOTE_VALUES

TURN_CAP = 200


class Deliberation:
    def __init__(self, case_text, cards, juror_fn, foreman_fn, emit,
                 turn_cap=TURN_CAP, transcript_dir="transcripts",
                 run_id=None):
        self.case_text = case_text
        self.cards = {c["seat"]: c for c in cards}
        self.juror_fn = juror_fn
        self.foreman_fn = foreman_fn
        self._emit = emit
        self.turn_cap = turn_cap
        self.transcript_dir = Path(transcript_dir)
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.transcript = []          # spoken record: {seat, name, speech}
        self.events = []              # every emitted event, in order
        self.leans = {}               # seat -> {lean, confidence}  PRIVATE
        self.last_tally = None
        self.turn = 0
        self.spoke_since_vote = True  # True so the forced opening vote passes
        self.verdict = None
        self._rr_next = 1             # round-robin fallback pointer

    def emit(self, event):
        self.events.append(event)
        self._emit(event)

    # --- juror speaks ----------------------------------------------------
    def _call_on(self, seat):
        card = self.cards[seat]
        self.emit({"type": "speaker", "seat": seat})
        try:
            reply = self.juror_fn(card, self.case_text, self.transcript,
                                  "speak")
        except Exception:
            self.emit({"type": "speech", "seat": seat, "name": card["name"],
                       "speech": f"(Juror #{seat} passes.)"})
            return
        self.transcript.append({"seat": seat, "name": card["name"],
                                "speech": reply["speech"]})
        self.leans[seat] = {"lean": reply.get("lean", "undecided"),
                            "confidence": reply.get("confidence", 0)}
        self.spoke_since_vote = True
        self.emit({"type": "speech", "seat": seat, "name": card["name"],
                   "speech": reply["speech"]})

    # --- public ballot ---------------------------------------------------
    def _call_vote(self):
        self.emit({"type": "vote_called"})
        seats = sorted(self.cards)

        def one_vote(seat):
            try:
                r = self.juror_fn(self.cards[seat], self.case_text,
                                  self.transcript, "vote")
                v = r.get("vote")
                return v if v in VOTE_VALUES else "undecided"
            except Exception:
                return "undecided"

        with ThreadPoolExecutor(max_workers=12) as pool:
            votes = dict(zip(seats, pool.map(one_vote, seats)))
        counts = tally(votes)
        self.last_tally = counts
        self.spoke_since_vote = False
        self.emit({"type": "vote_result", "votes": votes, "tally": counts})
        result = unanimous(counts)
        if result:
            self._declare(result, "unanimous vote")

    # --- end of deliberation ----------------------------------------------
    def _declare(self, verdict, reason):
        self.verdict = verdict
        self.emit({"type": "verdict", "verdict": verdict, "reason": reason})

    def _write_transcript(self):
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_dir / f"{self.run_id}.json"
        path.write_text(json.dumps(self.events, indent=2))
        return path
```

Also append the export to `tally.py` if `VOTE_VALUES` import fails — it was defined in Task 1 and is already importable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "feat: deliberation core - speeches, parallel votes, transcript"
```

---

### Task 7: Orchestrator loop — foreman handling, safeguards, terminal states

**Files:**
- Modify: `orchestrator.py` (add `run()`, `_foreman_turn()`, `_round_robin_seat()`)
- Modify: `tests/test_orchestrator.py` (append)

**Interfaces:**
- Consumes: everything from Task 6.
- Produces: `Deliberation.run() -> str` (final verdict). Consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:
```python
def split_juror(guilty_seats):
    """Jurors in guilty_seats vote guilty, the rest not_guilty."""
    def fn(card, case_text, transcript, mode):
        if mode == "speak":
            return {"speech": f"J{card['seat']} speaks.",
                    "lean": "undecided", "confidence": 0.5}
        v = "guilty" if card["seat"] in guilty_seats else "not_guilty"
        return {"vote": v}
    return fn


def test_run_unanimous_on_opening_vote(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(vote="not_guilty"), scripted_foreman([]),
                   emit, tmp_path)
    assert d.run() == "not_guilty"
    types = [e["type"] for e in events]
    assert types == ["case", "roster", "vote_called", "vote_result",
                     "verdict"]
    assert (tmp_path / "test.json").exists()


def test_run_foreman_drives_discussion_then_declares_hung(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 8},
        {"action": "call_on", "target": 3},
        {"action": "declare", "verdict": "hung", "reason": "deadlock"},
    ])
    d = make_delib(split_juror({1, 2, 3, 4, 5, 6}), foreman, emit, tmp_path)
    assert d.run() == "hung"
    speeches = [e for e in events if e["type"] == "speech"]
    assert [s["seat"] for s in speeches] == [8, 3]
    assert events[-1] == {"type": "verdict", "verdict": "hung",
                          "reason": "deadlock"}


def test_run_turn_cap_forces_hung(tmp_path):
    events, emit = collect()
    def always_call_on(transcript, last_tally, turn, turn_cap):
        return {"action": "call_on", "target": 8}
    d = make_delib(split_juror({1}), always_call_on, emit, tmp_path, cap=5)
    assert d.run() == "hung"
    assert events[-1]["reason"] == "turn cap reached"
    speeches = [e for e in events if e["type"] == "speech"]
    assert len(speeches) == 5


def test_no_back_to_back_votes(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_vote"},                  # right after opening vote
        {"action": "declare", "verdict": "hung", "reason": "x"},
    ])
    d = make_delib(split_juror({1, 2, 3}), foreman, emit, tmp_path)
    d.run()
    types = [e["type"] for e in events]
    # only ONE vote_called (the forced opener); second call_vote was
    # converted to a round-robin call_on
    assert types.count("vote_called") == 1
    assert "speech" in types


def test_premature_declare_rejected(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "declare", "verdict": "guilty", "reason": "im tired"},
        {"action": "declare", "verdict": "hung", "reason": "ok fine"},
    ])
    d = make_delib(split_juror({1, 2, 3}), foreman, emit, tmp_path)
    assert d.run() == "hung"       # guilty declare bounced (tally not 12-0)
    speeches = [e for e in events if e["type"] == "speech"]
    assert len(speeches) == 1      # bounce became a round-robin call_on


def test_malformed_foreman_falls_back_to_round_robin(tmp_path):
    events, emit = collect()
    calls = {"n": 0}
    def flaky_foreman(transcript, last_tally, turn, turn_cap):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("malformed")
        return {"action": "declare", "verdict": "hung", "reason": "x"}
    d = make_delib(split_juror({1, 2}), flaky_foreman, emit, tmp_path)
    assert d.run() == "hung"
    speeches = [e for e in events if e["type"] == "speech"]
    assert [s["seat"] for s in speeches] == [1]   # round-robin starts at 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: new tests FAIL with `AttributeError: 'Deliberation' object has no attribute 'run'`

- [ ] **Step 3: Write the implementation**

Append to the `Deliberation` class in `orchestrator.py` (after `__init__`, before `_call_on`):
```python
    # --- main loop ---------------------------------------------------------
    def run(self):
        """Run the deliberation to a verdict. Returns the verdict string."""
        self.emit({"type": "case", "text": self.case_text})
        self.emit({"type": "roster", "jurors": [
            {"seat": s, "name": c["name"], "occupation": c["occupation"]}
            for s, c in sorted(self.cards.items())]})
        self._call_vote()                    # forced opening ballot
        while self.verdict is None:
            self.turn += 1
            if self.turn > self.turn_cap:
                self._declare("hung", "turn cap reached")
                break
            self._foreman_turn()
        self._write_transcript()
        return self.verdict

    def _foreman_turn(self):
        try:
            action = self.foreman_fn(self.transcript, self.last_tally,
                                     self.turn, self.turn_cap)
        except Exception:
            self._call_on(self._round_robin_seat())
            return
        kind = action.get("action")
        if kind == "call_on" and action.get("target") in self.cards:
            self._call_on(action["target"])
        elif kind == "call_vote":
            if self.spoke_since_vote:
                self._call_vote()
            else:                      # no back-to-back ballots
                self._call_on(self._round_robin_seat())
        elif (kind == "declare"
              and action.get("verdict") in ("guilty", "not_guilty", "hung")):
            counts = self.last_tally or {}
            if (action["verdict"] != "hung"
                    and counts.get(action["verdict"], 0) != 12):
                # premature declare without a unanimous ballot: rejected
                self._call_on(self._round_robin_seat())
            else:
                self._declare(action["verdict"], action.get("reason", ""))
        else:                          # unknown/invalid action
            self._call_on(self._round_robin_seat())

    def _round_robin_seat(self):
        seat = self._rr_next
        self._rr_next = seat % 12 + 1
        return seat
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: 12 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all passed (tally 5, agent 10, prompts 6, loader 4, orchestrator 12)

- [ ] **Step 6: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "feat: deliberation loop - foreman actions, safeguards, terminal states"
```

---

### Task 8: Live agent adapters + FastAPI SSE server

**Files:**
- Create: `live_agents.py`
- Create: `server.py`
- Create: `tests/test_live_agents.py`

**Interfaces:**
- Consumes: `agent.ask_json` (Task 3), `prompts.*` (Task 4), `loader.*` (Task 5), `Deliberation` (Tasks 6-7).
- Produces:
  - `live_juror_fn(card, case_text, transcript, mode) -> dict` and `live_foreman_fn(transcript, last_tally, turn, turn_cap) -> dict` — match the injected-callable signatures from Task 6.
  - `server.py` app: `POST /start`, `GET /events` (SSE, `data: <json>\n\n` frames), static `web/` at `/`.
- Known v1 limitation (accepted): one SSE queue → single viewer per run.

- [ ] **Step 1: Write the failing tests**

`tests/test_live_agents.py`:
```python
from unittest.mock import patch

import live_agents

CARD = {"id": "juror_08", "seat": 8, "name": "James Davis",
        "occupation": "architect", "temperament": "t", "biases": "b",
        "speech_style": "s"}


def test_live_juror_speak_wires_prompts_and_schema():
    with patch("live_agents.agent.ask_json",
               return_value={"speech": "x", "lean": "guilty",
                             "confidence": 0.9}) as ask:
        out = live_agents.live_juror_fn(CARD, "CASE", [], "speak")
    assert out["speech"] == "x"
    sys_prompt, user_prompt = ask.call_args[0][0], ask.call_args[0][1]
    assert "Juror #8" in sys_prompt
    assert "CASE" in user_prompt
    assert ask.call_args[0][2] == ["speech", "lean", "confidence"]


def test_live_juror_vote_requires_vote_key():
    with patch("live_agents.agent.ask_json",
               return_value={"vote": "not_guilty"}) as ask:
        out = live_agents.live_juror_fn(CARD, "CASE", [], "vote")
    assert out == {"vote": "not_guilty"}
    assert ask.call_args[0][2] == ["vote"]


def test_live_foreman_requires_action_key():
    with patch("live_agents.agent.ask_json",
               return_value={"action": "call_vote"}) as ask:
        out = live_agents.live_foreman_fn([], None, 3, 200)
    assert out == {"action": "call_vote"}
    assert ask.call_args[0][2] == ["action"]
    assert "Turn 3 of max 200" in ask.call_args[0][1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_live_agents.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'live_agents'`

- [ ] **Step 3: Write live_agents.py**

`live_agents.py`:
```python
"""Glue: injected-callable signatures -> real claude -p calls."""

import agent
import prompts


def live_juror_fn(card, case_text, transcript, mode):
    system = prompts.juror_system_prompt(card)
    if mode == "speak":
        return agent.ask_json(
            system, prompts.juror_speak_prompt(case_text, transcript),
            ["speech", "lean", "confidence"])
    return agent.ask_json(
        system, prompts.juror_vote_prompt(case_text, transcript), ["vote"])


def live_foreman_fn(transcript, last_tally, turn, turn_cap):
    return agent.ask_json(
        prompts.foreman_system_prompt(),
        prompts.foreman_prompt(transcript, last_tally, turn, turn_cap),
        ["action"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_live_agents.py -v`
Expected: 3 passed

- [ ] **Step 5: Write server.py**

`server.py`:
```python
"""FastAPI server: serves web/, streams orchestrator events over SSE.

Run:  uvicorn server:app --port 8012
v1 limitation: single event queue = one viewer per run.
"""

import asyncio
import json
import threading

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from live_agents import live_foreman_fn, live_juror_fn
from loader import load_cards, load_case
from orchestrator import Deliberation

app = FastAPI()
queue: asyncio.Queue = asyncio.Queue()
_running = threading.Event()


@app.post("/start")
async def start():
    if _running.is_set():
        return {"status": "already-running"}
    _running.set()
    loop = asyncio.get_running_loop()

    def emit(event):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    delib = Deliberation(load_case(), load_cards(),
                         live_juror_fn, live_foreman_fn, emit)

    def work():
        try:
            delib.run()
        except Exception as exc:       # surface, never swallow
            emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            _running.clear()

    threading.Thread(target=work, daemon=True).start()
    return {"status": "started"}


@app.get("/events")
async def events():
    async def stream():
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")


# mounted last so /start and /events win routing
app.mount("/", StaticFiles(directory="web", html=True), name="web")
```

- [ ] **Step 6: Smoke the server imports and routes**

`web/` doesn't exist yet — create a placeholder so the mount doesn't error:

```bash
mkdir -p web && echo '<h1>room coming in task 9</h1>' > web/room.html
python -c "
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)
r = c.get('/room.html')
assert r.status_code == 200, r.status_code
print('static mount ok')
"
```
Expected: `static mount ok` (don't POST /start here — it would fire 12 real claude calls).

- [ ] **Step 7: Commit**

```bash
git add live_agents.py server.py tests/test_live_agents.py web/room.html
git commit -m "feat: live claude -p adapters + FastAPI SSE server"
```

---

### Task 9: UI state reducer (room_state.js) + node smoke test

**Files:**
- Create: `web/room_state.js`
- Create: `tests/test_room_state.mjs`

**Interfaces:**
- Produces (ES module):
  - `initialState() -> state object` with keys `jurors, activeSeat, dialogue, votes, tally, voting, verdict, error`
  - `applyEvent(state, event) -> new state` — pure, throws on unknown `event.type`
- Consumed by Task 10 (`room.js` imports it). This split exists so the VN's event logic is testable in node without a DOM.

- [ ] **Step 1: Write the failing test**

`tests/test_room_state.mjs`:
```javascript
import test from "node:test";
import assert from "node:assert";
import { initialState, applyEvent } from "../web/room_state.js";

const CANNED = [
  { type: "case", text: "the case" },
  { type: "roster", jurors: [
    { seat: 1, name: "Walt", occupation: "coach" },
    { seat: 8, name: "Davis", occupation: "architect" },
  ]},
  { type: "vote_called" },
  { type: "vote_result",
    votes: { 1: "guilty", 8: "not_guilty" },
    tally: { guilty: 1, not_guilty: 1, undecided: 0 } },
  { type: "speaker", seat: 8 },
  { type: "speech", seat: 8, name: "Davis", speech: "Let's talk." },
  { type: "verdict", verdict: "hung", reason: "deadlock" },
];

test("replaying a canned transcript handles every event type", () => {
  let s = initialState();
  for (const ev of CANNED) s = applyEvent(s, ev);
  assert.equal(s.jurors.length, 2);
  assert.equal(s.activeSeat, 8);
  assert.equal(s.dialogue.speech, "Let's talk.");
  assert.equal(s.votes[1], "guilty");
  assert.equal(s.tally.guilty, 1);
  assert.equal(s.verdict.verdict, "hung");
});

test("vote_called sets voting, vote_result clears it", () => {
  let s = applyEvent(initialState(), { type: "vote_called" });
  assert.equal(s.voting, true);
  s = applyEvent(s, { type: "vote_result", votes: {}, 
                      tally: { guilty: 0, not_guilty: 0, undecided: 0 } });
  assert.equal(s.voting, false);
});

test("unknown event type throws", () => {
  assert.throws(() => applyEvent(initialState(), { type: "nonsense" }));
});

test("applyEvent does not mutate the input state", () => {
  const s0 = initialState();
  applyEvent(s0, { type: "speaker", seat: 3 });
  assert.equal(s0.activeSeat, null);
});

test("error event stored", () => {
  const s = applyEvent(initialState(), { type: "error", message: "boom" });
  assert.equal(s.error, "boom");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/test_room_state.mjs`
Expected: FAIL — cannot find module `web/room_state.js`

- [ ] **Step 3: Write the implementation**

`web/room_state.js`:
```javascript
// Pure event reducer for the visual novel. No DOM. Imported by room.js
// (browser) and by the node smoke test.

export function initialState() {
  return {
    jurors: [],        // [{seat, name, occupation}]
    activeSeat: null,  // seat currently lit
    dialogue: null,    // {seat, name, speech} last spoken
    votes: {},         // seat -> "guilty"|"not_guilty"|"undecided"
    tally: null,       // {guilty, not_guilty, undecided}
    voting: false,     // between vote_called and vote_result
    verdict: null,     // {verdict, reason}
    error: null,
  };
}

export function applyEvent(state, ev) {
  const s = { ...state };
  switch (ev.type) {
    case "case":
      break;                              // shown once at start; no state
    case "roster":
      s.jurors = ev.jurors;
      break;
    case "speaker":
      s.activeSeat = ev.seat;
      break;
    case "speech":
      s.dialogue = { seat: ev.seat, name: ev.name, speech: ev.speech };
      break;
    case "vote_called":
      s.voting = true;
      break;
    case "vote_result":
      s.votes = ev.votes;
      s.tally = ev.tally;
      s.voting = false;
      break;
    case "verdict":
      s.verdict = { verdict: ev.verdict, reason: ev.reason };
      break;
    case "error":
      s.error = ev.message;
      break;
    default:
      throw new Error("unknown event type: " + ev.type);
  }
  return s;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/test_room_state.mjs`
Expected: 5 pass, 0 fail

- [ ] **Step 5: Commit**

```bash
git add web/room_state.js tests/test_room_state.mjs
git commit -m "feat: pure VN event reducer with node smoke test"
```

---

### Task 10: Visual-novel page (room.html / room.css / room.js)

**Files:**
- Modify: `web/room.html` (replace the Task 8 placeholder)
- Create: `web/room.css`
- Create: `web/room.js`

**Interfaces:**
- Consumes: `initialState`, `applyEvent` from `web/room_state.js`; SSE from `GET /events`; `POST /start`.
- Produces: the complete v1 UI. Replay = "Load transcript" file input feeding the same renderer.

- [ ] **Step 1: Write room.html**

`web/room.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Twelve Angry Agents</title>
<link rel="stylesheet" href="room.css">
</head>
<body>
<div id="stage">
  <div id="tally">GUILTY 0 — NOT GUILTY 0</div>
  <div id="seats"></div>
  <div id="dialogue">
    <div id="dialogue-name"></div>
    <div id="dialogue-text">Press Start to begin deliberation…</div>
  </div>
  <div id="controls">
    <button id="start">Start</button>
    <label>Speed
      <input id="speed" type="range" min="5" max="80" value="25">
    </label>
    <label id="replay-label">Replay
      <input id="replay" type="file" accept=".json">
    </label>
  </div>
  <div id="verdict">
    <div id="verdict-text"></div>
    <div id="verdict-reason"></div>
  </div>
</div>
<script type="module" src="room.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write room.css**

`web/room.css`:
```css
/* Hot, cramped jury room — single screen, no scrolling. */
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; overflow: hidden; font-family: Georgia, serif; }

#stage {
  height: 100%;
  display: flex; flex-direction: column;
  background: linear-gradient(180deg, #6b5537 0%, #4a3a26 55%, #2e2418 100%);
  color: #f3ead8;
}

#tally {
  text-align: center; padding: 10px;
  font-size: 1.4rem; letter-spacing: 2px;
  background: rgba(0,0,0,.35);
}
#tally.bump { animation: bump .4s ease; }
@keyframes bump { 50% { transform: scale(1.15); } }

#seats {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px; padding: 20px 30px;
  align-content: center;
}
.seat {
  text-align: center;
  opacity: .45; filter: grayscale(.4);
  transition: opacity .3s, transform .3s;
  border-radius: 8px; padding: 8px;
  border-bottom: 6px solid #777;      /* vote color bar */
}
.seat.active {
  opacity: 1; filter: none; transform: scale(1.12);
  background: rgba(255, 240, 200, .12);
}
.seat[data-vote="guilty"]     { border-bottom-color: #c0392b; }
.seat[data-vote="not_guilty"] { border-bottom-color: #27ae60; }
.seat[data-vote="undecided"],
.seat[data-vote="unknown"]    { border-bottom-color: #7f8c8d; }

.avatar {
  width: 64px; height: 64px; margin: 0 auto 6px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 1.6rem; font-weight: bold; color: #fff;
}
/* distinct placeholder color per seat */
.seat:nth-child(1)  .avatar { background: #8e44ad; }
.seat:nth-child(2)  .avatar { background: #2980b9; }
.seat:nth-child(3)  .avatar { background: #c0392b; }
.seat:nth-child(4)  .avatar { background: #16a085; }
.seat:nth-child(5)  .avatar { background: #d35400; }
.seat:nth-child(6)  .avatar { background: #7f8c8d; }
.seat:nth-child(7)  .avatar { background: #f39c12; }
.seat:nth-child(8)  .avatar { background: #2c3e50; }
.seat:nth-child(9)  .avatar { background: #27ae60; }
.seat:nth-child(10) .avatar { background: #a04000; }
.seat:nth-child(11) .avatar { background: #1f618d; }
.seat:nth-child(12) .avatar { background: #884ea0; }

.plate { font-size: .75rem; line-height: 1.25; }

#dialogue {
  min-height: 120px; margin: 0 30px 8px;
  background: rgba(10, 8, 5, .82);
  border: 2px solid #b89b6a; border-radius: 10px;
  padding: 14px 18px;
  cursor: pointer;
}
#dialogue-name { color: #e8c87a; font-weight: bold; margin-bottom: 6px; }
#dialogue-text { font-size: 1.05rem; line-height: 1.45; }

#controls {
  display: flex; gap: 18px; align-items: center;
  padding: 8px 30px 14px;
  font-family: sans-serif; font-size: .85rem;
}
#controls button {
  padding: 6px 22px; font-size: 1rem; cursor: pointer;
  background: #b89b6a; border: none; border-radius: 6px;
}

#verdict {
  position: fixed; inset: 0;
  display: none;
  align-items: center; justify-content: center; flex-direction: column;
  background: rgba(0, 0, 0, .88);
  text-align: center;
}
#verdict.show { display: flex; }
#verdict-text { font-size: 4rem; letter-spacing: 6px; color: #e8c87a; }
#verdict-reason { margin-top: 16px; font-size: 1.1rem; max-width: 40em; }
```

- [ ] **Step 3: Write room.js**

`web/room.js`:
```javascript
// Pure renderer of the event stream. All simulation state lives server-side;
// this file only queues events, reduces them via room_state.js, and paints.

import { initialState, applyEvent } from "./room_state.js";

let state = initialState();
const pending = [];       // events not yet shown
let busy = false;         // typewriter/pause in progress
let typeTimer = null;
let pauseTimer = null;
let pendingFullText = ""; // for click-to-skip
const AUTO_ADVANCE_MS = 1800;

const el = (id) => document.getElementById(id);

function buildSeats() {
  const box = el("seats");
  box.innerHTML = "";
  for (const j of state.jurors) {
    const d = document.createElement("div");
    d.className = "seat";
    d.id = "seat-" + j.seat;
    d.dataset.vote = "unknown";
    d.innerHTML =
      `<div class="avatar">${j.seat}</div>` +
      `<div class="plate">Juror #${j.seat}<br>${j.occupation}</div>`;
    box.appendChild(d);
  }
}

function render() {
  if (state.jurors.length && !el("seat-" + state.jurors[0].seat)) buildSeats();
  for (const j of state.jurors) {
    const d = el("seat-" + j.seat);
    d.classList.toggle("active", state.activeSeat === j.seat);
    d.dataset.vote = state.votes[j.seat] || "unknown";
  }
  if (state.tally) {
    const t = el("tally");
    t.textContent =
      `GUILTY ${state.tally.guilty} — NOT GUILTY ${state.tally.not_guilty}`;
    t.classList.remove("bump");
    void t.offsetWidth;               // restart animation
    t.classList.add("bump");
  }
  if (state.verdict) {
    el("verdict-text").textContent = {
      guilty: "CONVICTED",
      not_guilty: "ACQUITTED",
      hung: "HUNG JURY",
    }[state.verdict.verdict];
    el("verdict-reason").textContent = state.verdict.reason || "";
    el("verdict").classList.add("show");
  }
  if (state.error) {
    el("dialogue-name").textContent = "ERROR";
    el("dialogue-text").textContent = state.error;
  }
}

function typewriter(text, done) {
  const box = el("dialogue-text");
  box.textContent = "";
  pendingFullText = text;
  let i = 0;
  typeTimer = setInterval(() => {
    box.textContent = text.slice(0, ++i);
    if (i >= text.length) {
      clearInterval(typeTimer);
      typeTimer = null;
      done();
    }
  }, Number(el("speed").value));
}

function showNext() {
  if (busy || pending.length === 0) return;
  const ev = pending.shift();
  state = applyEvent(state, ev);
  render();
  if (ev.type === "speech") {
    busy = true;
    el("dialogue-name").textContent = `Juror #${ev.seat} — ${ev.name}`;
    typewriter(ev.speech, () => {
      pauseTimer = setTimeout(() => {
        pauseTimer = null;
        busy = false;
        showNext();
      }, AUTO_ADVANCE_MS);
    });
  } else if (ev.type === "vote_called") {
    el("dialogue-name").textContent = "FOREMAN";
    el("dialogue-text").textContent = "Alright — let's take a vote.";
    busy = true;
    pauseTimer = setTimeout(() => {
      pauseTimer = null;
      busy = false;
      showNext();
    }, AUTO_ADVANCE_MS / 2);
  } else {
    showNext();
  }
}

// Click: skip typewriter to full text, or skip the auto-advance pause.
el("dialogue").addEventListener("click", () => {
  if (typeTimer) {
    clearInterval(typeTimer);
    typeTimer = null;
    el("dialogue-text").textContent = pendingFullText;
    pauseTimer = setTimeout(() => {
      pauseTimer = null;
      busy = false;
      showNext();
    }, AUTO_ADVANCE_MS);
  } else if (pauseTimer) {
    clearTimeout(pauseTimer);
    pauseTimer = null;
    busy = false;
    showNext();
  }
});

function enqueue(ev) {
  pending.push(ev);
  showNext();
}

el("start").addEventListener("click", async () => {
  el("start").disabled = true;
  const source = new EventSource("/events");
  source.onmessage = (msg) => enqueue(JSON.parse(msg.data));
  await fetch("/start", { method: "POST" });
});

// Replay: feed a saved transcript through the identical pipeline.
el("replay").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const events = JSON.parse(await file.text());
  state = initialState();
  for (const ev of events) enqueue(ev);
});
```

- [ ] **Step 4: Reducer regression check**

Run: `node --test tests/test_room_state.mjs`
Expected: 5 pass (room.js must not have required reducer changes)

- [ ] **Step 5: Manual replay smoke test**

Create a canned transcript and eyeball the page:

```bash
cat > /home/yoshkoz/tmp/canned-run.json <<'EOF'
[
  {"type": "case", "text": "test case"},
  {"type": "roster", "jurors": [
    {"seat": 1, "name": "Walt Novak", "occupation": "coach"},
    {"seat": 2, "name": "Arthur Bell", "occupation": "bank teller"},
    {"seat": 3, "name": "Frank Della Rocca", "occupation": "messenger-service owner"},
    {"seat": 4, "name": "Charles Whitmore", "occupation": "stockbroker"},
    {"seat": 5, "name": "Sal Mercado", "occupation": "orderly"},
    {"seat": 6, "name": "Stan Kowalski", "occupation": "house painter"},
    {"seat": 7, "name": "Eddie Marchetti", "occupation": "salesman"},
    {"seat": 8, "name": "James Davis", "occupation": "architect"},
    {"seat": 9, "name": "Elias McCardle", "occupation": "retired"},
    {"seat": 10, "name": "Vic Harmon", "occupation": "garage owner"},
    {"seat": 11, "name": "Milos Novotny", "occupation": "watchmaker"},
    {"seat": 12, "name": "Brad Sherwood", "occupation": "ad executive"}
  ]},
  {"type": "vote_called"},
  {"type": "vote_result",
   "votes": {"1":"guilty","2":"guilty","3":"guilty","4":"guilty","5":"guilty",
             "6":"guilty","7":"guilty","8":"not_guilty","9":"guilty",
             "10":"guilty","11":"guilty","12":"guilty"},
   "tally": {"guilty": 11, "not_guilty": 1, "undecided": 0}},
  {"type": "speaker", "seat": 8},
  {"type": "speech", "seat": 8, "name": "James Davis",
   "speech": "I just think we owe him a few words before we send him off."},
  {"type": "speaker", "seat": 3},
  {"type": "speech", "seat": 3, "name": "Frank Della Rocca",
   "speech": "Words? The kid's guilty as sin and you know it."},
  {"type": "verdict", "verdict": "hung", "reason": "canned test transcript"}
]
EOF
uvicorn server:app --port 8012
```

Open `http://localhost:8012/room.html`, click **Replay**, pick
`~/tmp/canned-run.json`. Verify: 12 seats render; opening vote colors 11 red /
1 green; tally banner reads `GUILTY 11 — NOT GUILTY 1` and bumps; seat 8 lights
up; typewriter text plays; click skips it; hung-jury overlay appears. Stop
uvicorn.

- [ ] **Step 6: Commit**

```bash
git add web/room.html web/room.css web/room.js
git commit -m "feat: visual-novel room - stage, typewriter dialogue, vote board, verdict card"
```

---

### Task 11: Live end-to-end run (manual, real claude -p)

**Files:**
- No new files; produces `transcripts/<timestamp>.json`.

**Interfaces:**
- Consumes: the entire system.

- [ ] **Step 1: Full test suite green**

Run: `python -m pytest tests/ -v && node --test tests/test_room_state.mjs`
Expected: everything passes.

- [ ] **Step 2: One real deliberation**

```bash
uvicorn server:app --port 8012
```

Open `http://localhost:8012/room.html`, click **Start**. This fires real
`claude -p` subprocesses (12 parallel per vote, 2 serial per discussion turn) —
a run takes minutes. Watch for:
- Opening vote arrives and colors the board (split is emergent — any split is valid).
- Discussion turns play as VN dialogue; speaker highlighting follows the foreman's picks.
- Votes recur; tally animates; verdict overlay ends the run.
- No `error` event.

- [ ] **Step 3: Verify the transcript**

```bash
ls transcripts/
python -c "
import json, glob
path = sorted(glob.glob('transcripts/*.json'))[-1]
events = json.load(open(path))
types = [e['type'] for e in events]
assert types[0] == 'case' and types[1] == 'roster'
assert types[-1] == 'verdict'
assert 'lean' not in json.dumps(events)
print(path, len(events), 'events, verdict:', events[-1]['verdict'])
"
```
Expected: prints the path, event count, and verdict; no assertion errors.

- [ ] **Step 4: Replay the real run**

In the browser (fresh page load), use **Replay** with the file from
`transcripts/` — confirm the identical renderer plays the real run back.

- [ ] **Step 5: Fix anything that surfaced, then commit**

If real `claude -p` output exposed parsing/prompt issues, fix with the same
TDD cycle (failing test → fix → green) before committing.

```bash
git add -A
git commit -m "chore: live end-to-end run verified, transcript replayable"
```
