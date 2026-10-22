"""Configuration tests: HTTP_TIMEOUT, shared client ownership, env-driven router."""

from __future__ import annotations

import logging

import httpx
import pytest

from scrape_n_search.__main__ import build_router_from_env, log_level_from_env
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
