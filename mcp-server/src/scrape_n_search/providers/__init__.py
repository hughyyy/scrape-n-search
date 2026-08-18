"""Provider registry: builds configured providers from the environment."""

from __future__ import annotations

import os

import httpx

from .base import BaseProvider
from .exa import ExaProvider
from .firecrawl import FirecrawlProvider
from .jina import JinaProvider
from .serper import SerperProvider
from .tavily import TavilyProvider
from .tinyfish import TinyFishProvider
from .zenrows import ZenRowsProvider

# Default selection order: recommended stack first, cheap/fallback last.
DEFAULT_PRIORITY = ["tinyfish", "tavily", "exa", "zenrows", "firecrawl", "serper", "jina"]


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
        FirecrawlProvider(os.environ.get("FIRECRAWL_API_KEY"), client),
        SerperProvider(os.environ.get("SERPER_API_KEY"), client),
    ]
    if jina_keyless or os.environ.get("JINA_API_KEY"):
        providers.append(JinaProvider(os.environ.get("JINA_API_KEY") or None, client))
    return providers
