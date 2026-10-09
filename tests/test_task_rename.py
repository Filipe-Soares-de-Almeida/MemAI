"""Renaming a task item in place: same id, state, brief, comments and links."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from conftest import brief, item_at, item_of
from memai import server, tasks
from memai.admin.app import app as admin_app
from memai.store import connection, memories

ITEMS = ["Draft the grammar", "Write the lexer", "Wire the CLI"]


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


def _task(conn) -> str:
    return tasks.create_task(conn, title="Ship the config parser", goal="A parser for the config.",
                             items=ITEMS, domain="acme/parser")


def _text(conn, uid, n):
    return tasks.get_task(conn, uid)["items"][n - 1]["text"]


def test_a_renamed_item_keeps_everything_attached_to_it(conn):
    uid = _task(conn)
    fact = memories.insert_memory(conn, type="note", content="lexers are greedy", domain="acme/parser")
    tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "doing")
    tasks.link_item(conn, uid, item_at(conn, uid, 2), [fact])
    tasks.add_comment(conn, uid, "halfway", item=item_at(conn, uid, 2))
    nid = tasks.add_note(conn, uid, title="Lexer", body=brief("tokenize"), items=[item_at(conn, uid, 2)])
    second = item_at(conn, uid, 2)
    tasks.rename_item(conn, uid, second, "Write the tokenizer")
    item = tasks.get_task(conn, uid)["items"][1]
    assert (item["id"], item["text"], item["state"]) == (second, "Write the tokenizer", "doing")
    assert [link["uid"] for link in item["links"]] == [fact]
    assert tasks.note(conn, uid, nid)["items"] == [second]
    assert [c["body"] for c in tasks.get_task(conn, uid)["comments"]] == ["halfway"]


def test_the_previous_text_stays_in_the_edit_history(conn):
    uid = _task(conn)
    tasks.rename_item(conn, uid, item_at(conn, uid, 2), "Write the tokenizer")
    last = memories.get_edit_history(conn, uid)[-1]
    assert "Write the lexer" in last["prev_content"] and "Write the tokenizer" in last["new_content"]


@pytest.mark.parametrize("text, match", [
    ("x" * (tasks.ITEM_MAX + 1), "short label"),
    ("Write the lexer after i1", "cites i1"),
    ("   ", "empty"),
])
def test_a_new_text_is_held_to_the_item_rules(conn, text, match):
    uid = _task(conn)
    with pytest.raises(ValueError, match=match):
        tasks.rename_item(conn, uid, item_at(conn, uid, 2), text)
    assert _text(conn, uid, 2) == "Write the lexer"


def test_the_task_item_tool_renames_with_a_state_change(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        uid = _task(c)
    out = server.task_item(uid, item_of(uid, 2), state="doing", text="Write the tokenizer")
    assert out["state"] == "doing"
    with connection.connect() as c:
        assert _text(c, uid, 2) == "Write the tokenizer"


def test_a_refused_rename_leaves_the_rest_of_the_call_unwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        uid = _task(c)
    out = server.task_item(uid, item_of(uid, 2), state="done", text="Write it after i1")
    assert out["ok"] is False and "cites i1" in out["errors"][0]
    with connection.connect() as c:
        item = tasks.get_task(c, uid)["items"][1]
        assert (item["text"], item["state"]) == ("Write the lexer", "todo")


def test_the_task_item_schema_describes_the_new_text():
    [t] = [t for t in asyncio.run(server.mcp.list_tools()) if t.name == "task_item"]
    assert "keeps" in t.input_schema["properties"]["text"]["description"]


def test_the_dashboard_renames_an_item_by_its_id(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin_app) as client:
        with connection.connect() as c:
            uid = _task(c)
        ok = client.post(f"/api/tasks/{uid}/item/text",
                         json={"item": item_of(uid, 2), "text": "Write the tokenizer"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["task"]["items"][1]["text"] == "Write the tokenizer"
