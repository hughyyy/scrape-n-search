"""Tests for the stdlib key-probe script (scripts/check_keys.py)."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

import check_keys  # noqa: E402  (import after path setup)


class _Response:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch(monkeypatch, handler):
    monkeypatch.setattr(check_keys.urllib.request, "urlopen", handler)


def test_request_success_shape(monkeypatch):
    seen = {}

    def handler(request, timeout):
        seen["method"] = request.get_method()
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["body"] = request.data
        seen["timeout"] = timeout
        return _Response(200, b'{"ok": true}')

    _patch(monkeypatch, handler)
    status, body = check_keys._request(
        "POST", "https://example.com/x", headers={"X-API-KEY": "k"}, body={"a": 1}
    )
    assert (status, body) == (200, '{"ok": true}')
    assert seen["method"] == "POST"
    assert seen["url"] == "https://example.com/x"
    assert seen["headers"]["X-api-key"] == "k"
    assert json.loads(seen["body"]) == {"a": 1}
    assert seen["timeout"] == check_keys.TIMEOUT


def test_request_turns_http_error_into_status(monkeypatch):
    def handler(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", None, io.BytesIO(b'{"error": "bad key"}')
        )

    _patch(monkeypatch, handler)
    status, body = check_keys._request("GET", "https://example.com/x")
    assert status == 401
    assert body == '{"error": "bad key"}'


def test_request_network_failure_exits(monkeypatch):
    def handler(request, timeout):
        raise OSError("dns failure")

    _patch(monkeypatch, handler)
    with pytest.raises(SystemExit) as exc:
        check_keys._request("GET", "https://example.com/x")
    assert "FAIL network error" in str(exc.value)


def test_check_serper_ok(capsys, monkeypatch):
    def handler(request, timeout):
        return _Response(200, b"{}")

    _patch(monkeypatch, handler)
    with pytest.raises(SystemExit) as exc:
        check_keys.check_serper("a-valid-key")
    assert exc.value.code == 0
    assert "OK serper" in capsys.readouterr().out


def test_check_tavily_rejected(capsys, monkeypatch):
    def handler(request, timeout):
        return _Response(403, b"nope")

    _patch(monkeypatch, handler)
    with pytest.raises(SystemExit) as exc:
        check_keys.check_tavily("a-valid-key")
    assert exc.value.code == 1
    assert "rejected with HTTP 403" in capsys.readouterr().out


def test_check_scrappey_reports_balance(capsys, monkeypatch):
    def handler(request, timeout):
        return _Response(200, b'{"balance": 12.5}')

    _patch(monkeypatch, handler)
    with pytest.raises(SystemExit) as exc:
        check_keys.check_scrappey("a-valid-key")
    assert exc.value.code == 0
    assert "balance 12.5" in capsys.readouterr().out


def test_check_scrappey_rejects_missing_balance(capsys, monkeypatch):
    def handler(request, timeout):
        return _Response(200, b'{"other": 1}')

    _patch(monkeypatch, handler)
    with pytest.raises(SystemExit) as exc:
        check_keys.check_scrappey("a-valid-key")
    assert exc.value.code == 1
    assert "no balance" in capsys.readouterr().out


def test_main_validates_provider_and_key(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["check_keys.py", "nope", "a-valid-key"])
    with pytest.raises(SystemExit) as exc:
        check_keys.main()
    assert exc.value.code == 2

    monkeypatch.setattr(sys, "argv", ["check_keys.py", "serper", "short"])
    with pytest.raises(SystemExit) as exc:
        check_keys.main()
    assert exc.value.code == 2
    assert "too short" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["check_keys.py"])
    with pytest.raises(SystemExit) as exc:
        check_keys.main()
    assert exc.value.code == 2
