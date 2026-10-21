"""Provider selection and failover.

The router picks the first enabled provider that supports the requested tool
(optionally forced via `preferred`), and on failure falls through to the next
candidate, aggregating errors. Callers get normalized results plus the name of
the provider that actually served them.

An optional TTL cache (``cache_ttl``) deduplicates identical ``fetch`` calls
within a time window — agents re-reading the same page pay once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

import httpx

from .errors import NoProviderAvailable, ProviderError
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
        if priority:
            self._order = [n for n in priority if n in self._providers] + [
                n for n in self._providers if n not in priority
            ]
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

    def available(self, tool: str) -> list[BaseProvider]:
        """Enabled providers that support `tool`, in priority order."""
        return [
            self._providers[n]
            for n in self._order
            if n in self._providers and self._providers[n].enabled and tool in self._providers[n].capabilities
        ]

    def get(self, name: str) -> BaseProvider | None:
        """Look up a provider by name (enabled or not)."""
        return self._providers.get(name)

    async def run(self, tool: str, preferred: str | None = None, **kwargs) -> tuple[str, object]:
        """Dispatch `tool` to the best provider.

        Returns ``(provider_name, result)`` so callers can attribute the
        response. Fails over across candidates on error.
        """
        if tool not in TOOLS:
            raise NoProviderAvailable(f"unknown tool '{tool}'")
        candidates = self.available(tool)
        if preferred:
            if preferred not in self._providers:
                raise NoProviderAvailable(f"unknown provider '{preferred}'")
            prov = self._providers[preferred]
            if not prov.enabled:
                raise NoProviderAvailable(f"provider '{preferred}' is not configured")
            if tool not in prov.capabilities:
                raise NoProviderAvailable(f"provider '{preferred}' does not support '{tool}'")
            candidates = [prov] + [p for p in candidates if p.name != preferred]
        if not candidates:
            raise NoProviderAvailable(
                f"no configured provider supports '{tool}' "
                f"(enabled: {[p.name for p in self._providers.values() if p.enabled]})"
            )

        # TTL cache: only identical fetch calls (same tool, preferred provider,
        # and kwargs) are served from cache.
        cache_key = None
        if tool == "fetch" and self._cache_ttl > 0:
            cache_key = self._cache_key(tool, preferred, kwargs)
            hit = self._cache_get(tool, cache_key)
            if hit is not None:
                logger.debug("cache hit for '%s'", tool)
                return hit

        errors: list[str] = []
        start = time.monotonic()
        for prov in candidates:
            try:
                result = await getattr(prov, tool)(**kwargs)
                if cache_key is not None:
                    self._cache_put(tool, cache_key, prov.name, result)
                logger.info("'%s' served by %s in %.1fs", tool, prov.name, time.monotonic() - start)
                return prov.name, result
            except (ProviderError, httpx.HTTPError) as exc:
                logger.warning("provider %s failed '%s': %s", prov.name, tool, exc)
                errors.append(f"{prov.name}: {exc}")
        logger.error("'%s' failed on all providers", tool)
        raise NoProviderAvailable(f"all providers failed for '{tool}': " + "; ".join(errors))

    async def aclose(self) -> None:
        for prov in self._providers.values():
            await prov.aclose()
        if self._client is not None:
            await self._client.aclose()
