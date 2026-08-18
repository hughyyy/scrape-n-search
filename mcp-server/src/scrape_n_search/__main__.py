"""Entrypoint: run the MCP server over stdio."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .providers import DEFAULT_PRIORITY, build_providers
from .router import Router
from .server import build_server

# Directory containing this package's project (…/mcp-server), used to locate
# .env regardless of the process working directory.
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent


def build_router_from_env() -> Router:
    providers = build_providers()
    priority_raw = os.environ.get("PROVIDER_PRIORITY")
    priority = [p.strip() for p in priority_raw.split(",") if p.strip()] if priority_raw else DEFAULT_PRIORITY
    return Router(providers, priority=priority)


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
    router = build_router_from_env()
    server = build_server_from_env(router)
    # Provider clients are closed by the server lifespan (same event loop).
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
