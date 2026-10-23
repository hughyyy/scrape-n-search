"""Firecrawl provider: scrape, search, crawl, and extract."""

from __future__ import annotations

import asyncio

import httpx

from ..errors import ProviderError
from ..models import CrawlPage, FetchedPage, SearchResult
from .base import BaseProvider

BASE = "https://api.firecrawl.dev/v2"

_FRESHNESS_TBS = {"hour": "qdr:h", "day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


class FirecrawlProvider(BaseProvider):
    name = "firecrawl"
    capabilities = frozenset({"search", "fetch", "crawl", "extract"})

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
        freshness: str | None = None,
        country: str | None = None,
        location: str | None = None,
        **kw,
    ) -> list[SearchResult]:
        body: dict = {"query": query, "limit": max_results, "sources": [{"type": "web"}]}
        if freshness and freshness in _FRESHNESS_TBS:
            body["tbs"] = _FRESHNESS_TBS[freshness]
        if country:
            body["country"] = country
        if location:
            body["location"] = location
        data = await self._request_json("POST", f"{BASE}/search", json=body, headers=await self._headers())
        raw = data.get("data")
        if isinstance(raw, dict):
            raw = raw.get("web")
        results = []
        for r in self._items(raw, "data"):
            results.append(
                SearchResult(
                    title=self._text(r.get("title"), "title"),
                    url=self._text(r.get("url"), "url"),
                    snippet=self._text(r.get("description"), "description"),
                )
            )
        return results

    async def fetch(self, url: str, *, format: str = "markdown", **kw) -> FetchedPage:
        fmt = "markdown" if format not in ("markdown", "html") else format
        body = {"url": url, "formats": [{"type": fmt}], "onlyMainContent": True}
        data = await self._request_json("POST", f"{BASE}/scrape", json=body, headers=await self._headers())
        d = self._object(data, "data")
        content = self._text(d.get("markdown") or d.get("html"), "content")
        return FetchedPage(
            url=self._text(d.get("url"), "url") or url,
            content=content,
            title=d.get("title"),
            format=fmt,
        )

    async def crawl(
        self,
        url: str,
        *,
        max_pages: int = 10,
        max_depth: int = 2,
        poll_interval: float = 2.0,
        timeout: float = 120.0,
        **kw,
    ) -> list[CrawlPage]:
        body = {"url": url, "limit": max_pages, "maxDepth": max_depth}
        data = await self._request_json("POST", f"{BASE}/crawl", json=body, headers=await self._headers())
        job_id = data.get("id")
        if not job_id:
            raise ProviderError(f"firecrawl crawl returned no job id: {data}")
        elapsed = 0.0
        # Wall-clock deadline: accumulating elapsed by poll_interval would spin
        # forever when the interval is zero.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(max(poll_interval, 0))
            elapsed += poll_interval
            status = await self._request_json("GET", f"{BASE}/crawl/{job_id}", headers=await self._headers())
            state = status.get("status")
            if state == "completed":
                pages = []
                for p in self._items(status.get("data"), "data"):
                    pages.append(
                        CrawlPage(
                            url=self._text(p.get("url"), "url"),
                            title=p.get("title"),
                            content=p.get("markdown"),
                        )
                    )
                return pages
            if state in ("failed", "cancelled"):
                raise ProviderError(f"firecrawl crawl {state}: {status}")
        raise ProviderError("firecrawl crawl timed out")

    async def extract(self, url: str, *, prompt: str | None = None, **kw):
        body: dict = {"urls": [url]}
        if prompt:
            body["prompt"] = prompt
        data = await self._request_json("POST", f"{BASE}/extract", json=body, headers=await self._headers())
        return data.get("data")
