#!/usr/bin/env python3
"""Verify a provider API key with a single minimal real call (stdlib only).

Usage:
    python3 check_keys.py <provider> <key>

Exits 0 and prints "OK <detail>" when the provider accepts the key;
exits 1 and prints "FAIL <detail>" otherwise.

Endpoints, auth headers, and request bodies mirror the adapters in
mcp-server/src/scrape_n_search/providers/*.py, so this verifies the exact
code path the server uses. Costs at most a fraction of a credit per check
(e.g. Scrappey's balance call is free; search providers burn ~1 search unit).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 25

# provider -> check function name
CHECKS = (
    "tinyfish",
    "tavily",
    "exa",
    "zenrows",
    "scrappey",
    "firecrawl",
    "serper",
    "jina",
)


def _request(method: str, url: str, *, headers: dict | None = None, body: dict | None = None):
    """Return (status, body_text). Raises SystemExit(1) on network failure."""
    data = json.dumps(body).encode() if body is not None else None
    head = dict(headers or {})
    if data is not None:
        head.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, raw
    except Exception as e:  # noqa: BLE001 - any network error is a failed probe
        raise SystemExit(f"FAIL network error: {e}") from e


def _rejected(status: int) -> bool:
    return status in (401, 403)


def _ok(provider: str, detail: str = "") -> None:
    print(f"OK {provider} key verified" + (f" ({detail})" if detail else ""))
    sys.exit(0)


def _fail(provider: str, msg: str) -> None:
    print(f"FAIL {provider}: {msg}")
    sys.exit(1)


def check_tinyfish(key: str) -> None:
    status, raw = _request(
        "GET", "https://api.search.tinyfish.ai?query=test", headers={"X-API-Key": key}
    )
    if _rejected(status) or status != 200:
        _fail("tinyfish", f"rejected with HTTP {status}: {raw[:120]}")
    _ok("tinyfish")


def check_tavily(key: str) -> None:
    status, raw = _request(
        "POST",
        "https://api.tavily.com/search",
        body={"api_key": key, "query": "test", "max_results": 1, "search_depth": "basic"},
    )
    if _rejected(status):
        _fail("tavily", f"rejected with HTTP {status} (invalid or expired key)")
    if status != 200:
        _fail("tavily", f"unexpected HTTP {status}: {raw[:120]}")
    _ok("tavily")


def check_exa(key: str) -> None:
    status, raw = _request(
        "POST",
        "https://api.exa.ai/search",
        headers={"Authorization": f"Bearer {key}"},
        body={"query": "test", "numResults": 1, "contents": {"text": False}},
    )
    if _rejected(status):
        _fail("exa", f"rejected with HTTP {status} (invalid or expired key)")
    if status != 200:
        _fail("exa", f"unexpected HTTP {status}: {raw[:120]}")
    _ok("exa")


def check_zenrows(key: str) -> None:
    # A valid key proxies the target (any status); only auth failures mean a bad key.
    status, raw = _request("GET", f"https://api.zenrows.com/v1/?apikey={key}&url=https://example.com")
    if _rejected(status):
        _fail("zenrows", f"rejected with HTTP {status} (invalid or expired key)")
    if status not in (200, 404, 410):
        _fail("zenrows", f"unexpected HTTP {status}: {raw[:120]}")
    _ok("zenrows")


def check_scrappey(key: str) -> None:
    # Balance lookup is free - no scrape cost for the probe.
    status, raw = _request("GET", f"https://publisher.scrappey.com/api/v1/balance?key={key}")
    if status != 200:
        _fail("scrappey", f"rejected with HTTP {status}")
    try:
        balance = json.loads(raw).get("balance")
    except (json.JSONDecodeError, AttributeError):
        _fail("scrappey", f"unexpected response: {raw[:120]}")
    if balance is None:
        _fail("scrappey", f"no balance in response: {raw[:120]}")
    _ok("scrappey", f"balance {balance}")


def check_firecrawl(key: str) -> None:
    status, raw = _request(
        "POST",
        "https://api.firecrawl.dev/v2/search",
        headers={"Authorization": f"Bearer {key}"},
        body={"query": "test", "limit": 1, "sources": [{"type": "web"}]},
    )
    if _rejected(status):
        _fail("firecrawl", f"rejected with HTTP {status} (invalid or expired key)")
    if status != 200:
        _fail("firecrawl", f"unexpected HTTP {status}: {raw[:120]}")
    _ok("firecrawl")


def check_serper(key: str) -> None:
    status, raw = _request(
        "POST",
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key},
        body={"q": "test", "num": 1},
    )
    if _rejected(status):
        _fail("serper", f"rejected with HTTP {status} (invalid or expired key)")
    if status != 200:
        _fail("serper", f"unexpected HTTP {status}: {raw[:120]}")
    _ok("serper")


def check_jina(key: str) -> None:
    # s.jina.ai requires a key; r.jina.ai fetch is keyless, so this checks search access.
    status, raw = _request(
        "GET", "https://s.jina.ai?q=test", headers={"Authorization": f"Bearer {key}"}
    )
    if _rejected(status):
        _fail("jina", f"rejected with HTTP {status} (invalid or expired key)")
    if status != 200:
        _fail("jina", f"unexpected HTTP {status}: {raw[:120]}")
    _ok("jina")


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: check_keys.py <provider> <key>")
        print(f"providers: {', '.join(CHECKS)}")
        sys.exit(2)
    provider, key = sys.argv[1], sys.argv[2]
    if provider not in CHECKS:
        print(f"FAIL unknown provider '{provider}' (known: {', '.join(CHECKS)})")
        sys.exit(2)
    if not key or len(key) < 8:
        print("FAIL key looks too short to be valid")
        sys.exit(2)
    globals()[f"check_{provider}"](key)


if __name__ == "__main__":
    main()
