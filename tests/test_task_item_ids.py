"""Items are addressed by id: positions are computed on read, and a delete renumbers nothing."""

import pytest

from memai import sections, tasks
from memai.store import connection, memories


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


def _brief(depends: str) -> dict:
    return {"goal": "g", "context": "c", "steps": "s", "pitfalls": "p", "done_when": "d",
            "depends_on": depends}


def _four(conn):
    """A task whose ids differ from its positions: a filler task takes the first ids."""
    tasks.create_task(conn, title="Filler", goal="Take the first ids", items=[f"x{n}" for n in range(10)])
    uid = tasks.create_task(conn, title="Ship the lantern", goal="Light the bench",
                            items=["solder the header", "flash the board", "test the beam", "pack it"])
    return uid, tasks.item_ids(conn, uid)


def test_items_carry_their_id_and_their_position(conn):
    uid, ids = _four(conn)
    items = tasks.get_task(conn, uid)["items"]
    assert [i["id"] for i in items] == ids
    assert [i["n"] for i in items] == [1, 2, 3, 4]
    assert ids == sorted(ids)


def test_the_content_names_each_item_by_id(conn):
    uid, ids = _four(conn)
    lines = memories.get_memory(conn, uid)["content"].splitlines()
    assert lines[1] == f"[ ] {ids[0]} solder the header"


def test_deleting_an_item_moves_no_other_item(conn):
    uid, ids = _four(conn)
    tasks.add_comment(conn, uid, "flux first", item=ids[2])
    tasks.delete_item(conn, uid, ids[0])
    task = tasks.get_task(conn, uid)
    assert [i["id"] for i in task["items"]] == ids[1:]
    assert [i["n"] for i in task["items"]] == [1, 2, 3]
    assert [c["item"] for c in task["comments"]] == [ids[2]]


def test_a_delete_keeps_the_deleted_text_in_the_depends_rows_that_named_it(conn):
    uid, ids = _four(conn)
    nid = tasks.add_note(conn, uid, title="Beam", items=[ids[2]],
                         brief=_brief(f"{ids[0]} (needs power), {ids[1]}"))
    tasks.delete_item(conn, uid, ids[0])
    assert tasks.dependencies(conn, nid) == [
        {"item": None, "text": "solder the header", "reason": "needs power"},
        {"item": ids[1], "text": "", "reason": ""}]
    body = tasks.note(conn, uid, nid)["body"]
    assert f'DEPENDS ON: deleted "solder the header" (needs power), [[#{ids[1]}]]' in body


def test_an_item_added_after_a_delete_takes_a_fresh_id_at_the_end(conn):
    uid, ids = _four(conn)
    tasks.delete_item(conn, uid, ids[3])
    new = tasks.add_items(conn, uid, ["label the box"])["ids"]
    assert new[0] > ids[3]
    assert tasks.item_ids(conn, uid) == ids[:3] + new


def test_a_position_given_for_an_id_is_refused_naming_the_id_there(conn):
    uid, ids = _four(conn)
    with pytest.raises(ValueError, match=rf"has no item 2; .*item 2 on the list is {ids[1]}"):
        tasks.require_item(conn, uid, 2)


def test_another_tasks_item_is_refused(conn):
    uid, _ = _four(conn)
    _, others = _four(conn)
    with pytest.raises(ValueError, match=f"has no item {others[0]}"):
        tasks.set_item_state(conn, uid, others[0], "done")


def test_the_deletion_enters_the_edit_history_by_id(conn):
    uid, ids = _four(conn)
    tasks.delete_item(conn, uid, ids[1])
    assert memories.get_edit_history(conn, uid)[-1]["note"] == f"item {ids[1]} deleted: flash the board"


def test_a_brief_is_stored_without_depends_and_read_with_it(conn):
    uid, ids = _four(conn)
    nid = tasks.add_note(conn, uid, title="Beam", items=[ids[2]], brief=_brief(f"#{ids[0]}"))
    stored = conn.execute("SELECT body, split_depends FROM task_notes WHERE id = ?", (nid,)).fetchone()
    assert "DEPENDS ON" not in stored["body"] and stored["split_depends"] == 1
    assert f"DEPENDS ON: [[#{ids[0]}]]" in tasks.note(conn, uid, nid)["body"]


def test_a_note_that_never_had_depends_does_not_gain_it(conn):
    uid, _ = _four(conn)
    body = "GOAL: g\nCONTEXT: c\nSTEPS: s\nPITFALLS: p\nDONE WHEN: d"
    nid = tasks.add_note(conn, uid, title="Loose", body=body, items=[])
    assert tasks.note(conn, uid, nid)["body"] == body
    assert tasks.dependencies(conn, nid) is None


def test_editing_another_field_keeps_the_dependencies(conn):
    uid, ids = _four(conn)
    nid = tasks.add_note(conn, uid, title="Beam", items=[ids[2]], brief=_brief(f"{ids[0]} (power)"))
    tasks.edit_note(conn, uid, nid, brief={"steps": "aim, then fire"})
    assert tasks.dependencies(conn, nid) == [{"item": ids[0], "text": "", "reason": "power"}]


def test_a_dependency_on_an_item_the_note_applies_to_is_refused(conn):
    uid, ids = _four(conn)
    with pytest.raises(ValueError, match=f"{ids[2]} is an item this note applies to"):
        tasks.add_note(conn, uid, title="Beam", items=[ids[2]], brief=_brief(str(ids[2])))


def test_a_position_key_in_depends_on_is_refused(conn):
    uid, ids = _four(conn)
    with pytest.raises(ValueError, match="i1 is a position; name the item by its id"):
        tasks.add_note(conn, uid, title="Beam", items=[ids[2]], brief=_brief("i1"))


def test_purging_a_task_removes_its_dependency_rows(conn):
    uid, ids = _four(conn)
    tasks.add_note(conn, uid, title="Beam", items=[ids[2]], brief=_brief(str(ids[0])))
    memories.purge_memory(conn, uid)
    assert conn.execute("SELECT COUNT(*) FROM task_note_depends").fetchone()[0] == 0


def test_dependency_rows_round_trip_through_the_token_grammar(conn):
    uid, ids = _four(conn)
    nid = tasks.add_note(conn, uid, title="Beam", items=[ids[2]],
                         brief=_brief(f"[[#{ids[0]}]] (a), {ids[1]}"))
    field = tasks.brief_fields(tasks.note(conn, uid, nid)["body"])["depends_on"]
    assert sections.read_depends(field)[0] == [
        sections.Dependency(ids[0], "", "", "a"), sections.Dependency(ids[1], "", "", "")]
