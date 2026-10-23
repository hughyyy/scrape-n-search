"""Normalized result models shared across providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published_date: str | None = None
    score: float | None = None


@dataclass
class FetchedPage:
    url: str
    content: str
    title: str | None = None
    format: str = "markdown"


@dataclass
class BrowserActionsResult:
    url: str
    content: str
    format: str = "markdown"
    javascript_return: list = field(default_factory=list)


@dataclass
class CrawlPage:
    url: str
    title: str | None = None
    content: str | None = None


@dataclass
class CrawlFailure:
    """A page the emulated crawl could not fetch (partial-result reporting)."""

    url: str
    error: str


@dataclass
class CrawlOutcome:
    """Emulated crawl result: completed pages plus per-page failures.

    Iterable/sized over ``pages`` for callers that only need the results;
    ``failures`` carries the pages that could not be fetched.
    """

    pages: list[CrawlPage] = field(default_factory=list)
    failures: list[CrawlFailure] = field(default_factory=list)

    def __iter__(self):
        return iter(self.pages)

    def __len__(self) -> int:
        return len(self.pages)


def to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses (and containers) to plain JSON values.

    ``None`` fields are omitted from dataclass output (the unified response
    schema treats them as absent), while arbitrary dicts keep their keys
    unchanged so provider payloads are not silently rewritten.
    """
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {key: to_dict(value) for key, value in asdict(obj).items() if value is not None}
    return obj
