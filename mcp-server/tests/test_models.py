"""Model normalization tests (models.to_dict)."""

from __future__ import annotations

from scrape_n_search.models import (
    BrowserActionsResult,
    CrawlPage,
    FetchedPage,
    SearchResult,
    to_dict,
)


def test_to_dict_omits_none_fields():
    page = FetchedPage(url="https://x", content="# body", title=None)
    assert to_dict(page) == {"url": "https://x", "content": "# body", "format": "markdown"}


def test_to_dict_keeps_filled_optional_fields():
    page = FetchedPage(url="https://x", content="# body", title="Title")
    assert to_dict(page)["title"] == "Title"


def test_to_dict_nested_dataclass_in_list():
    results = [SearchResult(title="A", url="https://a"), SearchResult(title="B", url="https://b", snippet="s")]
    d = to_dict(results)
    assert d == [
        {"title": "A", "url": "https://a", "snippet": ""},
        {"title": "B", "url": "https://b", "snippet": "s"},
    ]


def test_to_dict_crawl_page_optional_content():
    assert to_dict(CrawlPage(url="https://x", title=None, content=None)) == {"url": "https://x"}


def test_to_dict_list_field_passthrough():
    result = BrowserActionsResult(url="https://x", content="# c", javascript_return=["a", "b"])
    d = to_dict(result)
    assert d["javascript_return"] == ["a", "b"]


def test_to_dict_converts_dicts_recursively():
    nested = {"outer": [SearchResult(title="A", url="https://a")], "n": None}
    assert to_dict(nested) == {
        "outer": [{"title": "A", "url": "https://a", "snippet": ""}],
        "n": None,
    }
    assert to_dict("plain") == "plain"
    assert to_dict(42) == 42
    assert to_dict(None) is None
