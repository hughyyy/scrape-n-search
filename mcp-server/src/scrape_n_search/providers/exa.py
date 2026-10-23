"""Exa provider: neural search + contents (content fetch)."""

from __future__ import annotations

import httpx

from ..errors import ProviderError
from ..models import FetchedPage, SearchResult
from .base import BaseProvider

BASE = "https://api.exa.ai"


class ExaProvider(BaseProvider):
    name = "exa"
    capabilities = frozenset({"search", "fetch"})

    def __init__(self, api_key: str | None, client: httpx.AsyncClient | None = None):
        super().__init__(client)
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def search(
        self,
        query: str,
 *,
        max_results: int = 5,
        search_type: str = "auto",
        category: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        **kw,
    ) -> list[SearchResult]:
        body: dict = {"query": query, "numResults": max_results, "contents": {"text": True}}
        if search_type:
            body["type"] = search_type
        if category:
            body["category"] = category
        if include_domains:
            body["includeDomains"] = include_domains
        if exclude_domains:
            body["excludeDomains"] = exclude_domains
        data = await self._request_json("POST", f"{BASE}/search", json=body, headers=await self._headers())
        results = []
        for r in data.get("results", []):
            snippet = r.get("text") or ""
            if not snippet and isinstance(r.get("highlights"), list):
                # the Exa API returns highlights as a list; flatten to text so
                # snippet keeps the unified string contract
                snippet = " ".join(str(h) for h in r["highlights"])
            results.append(
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=snippet[:500],
                    published_date=r.get("publishedDate"),
                    score=r.get("score"),
                )
            )
        return results

    async def fetch(self, url: str, *, format: str = "markdown", **kw) -> FetchedPage:
        data = await self._request_json(
            "POST", f"{BASE}/contents", json={"ids": [url], "text": True}, headers=await self._headers()
        )
        results = data.get("results", [])
        if not results:
            raise ProviderError("exa contents returned no result")
        r = results[0]
        return FetchedPage(
            url=r.get("url", url),
            content=r.get("text", ""),
            title=r.get("title"),
            format="markdown",
        )
