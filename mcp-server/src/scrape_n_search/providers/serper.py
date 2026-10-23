"""Serper provider: cheap Google SERP search (no content fetch)."""

from __future__ import annotations

import httpx

from ..models import SearchResult
from .base import BaseProvider

BASE = "https://google.serper.dev"


class SerperProvider(BaseProvider):
    name = "serper"
    capabilities = frozenset({"search"})

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
        country: str | None = None,
        language: str | None = None,
        **kw,
    ) -> list[SearchResult]:
        body: dict = {"q": query, "num": max_results}
        if country:
            body["gl"] = country
        if language:
            body["hl"] = language
        data = await self._request_json("POST", f"{BASE}/search", json=body, headers={"X-API-KEY": self.api_key})
        results = []
        for r in self._items(data.get("organic"), "organic")[:max_results]:
            results.append(
                SearchResult(
                    title=self._text(r.get("title"), "title"),
                    url=self._text(r.get("link"), "link"),
                    snippet=self._text(r.get("snippet"), "snippet"),
                    published_date=r.get("date"),
                )
            )
        return results
