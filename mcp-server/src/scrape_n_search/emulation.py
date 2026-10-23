"""Scrappey-emulated search, crawl, and extract.

Scrappey has no search, crawl, or extract endpoints: one request fetches
exactly one page. When Scrappey is the only configured provider, the
corresponding tools are served by emulation through its fetch modes:

- search: one full-browser request (``js_render=True``, €1/1K, ~20–40s) loads
  Bing's results page and the response is parsed into the unified
  ``SearchResult`` shape. Browser mode is required — live testing (2026-10)
  showed Bing serves stale/other-query cached SERPs to Scrappey's rotating
  residential proxies in basic mode.
- crawl: a breadth-first walk over same-host pages, one basic-mode request per
  page (first-party pages serve fine in basic mode), returned as ``CrawlPage``
  items with raw HTML content.
- extract: one Scrappey fetch plus one LLM call (key from the host harness or
  .env — see ``llm.py``) that reduces the page text to clean Markdown.

Each emulation sits behind a dual-run trigger in ``server.py``: the first
call returns an explanation of the mechanics and drawbacks instead of
executing; a follow-up call with ``force=true`` (deliberately absent from
the published tool schemas, accepted only as a real parameter so the call
still validates) runs the emulated path.
"""

from __future__ import annotations

import base64
import html as _html
import logging
import os
import re
import urllib.parse
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

from . import llm
from .errors import ProviderError
from .htmltext import clean_text
from .htmltext import html_to_text as _html_to_text
from .htmltext import strip_blocks as _strip_blocks
from .models import CrawlFailure, CrawlOutcome, CrawlPage, SearchResult
from .providers.base import BaseProvider

logger = logging.getLogger(__name__)

BING_SEARCH = "https://www.bing.com/search"

# Default cap on page text sent to the LLM (chars); override with
# EXTRACT_MAX_CHARS. Bounds context size and per-call cost.
_DEFAULT_MAX_EXTRACT_CHARS = 100_000

# Comments carry no navigable content (and often hold commented-out links).
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Cap links harvested from one page; bounds BFS fan-out on huge pages.
_MAX_LINKS_PER_PAGE = 200

# File extensions that are never worth fetching as pages.
_SKIP_EXTENSIONS = frozenset(
    {
        "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "avif", "bmp",
        "css", "js", "mjs", "json", "xml", "rss", "atom", "pdf", "zip", "gz",
        "tar", "7z", "rar", "mp3", "mp4", "webm", "ogg", "wav", "mov", "avi",
        "woff", "woff2", "ttf", "eot", "otf",
    }
)

_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _unredirect(url: str) -> str:
    """Resolve Bing ``/ck/a`` click-tracking redirects to the real target URL.

    Bing serves every organic result as ``https://www.bing.com/ck/a?...`` with
    the destination base64-encoded in the ``u`` query parameter — base64 of
    the URL with ``1a`` inserted after the first character and the padding
    stripped (observed on live SERPs, 2026-10). Returning the redirect URL
    would work but chains an extra hop (and leaks the query to Bing's click
    tracker); decoding keeps result links direct. Tries the plain decode
    first, then the obfuscated variant; falls back to the original URL.
    """
    parts = urlparse(url)
    if not (parts.netloc.lower().endswith("bing.com") and parts.path.startswith("/ck/a")):
        return url
    encoded = urllib.parse.parse_qs(parts.query).get("u", [""])[0]
    if not encoded:
        return url
    candidates = [encoded]
    if encoded[1:3] == "1a":  # known obfuscation: strip the inserted "1a"
        candidates.append(encoded[0] + encoded[3:])
    for candidate in candidates:
        try:
            # urlsafe alphabet: Bing values can contain '-'/'_' (urlsafe b64
            # also accepts the standard alphabet, so one call covers both).
            decoded = base64.urlsafe_b64decode(candidate + "=" * (-len(candidate) % 4)).decode(
                "utf-8"
            )
        except (ValueError, UnicodeDecodeError):
            continue
        if decoded.startswith(("http://", "https://")):
            return decoded
    return url


_clean_text = clean_text


def _join_text(pieces: list[str] | None) -> str:
    return _clean_text(" ".join(pieces or []))


