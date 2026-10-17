"""Web UI: uvicorn server:app --reload, then open http://localhost:8000"""
import asyncio
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from eval.fever_fast import Experiment
from factcheck.cli import SAMPLE_TEXT
from factcheck.articles import fetch_article
from factcheck.config import Config
from factcheck.orchestrator import Mission

app = FastAPI(title="Done Means Done: fact-check agent team")
RUNS = Path("runs")
experiment = Experiment()


class RunRequest(BaseModel):
    text: str
    goal: str = "Fact-check this text"


class ArticleRequest(BaseModel):
    url: str


@app.post("/api/article")
async def article(req: ArticleRequest):
    return await fetch_article(req.url)


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/api/config")
def config():
    cfg = Config()
    return {"mock": cfg.mock, "model_strong": cfg.model_strong, "model_fast": cfg.model_fast, "sample": SAMPLE_TEXT}


@app.get("/api/runs")
def list_runs():
    """Saved UI missions, newest first. Eval runs (no mission_start event) are left out."""
    out = []
    for p in sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        events = data.get("events", [])
        if not events or events[0].get("type") != "mission_start":
            continue
        s = data.get("summary", {})
        out.append({"id": p.stem, "t": events[0].get("t"), "claims": len(s.get("claims", [])),
                    "mock": s.get("mock"), "done": s.get("done")})
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if not re.fullmatch(r"[a-f0-9]+", run_id):
        raise HTTPException(400, "bad run id")
    p = RUNS / f"{run_id}.json"
    if not p.is_file():
        raise HTTPException(404, "run not found")
    return {"id": run_id, "events": json.loads(p.read_text()).get("events", [])}


@app.post("/api/run")
async def run(req: RunRequest):
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(ev):
        await queue.put(ev)

    async def worker():
        try:
            await Mission(Config(), emit).run(req.text, req.goal)
        except Exception as e:  # surface failures in the UI instead of a dead stream
            await queue.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(worker())

    async def stream():
        while True:
            ev = await queue.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev)}\n\n"
        await task

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/improvement")
def improvement_page():
    return FileResponse("static/improvement.html")


@app.get("/api/improvement")
def improvement_status():
    return experiment.snapshot()


@app.post("/api/improvement/start")
async def improvement_start():
    try:
        experiment.start()
    except ValueError as exc:
        raise HTTPException(409 if experiment.task and not experiment.task.done() else 400, str(exc)) from None
    return experiment.snapshot()


@app.post("/api/improvement/stop")
async def improvement_stop():
    await experiment.stop()
    return experiment.snapshot()
