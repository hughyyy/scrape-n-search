"""HTML-to-text helpers shared by the emulation layer and providers.

The block stripper drops markup-carrying elements (``script``, ``style``,
``head``, …) together with their content in one linear pass. A regex such as
``<(tag)[^>]*>.*?</\\1>`` is equivalent for well-formed input but quadratic on
tag soup (many unterminated openers rescan to end-of-input per opener), which
would stall the event loop on a hostile page. A differential test pins the
output to the documented regex semantics.

End tags may carry whitespace before ``>`` (``</script >``) per the HTML
grammar; both the scanner and the reference semantics used by the tests accept
it.
"""

from __future__ import annotations

import html as _html
import re
from bisect import bisect_left

# Blocks that carry markup, scripting, or chrome — never page content.
_BLOCK_TAG_GROUP = r"(script|style|noscript|template|svg|iframe|head)"
_BLOCK_OPEN_RE = re.compile(rf"<{_BLOCK_TAG_GROUP}\b[^>]*>", re.IGNORECASE | re.DOTALL)
_BLOCK_CLOSE_RE = re.compile(rf"</{_BLOCK_TAG_GROUP}\s*>", re.IGNORECASE)

# A tag may not contain '<'; excluding it keeps the scan linear on tag soup.
_TAG_RE = re.compile(r"<[^<>]+>")


def clean_text(text: str) -> str:
    """Collapse whitespace runs and strip the ends."""
    return re.sub(r"\s+", " ", text).strip()


def strip_blocks(html_text: str) -> str:
    """Drop block elements together with their content, in one linear pass.

    Equivalent to ``re.sub(r"<(tag)[^>]*>.*?</\\1\\s*>", " ", html_text)``
    (first closer wins, case-insensitive) — including the single space left
    behind — but without the quadratic rescan that pattern performs on input
    with many unterminated openers: one hostile page must not stall the event
    loop.
    """
    closers: dict[str, list[re.Match[str]]] = {}
    for match in _BLOCK_CLOSE_RE.finditer(html_text):
        closers.setdefault(match.group(1).lower(), []).append(match)
    if not closers:
        return html_text
    starts = {tag: [match.start() for match in matches] for tag, matches in closers.items()}
    kept: list[str] = []
    consumed_until = 0
    for match in _BLOCK_OPEN_RE.finditer(html_text):
        if match.start() < consumed_until:
            continue  # inside a block already dropped
        tag = match.group(1).lower()
        matches = closers.get(tag)
        if not matches:
            continue
        index = bisect_left(starts[tag], match.end())
        if index == len(matches):
            continue
        kept.append(html_text[consumed_until : match.start()])
        kept.append(" ")
        consumed_until = matches[index].end()
    if consumed_until == 0:
        return html_text
    kept.append(html_text[consumed_until:])
    return "".join(kept)


def html_to_text(html_text: str) -> str:
    """Strip page HTML to readable text.

    Drops script/style/chrome blocks and tags, removes stray raw angle brackets
    (e.g. ``1 < 2`` in text content), unescapes entities, and collapses
    whitespace. Entity-encoded brackets (``&lt;``/``&gt;``) are decoded after
    the raw ones are removed.
    """
    without_blocks = strip_blocks(html_text)
    without_tags = _TAG_RE.sub(" ", without_blocks)
    without_brackets = re.sub(r"[<>]", " ", without_tags)
    return clean_text(_html.unescape(without_brackets))
