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
    shared = tasks.add_note(conn, uid, title="Lexer rules", body=brief("Tokens are ASCII."),
                            items=["i2", "3"])
    top = tasks.add_note(conn, uid, title="How to pick an item", body="Claim it first.", items=[])
    assert [n["id"] for n in tasks.notes(conn, uid, item="i2")] == [shared]
    assert [n["id"] for n in tasks.notes(conn, uid, item="i3")] == [shared]
    assert [n["id"] for n in tasks.notes(conn, uid)] == [top]
    assert tasks.notes(conn, uid, item="i2")[0]["items"] == ["i2", "i3"]


def test_note_returns_one_note_with_its_items_in_item_order(conn, uid):
    nid = tasks.add_note(conn, uid, title="T", body=brief("b"), items=["i3", "i1"])
    assert tasks.note(conn, uid, nid)["items"] == ["i1", "i3"]


def test_notes_never_reach_the_memories_table_or_search(conn, uid):
    before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    tasks.add_note(conn, uid, title="Zebracorn lexer", body=brief("zebracorn tokens"), items=["i1"])
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == before
    assert search.search_ranked(conn, "zebracorn", limit=5) == []


def test_edit_overwrites_without_history(conn, uid):
    nid = tasks.add_note(conn, uid, title="Old", body=brief("old body"), items=["i1"])
    tasks.edit_note(conn, uid, nid, body=brief("new body"), items=["i2"])
    note = tasks.notes(conn, uid, item="i2")[0]
    assert (note["title"], note["body"]) == ("Old", brief("new body"))
    assert tasks.notes(conn, uid, item="i1") == []
    assert memories.get_edit_history(conn, uid) == []


def test_edit_with_empty_items_moves_the_note_to_task_level(conn, uid):
    nid = tasks.add_note(conn, uid, title="T", body=brief("b"), items=["i1"])
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
    nid = tasks.add_note(conn, uid, title="T", body=brief("b"), items=["i1"])
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
    tasks.add_note(conn, uid, title="T", body=brief("b"), items=["i1"])
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
        tasks.add_note(conn, uid, title="Lexer rules", body=brief("Tokens are ASCII."), items=["i2"])
        tasks.add_note(conn, uid, title="Pick-up rules", body="Claim first.", items=[])
        records = list(portable.export_records(conn, include_archived=True))
    task_record = next(r for r in records if r["record"] == "task")
    assert [n["title"] for n in task_record["notes"]] == ["Lexer rules", "Pick-up rules"]
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path / "b"))
    with connection.connect() as conn:
        portable.import_records(conn, records)
        assert tasks.notes(conn, uid, item="i2")[0]["body"] == brief("Tokens are ASCII.")
        assert [n["title"] for n in tasks.notes(conn, uid)] == ["Pick-up rules"]


# ------------------------------------------------------------- the brief rule

def test_a_free_body_on_items_is_refused_and_names_the_template(conn, uid):
    with pytest.raises(ValueError, match="a note on items is a brief: GOAL, CONTEXT, STEPS, "
                                         "PITFALLS, DONE WHEN, DEPENDS ON, then optionally EXTRA INFO"):
        tasks.add_note(conn, uid, title="Lexer", body="Tokens are ASCII.", items=["i2"])
    assert conn.execute("SELECT COUNT(*) FROM task_notes").fetchone()[0] == 0


def test_a_note_carrying_a_task_note_parameter_closing_tag_is_refused(conn, uid):
    with pytest.raises(ValueError, match="tool call's own source"):
        tasks.add_note(conn, uid, title="Pick-up", body="Claim it </extra_info> first.", items=[])
    nid = tasks.add_note(conn, uid, title="Pick-up", body="Claim it first.", items=[])
    with pytest.raises(ValueError, match="tool call's own source"):
        tasks.edit_note(conn, uid, nid, body="Claim it </body> first.")
    assert tasks.note(conn, uid, nid)["body"] == "Claim it first."


def test_a_free_body_on_the_whole_task_stays_free(conn, uid):
    nid = tasks.add_note(conn, uid, title="Pick-up", body="Claim it first.", items=[])
    assert tasks.note(conn, uid, nid)["body"] == "Claim it first."


def test_partial_fields_on_the_whole_task_say_a_brief_needs_every_field(conn, uid):
    with pytest.raises(ValueError, match="brief fields must form a whole brief: GOAL, CONTEXT, STEPS, "
                                         "PITFALLS, DONE WHEN, DEPENDS ON, then optionally EXTRA INFO") as raised:
        tasks.add_note(conn, uid, title="Pick-up", items=[], brief={"goal": "claim it first"})
    assert "a note on items" not in str(raised.value)
    nid = tasks.add_note(conn, uid, title="Pick-up", body="Claim it first.", items=[])
    with pytest.raises(ValueError, match="brief fields must form a whole brief"):
        tasks.edit_note(conn, uid, nid, brief={"goal": "claim it first"})
    assert tasks.note(conn, uid, nid)["body"] == "Claim it first."


