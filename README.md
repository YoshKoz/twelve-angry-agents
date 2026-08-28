# Twelve Angry Agents

Twelve Angry Men, re-enacted by AI agents. Twelve juror agents, a foreman, a
court officer and a judge work through the evidence one exhibit at a time and
deliberate to a verdict, live in the browser — and the app shows you where the
room reasoned its way to something the film didn't.

## What this is

Every decision in the room belongs to an agent. Each juror is a persona —
occupation, temperament, biases, speech style — and it does not merely answer
when spoken to: it can demand a ballot, send out for an exhibit, propose that
the room physically test a claim, put a direct question to another juror, or
announce that it is changing its vote. The foreman decides who speaks, when
the room votes and whether by show of hands or secret written ballot, rules on
every request from the floor, and decides when an exhibit is finished. The
court officer answers from the case file and refuses to invent a fact the
record does not carry. The judge takes the verdict or sends the jury back.

The room works a **docket**: the switchblade, the old man downstairs, the
elevated train, the woman across the street, the alibi, the angle of the
wound, the defendant's record. Each one opens with all twelve jurors reading
it *independently and concurrently*, before a word is spoken — nobody is
watching, nobody has been argued at. That private read is the honest picture
of where the room stands, and it is what the position board shows you.

Then you compare it to the film. In the film, one juror notices the marks on
the eyewitness's nose after ninety minutes. Given the same record, all twelve
agents catch it in thirty seconds. That divergence is the app.

What is *not* in the code is the point. An earlier version enforced the drama
in `orchestrator.py` — a forced opening straw poll, a rotation that overrode
the foreman's choice of speaker, a rule that took a vote every fifteen turns,
an `if` that rejected a premature verdict. All of it is gone. The state
machine owns bookkeeping, error handling, the operator's stop, and two runaway
guards; nothing else. Agents are injected callables, so the whole thing is
still unit-tested against deterministic fakes with no LLM in the loop.

## Features

- 12 juror agents with distinct cards (`data/jurors/*.json`) that act on the
  room rather than only speak
- An evidence docket per case; every exhibit gets twelve independent reads
  before it gets argued
- A per-juror position board: exhibit × juror, so a single agent breaking from
  the room is visible at a glance, next to what the film concluded
- A foreman who genuinely runs the room, and a judge who refuses an improper
  verdict and sends the jury back
- Show-of-hands and secret written ballots, the latter hiding who voted what
  and allowing a deliberate abstention
- Jurors changing their vote on the floor, without anyone's permission
- Full transcript + event log per run in `transcripts/<run_id>.json`, replayable
- Optional per-juror ElevenLabs TTS narration
- Two included cases (`data/cases/`)

## Architecture

```
orchestrator.py   Deliberation state machine — no LLM calls, no policy
live_agents.py    adapts injected-callable signatures to real agent calls
agent.py          backend-dispatching LLM client (claude_cli | ollama)
prompts.py        all prompt text, no I/O — the whole action space
loader.py         juror cards, case narrative + exhibit docket
tally.py          pure vote counting
tts.py            ElevenLabs narration, per-seat voice map, cached
server.py         FastAPI — POST /start, GET /events (SSE), GET /film/<case>
web/room_state.js pure event reducer shared by the browser and node tests
web/room.js       DOM rendering on top of the reducer
```

Layering is strict: pure logic → state machine → I/O adapters → server. The
orchestrator never imports an LLM or HTTP module, which is what makes it
testable with fakes.

## Speed

A run is minutes, not hours, and that came from the prompts rather than the
model. A juror used to receive the entire case file and forty remarks of
transcript on every turn; it now gets a short brief, the one exhibit in front
of it, the room's earlier findings, and the last eight remarks. The full
record goes only to the court officer. On top of that, the twelve assessments
that open each exhibit run concurrently — a whole exhibit's worth of
independent opinion costs about one agent call of wall-clock (~30s), where
twelve sequential speaking turns cost twelve.

## Tech stack

Python (FastAPI, httpx, uvicorn) on the backend, vanilla JS/HTML/CSS on the
frontend, ElevenLabs for TTS, and either backend for inference:

- **`claude_cli` (default)** — every agent turn is a headless `claude -p`
  subprocess whose entire system prompt is the persona, run from a scratch
  directory with tools, MCP, hooks, output styles and any `CLAUDE.md` stripped
  out so nothing bleeds into a juror's voice. Needs a logged-in `claude` on
  PATH.
- **`ollama`** — a LAN [Ollama](https://ollama.com) instance, via
  `OLLAMA_HOST` / `OLLAMA_MODEL`.

## Running it

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # pick AGENT_BACKEND, models, optional ELEVENLABS_API_KEY

venv/bin/uvicorn server:app --port 8012
# open http://localhost:8012/room.html and click Begin deliberation
```

## Tests

```bash
venv/bin/python -m pytest             # backend suite
node --test tests/test_room_state.mjs # the JS reducer
```

No test spawns a real agent or opens a socket.

## Status

Single-viewer per run (the SSE fan-out uses one queue) — fine for a demo, not
built for concurrent audiences. Everything else described above is implemented
and covered by tests, not aspirational.
