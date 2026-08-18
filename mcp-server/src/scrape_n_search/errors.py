"""Provider error types."""

from __future__ import annotations


class ProviderError(Exception):
    """A provider failed to fulfill a request."""


class NoProviderAvailable(ProviderError):
    """No configured provider can serve the requested tool."""
