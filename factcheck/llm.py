"""Crusoe Managed Inference client (OpenAI-compatible) with backoff, JSON parsing and a mock."""
import asyncio
import json
import random
import re
import time
from collections import deque

from .config import Config
from . import mock

# Request timestamps per model, shared by every LLM instance in the process: Crusoe's limit is per project and model.
_sent: dict[str, deque] = {}


async def _pace(model: str, rpm: int):
    """Wait until sending one more request keeps this model under `rpm` requests per rolling minute."""
    q = _sent.setdefault(model, deque())
    while True:
        now = time.monotonic()
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) < rpm:
            q.append(now)
            return
        await asyncio.sleep(60 - (now - q[0]) + random.uniform(0, 0.5))


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sem = asyncio.Semaphore(cfg.max_concurrency)
        self.calls = 0
        self.tokens = 0
        self.client = None
        if not cfg.mock:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(base_url=cfg.crusoe_base_url, api_key=cfg.crusoe_api_key)

    async def complete(self, role: str, system: str, user: str, strong: bool = False) -> str:
        self.calls += 1
        if self.cfg.mock:
            await asyncio.sleep(random.uniform(0.15, 0.5))
            return mock.llm(role, system, user)
        model = self.cfg.model_strong if strong else self.cfg.model_fast
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        extra_body = None
        if role == "extractor" and "nemotron" in model.lower():
            # Reserve the output budget for claims instead of internal reasoning.
            extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        async with self.sem:
            return await self._with_backoff(model, messages, extra_body=extra_body)

    async def _with_backoff(self, model, messages, max_tries=6, extra_body=None):
        from openai import APIStatusError, RateLimitError, InternalServerError, APIConnectionError

        for attempt in range(max_tries):
            await _pace(model, self.cfg.rpm)
            try:
                resp = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=self.cfg.max_tokens,
                    temperature=1.0,
                    top_p=0.95,
                    extra_body=extra_body,
                )
                if resp.usage:
                    self.tokens += resp.usage.total_tokens
                if resp.choices[0].finish_reason == "length":
                    if "nemotron" in model.lower():
                        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
                    raise ValueError("response exceeded max_tokens; refusing truncated output")
                content = resp.choices[0].message.content or ""
                if not content.strip():
                    raise ValueError("empty content (reasoning may have used up max_tokens)")
                return content
            except (RateLimitError, InternalServerError, APIConnectionError, ValueError):
                if attempt == max_tries - 1:
                    raise
                await asyncio.sleep(min(60, 2**attempt) + random.uniform(0, 0.5))
            except APIStatusError:
                raise

    async def json(self, role: str, system: str, user: str, strong: bool = False) -> dict:
        """Ask for JSON; on a parse failure, re-ask once with the error appended."""
        text = await self.complete(role, system, user, strong)
        try:
            return parse_json(text)
        except ValueError as e:
            text = await self.complete(
                role, system, f"{user}\n\nYour last reply was not valid JSON ({e}). Reply with JSON only.", strong
            )
            return parse_json(text)


def parse_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(str(e)) from e
