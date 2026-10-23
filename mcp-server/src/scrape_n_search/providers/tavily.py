"""Tavily provider: agent-native search + extract (content fetch)."""

from __future__ import annotations

import httpx

from ..errors import ProviderError
from ..models import FetchedPage, SearchResult
from .base import BaseProvider

BASE = "https://api.tavily.com"

# Tavily's time_range has no "hour"; the closest supported window is a day.
_TIME_RANGE = {"hour": "day", "day": "day", "week": "week", "month": "month", "year": "year"}


class TavilyProvider(BaseProvider):
    name = "tavily"
    capabilities = frozenset({"search", "fetch"})
    fetch_formats = frozenset({"markdown"})

    def __init__(self, api_key: str | None, client: httpx.AsyncClient | None = None):
        super().__init__(client)
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(
        self,
        query: str,
 *,
        max_results: int = 5,
        search_depth: str = "basic",
        topic: str = "general",
        freshness: str | None = None,
        country: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        **kw,
    ) -> list[SearchResult]:
        body: dict = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "topic": topic,
        }
        if freshness:
            body["time_range"] = _TIME_RANGE.get(freshness, freshness)
        if country:
            body["country"] = country
        if include_domains:
            body["include_domains"] = include_domains
        if exclude_domains:
            body["exclude_domains"] = exclude_domains
        data = await self._request_json("POST", f"{BASE}/search", json=body)
        results = []
        for r in self._items(data.get("results"), "results"):
            results.append(
                SearchResult(
                    title=self._text(r.get("title"), "title"),
                    url=self._text(r.get("url"), "url"),
                    snippet=self._text(r.get("content"), "content"),
                    published_date=r.get("published_date"),
                    score=r.get("score"),
                )
            )
        return results

    async def fetch(self, url: str, *, format: str = "markdown", **kw) -> FetchedPage:
        body = {"api_key": self.api_key, "urls": [url], "extract_depth": "advanced"}
        data = await self._request_json("POST", f"{BASE}/extract", json=body)
        results = self._items(data.get("results"), "results")
        if not results:
            raise ProviderError(f"tavily extract failed: {data.get('failed_results', [])}")
        r = results[0]
        return FetchedPage(
            url=self._text(r.get("url"), "url") or url,
            content=self._text(r.get("raw_content"), "raw_content"),
            title=r.get("title"),
            format="markdown",
        )
