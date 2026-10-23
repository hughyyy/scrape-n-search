"""Minimal OpenAI-compatible chat client for the emulated ``web_extract``.

Scrappey has no extract endpoint. When no real extract provider is
configured, ``web_extract`` is emulated: one Scrappey fetch plus one LLM
call that reduces the page text to clean markdown (see ``emulation.py``).

The key is inherited from the host harness when the server is spawned with
``OPENROUTER_API_KEY`` or ``HF_TOKEN`` in its environment — the Oh My Pi
``mcp.json`` ``env`` block can inject ``omp token`` values (same mechanism
as the look-n-see server) — or from ``.env`` when set there.

Thinking is requested off (``reasoning: {effort: none}`` for OpenRouter,
``enable_thinking: false`` for HuggingFace) but that is best-effort: some
models or endpoints reject the param or ignore it, and a rejected request
is retried once without it — the summary is still returned, only latency
and cost may be higher.
"""

from __future__ import annotations

import logging
import os

import httpx

from .errors import ProviderError

logger = logging.getLogger(__name__)

ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "huggingface": "https://router.huggingface.co/v1/chat/completions",
}

DEFAULT_MODELS = {
    "openrouter": "deepseek/deepseek-chat",
    "huggingface": "deepseek-ai/DeepSeek-V4-Flash-0731",
}

# Dedicated generous timeout: the LLM leg runs after the page fetch.
_LLM_TIMEOUT = httpx.Timeout(120.0, connect=15.0)

_SYSTEM_PROMPT = (
    "You reduce web page content to clean Markdown. Extract the key facts and details "
    "from the page text: a one-line summary, then short sections with the important "
    "information. Rules: only report facts present in the page content — never invent, "
    "guess, or extrapolate; drop navigation, cookie banners, ads, and repeated "
    "boilerplate; if the page is mostly boilerplate or empty, say so in one line. "
    "Respond with the Markdown only, no preamble."
)

_client: httpx.AsyncClient | None = None


def backend() -> str | None:
    """Configured LLM backend: ``"openrouter"``, ``"huggingface"``, or None.

    Env is read at call time (not import time) so keys injected after the
    module is imported (i.e. the .env loader in ``__main__``) are honored.
    """
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("HF_TOKEN"):
        return "huggingface"
    return None


def configured() -> bool:
    return backend() is not None


def model(backend_name: str) -> str:
    return os.environ.get("EXTRACT_MODEL") or DEFAULT_MODELS[backend_name]


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_LLM_TIMEOUT)
    return _client


async def aclose() -> None:
    """Close the shared client; called from the server lifespan teardown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _request_body(backend_name: str, page_text: str, instruction: str | None) -> dict:
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if instruction:
        messages.append(
            {"role": "user", "content": f"Extraction focus for this page: {instruction}"}
        )
    messages.append(
        {"role": "user", "content": f"Here is the page content:\n\n{page_text}"}
    )
    body: dict = {
        "model": model(backend_name),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 4096,
    }
    if backend_name == "openrouter":
        body["reasoning"] = {"effort": "none"}
    else:
        body["enable_thinking"] = False
    return body


async def summarize(page_text: str, *, instruction: str | None = None) -> str:
    """One-shot LLM summary of page text, returned as clean Markdown.

    ``instruction`` (the ``prompt`` tool argument) steers what to extract.
    Raises ``ProviderError`` on any failure so the emulation gate can wrap it.
    """
    backend_name = backend()
    if backend_name is None:
        raise ProviderError(
            "no LLM key available for emulated web_extract: set OPENROUTER_API_KEY "
            "or HF_TOKEN (in .env or in the harness mcp.json env block for the server)"
        )
    key = os.environ["OPENROUTER_API_KEY" if backend_name == "openrouter" else "HF_TOKEN"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = _request_body(backend_name, page_text, instruction)
    client = _get_client()
    retried = False
    while True:
        try:
            resp = await client.post(ENDPOINTS[backend_name], json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            # 4xx that rejects the thinking-off param -> retry once without it.
            if retried or exc.response.status_code not in (400, 422):
                raise ProviderError(f"LLM summarize failed: {exc}") from exc
            logger.info(
                "LLM thinking-off param rejected (%s); retrying without it",
                exc.response.status_code,
            )
            body.pop("reasoning", None)
            body.pop("enable_thinking", None)
            retried = True
            continue
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"LLM summarize failed: {exc}") from exc
        try:
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        except AttributeError as exc:  # pragma: no cover - malformed payload
            raise ProviderError("LLM summarize failed: malformed response payload") from exc
        if not content or not content.strip():
            raise ProviderError("LLM summarize failed: empty completion")
        return content.strip()
