"""Scrappey provider: pay-as-you-go real-browser fetch.

Cheapest scraping option in the set (€0.10/1K direct, €1/1K full browser),
no subscription, balance never expires, residential proxies included. Fetch
runs in a real browser with session/proxy/verification handling; returns HTML
or LLM-ready Markdown. Only bills successful requests.
"""

from __future__ import annotations

import httpx

from ..errors import ProviderError
from ..models import FetchedPage
from .base import BaseProvider

BASE = "https://publisher.scrappey.com/api/v1"


class ScrappeyProvider(BaseProvider):
    name = "scrappey"
    capabilities = frozenset({"fetch"})

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
        js_render: bool = True,
        premium_proxy: bool = True,
        proxy_country: str | None = None,
        **kw,
    ) -> FetchedPage:
        want_markdown = format == "markdown"
        browser = js_render
        body: dict = {"cmd": "request.get", "url": url}
        # "browser" renders JS and can convert to markdown (€1/1K);
        # "request" is direct HTTP with no rendering, returns raw HTML (€0.10/1K).
        # Markdown conversion is a browser-only feature, so basic mode never
        # requests it and returns the raw response instead.
        body["requestType"] = "browser" if browser else "request"
        if browser and want_markdown:
            body["markdown"] = True
            body["filter"] = ["markdown"]
        # Residential proxies are included on both modes, so premium_proxy is
        # always effectively enabled; the flag is accepted for interface parity.
        if proxy_country:
            body["proxyCountry"] = proxy_country

        data = await self._request_json("POST", f"{BASE}?key={self.api_key}", json=body)
        if data.get("data") != "success":
            raise ProviderError(f"scrappey fetch failed: {data.get('data', data)}")
        solution = data.get("solution", {})
        if browser and want_markdown:
            content = solution.get("markdown") or solution.get("innerText") or ""
            out_format = "markdown"
        else:
            content = solution.get("response") or solution.get("innerText") or ""
            out_format = "html"
        return FetchedPage(
            url=solution.get("currentUrl") or url,
            content=content,
            title=None,
            format=out_format,
        )
