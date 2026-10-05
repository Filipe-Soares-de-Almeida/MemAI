"""The output ceilings, the exact text measure and the paginator."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.utilities.func_metadata import _convert_to_content

from memai import budget


@pytest.mark.parametrize("result", [
    {"uid": "abc", "items": [{"key": "i1", "text": "ação é útil \"q\" \n"}], "n": 3},
    {"ok": False, "errors": ["x"], "none": None, "empty": [], "nested": {}},
    [{"a": 1}, {"b": True}],
    {"tab": "a\tb", "emoji": "\U0001f600", "ctrl": "\x01", "f": 1.5, "big": 10**20},
])
def test_text_of_matches_what_the_sdk_emits(result):
    blocks = _convert_to_content(result)
    assert budget.text_of(result) == "".join(b.text for b in blocks)


def test_a_string_result_is_measured_as_is():
    assert budget.result_chars("plain text") == len("plain text")


def test_page_fills_up_to_the_budget_and_points_at_the_rest():
    records = [{"body": "x" * 100} for _ in range(10)]
    first, nxt = budget.page(records, 0, max_chars=450)
    assert 1 <= len(first) < 10 and nxt == len(first)
    assert budget.result_chars({"records": first}) <= 450


def test_page_always_returns_one_record_even_when_it_is_too_big():
    records = [{"body": "x" * 1000}, {"body": "y"}]
    first, nxt = budget.page(records, 0, max_chars=100)
    assert first == [records[0]] and nxt == 1


def test_last_page_has_no_next_offset():
    records = [{"n": i} for i in range(3)]
    rows, nxt = budget.page(records, 1)
    assert rows == records[1:] and nxt is None


def test_offset_past_the_end_is_an_empty_last_page():
    assert budget.page([{"n": 1}], 5) == ([], None)


@pytest.mark.parametrize("bad", [-1, "x", 1.5, True])
def test_bad_offset_is_a_value_error(bad):
    with pytest.raises(ValueError):
        budget.page([], bad)


def test_clip_cuts_at_a_line_break_and_appends_the_tail():
    text = "line one\nline two\nline three"
    out = budget.clip(text, 20, "[more: see x]")
    assert len(out) <= 20 + len("\n[more: see x]")
    assert out.endswith("[more: see x]") and "line three" not in out


def test_clip_leaves_short_text_alone():
    assert budget.clip("short", 100, "tail") == "short"


def test_text_chunk_walks_a_long_text_in_pieces_that_fit_once_escaped():
    text = ('"quoted"\n' * 3000) + "tail"
    pieces, offset = [], 0
    while offset is not None:
        chunk, offset = budget.text_chunk(text, offset, max_chars=5000)
        assert len(budget.text_of({"text": chunk})) <= 5000 + 20
        pieces.append(chunk)
    assert "".join(pieces) == text and len(pieces) > 1


def test_text_chunk_of_a_short_text_is_the_text():
    assert budget.text_chunk("short", 0) == ("short", None)


def test_text_chunk_refuses_a_bad_offset():
    with pytest.raises(ValueError):
        budget.text_chunk("abc", -1)
