"""End-to-end MCP protocol test: the real server process, over stdio.

The unit tests build the server in-process and drive it over an in-memory
transport. This suite covers what that cannot: `__main__.main()` env wiring
(.env loading order, logging setup, provider construction), the stdio
transport itself, the lifespan teardown (`router.aclose()` + `llm.aclose()`
inside the server's event loop), and the schemas the client actually receives
over the wire.

Deterministic and offline: every provider key is blanked (blank, not absent,
so the machine-local `.env` cannot fill it back in — python-dotenv does not
override existing variables), keyless Jina is disabled, and any
`DEFAULT_PROVIDER`/`ENABLED_TOOLS` pinned in `.env` is cleared.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROJECT_DIR = Path(__file__).resolve().parents[1]

PROVIDER_KEYS = (
    "TINYFISH_API_KEY",
    "TAVILY_API_KEY",
    "EXA_API_KEY",
    "ZENROWS_API_KEY",
    "SCRAPPEY_API_KEY",
    "FIRECRAWL_API_KEY",
    "SERPER_API_KEY",
    "JINA_API_KEY",
    "OPENROUTER_API_KEY",
    "HF_TOKEN",
)

EXPECTED_TOOLS = {
    "web_search",
    "web_fetch",
    "web_crawl",
    "web_extract",
    "web_browser_actions",
    "web_screenshot",
    "web_session",
}


def _offline_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in PROVIDER_KEYS:
        env[key] = ""
    src = str(PROJECT_DIR / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src, env.get("PYTHONPATH")]))
    env.update(
        DEFAULT_PROVIDER="",
        ENABLED_TOOLS="",
        JINA_KEYLESS="0",
        CACHE_TTL="0",
        LOG_LEVEL="ERROR",
    )
    return env


@asynccontextmanager
async def _server_session():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "scrape_n_search"],
        env=_offline_env(),
        cwd=str(PROJECT_DIR),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def test_stdio_lists_every_tool_with_force_on_emulated_tools():
    async with _server_session() as session:
        assert session.server_info.name == "scrape-n-search"
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == EXPECTED_TOOLS
        schemas = {t.name: t.input_schema for t in tools.tools}
        for name in ("web_search", "web_crawl", "web_extract"):
            assert "force" in (schemas[name].get("properties") or {}), name
            assert "force" not in (schemas[name].get("required") or []), name
        for name in ("web_fetch", "web_browser_actions", "web_screenshot", "web_session"):
            assert "force" not in (schemas[name].get("properties") or {}), name


async def test_stdio_serves_playbook_resource():
    async with _server_session() as session:
        resources = await session.list_resources()
        assert "scrappey://playbook" in {str(r.uri) for r in resources.resources}
        contents = await session.read_resource("scrappey://playbook")
        assert "AliExpress" in contents.contents[0].text


async def test_stdio_offline_search_reports_missing_provider():
    async with _server_session() as session:
        res = await session.call_tool("web_search", {"query": "anything"})
        payload = json.loads(res.content[0].text)
        assert "no configured provider supports 'search'" in payload["error"]


async def test_stdio_argument_validation_roundtrip():
    async with _server_session() as session:
        res = await session.call_tool("web_search", {"query": "q", "max_results": 99})
        assert json.loads(res.content[0].text) == {
            "error": "max_results must be between 1 and 50"
        }