def test_partial_fields_on_items_say_a_note_on_items_is_a_brief(conn, uid):
    with pytest.raises(ValueError, match="a note on items is a brief"):
        tasks.add_note(conn, uid, title="Lexer", items=["i2"], brief={"goal": "tokenize"})


def test_fields_build_the_body(conn, uid):
    nid = tasks.add_note(conn, uid, title="Lexer", items=["i2"], brief={
        "goal": "tokenize the config", "context": "ASCII input", "steps": "write the scanner",
        "pitfalls": "tabs", "done_when": "every fixture lexes", "depends_on": "i1",
        "extra_info": "  "})
    assert tasks.note(conn, uid, nid)["body"] == (
        "GOAL: tokenize the config\nCONTEXT: ASCII input\nSTEPS: write the scanner\n"
        "PITFALLS: tabs\nDONE WHEN: every fixture lexes\nDEPENDS ON: i1")


def test_missing_fields_are_refused(conn, uid):
    with pytest.raises(ValueError, match="nothing under CONTEXT"):
        tasks.add_note(conn, uid, title="Lexer", items=["i2"], brief={"goal": "tokenize"})


def test_body_and_fields_together_are_refused(conn, uid):
    with pytest.raises(ValueError, match="not both"):
        tasks.add_note(conn, uid, title="Lexer", body=brief(), items=["i2"], brief={"goal": "g"})


def test_an_unknown_field_is_refused(conn, uid):
    with pytest.raises(ValueError, match="scope is not a brief field"):
        tasks.add_note(conn, uid, title="Lexer", items=["i2"], brief={"scope": "x"})


def test_an_edit_of_one_field_keeps_the_others(conn, uid):
    nid = tasks.add_note(conn, uid, title="Lexer", body=brief("tokenize"), items=["i2"])
    tasks.edit_note(conn, uid, nid, brief={"pitfalls": "tabs and CRLF"})
    fields = tasks.brief_fields(tasks.note(conn, uid, nid)["body"])
    assert fields["goal"] == "tokenize" and fields["pitfalls"] == "tabs and CRLF"


def _stored_free_item_note(conn, uid):
    cur = conn.execute("INSERT INTO task_notes (memory_uid, title, body, session, created_at, updated_at) "
                       "VALUES (?, 'Old brief', 'Free text from before the template.', '', '', '')", (uid,))
    conn.execute("INSERT INTO task_note_items (note_id, item_key) VALUES (?, 'i2')", (cur.lastrowid,))
    return cur.lastrowid


def test_a_title_only_edit_of_a_stored_free_item_note_passes(conn, uid):
    nid = _stored_free_item_note(conn, uid)
    tasks.edit_note(conn, uid, nid, title="Renamed")
    tasks.edit_note(conn, uid, nid, items=["i3"])
    note = tasks.note(conn, uid, nid)
    assert (note["title"], note["body"], note["items"]) == (
        "Renamed", "Free text from before the template.", ["i3"])


def test_a_new_body_for_a_stored_free_item_note_must_be_a_brief(conn, uid):
    nid = _stored_free_item_note(conn, uid)
    with pytest.raises(ValueError, match="a note on items is a brief"):
        tasks.edit_note(conn, uid, nid, body="Still free text.")
    with pytest.raises(ValueError, match="nothing under CONTEXT"):
        tasks.edit_note(conn, uid, nid, brief={"goal": "only the goal"})
    tasks.edit_note(conn, uid, nid, body=brief("now a brief"))


def test_moving_a_free_task_note_onto_items_is_refused(conn, uid):
    nid = tasks.add_note(conn, uid, title="Pick-up", body="Claim it first.", items=[])
    with pytest.raises(ValueError, match="a note on items is a brief"):
        tasks.edit_note(conn, uid, nid, items=["i1"])
    assert tasks.note(conn, uid, nid)["items"] == []


def test_brief_fields_is_none_for_free_text(conn, uid):
    assert tasks.brief_fields("Claim it first.") is None
    assert tasks.brief_fields(brief("g"))["goal"] == "g"


def _depends_note(conn, uid, depends, items=("i2",), **kw):
    return tasks.add_note(conn, uid, title="Lexer", items=list(items),
                          body=brief("tokenize", depends_on=depends), **kw)


def test_a_dependency_on_an_item_the_task_lacks_is_refused_naming_the_format(conn, uid):
    with pytest.raises(ValueError, match=r"DEPENDS ON is none, or item keys with an optional "
                                         r"reason in parentheses: i3 \(why\), i9 — i9 is not an item"):
        _depends_note(conn, uid, "i9")
    assert tasks.notes(conn, uid, "i2") == []


