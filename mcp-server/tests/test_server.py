"""End-to-end MCP server tests over an in-memory transport."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.client.client import InMemoryTransport
from mcp.client.session import ClientSession

from scrape_n_search.models import BrowserActionsResult, CrawlPage, FetchedPage, SearchResult
from scrape_n_search.providers.base import BaseProvider
from scrape_n_search.router import Router
from scrape_n_search.server import build_server
from scrape_n_search.errors import ProviderError


class FakeProvider(BaseProvider):
    name = "fake"
    capabilities = frozenset({"search", "fetch"})

    async def search(self, query: str, **kw) -> list[SearchResult]:
        return [SearchResult(title=query, url="https://fake", snippet="s")]

    async def fetch(self, url: str, **kw) -> FetchedPage:
        return FetchedPage(url=url, content="# Hello", title="T")


@asynccontextmanager
async def client_for(router: Router, **server_kwargs):
    server = build_server(router, **server_kwargs)
    async with InMemoryTransport(server) as (client_read, client_write):
        async with ClientSession(client_read, client_write) as session:
            await session.initialize()
            yield session


async def test_lists_all_tools():
    async with client_for(Router([FakeProvider()])) as session:
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {
            "web_search",
            "web_fetch",
            "web_crawl",
            "web_extract",
            "web_browser_actions",
            "web_screenshot",
            "web_session",
        }
        schemas = {t.name: t.input_schema for t in tools.tools}
        # provider is an optional param on every tool
        assert "provider" in schemas["web_search"]["properties"]
        assert "query" in schemas["web_search"]["properties"]
        assert "required" in schemas["web_search"] and "query" in schemas["web_search"]["required"]
        # browser actions takes a list of action objects
        assert schemas["web_browser_actions"]["properties"]["actions"]["type"] == "array"


async def test_web_search_returns_normalized_json():
    async with client_for(Router([FakeProvider()])) as session:
        res = await session.call_tool("web_search", {"query": "hello", "max_results": 3})
        payload = json.loads(res.content[0].text)
        assert payload["provider"] == "fake"
        assert payload["results"][0]["title"] == "hello"
        assert payload["results"][0]["url"] == "https://fake"


async def test_web_fetch_returns_normalized_json():
    async with client_for(Router([FakeProvider()])) as session:
        res = await session.call_tool("web_fetch", {"url": "https://example.com"})
        payload = json.loads(res.content[0].text)
        assert payload["provider"] == "fake"
        assert payload["content"] == "# Hello"
        assert payload["url"] == "https://example.com"


async def test_unsupported_tool_returns_error_json():
    async with client_for(Router([FakeProvider()])) as session:
        res = await session.call_tool("web_crawl", {"url": "https://example.com"})
        payload = json.loads(res.content[0].text)
        assert "error" in payload
        assert "no configured provider" in payload["error"]


async def test_no_provider_returns_error_json():
    async with client_for(Router([])) as session:
        res = await session.call_tool("web_search", {"query": "hello"})
        payload = json.loads(res.content[0].text)
        assert "error" in payload


class FetchOnlyProvider(BaseProvider):
    name = "scrappey"
    capabilities = frozenset({"fetch"})

    def __init__(self, enabled: bool = True):
        super().__init__()
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def fetch(self, url: str, **kw) -> FetchedPage:
        return FetchedPage(url=url, content="# Hello", title="T")


class OtherFetchProvider(FetchOnlyProvider):
    name = "other"


async def _list_names(router, **kwargs):
    server = build_server(router, **kwargs)
    async with InMemoryTransport(server) as (client_read, client_write):
        async with ClientSession(client_read, client_write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return {t.name for t in tools.tools}


async def test_default_provider_hides_unsupported_tools():
    names = await _list_names(Router([FetchOnlyProvider()]), default_provider="scrappey")
    # Scrappey serves fetch natively; search, crawl, and extract stay visible
    # because an enabled Scrappey can emulate them (gated behind the dual-run
    # trigger: search/crawl by parsing, extract via fetch + LLM summary).
    assert names == {"web_fetch", "web_search", "web_crawl", "web_extract"}


async def test_default_provider_used_when_omitted():
    router = Router([FetchOnlyProvider()])
    async with client_for(router, default_provider="scrappey") as session:
        res = await session.call_tool("web_fetch", {"url": "https://example.com"})
        payload = json.loads(res.content[0].text)
        assert payload["provider"] == "scrappey"


async def test_explicit_provider_overrides_default():
    router = Router([FetchOnlyProvider(), OtherFetchProvider()])
    async with client_for(router, default_provider="scrappey") as session:
        res = await session.call_tool("web_fetch", {"url": "https://example.com", "provider": "other"})
        payload = json.loads(res.content[0].text)
        assert payload["provider"] == "other"


async def test_unknown_default_provider_raises():
    with pytest.raises(ValueError):
        build_server(Router([FetchOnlyProvider()]), default_provider="nope")


async def test_unconfigured_default_provider_raises():
    router = Router([FetchOnlyProvider(enabled=False)])
    with pytest.raises(ValueError):
        build_server(router, default_provider="scrappey")


async def test_enabled_tools_allow_list():
    names = await _list_names(Router([FetchOnlyProvider()]), enabled_tools={"web_fetch", "web_search"})
    assert names == {"web_fetch", "web_search"}


class BrowserProvider(BaseProvider):
    name = "scrappey"
    capabilities = frozenset({"fetch", "browser_actions", "screenshot", "session"})

    async def fetch(self, url: str, **kw) -> FetchedPage:
        return FetchedPage(url=url, content="# Hello", title="T")

    async def browser_actions(self, url: str, actions: list, **kw):
        return BrowserActionsResult(url=url, content="# After actions", javascript_return=["clicked"])

    async def screenshot(self, url: str, **kw) -> str:
        return "aGVsbG8="

    async def session(self, op: str, session: str, **kw):
        return {"op": op, "session": session, "solution": {"session": session}}


async def test_default_provider_exposes_new_tools():
    names = await _list_names(Router([BrowserProvider()]), default_provider="scrappey")
    # Emulated search/crawl/extract join Scrappey's native capabilities.
    assert names == {
        "web_fetch",
        "web_browser_actions",
        "web_screenshot",
        "web_session",
        "web_search",
        "web_crawl",
        "web_extract",
    }


async def test_browser_actions_tool_call():
    async with client_for(Router([BrowserProvider()]), default_provider="scrappey") as session:
        res = await session.call_tool(
            "web_browser_actions",
            {"url": "https://example.com", "actions": [{"type": "execute_js", "code": "1"}]},
        )
        payload = json.loads(res.content[0].text)
        assert payload["provider"] == "scrappey"
        assert payload["content"] == "# After actions"
        assert payload["javascript_return"] == ["clicked"]


async def test_screenshot_tool_call():
    async with client_for(Router([BrowserProvider()]), default_provider="scrappey") as session:
        res = await session.call_tool("web_screenshot", {"url": "https://example.com", "full_page": True})
        payload = json.loads(res.content[0].text)
        assert payload["provider"] == "scrappey"
        assert payload["screenshot_base64"] == "aGVsbG8="
        assert payload["format"] == "png"


async def test_playbook_resource_listed_and_readable():
    async with client_for(Router([BrowserProvider()]), default_provider="scrappey") as session:
        resources = await session.list_resources()
        uris = {r.uri for r in resources.resources}
        assert "scrappey://playbook" in uris
        result = await session.read_resource("scrappey://playbook")
        text = result.contents[0].text
        assert "AliExpress" in text
        assert "old.reddit" in text


class RecordingProvider(BaseProvider):
    """Scrappey-shaped provider that records the kwargs each call received."""

    name = "scrappey"
    capabilities = frozenset({"fetch", "browser_actions", "screenshot", "session"})

    def __init__(self):
        super().__init__()
        self.calls: dict[str, dict] = {}

    async def fetch(self, url: str, **kw) -> FetchedPage:
        self.calls["fetch"] = kw
        return FetchedPage(url=url, content="# Hello", title="T")

    async def browser_actions(self, url: str, actions: list, **kw):
        self.calls["browser_actions"] = kw
        return BrowserActionsResult(url=url, content="# After actions")

    async def screenshot(self, url: str, **kw) -> str:
        self.calls["screenshot"] = kw
        return "aGVsbG8="

    async def session(self, op: str, session: str, **kw):
        self.calls["session"] = {"op": op, "session": session}
        return {"op": op, "session": session, "solution": {"session": session}}


async def test_proxy_country_passed_to_fetch():
    provider = RecordingProvider()
    async with client_for(Router([provider])) as session:
        res = await session.call_tool(
            "web_fetch",
            {"url": "https://example.com", "proxy_country": "UnitedStates"},
        )
        payload = json.loads(res.content[0].text)
        assert payload["provider"] == "scrappey"
        assert provider.calls["fetch"]["proxy_country"] == "UnitedStates"


async def test_proxy_country_passed_to_actions_and_screenshot():
    provider = RecordingProvider()
    async with client_for(Router([provider])) as session:
        await session.call_tool(
            "web_browser_actions",
            {"url": "https://example.com", "actions": [{"type": "wait", "wait": 1}], "proxy_country": "UnitedStates"},
        )
        await session.call_tool("web_screenshot", {"url": "https://example.com", "proxy_country": "UnitedKingdom"})
        assert provider.calls["browser_actions"]["proxy_country"] == "UnitedStates"
        assert provider.calls["screenshot"]["proxy_country"] == "UnitedKingdom"


async def test_proxy_country_in_tool_schemas():
    async with client_for(Router([RecordingProvider()])) as session:
        tools = await session.list_tools()
        schemas = {t.name: t.input_schema for t in tools.tools}
        for tool in ("web_fetch", "web_browser_actions", "web_screenshot"):
            assert "proxy_country" in schemas[tool]["properties"]


async def test_format_is_enum_in_schemas():
    async with client_for(Router([RecordingProvider()])) as session:
        tools = await session.list_tools()
        schemas = {t.name: t.input_schema for t in tools.tools}
        for tool in ("web_fetch", "web_browser_actions"):
            assert schemas[tool]["properties"]["format"]["enum"] == ["markdown", "html"]
            assert schemas[tool]["properties"]["format"]["default"] == "markdown"


async def test_invalid_format_rejected():
    async with client_for(Router([RecordingProvider()])) as session:
        res = await session.call_tool("web_fetch", {"url": "https://x", "format": "pdf"})
        assert "Input should be 'markdown' or 'html'" in res.content[0].text


async def test_max_results_bounds_rejected():
    async with client_for(Router([FakeProvider()])) as session:
        for bad in (0, 51):
            res = await session.call_tool("web_search", {"query": "q", "max_results": bad})
            payload = json.loads(res.content[0].text)
            assert "max_results must be between 1 and 50" in payload["error"]


async def test_crawl_bounds_rejected():
    async with client_for(Router([FakeProvider()])) as session:
        res = await session.call_tool("web_crawl", {"url": "https://x", "max_pages": 101})
        assert "max_pages must be between 1 and 100" in json.loads(res.content[0].text)["error"]
        res = await session.call_tool("web_crawl", {"url": "https://x", "max_depth": 0})
        assert "max_depth must be between 1 and 10" in json.loads(res.content[0].text)["error"]


async def test_empty_actions_rejected():
    async with client_for(Router([RecordingProvider()])) as session:
        res = await session.call_tool("web_browser_actions", {"url": "https://x", "actions": []})
        payload = json.loads(res.content[0].text)
        assert "non-empty list" in payload["error"]


async def test_web_session_create_and_destroy():
    provider = RecordingProvider()
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_session", {"op": "create", "session": "s1"})
        payload = json.loads(res.content[0].text)
        assert payload["provider"] == "scrappey"
        assert payload["op"] == "create"
        assert payload["session"] == "s1"
        res = await session.call_tool("web_session", {"op": "destroy", "session": "s1"})
        assert json.loads(res.content[0].text)["op"] == "destroy"
        assert provider.calls["session"] == {"op": "destroy", "session": "s1"}


async def test_web_session_empty_name_rejected():
    async with client_for(Router([RecordingProvider()])) as session:
        res = await session.call_tool("web_session", {"op": "create", "session": ""})
        payload = json.loads(res.content[0].text)
        assert "session name is required" in payload["error"]


async def test_session_passed_to_fetch_actions_screenshot():
    provider = RecordingProvider()
    async with client_for(Router([provider])) as session:
        await session.call_tool("web_fetch", {"url": "https://x", "session": "s1"})
        await session.call_tool(
            "web_browser_actions", {"url": "https://x", "actions": [{"type": "wait", "wait": 1}], "session": "s1"}
        )
        await session.call_tool("web_screenshot", {"url": "https://x", "session": "s1"})
        assert provider.calls["fetch"]["session"] == "s1"
        assert provider.calls["browser_actions"]["session"] == "s1"
        assert provider.calls["screenshot"]["session"] == "s1"


async def test_web_session_in_schemas():
    async with client_for(Router([RecordingProvider()])) as session:
        tools = await session.list_tools()
        schemas = {t.name: t.input_schema for t in tools.tools}
        assert schemas["web_session"]["properties"]["op"]["enum"] == ["create", "destroy"]
        assert "session" in schemas["web_session"]["required"]
        for tool in ("web_fetch", "web_browser_actions", "web_screenshot"):
            assert "session" in schemas[tool]["properties"]


class GateProvider(BaseProvider):
    """Scrappey-shaped provider that records every basic-mode fetch."""

    name = "scrappey"
    capabilities = frozenset({"fetch"})

    def __init__(self, page_html: str | None = None):
        super().__init__()
        self.page_html = page_html or "<html><body></body></html>"
        self.fetch_calls: list[tuple[str, dict]] = []

    async def fetch(self, url: str, **kw) -> FetchedPage:
        self.fetch_calls.append((url, dict(kw)))
        return FetchedPage(url=url, content=self.page_html, title=None)


_BING_HTML = """
<ol id="b_results">
  <li class="b_algo"><h2><a href="https://example.com/a">Alpha</a></h2>
    <p>First snippet.</p></li>
  <li class="b_algo"><h2><a href="https://example.com/b">Beta</a></h2>
    <p>Second snippet.</p></li>
