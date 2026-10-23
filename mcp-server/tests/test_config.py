"""Configuration tests: HTTP_TIMEOUT, shared client ownership, env-driven router."""

from __future__ import annotations

import logging

import pytest
from scrape_n_search.__main__ import build_router_from_env, build_server_from_env, log_level_from_env
from scrape_n_search.providers import build_providers, configured_timeout
from scrape_n_search.providers.base import DEFAULT_TIMEOUT
from scrape_n_search.router import Router


async def test_configured_timeout_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("HTTP_TIMEOUT", raising=False)
    assert configured_timeout() is DEFAULT_TIMEOUT


async def test_configured_timeout_reads_env(monkeypatch):
    monkeypatch.setenv("HTTP_TIMEOUT", "120")
    timeout = configured_timeout()
    assert timeout.read == 120.0
    assert timeout.connect == 15.0


async def test_configured_timeout_clamps_connect_below_total(monkeypatch):
    monkeypatch.setenv("HTTP_TIMEOUT", "5")
    timeout = configured_timeout()
    assert timeout.read == 5.0
    assert timeout.connect == 5.0


async def test_configured_timeout_rejects_invalid(monkeypatch):
    for bad in ("abc", "0", "-3"):
        monkeypatch.setenv("HTTP_TIMEOUT", bad)
        with pytest.raises(ValueError):
            configured_timeout()


async def test_build_router_shares_configured_client(monkeypatch):
    monkeypatch.setenv("HTTP_TIMEOUT", "120")
    monkeypatch.delenv("PROVIDER_PRIORITY", raising=False)
    router = build_router_from_env()
    try:
        providers = [p for p in router._providers.values()]
        assert len(providers) > 0
        client = providers[0]._client
        assert client is router._client
        assert client.timeout.read == 120.0
        for p in providers[1:]:
            assert p._client is client
    finally:
        await router.aclose()


async def test_build_router_reads_cache_ttl(monkeypatch):
    monkeypatch.setenv("CACHE_TTL", "300")
    monkeypatch.delenv("PROVIDER_PRIORITY", raising=False)
    router = build_router_from_env()
    try:
        assert router._cache_ttl == 300.0
    finally:
        await router.aclose()


async def test_build_router_rejects_bad_cache_ttl(monkeypatch):
    for bad in ("-5", "abc"):
        monkeypatch.setenv("CACHE_TTL", bad)
        with pytest.raises(ValueError):
            build_router_from_env()


def test_log_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert log_level_from_env() == logging.INFO


def test_log_level_reads_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert log_level_from_env() == logging.DEBUG


