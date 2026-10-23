"""Abstract provider interface and shared HTTP helpers.

Every provider implements the subset of tools it supports (search, fetch,
crawl, extract) and normalizes responses into the common models. The router
picks the first enabled provider that supports the requested tool and fails
over to the next on error, so callers never depend on a specific provider.
"""

from __future__ import annotations

import httpx

from ..errors import ProviderError
from ..models import CrawlPage, FetchedPage, SearchResult

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class BaseProvider:
    name: str = ""
    capabilities: frozenset[str] = frozenset()
    # Formats this provider can actually produce from fetch(). Override with a
    # narrower set when the API only returns one shape (e.g. plain text).
    fetch_formats: frozenset[str] = frozenset({"markdown", "html"})

    def supports_fetch_format(self, format: str) -> bool:
        return format in self.fetch_formats

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        """True when the provider is configured and usable."""
        return True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _require(self, tool: str) -> None:
        if tool not in self.capabilities:
            raise ProviderError(f"provider '{self.name}' does not support '{tool}'")

    async def search(self, query: str, *, max_results: int = 5, **kw) -> list[SearchResult]:
        self._require("search")
        raise ProviderError(f"provider '{self.name}' search not implemented")

    async def fetch(self, url: str, *, format: str = "markdown", **kw) -> FetchedPage:
        self._require("fetch")
        raise ProviderError(f"provider '{self.name}' fetch not implemented")

    async def crawl(self, url: str, *, max_pages: int = 10, **kw) -> list[CrawlPage]:
        self._require("crawl")
        raise ProviderError(f"provider '{self.name}' crawl not implemented")

    async def extract(self, url: str, *, prompt: str | None = None, **kw):
        self._require("extract")
        raise ProviderError(f"provider '{self.name}' extract not implemented")

    async def session(self, op: str, session: str, **kw):
        self._require("session")
        raise ProviderError(f"provider '{self.name}' session not implemented")

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        resp = await self._client.request(method, url, params=params, json=json, headers=headers)
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            # 200 with an HTML challenge/error page is a provider fault; it must
            # fail over, not escape as a JSONDecodeError.
            raise ProviderError(f"provider '{self.name}' returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise ProviderError(
                f"provider '{self.name}' returned a non-object JSON response"
            )
        return payload

    def _items(self, value: object, label: str) -> list[dict]:
        """Response list field: absent/None means no items.

        A non-list, or a list holding non-objects, is a provider fault — raising
        ProviderError lets the router fail over instead of crashing on
        ``r.get(...)``.
        """
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ProviderError(f"provider '{self.name}' returned a malformed '{label}' field")
        return value

    def _object(self, data: dict, label: str) -> dict:
        """Object field: absent/None means empty; any other shape is a fault."""
        value = data.get(label)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ProviderError(f"provider '{self.name}' returned a malformed '{label}' field")
        return value

    def _text(self, value: object, label: str) -> str:
        """String field: absent/None means empty; any other shape is a fault."""
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ProviderError(f"provider '{self.name}' returned a non-string '{label}' field")
        return value
