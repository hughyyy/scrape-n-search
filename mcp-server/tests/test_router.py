"""Router selection, priority, and failover tests."""

from __future__ import annotations

import pytest

from scrape_n_search.errors import NoProviderAvailable, ProviderError
from scrape_n_search.models import SearchResult
from scrape_n_search.providers.base import BaseProvider
from scrape_n_search.router import Router


class FakeProvider(BaseProvider):
    def __init__(self, name: str, caps: set[str], *, enabled: bool = True, fail: bool = False):
        self.name = name
        self.capabilities = frozenset(caps)
        self._enabled = enabled
        self._fail = fail

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def search(self, query: str, **kw) -> list[SearchResult]:
        if self._fail:
            raise ProviderError(f"{self.name} exploded")
        return [SearchResult(title=f"{self.name}:{query}", url="https://x")]


def make_router(*providers, priority=None) -> Router:
    return Router(list(providers), priority=priority)


async def test_uses_first_priority():
    router = make_router(FakeProvider("a", {"search"}), FakeProvider("b", {"search"}), priority=["b", "a"])
    name, results = await router.run("search", query="q")
    assert name == "b"
    assert results[0].title == "b:q"


async def test_skips_disabled_providers():
    router = make_router(
        FakeProvider("a", {"search"}, enabled=False),
        FakeProvider("b", {"search"}),
    )
    name, _ = await router.run("search", query="q")
    assert name == "b"


async def test_filters_by_capability():
    router = make_router(FakeProvider("a", {"fetch"}))
    with pytest.raises(NoProviderAvailable):
        await router.run("search", query="q")


async def test_preferred_provider_forced():
    router = make_router(FakeProvider("a", {"search"}), FakeProvider("b", {"search"}))
    name, _ = await router.run("search", preferred="a", query="q")
    assert name == "a"


async def test_preferred_unknown_provider():
    router = make_router(FakeProvider("a", {"search"}))
    with pytest.raises(NoProviderAvailable):
        await router.run("search", preferred="nope", query="q")


async def test_preferred_unsupported_tool():
    router = make_router(FakeProvider("a", {"search"}), FakeProvider("b", {"search"}))
    with pytest.raises(NoProviderAvailable):
        await router.run("fetch", preferred="a", url="https://x")


async def test_failover_to_next_provider():
    router = make_router(
        FakeProvider("a", {"search"}, fail=True),
        FakeProvider("b", {"search"}),
    )
    name, results = await router.run("search", query="q")
    assert name == "b"
    assert results[0].title == "b:q"


async def test_all_fail_aggregates_errors():
    router = make_router(
        FakeProvider("a", {"search"}, fail=True),
        FakeProvider("b", {"search"}, fail=True),
    )
    with pytest.raises(NoProviderAvailable) as exc:
        await router.run("search", query="q")
    assert "a exploded" in str(exc.value)
    assert "b exploded" in str(exc.value)


async def test_no_provider_available():
    router = make_router(FakeProvider("a", {"search"}, enabled=False))
    with pytest.raises(NoProviderAvailable):
        await router.run("search", query="q")


async def test_unknown_tool():
    router = make_router(FakeProvider("a", {"search"}))
    with pytest.raises(NoProviderAvailable):
        await router.run("bogus", query="q")


class SessionProvider(FakeProvider):
    async def session(self, op: str, session: str, **kw):
        return {"op": op, "session": session}


async def test_session_tool_dispatch():
    router = make_router(SessionProvider("a", {"session"}))
    name, result = await router.run("session", op="create", session="s1")
    assert name == "a"
    assert result == {"op": "create", "session": "s1"}
