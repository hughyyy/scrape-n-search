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
        assert names == {"web_search", "web_fetch", "web_crawl", "web_extract", "web_browser_actions", "web_screenshot"}
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
    capabilities = frozenset({"fetch", "browser_actions", "screenshot"})

    async def fetch(self, url: str, **kw) -> FetchedPage:
        return FetchedPage(url=url, content="# Hello", title="T")

    async def browser_actions(self, url: str, actions: list, **kw):
        return BrowserActionsResult(url=url, content="# After actions", javascript_return=["clicked"])

    async def screenshot(self, url: str, **kw) -> str:
        return "aGVsbG8="


async def test_default_provider_exposes_new_tools():
    names = await _list_names(Router([BrowserProvider()]), default_provider="scrappey")
    assert names == {"web_fetch", "web_browser_actions", "web_screenshot"}


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
