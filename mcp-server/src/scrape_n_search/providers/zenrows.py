"""ZenRows provider: credit-based fetch + auto-extract (anti-bot / JS pages)."""

from __future__ import annotations

import httpx

from ..errors import ProviderError
from ..models import FetchedPage
from .base import BaseProvider

BASE = "https://api.zenrows.com/v1"


class ZenRowsProvider(BaseProvider):
    name = "zenrows"
    capabilities = frozenset({"fetch", "extract"})

    def __init__(self, api_key: str | None, client: httpx.AsyncClient | None = None):
        super().__init__(client)
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def fetch(
        self,
        url: str,
 *,
        format: str = "markdown",
        js_render: bool = False,
        premium_proxy: bool = False,
        mode: str | None = None,
        proxy_country: str | None = None,
        **kw,
    ) -> FetchedPage:
        params: dict = {"apikey": self.api_key, "url": url}
        if format == "markdown":
            params["response_type"] = "markdown"
        elif format == "text":
            params["response_type"] = "plaintext"
        # html is the default; leave response_type unset
        if js_render:
            params["js_render"] = "true"
        if premium_proxy:
            params["premium_proxy"] = "true"
        if mode:
            params["mode"] = mode
        if proxy_country:
            params["proxy_country"] = proxy_country
        resp = await self._client.get(f"{BASE}/", params=params)
        resp.raise_for_status()
        final_url = resp.headers.get("Zr-Final-Url", url)
        return FetchedPage(url=final_url, content=resp.text, title=None, format=format)

    async def extract(self, url: str, *, prompt: str | None = None, **kw):
        params = {"apikey": self.api_key, "url": url, "extract": "auto"}
        resp = await self._client.get(f"{BASE}/extract", params=params)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"provider '{self.name}' returned a non-JSON response") from exc
