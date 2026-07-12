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
