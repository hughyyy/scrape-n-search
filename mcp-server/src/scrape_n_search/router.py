"""Provider selection and failover.

The router picks the first enabled provider that supports the requested tool
(optionally forced via `preferred`), and on failure falls through to the next
candidate, aggregating errors. Callers get normalized results plus the name of
the provider that actually served them.

Selection semantics:

- no ``preferred``: auto-select in priority order; fail over on error.
- ``preferred`` with ``pin=False`` (the ``DEFAULT_PROVIDER`` case): try it
  first, then the normal priority order — a soft preference.
- ``preferred`` with ``pin=True`` (an explicit caller ``provider=`` argument):
  that provider only. A caller pin is a cost/privacy decision, so silently
  answering from another provider would be wrong; a failure surfaces instead.

Fetch candidates are additionally filtered by the requested ``format`` so a
provider that can only return plain text is never handed ``format="html"``.

An optional TTL cache (``cache_ttl``) deduplicates identical ``fetch`` calls
within a time window — agents re-reading the same page pay once. Concurrent
identical misses share a single in-flight request (single-flight), so a burst
of parallel tool calls bills the provider once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

import httpx

from .errors import (
    AllProvidersFailed,
    NoProviderConfigured,
    ProviderDisabled,
    ProviderError,
    UnknownProvider,
    UnknownTool,
    UnsupportedCapability,
)
from .logging_utils import redact
from .providers.base import BaseProvider

logger = logging.getLogger(__name__)

TOOLS = frozenset({"search", "fetch", "crawl", "extract", "browser_actions", "screenshot", "session"})

# Upper bound on cached fetch results; oldest entries are evicted beyond this.
_MAX_CACHE_ENTRIES = 512


class Router:
    def __init__(
        self,
        providers: list[BaseProvider],
        priority: list[str] | None = None,
        client: httpx.AsyncClient | None = None,
        cache_ttl: float = 0.0,
    ):
        self._providers = {p.name: p for p in providers}
        self._client = client
        self._cache_ttl = cache_ttl
        # tool -> {key: (expires_monotonic, provider_name, result)}
        self._cache: dict[str, dict[str, tuple[float, str, object]]] = {}
        # cache key -> (event, holder) for in-flight fetch deduplication
        self._inflight: dict[str, tuple[asyncio.Event, dict[str, object]]] = {}
        if priority:
            # Dedupe: a repeated name would otherwise be tried (and billed) twice.
            named = list(dict.fromkeys(n for n in priority if n in self._providers))
            self._order = named + [n for n in self._providers if n not in named]
        else:
            self._order = list(self._providers)

    def _cache_key(self, tool: str, preferred: str | None, kwargs: dict) -> str:
        payload = json.dumps([tool, preferred, kwargs], sort_keys=True, default=str)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _cache_get(self, tool: str, key: str):
        entry = self._cache.get(tool, {}).get(key)
        if entry is None:
            return None
        expires, pname, result = entry
        if time.monotonic() >= expires:
            del self._cache[tool][key]
            return None
        return pname, result

    def _cache_put(self, tool: str, key: str, pname: str, result: object) -> None:
        entries = self._cache.setdefault(tool, {})
        # Evict expired entries, then the oldest, to bound memory.
        if len(entries) >= _MAX_CACHE_ENTRIES:
            now = time.monotonic()
            expired = [k for k, (exp, _, _) in entries.items() if exp <= now]
            for k in expired:
                del entries[k]
        if len(entries) >= _MAX_CACHE_ENTRIES:
            oldest = next(iter(entries))
            del entries[oldest]
        entries[key] = (time.monotonic() + self._cache_ttl, pname, result)

    def available(self, tool: str, fetch_format: str | None = None) -> list[BaseProvider]:
        """Enabled providers that support `tool` (and fetch format), in priority order."""
        out: list[BaseProvider] = []
        for name in self._order:
            prov = self._providers.get(name)
            if prov is None or not prov.enabled or tool not in prov.capabilities:
                continue
            if fetch_format is not None and not prov.supports_fetch_format(fetch_format):
                continue
            out.append(prov)
        return out

    def get(self, name: str) -> BaseProvider | None:
        """Look up a provider by name (enabled or not)."""
        return self._providers.get(name)

    async def run(
        self,
        tool: str,
        preferred: str | None = None,
        *,
        pin: bool = False,
        **kwargs,
    ) -> tuple[str, object]:
        """Dispatch `tool` to the best provider.

        Returns ``(provider_name, result)`` so callers can attribute the
        response. Fails over across candidates on error unless ``pin`` is set.
        """
        if tool not in TOOLS:
            raise UnknownTool(f"unknown tool '{tool}'")
        fetch_format = kwargs.get("format") if tool == "fetch" else None
        if not isinstance(fetch_format, str):
            fetch_format = None
        candidates = self.available(tool, fetch_format=fetch_format)
        if preferred:
            if preferred not in self._providers:
                raise UnknownProvider(f"unknown provider '{preferred}'")
            prov = self._providers[preferred]
            if not prov.enabled:
                raise ProviderDisabled(f"provider '{preferred}' is not configured")
            if tool not in prov.capabilities:
                raise UnsupportedCapability(f"provider '{preferred}' does not support '{tool}'")
            if fetch_format is not None and not prov.supports_fetch_format(fetch_format):
                raise UnsupportedCapability(
                    f"provider '{preferred}' does not support '{tool}' with format "
                    f"'{fetch_format}'"
                )
            if pin:
                candidates = [prov]
            else:
                candidates = [prov] + [p for p in candidates if p.name != preferred]
        if not candidates:
            suffix = f" with format '{fetch_format}'" if fetch_format is not None else ""
            raise NoProviderConfigured(
                f"no configured provider supports '{tool}'{suffix} "
                f"(enabled: {[p.name for p in self._providers.values() if p.enabled]})"
            )

        cache_key = None
        if tool == "fetch" and self._cache_ttl > 0:
            cache_key = self._cache_key(tool, preferred, kwargs)
            hit = self._cache_get(tool, cache_key)
            if hit is not None:
                logger.debug("cache hit for '%s'", tool)
                return hit
        if cache_key is None:
            return await self._dispatch(tool, candidates, kwargs, None)
        return await self._dispatch_cached(tool, candidates, kwargs, cache_key)

    async def _dispatch_cached(
        self,
        tool: str,
        candidates: list[BaseProvider],
        kwargs: dict,
        cache_key: str,
    ) -> tuple[str, object]:
        """Run one fetch per cache key, sharing it with concurrent waiters."""
        while True:
            inflight = self._inflight.get(cache_key)
            if inflight is None:
                break
            event, holder = inflight
            await event.wait()
            if "result" in holder:
                return holder["result"]  # type: ignore[return-value]
            if not isinstance(holder.get("error"), asyncio.CancelledError):
                raise holder["error"]  # type: ignore[misc]
            # The leader was cancelled: reclaim its stale slot (unless a newer
            # leader already replaced it) and run this call as the new leader.
            if self._inflight.get(cache_key) is inflight:
                self._inflight.pop(cache_key, None)

        event = asyncio.Event()
        outcome_holder: dict[str, object] = {}
        self._inflight[cache_key] = (event, outcome_holder)
        try:
            outcome = await self._dispatch(tool, candidates, kwargs, cache_key)
            outcome_holder["result"] = outcome
            return outcome
        except BaseException as exc:
            outcome_holder["error"] = exc
            raise
        finally:
            self._inflight.pop(cache_key, None)
            event.set()

    async def _dispatch(
        self,
        tool: str,
        candidates: list[BaseProvider],
        kwargs: dict,
        cache_key: str | None,
    ) -> tuple[str, object]:
        errors: list[str] = []
        start = time.monotonic()
        for prov in candidates:
            try:
                result = await getattr(prov, tool)(**kwargs)
            except (ProviderError, httpx.HTTPError) as exc:
                message = redact(str(exc))
                logger.warning("provider %s failed '%s': %s", prov.name, tool, message)
                errors.append(f"{prov.name}: {message}")
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A provider adapter must not take the whole call down: an
                # unexpected parsing bug degrades to the next candidate. The
                # traceback is logged so the adapter bug stays visible.
                logger.exception("provider %s crashed '%s'", prov.name, tool)
                errors.append(f"{prov.name}: {type(exc).__name__}: {redact(str(exc))}")
                continue
            if cache_key is not None:
                self._cache_put(tool, cache_key, prov.name, result)
            logger.info("'%s' served by %s in %.1fs", tool, prov.name, time.monotonic() - start)
            return prov.name, result
        logger.error("'%s' failed on all providers", tool)
        raise AllProvidersFailed(f"all providers failed for '{tool}': " + "; ".join(errors))

    async def aclose(self) -> None:
        for prov in self._providers.values():
            await prov.aclose()
        if self._client is not None:
            await self._client.aclose()