class _BingSERPParser(HTMLParser):
    """Extract organic results from a Bing HTML results page.

    Bing wraps each result in ``<li class="b_algo">`` with an
    ``<h2><a href="…">title</a></h2>`` result link and a ``<p>`` snippet.
    The parser is deliberately best-effort: markup changes or anti-bot
    pages yield fewer results, never an exception.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        # One entry per open <li>, True when it is a b_algo result. A plain
        # <li> nested inside a result (deep-link or related-item lists) must
        # not end the surrounding result — only the matching b_algo closer does.
        self._li_stack: list[bool] = []
        self._algo_depth = 0
        self._cur: dict[str, str] | None = None
        self._in_h2 = False
        self._in_p = False
        self._buf: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "li":
            is_result = "b_algo" in (attr_dict.get("class") or "").split()
            self._li_stack.append(is_result)
            if is_result:
                self._algo_depth += 1
                if self._algo_depth == 1:
                    self._cur = {"url": "", "title": "", "snippet": ""}
            return
        # Only the outermost result is captured: content inside a nested
        # b_algo (malformed markup) must not overwrite the outer title/url.
        if self._algo_depth != 1:
            return
        if tag == "h2":
            self._in_h2 = True
            self._buf = []
        elif tag == "a" and self._in_h2 and self._cur and not self._cur["url"]:
            self._cur["url"] = attr_dict.get("href") or ""
        elif tag == "p" and self._cur and not self._cur["snippet"]:
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "li":
            if not self._li_stack:
                return
            if self._li_stack.pop():
                self._algo_depth -= 1
                if self._algo_depth == 0:
                    self._flush()
            return
        if self._algo_depth != 1:
            return
        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            if self._cur is not None:
                self._cur["title"] = _join_text(self._buf)
            self._buf = None
        elif tag == "p" and self._in_p:
            self._in_p = False
            if self._cur is not None:
                self._cur["snippet"] = _join_text(self._buf)
            self._buf = None
    def close(self) -> None:
        """Flush a result whose closing ``</li>`` never arrived (truncated SERP)."""
        super().close()
        if self._algo_depth:
            self._algo_depth = 0
            self._li_stack.clear()
            self._flush()

    def _flush(self) -> None:
        cur, self._cur = self._cur, None
        # A truncated response can cut mid-title/mid-snippet: harvest whatever
        # text arrived before the missing end tag.
        if cur is not None and self._buf is not None:
            cur["title" if self._in_h2 else "snippet"] = _join_text(self._buf)
        self._in_h2 = False
        self._in_p = False
        self._buf = None
        if cur is None:
            return
        url = _unredirect(_html.unescape(cur["url"].strip()))
        title = _clean_text(cur["title"])
        if url and title:
            self.results.append(
                SearchResult(title=title, url=url, snippet=_clean_text(cur["snippet"]))
            )

    def handle_data(self, data: str) -> None:
        if self._buf is not None:
            self._buf.append(data)


def _bing_search_url(query: str) -> str:
    # No `count` param: live testing showed Bing serves stale/wrong cached
    # SERPs when `count` is present (basic and browser mode both), and correct
    # fresh results without it. Cap results client-side instead.
    return f"{BING_SEARCH}?{urllib.parse.urlencode({'q': query})}"


async def emulated_search(provider: BaseProvider, query: str, max_results: int) -> list[SearchResult]:
    """Emulated web search: one full-browser Bing SERP fetch, parsed results.

    Full browser mode (``js_render=True``) is deliberate: live testing showed
    Bing serves stale/other-query cached SERPs to Scrappey's rotating
    residential proxies in basic mode (4/4 probe requests returned coherent
    results for a different query), while browser mode returns fresh, relevant
    results at ~10× the cost and ~30s latency. ``provider`` must be an enabled
    Scrappey provider; ``fetch`` errors propagate to the caller.
    """
    page = await provider.fetch(_bing_search_url(query), format="html", js_render=True)
    parser = _BingSERPParser()
    try:
        parser.feed(page.content or "")
        parser.close()
    except Exception:  # pragma: no cover - parser must never take the gate down
        logger.exception("bing serp parse failed for %r", query)
        return []
    seen: set[str] = set()
    results: list[SearchResult] = []
    for result in parser.results:
        if result.url in seen:
            continue
        seen.add(result.url)
        results.append(result)
        if len(results) >= max_results:
            break
    return results


def _extract_title(html_text: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    without_tags = re.sub(r"<[^>]+>", "", match.group(1))
    title = _clean_text(_html.unescape(without_tags))
    return title or None


def _extract_links(html_text: str, base_url: str) -> list[str]:
    """Harvest ``href`` values, resolved against ``base_url``, capped.

    Script/style blocks and HTML comments are stripped first: a link-shaped
    string inside a script literal or a commented-out block is not a real
    link, and following it would bill a pointless provider request.
    """
    cleaned = _COMMENT_RE.sub(" ", _strip_blocks(html_text))
    links: list[str] = []
    for match in _HREF_RE.finditer(cleaned):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        links.append(urljoin(base_url, _html.unescape(href)))
        if len(links) >= _MAX_LINKS_PER_PAGE:
            break
    return links


def _normalized_netloc(url: str) -> str:
    """Dedupe/comparison host: lowercase, ``www.``-stripped, no trailing dot."""
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.rstrip(".")


def _crawl_key(url: str) -> str:
    """Dedupe identity: scheme + www-stripped host + path + query, no fragment."""
    scheme, _, path, _, query, _ = urlparse(urldefrag(url)[0])
    host = _normalized_netloc(url)
    return f"{scheme.lower()}://{host}{path or '/'}{'?' + query if query else ''}"


def _same_host(a: str, b: str) -> bool:
    """Same site, ignoring scheme and a leading ``www.`` (both are common)."""
    return _normalized_netloc(a) == _normalized_netloc(b)


def _is_skippable(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    segment = path.rsplit("/", 1)[-1]
    return "." in segment and segment.rsplit(".", 1)[-1].lower() in _SKIP_EXTENSIONS


async def emulated_crawl(
    provider: BaseProvider, start_url: str, max_pages: int, max_depth: int
) -> CrawlOutcome:
    """Emulated crawl: breadth-first walk of same-host pages, basic mode.

    One basic-mode ``fetch`` per page (so up to ``max_pages`` billed
    requests). A failure on the start page aborts (there is nothing to
    report); later page failures are recorded in ``CrawlOutcome.failures``
    and the walk continues, so one dead link does not discard the pages
    already fetched (and billed).
    """
    if _is_skippable(start_url):
        raise ProviderError(f"crawl start URL looks like a file, not a page: {start_url}")
    outcome = CrawlOutcome()
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    while queue and len(outcome.pages) < max_pages:
        url, depth = queue.popleft()
        key = _crawl_key(url)
        if key in seen:
            continue
        seen.add(key)
        try:
            fetched = await provider.fetch(url, format="html", js_render=False)
        except (ProviderError, httpx.HTTPError) as exc:
            if not outcome.pages:
                raise
            logger.warning("crawl page %s failed: %s", url, exc)
            outcome.failures.append(CrawlFailure(url=url, error=str(exc)))
            continue
        content = fetched.content or ""
        outcome.pages.append(
            CrawlPage(url=fetched.url or url, title=_extract_title(content), content=content)
        )
        if depth >= max_depth:
            continue
        for link in _extract_links(content, fetched.url or url):
            if _is_skippable(link):
                continue
            link_key = _crawl_key(link)
            if link_key in seen or not _same_host(start_url, link):
                continue
            queue.append((link, depth + 1))
    return outcome




def _llm_max_chars() -> int:
    raw = os.environ.get("EXTRACT_MAX_CHARS")
    if not raw:
        return _DEFAULT_MAX_EXTRACT_CHARS
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"EXTRACT_MAX_CHARS must be a positive integer, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"EXTRACT_MAX_CHARS must be a positive integer, got {raw!r}")
    return value


async def emulated_extract(
    provider: BaseProvider, url: str, *, prompt: str | None = None, js_render: bool = False
) -> str:
    """Emulated web extract: one Scrappey fetch + one LLM summary.

    Basic mode (default) fetches raw HTML (€0.10/1K) and strips it to text
    locally; ``js_render=True`` uses full browser mode (€1/1K) and hands
    Scrappey's markdown conversion straight to the LLM — needed for JS-heavy
    pages (SPAs, lazy-loaded content). The page text is truncated to
    ``EXTRACT_MAX_CHARS`` (default 100k) before the LLM call. The completion
    is returned as clean Markdown; errors propagate as ``ProviderError``.
    The LLM key is checked before the fetch — no billed request when the
    summary leg is impossible.
    """
    if not llm.configured():
        raise ProviderError(
            "no LLM key available for emulated web_extract: set OPENROUTER_API_KEY "
            "or HF_TOKEN (in .env or in the harness mcp.json env block for the server)"
        )
    if js_render:
        page = await provider.fetch(url, format="markdown", js_render=True)
        text = page.content or ""
    else:
        page = await provider.fetch(url, format="html", js_render=False)
        text = _html_to_text(page.content or "")
    if not text.strip():
        raise ProviderError(f"emulated extract: page {url!r} returned no content")
    max_chars = _llm_max_chars()
    if len(text) > max_chars:
        text = text[:max_chars]
    return await llm.summarize(text, instruction=prompt)


def extract_explanation(
    url: str, prompt: str | None, js_render: bool, llm_backend: str | None
) -> dict:
    """Dual-run gate payload for ``web_extract`` (not executed)."""
    max_chars = _llm_max_chars()
    how = (
        f"one Scrappey fetch of {url!r} "
        f"({'full-browser mode (js_render=true, €1/1K)' if js_render else 'basic mode (js_render=false, €0.10/1K)'})"
        f" followed by one LLM call that reduces the page text to clean Markdown "
        f"with the key details"
    )
    drawbacks = [
        "the summary is LLM-generated: it can miss details or hallucinate facts — it is "
        "not a lossless or structured extract (real extract providers return JSON "
        "per their prompt)",
        f"page text is truncated to {max_chars:,} characters before the LLM call",
        "the LLM call bills the harness LLM account (OpenRouter or HuggingFace key), "
        "not the Scrappey balance, and adds roughly 5–30s latency",
        "basic mode sees raw HTML with no JS rendering — SPA/lazy-loaded content is "
        "missing unless js_render=true (€1/1K browser mode)",
        "thinking is requested off but this is best-effort: some models or endpoints "
        "ignore it, which only raises cost/latency, not output quality",
    ]
    if llm_backend is None:
        drawbacks.append(
            "no LLM key is currently available to the server: set OPENROUTER_API_KEY "
            "or HF_TOKEN in mcp-server/.env, or in the harness's mcp.json env block "
            "for this server (same mechanism as look-n-see), before force=true"
        )
    emulation = {
        "tool": "web_extract",
        "provider": "scrappey",
        "executed": False,
        "how": how + (f" with extraction focus {prompt!r}" if prompt else ""),
        "drawbacks": drawbacks,
        "trigger": (
            're-call web_extract with "force": true to execute the emulated path. '
            "force is an optional boolean parameter on this tool; without it the call "
            "only explains what would happen."
        ),
    }
    return {"error": (
        "web_extract was NOT executed: no real extract provider is configured "
        "(only Scrappey), so the request would have to be emulated through an "
        "LLM summary."
    ), "emulation": emulation}


def search_explanation(
    query: str, max_results: int, freshness: str | None, country: str | None
) -> dict:
    """Dual-run gate payload for ``web_search`` (not executed)."""
    return {
        "error": (
            "web_search was NOT executed: no real search provider is configured "
            "(only Scrappey), so the request would have to be emulated."
        ),
        "emulation": {
            "tool": "web_search",
            "provider": "scrappey",
            "executed": False,
            "how": (
                f"one Scrappey full-browser request (js_render=true, €1/1K, ~20–40s) "
                f"loads Bing's results page for {query!r} and parses up to {max_results} "
                "results into the normal web_search response shape. Full browser mode is "
                "required: live testing showed Bing serves stale cached result pages for "
                "other queries to Scrappey's rotating residential proxies in cheap basic "
                "mode, while browser mode returns fresh, relevant results."
            ),
            "drawbacks": [
                "results are scraped from Bing's HTML, not a search index: no published "
                "dates, no relevance scores, and promoted results can appear",
                "Bing markup or anti-bot changes can still break or degrade parsing "
                "(DuckDuckGo already challenges Scrappey basic mode)",
                f"freshness={freshness!r} and country={country!r} are ignored in emulation",
                "each call costs ~10× basic mode (€1/1K) and takes ~20–40s (full browser)",
                "result geo follows the rotating residential proxy region, which varies "
                "per request (web_search has no proxy_country param)",
                "max_results is a ceiling, not a guarantee (Bing serves ~10 results per "
                "page by default)",
            ],
            "trigger": (
                're-call web_search with "force": true to execute the emulated path. '
                "force is an optional boolean parameter on this tool; without it the call "
                "only explains what would happen."
            ),
        },
    }


def crawl_explanation(url: str, max_pages: int, max_depth: int) -> dict:
    """Dual-run gate payload for ``web_crawl`` (not executed)."""
    return {
        "error": (
            "web_crawl was NOT executed: no real crawl provider is configured "
            "(only Scrappey), so the request would have to be emulated."
        ),
        "emulation": {
            "tool": "web_crawl",
            "provider": "scrappey",
            "executed": False,
            "how": (
                f"a breadth-first crawl starting at {url!r}: every page is one Scrappey "
                f"basic-mode request (js_render=false, €0.10/1K each), same-host links "
                f"only, up to {max_pages} pages at depth ≤ {max_depth}, returned with "
                "raw HTML content."
            ),
            "drawbacks": [
                "each page is a billed request: a crawl costs up to max_pages × basic-mode "
                "fetch (€0.10/1K each); max_pages defaults to 10",
                "no JS rendering: single-page-app content is missing, and anti-bot "
                "protection is only the built-in residential proxies",
                "content is raw HTML, not markdown",
                "link discovery is best-effort over page HTML: JS-injected links and "
                "sitemap-only pages are missed",
                "a page that fails to fetch is reported in 'failures' and skipped; the "
                "crawl continues with the pages already fetched (the start page failure "
                "aborts, since there is nothing to report)",
            ],
            "trigger": (
                're-call web_crawl with "force": true to execute the emulated path. '
                "force is an optional boolean parameter on this tool; without it the call "
                "only explains what would happen."
            ),
        },
    }
