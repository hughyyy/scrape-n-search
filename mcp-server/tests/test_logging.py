"""Log redaction: query-string keys and bearer tokens never reach handlers."""

from __future__ import annotations

import logging

import httpx
import pytest
from scrape_n_search.errors import ProviderError
from scrape_n_search.logging_utils import RedactFilter, redact
from scrape_n_search.providers.base import BaseProvider
from scrape_n_search.router import Router


def test_redact_query_keys():
    text = "POST https://publisher.scrappey.com/api/v1?key=SECRET123&x=1 failed"
    assert "SECRET123" not in redact(text)
    assert "key=***" in redact(text)
    assert "x=1" in redact(text)  # unrelated params survive


def test_redact_apikey_and_bearer():
    assert "SECRET" not in redact("https://api.zenrows.com/v1?apikey=SECRET&url=x")
    assert "SECRET" not in redact("Authorization: Bearer SECRETSECRET")
    assert "Authorization: Bearer ***" == redact("Authorization: Bearer SECRETSECRET")


def test_redact_leaves_ordinary_text():
    assert redact("provider exa failed 'search': boom") == "provider exa failed 'search': boom"


def test_redact_filter_rewrites_record():
    record = logging.LogRecord(
        "x", logging.WARNING, __file__, 1, "call %s", ("https://h/?apikey=SECRET",), None
    )
    assert RedactFilter().filter(record) is True
    assert record.getMessage() == "call https://h/?apikey=***"


class _KeyErrorProvider(BaseProvider):
    name = "leaky"
    capabilities = frozenset({"search"})

    async def search(self, query: str, **kw):
        url = "https://api.example/v1?key=SECRET123"
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            f"Server error for url '{url}'",
            request=request,
            response=httpx.Response(500, request=request),
        )


async def test_router_failover_log_redacts_key(caplog):
    with caplog.at_level(logging.WARNING, logger="scrape_n_search.router"):
        with pytest.raises(ProviderError):
            await Router([_KeyErrorProvider()]).run("search", query="q")
    assert "SECRET123" not in caplog.text
    assert "key=***" in caplog.text