</ol>
"""


async def test_emulated_search_first_call_explains_without_fetching():
    provider = GateProvider()
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_search", {"query": "pizza", "max_results": 5})
    payload = json.loads(res.content[0].text)
    assert payload["emulation"]["executed"] is False
    assert payload["emulation"]["provider"] == "scrappey"
    assert payload["emulation"]["tool"] == "web_search"
    assert '"force": true' in payload["emulation"]["trigger"]
    assert provider.fetch_calls == []  # explained, not executed


async def test_emulated_search_force_runs():
    provider = GateProvider(page_html=_BING_HTML)
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_search", {"query": "pizza", "max_results": 5, "force": True})
    payload = json.loads(res.content[0].text)
    assert payload["provider"] == "scrappey"
    assert [r["title"] for r in payload["results"]] == ["Alpha", "Beta"]
    assert [r["url"] for r in payload["results"]] == ["https://example.com/a", "https://example.com/b"]
    (url, kw) = provider.fetch_calls[0]
    assert url == "https://www.bing.com/search?q=pizza"
    assert kw["js_render"] is True  # full browser mode (basic mode serves stale SERPs)


async def test_emulated_crawl_first_call_explains_without_fetching():
    provider = GateProvider()
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_crawl", {"url": "https://example.com"})
    payload = json.loads(res.content[0].text)
    assert payload["emulation"]["executed"] is False
    assert payload["emulation"]["tool"] == "web_crawl"
    assert '"force": true' in payload["emulation"]["trigger"]
    assert provider.fetch_calls == []


async def test_emulated_crawl_force_runs():
    page = """
    <html><head><title>Home</title></head><body>
      <a href="/about">About</a>
    </body></html>
    """
    provider = GateProvider(page_html=page)
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool(
            "web_crawl", {"url": "https://example.com", "max_pages": 5, "max_depth": 2, "force": True}
        )
    payload = json.loads(res.content[0].text)
    assert payload["provider"] == "scrappey"
    assert [p["url"] for p in payload["pages"]] == ["https://example.com", "https://example.com/about"]
    assert payload["pages"][0]["title"] == "Home"
    (url, kw) = provider.fetch_calls[0]
    assert url == "https://example.com"
    assert kw["js_render"] is False
    # the child link was followed with another basic-mode request
    assert provider.fetch_calls[1][0] == "https://example.com/about"


async def test_emulated_gate_needs_scrappey():
    # No Scrappey -> the original router error, not an explanation.
    async with client_for(Router([])) as session:
        res = await session.call_tool("web_search", {"query": "x"})
        payload = json.loads(res.content[0].text)
        assert "error" in payload and "emulation" not in payload


async def test_emulated_extract_first_call_explains_without_fetching(monkeypatch):
    provider = GateProvider()
    async def fake_summarize(text, *, instruction=None):
        raise AssertionError("must not call the LLM before force=true")
    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_extract", {"url": "https://example.com", "prompt": "prices"})
    payload = json.loads(res.content[0].text)
    assert payload["emulation"]["executed"] is False
    assert payload["emulation"]["provider"] == "scrappey"
    assert payload["emulation"]["tool"] == "web_extract"
    assert '"force": true' in payload["emulation"]["trigger"]
    assert provider.fetch_calls == []  # explained, not executed


async def test_emulated_extract_force_runs(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = GateProvider(page_html="<html><body><h1>Product</h1><p>Price: 42</p></body></html>")
    calls = []

    async def fake_summarize(text, *, instruction=None):
        calls.append((text, instruction))
        return "# Product\n\n- price: 42"

    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool(
            "web_extract", {"url": "https://example.com", "prompt": "price", "force": True}
        )
    payload = json.loads(res.content[0].text)
    assert payload["provider"] == "scrappey"
    assert payload["url"] == "https://example.com"
    assert payload["format"] == "markdown"
    assert payload["data"] == "# Product\n\n- price: 42"
    (url, kw) = provider.fetch_calls[0]
    assert url == "https://example.com"
    assert kw["js_render"] is False  # cheap basic mode by default
    assert kw["format"] == "html"
    (text, instruction) = calls[0]
    assert "<h1>" not in text and "Price: 42" in text  # HTML stripped before the LLM
    assert instruction == "price"


async def test_emulated_extract_force_js_render_uses_browser_mode(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = GateProvider(page_html="# rendered")
    async def fake_summarize(text, *, instruction=None):
        return "# Out"
    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool(
            "web_extract", {"url": "https://example.com", "js_render": True, "force": True}
        )
    payload = json.loads(res.content[0].text)
    assert payload["data"] == "# Out"
    (url, kw) = provider.fetch_calls[0]
    assert kw["js_render"] is True
    assert kw["format"] == "markdown"


async def test_emulated_extract_force_without_llm_key_errors(monkeypatch):
    provider = GateProvider(page_html="<p>x</p>")
    async def fake_summarize(text, *, instruction=None):
        raise ProviderError("no LLM key available for emulated web_extract: set OPENROUTER_API_KEY or HF_TOKEN (in .env or in the harness mcp.json env block for the server)")
    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_extract", {"url": "https://example.com", "force": True})
    payload = json.loads(res.content[0].text)
    assert "emulated extract via scrappey failed" in payload["error"]
    assert "OPENROUTER_API_KEY" in payload["error"]


async def test_emulated_extract_js_render_in_schema_only_without_force():
    async with client_for(Router([FakeProvider()])) as session:
        tools = await session.list_tools()
        schemas = {t.name: t.input_schema for t in tools.tools}
    props = schemas["web_extract"]["properties"]
    assert "js_render" in props
    assert "force" not in props


async def test_force_flag_absent_from_all_tool_schemas():
    async with client_for(Router([FakeProvider()])) as session:
        tools = await session.list_tools()
        schemas = {t.name: t.input_schema for t in tools.tools}
    for schema in schemas.values():
        assert "force" not in schema["properties"]


async def test_emulated_gate_needs_enabled_scrappey():
    # Scrappey present but disabled -> the original router error, not a gate
    provider = FetchOnlyProvider(enabled=False)
    async with client_for(Router([provider])) as session:
        res = await session.call_tool("web_search", {"query": "x"})
    payload = json.loads(res.content[0].text)
    assert "error" in payload and "emulation" not in payload
    # and the tool is not even listed under default_provider=scrappey
    with pytest.raises(ValueError):
        build_server(Router([provider]), default_provider="scrappey")


async def test_unknown_provider_param_returns_error_json():
    async with client_for(Router([FakeProvider()])) as session:
        res = await session.call_tool("web_fetch", {"url": "https://x", "provider": "bogus"})
    payload = json.loads(res.content[0].text)
    assert "unknown provider 'bogus'" in payload["error"]


class FailingGateProvider(GateProvider):
    async def fetch(self, url: str, **kw) -> FetchedPage:
        self.fetch_calls.append((url, dict(kw)))
        raise ProviderError("page fetch exploded")


async def test_emulated_search_force_wraps_provider_error(monkeypatch):
    provider = FailingGateProvider()
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_search", {"query": "x", "force": True})
    payload = json.loads(res.content[0].text)
    assert "emulated search via scrappey failed" in payload["error"]
    assert "page fetch exploded" in payload["error"]
    assert len(provider.fetch_calls) == 1


async def test_emulated_crawl_force_wraps_provider_error():
    provider = FailingGateProvider()
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_crawl", {"url": "https://example.com", "force": True})
    payload = json.loads(res.content[0].text)
    assert "emulated crawl via scrappey failed" in payload["error"]


async def test_web_extract_real_provider_wins_before_emulation():
    class ExtractProvider(BaseProvider):
        name = "ex"
        capabilities = frozenset({"extract"})

        async def extract(self, url: str, *, prompt: str | None = None, **kw):
            return {"title": "Extracted"}

    router = Router([ExtractProvider(), GateProvider()])
    async with client_for(router, default_provider="scrappey") as session:
        res = await session.call_tool("web_extract", {"url": "https://example.com"})
    payload = json.loads(res.content[0].text)
    assert payload["provider"] == "ex"
    assert payload["data"] == {"title": "Extracted"}


async def test_default_provider_defers_to_real_search_provider():
    # default_provider=scrappey cannot serve search natively, so a configured
    # real search provider must win over the emulation gate
    router = Router([FakeProvider(), GateProvider()])
    async with client_for(router, default_provider="scrappey") as session:
        res = await session.call_tool("web_search", {"query": "hello"})
    payload = json.loads(res.content[0].text)
    assert payload["provider"] == "fake"
    assert payload["results"][0]["title"] == "hello"
    assert "emulation" not in payload


async def test_default_provider_defers_to_real_crawl_provider():
    class CrawlProvider(BaseProvider):
        name = "cr"
        capabilities = frozenset({"crawl"})

        async def crawl(self, url: str, **kw):
            return [CrawlPage(url=url, title="T", content="# c")]

    router = Router([CrawlProvider(), GateProvider()])
    async with client_for(router, default_provider="scrappey") as session:
        res = await session.call_tool("web_crawl", {"url": "https://example.com"})
    payload = json.loads(res.content[0].text)
    assert payload["provider"] == "cr"
    assert payload["pages"][0]["title"] == "T"
    assert "emulation" not in payload


async def test_default_provider_still_pins_fetch():
    # the default provider keeps serving capabilities it owns
    router = Router([FakeProvider(), GateProvider()])
    async with client_for(router, default_provider="scrappey") as session:
        res = await session.call_tool("web_fetch", {"url": "https://x"})
    payload = json.loads(res.content[0].text)
    assert payload["provider"] == "scrappey"


def test_load_playbook_falls_back_when_package_missing(monkeypatch):
    from scrape_n_search import server as server_mod

    def broken_files(_pkg):
        raise OSError("no package data")

    monkeypatch.setattr(server_mod.resources, "files", broken_files)
    assert server_mod._load_playbook() == server_mod._PLAYBOOK_FALLBACK


def test_load_playbook_reads_package():
    from scrape_n_search import server as server_mod

    text = server_mod._load_playbook()
    assert "AliExpress" in text
    assert "old.reddit" in text


async def test_unknown_provider_with_scrappey_returns_error_not_gate():
    # a typo'd provider name must surface the real error, not an emulation
    # explanation claiming no provider is configured
    provider = GateProvider()
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_search", {"query": "x", "provider": "bogus"})
    payload = json.loads(res.content[0].text)
    assert "unknown provider 'bogus'" in payload["error"]
    assert "emulation" not in payload
    assert provider.fetch_calls == []


async def test_all_providers_failed_during_failover_fires_gate():
    # every configured candidate failing (e.g. keyless Jina always failing
    # search) is an environment condition -> gate, not a bare error
    class FailingSearchProvider(BaseProvider):
        name = "s1"
        capabilities = frozenset({"search"})

        async def search(self, query: str, **kw):
            raise ProviderError("search exploded")

    class FailingSearchProvider2(FailingSearchProvider):
        name = "s2"

    router = Router([FailingSearchProvider(), FailingSearchProvider2(), GateProvider()])
    async with client_for(router) as session:
        res = await session.call_tool("web_search", {"query": "x"})
    payload = json.loads(res.content[0].text)
    assert payload["emulation"]["executed"] is False
    assert payload["emulation"]["tool"] == "web_search"


async def test_disabled_preferred_provider_surfaces_error():
    # a caller pinning a disabled provider gets the real error, not a gate
    provider = GateProvider()
    disabled = FetchOnlyProvider(enabled=False)
    disabled.name = "other"
    async with client_for(Router([provider, disabled]), default_provider="scrappey") as session:
        res = await session.call_tool("web_fetch", {"url": "https://x", "provider": "other"})
    payload = json.loads(res.content[0].text)
    assert "not configured" in payload["error"]


async def test_gate_fires_with_scrappey_enabled_but_no_default_provider():
    provider = GateProvider()
    async with client_for(Router([provider])) as session:
        res = await session.call_tool("web_search", {"query": "x"})
    payload = json.loads(res.content[0].text)
    assert payload["emulation"]["executed"] is False
    assert provider.fetch_calls == []


async def test_web_session_invalid_op_rejected():
    async with client_for(Router([RecordingProvider()])) as session:
        res = await session.call_tool("web_session", {"op": "pause", "session": "s1"})
    assert "Input should be 'create' or 'destroy'" in res.content[0].text


async def test_schema_defaults_and_optional_params():
    async with client_for(Router([RecordingProvider()])) as session:
        tools = await session.list_tools()
        schemas = {t.name: t.input_schema for t in tools.tools}
    assert schemas["web_extract"]["properties"]["js_render"]["default"] is False
    assert schemas["web_fetch"]["properties"]["format"]["default"] == "markdown"
    assert schemas["web_browser_actions"]["properties"]["format"]["default"] == "markdown"
    search = schemas["web_search"]["properties"]
    assert "freshness" in search and "country" in search and "force" not in search
    assert schemas["web_search"]["required"] == ["query"]  # defaults are optional
    assert schemas["web_fetch"]["required"] == ["url"]


async def test_no_provider_error_for_native_tools():
    # browser_actions / screenshot / session are not emulated: no provider ->
    # clean error json, no gate
    async with client_for(Router([])) as session:
        for tool, args in [
            ("web_browser_actions", {"url": "https://x", "actions": [{"type": "wait", "wait": 1}]}),
            ("web_screenshot", {"url": "https://x"}),
            ("web_session", {"op": "create", "session": "s1"}),
        ]:
            res = await session.call_tool(tool, args)
            payload = json.loads(res.content[0].text)
            assert "error" in payload
            assert "emulation" not in payload


async def test_web_crawl_unknown_provider_surfaces_error():
    provider = GateProvider()
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_crawl", {"url": "https://x", "provider": "bogus"})
    payload = json.loads(res.content[0].text)
    assert "unknown provider 'bogus'" in payload["error"]
    assert "emulation" not in payload
    assert provider.fetch_calls == []


async def test_web_extract_unknown_provider_surfaces_error():
    provider = GateProvider()
    async with client_for(Router([provider]), default_provider="scrappey") as session:
        res = await session.call_tool("web_extract", {"url": "https://x", "provider": "bogus"})
    payload = json.loads(res.content[0].text)
    assert "unknown provider 'bogus'" in payload["error"]
    assert "emulation" not in payload
    assert provider.fetch_calls == []


def test_hide_force_flags_strips_from_properties_and_required():
    from types import SimpleNamespace

    from scrape_n_search.server import _hide_force_flags

    class FakeTool:
        def __init__(self, parameters):
            self.parameters = parameters

    class FakeManager:
        def __init__(self, tools):
            self._tools = tools

        def get_tool(self, name):
            return self._tools.get(name)

    params = {"properties": {"force": {"type": "boolean"}, "query": {"type": "string"}}, "required": ["force", "query"]}
    tool = FakeTool(params)
    server = SimpleNamespace(_tool_manager=FakeManager({"web_search": tool, "web_crawl": None}))
    _hide_force_flags(server)
    assert "force" not in tool.parameters["properties"]
    assert tool.parameters["required"] == ["query"]
    # absent tools are skipped without error


def test_hide_force_flags_tolerates_missing_tool_manager():
    from types import SimpleNamespace

    from scrape_n_search.server import _hide_force_flags

    class NoManager:
        pass

    server = SimpleNamespace(_tool_manager=NoManager())  # no get_tool attr
    _hide_force_flags(server)  # must not raise; falls back to keeping the flag


async def test_disabled_scrappey_with_other_default_provider():
    # scrappey present but disabled: the emulated-tools union is skipped
    disabled = FetchOnlyProvider(enabled=False)
    disabled.name = "scrappey"
    names = await _list_names(Router([FakeProvider(), disabled]), default_provider="fake")
    assert names == {"web_search", "web_fetch"}  # only fake's capabilities


async def test_enabled_tools_excluding_fetch_skips_its_block():
    names = await _list_names(Router([FetchOnlyProvider()]), enabled_tools={"web_search"})
    assert names == {"web_search"}


def test_hide_force_flags_tolerates_non_dict_properties():
    from types import SimpleNamespace

    from scrape_n_search.server import _hide_force_flags

    class FakeTool:
        def __init__(self, parameters):
            self.parameters = parameters

    class FakeManager:
        def get_tool(self, name):
            return FakeTool({"properties": None, "required": None})

    server = SimpleNamespace(_tool_manager=FakeManager())
    _hide_force_flags(server)  # must not raise on malformed schema shapes


# --- inclusive bounds and default argument plumbing ---------------------


class KwargProvider(BaseProvider):
    """Records the exact kwargs each unified tool forwards to the provider."""

    name = "kwarg"
    capabilities = frozenset({"search", "fetch", "crawl"})

    def __init__(self):
        super().__init__()
        self.calls: dict[str, dict] = {}

    async def search(self, query: str, **kw):
        self.calls["search"] = {"query": query, **kw}
        return [SearchResult(title=query, url="https://kwarg")]

    async def fetch(self, url: str, **kw):
        self.calls["fetch"] = {"url": url, **kw}
        return FetchedPage(url=url, content="c")

    async def crawl(self, url: str, **kw):
        self.calls["crawl"] = {"url": url, **kw}
        return [CrawlPage(url=url)]


@pytest.mark.parametrize("max_results", [1, 50])
async def test_web_search_accepts_inclusive_max_results_bounds(max_results):
    provider = KwargProvider()
    async with client_for(Router([provider])) as session:
        res = await session.call_tool("web_search", {"query": "q", "max_results": max_results})
        assert "error" not in json.loads(res.content[0].text)
    assert provider.calls["search"]["max_results"] == max_results


@pytest.mark.parametrize("max_pages,max_depth", [(1, 1), (100, 10)])
async def test_web_crawl_accepts_inclusive_bounds(max_pages, max_depth):
    provider = KwargProvider()
    async with client_for(Router([provider])) as session:
        res = await session.call_tool(
            "web_crawl", {"url": "https://x", "max_pages": max_pages, "max_depth": max_depth}
        )
        assert "error" not in json.loads(res.content[0].text)
    assert provider.calls["crawl"] == {
        "url": "https://x",
        "max_pages": max_pages,
        "max_depth": max_depth,
    }


async def test_web_fetch_forwards_documented_defaults():
    provider = KwargProvider()
    async with client_for(Router([provider])) as session:
        await session.call_tool("web_fetch", {"url": "https://x"})
    assert provider.calls["fetch"] == {
        "url": "https://x",
        "format": "markdown",
        "js_render": False,
        "premium_proxy": False,
        "proxy_country": None,
        "session": None,
    }


async def test_web_fetch_forwards_explicit_flags():
    provider = KwargProvider()
    async with client_for(Router([provider])) as session:
        await session.call_tool(
            "web_fetch",
            {"url": "https://x", "format": "html", "js_render": True, "premium_proxy": True},
        )
    assert provider.calls["fetch"]["format"] == "html"
    assert provider.calls["fetch"]["js_render"] is True
    assert provider.calls["fetch"]["premium_proxy"] is True


async def test_enabled_tools_empty_set_exposes_no_tools():
    names = await _list_names(Router([FakeProvider()]), enabled_tools=set())
    assert names == set()


async def test_enabled_tools_unknown_names_ignored():
    names = await _list_names(Router([FakeProvider()]), enabled_tools={"web_fetch", "bogus"})
    assert names == {"web_fetch"}


# --- protocol-level surfaces --------------------------------------------


async def test_unknown_tool_is_a_protocol_error():
    async with client_for(Router([FakeProvider()])) as session:
        res = await session.call_tool("does_not_exist", {})
    assert res.is_error
    assert "Unknown tool" in res.content[0].text


async def test_missing_required_argument_is_a_validation_error():
    async with client_for(Router([FakeProvider()])) as session:
        res = await session.call_tool("web_search", {})
    assert res.is_error
    assert "validation error" in res.content[0].text.lower()


async def test_wrong_argument_type_is_a_validation_error():
    async with client_for(Router([FakeProvider()])) as session:
        res = await session.call_tool("web_search", {"query": 5})
    assert res.is_error
    assert "validation error" in res.content[0].text.lower()


async def test_unknown_resource_uri_is_a_protocol_error():
    from mcp.shared.exceptions import MCPError


    async with client_for(Router([FakeProvider()])) as session:
        with pytest.raises(MCPError, match="Unknown resource"):
            await session.read_resource("scrape-n-search://nope")


async def test_malformed_provider_surfaces_aggregated_error_json():
    # full chain: shape-invalid 200 -> ProviderError -> router aggregation ->
    # tool JSON error (never an unhandled crash through the MCP transport)
    from conftest import make_client

    from scrape_n_search.providers.tavily import TavilyProvider


    malformed = TavilyProvider(
        "k", make_client(lambda r: httpx.Response(200, json={"results": {}}))
    )
    async with client_for(Router([malformed])) as session:
        res = await session.call_tool("web_search", {"query": "q"})
    assert not res.is_error
    payload = json.loads(res.content[0].text)
    assert "all providers failed for 'search'" in payload["error"]
    assert "malformed 'results'" in payload["error"]
