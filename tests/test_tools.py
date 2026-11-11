import asyncio
import gzip
import json

import httpx
import pytest

from research_agent.runtime import Runtime
from research_agent.tools import ArxivTool, WikipediaTool
from research_agent.tools.base import ProviderHTTP

WIKI = {"query": {"pages": [
    {"pageid": 23, "index": 2, "title": "Bank (geography)", "extract": "An edge of a river."},
    {"pageid": 12, "index": 1, "title": "Bank", "extract": "A bank is a financial institution.",
     "revisions": [{"revid": 99}]},
    {"pageid": 1, "index": 3, "title": "Disambiguation", "extract": "Ambiguous.",
     "pageprops": {"disambiguation": ""}}]}}
ATOM = b'''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title>
<summary>We propose a model based on attention.\nNo recurrence is used.</summary>
<published>2017-06-12T17:57:34Z</published><updated>2023-08-02T00:00:00Z</updated>
<author><name>Ashish Vaswani</name></author></entry></feed>'''


def search(tool_type, handler, query="bank", timeout=1):
    async def go():
        rt = Runtime()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tool = tool_type(ProviderHTTP(client, rt))
            return await tool.search(query, 3, timeout), rt
    return asyncio.run(go())


def test_wikipedia_returns_ranked_extracts_and_revisions():
    def handler(request):
        assert request.url.host == "en.wikipedia.org"
        assert request.url.params["gsrsearch"] == "bank"
        assert request.url.params["explaintext"] == "1"
        assert "User-Agent" in request.headers
        return httpx.Response(200, json=WIKI)
    result, rt = search(WikipediaTool, handler)
    assert result.status == "ok" and rt.http_calls == 1
    assert [s.title for s in result.sources] == ["Bank", "Bank (geography)"]
    assert result.sources[0].revision == "99"
    assert result.sources[0].text == "A bank is a financial institution."
    assert result.sources[1].url == "https://en.wikipedia.org/wiki/Bank_(geography)"


def test_compressed_provider_response_is_decoded_once():
    result, _ = search(WikipediaTool, lambda r: httpx.Response(
        200, content=gzip.compress(json.dumps(WIKI).encode()),
        headers={"Content-Encoding": "gzip"}))
    assert result.status == "ok" and result.sources[0].title == "Bank"


@pytest.mark.parametrize("payload", [{}, {"query": {"pages": []}}])
def test_wikipedia_empty_results(payload):
    result, _ = search(WikipediaTool, lambda r: httpx.Response(200, json=payload))
    assert result.status == "empty" and not result.sources


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"error": {"code": "badrequest"}}),
    httpx.Response(200, text="invalid json"),
    httpx.Response(403),
])
def test_wikipedia_provider_errors_are_structured(response):
    result, _ = search(WikipediaTool, lambda r: response)
    assert result.status == "error" and result.error


def test_arxiv_parses_abstract_metadata_and_canonical_link():
    def handler(request):
        assert request.url.params["search_query"] == 'all:"attention" AND all:"transformer"'
        assert request.url.params["max_results"] == "3"
        return httpx.Response(200, content=ATOM)
    result, _ = search(ArxivTool, handler, "attention transformer")
    source = result.sources[0]
    assert result.status == "ok"
    assert source.source_id == "1706.03762v7"
    assert source.url == "https://arxiv.org/abs/1706.03762v7"
    assert source.authors == ["Ashish Vaswani"]
    assert source.published.startswith("2017")
    assert "\n" not in source.text and "attention" in source.text


@pytest.mark.parametrize("body", [b"<broken", b"<html>gateway</html>",
    b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><feed>&xxe;</feed>',
    b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Error</title><id>http://arxiv.org/api/errors</id></entry></feed>'])
def test_arxiv_rejects_invalid_unsafe_or_error_feeds(body):
    result, _ = search(ArxivTool, lambda r: httpx.Response(200, content=body))
    assert result.status == "error" and not result.sources


def test_arxiv_empty_feed():
    result, _ = search(ArxivTool, lambda r: httpx.Response(
        200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"/>'))
    assert result.status == "empty"


def test_arxiv_rejects_untrusted_source_host():
    body = ATOM.replace(b"http://arxiv.org/abs/", b"https://evil.example/abs/")
    result, _ = search(ArxivTool, lambda r: httpx.Response(200, content=body))
    assert result.status == "empty"


@pytest.mark.parametrize("tool_type", [WikipediaTool, ArxivTool])
@pytest.mark.parametrize("query,count", [("", 3), ("x" * 501, 3), ("bank", 4), ("bank", True)])
def test_invalid_arguments_never_request_network(tool_type, query, count):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: pytest.fail("request should not run"))) as client:
            tool = tool_type(ProviderHTTP(client, Runtime()))
            await tool.search(query, count)
    with pytest.raises(ValueError):
        asyncio.run(go())


def test_retry_after_is_not_shortened():
    result, rt = search(WikipediaTool, lambda r: httpx.Response(429, headers={"Retry-After": "100"}))
    assert result.status == "error" and rt.http_calls == 1


def test_one_retry_counts_http_attempts(monkeypatch):
    calls = []

    async def no_sleep(delay):
        calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    responses = iter([httpx.Response(503), httpx.Response(200, json=WIKI)])
    result, rt = search(WikipediaTool, lambda r: next(responses))
    assert result.status == "ok" and rt.http_calls == 2 and calls == [1.0]


def test_arxiv_request_spacing_includes_retries(monkeypatch):
    delays = []

    async def no_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    responses = iter([httpx.Response(503), httpx.Response(200, content=ATOM)])
    result, rt = search(ArxivTool, lambda r: next(responses))
    assert result.status == "ok" and rt.http_calls == 2
    assert delays[0] == 1 and delays[1] > 2.9


def test_hanging_provider_is_cancelled():
    async def handler(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json=WIKI)
    result, _ = search(WikipediaTool, handler, timeout=0.01)
    assert result.status == "error" and result.retryable


def test_http_request_budget_prevents_network():
    async def go():
        rt = Runtime(max_http=0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: pytest.fail("budget must prevent request"))) as client:
            return await WikipediaTool(ProviderHTTP(client, rt)).search("bank")
    assert asyncio.run(go()).status == "error"


def test_provider_size_cap():
    result, _ = search(WikipediaTool, lambda r: httpx.Response(200, content=b"x" * 2_000_001))
    assert result.status == "error" and "2 MB" in result.error
