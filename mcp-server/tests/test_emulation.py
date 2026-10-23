"""Unit tests for Scrappey-emulated search and crawl (emulation.py)."""

from __future__ import annotations

import base64
import random
import re
import time
from urllib.parse import urlparse

import pytest
from scrape_n_search.emulation import (
    _MAX_LINKS_PER_PAGE,
    _BingSERPParser,
    _crawl_key,
    _extract_links,
    _extract_title,
    _html_to_text,
    _is_skippable,
    _llm_max_chars,
    _strip_blocks,
    _unredirect,
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


def test_unredirect_passthrough_non_bing():
    url = "https://example.com/path?u=abc"
    assert _unredirect(url) == url


def test_unredirect_passthrough_bing_without_u_param():
    url = "https://www.bing.com/ck/a?p=abc&ntb=1"
    assert _unredirect(url) == url


def test_unredirect_passthrough_invalid_base64():
    url = "https://www.bing.com/ck/a?u=%21%21%21"
    assert _unredirect(url) == url


def test_unredirect_passthrough_non_url_decode():
    target = "not a url at all"
    encoded = base64.b64encode(target.encode()).decode()
    url = f"https://www.bing.com/ck/a?u={encoded}"
    assert _unredirect(url) == url


def test_unredirect_plain_base64_without_obfuscation():
    target = "https://example.com/a?b=1"
    encoded = base64.b64encode(target.encode()).decode().rstrip("=")
    url = f"https://www.bing.com/ck/a?u={encoded}"
    assert _unredirect(url) == target


def test_unredirect_padding_restored():
    # url-safe base64 that needs padding re-added before decoding
    target = "https://example.com/zz"
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    assert "=" not in encoded
    url = f"https://www.bing.com/ck/a?u={encoded}"
    assert _unredirect(url) == target


def test_bing_parser_skips_results_without_href():
    html = (
        '<ol id="b_results"><li class="b_algo">'
        "<h2><a>No href</a></h2><p>snippet</p></li></ol>"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert parser.results == []


def test_bing_parser_skips_empty_titles():
    html = (
        '<ol id="b_results"><li class="b_algo">'
        '<h2><a href="https://example.com/">   </a></h2><p>snippet</p></li></ol>'
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert parser.results == []


def test_bing_parser_nested_li_keeps_outer_result():
    html = (
        '<ol id="b_results"><li class="b_algo">'
        '<h2><a href="https://example.com/a">Outer</a></h2>'
        "<ul><li>deep link one</li><li>deep link two</li></ul>"
        "<p>outer snippet</p></li>"
        '<li class="b_algo"><h2><a href="https://example.com/b">Second</a></h2>'
        "<p>second snippet</p></li></ol>"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    parser.close()
    # a plain nested <li> must not end the surrounding b_algo result
    assert [(r.title, r.url) for r in parser.results] == [
        ("Outer", "https://example.com/a"),
        ("Second", "https://example.com/b"),
    ]
    assert parser.results[0].snippet == "outer snippet"


def test_bing_parser_takes_first_snippet_only():
    html = (
        '<ol id="b_results"><li class="b_algo">'
        '<h2><a href="https://example.com/a">A</a></h2>'
        '<p class="b_lineclamp2">first</p><p>second</p>'
        "</li></ol>"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert parser.results[0].snippet == "first"


def test_bing_parser_handles_missing_class_attr():
    # a <p> without a class attribute is still the snippet container
    html = (
        '<ol id="b_results"><li class="b_algo">'
        '<h2><a href="https://example.com/a">A</a></h2><p>snippet</p></li></ol>'
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert parser.results[0].snippet == "snippet"


async def test_emulated_search_survives_non_string_content():
    # a non-string page is a parse failure, not a crash: [] returned
    provider = StubProvider({"https://www.bing.com/search?q=x": 123})
    results = await emulated_search(provider, "x", 5)
    assert results == []


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


async def test_emulated_crawl_cycle_does_not_revisit():
    # /about links back to / — the seen-set must prevent a second fetch
    provider = StubProvider(
        {
            "https://example.com/": (
                '<html><head><title>Home</title></head><body>'
                '<a href="/about">About</a></body></html>'
            ),
            "https://example.com/about": (
                '<html><head><title>About</title></head><body>'
                '<a href="/">Home</a></body></html>'
            ),
        }
    )
    pages = await emulated_crawl(provider, "https://example.com/", max_pages=10, max_depth=2)
    assert [p.url for p in pages] == ["https://example.com/", "https://example.com/about"]
    assert len(provider.calls) == 2


async def test_emulated_crawl_dedupes_relative_and_absolute_www_links():
    # the relative link resolves to the www netloc; the absolute link has the
    # same dedupe key (www stripped) and must be fetched once
    provider = StubProvider(
        {
            "https://www.example.com/": (
                '<html><head><title>Home</title></head><body>'
                '<a href="/about">rel</a>'
                '<a href="https://www.example.com/about">abs</a>'
                "</body></html>"
            ),
            "https://www.example.com/about": (
                "<html><head><title>About</title></head><body></body></html>"
            ),
        }
    )
    pages = await emulated_crawl(provider, "https://www.example.com/", max_pages=10, max_depth=2)
    assert [p.url for p in pages] == ["https://www.example.com/", "https://www.example.com/about"]
    assert len(provider.calls) == 2


async def test_emulated_crawl_follows_scheme_switch_same_host():
    # _same_host compares netlocs only, so http->https (same host) is followed
    provider = StubProvider(
        {
            "http://example.com/": (
                '<html><head><title>Home</title></head><body>'
                '<a href="https://example.com/secure">Secure</a></body></html>'
            ),
            "https://example.com/secure": (
                "<html><head><title>Secure</title></head><body></body></html>"
            ),
        }
    )
    pages = await emulated_crawl(provider, "http://example.com/", max_pages=10, max_depth=2)
    assert [p.url for p in pages] == ["http://example.com/", "https://example.com/secure"]


async def test_emulated_crawl_max_pages_one_fetches_only_start():
    provider = StubProvider(_crawl_pages())
    pages = await emulated_crawl(provider, "https://example.com/", max_pages=1, max_depth=2)
    assert [p.url for p in pages] == ["https://example.com/"]
    assert len(provider.calls) == 1


async def test_emulated_crawl_start_url_with_fragment_dedupes_links():
    provider = StubProvider(
        {
            "https://example.com/?q=1#frag": (
                '<html><head><title>T</title></head><body>'
                '<a href="?q=1#frag">self with fragment</a>'
                '<a href="/?q=1">self no fragment</a>'
                "</body></html>"
            ),
        }
    )
    pages = await emulated_crawl(provider, "https://example.com/?q=1#frag", max_pages=10, max_depth=2)
    assert [p.url for p in pages] == ["https://example.com/?q=1#frag"]
    assert len(provider.calls) == 1


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


def test_html_to_text_handles_entities_and_collapses_whitespace():
    html = "<p>a &lt; b &gt; c &quot;d&quot; &amp; &#39;e&#39;</p>"
    assert _html_to_text(html) == "a < b > c \"d\" & 'e'"
    assert _html_to_text("<div>  many   \n\n spaces\t here </div>") == "many spaces here"


def test_html_to_text_survives_malformed_blocks():
    # unclosed <script> is not stripped (the regex needs the close tag) but
    # the output must still be clean text, not markup
    text = _html_to_text("<p>before</p><script>var x = 1;<p>after</p>")
    assert "before" in text and "after" in text
    assert "<" not in text


def test_html_to_text_empty_and_whitespace_only():
    assert _html_to_text("") == ""
    assert _html_to_text("   \n\t  ") == ""


def test_llm_max_chars_default_and_env(monkeypatch):
    assert _llm_max_chars() == 100_000
    with monkeypatch.context() as m:
        m.setenv("EXTRACT_MAX_CHARS", "5000")
        assert _llm_max_chars() == 5000
    with monkeypatch.context() as m:
        m.setenv("EXTRACT_MAX_CHARS", " 7000 ")
        assert _llm_max_chars() == 7000
    for bad in ("bogus", "0", "-10", "0.5"):
        with monkeypatch.context() as m:
            m.setenv("EXTRACT_MAX_CHARS", bad)
            with pytest.raises(ValueError, match="EXTRACT_MAX_CHARS"):
                _llm_max_chars()


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


# --- additional edge coverage -----------------------------------------------


async def test_emulated_search_caps_below_parser_yield():
    three_results = BING_HTML + """
    <li class="b_algo"><h2><a href="https://third.example/">Third</a></h2><p>s</p></li>
    """
    provider = StubProvider({"https://www.bing.com/search?q=x": three_results})
    results = await emulated_search(provider, "x", 2)
    assert [r.title for r in results] == ['Example "Domain"', "Other Org"]


async def test_emulated_extract_truncation_boundary(monkeypatch):
    seen = {}

    async def fake_summarize(text, *, instruction=None):
        seen["text"] = text
        return "# ok"

    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    with monkeypatch.context() as m:
        m.setenv("OPENROUTER_API_KEY", "k")
        m.setenv("EXTRACT_MAX_CHARS", "100")
        provider = StubProvider({"https://example.com/": "<p>" + "x" * 100 + "</p>"})
        await emulated_extract(provider, "https://example.com/")
        assert len(seen["text"]) == 100  # exactly at cap: no truncation

        provider2 = StubProvider({"https://example.com/": "<p>" + "y" * 101 + "</p>"})
        await emulated_extract(provider2, "https://example.com/")
        assert len(seen["text"]) == 100  # one over: truncated to the cap


def test_search_explanation_echoes_params_and_costs():
    payload = search_explanation("pizza", 7, freshness="week", country="us")
    emu = payload["emulation"]
    assert "one Scrappey full-browser request" in emu["how"]
    assert "js_render=true" in emu["how"]
    assert "€1/1K" in emu["how"] and "20–40s" in emu["how"]
    assert any("freshness='week' and country='us' are ignored" in d for d in emu["drawbacks"])
    assert any("€1/1K" in d for d in emu["drawbacks"])
    assert any("Bing markup or anti-bot" in d for d in emu["drawbacks"])
    assert any("max_results is a ceiling" in d for d in emu["drawbacks"])


def test_crawl_explanation_echoes_bounds_and_cost():
    payload = crawl_explanation("https://example.com", 25, 3)
    emu = payload["emulation"]
    assert "https://example.com" in emu["how"]
    assert "25 pages" in emu["how"] and "depth ≤ 3" in emu["how"]
    assert "€0.10/1K" in emu["how"]
    assert any("max_pages × basic-mode" in d for d in emu["drawbacks"])
    assert any("reported in 'failures'" in d for d in emu["drawbacks"])
    assert any("raw HTML" in d for d in emu["drawbacks"])


def test_extract_explanation_mentions_truncation_and_llm_cost():
    payload = extract_explanation("https://example.com", None, False, "huggingface")
    emu = payload["emulation"]
    # the formatted cap (100,000) appears in the drawbacks
    assert any("100,000" in d for d in emu["drawbacks"])
    assert any("huggingface" in emu["how"] or "harness LLM account" in d for d in emu["drawbacks"])
    assert any("not a lossless or structured extract" in d for d in emu["drawbacks"])


async def test_link_extraction_excludes_empty_and_protocol_hrefs():
    html = (
        '<a href="">empty</a>'
        '<a href="tel:+123">tel</a>'
        '<a href="#">frag</a>'
        '<a href="HTTP://example.com/up">case</a>'
    )
    links = _extract_links(html, "https://example.com/dir/")
    assert links == ["HTTP://example.com/up"]  # scheme case is preserved; _crawl_key lowercases for dedupe


# --- branch gaps: parser end-tag short-circuits + property fuzzing -----------


def test_bing_parser_tolerates_orphan_end_tags():
    # </h2> and </p> arriving without a matching open tag must be skipped
    # (the _in_h2/_in_p short-circuit arms), and valid results still parse
    html = (
        '<ol id="b_results"><li class="b_algo">'
        "</h2></p>"  # orphan end tags before any h2/p
        '<h2><a href="https://example.com/a">A</a></h2>'
        "<p>snippet</p>"
        "</li></ol>"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert len(parser.results) == 1
    assert parser.results[0].title == "A"
    assert parser.results[0].snippet == "snippet"


def test_bing_parser_fuzz_never_raises_or_emits_empty_results():
    import random

    rng = random.Random(20261023)
    for _ in range(40):
        parts = ['<ol id="b_results">']
        n_results = rng.randint(0, 6)
        for _ in range(n_results):
            parts.append('<li class="b_algo">')
            # random malformed noise inside the result
            for _ in range(rng.randint(0, 3)):
                parts.append(rng.choice(["<div>", "</div>", "<span class='x'>", "text", "<p>", "</li>", "<b>"]))
            if rng.random() < 0.8:
                href = f"https://example.com/{rng.randint(0, 99)}"
                parts.append(f'<h2><a href="{href}">Title {rng.randint(0, 99)}</a></h2>')
            if rng.random() < 0.8:
                parts.append(f"<p>Snippet {rng.randint(0, 99)}</p>")
            parts.append("</li>")
        parts.append("</ol>")
        html = "".join(parts)
        parser = _BingSERPParser()
        parser.feed(html)
        assert isinstance(parser.results, list)
        assert all(r.title and r.url for r in parser.results)
        assert len(parser.results) <= n_results


def test_html_to_text_fuzz_never_raises_or_leaks_markup():
    import random

    rng = random.Random(20261023)
    # note: bracket entities (&lt;/&gt;) decode to real brackets by design
    # (meaningful text), so the pool only carries raw brackets
    soup = ["<div>", "</div>", "<p>", "</p>", "<script>", "</script>", "&amp;", "text ",
            "<a href='x'>", "</a>", "<b>", "1 < 2", "a > b", "<style>.c{}</style>", "<h1>", "\n\n  "]
    for _ in range(50):
        html = "".join(rng.choice(soup) for _ in range(rng.randint(0, 200)))
        text = _html_to_text(html)
        assert isinstance(text, str)
        assert "<" not in text and ">" not in text  # raw brackets never leak


async def test_emulated_crawl_random_graph_invariants():
    import random

    rng = random.Random(20261023)
    internal = [f"https://example.com/page{i}" for i in range(20)]
    pages: dict[str, str] = {}
    for url in internal:
        links = []
        for _ in range(rng.randint(0, 6)):
            choice = rng.random()
            if choice < 0.7:
                links.append(rng.choice(internal))
            elif choice < 0.85:
                links.append("https://external.example.com/x")   # other host
            else:
                links.append("https://example.com/logo.png")      # asset
        html = "<html><head><title>T</title></head><body>" + "".join(
            f'<a href="{link}">{link}</a>' for link in links
        ) + "</body></html>"
        pages[url] = html
    provider = StubProvider(pages)
    start = internal[0]
    results = await emulated_crawl(provider, start, max_pages=8, max_depth=3)
    assert len(results) <= 8
    fetched = [p.url for p in results]
    # invariant: every fetched page is unique and same-host
    assert len(set(fetched)) == len(fetched)
    assert all(urlparse(p).netloc == "example.com" for p in fetched)
    # external hosts and assets were never fetched (StubProvider would KeyError)
    assert all(not _is_skippable(p) for p in fetched)
    assert len(provider.calls) == len(fetched)


def test_bing_parser_defensive_guards_under_corrupt_state():
    # h2/p end tags can reference no in-flight result when internal state is
    # inconsistent (malicious/malformed input); the guards must hold, not crash
    parser = _BingSERPParser()
    parser._algo_depth = 1
    parser._li_stack = [True]
    parser._in_h2 = True
    parser._cur = None
    parser._buf = None
    parser.feed("</h2>")  # h2 close with no result context
    assert parser.results == []

    parser._algo_depth = 1
    parser._li_stack = [True]
    parser._in_p = True
    parser._cur = None
    parser._buf = None
    parser.feed("</p>")  # p close with no result context
    assert parser.results == []


def test_bing_parser_nested_b_algo_depth():
    # nested b_algo openers must not start a second result until the outer one
    # closes; the depth counter drives both the start and end paths
    html = (
        '<li class="b_algo"><h2><a href="https://e/a">A</a></h2>'
        '<li class="b_algo"><h2><a href="https://e/b">B</a></h2></li>'
        "<p>outer snippet</p></li>"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    parser.close()
    assert [(r.title, r.url) for r in parser.results] == [("A", "https://e/a")]
    assert parser.results[0].snippet == "outer snippet"


# --- truncated SERP handling --------------------------------------------


def test_bing_parser_flushes_truncated_final_result():
    # a response cut mid-<li> (proxy truncation) must still yield the result
    # whose title, href, and snippet all arrived
    html = (
        '<ol id="b_results"><li class="b_algo">'
        '<h2><a href="https://example.com/a">A</a></h2><p>first</p></li>'
        '<li class="b_algo"><h2><a href="https://example.com/b">B</a></h2><p>second</p>'
    )
    parser = _BingSERPParser()
    parser.feed(html)
    assert [r.title for r in parser.results] == ["A"]
    parser.close()
    assert [r.title for r in parser.results] == ["A", "B"]
    assert parser.results[1].url == "https://example.com/b"
    assert parser.results[1].snippet == "second"
    parser.close()  # idempotent: closing twice must not duplicate the result
    assert len(parser.results) == 2


def test_bing_parser_close_without_open_result_is_noop():
    parser = _BingSERPParser()
    parser.feed("<ol><li>not a result</li></ol>")
    parser.close()
    assert parser.results == []


async def test_emulated_search_keeps_truncated_tail():
    html = (
        '<li class="b_algo"><h2><a href="https://example.com/a">A</a></h2>'
        '<p>snip</p></li><li class="b_algo"><h2><a href="https://example.com/b">B</a>'
    )
    provider = StubProvider({"https://www.bing.com/search?q=x": html})
    results = await emulated_search(provider, "x", 5)
    assert [r.title for r in results] == ["A", "B"]


# --- query encoding and result caps -------------------------------------


async def test_emulated_search_encodes_special_and_non_ascii_query():
    fetched: list[str] = []

    async def capture(url: str, **kw) -> FetchedPage:
        fetched.append(url)
        return FetchedPage(
            url=url,
            content='<li class="b_algo"><h2><a href="https://example.com/a">A</a></h2></li>',
            title=None,
        )

    provider = StubProvider()
    provider.fetch = capture  # type: ignore[method-assign]
    results = await emulated_search(provider, "a b&c=d/ç", 1)
    assert fetched == ["https://www.bing.com/search?q=a+b%26c%3Dd%2F%C3%A7"]
    assert len(results) == 1
    assert results[0].title == "A"


async def test_emulated_search_dedupes_after_redirect_decoding():
    target = "https://example.com/same"
    encoded = base64.urlsafe_b64encode(target.encode()).decode()
    unpadded = encoded.rstrip("=")
    html = (
        f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u={unpadded}">A</a></h2></li>'
        f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u={encoded}">B</a></h2></li>'
    )
    provider = StubProvider({"https://www.bing.com/search?q=x": html})
    results = await emulated_search(provider, "x", 5)
    # both redirects decode to the same URL: one result, first title wins
    assert [(r.title, r.url) for r in results] == [("A", target)]


async def test_emulated_search_max_results_one_truncates_parser_yield():
    html = "".join(
        f'<li class="b_algo"><h2><a href="https://example.com/{i}">R{i}</a></h2></li>'
        for i in range(5)
    )
    provider = StubProvider({"https://www.bing.com/search?q=x": html})
    results = await emulated_search(provider, "x", 1)
    assert [r.title for r in results] == ["R0"]


# --- block stripping: semantics, coverage, and linear time ---------------


@pytest.mark.parametrize("tag", ["script", "style", "noscript", "template", "svg", "iframe", "head"])
def test_html_to_text_drops_every_block_tag(tag):
    html = f"<div>keep1</div><{tag} class='x'>DROP</{tag}><div>keep2</div>"
    text = _html_to_text(html)
    assert "DROP" not in text
    assert "keep1" in text and "keep2" in text


def test_html_to_text_block_strip_is_case_insensitive():
    assert "DROP" not in _html_to_text("<SCRIPT>DROP</SCRIPT><p>keep</p>")
    assert "DROP" not in _html_to_text("<Style>DROP</STYLE><p>keep</p>")


def test_strip_blocks_equivalent_to_reference_regex():
    # the linear scanner must match the documented regex semantics exactly,
    # including the single space left where a block was removed
    reference = re.compile(
        r"<(script|style|noscript|template|svg|iframe|head)[^>]*>.*?</\1\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    tokens = [
        "<script>", "</script>", "<style >", "</style>", "<head>", "</head>",
        "<svg>", "</svg>", "<div>", "</div>", "<p>", "<SCRIPT>", "</SCRIPT>",
        "text", " ", "<b>", "</b>", "<script x=1>", "</script >", "<template>",
        "</template>", "<noscript>", "</noscript>", "<", "a1", "\n",
    ]
    rng = random.Random(20261023)
    for _ in range(3000):
        soup = "".join(rng.choice(tokens) for _ in range(rng.randint(0, 30)))
        assert _strip_blocks(soup) == reference.sub(" ", soup), soup


def test_block_stripping_stays_linear_on_unterminated_openers():
    # 8000 unterminated openers: the backtracking pattern rescanned to EOF per
    # opener (seconds to minutes, blocking the event loop); linear must be ms
    soup = "<script>" * 8000 + "<p>keep</p>"
    started = time.monotonic()
    text = _html_to_text(soup)
    elapsed = time.monotonic() - started
    assert text == "keep"
    assert elapsed < 2.0, f"block stripping is superlinear: {elapsed:.2f}s"


# --- URL helpers --------------------------------------------------------


def test_skippable_trailing_slash_and_path_segments():
    assert _is_skippable("https://example.com/dl/report.pdf/")
    assert _is_skippable("https://example.com/a.PDF")
    assert _is_skippable("https://example.com/x.mp4?token=1")
    assert not _is_skippable("https://example.com/dir/file.pdf/page")
    assert not _is_skippable("https://example.com/v1.2")
    assert not _is_skippable("https://example.com/")


def test_unredirect_mixed_case_host_and_extra_params():
    target = "https://example.com/page?a=1"
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    url = f"https://WWW.Bing.COM/ck/a?!&&p=abc&u={encoded}&ntb=1"
    assert _unredirect(url) == target


def test_unredirect_first_u_param_wins():
    first = base64.urlsafe_b64encode(b"https://first.example/").decode().rstrip("=")
    second = base64.urlsafe_b64encode(b"https://second.example/").decode().rstrip("=")
    assert _unredirect(f"https://www.bing.com/ck/a?u={first}&u={second}") == "https://first.example/"


def test_title_extraction_nested_tags_and_unterminated():
    assert _extract_title("<title> A <b>B</b> </title>") == "A B"
    assert _extract_title("<title>   </title>") is None
    assert _extract_title("<title>never closed") is None
    assert _extract_title("<TITLE>Upper</TITLE>") == "Upper"


def test_crawl_key_root_path_and_port():
    assert _crawl_key("https://example.com") == "https://example.com/"
    assert _crawl_key("HTTPS://WWW.Example.COM/A") == "https://example.com/A"
    assert _crawl_key("https://example.com:8443/a#f") == "https://example.com:8443/a"


def test_link_extraction_protocol_relative_and_uppercase_href():
    links = _extract_links(
        '<a HREF="//cdn.example/x.js">js</a><a href="  a b ">sp</a>',
        "https://example.com/dir/page",
    )
    assert links == ["https://cdn.example/x.js", "https://example.com/dir/a b"]


# --- crawl edge cases ---------------------------------------------------


async def test_emulated_crawl_skips_asset_with_trailing_slash():
    pages = {
        "https://example.com/": '<a href="/report.pdf/">pdf</a><a href="/about">a</a>',
        "https://example.com/about": "<p>about</p>",
    }
    provider = StubProvider(pages)
    results = await emulated_crawl(provider, "https://example.com/", max_pages=5, max_depth=1)
    assert [p.url for p in results] == ["https://example.com/", "https://example.com/about"]
    assert all("report" not in url for url, _ in provider.calls)


async def test_emulated_crawl_zero_depth_fetches_only_start():
    provider = StubProvider({"https://example.com/": '<a href="/a">a</a>'})
    pages = await emulated_crawl(provider, "https://example.com/", max_pages=5, max_depth=0)
    assert [p.url for p in pages] == ["https://example.com/"]


# --- extract truncation boundaries --------------------------------------


async def test_emulated_extract_truncates_multibyte_text_by_chars(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = StubProvider({"https://example.com/": "<p>日本語のテキストです</p>"})
    seen: list[str] = []

    async def fake_summarize(text, *, instruction=None):
        seen.append(text)
        return "summary"

    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    with monkeypatch.context() as m:
        m.setenv("EXTRACT_MAX_CHARS", "5")
        result = await emulated_extract(provider, "https://example.com/")
    assert result == "summary"
    assert seen == ["日本語のテ"]


async def test_emulated_extract_exactly_at_limit_is_not_truncated(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = StubProvider({"https://example.com/": "<p>" + "abcdef" + "</p>"})
    seen: list[str] = []

    async def fake_summarize(text, *, instruction=None):
        seen.append(text)
        return "summary"

    monkeypatch.setattr("scrape_n_search.llm.summarize", fake_summarize)
    with monkeypatch.context() as m:
        m.setenv("EXTRACT_MAX_CHARS", "6")
        await emulated_extract(provider, "https://example.com/")
    assert seen == ["abcdef"]


def test_bing_parser_flushes_truncated_snippet_text():
    # response cut mid-<p>: the title and url arrived, so the partial snippet
    # must be kept rather than dropping the whole result
    html = (
        '<li class="b_algo"><h2><a href="https://example.com/a">A</a></h2>'
        "<p>partial snippet without end tag"
    )
    parser = _BingSERPParser()
    parser.feed(html)
    parser.close()
    assert [(r.title, r.snippet) for r in parser.results] == [
        ("A", "partial snippet without end tag")
    ]


def test_bing_parser_flush_without_result_is_guarded():
    # inconsistent internal state (depth says "in result", no result buffered)
    # must not crash the flush path
    parser = _BingSERPParser()
    parser._algo_depth = 1
    parser._li_stack = [True]
    parser._cur = None
    parser.feed("</li>")
    assert parser.results == []
    parser._algo_depth = 1
    parser._li_stack = [True]
    parser.close()
    assert parser.results == []


# --- invariants ---------------------------------------------------------


def test_unredirect_is_idempotent_and_never_leaves_tracking_url():
    rng = random.Random(4242)
    for _ in range(200):
        target = (
            f"https://example{rng.randint(0, 9)}.com/p{rng.randint(0, 999)}"
            f"?q={rng.randint(0, 9)}"
        )
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        redirect = f"https://www.bing.com/ck/a?!&&p={rng.randint(0, 99)}&u={encoded}"
        decoded = _unredirect(redirect)
        assert decoded == target
        assert _unredirect(decoded) == decoded  # decoding is idempotent


async def test_emulated_crawl_never_fetches_beyond_max_depth():
    pages = {
        "https://example.com/": '<a href="/l1">l1</a>',
        "https://example.com/l1": '<a href="/l2">l2</a>',
        "https://example.com/l2": '<a href="/l3">l3</a>',
    }
    provider = StubProvider(pages)
    results = await emulated_crawl(provider, "https://example.com/", max_pages=10, max_depth=1)
    assert [p.url for p in results] == ["https://example.com/", "https://example.com/l1"]
    assert [url for url, _ in provider.calls] == [
        "https://example.com/",
        "https://example.com/l1",
    ]


def test_strip_blocks_accepts_whitespace_in_end_tag():
    assert _html_to_text("<script>alert(1)</script >visible") == "visible"
    assert _html_to_text("<style>DROP</STYLE\n><p>keep</p>") == "keep"
    assert _html_to_text("<head>DROP</HEAD\t><p>keep</p>") == "keep"


async def test_emulated_crawl_partial_failures_are_reported():
    class FlakyProvider(StubProvider):
        async def fetch(self, url: str, **kw) -> FetchedPage:
            self.calls.append((url, dict(kw)))
            if url.endswith("/broken"):
                raise ProviderError("page 500")
            return FetchedPage(url=url, content=self.pages[url], title=None)

    provider = FlakyProvider(
        {
            "https://example.com/": '<a href="/ok">ok</a><a href="/broken">broken</a>',
            "https://example.com/ok": "<p>fine</p>",
        }
    )
    outcome = await emulated_crawl(provider, "https://example.com/", max_pages=10, max_depth=2)
    assert [p.url for p in outcome.pages] == ["https://example.com/", "https://example.com/ok"]
    assert [f.url for f in outcome.failures] == ["https://example.com/broken"]
    assert "page 500" in outcome.failures[0].error
    assert len(outcome) == 2  # iterable/sized over pages


async def test_emulated_crawl_start_failure_still_aborts():
    class DownProvider(StubProvider):
        async def fetch(self, url: str, **kw) -> FetchedPage:
            self.calls.append((url, dict(kw)))
            raise ProviderError("start down")

    with pytest.raises(ProviderError, match="start down"):
        await emulated_crawl(DownProvider({}), "https://example.com/", max_pages=5, max_depth=2)


async def test_emulated_crawl_follows_apex_and_www_variants():
    provider = StubProvider(
        {
            "https://example.com/": '<a href="https://www.example.com/about">www link</a>',
            "https://www.example.com/about": '<a href="https://example.com/contact">apex link</a>',
            "https://example.com/contact": "<p>done</p>",
        }
    )
    outcome = await emulated_crawl(provider, "https://example.com/", max_pages=10, max_depth=3)
    assert [p.url for p in outcome.pages] == [
        "https://example.com/",
        "https://www.example.com/about",
        "https://example.com/contact",
    ]


async def test_emulated_crawl_ignores_links_in_scripts_and_comments():
    provider = StubProvider(
        {
            "https://example.com/": (
                "<!-- <a href='/commented'>x</a> -->"
                "<script>var s = '<a href=\"/scripted\">y</a>';</script>"
                '<a href="/real">real</a>'
            ),
            "https://example.com/real": "<p>ok</p>",
        }
    )
    outcome = await emulated_crawl(provider, "https://example.com/", max_pages=10, max_depth=1)
    assert [url for url, _ in provider.calls] == [
        "https://example.com/",
        "https://example.com/real",
    ]
    assert len(outcome.pages) == 2