def test_log_level_rejects_unknown(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        log_level_from_env()


async def test_build_providers_without_client_uses_default_timeout():
    providers = build_providers()
    for p in providers:
        try:
            assert p._client.timeout == DEFAULT_TIMEOUT
        finally:
            await p.aclose()


class StubClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


async def test_router_aclose_closes_shared_client():
    client = StubClient()
    router = Router([], client=client)
    await router.aclose()
    assert client.closed


# --- build_providers: keyless Jina toggle and env keys ---------------------


async def test_build_providers_drops_jina_when_keyless_disabled(monkeypatch):
    monkeypatch.setenv("JINA_KEYLESS", "0")
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    providers = build_providers()
    try:
        names = {p.name for p in providers}
        assert "jina" not in names
    finally:
        for p in providers:
            await p.aclose()


async def test_build_providers_jina_with_key_even_when_keyless_off(monkeypatch):
    monkeypatch.setenv("JINA_KEYLESS", "0")
    monkeypatch.setenv("JINA_API_KEY", "k")
    providers = build_providers()
    try:
        jina = next(p for p in providers if p.name == "jina")
        assert jina.enabled
        assert jina.api_key == "k"
    finally:
        for p in providers:
            await p.aclose()


async def test_build_providers_reads_scrappey_key(monkeypatch):
    monkeypatch.setenv("SCRAPPEY_API_KEY", "k")
    monkeypatch.delenv("JINA_KEYLESS", raising=False)
    providers = build_providers()
    try:
        scrappey = next(p for p in providers if p.name == "scrappey")
        assert scrappey.enabled
        jina = next(p for p in providers if p.name == "jina")
        assert jina.enabled  # keyless default
    finally:
        for p in providers:
            await p.aclose()


async def test_build_providers_default_priority_order(monkeypatch):
    from scrape_n_search.providers import DEFAULT_PRIORITY

    assert DEFAULT_PRIORITY == ["tinyfish", "tavily", "exa", "zenrows", "scrappey", "firecrawl", "serper", "jina"]
    monkeypatch.delenv("PROVIDER_PRIORITY", raising=False)
    router = build_router_from_env()
    try:
        assert router._order == DEFAULT_PRIORITY  # no PROVIDER_PRIORITY env
    finally:
        await router.aclose()


# --- build_server_from_env: ENABLED_TOOLS allow-list ------------------------


async def test_enabled_tools_env_restricts_visible_tools(monkeypatch):
    from scrape_n_search.__main__ import build_server_from_env

    monkeypatch.setenv("ENABLED_TOOLS", "web_fetch")
    monkeypatch.delenv("DEFAULT_PROVIDER", raising=False)
    router = build_router_from_env()
    try:
        server = build_server_from_env(router)
        assert server._tool_manager.get_tool("web_fetch") is not None
        assert server._tool_manager.get_tool("web_search") is None
        # unknown tool names in the allow-list are ignored
        monkeypatch.setenv("ENABLED_TOOLS", "web_fetch,web_bogus")
        server2 = build_server_from_env(router)
        assert server2._tool_manager.get_tool("web_fetch") is not None
        assert server2._tool_manager.get_tool("web_bogus") is None
    finally:
        await router.aclose()


# --- configure_logging and env-driven router order --------------------------


def test_configure_logging_applies_env_level(monkeypatch):
    from scrape_n_search.__main__ import configure_logging

    kwargs = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: kwargs.update(kw))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging()
    assert kwargs["level"] == logging.DEBUG


def test_configure_logging_rejects_invalid(monkeypatch):
    from scrape_n_search.__main__ import configure_logging

    monkeypatch.setenv("LOG_LEVEL", "loud")
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        configure_logging()


async def test_build_router_applies_custom_priority(monkeypatch):
    monkeypatch.delenv("PROVIDER_PRIORITY", raising=False)
    monkeypatch.setenv("PROVIDER_PRIORITY", "jina,serper,scrappey")
    monkeypatch.delenv("JINA_KEYLESS", raising=False)
    router = build_router_from_env()
    try:
        order = router._order
        assert order.index("jina") < order.index("serper") < order.index("scrappey")
        # providers not named keep their registry order at the tail
        assert set(order) == {p.name for p in router._providers.values()}
    finally:
        await router.aclose()


async def test_build_router_priority_ignores_unknown_names(monkeypatch):
    monkeypatch.setenv("PROVIDER_PRIORITY", "nope,also-nope")
    monkeypatch.delenv("JINA_KEYLESS", raising=False)
    router = build_router_from_env()
    try:
        assert router._order == [p.name for p in router._providers.values()]
    finally:
        await router.aclose()


# --- main(): full boot wiring -----------------------------------------------


def test_main_wires_env_logging_router_server(monkeypatch):
    from mcp.server.mcpserver import MCPServer
    from scrape_n_search import __main__ as m

    calls: dict = {"dotenv": [], "run": None}
    monkeypatch.setattr(m, "load_dotenv", lambda *a, **k: calls["dotenv"].append((a, k)))
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv("ENABLED_TOOLS", raising=False)

    def fake_run(self, transport=None):
        calls["run"] = transport

    monkeypatch.setattr(MCPServer, "run", fake_run)
    m.main()
    # both dotenv loaders ran (cwd chain + project .env)
    assert len(calls["dotenv"]) == 2
    assert calls["run"] == "stdio"


def test_main_guard_executes_server_under_main(monkeypatch):
    import runpy
    import sys

    from mcp.server.mcpserver import MCPServer

    calls = {"run": None}

    def fake_run(self, transport=None):
        calls["run"] = transport

    monkeypatch.setattr(MCPServer, "run", fake_run)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv("ENABLED_TOOLS", raising=False)
    # re-execute the module with __name__ == "__main__": exercises the guard.
    # Drop the previously-imported copy first so runpy reloads it cleanly.
    monkeypatch.delitem(sys.modules, "scrape_n_search.__main__", raising=False)
    runpy.run_module("scrape_n_search.__main__", run_name="__main__")
    assert calls["run"] == "stdio"


