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
        raw = data.get("data", {})
        items = raw.get("web") if isinstance(raw, dict) else raw
        results = []
        for r in items or []:
            results.append(
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("description", ""),
                )
            )
        return results

    async def fetch(self, url: str, *, format: str = "markdown", **kw) -> FetchedPage:
        fmt = "markdown" if format not in ("markdown", "html") else format
        body = {"url": url, "formats": [{"type": fmt}], "onlyMainContent": True}
        data = await self._request_json("POST", f"{BASE}/scrape", json=body, headers=await self._headers())
        d = data.get("data", {})
        content = d.get("markdown") or d.get("html") or ""
        return FetchedPage(
            url=d.get("url", url),
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
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            status = await self._request_json("GET", f"{BASE}/crawl/{job_id}", headers=await self._headers())
            state = status.get("status")
            if state == "completed":
                pages = []
                for p in status.get("data", []):
                    pages.append(
                        CrawlPage(
                            url=p.get("url", ""),
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
