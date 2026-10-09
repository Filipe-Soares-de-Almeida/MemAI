"""Layered task reads: a head with counts, then one offset per collection."""

from __future__ import annotations

import pytest

from conftest import brief, item_at
from memai import budget, tasks
from memai.store import connection, memories


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        yield c


@pytest.fixture
def uid(conn):
    u = tasks.create_task(conn, title="Ship the parser", goal="Parse every config file",
                          items=["read the spec", "write the lexer"], domain="acme/parser")
    tasks.add_note(conn, u, title="Pick-up rules", body="Claim first.", items=[])
    tasks.add_note(conn, u, title="Lexer rules", body=brief("ASCII only."), items=[item_at(conn, u, 2)])
    tasks.add_comment(conn, u, "on the task")
    tasks.add_comment(conn, u, "on the lexer", item=item_at(conn, u, 2))
    other = memories.insert_memory(conn, type="note", content="Lexers are fun.", title="Lexers",
                             domain="acme/parser")
    tasks.link_item(conn, u, item_at(conn, u, 2), [other])
    return u


def test_head_counts_task_level_records_only(conn, uid):
    head = tasks.head(conn, uid)
    assert head["goal"] == "Parse every config file"
    assert head["state"] == "open"
    assert head["progress"] == {"done": 0, "dropped": 0, "total": 2}
    assert head["counts"] == {"items": 2, "notes": 1, "comments": 1}
    assert "items" not in head


def test_items_page_carries_per_item_counts(conn, uid):
    page = tasks.read_part(conn, uid, "items")
    assert page["total"] == 2 and "next_offset" not in page
    assert page["records"][1] == {"id": item_at(conn, uid, 2), "n": 2, "state": "todo", "text": "write the lexer",
                                  "counts": {"notes": 1, "comments": 1, "links": 1}}


def test_each_part_scopes_by_item(conn, uid):
    assert [n["title"] for n in tasks.read_part(conn, uid, "notes")["records"]] == ["Pick-up rules"]
    assert [n["title"] for n in tasks.read_part(conn, uid, "notes", item_at(conn, uid, 2))["records"]] == ["Lexer rules"]
    assert [c["body"] for c in tasks.read_part(conn, uid, "comments")["records"]] == ["on the task"]
    assert [c["body"] for c in tasks.read_part(conn, uid, "comments", str(item_at(conn, uid, 2)))["records"]] == ["on the lexer"]
    link = tasks.read_part(conn, uid, "links", item_at(conn, uid, 2))["records"][0]
    assert set(link) == {"uid", "type", "title", "est_tokens"}


@pytest.mark.parametrize("part,item", [("links", 0), ("items", 1), ("bogus", 0),
                                       ("notes", 999_999), ("notes", "i1")])
def test_invalid_reads_are_value_errors(conn, uid, part, item):
    with pytest.raises(ValueError):
        tasks.read_part(conn, uid, part, item)


@pytest.mark.parametrize("offset", [-1, "3", True])
def test_bad_offsets_are_value_errors(conn, uid, offset):
    with pytest.raises(ValueError):
        tasks.read_part(conn, uid, "items", offset=offset)


def test_offset_past_the_end_is_an_empty_last_page(conn, uid):
    page = tasks.read_part(conn, uid, "items", offset=9)
    assert page["records"] == [] and page["total"] == 2 and "next_offset" not in page


def test_not_a_task_is_a_value_error(conn):
    other = memories.insert_memory(conn, type="note", content="x", title="x", domain="acme")
    with pytest.raises(ValueError):
        tasks.head(conn, other)
    with pytest.raises(ValueError):
        tasks.read_part(conn, other, "items")


def test_walking_next_offset_returns_every_comment_once(conn, uid):
    for n in range(60):
        tasks.add_comment(conn, uid, f"comment {n} " + "z" * 1900)
    seen, offset, pages = [], 0, 0
    while offset is not None:
        page = tasks.read_part(conn, uid, "comments", offset=offset)
        assert budget.result_chars(page) <= budget.PAGE_MAX_CHARS + 500
        seen += [c["id"] for c in page["records"]]
        offset = page.get("next_offset")
        pages += 1
    assert len(seen) == len(set(seen)) == 61 and pages > 1
