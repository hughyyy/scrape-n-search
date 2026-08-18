"""Provider adapter normalization tests (offline, mocked HTTP)."""

from __future__ import annotations

import json

import httpx
import pytest

from scrape_n_search.providers.exa import ExaProvider
from scrape_n_search.providers.firecrawl import FirecrawlProvider
from scrape_n_search.providers.jina import JinaProvider, _parse_search_markdown
from scrape_n_search.providers.scrappey import ScrappeyProvider
from scrape_n_search.providers.serper import SerperProvider
from scrape_n_search.providers.tavily import TavilyProvider
from scrape_n_search.providers.tinyfish import TinyFishProvider
from scrape_n_search.providers.zenrows import ZenRowsProvider

from conftest import make_client


async def test_tavily_search_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Example",
                        "url": "https://example.com",
                        "content": "a snippet",
                        "published_date": "2026-01-02",
                        "score": 0.91,
                    }
                ]
            },
        )

    provider = TavilyProvider("k", make_client(handler))
    results = await provider.search("q")
    assert len(results) == 1
    r = results[0]
    assert (r.title, r.url, r.snippet) == ("Example", "https://example.com", "a snippet")
    assert r.published_date == "2026-01-02"
    assert r.score == 0.91


async def test_tavily_disabled_without_key():
    assert not TavilyProvider(None).enabled


async def test_tavily_fetch_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/extract"
        return httpx.Response(
            200,
            json={"results": [{"url": "https://example.com", "raw_content": "# Hello"}]},
        )

    provider = TavilyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com")
    assert page.url == "https://example.com"
    assert page.content == "# Hello"
    assert page.format == "markdown"


async def test_exa_search_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        body = request.read().decode()
        assert '"numResults":3' in body
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T", "url": "https://t", "text": "long text here", "publishedDate": "2026-03-01"}
                ]
            },
        )

    provider = ExaProvider("k", make_client(handler))
    results = await provider.search("q", max_results=3)
    assert results[0].snippet == "long text here"
    assert results[0].published_date == "2026-03-01"


async def test_tinyfish_search_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "k"
        assert request.url.params["query"] == "q"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"position": 1, "site_name": "x", "title": "T", "snippet": "s", "url": "https://t"}
                ]
            },
        )

    provider = TinyFishProvider("k", make_client(handler))
    results = await provider.search("q")
    assert results[0].title == "T"
    assert results[0].url == "https://t"


async def test_tinyfish_fetch_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.fetch.tinyfish.ai"
        return httpx.Response(
            200,
            json={"results": [{"url": "https://t", "title": "T", "text": "# body", "format": "markdown"}], "errors": []},
        )

    provider = TinyFishProvider("k", make_client(handler))
    page = await provider.fetch("https://t")
    assert page.content == "# body"
    assert page.title == "T"


async def test_zenrows_fetch_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["url"] == "https://example.com"
        assert request.url.params["response_type"] == "markdown"
        return httpx.Response(200, text="# Hello", headers={"Zr-Final-Url": "https://final.example.com"})

    provider = ZenRowsProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com")
    assert page.content == "# Hello"
    assert page.url == "https://final.example.com"


async def test_zenrows_extract_returns_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/extract"
        return httpx.Response(200, json={"title": "T", "price": 42})

    provider = ZenRowsProvider("k", make_client(handler))
    data = await provider.extract("https://example.com")
    assert data == {"title": "T", "price": 42}


async def test_firecrawl_search_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/search"
        return httpx.Response(
            200,
            json={"success": True, "data": {"web": [{"url": "https://t", "title": "T", "description": "d"}]}},
        )

    provider = FirecrawlProvider("k", make_client(handler))
    results = await provider.search("q")
    assert results[0].title == "T"
    assert results[0].snippet == "d"


async def test_firecrawl_fetch_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/scrape"
        return httpx.Response(
            200,
            json={"success": True, "data": {"url": "https://t", "title": "T", "markdown": "# body"}},
        )

    provider = FirecrawlProvider("k", make_client(handler))
    page = await provider.fetch("https://t")
    assert page.content == "# body"
    assert page.title == "T"


async def test_firecrawl_crawl_polls_to_completion():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "id": "job1"})
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"status": "completed", "data": [{"url": "https://t/a", "title": "A", "markdown": "# a"}]},
        )

    provider = FirecrawlProvider("k", make_client(handler))
    pages = await provider.crawl("https://t", poll_interval=0.01, timeout=1)
    assert calls["n"] >= 1
    assert pages[0].url == "https://t/a"
    assert pages[0].content == "# a"


