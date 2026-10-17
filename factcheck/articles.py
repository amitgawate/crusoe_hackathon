"""Download public HTML pages and extract their readable article text."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException
from trafilatura import bare_extraction

MAX_DOWNLOAD_BYTES = 5_000_000


async def validate_url(url: str) -> str:
    """Reject private destinations and return a public address to connect to."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
            raise ValueError()
        try:
            addresses = [ipaddress.ip_address(parsed.hostname)]
        except ValueError:
            records = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname,
                                             parsed.port or 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(record[4][0]) for record in records]
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError()
        return str(sorted(addresses, key=lambda address: address.version)[0])
    except (ValueError, OSError):
        raise HTTPException(400, "Enter a public http:// or https:// article URL.") from None


async def fetch_article(url: str) -> dict:
    """Bound download size/time; validate each redirect before following it."""
    url = url.strip()
    try:
        async with asyncio.timeout(30):
            async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False,
                                         limits=httpx.Limits(max_keepalive_connections=0),
                                         headers={"User-Agent": "DoneMeansDone/1.0 (article reader)"}) as client:
                for _ in range(6):
                    address = await validate_url(url)
                    target = httpx.URL(url)
                    # Pin the validated address while retaining Host and TLS certificate checks.
                    async with client.stream("GET", target.copy_with(host=address),
                                             headers={"Host": target.netloc.decode("ascii")},
                                             extensions={"sni_hostname": target.raw_host.decode("ascii")}) as response:
                        if response.is_redirect and response.headers.get("location"):
                            url = urljoin(url, response.headers["location"])
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";")[0].strip()
                        if content_type not in ("text/html", "application/xhtml+xml"):
                            raise HTTPException(422, "This link is not an HTML article. Paste its text instead.")
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_DOWNLOAD_BYTES:
                                raise HTTPException(413, "This page is too large. Paste the article text instead.")
                        break
                else:
                    raise HTTPException(422, "Too many redirects. Paste the article text instead.")
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(502, "Could not download this page. It may block downloads; paste the article text instead.") from None

    article = await asyncio.to_thread(bare_extraction, bytes(content), url=url,
                                      with_metadata=True, include_comments=False)
    if not article or not article.text or len(article.text.split()) < 20:
        raise HTTPException(422, "No readable article found. The page may require login or JavaScript; paste its text instead.")
    return {"url": url, "title": article.title or "", "published": article.date or "",
            "publisher": article.sitename or "", "text": article.text,
            "words": len(article.text.split())}
