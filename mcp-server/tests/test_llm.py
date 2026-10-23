"""Direct tests for the emulated-extract LLM client (llm.py).

Coverage targets: backend/key resolution, model selection, request-body
construction (thinking-off params per backend), auth headers, the
retry-without-thinking-off fallback, and every error path.

All HTTP is mocked via httpx.MockTransport; no network, no billing.
"""

from __future__ import annotations

import httpx
import pytest

from scrape_n_search import llm
from scrape_n_search.errors import ProviderError

from conftest import make_client


def _patch_client(monkeypatch, handler) -> httpx.AsyncClient:
    client = make_client(handler)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    return client


# --- backend / key resolution -------------------------------------------


def test_backend_none_without_keys(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert llm.backend() is None
    assert llm.configured() is False


def test_backend_openrouter_wins_over_hf(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("HF_TOKEN", "hf-key")
    assert llm.backend() == "openrouter"


def test_backend_hf_used_without_openrouter(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf-key")
    assert llm.backend() == "huggingface"
    assert llm.configured() is True


def test_model_defaults_and_override(monkeypatch):
    assert llm.model("openrouter") == "deepseek/deepseek-chat"
    assert llm.model("huggingface") == "deepseek-ai/DeepSeek-V4-Flash-0731"
    monkeypatch.setenv("EXTRACT_MODEL", "acme/model")
    assert llm.model("openrouter") == "acme/model"
    assert llm.model("huggingface") == "acme/model"


def test_backend_reads_env_at_call_time(monkeypatch):
    # Keys injected after import (e.g. .env loader) must be honored.
    assert llm.backend() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "late-key")
    assert llm.backend() == "openrouter"


# --- summarize: happy paths ---------------------------------------------


async def test_summarize_openrouter_request_shape(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  # Summary\n\n- one  "}}]},
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    _patch_client(monkeypatch, handler)
    result = await llm.summarize("page text here", instruction="prices")
    assert result == "# Summary\n\n- one"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer or-key"
    body = __import__("json").loads(seen["body"])
    assert body["model"] == "deepseek/deepseek-chat"
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 4096
    assert body["reasoning"] == {"effort": "none"}  # thinking off for OpenRouter
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"] == "Extraction focus for this page: prices"
    assert "page text here" in body["messages"][-1]["content"]


async def test_summarize_huggingface_request_shape(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "# Ok"}}]})

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf-key")
    _patch_client(monkeypatch, handler)
    result = await llm.summarize("text")
    assert result == "# Ok"
    assert seen["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert seen["auth"] == "Bearer hf-key"
    body = __import__("json").loads(seen["body"])
    assert body["model"] == "deepseek-ai/DeepSeek-V4-Flash-0731"
    assert body["enable_thinking"] is False
    assert "reasoning" not in body
    # no instruction -> no extraction-focus message
    assert all("Extraction focus" not in m.get("content", "") for m in body["messages"])


async def test_summarize_model_override(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("EXTRACT_MODEL", "vendor/model-7b")
    _patch_client(monkeypatch, handler)
    await llm.summarize("text")
    assert __import__("json").loads(seen["body"])["model"] == "vendor/model-7b"


# --- summarize: thinking-off retry fallback -----------------------------


async def test_summarize_retries_without_thinking_param_on_400(monkeypatch):
    requests = []
    statuses = [400, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.read().decode())
        status = statuses.pop(0)
        if status == 200:
            return httpx.Response(200, json={"choices": [{"message": {"content": "# Recovered"}}]})
        return httpx.Response(status, text="bad param")

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    result = await llm.summarize("text")
    assert result == "# Recovered"
    assert len(requests) == 2
    first = __import__("json").loads(requests[0])
    second = __import__("json").loads(requests[1])
    assert first["reasoning"] == {"effort": "none"}
    assert "reasoning" not in second


async def test_summarize_retries_without_thinking_param_on_hf_422(monkeypatch):
    requests = []
    statuses = [422, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.read().decode())
        status = statuses.pop(0)
        if status == 200:
            return httpx.Response(200, json={"choices": [{"message": {"content": "# Ok"}}]})
        return httpx.Response(status, text="bad param")

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("HF_TOKEN", "k")
    _patch_client(monkeypatch, handler)
    result = await llm.summarize("text")
    assert result == "# Ok"
    first = __import__("json").loads(requests[0])
    second = __import__("json").loads(requests[1])
    assert first["enable_thinking"] is False
    assert "enable_thinking" not in second


async def test_summarize_400_twice_raises(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="nope")

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="LLM summarize failed"):
        await llm.summarize("text")
    assert calls["n"] == 2  # one retry, then fail


# --- summarize: error paths ----------------------------------------------


async def test_summarize_no_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(ProviderError, match="no LLM key"):
        await llm.summarize("text")


async def test_summarize_http_500_raises_without_retry(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="LLM summarize failed"):
        await llm.summarize("text")
    assert calls["n"] == 1  # only thinking-off rejection (400/422) retries


async def test_summarize_network_error_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="LLM summarize failed"):
        await llm.summarize("text")


async def test_summarize_empty_completion_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "   "}}]}
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="empty completion"):
        await llm.summarize("text")


async def test_summarize_missing_choices_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="empty completion"):
        await llm.summarize("text")


async def test_summarize_malformed_payload_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="LLM summarize failed"):
        await llm.summarize("text")


async def test_summarize_non_json_response_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="LLM summarize failed"):
        await llm.summarize("text")


# --- client lifecycle -----------------------------------------------------


async def test_aclose_closes_shared_client_and_is_idempotent():
    client = make_client(lambda r: httpx.Response(200))
    llm._client = client
    await llm.aclose()
    assert client.is_closed
    assert llm._client is None
    await llm.aclose()  # second close must not raise


async def test_summarize_401_no_retry(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _patch_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="LLM summarize failed"):
        await llm.summarize("text")
    assert calls["n"] == 1  # auth errors are not retried without the param


async def test_get_client_lazy_creates_and_aclose_resets():
    # real (offline) client path: module-level lazy creation, close, recreate
    llm._client = None
    client = llm._get_client()
    try:
        assert client is llm._get_client()  # second call reuses
    finally:
        await llm.aclose()
    assert llm._client is None
    client2 = llm._get_client()  # recreated after close
    await llm.aclose()
    assert client2.is_closed