async def test_serper_search_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "k"
        return httpx.Response(
            200,
            json={"organic": [{"title": "T", "link": "https://t", "snippet": "s", "date": "2 days ago"}]},
        )

    provider = SerperProvider("k", make_client(handler))
    results = await provider.search("q")
    assert results[0].url == "https://t"
    assert results[0].published_date == "2 days ago"


async def test_jina_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "r.jina.ai"
        return httpx.Response(200, text="# Hello")

    provider = JinaProvider(client=make_client(handler))
    page = await provider.fetch("https://example.com")
    assert page.content == "# Hello"


async def test_jina_search_parses_markdown():
    sample = (
        "Title: First Result\nURL: https://first.com\nDescription: about first\n\n"
        "Title: Second Result\nURL: https://second.com\nDescription: about second\n\n"
        "Some trailing text without fields"
    )
    results = _parse_search_markdown(sample, 5)
    assert len(results) == 2
    assert results[0].title == "First Result"
    assert results[0].url == "https://first.com"
    assert results[0].snippet == "about first"
    assert results[1].title == "Second Result"


async def test_scrappey_fetch_markdown():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "k"
        body = json.loads(request.read())
        assert body == {
            "cmd": "request.get",
            "url": "https://example.com",
            "markdown": True,
            "filter": ["markdown"],
            "requestType": "browser",
        }
        return httpx.Response(
            200,
            json={
                "solution": {
                    "statusCode": 200,
                    "currentUrl": "https://final.example.com",
                    "markdown": "# Hello",
                    "response": "<html>...</html>",
                },
                "timeElapsed": 1234,
                "data": "success",
            },
        )

    provider = ScrappeyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com")
    assert page.content == "# Hello"
    assert page.format == "markdown"
    assert page.url == "https://final.example.com"


async def test_scrappey_fetch_html():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body == {"cmd": "request.get", "url": "https://example.com", "requestType": "request"}
        return httpx.Response(
            200,
            json={"solution": {"response": "<html>hi</html>", "innerText": "hi"}, "data": "success"},
        )

    provider = ScrappeyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", format="html", js_render=False)
    assert page.content == "<html>hi</html>"
    assert page.format == "html"


async def test_scrappey_basic_mode_never_requests_markdown():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        # basic mode: no markdown/filter, even when markdown was requested
        assert body == {"cmd": "request.get", "url": "https://example.com", "requestType": "request"}
        return httpx.Response(
            200,
            json={"solution": {"response": "<html>raw</html>", "innerText": "raw"}, "data": "success"},
        )

    provider = ScrappeyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", format="markdown", js_render=False)
    assert page.content == "<html>raw</html>"
    assert page.format == "html"


async def test_scrappey_failed_request_raises():
    from scrape_n_search.errors import ProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "error", "solution": {}})

    provider = ScrappeyProvider("k", make_client(handler))
    with pytest.raises(ProviderError):
        await provider.fetch("https://example.com")


async def test_scrappey_browser_actions():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["requestType"] == "browser"
        assert body["markdown"] is True
        assert body["browserActions"] == [{"type": "execute_js", "code": "document.body.innerText"}]
        return httpx.Response(
            200,
            json={
                "solution": {
                    "currentUrl": "https://final.example.com",
                    "markdown": "# After actions",
                    "javascriptReturn": ["clicked", "body text"],
                },
                "data": "success",
            },
        )

    provider = ScrappeyProvider("k", make_client(handler))
    result = await provider.browser_actions(
        "https://example.com",
        actions=[{"type": "execute_js", "code": "document.body.innerText"}],
    )
    assert result.content == "# After actions"
    assert result.format == "markdown"
    assert result.javascript_return == ["clicked", "body text"]
    assert result.url == "https://final.example.com"


async def test_scrappey_browser_actions_empty_raises():
    from scrape_n_search.errors import ProviderError

    provider = ScrappeyProvider("k", make_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ProviderError):
        await provider.browser_actions("https://example.com", actions=[])


async def test_scrappey_screenshot():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["screenshot_fullpage"] is True
        assert "markdown" not in body
        return httpx.Response(
            200,
            json={"solution": {"screenshot": "aGVsbG8="}, "data": "success"},
        )

    provider = ScrappeyProvider("k", make_client(handler))
    img = await provider.screenshot("https://example.com", full_page=True)
    assert img == "aGVsbG8="


async def test_scrappey_disabled_without_key():
    assert not ScrappeyProvider(None).enabled


async def test_unsupported_tool_raises():
    from scrape_n_search.errors import ProviderError

    provider = SerperProvider("k", make_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ProviderError):
        await provider.fetch("https://t")
