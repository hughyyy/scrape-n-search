"""Provider adapter normalization tests (offline, mocked HTTP)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from conftest import make_client
from scrape_n_search.errors import ProviderError
from scrape_n_search.providers.base import BaseProvider
from scrape_n_search.providers.exa import ExaProvider
from scrape_n_search.providers.firecrawl import FirecrawlProvider
from scrape_n_search.providers.jina import JinaProvider, _parse_search_markdown
from scrape_n_search.providers.scrappey import ScrappeyProvider
from scrape_n_search.providers.serper import SerperProvider
from scrape_n_search.providers.tavily import TavilyProvider
from scrape_n_search.providers.tinyfish import TinyFishProvider
from scrape_n_search.providers.zenrows import ZenRowsProvider


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
        # v2 nests title/url under data.metadata
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "# body",
                    "metadata": {"title": "T", "url": "https://final"},
                },
            },
        )

    provider = FirecrawlProvider("k", make_client(handler))
    page = await provider.fetch("https://t")
    assert page.content == "# body"
    assert page.title == "T"
    assert page.url == "https://final"


async def test_firecrawl_fetch_accepts_list_title():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"markdown": "x", "metadata": {"title": ["A", "B"]}}},
        )

    provider = FirecrawlProvider("k", make_client(handler))
    assert (await provider.fetch("https://t")).title == "A B"


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
            json={"solution": {"response": "<html><body><p>raw</p></body></html>", "innerText": "raw"}, "data": "success"},
        )

    provider = ScrappeyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", format="markdown", js_render=False)
    # the API cannot convert; the adapter reduces HTML to readable text so the
    # requested markdown shape is honored as closely as basic mode allows.
    assert page.content == "raw"
    assert page.format == "markdown"


async def test_scrappey_basic_mode_html_stays_raw():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"solution": {"response": "<html>raw</html>"}, "data": "success"},
        )

    provider = ScrappeyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", format="html", js_render=False)
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


async def test_scrappey_session_create():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body == {"cmd": "sessions.create", "session": "s1"}
        return httpx.Response(
            200,
            json={"data": "success", "solution": {"session": "s1"}},
        )

    provider = ScrappeyProvider("k", make_client(handler))
    result = await provider.session("create", "s1")
    assert result["session"] == "s1"
    assert result["op"] == "create"


async def test_scrappey_session_destroy():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body == {"cmd": "sessions.destroy", "session": "s1"}
        return httpx.Response(200, json={"data": "success", "solution": {}})

    provider = ScrappeyProvider("k", make_client(handler))
    result = await provider.session("destroy", "s1")
    assert result["op"] == "destroy"


async def test_scrappey_session_unknown_op_raises():
    from scrape_n_search.errors import ProviderError

    provider = ScrappeyProvider("k", make_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ProviderError, match="unknown op"):
        await provider.session("pause", "s1")


async def test_scrappey_session_empty_name_raises():
    from scrape_n_search.errors import ProviderError

    provider = ScrappeyProvider("k", make_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ProviderError, match="session name is required"):
        await provider.session("create", "")


async def test_scrappey_session_failure_raises():
    from scrape_n_search.errors import ProviderError

    provider = ScrappeyProvider(
        "k", make_client(lambda r: httpx.Response(200, json={"data": "error", "solution": {}}))
    )
    with pytest.raises(ProviderError, match="session create failed"):
        await provider.session("create", "s1")


async def test_scrappey_fetch_uses_session():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["session"] == "s1"
        assert body["requestType"] == "browser"
        return httpx.Response(
            200,
            json={"solution": {"response": "<html>logged in</html>"}, "data": "success"},
        )

    provider = ScrappeyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", session="s1", js_render=True, format="html")
    assert page.content == "<html>logged in</html>"


async def test_scrappey_actions_and_screenshot_use_session():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["session"] == "s1"
        return httpx.Response(200, json={"solution": {"response": "x", "screenshot": "img"}, "data": "success"})

    provider = ScrappeyProvider("k", make_client(handler))
    await provider.browser_actions("https://example.com", actions=[{"type": "wait", "wait": 1}], session="s1")
    await provider.screenshot("https://example.com", session="s1")


async def test_unsupported_tool_raises():
    from scrape_n_search.errors import ProviderError

    provider = SerperProvider("k", make_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ProviderError):
        await provider.fetch("https://t")


# --- Scrappey: request-body details and failure paths ----------------------


async def test_scrappey_fetch_sends_proxy_country_and_session():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["proxyCountry"] == "UnitedStates"
        assert body["session"] == "s1"
        assert body["requestType"] == "browser"
        return httpx.Response(200, json={"solution": {"response": "x"}, "data": "success"})

    provider = ScrappeyProvider("k", make_client(handler))
    await provider.fetch(
        "https://example.com", js_render=True, format="html", proxy_country="UnitedStates", session="s1"
    )


async def test_scrappey_browser_html_mode_skips_markdown_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["requestType"] == "browser"
        assert "markdown" not in body and "filter" not in body
        return httpx.Response(200, json={"solution": {"response": "<html>x</html>"}, "data": "success"})

    provider = ScrappeyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", js_render=True, format="html")
    assert page.content == "<html>x</html>"
    assert page.format == "html"


async def test_scrappey_markdown_falls_back_to_inner_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"solution": {"innerText": "# from innerText"}, "data": "success"}
        )

    provider = ScrappeyProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", js_render=True, format="markdown")
    assert page.content == "# from innerText"
    assert page.format == "markdown"


async def test_scrappey_fetch_empty_solution_no_crash():
    provider = ScrappeyProvider("k", make_client(lambda r: httpx.Response(200, json={"solution": {}, "data": "success"})))
    page = await provider.fetch("https://example.com")
    assert page.content == ""
    assert page.url == "https://example.com"


async def test_scrappey_browser_actions_html_mode_and_failure():
    from scrape_n_search.errors import ProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert "markdown" not in body
        return httpx.Response(
            200, json={"solution": {"response": "x", "javascriptReturn": ["r1"]}, "data": "success"}
        )

    provider = ScrappeyProvider("k", make_client(handler))
    result = await provider.browser_actions(
        "https://example.com", actions=[{"type": "wait", "wait": 1}], format="html"
    )
    assert result.format == "html"
    assert result.javascript_return == ["r1"]

    bad = ScrappeyProvider("k", make_client(lambda r: httpx.Response(200, json={"data": "error"})))
    with pytest.raises(ProviderError, match="browser_actions failed"):
        await bad.browser_actions("https://example.com", actions=[{"type": "wait", "wait": 1}])


async def test_scrappey_screenshot_selector_vs_fullpage():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body.get("screenshot_selector") == "#price"
        assert "screenshot_fullpage" not in body
        return httpx.Response(200, json={"solution": {"screenshot": "img"}, "data": "success"})

    provider = ScrappeyProvider("k", make_client(handler))
    assert await provider.screenshot("https://example.com", selector="#price") == "img"


async def test_scrappey_screenshot_failures():
    from scrape_n_search.errors import ProviderError

    no_img = ScrappeyProvider("k", make_client(lambda r: httpx.Response(200, json={"solution": {}, "data": "success"})))
    with pytest.raises(ProviderError, match="no screenshot"):
        await no_img.screenshot("https://example.com")

    failed = ScrappeyProvider("k", make_client(lambda r: httpx.Response(200, json={"data": "error"})))
    with pytest.raises(ProviderError, match="screenshot failed"):
        await failed.screenshot("https://example.com")


# --- Firecrawl: params, crawl states, extract ------------------------------


async def test_firecrawl_search_sends_freshness_country_location():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["tbs"] == "qdr:w"
        assert body["country"] == "us"
        assert body["location"] == "New York"
        return httpx.Response(200, json={"data": {"web": [{"title": "T", "url": "https://t", "description": "d"}]}})

    provider = FirecrawlProvider("k", make_client(handler))
    results = await provider.search("q", freshness="week", country="us", location="New York")
    assert results[0].title == "T"


async def test_firecrawl_search_ignores_unknown_freshness():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert "tbs" not in body
        return httpx.Response(200, json={"data": {"web": []}})

    provider = FirecrawlProvider("k", make_client(handler))
    assert await provider.search("q", freshness="decade") == []


async def test_firecrawl_fetch_html_format():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["formats"] == [{"type": "html"}]
        return httpx.Response(200, json={"data": {"url": "https://t", "html": "<p>hi</p>"}})

    provider = FirecrawlProvider("k", make_client(handler))
    page = await provider.fetch("https://t", format="html")
    assert page.content == "<p>hi</p>"
    assert page.format == "html"


async def test_firecrawl_fetch_empty_data_no_crash():
    provider = FirecrawlProvider("k", make_client(lambda r: httpx.Response(200, json={})))
    page = await provider.fetch("https://t")
    assert page.content == ""
    assert page.url == "https://t"


async def test_firecrawl_crawl_no_job_id_raises():
    from scrape_n_search.errors import ProviderError

    provider = FirecrawlProvider("k", make_client(lambda r: httpx.Response(200, json={"success": True})))
    with pytest.raises(ProviderError, match="no job id"):
        await provider.crawl("https://t", poll_interval=0.01)


async def test_firecrawl_crawl_failed_state_raises():
    from scrape_n_search.errors import ProviderError

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "id": "job1"})
        calls["n"] += 1
        return httpx.Response(200, json={"status": "failed", "error": "blocked"})

    provider = FirecrawlProvider("k", make_client(handler))
    with pytest.raises(ProviderError, match="crawl failed"):
        await provider.crawl("https://t", poll_interval=0.01, timeout=1)
    assert calls["n"] == 1


async def test_firecrawl_crawl_cancelled_state_raises():
    from scrape_n_search.errors import ProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "id": "job1"})
        return httpx.Response(200, json={"status": "cancelled"})

    provider = FirecrawlProvider("k", make_client(handler))
    with pytest.raises(ProviderError, match="cancelled"):
        await provider.crawl("https://t", poll_interval=0.01, timeout=1)


async def test_firecrawl_crawl_times_out():
    from scrape_n_search.errors import ProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "id": "job1"})
        return httpx.Response(200, json={"status": "processing"})

    provider = FirecrawlProvider("k", make_client(handler))
    with pytest.raises(ProviderError, match="timed out"):
        await provider.crawl("https://t", poll_interval=0.01, timeout=0.02)


async def test_firecrawl_extract_polls_async_job():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "id": "job1"})
        return httpx.Response(
            200,
            json={"success": True, "status": "completed", "data": {"title": "T"}},
        )

    provider = FirecrawlProvider("k", make_client(handler))
    assert await provider.extract("https://t", prompt="price") == {"title": "T"}
    assert calls[0] == ("POST", "/v2/extract")
    assert ("GET", "/v2/extract/job1") in calls


async def test_firecrawl_extract_sends_prompt_only_when_given():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.read()))
        # no job id -> synchronous/legacy inline response
        return httpx.Response(200, json={"data": {"title": "T"}})

    provider = FirecrawlProvider("k", make_client(handler))
    assert await provider.extract("https://t", prompt="price") == {"title": "T"}
    assert await provider.extract("https://t") == {"title": "T"}
    assert seen[0] == {"urls": ["https://t"], "prompt": "price"}
    assert seen[1] == {"urls": ["https://t"]}


async def test_firecrawl_extract_failed_state_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job1"})
        return httpx.Response(200, json={"status": "failed"})

    provider = FirecrawlProvider("k", make_client(handler))
    with pytest.raises(ProviderError, match="failed"):
        await provider.extract("https://t", poll_interval=0.01, timeout=1)


async def test_firecrawl_extract_times_out():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job1"})
        return httpx.Response(200, json={"status": "processing"})

    provider = FirecrawlProvider("k", make_client(handler))
    with pytest.raises(ProviderError, match="timed out"):
        await provider.extract("https://t", poll_interval=0.01, timeout=0.02)


# --- Jina: key requirements, fetch errors, parse edges ---------------------


async def test_jina_search_requires_key():
    from scrape_n_search.errors import ProviderError

    provider = JinaProvider(client=make_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ProviderError, match="JINA_API_KEY"):
        await provider.search("q")


async def test_jina_search_with_key_sends_auth_and_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        assert request.url.params["q"] == "pizza"
        return httpx.Response(
            200,
            text=(
                "Title: A\nURL: https://a\nDescription: first\n\n"
                "Title: B\nURL: https://b\nDescription: second\n\n"
            ),
        )

    provider = JinaProvider("k", make_client(handler))
    results = await provider.search("pizza")
    assert [r.title for r in results] == ["A", "B"]
    assert results[1].snippet == "second"


async def test_jina_fetch_http_error_raises():
    import httpx as _httpx

    provider = JinaProvider(client=make_client(lambda r: _httpx.Response(500, text="boom")))
    with pytest.raises(_httpx.HTTPStatusError):
        await provider.fetch("https://example.com")


async def test_jina_parse_skips_fieldless_blocks_and_caps():
    sample = (
        "Title: A\nURL: https://a\n\n"          # no description
        "no fields here\n\n"                    # skipped entirely
        "Title: B\nURL: https://b\n\n"
        "Title: C\nURL: https://c\n\n"
    )
    results = _parse_search_markdown(sample, 2)
    assert len(results) == 2
    assert results[0].title == "A" and results[0].snippet == ""
    assert results[1].title == "B"


async def test_jina_parse_uses_url_as_title_fallback():
    results = _parse_search_markdown("URL: https://only.com\nDescription: d", 5)
    assert len(results) == 1
    assert results[0].title == "https://only.com"
    assert results[0].url == "https://only.com"


# --- ZenRows fetch params ---------------------------------------------------


async def test_zenrows_fetch_passes_js_render_premium_proxy_mode():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["js_render"] == "true"
        assert request.url.params["premium_proxy"] == "true"
        assert request.url.params["mode"] == "render"
        assert "response_type" not in request.url.params  # html default
        return httpx.Response(200, text="# html")

    provider = ZenRowsProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", format="html", js_render=True, premium_proxy=True, mode="render")
    assert page.content == "# html"
    assert page.format == "html"


async def test_zenrows_fetch_text_mode_sets_plaintext():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["response_type"] == "plaintext"
        return httpx.Response(200, text="plain")

    provider = ZenRowsProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", format="text")
    assert page.format == "text"


# --- Tavily extract failure -------------------------------------------------


async def test_tavily_extract_failure_raises():
    from scrape_n_search.errors import ProviderError

    provider = TavilyProvider("k", make_client(lambda r: httpx.Response(
        200, json={"results": [], "failed_results": [{"url": "https://x", "error": "blocked"}]}
    )))
    with pytest.raises(ProviderError, match="blocked"):
        await provider.fetch("https://x")


# --- Exa: fetch normalization, no-result error, search params ---------------


async def test_exa_fetch_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/contents"
        body = json.loads(request.read())
        assert body == {"ids": ["https://example.com"], "text": True}
        return httpx.Response(
            200,
            json={"results": [{"url": "https://final.example.com", "title": "T", "text": "# body"}]},
        )

    provider = ExaProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com")
    assert page.url == "https://final.example.com"
    assert page.content == "# body"
    assert page.title == "T"
    assert page.format == "markdown"


async def test_exa_fetch_no_result_raises():
    from scrape_n_search.errors import ProviderError

    provider = ExaProvider("k", make_client(lambda r: httpx.Response(200, json={"results": []})))
    with pytest.raises(ProviderError, match="no result"):
        await provider.fetch("https://example.com")


async def test_exa_search_sends_optional_params_and_caps_snippet():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["type"] == "neural"
        assert body["category"] == "company"
        assert body["includeDomains"] == ["a.com"]
        assert body["excludeDomains"] == ["b.com"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T", "url": "https://t", "highlights": ["hl"], "publishedDate": "2026-01-01", "score": 0.5},
                    {"title": "T2", "url": "https://t2", "text": "x" * 900},
                ]
            },
        )

    provider = ExaProvider("k", make_client(handler))
    results = await provider.search(
        "q", search_type="neural", category="company",
        include_domains=["a.com"], exclude_domains=["b.com"],
    )
    # highlights list is flattened to a string (unified snippet contract)
    assert results[0].snippet == "hl"
    assert results[0].score == 0.5
    # long text snippet capped at 500 chars
    assert len(results[1].snippet) == 500


async def test_exa_search_flattens_multi_highlight_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"title": "T", "url": "https://t", "highlights": ["first", "second"]}]},
        )

    provider = ExaProvider("k", make_client(handler))
    results = await provider.search("q")
    assert results[0].snippet == "first second"


async def test_exa_search_empty_highlights_type_tolerance():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t", "highlights": []}]})

    provider = ExaProvider("k", make_client(handler))
    results = await provider.search("q")
    assert len(results) == 1
    assert results[0].snippet == ""  # empty highlights -> empty snippet, not a crash



# --- Tavily: search params body ---------------------------------------------


async def test_tavily_search_sends_optional_params():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["search_depth"] == "advanced"
        assert body["topic"] == "news"
        assert body["time_range"] == "week"
        assert body["country"] == "us"
        assert body["include_domains"] == ["a.com"]
        assert body["exclude_domains"] == ["b.com"]
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t"}]})

    provider = TavilyProvider("k", make_client(handler))
    results = await provider.search(
        "q", search_depth="advanced", topic="news", freshness="week", country="us",
        include_domains=["a.com"], exclude_domains=["b.com"],
    )
    assert results[0].title == "T"


async def test_tavily_search_defaults_body():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body == {
            "api_key": "k",
            "query": "q",
            "max_results": 5,
            "search_depth": "basic",
            "topic": "general",
        }
        return httpx.Response(200, json={"results": []})

    provider = TavilyProvider("k", make_client(handler))
    assert await provider.search("q") == []


async def test_tavily_search_maps_hour_freshness_to_day():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.read())["time_range"] == "day"
        return httpx.Response(200, json={"results": []})

    provider = TavilyProvider("k", make_client(handler))
    assert await provider.search("q", freshness="hour") == []



def test_fetch_format_declarations_match_adapters():
    from scrape_n_search.providers.exa import ExaProvider
    from scrape_n_search.providers.firecrawl import FirecrawlProvider
    from scrape_n_search.providers.jina import JinaProvider
    from scrape_n_search.providers.scrappey import ScrappeyProvider
    from scrape_n_search.providers.tavily import TavilyProvider
    from scrape_n_search.providers.tinyfish import TinyFishProvider
    from scrape_n_search.providers.zenrows import ZenRowsProvider

    assert ExaProvider.fetch_formats == frozenset({"markdown"})
    assert TavilyProvider.fetch_formats == frozenset({"markdown"})
    assert JinaProvider.fetch_formats == frozenset({"markdown"})
    for cls in (FirecrawlProvider, ScrappeyProvider, TinyFishProvider, ZenRowsProvider):
        assert cls.fetch_formats == frozenset({"markdown", "html"})

# --- TinyFish: search params, result cap, fetch error ------------------------


async def test_tinyfish_search_sends_optional_params():
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert params["recency_minutes"] == "1440"  # "day" freshness
        assert params["location"] == "Berlin"
        assert params["language"] == "de"
        assert params["domain_type"] == "news"
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t", "snippet": "s"}]})

    provider = TinyFishProvider("k", make_client(handler))
    results = await provider.search("q", freshness="day", location="Berlin", language="de", domain_type="news")
    assert results[0].title == "T"


async def test_tinyfish_search_ignores_unknown_freshness_and_web_domain_type():
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert "recency_minutes" not in params
        assert "domain_type" not in params  # "web" is the default -> omitted
        return httpx.Response(200, json={"results": []})

    provider = TinyFishProvider("k", make_client(handler))
    assert await provider.search("q", freshness="decade") == []


async def test_tinyfish_search_caps_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"title": f"r{i}", "url": f"https://t/{i}"} for i in range(10)]},
        )

    provider = TinyFishProvider("k", make_client(handler))
    results = await provider.search("q", max_results=3)
    assert len(results) == 3


async def test_tinyfish_fetch_error_path():
    from scrape_n_search.errors import ProviderError

    provider = TinyFishProvider("k", make_client(lambda r: httpx.Response(
        200, json={"results": [], "errors": [{"message": "blocked"}]}
    )))
    with pytest.raises(ProviderError, match="blocked"):
        await provider.fetch("https://example.com")


# --- Serper: geo/language params, empty results ------------------------------


async def test_serper_search_sends_country_language_and_caps():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["gl"] == "us"
        assert body["hl"] == "en"
        assert body["num"] == 2
        return httpx.Response(
            200,
            json={"organic": [{"title": "T", "link": "https://t", "snippet": "s", "date": "y"} for _ in range(5)]},
        )

    provider = SerperProvider("k", make_client(handler))
    results = await provider.search("q", max_results=2, country="us", language="en")
    assert len(results) == 2


async def test_serper_search_empty_organic():
    provider = SerperProvider("k", make_client(lambda r: httpx.Response(200, json={"organic": []})))
    assert await provider.search("q") == []


# --- ZenRows: extract request shape, proxy_country ---------------------------


async def test_zenrows_extract_request_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/extract"
        assert request.url.params["apikey"] == "k"
        assert request.url.params["url"] == "https://example.com"
        assert request.url.params["extract"] == "auto"
        return httpx.Response(200, json={"title": "T", "price": 1})

    provider = ZenRowsProvider("k", make_client(handler))
    assert await provider.extract("https://example.com") == {"title": "T", "price": 1}


async def test_zenrows_fetch_sends_proxy_country():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["proxy_country"] == "us"
        return httpx.Response(200, text="# body")

    provider = ZenRowsProvider("k", make_client(handler))
    page = await provider.fetch("https://example.com", proxy_country="us")
    assert page.url == "https://example.com"


# --- Scrappey: screenshot default body, browser_actions defaults -------------


async def test_scrappey_screenshot_default_body():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["screenshot"] is True
        assert "screenshot_fullpage" not in body
        assert "screenshot_selector" not in body
        return httpx.Response(200, json={"solution": {"screenshot": "img"}, "data": "success"})

    provider = ScrappeyProvider("k", make_client(handler))
    assert await provider.screenshot("https://example.com") == "img"


async def test_scrappey_browser_actions_defaults_to_markdown_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["markdown"] is True
        assert body["filter"] == ["markdown"]
        return httpx.Response(
            200, json={"solution": {"markdown": "# m"}, "data": "success"}
        )

    provider = ScrappeyProvider("k", make_client(handler))
    result = await provider.browser_actions("https://example.com", actions=[{"type": "wait", "wait": 1}])
    assert result.content == "# m"
    assert result.format == "markdown"
    assert result.javascript_return == []  # missing javascriptReturn -> []


# --- Firecrawl: search data-as-list, crawl without data ----------------------


async def test_firecrawl_search_data_as_plain_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"title": "T", "url": "https://t"}]})

    provider = FirecrawlProvider("k", make_client(handler))
    results = await provider.search("q")
    assert len(results) == 1


async def test_firecrawl_crawl_completed_without_data():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "id": "job1"})
        return httpx.Response(200, json={"status": "completed", "data": []})

    provider = FirecrawlProvider("k", make_client(handler))
    assert await provider.crawl("https://t", poll_interval=0.01, timeout=1) == []


async def test_firecrawl_extract_missing_data_returns_none():
    provider = FirecrawlProvider("k", make_client(lambda r: httpx.Response(200, json={"data": None})))
    assert await provider.extract("https://t") is None


# --- BaseProvider: 'not implemented' stubs and disabled-by-empty-key ---------


class BareProvider(BaseProvider):
    """Declares all capabilities but overrides nothing: base stubs must raise."""

    name = "bare"
    capabilities = frozenset({"search", "fetch", "crawl", "extract", "session"})


async def test_base_stubs_raise_not_implemented():
    from scrape_n_search.errors import ProviderError

    provider = BareProvider()
    for tool, kwargs in [
        ("search", {"query": "q"}),
        ("fetch", {"url": "https://x"}),
        ("crawl", {"url": "https://x"}),
        ("extract", {"url": "https://x"}),
        ("session", {"op": "create", "session": "s"}),
    ]:
        with pytest.raises(ProviderError, match="not implemented"):
            await getattr(provider, tool)(**kwargs)


async def test_base_stub_requires_declared_capability():
    # capability not declared -> _require raises before the stub
    from scrape_n_search.errors import ProviderError

    provider = BaseProvider()  # empty capabilities
    with pytest.raises(ProviderError, match="does not support 'search'"):
        await provider.search("q")


async def test_providers_disabled_by_empty_string_key():
    assert not ExaProvider("").enabled
    assert not FirecrawlProvider("").enabled
    assert not SerperProvider("").enabled
    assert not TinyFishProvider("").enabled
    assert not ZenRowsProvider("").enabled
    assert not ScrappeyProvider("").enabled
    assert not TavilyProvider("").enabled


async def test_scrappey_browser_actions_send_proxy_country_and_session():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["proxyCountry"] == "Germany"
        assert body["session"] == "s1"
        return httpx.Response(200, json={"solution": {"markdown": "# m"}, "data": "success"})

    provider = ScrappeyProvider("k", make_client(handler))
    await provider.browser_actions(
        "https://example.com", actions=[{"type": "wait", "wait": 1}],
        proxy_country="Germany", session="s1",
    )


async def test_scrappey_screenshot_send_proxy_country_and_session():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["proxyCountry"] == "UnitedKingdom"
        assert body["session"] == "s2"
        return httpx.Response(200, json={"solution": {"screenshot": "img"}, "data": "success"})

    provider = ScrappeyProvider("k", make_client(handler))
    assert await provider.screenshot(
        "https://example.com", proxy_country="UnitedKingdom", session="s2"
    ) == "img"


async def test_exa_search_omits_type_when_falsy():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert "type" not in body  # search_type="" -> omitted, like the default check
        return httpx.Response(200, json={"results": []})

    provider = ExaProvider("k", make_client(handler))
    assert await provider.search("q", search_type="") == []


# --- malformed 200 responses -------------------------------------------
# A provider answering 200 with the wrong JSON shape must raise ProviderError
# (so the router fails over), never AttributeError/TypeError/KeyError/
# JSONDecodeError. These shapes are what real APIs emit during partial
# outages, deprecations, and interstitial challenge pages.


async def test_request_json_non_json_body_raises_provider_error():
    provider = TavilyProvider(
        "k", make_client(lambda r: httpx.Response(200, text="<html>challenge</html>"))
    )
    with pytest.raises(ProviderError, match="non-JSON response"):
        await provider.search("q")


async def test_request_json_non_object_root_raises_provider_error():
    provider = TavilyProvider("k", make_client(lambda r: httpx.Response(200, json=[1, 2])))
    with pytest.raises(ProviderError, match="non-object JSON"):
        await provider.search("q")


async def test_list_field_absent_or_null_means_no_items():
    for payload in ({}, {"results": None}):
        provider = TavilyProvider(
            "k", make_client(lambda r, p=payload: httpx.Response(200, json=p))
        )
        assert await provider.search("q") == []


@pytest.mark.parametrize("bad", [{}, "x", 3, ["not-an-object"]])
async def test_list_field_malformed_shape_raises_provider_error(bad):
    provider = TavilyProvider(
        "k", make_client(lambda r: httpx.Response(200, json={"results": bad}))
    )
    with pytest.raises(ProviderError, match="malformed 'results'"):
        await provider.search("q")


async def test_text_field_non_string_raises_provider_error():
    provider = TavilyProvider(
        "k",
        make_client(lambda r: httpx.Response(200, json={"results": [{"content": {"a": 1}}]})),
    )
    with pytest.raises(ProviderError, match="non-string 'content'"):
        await provider.search("q")


async def test_exa_search_rejects_malformed_results():
    for payload in ({"results": {}}, {"results": [1]}):
        provider = ExaProvider(
            "k", make_client(lambda r, p=payload: httpx.Response(200, json=p))
        )
        with pytest.raises(ProviderError, match="malformed 'results'"):
            await provider.search("q")


async def test_firecrawl_fetch_rejects_null_data():
    provider = FirecrawlProvider("k", make_client(lambda r: httpx.Response(200, json={"data": None})))
    # null data is "empty object", not a crash: fetch yields empty content
    page = await provider.fetch("https://t")
    assert page.content == "" and page.url == "https://t"


async def test_firecrawl_fetch_rejects_non_object_data():
    provider = FirecrawlProvider("k", make_client(lambda r: httpx.Response(200, json={"data": [1]})))
    with pytest.raises(ProviderError, match="malformed 'data'"):
        await provider.fetch("https://t")


async def test_firecrawl_search_rejects_non_list_web():
    provider = FirecrawlProvider(
        "k", make_client(lambda r: httpx.Response(200, json={"data": {"web": 5}}))
    )
    with pytest.raises(ProviderError, match="malformed 'data'"):
        await provider.search("q")


async def test_firecrawl_crawl_rejects_non_object_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job1"})
        return httpx.Response(200, json=[1])


    provider = FirecrawlProvider("k", make_client(handler))
    with pytest.raises(ProviderError, match="non-object JSON"):
        await provider.crawl("https://t", poll_interval=0.01, timeout=1)


async def test_firecrawl_crawl_completed_null_data_returns_no_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job1"})
        return httpx.Response(200, json={"status": "completed", "data": None})


    provider = FirecrawlProvider("k", make_client(handler))
    assert await provider.crawl("https://t", poll_interval=0.01, timeout=1) == []


async def test_firecrawl_crawl_zero_poll_interval_still_times_out():
    # elapsed += poll_interval never advanced with a zero interval: the old
    # loop spun forever. The wall-clock deadline bounds it.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job1"})
        return httpx.Response(200, json={"status": "processing"})


    provider = FirecrawlProvider("k", make_client(handler))
    with pytest.raises(ProviderError, match="timed out"):
        await asyncio.wait_for(
            provider.crawl("https://t", poll_interval=0, timeout=0.02), timeout=5
        )


async def test_serper_rejects_malformed_organic():
    provider = SerperProvider("k", make_client(lambda r: httpx.Response(200, json={"organic": {}})))
    with pytest.raises(ProviderError, match="malformed 'organic'"):
        await provider.search("q")


async def test_tinyfish_fetch_rejects_non_object_results():
    provider = TinyFishProvider(
        "k", make_client(lambda r: httpx.Response(200, json={"results": ["x"]}))
    )
    with pytest.raises(ProviderError, match="malformed 'results'"):
        await provider.fetch("https://t")


async def test_scrappey_rejects_malformed_solution():
    for solution in (None, [], "s"):
        provider = ScrappeyProvider(
            "k",
            make_client(
                lambda r, s=solution: httpx.Response(200, json={"data": "success", "solution": s})
            ),
        )
        if solution is None:
            # absent solution is treated as an empty object: empty page
            page = await provider.fetch("https://t")
            assert page.content == ""
        else:
            with pytest.raises(ProviderError, match="malformed 'solution'"):
                await provider.fetch("https://t")


async def test_scrappey_screenshot_rejects_non_string_image():
    provider = ScrappeyProvider(
        "k",
        make_client(lambda r: httpx.Response(200, json={"data": "success", "solution": {"screenshot": {"a": 1}}})),
    )
    with pytest.raises(ProviderError, match="non-string 'screenshot'"):
        await provider.screenshot("https://t")


async def test_scrappey_session_null_solution_is_empty_object():
    provider = ScrappeyProvider(
        "k",
        make_client(lambda r: httpx.Response(200, json={"data": "success", "solution": None})),
    )
    assert await provider.session("create", "s1") == {"op": "create", "session": "s1", "solution": {}}


async def test_scrappey_actions_rejects_non_list_javascript_return():
    provider = ScrappeyProvider(
        "k",
        make_client(
            lambda r: httpx.Response(
                200,
                json={"data": "success", "solution": {"javascriptReturn": "nope"}},
            )
        ),
    )
    with pytest.raises(ProviderError, match="malformed 'javascriptReturn'"):
        await provider.browser_actions("https://t", actions=[{"type": "wait", "wait": 1}])


async def test_zenrows_extract_non_json_raises_provider_error():
    provider = ZenRowsProvider(
        "k", make_client(lambda r: httpx.Response(200, text="<html>blocked</html>"))
    )
    with pytest.raises(ProviderError, match="non-JSON response"):
        await provider.extract("https://t")
