# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Twelve Angry Men re-enacted by AI agents: 12 juror agents, a foreman, a court
officer and a judge work an evidence docket one exhibit at a time and
deliberate to a verdict, rendered live in a web UI. The point of the app is to
see where a room of agents reasons its way to something different from the
film.

## Commands

```bash
venv/bin/python -m pytest                  # backend suite
venv/bin/python -m pytest tests/test_orchestrator.py::test_name -v
node --test tests/test_room_state.mjs      # the pure JS reducer

venv/bin/uvicorn server:app --port 8012    # then open /room.html
```

Live runs need `AGENT_BACKEND=claude_cli` (default; a logged-in `claude` on
PATH) or `AGENT_BACKEND=ollama`. `ELEVENLABS_API_KEY` enables TTS.

## Two core design rules

**1. No decision that a person in the room would make lives in the code.** The
orchestrator sequences agent calls and keeps books; it does not decide who
speaks, when or how to vote, whether a request is granted, when an exhibit is
finished, or whether a verdict stands. If you are adding an `if` that
overrides an agent, you are re-adding what this rework removed. What
legitimately stays in code: bookkeeping, error handling, the operator's stop,
and runaway guards (`TURN_CAP`, `EXHIBIT_TURN_CAP`).

Deliberately absent, do not reintroduce: a forced opening straw poll, a
speaker-rotation cooldown, a no-back-to-back-ballots block, a forced re-vote
every N turns, and a code-level rejection of a non-unanimous `declare` (the
judge agent rules on that).

**2. The room is only as fast as its prompts.** A juror gets the case brief,
the one exhibit in front of it, the room's earlier findings, and the last 8
remarks — not the whole case file and forty remarks. The full narrative goes
only to the court officer, which is the one agent that answers from the
record. Anything that grows a prompt unboundedly is a bug.

Agents are **injected callables**, so tests drive `Deliberation` with
deterministic fakes.

## The docket

A case is `data/cases/<id>.md` (the narrative record) plus
`data/cases/<id>.exhibits.json` (the docket). Each exhibit has `id`, `name`,
`prosecution_claim`, `record`, and an optional `film_finding`.

`film_finding` is what the film concluded. `load_case()` **strips it**;
`load_film_findings()` returns it separately for the UI's comparison column.
It must never reach a prompt — that would hand the agents the answer and break
`HONESTY_RULE`. Keep those two paths separate.

Each exhibit runs: `_open_exhibit` (all 12 jurors assess it **concurrently**,
before anyone speaks — this is both the honest picture of the room and the
main reason a run is minutes rather than hours) → discussion turns the foreman
directs → `close_exhibit` with a one-line finding → next exhibit →
`docket_closed`.

## Architecture

1. `orchestrator.py` — `Deliberation`. Owns all state (transcript, leans,
   tally, positions per exhibit, findings, open requests, speech counts),
   emits typed events: `case`, `roster`, `docket`, `exhibit`, `assessed`,
   `positions`, `exhibit_closed`, `docket_closed`, `speaker`, `speech`,
   `request`, `ruling`, `record`, `vote_called`, `voter_done`, `vote_result`,
   `vote_change`, `verdict_announced`, `judge_ruling`, `prompt`, `reasoning`,
   `verdict`, `error`. Raises `Stopped` from `_check_stop()`, called before
   and after every agent call so the operator's Stop lands within one call
   rather than one turn.
2. `live_agents.py` — adapts injected signatures to real agent calls:
   - `assess_fn(card, case, exhibit, findings)` → `{position, reasoning, confidence}`
   - `juror_fn(card, case, transcript, mode, last_tally, floor_note, vote_method, exhibit, findings)`
   - `foreman_fn(case, transcript, last_tally, turn, turn_cap, pending, speech_counts, judge_note, exhibit, findings, exhibit_turns, remaining)`
   - `bailiff_fn(kind, request, seat, case, transcript)` — `"evidence"` → `{granted, record}`, `"experiment"` → `{possible, result}`
   - `judge_fn(verdict, reason, last_tally, turn, transcript)` → `{accept, instruction}`
3. `agent.py` — backend dispatch on `AGENT_BACKEND`. `claude_cli` shells out
   to `claude -p` with the persona as the full `--system-prompt` and every
   source of ambient context stripped (`--allowed-tools ""`, empty MCP config,
   default output style, hooks off, cwd = a scratch dir outside the home
   tree). `model_for(role)` routes foreman/judge/bailiff to
   `CLAUDE_MODEL_FOREMAN`, everyone else to `CLAUDE_MODEL`.
4. `server.py` — FastAPI. `POST /start` runs the deliberation in a background
   thread and bridges its synchronous `emit` onto an asyncio queue;
   `GET /events` streams SSE; `GET /film/{case_id}` serves the film's findings
   for the comparison column. Static `web/` mounted last so API routes win.
5. `web/room_state.js` — pure event reducer (no DOM, no fetch), shared by the
   browser and the node test. DOM work belongs in `room.js`.

## The action space

Juror `action` on a speaking turn: `none`, `demand_vote` (`method`),
`request_evidence` (`item`), `propose_experiment` (`description`),
`change_vote` (`vote`), `challenge` (`target`). Everything except
`change_vote` becomes a pending request the foreman must rule on — one open
request per juror. `change_vote` applies immediately and re-tallies; nobody
grants it.

Foreman action: `call_on` (`target`), `close_exhibit` (`finding`, only while
an exhibit is open), `call_vote` (`method`, `binding`), `rule_on_request`
(`seat`, `grant`, `reason`), `declare` (`verdict`, `reason`). `declare` goes
to the judge, who may reject it — deliberation continues with the judge's
instruction in the foreman's next prompt.

A secret ballot hides who voted what (`vote_result.votes` is empty by design)
and is the only ballot on which `abstain` is honored — a show of hands
downgrades it to `undecided`.

## Conventions

- Layering is strict: pure logic (`tally`, `prompts`, `room_state.js`) ←
  state machine (`orchestrator`) ← I/O adapters (`agent`, `tts`,
  `live_agents`) ← server. Don't import LLM/HTTP modules into the orchestrator
  or pure modules.
- New event types must be handled in `web/room_state.js` (its reducer throws
  on unknown types) and covered in `tests/test_room_state.mjs`.
- The foreman is told the roster from `speech_counts`, pre-seeded with every
  seat at zero. Never hardcode seats 1–12 into a prompt.
- Agent failures degrade gracefully, never crash the deliberation — including
  an agent that returns the *wrong shape* rather than raising.
- Tests must never spawn a real `claude` process or open a socket;
  monkeypatch `subprocess.run` and pin `agent.BACKEND` explicitly.
- Prompt wording is load-bearing and has been tuned against observed failure
  modes; the comments in `prompts.py` record what each guard-rail is for
  (e.g. why the reasonable-doubt standard is stated once, and why the
  assessment prompt tells a juror not to perform scrutiny it wouldn't). Don't
  "clean those up" without re-running a live room.
