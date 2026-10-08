"""Task notes: owned by a task, invisible to every memory read."""

from __future__ import annotations

import pytest

from conftest import brief
from memai import portable, tasks
from memai.store import connection, memories, search, sections


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        yield c


@pytest.fixture
def uid(conn):
    return tasks.create_task(conn, title="Ship the parser", goal="Parse every config file",
                             items=["read the spec", "write the lexer", "write the parser"],
                             domain="acme/parser")


def test_a_note_attaches_to_several_items_or_to_the_task(conn, uid):
    shared = tasks.add_note(conn, uid, title="Lexer rules", body="Tokens are ASCII.",
                            items=["i2", "3"])
    top = tasks.add_note(conn, uid, title="How to pick an item", body="Claim it first.", items=[])
    assert [n["id"] for n in tasks.notes(conn, uid, item="i2")] == [shared]
    assert [n["id"] for n in tasks.notes(conn, uid, item="i3")] == [shared]
    assert [n["id"] for n in tasks.notes(conn, uid)] == [top]
    assert tasks.notes(conn, uid, item="i2")[0]["items"] == ["i2", "i3"]


def test_note_returns_one_note_with_its_items_in_item_order(conn, uid):
    nid = tasks.add_note(conn, uid, title="T", body="b", items=["i3", "i1"])
    assert tasks.note(conn, uid, nid)["items"] == ["i1", "i3"]


def test_notes_never_reach_the_memories_table_or_search(conn, uid):
    before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    tasks.add_note(conn, uid, title="Zebracorn lexer", body="zebracorn tokens", items=["i1"])
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == before
    assert search.search_ranked(conn, "zebracorn", limit=5) == []


def test_edit_overwrites_without_history(conn, uid):
    nid = tasks.add_note(conn, uid, title="Old", body="old body", items=["i1"])
    tasks.edit_note(conn, uid, nid, body="new body", items=["i2"])
    note = tasks.notes(conn, uid, item="i2")[0]
    assert (note["title"], note["body"]) == ("Old", "new body")
    assert tasks.notes(conn, uid, item="i1") == []
    assert memories.get_edit_history(conn, uid) == []


def test_edit_with_empty_items_moves_the_note_to_task_level(conn, uid):
    nid = tasks.add_note(conn, uid, title="T", body="b", items=["i1"])
    tasks.edit_note(conn, uid, nid, items=[])
    assert [n["id"] for n in tasks.notes(conn, uid)] == [nid]


def test_limits_and_unknown_items_are_refused_before_writing(conn, uid):
    with pytest.raises(ValueError):
        tasks.add_note(conn, uid, title="T", body="x" * (tasks.NOTE_MAX + 1), items=[])
    with pytest.raises(ValueError):
        tasks.add_note(conn, uid, title="x" * (sections.TITLE_MAX + 1), body="b", items=[])
    with pytest.raises(ValueError):
        tasks.add_note(conn, uid, title="T", body="b", items=["i9"])
    with pytest.raises(ValueError):
        tasks.add_note(conn, uid, title=" ", body="b", items=[])
    assert conn.execute("SELECT COUNT(*) FROM task_notes").fetchone()[0] == 0


def test_a_note_from_another_task_is_refused(conn, uid):
    other = tasks.create_task(conn, title="Other", goal="g", items=["a"])
    nid = tasks.add_note(conn, other, title="T", body="b", items=[])
    with pytest.raises(ValueError):
        tasks.edit_note(conn, uid, nid, body="hijack")
    with pytest.raises(ValueError):
        tasks.delete_note(conn, uid, nid)


def test_delete_note_removes_it(conn, uid):
    nid = tasks.add_note(conn, uid, title="T", body="b", items=["i1"])
    tasks.delete_note(conn, uid, nid)
    assert tasks.notes(conn, uid, item="i1") == []
    assert conn.execute("SELECT COUNT(*) FROM task_note_items").fetchone()[0] == 0


def test_deleting_an_item_keeps_the_note_on_its_other_items(conn, uid):
    nid = tasks.add_note(conn, uid, title="T", body=brief("b"), items=["i1", "i2"])
    tasks.delete_item(conn, uid, "i1")
    assert tasks.notes(conn, uid, item="i1")[0]["items"] == ["i1"]
    tasks.delete_item(conn, uid, "i1")
    assert [n["id"] for n in tasks.notes(conn, uid)] == [nid]


def test_purging_the_task_removes_its_notes(conn, uid):
    tasks.add_note(conn, uid, title="T", body="b", items=["i1"])
    memories.purge_memory(conn, uid)
    assert conn.execute("SELECT COUNT(*) FROM task_notes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM task_note_items").fetchone()[0] == 0


def test_item_changes_record_no_edit_but_a_delete_and_a_goal_edit_do(conn, uid):
    tasks.set_item_state(conn, uid, "i1", "done")
    tasks.add_items(conn, uid, ["write the docs"])
    assert memories.get_edit_history(conn, uid) == []
    tasks.delete_item(conn, uid, "i4")
    assert len(memories.get_edit_history(conn, uid)) == 1
    assert "[x] i1" in memories.get_memory(conn, uid)["content"]
    tasks.set_goal(conn, uid, "Parse every config file fast")
    assert len(memories.get_edit_history(conn, uid)) == 2


def test_export_and_import_carry_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path / "a"))
    with connection.connect() as conn:
        uid = tasks.create_task(conn, title="Ship the parser", goal="g",
                                items=["read the spec", "write the lexer"], domain="acme")
        tasks.add_note(conn, uid, title="Lexer rules", body="Tokens are ASCII.", items=["i2"])
        tasks.add_note(conn, uid, title="Pick-up rules", body="Claim first.", items=[])
        records = list(portable.export_records(conn, include_archived=True))
    task_record = next(r for r in records if r["record"] == "task")
    assert [n["title"] for n in task_record["notes"]] == ["Lexer rules", "Pick-up rules"]
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path / "b"))
    with connection.connect() as conn:
        portable.import_records(conn, records)
        assert tasks.notes(conn, uid, item="i2")[0]["body"] == "Tokens are ASCII."
        assert [n["title"] for n in tasks.notes(conn, uid)] == ["Pick-up rules"]
