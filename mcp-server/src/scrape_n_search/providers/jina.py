"""Jina provider: keyless URL->markdown fetch and search (no anti-bot bypass).

Acts as a zero-cost fallback. Enabled by default without a key; disable with
JINA_KEYLESS=0. Search results come back as markdown and are parsed into the
normalized SearchResult shape.
"""

from __future__ import annotations

import re

import httpx

from ..errors import ProviderError
from ..models import FetchedPage, SearchResult
from .base import BaseProvider

R_READER = "https://r.jina.ai"
S_SEARCH = "https://s.jina.ai"


def _parse_search_markdown(text: str, max_results: int) -> list[SearchResult]:
    """Parse s.jina.ai output: 'Title: ...\\nURL: ...\\nDescription: ...' blocks."""
    results: list[SearchResult] = []
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        title = _field(block, "Title")
        url = _field(block, "URL")
        if not title and not url:
            continue
        results.append(
            SearchResult(
                title=title or url or "",
                url=url or "",
                snippet=_field(block, "Description"),
            )
        )
        if len(results) >= max_results:
            break
    return results


def _field(block: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


class JinaProvider(BaseProvider):
    name = "jina"
    capabilities = frozenset({"search", "fetch"})

    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        super().__init__(client)
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return True

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def fetch(self, url: str, *, format: str = "markdown", **kw) -> FetchedPage:
        resp = await self._client.get(f"{R_READER}/{url}", headers=await self._headers())
        resp.raise_for_status()
        return FetchedPage(url=url, content=resp.text, title=None, format="markdown")

    async def search(self, query: str, *, max_results: int = 5, **kw) -> list[SearchResult]:
        if not self.api_key:
            raise ProviderError(
                "jina search requires a JINA_API_KEY (s.jina.ai now requires authentication); "
                "keyless fetch via r.jina.ai still works"
            )
        resp = await self._client.get(S_SEARCH, params={"q": query}, headers=await self._headers())
        resp.raise_for_status()
        return _parse_search_markdown(resp.text, max_results)
