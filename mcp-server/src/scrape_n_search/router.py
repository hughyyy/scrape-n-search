"""Provider selection and failover.

The router picks the first enabled provider that supports the requested tool
(optionally forced via `preferred`), and on failure falls through to the next
candidate, aggregating errors. Callers get normalized results plus the name of
the provider that actually served them.
"""

from __future__ import annotations

import httpx

from .errors import NoProviderAvailable, ProviderError
from .providers.base import BaseProvider

TOOLS = frozenset({"search", "fetch", "crawl", "extract", "browser_actions", "screenshot"})


class Router:
    def __init__(self, providers: list[BaseProvider], priority: list[str] | None = None):
        self._providers = {p.name: p for p in providers}
        if priority:
            self._order = [n for n in priority if n in self._providers] + [
                n for n in self._providers if n not in priority
            ]
        else:
            self._order = list(self._providers)

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

        errors: list[str] = []
        for prov in candidates:
            try:
                result = await getattr(prov, tool)(**kwargs)
                return prov.name, result
            except (ProviderError, httpx.HTTPError) as exc:
                errors.append(f"{prov.name}: {exc}")
        raise NoProviderAvailable(f"all providers failed for '{tool}': " + "; ".join(errors))

    async def aclose(self) -> None:
        for prov in self._providers.values():
            await prov.aclose()
