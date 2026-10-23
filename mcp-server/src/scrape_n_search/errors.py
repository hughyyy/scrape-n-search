"""Provider and routing error types.

The router distinguishes *caller errors* (a bad provider name, a disabled
provider, an unsupported capability or format) from *environment errors* (no
provider configured for the capability, every candidate failing). The server
uses the distinction to decide whether a call may fall through to the gated
emulation path: only environment errors are eligible, so a typo'd provider
name is reported verbatim instead of being answered with an explanation that
claims nothing is configured.
"""

from __future__ import annotations


class ProviderError(Exception):
    """A provider failed to fulfill a request."""


class NoProviderAvailable(ProviderError):
    """Base class: the router could not dispatch the call."""


class UnknownTool(NoProviderAvailable):
    """The requested capability is not a router tool."""


class UnknownProvider(NoProviderAvailable):
    """The caller named a provider the router does not know."""


class ProviderDisabled(NoProviderAvailable):
    """The caller named a provider that is not configured (no API key)."""


class UnsupportedCapability(NoProviderAvailable):
    """The caller pinned a provider that cannot serve this capability/format."""


class NoProviderConfigured(NoProviderAvailable):
    """No enabled provider supports the requested capability/format."""


class AllProvidersFailed(NoProviderAvailable):
    """Every configured candidate failed; the environment could not serve it."""
