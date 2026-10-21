"""Configuration tests: HTTP_TIMEOUT, shared client ownership, env-driven router."""

from __future__ import annotations

import httpx
import pytest

from scrape_n_search.__main__ import build_router_from_env
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
