"""TinyFish provider: free search + fetch (real-browser render)."""

from __future__ import annotations

import httpx

from ..errors import ProviderError
from ..models import FetchedPage, SearchResult
from .base import BaseProvider

SEARCH = "https://api.search.tinyfish.ai"
FETCH = "https://api.fetch.tinyfish.ai"

# recency_minutes mapping for the unified `freshness` param
_FRESHNESS_MINUTES = {"hour": 60, "day": 1440, "week": 10080, "month": 43200, "year": 525600}


class TinyFishProvider(BaseProvider):
    name = "tinyfish"
    capabilities = frozenset({"search", "fetch"})

    def __init__(self, api_key: str | None, client: httpx.AsyncClient | None = None):
        super().__init__(client)
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    async def search(
        self,
        query: str,
 *,
        max_results: int = 5,
        freshness: str | None = None,
        location: str | None = None,
        language: str | None = None,
        domain_type: str = "web",
        **kw,
    ) -> list[SearchResult]:
        params: dict = {"query": query}
        if freshness and freshness in _FRESHNESS_MINUTES:
            params["recency_minutes"] = _FRESHNESS_MINUTES[freshness]
        if location:
            params["location"] = location
        if language:
            params["language"] = language
        if domain_type != "web":
            params["domain_type"] = domain_type
        data = await self._request_json("GET", SEARCH, params=params, headers=await self._headers())
        results = []
        for r in self._items(data.get("results"), "results")[:max_results]:
            results.append(
                SearchResult(
                    title=self._text(r.get("title"), "title"),
                    url=self._text(r.get("url"), "url"),
                    snippet=self._text(r.get("snippet"), "snippet"),
                )
            )
        return results

    async def fetch(self, url: str, *, format: str = "markdown", **kw) -> FetchedPage:
        data = await self._request_json(
            "POST", FETCH, json={"urls": [url], "format": format}, headers=await self._headers()
        )
        results = self._items(data.get("results"), "results")
        if results:
            r = results[0]
            return FetchedPage(
                url=self._text(r.get("url"), "url") or url,
                content=self._text(r.get("text"), "text"),
                title=r.get("title"),
                format=self._text(r.get("format"), "format") or format,
            )
        raise ProviderError(f"tinyfish fetch failed: {data.get('errors', [])}")
