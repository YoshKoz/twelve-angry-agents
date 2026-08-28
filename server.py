"""FastAPI server: serves web/, streams orchestrator events over SSE.

Run:  uvicorn server:app --port 8012
Events fan out to every /events subscriber; new subscribers first
receive the full history of the current run, so late joins and
reconnects see the whole deliberation.
"""

import asyncio
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

import tts
from live_agents import (live_assess_fn, live_bailiff_fn, live_foreman_fn,
                         live_judge_fn, live_juror_fn)
from loader import (DEFAULT_CASE, list_cases, load_cards, load_case,
                    load_film_findings)
from orchestrator import Deliberation

VOTE_CALL_LINE = "Alright — let's take a vote."
TRANSCRIPT_DIR = Path("transcripts")
_TRANSCRIPT_ID = re.compile(r"^[\w.-]+$")   # no path separators or traversal

logger = logging.getLogger(__name__)

_running = threading.Event()
_stop_requested = threading.Event()
_subscribers: set[asyncio.Queue] = set()
_history: list[dict] = []      # events of the current run, in order


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    def prewarm():
        # generate narration now so /start doesn't block on TTS
        try:
            for case_id in list_cases():
                tts.narrate(load_case(case_id)["narrative"])
            tts.narrate(VOTE_CALL_LINE)
            logger.info("narration pre-warmed")
        except Exception as exc:
            logger.warning("narration pre-warm failed: %s", exc)

    threading.Thread(target=prewarm, daemon=True).start()
    yield


app = FastAPI(lifespan=_lifespan)


def _attach_narration(event):
    """Announcer audio for the case reading, vote calls, and the court's
    own words — what the officer reads back and what the judge rules are
    spoken by the room, not by a juror."""
    try:
        if event["type"] == "case":
            event["audio"] = tts.narrate(event["text"])
        elif event["type"] == "vote_called":
            event["audio"] = tts.narrate(VOTE_CALL_LINE)
        elif event["type"] == "record":
            event["audio"] = tts.narrate(event["text"])
        elif event["type"] == "judge_ruling" and event.get("instruction"):
            event["audio"] = tts.narrate(event["instruction"])
    except Exception as exc:
        logger.warning("narration failed for %s: %s", event["type"], exc)
    return event


def _publish(event):
    """Run on the event loop: record and fan out to all subscribers."""
    _history.append(event)
    idx = len(_history) - 1
    for q in list(_subscribers):
        q.put_nowait((idx, event))


@app.get("/cases")
async def cases():
    return {"cases": list_cases(), "default": DEFAULT_CASE}


@app.get("/film/{case_id}")
async def film(case_id: str):
    """What the film concluded about each exhibit, so the UI can show where
    this jury of agents diverged. Served separately from the case on purpose:
    this text must never reach an agent's prompt."""
    if not _TRANSCRIPT_ID.match(case_id):
        raise HTTPException(status_code=400, detail="invalid case id")
    return {"findings": load_film_findings(case_id)}


@app.get("/status")
async def status():
    """Lets a freshly loaded page discover a deliberation already in
    progress (from an earlier click, another tab, or a stale reload) and
    rejoin it instead of hitting 'already-running' with no recourse."""
    return {"running": _running.is_set()}


@app.post("/start")
async def start(case_id: str | None = None):
    if _running.is_set():
        return {"status": "already-running"}
    # Everything that can fail has to fail BEFORE the running flag goes up.
    # Loading the cards outside this try left _running set on a bad card file
    # with no thread alive to clear it, and /stop cannot clear it either — the
    # server was wedged into "already-running" until it was restarted.
    try:
        case = load_case(case_id)
        cards = load_cards()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "message": str(exc)}
    _running.set()
    _stop_requested.clear()
    _history.clear()
    loop = asyncio.get_running_loop()

    def emit(event):
        logger.debug("emit: %s", event.get("type"))
        loop.call_soon_threadsafe(_publish, _attach_narration(event))

    delib = Deliberation(case, cards,
                         live_juror_fn, live_foreman_fn, emit,
                         should_stop=_stop_requested.is_set,
                         bailiff_fn=live_bailiff_fn,
                         judge_fn=live_judge_fn,
                         assess_fn=live_assess_fn)

    def work():
        try:
            delib.run()
            logger.info("deliberation finished: %s", delib.verdict)
        except Exception as exc:
            logger.exception("deliberation crashed")
            emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            _running.clear()

    threading.Thread(target=work, daemon=True).start()
    return {"status": "started"}


@app.post("/stop")
async def stop():
    if not _running.is_set():
        return {"status": "not-running"}
    _stop_requested.set()
    return {"status": "stopping"}


@app.get("/transcripts")
async def transcripts():
    files = sorted(TRANSCRIPT_DIR.glob("*.json"), reverse=True)
    return {"transcripts": [f.stem for f in files]}


@app.get("/transcripts/{transcript_id}")
async def transcript(transcript_id: str):
    if not _TRANSCRIPT_ID.match(transcript_id):
        raise HTTPException(status_code=400, detail="invalid transcript id")
    path = TRANSCRIPT_DIR / f"{transcript_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such transcript")
    return json.loads(path.read_text())


@app.get("/events")
async def events(request: Request):
    # on reconnect the browser sends Last-Event-ID: skip what it has seen
    try:
        seen = int(request.headers.get("last-event-id", -1))
    except ValueError:
        seen = -1
    q: asyncio.Queue = asyncio.Queue()
    for i in range(seen + 1, len(_history)):   # catch up on the current run
        q.put_nowait((i, _history[i]))
    _subscribers.add(q)

    async def stream():
        try:
            while True:
                idx, event = await q.get()
                yield f"id: {idx}\ndata: {json.dumps(event)}\n\n"
        finally:                   # client gone: stop receiving events
            _subscribers.discard(q)
    return StreamingResponse(stream(), media_type="text/event-stream")


# mounted last so /start and /events win routing
app.mount("/", StaticFiles(directory="web", html=True), name="web")
