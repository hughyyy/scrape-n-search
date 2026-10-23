"""Entrypoint: run the MCP server over stdio."""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .emulation import _llm_max_chars
from .logging_utils import RedactFilter
from .providers import DEFAULT_PRIORITY, build_providers, configured_timeout
from .router import Router
from .server import build_server

# Directory containing this package's project (…/mcp-server), used to locate
# .env regardless of the process working directory. Overridable for wheel
# installs, where it cannot be derived from the installed package path.
PROJECT_DIR = Path(
    os.environ.get("SCRAPE_N_SEARCH_ENV_DIR") or Path(__file__).resolve().parent.parent.parent
)


def log_level_from_env() -> int:
    """Logging level from LOG_LEVEL (default INFO)."""
    raw = os.environ.get("LOG_LEVEL", "INFO")
    level = getattr(logging, raw.upper(), None)
    if not isinstance(level, int):
        raise ValueError(
            f"LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL, got {raw!r}"
        )
    return level


def configure_logging() -> None:
    logging.basicConfig(
        level=log_level_from_env(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Provider keys ride in URLs, so httpx/router log lines must be redacted
    # before any handler formats them.
    for handler in logging.getLogger().handlers:
        if not any(isinstance(existing, RedactFilter) for existing in handler.filters):
            handler.addFilter(RedactFilter())


def build_router_from_env() -> Router:
    # One shared HTTP client for all providers (connection pooling + a single
    # close point); the Router closes it in its lifespan teardown.
    client = httpx.AsyncClient(timeout=configured_timeout())
    providers = build_providers(client)
    priority_raw = os.environ.get("PROVIDER_PRIORITY")
    priority = [p.strip() for p in priority_raw.split(",") if p.strip()] if priority_raw else DEFAULT_PRIORITY
    cache_ttl = float(os.environ.get("CACHE_TTL") or 0)
    if not math.isfinite(cache_ttl) or cache_ttl < 0:
        raise ValueError("CACHE_TTL must be a finite, non-negative number of seconds")
    return Router(providers, priority=priority, client=client, cache_ttl=cache_ttl)


def build_server_from_env(router: Router):
    """Configure the server from environment variables.

    - DEFAULT_PROVIDER: provider used when the caller does not specify one;
      tools it cannot serve are hidden.
    - ENABLED_TOOLS: explicit comma-separated allow-list of tool names
      (overrides the default-provider-derived set).
    """
    default_provider = os.environ.get("DEFAULT_PROVIDER") or None
    enabled_raw = os.environ.get("ENABLED_TOOLS")
    enabled_tools = {t.strip() for t in enabled_raw.split(",") if t.strip()} if enabled_raw else None
    return build_server(router, default_provider=default_provider, enabled_tools=enabled_tools)


def main() -> None:
    # Load .env from the working directory (or its parents), then fall back to
    # the project's own .env. Existing environment variables take precedence and
    # are not overwritten.
    load_dotenv()
    load_dotenv(PROJECT_DIR / ".env")
    # Fail loudly at startup on a malformed numeric knob, not on first use.
    if os.environ.get("EXTRACT_MAX_CHARS"):
        _llm_max_chars()
    configure_logging()
    router = build_router_from_env()
    server = build_server_from_env(router)
    # Provider clients are closed by the server lifespan (same event loop).
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
