"""MCP server exposing unified search/scrape tools over the provider router."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from importlib import resources
from typing import Literal

from mcp.server.mcpserver import MCPServer

from . import __version__, llm
from .emulation import (
    crawl_explanation,
    emulated_crawl,
    emulated_extract,
    emulated_search,
    extract_explanation,
    search_explanation,
)
from .errors import NoProviderAvailable, ProviderError
from .models import to_dict
from .router import Router

logger = logging.getLogger(__name__)

# capability -> unified tool name
CAPABILITY_TO_TOOL = {
    "search": "web_search",
    "fetch": "web_fetch",
    "crawl": "web_crawl",
    "extract": "web_extract",
    "browser_actions": "web_browser_actions",
    "screenshot": "web_screenshot",
    "session": "web_session",
}

_TOOL_DESCRIPTIONS = {
    "web_search": (
        "Search the web and return ranked results (title, url, snippet, optional date/score). "
        "Provider is chosen automatically from the configured providers; set 'provider' to force "
        "one. Results are normalized, so the caller does not need to know which provider served them. "
        "When no real search provider is configured (only Scrappey), the call returns an "
        "explanation and a way to run an emulated search."
    ),
    "web_fetch": (
        "Fetch a URL and return its content (markdown by default). Runs server-side on the provider's "
        "infrastructure, so the caller's IP is never seen by the target site. "
        "js_render=false uses Scrappey basic mode (raw HTML, cheap); js_render=true uses full browser "
        "mode (JS rendering, markdown). proxy_country pins the Scrappey residential proxy region "
        "(e.g. 'UnitedStates') — otherwise the geo varies per request. session=<name> attaches the "
        "request to a Scrappey session managed via web_session (see its known-limitation note on "
        "cross-request state). Prefer old.reddit.com over www.reddit.com for Reddit."
    ),
    "web_crawl": (
        "Recursively crawl a domain and return discovered pages with their content. Provider is "
        "chosen automatically; set 'provider' to force one. Scrappey does not support crawling "
        "natively; when only Scrappey is configured the call returns an explanation and a way "
        "to run an emulated crawl."
    ),
    "web_extract": (
        "Extract structured data from a page. Provider is chosen automatically; set 'provider' "
        "to force one. When no real extract provider is configured (only Scrappey), the call "
        "returns an explanation and a way to run an emulated extract: one Scrappey fetch plus "
        "an LLM summary (harness key) returned as clean markdown. js_render applies to the "
        "Scrappey emulation (true = full browser mode for JS-heavy pages)."
    ),
    "web_browser_actions": (
        "Run a sequence of browser actions on a URL (full browser mode) and return the page content "
        "plus any execute_js results. 'actions' is a list of Scrappey action objects: click, type, "
        "wait, wait_for_selector, wait_for_function, execute_js, scroll, hover, keyboard, "
        "dropdown. proxy_country pins the residential proxy region (e.g. 'UnitedStates'). "
        "NOTE: execute_js results (javascript_return) are only returned when format='html'; "
        "the markdown filter suppresses them. See the scrappey://playbook resource for recipes (e.g. "
        "reading a full AliExpress item description requires scrolling + clicking 'View more' + extracting)."
    ),
    "web_screenshot": (
        "Capture a screenshot of a URL (full browser mode) and return it as base64 PNG. "
        "full_page=true for a full-page capture; selector captures a specific element. "
        "proxy_country pins the residential proxy region (e.g. 'UnitedStates')."
    ),
    "web_session": (
        "Manage Scrappey browser sessions: op='create' starts a session, op='destroy' shuts it "
        "down (also frees resources when you are done). Pass the session name as the 'session' "
        "parameter of web_fetch, web_browser_actions, or web_screenshot to attach requests to it. "
        "KNOWN LIMITATION (verified 2026-10): Scrappey currently destroys a named session once a "
        "request completes, so cookies/browser state do not persist across calls yet — the "
        "create/destroy lifecycle works, cross-request reuse does not."
    ),
}

# Guidance injected into the agent's context by the MCP client.
_GUIDANCE = (
    "Web access is provided by the scrape-n-search MCP server (tools: search, fetch, "
    "browser_actions, screenshot, crawl, extract). "
    "Prefer old.reddit.com over www.reddit.com (new-reddit search returns spam). "
    "AliExpress item descriptions are lazy-loaded and collapsed: use web_browser_actions to "
    "scroll, click 'View more', then extract. "
    "Read the full playbook from the resource scrappey://playbook."
)

_PLAYBOOK_FALLBACK = (
    "Scrappey browser-action playbook. Use web_browser_actions to craft a list of Scrappey "
    "actions on a page: scroll (trigger lazy-load), wait, click (cssSelector), execute_js (code), "
    "wait_for_selector, type, keyboard. Prefer old.reddit.com for Reddit. AliExpress descriptions "
    "are lazy-loaded: scroll + click 'View more' + extract. execute_js results come back in the "
    "response's javascript_return array."
)


def _load_playbook() -> str:
    try:
        return (resources.files("scrape_n_search") / "playbook.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _PLAYBOOK_FALLBACK


def build_server(
    router: Router,
    *,
    default_provider: str | None = None,
    enabled_tools: set[str] | None = None,
) -> MCPServer:
    """Build the MCP server.

    - ``default_provider``: provider used when the caller does not pass one.
      When set, tools that provider cannot serve are hidden from the client.
    - ``enabled_tools``: explicit allow-list of tool names; defaults to all.
    """
    if enabled_tools is None:
        enabled_tools = set(CAPABILITY_TO_TOOL.values())
    if default_provider:
        prov = router.get(default_provider)
        if prov is None:
            raise ValueError(f"DEFAULT_PROVIDER '{default_provider}' is not a known provider")
        if not prov.enabled:
            raise ValueError(
                f"DEFAULT_PROVIDER '{default_provider}' is not configured (missing API key)"
            )
        supported = {CAPABILITY_TO_TOOL[c] for c in prov.capabilities}
        # Scrappey has no search/crawl/extract endpoints, but an enabled Scrappey can
        # emulate all three (search + crawl through basic-mode fetch, extract through
        # fetch + an LLM summary — see emulation.py). Keep the tools visible so the
        # dual-run trigger can explain and gate them.
        scrappey = router.get("scrappey")
        if scrappey is not None and scrappey.enabled:
            supported |= {"web_search", "web_crawl", "web_extract"}
        enabled_tools = enabled_tools & supported

    @asynccontextmanager
    async def _lifespan(_server: MCPServer):
        yield None
        # Close provider HTTP clients inside the server's event loop.
        await router.aclose()
        await llm.aclose()

    server = MCPServer(
        name="scrape-n-search",
        version=__version__,
        description="Unified web search and scraping tools backed by multiple providers.",
        instructions=_GUIDANCE,
        lifespan=_lifespan,
    )

    @server.resource(
        "scrappey://playbook",
        name="Scrappey common-actions playbook",
        description="Browser-action recipes for common web tasks (AliExpress descriptions, old.reddit, lazy-loaded content).",
        mime_type="text/markdown",
    )
    async def _playbook_resource() -> str:
        return _load_playbook()

    def _preferred(provider: str | None, capability: str) -> str | None:
        """Resolve the effective preferred provider for a capability.

        An explicit ``provider`` argument pins that provider. The default
        provider is pinned only when it can actually serve the capability;
        otherwise the router auto-selects — so a configured real provider
        (e.g. Firecrawl for search) wins over Scrappey emulation, and the
        emulation gate only fires when nothing can serve the capability.
        """
        if provider:
            return provider
        if default_provider:
            prov = router.get(default_provider)
            if prov is not None and capability in prov.capabilities:
                return default_provider
        return None

    async def _dual_run(
        router: Router,
        *,
        capability: str,
        force: bool,
        run,
        unavailable_error: str,
        explanation: dict,
        extra: dict | None = None,
    ) -> str:
        """Dual-run trigger for capabilities Scrappey can only emulate.

        No real provider could serve the call. If Scrappey is enabled, the
        first call (no ``force``) returns an explanation instead of executing;
        a follow-up call with ``force=true`` runs the emulated path. Without
        Scrappey the original router error is returned unchanged.
        """
        scrappey = router.get("scrappey")
        if scrappey is None or not scrappey.enabled:
            return json.dumps({"error": unavailable_error})
        if not force:
            return json.dumps(explanation)
        try:
            result = await run(scrappey)
        except ProviderError as exc:
            return json.dumps({"error": f"emulated {capability} via scrappey failed: {exc}"})
        if capability == "search":
            payload = {"provider": "scrappey", "results": to_dict(result)}
        elif capability == "extract":
            payload = {
                "provider": "scrappey",
                "url": (extra or {}).get("url"),
                "format": "markdown",
                "data": to_dict(result),
            }
        else:
            payload = {"provider": "scrappey", "pages": to_dict(result)}
        payload.update(extra or {})
        return json.dumps(payload)

    if "web_search" in enabled_tools:

        @server.tool(name="web_search", description=_TOOL_DESCRIPTIONS["web_search"])
        async def web_search(
            query: str,
            max_results: int = 5,
            provider: str | None = None,
            freshness: str | None = None,
            country: str | None = None,
            force: bool = False,
        ) -> str:
            if not 1 <= max_results <= 50:
                return json.dumps({"error": "max_results must be between 1 and 50"})
            try:
                name, results = await router.run(
                    "search",
                    preferred=_preferred(provider, "search"),
                    query=query,
                    max_results=max_results,
                    freshness=freshness,
                    country=country,
                )
            except NoProviderAvailable as exc:
                return await _dual_run(
                    router,
                    capability="search",
                    force=force,
                    run=lambda p: emulated_search(p, query, max_results),
                    unavailable_error=str(exc),
                    explanation=search_explanation(
                        query, max_results, freshness=freshness, country=country
                    ),
                    extra={"query": query},
                )
            return json.dumps({"query": query, "provider": name, "results": to_dict(results)})

    if "web_fetch" in enabled_tools:

        @server.tool(name="web_fetch", description=_TOOL_DESCRIPTIONS["web_fetch"])
        async def web_fetch(
            url: str,
            format: Literal["markdown", "html"] = "markdown",
            provider: str | None = None,
            js_render: bool = False,
            premium_proxy: bool = False,
            proxy_country: str | None = None,
            session: str | None = None,
        ) -> str:
            try:
                name, page = await router.run(
                    "fetch",
                    preferred=_preferred(provider, "fetch"),
                    url=url,
                    format=format,
                    js_render=js_render,
                    premium_proxy=premium_proxy,
                    proxy_country=proxy_country,
                    session=session,
                )
            except NoProviderAvailable as exc:
                return json.dumps({"error": str(exc)})
            return json.dumps({"provider": name, **to_dict(page)})

    if "web_browser_actions" in enabled_tools:

        @server.tool(name="web_browser_actions", description=_TOOL_DESCRIPTIONS["web_browser_actions"])
        async def web_browser_actions(
            url: str,
            actions: list[dict],
            format: Literal["markdown", "html"] = "markdown",
            provider: str | None = None,
            proxy_country: str | None = None,
            session: str | None = None,
        ) -> str:
            if not actions:
                return json.dumps({"error": "actions must be a non-empty list of Scrappey action objects"})
            try:
                name, result = await router.run(
                    "browser_actions",
                    preferred=_preferred(provider, "browser_actions"),
                    url=url,
                    actions=actions,
                    format=format,
                    proxy_country=proxy_country,
                    session=session,
                )
            except NoProviderAvailable as exc:
                return json.dumps({"error": str(exc)})
            return json.dumps({"provider": name, **to_dict(result)})

    if "web_screenshot" in enabled_tools:

        @server.tool(name="web_screenshot", description=_TOOL_DESCRIPTIONS["web_screenshot"])
        async def web_screenshot(
            url: str,
            full_page: bool = False,
            selector: str | None = None,
            provider: str | None = None,
            proxy_country: str | None = None,
            session: str | None = None,
        ) -> str:
            try:
                name, img = await router.run(
                    "screenshot",
                    preferred=_preferred(provider, "screenshot"),
                    url=url,
                    full_page=full_page,
                    selector=selector,
                    proxy_country=proxy_country,
                    session=session,
                )
            except NoProviderAvailable as exc:
                return json.dumps({"error": str(exc)})
            return json.dumps({"provider": name, "url": url, "format": "png", "screenshot_base64": img})

    if "web_session" in enabled_tools:

        @server.tool(name="web_session", description=_TOOL_DESCRIPTIONS["web_session"])
        async def web_session(
            op: Literal["create", "destroy"],
            session: str,
            provider: str | None = None,
        ) -> str:
            if not session:
                return json.dumps({"error": "session name is required"})
            try:
                name, result = await router.run(
                    "session", preferred=_preferred(provider, "session"), op=op, session=session
                )
            except NoProviderAvailable as exc:
                return json.dumps({"error": str(exc)})
            return json.dumps({"provider": name, **result})

    if "web_crawl" in enabled_tools:

        @server.tool(name="web_crawl", description=_TOOL_DESCRIPTIONS["web_crawl"])
        async def web_crawl(
            url: str,
            max_pages: int = 10,
            max_depth: int = 2,
            provider: str | None = None,
            force: bool = False,
        ) -> str:
            if not 1 <= max_pages <= 100:
                return json.dumps({"error": "max_pages must be between 1 and 100"})
            if not 1 <= max_depth <= 10:
                return json.dumps({"error": "max_depth must be between 1 and 10"})
            try:
                name, pages = await router.run(
                    "crawl",
                    preferred=_preferred(provider, "crawl"),
                    url=url,
                    max_pages=max_pages,
                    max_depth=max_depth,
                )
            except NoProviderAvailable as exc:
                return await _dual_run(
                    router,
                    capability="crawl",
                    force=force,
                    run=lambda p: emulated_crawl(p, url, max_pages, max_depth),
                    unavailable_error=str(exc),
                    explanation=crawl_explanation(url, max_pages, max_depth),
                )
            return json.dumps({"provider": name, "pages": to_dict(pages)})

    if "web_extract" in enabled_tools:

        @server.tool(name="web_extract", description=_TOOL_DESCRIPTIONS["web_extract"])
        async def web_extract(
            url: str,
            prompt: str | None = None,
            provider: str | None = None,
            js_render: bool = False,
            force: bool = False,
        ) -> str:
            try:
                name, data = await router.run("extract", preferred=_preferred(provider, "extract"), url=url, prompt=prompt)
            except NoProviderAvailable as exc:
                return await _dual_run(
                    router,
                    capability="extract",
                    force=force,
                    run=lambda p: emulated_extract(p, url, prompt=prompt, js_render=js_render),
                    unavailable_error=str(exc),
                    explanation=extract_explanation(url, prompt, js_render, llm.backend()),
                    extra={"url": url},
                )
            return json.dumps({"provider": name, "url": url, "data": data})

    _hide_force_flags(server)
    return server


def _hide_force_flags(server: MCPServer) -> None:
    """Remove the dual-run ``force`` parameter from the published tool schemas.

    ``force`` must stay a real function parameter — the SDK validates call
    arguments against its pydantic argument model and silently drops keys
    that are not model fields — but must not appear in ``tools/list``: the
    dual-run trigger reveals it only through the first-call explanation.
    Mutating the stored schema dict reaches the wire because tools/list
    reads the tool's ``parameters`` at request time.

    This touches SDK internals (``_tool_manager``); if the structure ever
    changes, the worst case is the flag becoming visible in the schema,
    which degrades the surprise but not the gate. Schema is rebuilt on each
    tools/list response, so one mutation at build time is enough.
    """
    for name in ("web_search", "web_crawl", "web_extract"):
        try:
            tool = server._tool_manager.get_tool(name)
        except AttributeError:  # pragma: no cover - SDK structure change
            logger.warning("dual-run schemas: tool manager not found; 'force' stays visible")
            return
        if tool is None:
            continue
        parameters = tool.parameters
        properties = parameters.get("properties")
        if isinstance(properties, dict):
            properties.pop("force", None)
        required = parameters.get("required")
        if isinstance(required, list) and "force" in required:
            required.remove("force")
