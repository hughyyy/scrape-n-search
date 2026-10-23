"""Firecrawl provider: scrape, search, crawl, and extract."""

from __future__ import annotations

import asyncio

import httpx

from ..errors import ProviderError
from ..models import CrawlPage, FetchedPage, SearchResult
from .base import BaseProvider

BASE = "https://api.firecrawl.dev/v2"

_FRESHNESS_TBS = {"hour": "qdr:h", "day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}

# Floor for poll sleeps: a zero interval would otherwise busy-loop requests.
_MIN_POLL_INTERVAL = 0.25


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
        # v2 nests page metadata under data.metadata (title may be a list).
        metadata = self._object(d, "metadata")
        title = metadata.get("title")
        if isinstance(title, list):
            title = " ".join(str(part) for part in title)
        final_url = metadata.get("url") or metadata.get("sourceURL")
        return FetchedPage(
            url=self._text(final_url, "url") or url,
            content=content,
            title=self._text(title, "title") or None,
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
        # Wall-clock deadline: accumulating elapsed by poll_interval would spin
        # forever when the interval is zero; a floor also avoids hot-looping.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(max(poll_interval, _MIN_POLL_INTERVAL))
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

    async def extract(
        self,
        url: str,
        *,
        prompt: str | None = None,
        poll_interval: float = 2.0,
        timeout: float = 120.0,
        **kw,
    ):
        body: dict = {"urls": [url]}
        if prompt:
            body["prompt"] = prompt
        data = await self._request_json(
            "POST", f"{BASE}/extract", json=body, headers=await self._headers()
        )
        job_id = data.get("id")
        if not job_id:
            # Synchronous/legacy deployments answer with the data inline.
            return data.get("data")
        # v2 extract is a job: submit returns an id and the data arrives via
        # GET /extract/{id} once the status is "completed".
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(max(poll_interval, _MIN_POLL_INTERVAL))
            status = await self._request_json(
                "GET", f"{BASE}/extract/{job_id}", headers=await self._headers()
            )
            state = status.get("status")
            if state == "completed":
                return status.get("data")
            if state in ("failed", "cancelled"):
                raise ProviderError(f"firecrawl extract {state}: {status}")
        raise ProviderError("firecrawl extract timed out")