# --- non-finite numeric env values --------------------------------------
# float("nan"/"inf") parses but breaks the semantics these knobs promise:
# nan silently disables the TTL cache, inf makes entries never expire and
# poisons httpx timeouts. Both must fail loudly at startup instead.


async def test_configured_timeout_rejects_non_finite(monkeypatch):
    for bad in ("nan", "inf", "-inf"):
        monkeypatch.setenv("HTTP_TIMEOUT", bad)
        with pytest.raises(ValueError):
            configured_timeout()


async def test_build_router_rejects_non_finite_cache_ttl(monkeypatch):
    for bad in ("nan", "inf", "-inf"):
        monkeypatch.setenv("CACHE_TTL", bad)
        with pytest.raises(ValueError):
            build_router_from_env()


def test_log_level_accepts_lowercase(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert log_level_from_env() == logging.DEBUG


async def test_provider_priority_duplicates_collapse(monkeypatch):
    monkeypatch.setenv("PROVIDER_PRIORITY", "jina,jina,exa")
    monkeypatch.delenv("HTTP_TIMEOUT", raising=False)
    router = build_router_from_env()
    try:
        names = [p.name for p in router.available("fetch")]
        assert names[:1] == ["jina"]  # first occurrence keeps its position
        assert names.count("jina") == 1  # never offered twice
    finally:
        await router.aclose()


# --- log redaction wiring ---------------------------------------------------


def test_configure_logging_installs_redact_filter_once(monkeypatch):
    from scrape_n_search.__main__ import configure_logging
    from scrape_n_search.logging_utils import RedactFilter

    handler = logging.StreamHandler()
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: logging.getLogger().addHandler(handler))
    try:
        configure_logging()
        configure_logging()  # idempotent: never stacks duplicate filters
        filters = [f for f in handler.filters if isinstance(f, RedactFilter)]
        assert len(filters) == 1
    finally:
        logging.getLogger().removeHandler(handler)


# --- startup validation -----------------------------------------------------


def test_main_rejects_invalid_extract_max_chars(monkeypatch):
    import scrape_n_search.__main__ as main_mod

    monkeypatch.setattr(main_mod, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("EXTRACT_MAX_CHARS", "bogus")
    with pytest.raises(ValueError, match="EXTRACT_MAX_CHARS"):
        main_mod.main()


def test_project_dir_env_override(monkeypatch, tmp_path):
    import importlib

    import scrape_n_search.__main__ as main_mod

    monkeypatch.setenv("SCRAPE_N_SEARCH_ENV_DIR", str(tmp_path))
    try:
        assert importlib.reload(main_mod).PROJECT_DIR == tmp_path
    finally:
        monkeypatch.delenv("SCRAPE_N_SEARCH_ENV_DIR", raising=False)
        importlib.reload(main_mod)


# --- packaging / env-example regressions -----------------------------------


def test_version_matches_pyproject():
    import re
    from pathlib import Path

    import scrape_n_search

    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', text, re.MULTILINE).group(1)
    assert declared == scrape_n_search.__version__


def test_env_example_does_not_pin_a_default_provider():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    active = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(line.startswith("DEFAULT_PROVIDER=") for line in active)


async def test_server_boots_with_a_single_non_scrappey_key(monkeypatch):
    for key in (
        "TINYFISH_API_KEY",
        "TAVILY_API_KEY",
        "EXA_API_KEY",
        "ZENROWS_API_KEY",
        "SCRAPPEY_API_KEY",
        "FIRECRAWL_API_KEY",
        "SERPER_API_KEY",
        "JINA_API_KEY",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.delenv("DEFAULT_PROVIDER", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("JINA_KEYLESS", "0")
    monkeypatch.delenv("HTTP_TIMEOUT", raising=False)
    router = build_router_from_env()
    try:
        assert build_server_from_env(router) is not None
    finally:
        await router.aclose()
