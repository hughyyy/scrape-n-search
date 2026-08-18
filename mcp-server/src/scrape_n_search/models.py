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


def to_dict(obj: Any) -> dict[str, Any]:
    """Recursively convert a dataclass tree to plain dicts."""
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_dict(v) for k, v in asdict(obj).items() if v is not None}
    return obj
