"""The MCP tools that create and work a task.

Every tool is exercised against a store under tmp_path (MEMAI_HOME), never the
real ~/.memai. A refusal comes back as {"ok": False, "errors": [...]} and leaves
the store as it was.
"""

from __future__ import annotations

import pytest

from memai import db, server


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _task(**over) -> dict:
    args = {"title": "Ship the parser", "goal": "Parse every config file",
            "items": "read the spec\nwrite the lexer", "domain": "acme/parser"}
    return server.task(**{**args, **over})


def test_task_creates_and_reports_item_keys(store):
    result = _task()
    assert result["uid"]
    assert result["items"] == ["i1", "i2"]
    assert server.get_memory(result["uid"])["type"] == "task"


def test_task_never_adds_the_type_name_to_tags(store):
    uid = _task(tags="parser, lexer")["uid"]
    with db.connect() as conn:
        assert "task" not in db.get_memory(conn, uid)["tags"].split(",")


def test_task_refuses_bad_input_as_an_error_result(store):
    too_many = _task(items="\n".join(f"step {n}" for n in range(51)))
    assert too_many["ok"] is False and "uid" not in too_many
    leaked = _task(items="read the spec</parameter>\nwrite the lexer")
    assert leaked["ok"] is False and "uid" not in leaked
    assert _task(goal="  ")["ok"] is False


def test_a_refused_task_writes_nothing(store):
    _task(items="read the spec</parameter>")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_task_item_needs_something_to_do(store):
    uid = _task()["uid"]
    result = server.task_item(uid, "i1")
    assert result["ok"] is False
    message = " ".join(result["errors"])
    assert "state" in message and "comment" in message and "related" in message


def test_task_item_applies_link_comment_and_state_together(store):
    uid = _task()["uid"]
    note_uid = server.note("Lexer design", content="a fact about the lexer")["uid"]
    result = server.task_item(uid, "1", state="done", comment="lexer merged",
                              related=note_uid)
    assert result["item"] == "i1"
    assert result["state"] == "done"
    assert result["progress"] == {"done": 1, "dropped": 0, "total": 2}
    assert result["task_state"] == "open" and result["archived"] is False
    task = server.get_memory(uid)["task"]
    assert [c["item"] for c in task["comments"]] == ["i1"]
    assert task["comments"][0]["body"] == "lexer merged"
    assert task["comments"][0]["author"] == "agent"
    assert [link["uid"] for link in task["items"][0]["links"]] == [note_uid]


def test_task_item_with_a_comment_alone_reports_the_current_state(store):
    uid = _task()["uid"]
    server.task_item(uid, "i2", state="doing")
    result = server.task_item(uid, "i2", comment="waiting on review")
    assert result["state"] == "doing"
    assert result["progress"] == {"done": 0, "dropped": 0, "total": 2}


def test_task_item_with_an_unknown_related_uid_changes_nothing(store):
    uid = _task()["uid"]
    note_uid = server.note("Lexer design", content="a fact about the lexer")["uid"]
    with db.connect() as conn:
        before = _snapshot(conn, uid)
    result = server.task_item(uid, "i1", state="done", comment="lexer merged",
                              related=f"{note_uid},no-such-uid")
    assert result["ok"] is False and "no-such-uid" in " ".join(result["errors"])
    with db.connect() as conn:
        assert _snapshot(conn, uid) == before
    task = server.get_memory(uid)["task"]
    assert task["items"][0]["state"] == "todo" and task["comments"] == []


def test_task_item_with_a_bad_state_rolls_back_the_link_and_comment(store):
    uid = _task()["uid"]
    note_uid = server.note("Lexer design", content="a fact about the lexer")["uid"]
    with db.connect() as conn:
        before = _snapshot(conn, uid)
    result = server.task_item(uid, "i1", state="finished", comment="x", related=note_uid)
    assert result["ok"] is False
    with db.connect() as conn:
        assert _snapshot(conn, uid) == before


def test_task_item_on_an_unknown_item_is_an_error_result(store):
    uid = _task()["uid"]
    result = server.task_item(uid, "i9", state="done")
    assert result["ok"] is False and "i9" in " ".join(result["errors"])


def test_closing_the_last_item_reports_archived(store):
    uid = _task()["uid"]
    server.task_item(uid, "i1", state="done")
    result = server.task_item(uid, "i2", state="dropped")
    assert result["archived"] is True
    assert result["task_state"] == "completed"


def test_task_add_and_task_comment(store):
    uid = _task()["uid"]
    added = server.task_add(uid, "ship the docs")
    assert added["items"] == ["i3"]
    assert added["progress"] == {"done": 0, "dropped": 0, "total": 3}
    assert added["task_state"] == "open" and added["archived"] is False
    comment = server.task_comment(uid, "blocked on review")
    assert comment["uid"] == uid and isinstance(comment["comment_id"], int)
    on_item = server.task_comment(uid, "see the draft", item="i3")
    assert on_item["comment_id"] != comment["comment_id"]


def test_task_add_and_task_comment_refuse_as_error_results(store):
    uid = _task()["uid"]
    assert server.task_add(uid, "  \n ")["ok"] is False
    assert server.task_add("no-such-uid", "step")["ok"] is False
    assert server.task_comment(uid, "  ")["ok"] is False
    assert server.task_comment(uid, "hello", item="i9")["ok"] is False


def test_forget_on_an_open_task_cancels_it(store):
    uid = _task()["uid"]
    assert server.forget(uid)["ok"] is True
    assert server.get_memory(uid)["task"]["state"] == "cancelled"


def test_get_memory_carries_the_task_block(store):
    uid = _task()["uid"]
    task = server.get_memory(uid)["task"]
    assert {"goal", "state", "items", "comments"} <= set(task)
    assert task["goal"] == "Parse every config file"


def test_get_memory_of_a_note_has_no_task_block(store):
    uid = server.note("Lexer design", content="a fact about the lexer")["uid"]
    assert "task" not in server.get_memory(uid)


def _snapshot(conn, uid: str) -> dict:
    """Everything a task_item write could touch, as plain values."""
    def rows(sql):
        return [tuple(r) for r in conn.execute(sql, (uid,))]
    return {
        "items": rows("SELECT item_key, state, updated_at FROM task_items WHERE memory_uid = ? ORDER BY seq"),
        "comments": rows("SELECT id, item_key, body FROM task_comments WHERE memory_uid = ?"),
        "links": rows("SELECT item_key, target_uid FROM task_item_links WHERE memory_uid = ?"),
        "edits": rows("SELECT id, note, new_content FROM edits WHERE memory_uid = ?"),
        "memory": tuple(db.get_memory(conn, uid)),
    }


LEAK = "see the call </parameter> that ended early"


def _comments(uid: str) -> list:
    with db.connect() as conn:
        return conn.execute("SELECT body FROM task_comments WHERE memory_uid = ?",
                            (uid,)).fetchall()


def test_the_comment_tools_refuse_a_tool_calls_closing_tag(store):
    uid = _task()["uid"]
    for result in (server.task_comment(uid, LEAK),
                   server.task_comment(uid, LEAK, item="i1"),
                   server.task_item(uid, "i1", comment=LEAK),
                   server.task_item(uid, "i1", state="done", comment=LEAK)):
        assert result["ok"] is False
        assert "tool call" in result["errors"][0]
    assert _comments(uid) == []
    with db.connect() as conn:
        assert [i["state"] for i in server.tasks.get_task(conn, uid)["items"]] == ["todo", "todo"]
