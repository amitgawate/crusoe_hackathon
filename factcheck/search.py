"""You.com Search API client."""
import asyncio
from urllib.parse import urlparse

import httpx

from .config import Config
from . import mock


class Search:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.calls = 0

    async def search(self, query: str, count: int = 5) -> list[dict]:
        """Return [{url, title, snippet, domain}]. Raises on transport errors."""
        self.calls += 1
        if self.cfg.mock:
            await asyncio.sleep(0.3)
            raw = mock.search(query)
        else:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    self.cfg.ydc_url,
                    headers={"X-API-Key": self.cfg.ydc_api_key},
                    json={"query": query, "count": count},
                )
                r.raise_for_status()
                raw = r.json().get("results", {}).get("web", [])
        out = []
        for item in raw[:count]:
            snippets = item.get("snippets") or []
            out.append(
                {
                    "url": item.get("url", ""),
                    "title": item.get("title", ""),
                    "snippet": " ".join(snippets)[:600] or item.get("description", ""),
                    "domain": domain_of(item.get("url", "")),
                }
            )
        return out


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host
