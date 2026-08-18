"""Shared test helpers."""

from __future__ import annotations

import httpx


def make_client(handler) -> httpx.AsyncClient:
    """Build an AsyncClient backed by a MockTransport for offline provider tests."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
