import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from server import app


@pytest.fixture
def remote_page(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])

    def serve(status=200, html="", headers=None):
        async def send(client, request, **kwargs):
            return httpx.Response(status, text=html, request=request,
                                  headers=headers or {"content-type": "text/html"})
        monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return serve


def test_download_returns_article_text_and_metadata(remote_page):
    paragraph = "The observatory recorded twelve new stars during its survey. Scientists published the measurements on Friday."
    remote_page(html=f'<html><head><title>New stars</title></head><body><nav>Subscribe</nav>'
                     f'<article><h1>New stars</h1><p>{paragraph}</p>'
                     '<p>The team used a telescope in Chile to observe the southern sky over six months.</p>'
                     '</article></body></html>')
    response = TestClient(app).post("/api/article", json={"url": "https://example.com/news"})
    assert response.status_code == 200
    article = response.json()
    assert paragraph in article["text"]
    assert article["title"] == "New stars"
    assert article["url"] == "https://example.com/news"
    assert "Subscribe" not in article["text"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/", "http://[::1]/", "not a url"])
def test_rejects_nonpublic_or_invalid_urls(url):
    response = TestClient(app).post("/api/article", json={"url": url})
    assert response.status_code == 400


def test_redirect_cannot_target_local_services(remote_page):
    remote_page(status=302, headers={"location": "http://127.0.0.1/"})
    response = TestClient(app).post("/api/article", json={"url": "https://example.com/news"})
    assert response.status_code == 400


def test_empty_page_has_actionable_error(remote_page):
    remote_page(html="<html><body></body></html>")
    response = TestClient(app).post("/api/article", json={"url": "https://example.com/news"})
    assert response.status_code == 422
    assert "paste" in response.json()["detail"].lower()


def test_blocked_download_is_reported(remote_page):
    remote_page(status=403)
    response = TestClient(app).post("/api/article", json={"url": "https://example.com/news"})
    assert response.status_code == 502


def test_download_connects_to_validated_address(monkeypatch, remote_page):
    async def send(client, request, **kwargs):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(403, request=request)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    assert TestClient(app).post("/api/article", json={"url": "https://example.com/news"}).status_code == 502


def test_rejects_non_html_content(remote_page):
    remote_page(headers={"content-type": "application/pdf"})
    assert TestClient(app).post("/api/article", json={"url": "https://example.com/news"}).status_code == 422


def test_rejects_oversized_page(monkeypatch, remote_page):
    monkeypatch.setattr("factcheck.articles.MAX_DOWNLOAD_BYTES", 100)
    remote_page(html="x" * 101)
    assert TestClient(app).post("/api/article", json={"url": "https://example.com/news"}).status_code == 413
