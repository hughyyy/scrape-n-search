"""Entrypoint: run the MCP server over stdio."""

from __future__ import annotations

import os

from .providers import DEFAULT_PRIORITY, build_providers
from .router import Router
from .server import build_server


def build_router_from_env() -> Router:
    providers = build_providers()
    priority_raw = os.environ.get("PROVIDER_PRIORITY")
    priority = [p.strip() for p in priority_raw.split(",") if p.strip()] if priority_raw else DEFAULT_PRIORITY
    return Router(providers, priority=priority)


def main() -> None:
    router = build_router_from_env()
    server = build_server(router)
    # Provider clients are closed by the server lifespan (same event loop).
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
