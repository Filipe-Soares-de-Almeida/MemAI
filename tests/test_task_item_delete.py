"""Deleting a task item through task_item: guarded by the text the caller read."""

from __future__ import annotations

import asyncio

import pytest

from conftest import brief, item_at
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


def test_a_delete_given_the_current_text_removes_the_item_and_reports_the_renumbering(uid):
    out = server.task_item(uid, "2", delete=True, expect="Write the lexer")
    assert out["deleted"] is True and out["item"] == "i2"
    assert out["renumbered"] == {"i3": "i2", "i4": "i3"}
    assert _texts(uid) == ["Draft the grammar", "Wire the CLI", "Ship the release"]


@pytest.mark.parametrize("expect, match", [("", "current text"), ("Draft the grammar", "no longer")])
def test_a_delete_without_the_current_text_is_refused(uid, expect, match):
    out = server.task_item(uid, "2", delete=True, expect=expect)
    assert out["ok"] is False and match in out["errors"][0]
    assert _texts(uid) == ITEMS


def test_a_delete_stands_alone_in_its_call(uid):
    out = server.task_item(uid, "2", delete=True, expect="Write the lexer", state="done")
    assert out["ok"] is False and "alone" in out["errors"][0]
    assert _texts(uid) == ITEMS


def test_the_only_item_is_not_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        one = tasks.create_task(c, title="Tag the release", goal="The release is tagged.",
                                items=["Tag the release"], domain="acme/parser")
    out = server.task_item(one, "1", delete=True, expect="Tag the release")
    assert out["ok"] is False and "at least one item" in out["errors"][0]


def test_a_brief_depending_on_a_moved_item_follows_it(uid):
    with connection.connect() as c:
        nid = tasks.add_note(c, uid, title="Release", items=[item_at(c, uid, 4)],
                             body=brief("publish it", depends_on="i3 (the CLI)"))
    server.task_item(uid, "1", delete=True, expect="Draft the grammar")
    with connection.connect() as c:
        assert tasks.brief_fields(tasks.note(c, uid, nid)["body"])["depends_on"] == "i2 (the CLI)"


def test_a_rename_from_a_stale_read_is_refused(uid):
    out = server.task_item(uid, "2", text="Write the scanner", expect="Draft the grammar")
    assert out["ok"] is False and "no longer" in out["errors"][0]
    assert _texts(uid) == ITEMS


def test_task_item_says_when_to_delete_and_when_to_drop():
    [t] = [t for t in asyncio.run(server.mcp.list_tools()) if t.name == "task_item"]
    props = t.input_schema["properties"]
    assert "dropped" in props["delete"]["description"] and "renumbered" in props["delete"]["description"]
    assert "current text" in props["expect"]["description"]