def test_a_note_may_not_depend_on_its_own_item(conn, uid):
    with pytest.raises(ValueError, match="i2 is an item this note applies to"):
        _depends_note(conn, uid, "i1, i2")
    with pytest.raises(ValueError, match="i3 is an item this note applies to"):
        _depends_note(conn, uid, "i3", items=("i2", "i3"))


def test_free_text_in_depends_on_is_refused_on_items(conn, uid):
    with pytest.raises(ValueError, match="DEPENDS ON is none"):
        _depends_note(conn, uid, "Item 1")
    with pytest.raises(ValueError, match="DEPENDS ON is none"):
        _depends_note(conn, uid, "nothing to add")


def test_item_references_with_reasons_and_none_are_accepted(conn, uid):
    _depends_note(conn, uid, "i1 (the spec decides the tokens), i3")
    _depends_note(conn, uid, "none")
    _depends_note(conn, uid, 'deleted "Draft the grammar" (it was dropped)')


def test_fields_over_a_stored_brief_recheck_depends_on(conn, uid):
    nid = _depends_note(conn, uid, "i1")
    tasks.edit_note(conn, uid, nid, brief={"pitfalls": "tabs"})
    with pytest.raises(ValueError, match="DEPENDS ON is none"):
        tasks.edit_note(conn, uid, nid, brief={"depends_on": "i9"})
    with pytest.raises(ValueError, match="i2 is an item this note applies to"):
        tasks.edit_note(conn, uid, nid, brief={"depends_on": "i2"})
    assert tasks.brief_fields(tasks.note(conn, uid, nid)["body"])["depends_on"] == "i1"


def test_fields_over_a_stored_brief_with_free_depends_on_must_restate_it(conn, uid):
    nid = tasks.add_note(conn, uid, title="Lexer", items=["i2"], body=brief("tokenize"))
    conn.execute("UPDATE task_notes SET body = REPLACE(body, 'DEPENDS ON: none', 'DEPENDS ON: Item 1') "
                 "WHERE id = ?", (nid,))
    assert tasks.brief_fields(tasks.note(conn, uid, nid)["body"])["depends_on"] == "Item 1"
    tasks.edit_note(conn, uid, nid, title="Renamed")
    with pytest.raises(ValueError, match="DEPENDS ON is none"):
        tasks.edit_note(conn, uid, nid, brief={"pitfalls": "tabs"})
    tasks.edit_note(conn, uid, nid, brief={"pitfalls": "tabs", "depends_on": "none"})


def test_moving_a_brief_onto_its_dependency_is_refused(conn, uid):
    nid = _depends_note(conn, uid, "i1")
    with pytest.raises(ValueError, match="i1 is an item this note applies to"):
        tasks.edit_note(conn, uid, nid, body=brief("tokenize", depends_on="i1"), items=["i1"])


def test_a_task_level_brief_built_from_fields_is_held_to_known_items(conn, uid):
    fields = {"goal": "g", "context": "c", "steps": "s", "pitfalls": "p", "done_when": "d"}
    with pytest.raises(ValueError, match="i9 is not an item"):
        tasks.add_note(conn, uid, title="Rule", items=[], brief={**fields, "depends_on": "i9"})
    tasks.add_note(conn, uid, title="Rule", items=[], brief={**fields, "depends_on": "i1, i3"})


def test_a_free_task_note_is_not_read_for_depends(conn, uid):
    tasks.add_note(conn, uid, title="Pick-up", body="DEPENDS ON: Item 9", items=[])


def test_a_scope_edit_may_not_put_a_brief_on_its_own_dependency(conn, uid):
    nid = _depends_note(conn, uid, "i1 (the spec), i3")
    with pytest.raises(ValueError, match="i1 is an item this note applies to"):
        tasks.edit_note(conn, uid, nid, items=["i1"])
    with pytest.raises(ValueError, match="i3 is an item this note applies to"):
        tasks.edit_note(conn, uid, nid, items=["i2", "i3"])
    tasks.edit_note(conn, uid, nid, items=[])
    assert tasks.note(conn, uid, nid)["items"] == []
    tasks.edit_note(conn, uid, nid, items=["i2"])
    assert tasks.note(conn, uid, nid)["items"] == ["i2"]


def test_a_scope_edit_leaves_a_pre_grammar_brief_lenient(conn, uid):
    nid = _depends_note(conn, uid, "none")
    conn.execute("UPDATE task_notes SET body = REPLACE(body, 'DEPENDS ON: none', 'DEPENDS ON: Item 9') "
                 "WHERE id = ?", (nid,))
    tasks.edit_note(conn, uid, nid, items=["i3"])
    assert tasks.note(conn, uid, nid)["items"] == ["i3"]
