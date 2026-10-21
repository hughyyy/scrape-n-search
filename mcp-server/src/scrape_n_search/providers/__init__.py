"""Provider registry: builds configured providers from the environment."""

from __future__ import annotations

import os

import httpx

from .base import DEFAULT_TIMEOUT, BaseProvider
from .exa import ExaProvider
from .firecrawl import FirecrawlProvider
from .jina import JinaProvider
from .scrappey import ScrappeyProvider
from .serper import SerperProvider
from .tavily import TavilyProvider
from .tinyfish import TinyFishProvider
from .zenrows import ZenRowsProvider

# Default selection order: recommended stack first, cheap/fallback last.
DEFAULT_PRIORITY = ["tinyfish", "tavily", "exa", "zenrows", "scrappey", "firecrawl", "serper", "jina"]


def configured_timeout() -> httpx.Timeout:
    """HTTP timeout for provider clients, from the HTTP_TIMEOUT env var (seconds).

    Defaults to DEFAULT_TIMEOUT (60s total, 15s connect). Scrappey browser
    requests routinely take 20-40s — raise this (e.g. 120s) for long
    web_browser_actions sequences.
    """
    raw = os.environ.get("HTTP_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    total = float(raw)
    if total <= 0:
        raise ValueError(f"HTTP_TIMEOUT must be a positive number of seconds, got {raw!r}")
    return httpx.Timeout(total, connect=min(15.0, total))


def build_providers(client: httpx.AsyncClient | None = None) -> list[BaseProvider]:
    """Construct providers from environment variables.

    Providers whose API key is missing report `enabled == False` and are
    skipped by the router. Jina works without a key and is enabled unless
    JINA_KEYLESS=0.
    """
    jina_keyless = os.environ.get("JINA_KEYLESS", "1") != "0"
    providers: list[BaseProvider] = [
        TinyFishProvider(os.environ.get("TINYFISH_API_KEY"), client),
        TavilyProvider(os.environ.get("TAVILY_API_KEY"), client),
        ExaProvider(os.environ.get("EXA_API_KEY"), client),
        ZenRowsProvider(os.environ.get("ZENROWS_API_KEY"), client),
        ScrappeyProvider(os.environ.get("SCRAPPEY_API_KEY"), client),
        FirecrawlProvider(os.environ.get("FIRECRAWL_API_KEY"), client),
        SerperProvider(os.environ.get("SERPER_API_KEY"), client),
    ]
    if jina_keyless or os.environ.get("JINA_API_KEY"):
        providers.append(JinaProvider(os.environ.get("JINA_API_KEY") or None, client))
    return providers
