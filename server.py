"""Web UI: uvicorn server:app --reload, then open http://localhost:8000"""
import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from factcheck.cli import SAMPLE_TEXT
from factcheck.articles import fetch_article
from factcheck.config import Config
from factcheck.orchestrator import Mission

app = FastAPI(title="Done Means Done: fact-check agent team")


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
