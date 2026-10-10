"""The size ceilings every MCP result and hook output is held to, and how they are measured."""

from __future__ import annotations

import copy
import json

# Claude Code moves a tool result over 50,000 characters, and a hook field over 10,000, to a file.
MCP_RESULT_MAX_CHARS = 40_000
PAGE_MAX_CHARS = 20_000
HOOK_MAX_CHARS = 9_000


def text_of(result) -> str:
    """The text the MCP SDK sends for `result`; tests pin it to func_metadata._convert_to_content."""
    if isinstance(result, str):
        return result
    if isinstance(result, list | tuple):
        # The SDK sends each element of a list result as its own text block.
        return "".join(text_of(item) for item in result)
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


def result_chars(result) -> int:
    return len(text_of(result))


def item_chars(item) -> int:
    """What `item` adds, separator included, to a list held directly in a dict result."""
    return len(text_of({"records": [item]})) - len(text_of({"records": []})) + 2


# Strings this short name things (uids, keys, labels) and are never cut; a record made of them is
# shortened through its lists instead.
_CUT_FROM = 64


def _longest(node, kind: type, floor: int):
    """(container, key, value) of the longest `kind` value inside `node` at least `floor` long."""
    best = None
    pairs = node.items() if isinstance(node, dict) else enumerate(node)
    for key, value in pairs:
        if isinstance(value, kind) and len(value) >= floor and (best is None or len(value) > len(best[2])):
            best = (node, key, value)
        if isinstance(value, dict | list):
            inner = _longest(value, kind, floor)
            if inner and (best is None or len(inner[2]) > len(best[2])):
                best = inner
    return best


def fit(item, max_chars: int):
    """`item` as it fits `max_chars` as one list element; an item that already fits comes back as is.

    The longest strings are halved first, each with `<key>_chars` beside it holding its full
    length, then the longest lists, with `<key>_total`; a cut dict says `clipped: true`. An item
    with nothing left to cut comes back as it stands.
    """
    if item_chars(item) <= max_chars:
        return item
    item = copy.deepcopy(item)
    while item_chars(item) > max_chars:
        found = _longest(item, str, _CUT_FROM)
        if found:
            parent, key, value = found
            if isinstance(key, str):
                parent.setdefault(f"{key}_chars", len(value))
            parent[key] = value[: len(value) // 2] + "…"
        elif found := _longest(item, list, 2):
            parent, key, value = found
            if isinstance(key, str):
                parent.setdefault(f"{key}_total", len(value))
            parent[key] = value[: len(value) // 2]
        else:
            break
        if isinstance(item, dict):
            item["clipped"] = True
    return item


def _offset(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"offset must be a whole number of 0 or more, got {value!r}")
    return value


def page(records: list, offset: int, max_chars: int = PAGE_MAX_CHARS) -> tuple[list, int | None]:
    """Records from `offset` that fit in `max_chars` (at least one), and the next offset or None.

    Measured as the list sits inside a dict result, the only shape a page is returned in. A first
    record too big for the page on its own goes through fit().
    """
    start = _offset(offset)
    taken: list = []
    size = len(text_of({"records": []}))
    for record in records[start:]:
        if not taken:
            record = fit(record, max_chars - size)
        cost = item_chars(record)
        if taken and size + cost > max_chars:
            break
        taken.append(record)
        size += cost
    end = start + len(taken)
    return taken, (end if end < len(records) else None)


def text_chunk(text: str, offset: int, max_chars: int = PAGE_MAX_CHARS) -> tuple[str, int | None]:
    """The piece of `text` from `offset` whose JSON-escaped form fits `max_chars`, and the next offset."""
    start = _offset(offset)
    end = min(len(text), start + max_chars)
    while end > start + 1 and len(json.dumps(text[start:end], ensure_ascii=False)) > max_chars:
        end = start + (end - start) * 3 // 4
    return text[start:end], (end if end < len(text) else None)


def clip(text: str, max_chars: int, tail: str) -> str:
    """`text` cut at the last line break within `max_chars`, with `tail` on its own line."""
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    return f"{text[:cut if cut > 0 else max_chars].rstrip()}\n{tail}"
