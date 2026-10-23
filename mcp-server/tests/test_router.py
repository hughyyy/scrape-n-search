"""Router selection, priority, and failover tests."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from scrape_n_search.errors import NoProviderAvailable, ProviderError
from scrape_n_search.models import FetchedPage, SearchResult
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


async def test_preferred_disabled_provider():
    router = make_router(FakeProvider("a", {"search"}, enabled=False))
    with pytest.raises(NoProviderAvailable, match="not configured"):
        await router.run("search", preferred="a", query="q")


async def test_priority_ignores_unknown_names():
    router = make_router(FakeProvider("a", {"search"}), FakeProvider("b", {"search"}), priority=["zz", "a"])
    name, _ = await router.run("search", query="q")
    assert name == "a"  # "zz" dropped; a promoted; b appended after


async def test_failover_on_httpx_error():
    class NetProvider(FakeProvider):
        async def search(self, query: str, **kw):
            if self._fail:
                raise httpx.ConnectError("refused", request=None)
            return [SearchResult(title="ok", url="https://x")]

    router = make_router(
        NetProvider("a", {"search"}, fail=True),
        NetProvider("b", {"search"}),
    )
    name, results = await router.run("search", query="q")
    assert name == "b"
    assert results[0].title == "ok"


async def test_uncaught_exception_propagates_without_failover():
    class BoomProvider(FakeProvider):
        async def search(self, query: str, **kw):
            raise RuntimeError("boom")

    router = make_router(BoomProvider("a", {"search"}), FakeProvider("b", {"search"}))
    with pytest.raises(RuntimeError):
        await router.run("search", query="q")


async def test_no_provider_error_lists_enabled():
    router = make_router(FakeProvider("a", {"search"}, enabled=False))
    with pytest.raises(NoProviderAvailable, match="enabled"):
        await router.run("search", query="q")


async def test_cache_evicts_oldest_at_cap():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    for i in range(513):
        await router.run("fetch", url=f"https://x/{i}")
    assert provider.calls == 513
    # cap enforced: only 512 entries kept
    assert len(router._cache["fetch"]) == 512
    # the first (oldest) entry was evicted -> re-fetch
    await router.run("fetch", url="https://x/0")
    assert provider.calls == 514
    # the newest entry is still cached
    await router.run("fetch", url="https://x/512")
    assert provider.calls == 514


async def test_cache_put_drops_expired_entries_first():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    # plant 511 live entries + 1 already-expired entry (= cap), so inserting
    # one more must evict the expired entry rather than a live one
    live = {f"live-{i}": (10**9, "p", "r") for i in range(511)}
    router._cache["fetch"] = dict(live)
    router._cache["fetch"]["expired"] = (0.0, "p", "r")  # expires in the past
    await router.run("fetch", url="https://x/new")
    assert "expired" not in router._cache["fetch"]
    assert sum(k.startswith("live-") for k in router._cache["fetch"]) == 511
    assert len(router._cache["fetch"]) == 512


async def test_cache_hit_returns_provider_name_and_result():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    name1, r1 = await router.run("fetch", url="https://x")
    name2, r2 = await router.run("fetch", url="https://x")
    assert (name1, r1.content) == (name2, r2.content) == ("p", "content #1")


async def test_cache_evicts_expired_on_get():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    await router.run("fetch", url="https://x")
    # expire the stored entry directly
    key = router._cache_key("fetch", None, {"url": "https://x"})
    router._cache["fetch"][key] = (0.0, "p", "unused")
    hit = router._cache_get("fetch", key)
    assert hit is None
    assert key not in router._cache["fetch"]


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


class CountingProvider(BaseProvider):
    name = "p"
    capabilities = frozenset({"fetch"})

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def fetch(self, url: str, **kw) -> FetchedPage:
        self.calls += 1
        return FetchedPage(url=url, content=f"content #{self.calls}")


async def test_cache_disabled_by_default():
    provider = CountingProvider()
    router = Router([provider])
    await router.run("fetch", url="https://x")
    await router.run("fetch", url="https://x")
    assert provider.calls == 2


async def test_cache_hit_skips_provider():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    name1, r1 = await router.run("fetch", url="https://x", format="markdown")
    name2, r2 = await router.run("fetch", url="https://x", format="markdown")
    assert provider.calls == 1
    assert r1.content == r2.content == "content #1"
    assert name1 == name2 == "p"


async def test_cache_keyed_by_kwargs():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    await router.run("fetch", url="https://x")
    await router.run("fetch", url="https://y")
    await router.run("fetch", url="https://x", js_render=True)
    assert provider.calls == 3


async def test_cache_does_not_serve_other_tools():
    provider = FakeProvider("a", {"search"})
    router = Router([provider], cache_ttl=60)
    _, r1 = await router.run("search", query="q")
    _, r2 = await router.run("search", query="q")
    assert r1[0].title == "a:q"
    assert r2[0].title == "a:q"


async def test_cache_expires():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=0.05)
    await router.run("fetch", url="https://x")
    await asyncio.sleep(0.08)
    await router.run("fetch", url="https://x")
    assert provider.calls == 2


async def test_cache_key_includes_preferred_provider():
    p = CountingProvider()
    q = CountingProvider()
    q.name = "q"
    router = Router([p, q], cache_ttl=60)
    await router.run("fetch", preferred="p", url="https://x")
    await router.run("fetch", preferred="p", url="https://x")
    assert p.calls == 1
    await router.run("fetch", preferred="q", url="https://x")
    assert q.calls == 1
    assert p.calls == 1


async def test_router_logs_serve_and_failover(caplog):
    a = FakeProvider("a", {"search"}, fail=True)
    b = FakeProvider("b", {"search"})
    router = Router([a, b], priority=["a", "b"])
    with caplog.at_level(logging.INFO, logger="scrape_n_search.router"):
        name, _ = await router.run("search", query="q")
    assert name == "b"
    assert any("'search' served by b" in record.message for record in caplog.records)
    assert any("provider a failed 'search'" in record.message for record in caplog.records)


class FailingFetchProvider(BaseProvider):
    name = "p"
    capabilities = frozenset({"fetch"})

    def __init__(self, fail: bool):
        super().__init__()
        self._fail = fail
        self.calls = 0

    async def fetch(self, url: str, **kw) -> FetchedPage:
        self.calls += 1
        if self._fail:
            raise ProviderError("boom")
        return FetchedPage(url=url, content="ok")


class NamedFailingFetchProvider(FailingFetchProvider):
    def __init__(self, name: str, fail: bool):
        super().__init__(fail)
        self.name = name


async def test_failed_provider_result_never_cached():
    failing = NamedFailingFetchProvider("a", fail=True)
    ok = NamedFailingFetchProvider("b", fail=False)
    router = Router([failing, ok], cache_ttl=60)
    name1, r1 = await router.run("fetch", url="https://x")
    assert name1 == "b" and r1.content == "ok"
    assert failing.calls == 1 and ok.calls == 1
    # second call served from cache: no further provider calls
    name2, _ = await router.run("fetch", url="https://x")
    assert name2 == "b" and failing.calls == 1 and ok.calls == 1


async def test_cache_keyed_by_format():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    await router.run("fetch", url="https://x", format="markdown")
    await router.run("fetch", url="https://x", format="html")
    assert provider.calls == 2
    await router.run("fetch", url="https://x", format="markdown")
    assert provider.calls == 2


async def test_cache_key_is_stable():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    k1 = router._cache_key("fetch", None, {"url": "https://x", "format": "markdown"})
    k2 = router._cache_key("fetch", None, {"format": "markdown", "url": "https://x"})
    assert k1 == k2  # kwargs sorted -> order-independent
    k3 = router._cache_key("fetch", None, {"url": "https://x", "format": "html"})
    assert k1 != k3
    k4 = router._cache_key("fetch", "p", {"url": "https://x", "format": "markdown"})
    assert k1 != k4  # preferred provider participates


async def test_available_returns_priority_order_and_filters():
    router = make_router(
        FakeProvider("a", {"search", "fetch"}),
        FakeProvider("x", {"search"}, enabled=False),
        FakeProvider("b", {"search"}),
        priority=["b", "a", "x"],
    )
    names = [p.name for p in router.available("search")]
    assert names == ["b", "a"]  # disabled x excluded, order honored
    assert [p.name for p in router.available("fetch")] == ["a"]


async def test_preferred_without_capability_and_no_alternatives():
    router = make_router(FakeProvider("a", {"search"}))
    with pytest.raises(NoProviderAvailable, match="does not support 'fetch'"):
        await router.run("fetch", preferred="a", url="https://x")


async def test_concurrent_identical_fetches_safe():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    results = await asyncio.gather(*(router.run("fetch", url="https://x") for _ in range(5)))
    names = {name for name, _ in results}
    assert names == {"p"}
    assert all(r.content for _, r in results)
    # whichever raced, the cache ends consistent: a later call is a cache hit
    await router.run("fetch", url="https://x")
    calls_after = provider.calls
    await router.run("fetch", url="https://x")
    assert provider.calls == calls_after


# --- priority hygiene ---------------------------------------------------


async def test_duplicate_priority_names_are_tried_once():
    class Boom(BaseProvider):
        name = "boom"
        capabilities = frozenset({"search"})

        def __init__(self):
            super().__init__()
            self.calls = 0

        async def search(self, query: str, **kw):
            self.calls += 1
            raise ProviderError("exploded")

    provider = Boom()
    router = Router([provider], priority=["boom", "boom", "missing"])
    assert [p.name for p in router.available("search")] == ["boom"]
    with pytest.raises(NoProviderAvailable, match="boom: exploded"):
        await router.run("search", query="q")
    assert provider.calls == 1  # a repeated name is not retried (and billed) twice


async def test_duplicate_priority_keeps_first_occurrence_order():
    a, b = FakeProvider("a", {"search"}), FakeProvider("b", {"search"})
    router = Router([a, b], priority=["b", "a", "b"])
    assert [p.name for p in router.available("search")] == ["b", "a"]


async def test_cache_key_separates_fetch_options():
    provider = CountingProvider()
    router = Router([provider], cache_ttl=60)
    await router.run("fetch", url="https://x")
    await router.run("fetch", url="https://x", js_render=True)
    await router.run("fetch", url="https://x", premium_proxy=True)
    await router.run("fetch", url="https://x", proxy_country="UnitedStates")
    await router.run("fetch", url="https://x", session="s1")
    assert provider.calls == 5  # each option set is a distinct billed request
    await router.run("fetch", url="https://x")  # the original key is still cached
    assert provider.calls == 5


# --- transport failures and malformed provider responses ----------------


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("dns failure"),
        httpx.ReadTimeout("too slow"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
async def test_transport_failure_fails_over(exc):
    class Flaky(BaseProvider):
        name = "flaky"
        capabilities = frozenset({"search"})

        async def search(self, query: str, **kw):
            raise exc

    router = make_router(Flaky(), FakeProvider("ok", {"search"}))
    name, results = await router.run("search", query="q")
    assert name == "ok"
    assert results[0].title == "ok:q"


async def test_real_adapter_malformed_response_fails_over():
    # end-to-end: a provider answering 200 with a malformed body degrades to
    # the next candidate instead of crashing the tool call
    from conftest import make_client

    from scrape_n_search.providers.tavily import TavilyProvider

    malformed = TavilyProvider(
        "k", make_client(lambda r: httpx.Response(200, json={"results": {"web": []}}))
    )
    router = Router([malformed, FakeProvider("ok", {"search"})], priority=["tavily", "ok"])
    name, results = await router.run("search", query="q")
    assert name == "ok"
    assert results[0].title == "ok:q"


async def test_real_adapter_non_json_response_fails_over():
    from conftest import make_client


    from scrape_n_search.providers.tavily import TavilyProvider


    challenge = TavilyProvider(
        "k", make_client(lambda r: httpx.Response(200, text="<html>verify you are human</html>"))
    )
    router = Router([challenge, FakeProvider("ok", {"search"})], priority=["tavily", "ok"])
    name, _ = await router.run("search", query="q")
    assert name == "ok"
