"""End-to-end MCP server tests over an in-memory transport."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from mcp.client.client import InMemoryTransport
from mcp.client.session import ClientSession

from scrape_n_search.models import FetchedPage, SearchResult
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
async def client_for(router: Router):
    server = build_server(router)
    async with InMemoryTransport(server) as (client_read, client_write):
        async with ClientSession(client_read, client_write) as session:
            await session.initialize()
            yield session


async def test_lists_four_tools():
    async with client_for(Router([FakeProvider()])) as session:
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {"web_search", "web_fetch", "web_crawl", "web_extract"}
        schemas = {t.name: t.input_schema for t in tools.tools}
        # provider is an optional param on every tool
        assert "provider" in schemas["web_search"]["properties"]
        assert "query" in schemas["web_search"]["properties"]
        assert "required" in schemas["web_search"] and "query" in schemas["web_search"]["required"]


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
