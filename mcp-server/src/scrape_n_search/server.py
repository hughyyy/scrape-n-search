"""MCP server exposing unified search/scrape tools over the provider router."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from . import __version__
from .errors import NoProviderAvailable
from .models import to_dict
from .router import Router

_TOOL_DESCRIPTIONS = {
    "web_search": (
        "Search the web and return ranked results (title, url, snippet, optional date/score). "
        "Provider is chosen automatically from the configured providers; set 'provider' to force "
        "one. Results are normalized, so the caller does not need to know which provider served them."
    ),
    "web_fetch": (
        "Fetch a URL and return its clean content (markdown by default). Runs server-side on the "
        "provider's infrastructure, so the caller's IP is never seen by the target site. Provider is "
        "chosen automatically; set 'provider' to force one."
    ),
    "web_crawl": (
        "Recursively crawl a domain and return discovered pages with their content. Provider is "
        "chosen automatically; set 'provider' to force one."
    ),
    "web_extract": (
        "Extract structured JSON data from a page. Provider is chosen automatically; set 'provider' "
        "to force one."
    ),
}


def build_server(router: Router) -> MCPServer:
    @asynccontextmanager
    async def _lifespan(_server: MCPServer):
        yield None
        # Close provider HTTP clients inside the server's event loop.
        await router.aclose()

    server = MCPServer(
        name="scrape-n-search",
        version=__version__,
        description="Unified web search and scraping tools backed by multiple providers.",
        lifespan=_lifespan,
    )

    @server.tool(name="web_search", description=_TOOL_DESCRIPTIONS["web_search"])
    async def web_search(
        query: str,
        max_results: int = 5,
        provider: str | None = None,
        freshness: str | None = None,
        country: str | None = None,
    ) -> str:
        try:
            name, results = await router.run(
                "search",
                preferred=provider,
                query=query,
                max_results=max_results,
                freshness=freshness,
                country=country,
            )
        except NoProviderAvailable as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"query": query, "provider": name, "results": to_dict(results)})

    @server.tool(name="web_fetch", description=_TOOL_DESCRIPTIONS["web_fetch"])
    async def web_fetch(
        url: str,
        format: str = "markdown",
        provider: str | None = None,
        js_render: bool = False,
        premium_proxy: bool = False,
    ) -> str:
        try:
            name, page = await router.run(
                "fetch",
                preferred=provider,
                url=url,
                format=format,
                js_render=js_render,
                premium_proxy=premium_proxy,
            )
        except NoProviderAvailable as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"provider": name, **to_dict(page)})

    @server.tool(name="web_crawl", description=_TOOL_DESCRIPTIONS["web_crawl"])
    async def web_crawl(
        url: str,
        max_pages: int = 10,
        max_depth: int = 2,
        provider: str | None = None,
    ) -> str:
        try:
            name, pages = await router.run(
                "crawl",
                preferred=provider,
                url=url,
                max_pages=max_pages,
                max_depth=max_depth,
            )
        except NoProviderAvailable as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"provider": name, "pages": to_dict(pages)})

    @server.tool(name="web_extract", description=_TOOL_DESCRIPTIONS["web_extract"])
    async def web_extract(
        url: str,
        prompt: str | None = None,
        provider: str | None = None,
    ) -> str:
        try:
            name, data = await router.run("extract", preferred=provider, url=url, prompt=prompt)
        except NoProviderAvailable as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"provider": name, "url": url, "data": data})

    return server
