"""Scrappey provider: pay-as-you-go real-browser fetch.

Cheapest scraping option in the set (€0.10/1K direct, €1/1K full browser),
no subscription, balance never expires, residential proxies included. Fetch
runs in a real browser with session/proxy/verification handling; returns HTML
or LLM-ready Markdown. Only bills successful requests.
"""

from __future__ import annotations

import httpx

from ..errors import ProviderError
from ..htmltext import html_to_text
from ..models import BrowserActionsResult, FetchedPage
from .base import BaseProvider

BASE = "https://publisher.scrappey.com/api/v1"


class ScrappeyProvider(BaseProvider):
    name = "scrappey"
    capabilities = frozenset({"fetch", "browser_actions", "screenshot", "session"})
    fetch_formats = frozenset({"markdown", "html"})

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
        session: str | None = None,
        **kw,
    ) -> FetchedPage:
        want_markdown = format == "markdown"
        browser = js_render
        body: dict = {"cmd": "request.get", "url": url}
        # "browser" renders JS and can convert to markdown (€1/1K);
        # "request" is direct HTTP with no rendering, returns raw HTML (€0.10/1K).
        # Markdown conversion is a browser-only feature, so basic mode never
        # requests it; a markdown request is honored locally below by reducing
        # the raw HTML to readable text.
        body["requestType"] = "browser" if browser else "request"
        if browser and want_markdown:
            body["markdown"] = True
            body["filter"] = ["markdown"]
        # Residential proxies are included on both modes, so premium_proxy is
        # always effectively enabled; the flag is accepted for interface parity.
        if proxy_country:
            body["proxyCountry"] = proxy_country
        if session:
            body["session"] = session

        data = await self._request_json("POST", f"{BASE}?key={self.api_key}", json=body)
        if data.get("data") != "success":
            raise ProviderError(f"scrappey fetch failed: {data.get('data', data)}")
        solution = self._object(data, "solution")
        if browser and want_markdown:
            content = self._text(
                solution.get("markdown") or solution.get("innerText"), "markdown"
            )
            out_format = "markdown"
        else:
            raw = self._text(
                solution.get("response") or solution.get("innerText"), "response"
            )
            content = html_to_text(raw) if want_markdown else raw
            out_format = "markdown" if want_markdown else "html"
        return FetchedPage(
            url=self._text(solution.get("currentUrl"), "currentUrl") or url,
            content=content,
            title=None,
            format=out_format,
        )

    async def browser_actions(
        self,
        url: str,
        *,
        actions: list[dict],
        format: str = "markdown",
        proxy_country: str | None = None,
        session: str | None = None,
        **kw,
    ) -> BrowserActionsResult:
        """Run a sequence of browser actions on a page (full browser mode).

        ``actions`` is a list of Scrappey browser-action objects (click, type,
        scroll, execute_js, wait, …). ``execute_js`` results are returned in
        ``javascript_return``; the rendered page content is also returned.
        """
        if not actions:
            raise ProviderError("scrappey browser_actions: actions list is empty")
        want_markdown = format == "markdown"
        body: dict = {
            "cmd": "request.get",
            "url": url,
            "requestType": "browser",
            "browserActions": actions,
        }
        if want_markdown:
            body["markdown"] = True
            body["filter"] = ["markdown"]
        if proxy_country:
            body["proxyCountry"] = proxy_country
        if session:
            body["session"] = session

        data = await self._request_json("POST", f"{BASE}?key={self.api_key}", json=body)
        if data.get("data") != "success":
            raise ProviderError(f"scrappey browser_actions failed: {data.get('data', data)}")
        solution = self._object(data, "solution")
        if want_markdown:
            content = self._text(
                solution.get("markdown") or solution.get("innerText"), "markdown"
            )
        else:
            content = self._text(
                solution.get("response") or solution.get("innerText"), "response"
            )
        javascript_return = solution.get("javascriptReturn") or []
        if not isinstance(javascript_return, list):
            raise ProviderError(
                f"provider '{self.name}' returned a malformed 'javascriptReturn' field"
            )
        return BrowserActionsResult(
            url=self._text(solution.get("currentUrl"), "currentUrl") or url,
            content=content,
            format="markdown" if want_markdown else "html",
            javascript_return=javascript_return,
        )

    async def screenshot(
        self,
        url: str,
        *,
        full_page: bool = False,
        selector: str | None = None,
        proxy_country: str | None = None,
        session: str | None = None,
        **kw,
    ) -> str:
        """Capture a screenshot (full browser mode). Returns base64-encoded image."""
        body: dict = {"cmd": "request.get", "url": url, "requestType": "browser"}
        if full_page:
            body["screenshot_fullpage"] = True
        elif selector:
            body["screenshot_selector"] = selector
        else:
            body["screenshot"] = True
        if proxy_country:
            body["proxyCountry"] = proxy_country
        if session:
            body["session"] = session

        data = await self._request_json("POST", f"{BASE}?key={self.api_key}", json=body)
        if data.get("data") != "success":
            raise ProviderError(f"scrappey screenshot failed: {data.get('data', data)}")
        solution = self._object(data, "solution")
        img = solution.get("screenshot")
        if not img:
            raise ProviderError("scrappey screenshot: no screenshot returned in response")
        if not isinstance(img, str):
            raise ProviderError(
                f"provider '{self.name}' returned a non-string 'screenshot' field"
            )
        return img

    async def session(self, op: str, session: str, **kw) -> dict:
        """Create or destroy a browser session.

        ``create`` returns success immediately; ``destroy`` shuts the session
        down. Request-level reuse is not yet reliable: live testing (2026-10)
        shows Scrappey destroys a named session when a request.get completes,
        so cookies/state do not persist across requests despite the vendor
        docs.
        """
        bodies = {"create": "sessions.create", "destroy": "sessions.destroy"}
        if op not in bodies:
            raise ProviderError(
                f"scrappey session: unknown op '{op}' (expected 'create' or 'destroy')"
            )
        if not session:
            raise ProviderError("scrappey session: session name is required")
        data = await self._request_json(
            "POST", f"{BASE}?key={self.api_key}", json={"cmd": bodies[op], "session": session}
        )
        if data.get("data") != "success":
            raise ProviderError(f"scrappey session {op} failed: {data.get('data', data)}")
        return {"op": op, "session": session, "solution": self._object(data, "solution")}
