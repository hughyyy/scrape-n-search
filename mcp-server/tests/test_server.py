"""End-to-end MCP server tests over an in-memory transport."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from mcp.client.client import InMemoryTransport
from mcp.client.session import ClientSession

from scrape_n_search.models import BrowserActionsResult, FetchedPage, SearchResult
from scrape_n_search.providers.base import BaseProvider
from scrape_n_search.router import Router
from scrape_n_search.server import build_server


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
    assert names == {"web_fetch"}


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
    assert names == {"web_fetch", "web_browser_actions", "web_screenshot", "web_session"}


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
