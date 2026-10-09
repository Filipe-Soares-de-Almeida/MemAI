"""Deleting a task item through task_item: alone in its call, and no other item moves."""

from __future__ import annotations

import asyncio

import pytest

from conftest import brief, item_at, item_of
from memai import server, tasks
from memai.store import connection

ITEMS = ["Draft the grammar", "Write the lexer", "Wire the CLI", "Ship the release"]


@pytest.fixture
def uid(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        return tasks.create_task(c, title="Ship the config parser", goal="A parser for the config.",
                                 items=ITEMS, domain="acme/parser")


def _texts(uid):
    with connection.connect() as c:
        return [i["text"] for i in tasks.get_task(c, uid)["items"]]


def test_a_delete_removes_the_item_and_moves_no_other(uid):
    second, third = item_of(uid, 2), item_of(uid, 3)
    out = server.task_item(uid, second, delete=True)
    assert out["deleted"] is True and out["item"] == second and "renumbered" not in out
    assert _texts(uid) == ["Draft the grammar", "Wire the CLI", "Ship the release"]
    assert item_of(uid, 2) == third


def test_a_delete_stands_alone_in_its_call(uid):
    out = server.task_item(uid, item_of(uid, 2), delete=True, state="done")
    assert out["ok"] is False and "alone" in out["errors"][0]
    assert _texts(uid) == ITEMS


def test_the_only_item_is_not_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        one = tasks.create_task(c, title="Tag the release", goal="The release is tagged.",
                                items=["Tag the release"], domain="acme/parser")
    out = server.task_item(one, item_of(one, 1), delete=True)
    assert out["ok"] is False and "at least one item" in out["errors"][0]


def test_a_brief_depending_on_a_deleted_item_keeps_its_text(uid):
    with connection.connect() as c:
        first = item_at(c, uid, 1)
        nid = tasks.add_note(c, uid, title="Release", items=[item_at(c, uid, 4)],
                             body=brief("publish it", depends_on=f"{first} (the grammar), {item_at(c, uid, 3)}"))
        third = item_at(c, uid, 3)
    server.task_item(uid, first, delete=True)
    with connection.connect() as c:
        assert tasks.brief_fields(tasks.note(c, uid, nid)["body"])["depends_on"] == (
            f'deleted "Draft the grammar" (the grammar), [[#{third}]]')


def test_task_item_says_when_to_delete_and_when_to_drop():
    [t] = [t for t in asyncio.run(server.mcp.list_tools()) if t.name == "task_item"]
    props = t.input_schema["properties"]
    assert "dropped" in props["delete"]["description"] and "No other item moves" in props["delete"]["description"]
    assert "expect" not in props
