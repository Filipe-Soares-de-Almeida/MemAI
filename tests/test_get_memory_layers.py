"""get_memory returns a head; every growable part of a record comes back through its own offset."""

from __future__ import annotations

import pytest

from memai import budget, guard, server
from memai.store import connection, memories


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


def _task(**over) -> str:
    args = {"title": "Ship the parser", "goal": "Parse every config file",
            "items": "read the spec\nwrite the lexer", "domain": "acme/parser"}
    return server.task(**{**args, **over})["uid"]


def _note(content: str = "v0") -> str:
    with connection.connect() as conn:
        return memories.insert_memory(conn, type="note", content=content, title="Parser fact",
                                domain="acme/parser")


def test_get_memory_on_a_task_is_a_head_without_items(store):
    uid = _task()
    server.task_item(uid, "i1", state="done")
    record = server.get_memory(uid)
    assert "content" not in record
    task = record["task"]
    assert task["goal"] == "Parse every config file"
    assert task["counts"] == {"items": 2, "notes": 0, "comments": 0}
    assert task["next"]["items"] == f"task_read(uid='{uid}', part='items')"
    assert "items" not in task and "comments" not in task
    assert record["edit_count"] == 0 and "edit_history" not in record


def test_edit_history_is_a_count_and_a_separate_page(store):
    uid = _note()
    with connection.connect() as conn:
        for n in range(1, 4):
            memories.update_memory_content(conn, uid, f"v{n}", note=f"edit {n}")
    record = server.get_memory(uid)
    assert record["edit_count"] == 3 and "edit_history" not in record
    assert record["next"]["edits"] == f"get_memory(uid='{uid}', edits_offset=0)"
    page = server.get_memory(uid, edits_offset=0)
    assert [e["note"] for e in page["records"]] == ["edit 1", "edit 2", "edit 3"]
    assert page["total"] == 3 and "next_offset" not in page


def test_a_long_edit_body_is_cut_and_says_so(store):
    uid = _note()
    with connection.connect() as conn:
        memories.update_memory_content(conn, uid, "y" * 50_000, note="big")
    edit = server.get_memory(uid, edits_offset=0)["records"][0]
    assert edit["new_chars"] == 50_000 and len(edit["new_content"]) < 50_000
    assert edit["truncated"] is True


def test_a_long_body_comes_back_in_chunks(store):
    body = "".join(f"line {n} \"quoted\"\n" for n in range(6000))
    uid = _note(body)
    record = server.get_memory(uid)
    assert budget.result_chars(record) <= budget.MCP_RESULT_MAX_CHARS
    assert record["content_chars"] == len(body)
    offset, parts = record["next"]["content_offset"], [record["content"]]
    while offset is not None:
        page = server.get_memory(uid, content_offset=offset)
        assert budget.result_chars(page) <= budget.MCP_RESULT_MAX_CHARS
        parts.append(page["text"])
        offset = page.get("next_offset")
    assert "".join(parts) == body


def test_a_short_body_has_no_content_offset(store):
    record = server.get_memory(_note("short"))
    assert record["content"] == "short" and "next" not in record


def test_bad_offsets_are_error_results(store):
    uid = _note()
    assert server.get_memory(uid, edits_offset=-2)["ok"] is False
    assert server.get_memory(uid, content_offset=-2)["ok"] is False


BRIEF_ARGS = dict(goal="lex the config", context="ASCII input", steps="write the scanner",
                  pitfalls="tabs", done_when="every fixture lexes", depends_on="none")


def test_task_read_and_task_note_round_trip(store):
    uid = _task()
    made = server.task_note(uid, title="Lexer rules", items="i2", **BRIEF_ARGS)
    assert made["note_id"] and made["items"] == ["i2"]
    body = server.task_read(uid, "notes", item="i2")["records"][0]["body"]
    assert body.startswith("GOAL: lex the config\nCONTEXT: ASCII input")
    assert server.task_note(uid, note_id=made["note_id"], pitfalls="tabs and CRLF").get("ok") is not False
    assert "PITFALLS: tabs and CRLF" in server.task_read(uid, "notes", item="i2")["records"][0]["body"]
    assert server.task_note(uid, note_id=made["note_id"], items="-")["items"] == []
    assert server.task_read(uid, "notes")["total"] == 1
    assert server.task_note(uid, note_id=made["note_id"], delete=True)["deleted"] is True
    assert server.task_read(uid, "notes")["total"] == 0


@pytest.mark.parametrize("kwargs", [{"part": "links"}, {"part": "items", "item": "i1"},
                                    {"part": "bogus"}, {"part": "notes", "offset": -1}])
def test_task_read_refusals_are_error_results(store, kwargs):
    assert server.task_read(_task(), **kwargs)["ok"] is False


def test_task_note_refusals_are_error_results(store):
    uid = _task()
    assert server.task_note(uid, title="T", body="")["ok"] is False
    assert server.task_note(uid, title="T", body="b", items="i9")["ok"] is False
    assert server.task_note(uid, note_id=999, delete=True)["ok"] is False
    assert server.task_note(_note(), title="T", body="b")["ok"] is False
    assert server.task_note(uid, title="T", body="free text", items="i1")["ok"] is False
    assert server.task_note(uid, title="T", body="b", goal="g")["ok"] is False
    assert server.task_note(uid, title="T", items="i1", goal="only a goal")["ok"] is False


def test_new_task_tools_are_guarded():
    assert guard.GUARDED["task_note"] == ("uid",)
    assert guard.GUARDED["task_read"] == ("uid", "part")
