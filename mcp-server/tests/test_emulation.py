"""Unit tests for Scrappey-emulated search and crawl (emulation.py)."""

from __future__ import annotations

import base64

import pytest

from scrape_n_search.emulation import (
    _BingSERPParser,
    _MAX_LINKS_PER_PAGE,
    _crawl_key,
    _extract_links,
    _extract_title,
    _html_to_text,
    _is_skippable,
    _llm_max_chars,
    crawl_explanation,
    emulated_crawl,
    emulated_extract,
    emulated_search,
    extract_explanation,
    search_explanation,
)
from scrape_n_search.errors import ProviderError
from scrape_n_search.models import FetchedPage
from scrape_n_search.providers.base import BaseProvider

BING_HTML = """
<html><body><ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://example.com/a?x=1&amp;y=2" h="ID=SERP,5007.1">Example &quot;Domain&quot;</a></h2>
    <div class="b_caption"><p class="b_lineclamp2">First <b>snippet</b> text.</p></div>
  </li>
  <li class="b_algo">
    <h2><a href="https://other.org/">Other Org</a></h2>
    <p class="b_lineclamp2">Second snippet.</p>
  </li>
  <li class="b_noscript">never a result</li>
  <li class="b_algo">
    <h2><span class="no_highlight">Nested</span> Title</h2>
    <a href="https://broken.example/">anchor outside the h2 is not the result link</a>
    <p>skipped: no result url</p>
  </li>
</ol></body></html>
"""


class StubProvider(BaseProvider):
    """Scrappey-shaped provider serving a URL -> HTML map; records fetches."""

    name = "scrappey"
    capabilities = frozenset({"fetch"})

    def __init__(self, pages: dict[str, str] | None = None):
        super().__init__()
        self.pages = pages or {}
        self.calls: list[tuple[str, dict]] = []

    async def fetch(self, url: str, **kw) -> FetchedPage:
        self.calls.append((url, dict(kw)))
        content = self.pages[url]  # KeyError = unexpected fetch
        return FetchedPage(url=url, content=content, title=None)


def test_bing_parser_extracts_results():
    parser = _BingSERPParser()
    parser.feed(BING_HTML)
    assert len(parser.results) == 2
    first, second = parser.results
    assert first.title == 'Example "Domain"'
    assert first.url == "https://example.com/a?x=1&y=2"
    assert first.snippet == "First snippet text."
    assert second.title == "Other Org"
    assert second.url == "https://other.org/"
    assert second.snippet == "Second snippet."


async def test_emulated_search_queries_bing_in_browser_mode_and_caps():
    pages = {"https://www.bing.com/search?q=hello+world": BING_HTML}
    provider = StubProvider(pages)
    results = await emulated_search(provider, "hello world", 3)
    assert [r.title for r in results] == ['Example "Domain"', "Other Org"]
    (url, kw) = provider.calls[0]
    # No count= param: live testing (2026-10) showed Bing serves stale/off-query
    # cached SERPs when `count` is present; results are capped client-side.
    assert url == "https://www.bing.com/search?q=hello+world"
    # Full browser mode: basic mode was verified live to serve stale, off-query
    # cached SERPs to Scrappey's rotating proxies too.
    assert kw["js_render"] is True
    assert kw["format"] == "html"


async def test_emulated_search_dedupes_urls():
    pages = {"https://www.bing.com/search?q=x": BING_HTML + BING_HTML}
    provider = StubProvider(pages)
    results = await emulated_search(provider, "x", 5)
    assert [r.url for r in results] == ["https://example.com/a?x=1&y=2", "https://other.org/"]


def test_bing_parser_decodes_click_redirect():
    target = "https://www.ilovepdf.com/merge_pdf?src=1&x=2"
    encoded = base64.b64encode(target.encode()).decode()
    html = (
        '<ol id="b_results"><li class="b_algo">'
        f'<h2><a href="https://www.bing.com/ck/a?!&&p=abc&u={encoded}&ntb=1">Merged</a></h2>'
        "<p>snippet</p></li></ol>"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert parser.results[0].url == target


def test_bing_parser_decodes_obfuscated_live_values():
    """Real u= values captured from live Bing SERPs (2026-10): standard
    base64 with "1a" inserted after the first character, padding stripped."""
    live = [
        (
            "a1aHR0cHM6Ly93d3cuaWxvdmVwZGYuY29tL21lcmdlX3BkZg",
            "https://www.ilovepdf.com/merge_pdf",
        ),
        (
            "a1aHR0cHM6Ly9kaWN0aW9uYXJ5LmNhbWJyaWRnZS5vcmcvZGljdGlvbmFyeS9lbmdsaXNoL29o",
            "https://dictionary.cambridge.org/dictionary/english/oh",
        ),
    ]
    for encoded, target in live:
        html = (
            '<ol id="b_results"><li class="b_algo">'
            f'<h2><a href="https://www.bing.com/ck/a?u={encoded}&ntb=1">X</a></h2>'
            "<p>snippet</p></li></ol>"
        )
        parser = _BingSERPParser()
        parser.feed(html)
        assert parser.results[0].url == target, encoded


def test_bing_parser_decodes_urlsafe_alphabet_value():
    """Live capture (2026-10): YouTube's /ck/a u= uses the url-safe alphabet."""
    encoded = "a1aHR0cHM6Ly93d3cueW91dHViZS5jb20vd2F0Y2g_dj1jX2ZRb0RrVUxsMA"
    html = (
        '<ol id="b_results"><li class="b_algo">'
        f'<h2><a href="https://www.bing.com/ck/a?u={encoded}&ntb=1">YT</a></h2>'
        "<p>snippet</p></li></ol>"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert parser.results[0].url == "https://www.youtube.com/watch?v=c_fQoDkULl0"


def test_bing_parser_keeps_plain_urls():
    html = (
        '<ol id="b_results"><li class="b_algo">'
        '<h2><a href="https://example.com/x">Plain</a></h2>'
        "<p>snippet</p></li></ol>"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert parser.results[0].url == "https://example.com/x"


def test_title_extraction():
    html = "<html><head><title>  Home &amp; more  </title></head><body></body></html>"
    assert _extract_title(html) == "Home & more"
    assert _extract_title("<html><body></body></html>") is None


def test_link_extraction_resolves_and_filters():
    base = "https://example.com/dir/page.html"
    html = (
        '<a href="about">rel</a>'
        '<a HREF="https://other.org/x">abs</a>'
        '<a href="#frag">frag</a>'
        '<a href="javascript:void(0)">js</a>'
        '<a href="mailto:a@b.c">mail</a>'
        '<a href="/root?q=1&amp;r=2">query</a>'
    )
    links = _extract_links(html, base)
    assert links == [
        "https://example.com/dir/about",
        "https://other.org/x",
        "https://example.com/root?q=1&r=2",
    ]


def test_link_extraction_capped():
    html = "".join(f'<a href="/p{i}">x</a>' for i in range(_MAX_LINKS_PER_PAGE + 50))
    assert len(_extract_links(html, "https://example.com/")) == _MAX_LINKS_PER_PAGE


def test_crawl_key_normalization():
    assert _crawl_key("https://www.Example.com/a#frag") == "https://example.com/a"
    assert _crawl_key("http://example.com/a") == "http://example.com/a"
    assert _crawl_key("https://example.com/a?q=1") == "https://example.com/a?q=1"


def test_skippable_extensions():
    assert _is_skippable("https://example.com/logo.png")
    assert _is_skippable("https://example.com/app.js")
    assert not _is_skippable("https://example.com/page")
    assert not _is_skippable("https://example.com/a.b.html")


def _crawl_pages():
    return {
        "https://example.com/": (
            '<html><head><title>Home</title></head><body>'
            '<a href="/about">About</a>'
            '<a href="https://external.example/x">external</a>'
            '<a href="/about#frag">duplicate of /about</a>'
            '<a href="/logo.png">asset</a>'
            "</body></html>"
        ),
        "https://example.com/about": (
            '<html><head><title>About</title></head><body>'
            '<a href="/contact">Contact</a>'
            "</body></html>"
        ),
        "https://example.com/contact": (
            "<html><head><title>Contact</title></head><body></body></html>"
        ),
    }


async def test_emulated_crawl_bfs_same_host_dedupe():
    provider = StubProvider(_crawl_pages())
    pages = await emulated_crawl(provider, "https://example.com/", max_pages=10, max_depth=2)
    assert [p.url for p in pages] == [
        "https://example.com/",
        "https://example.com/about",
        "https://example.com/contact",
    ]
    assert [p.title for p in pages] == ["Home", "About", "Contact"]
    assert all("</html>" in (p.content or "") for p in pages)
    # external host, fragment-dupe, and asset links never fetched
    assert len(provider.calls) == 3


async def test_emulated_crawl_respects_max_depth():
    provider = StubProvider(_crawl_pages())
    pages = await emulated_crawl(provider, "https://example.com/", max_pages=10, max_depth=1)
    assert [p.url for p in pages] == ["https://example.com/", "https://example.com/about"]
    assert len(provider.calls) == 2


async def test_emulated_crawl_respects_max_pages():
    provider = StubProvider(_crawl_pages())
    pages = await emulated_crawl(provider, "https://example.com/", max_pages=2, max_depth=10)
    assert [p.url for p in pages] == ["https://example.com/", "https://example.com/about"]
    assert len(provider.calls) == 2


async def test_emulated_crawl_rejects_file_start():
    provider = StubProvider({})
    with pytest.raises(ProviderError):
        await emulated_crawl(provider, "https://example.com/logo.png", max_pages=5, max_depth=2)
    assert provider.calls == []


async def test_emulated_crawl_propagates_fetch_errors():
    provider = StubProvider({})  # start URL missing -> KeyError inside fetch
    with pytest.raises(KeyError):
        await emulated_crawl(provider, "https://example.com/", max_pages=5, max_depth=2)


def test_search_explanation_shape():
    payload = search_explanation("pizza", 5, freshness="week", country="us")
    emu = payload["emulation"]
    assert payload["error"]
    assert emu["tool"] == "web_search"
    assert emu["executed"] is False
    assert emu["how"] and emu["drawbacks"]
    assert '"force": true' in emu["trigger"]
    assert "not a search index" in emu["drawbacks"][0]


def test_crawl_explanation_shape():
    payload = crawl_explanation("https://example.com", 10, 2)
    emu = payload["emulation"]
    assert payload["error"]
    assert emu["tool"] == "web_crawl"
    assert emu["executed"] is False
    assert emu["how"] and emu["drawbacks"]
    assert '"force": true' in emu["trigger"]


# --- emulated web_extract ---

_LLM_RESULT = "# Key details\n\n- fact one\n- fact two"


async def test_emulated_extract_basic_mode_strips_html_to_text(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = StubProvider(
        {
            "https://example.com/": (
                "<html><head><title>T</title></head><body>"
                "<script>var x = 1;</script>"
                "<style>.a{}</style>"
                "<nav>menu</nav>"
                "<h1>  Hello   world  </h1>"
                "<p>A &amp; B.</p>"
                "</body></html>"
            )
        }
    )
    calls = []
    async def fake_summarize(text, *, instruction=None):
        calls.append((text, instruction))
        return _LLM_RESULT
    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    result = await emulated_extract(provider, "https://example.com/", prompt="prices")
    assert result == _LLM_RESULT
    (url, kw) = provider.calls[0]
    assert url == "https://example.com/"
    assert kw["js_render"] is False  # basic mode: cheap raw HTML
    assert kw["format"] == "html"
    (text, instruction) = calls[0]
    assert "<script>" not in text and "<h1>" not in text
    assert "Hello world" in text and "A & B." in text and "menu" in text
    assert instruction == "prices"


async def test_emulated_extract_browser_mode_passes_markdown_through(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = StubProvider({"https://example.com/": "# Rendered markdown"})
    calls = []
    async def fake_summarize(text, *, instruction=None):
        calls.append((text, instruction))
        return _LLM_RESULT
    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    result = await emulated_extract(provider, "https://example.com/", js_render=True)
    assert result == _LLM_RESULT
    (url, kw) = provider.calls[0]
    assert kw["js_render"] is True  # browser mode for JS-heavy pages
    assert kw["format"] == "markdown"
    assert calls[0][0] == "# Rendered markdown"


async def test_emulated_extract_truncates_to_max_chars(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = StubProvider({"https://example.com/": "<p>" + "x" * 300 + "</p>"})
    seen = []
    async def fake_summarize(text, *, instruction=None):
        seen.append(text)
        return _LLM_RESULT
    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    with monkeypatch.context() as m:
        m.setenv("EXTRACT_MAX_CHARS", "100")
        result = await emulated_extract(provider, "https://example.com/")
    assert result == _LLM_RESULT
    assert len(seen[0]) == 100


async def test_emulated_extract_propagates_llm_failure(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = StubProvider({"https://example.com/": "<p>content</p>"})
    async def fake_summarize(text, *, instruction=None):
        raise ProviderError("LLM summarize failed: boom")
    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    with pytest.raises(ProviderError, match="boom"):
        await emulated_extract(provider, "https://example.com/")


async def test_emulated_extract_empty_page_rejected(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = StubProvider({"https://example.com/": "   "})
    async def fake_summarize(text, *, instruction=None):
        raise AssertionError("must not call the LLM for empty content")
    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    with pytest.raises(ProviderError, match="no content"):
        await emulated_extract(provider, "https://example.com/")


async def test_emulated_extract_checks_llm_key_before_fetch(monkeypatch):
    provider = StubProvider({"https://example.com/": "<p>x</p>"})
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        await emulated_extract(provider, "https://example.com/")
    assert provider.calls == []  # no billed fetch without a key


def test_html_to_text_strips_chrome_and_tags():
    html = (
        "<html><head><title>ignored</title></head><body>"
        "<script>var x=1;</script><style>.c{}</style>"
        "<noscript>no</noscript>"
        "<div><b>Bold</b> text &amp; more</div>"
        "</body></html>"
    )
    text = _html_to_text(html)
    assert "script" not in text and "Bold" in text
    assert "Bold text & more" == text


def test_llm_max_chars_default_and_env(monkeypatch):
    assert _llm_max_chars() == 100_000
    with monkeypatch.context() as m:
        m.setenv("EXTRACT_MAX_CHARS", "5000")
        assert _llm_max_chars() == 5000
    with monkeypatch.context() as m:
        m.setenv("EXTRACT_MAX_CHARS", "bogus")
        assert _llm_max_chars() == 100_000


def test_extract_explanation_shape():
    payload = extract_explanation("https://example.com", "prices", False, None)
    emu = payload["emulation"]
    assert payload["error"]
    assert emu["tool"] == "web_extract"
    assert emu["executed"] is False
    assert "basic mode" in emu["how"] and "prices" in emu["how"]
    assert '"force": true' in emu["trigger"]
    assert any("no LLM key" in d for d in emu["drawbacks"])
    # with a backend configured, the missing-key drawback disappears
    payload2 = extract_explanation("https://example.com", None, True, "openrouter")
    assert "full-browser mode" in payload2["emulation"]["how"]
    assert not any("no LLM key" in d for d in payload2["emulation"]["drawbacks"])
