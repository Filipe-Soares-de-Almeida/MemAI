"""An item's key is its position: deleting one renumbers the items after it."""

from __future__ import annotations

import pytest

from conftest import brief
from memai import portable, tasks
from memai.store import connection, memories, task_items


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


def _four(conn):
    return tasks.create_task(conn, title="Ship the lantern", goal="Light the lantern",
                             items=["solder the header", "flash the board", "seal the case",
                                    "test the beam"], domain="acme/lantern")


def _keys(conn, uid):
    return [i["key"] for i in tasks.get_task(conn, uid)["items"]]


def test_deleting_the_first_item_renumbers_every_row_that_names_the_others(conn):
    uid = _four(conn)
    other = memories.insert_memory(conn, type="note", content="beam spec", domain="acme/lantern")
    tasks.set_item_state(conn, uid, "i3", "doing")
    tasks.link_item(conn, uid, "i4", [other])
    tasks.add_comment(conn, uid, "on the case", item="i3")
    tasks.add_comment(conn, uid, "on the task")
    nid = tasks.add_note(conn, uid, title="Beam", body=brief("aim the beam"), items=["i4"])
    result = tasks.delete_item(conn, uid, "1")
    assert result["renumbered"] == {"i2": "i1", "i3": "i2", "i4": "i3"}
    task = tasks.get_task(conn, uid)
    assert [(i["key"], i["seq"], i["text"], i["state"]) for i in task["items"]] == [
        ("i1", 1, "flash the board", "todo"), ("i2", 2, "seal the case", "doing"),
        ("i3", 3, "test the beam", "todo")]
    assert task["items"][2]["links"][0]["uid"] == other
    assert [(c["item"], c["body"]) for c in task["comments"]] == [("i2", "on the case"), ("", "on the task")]
    assert tasks.note(conn, uid, nid)["items"] == ["i3"]
    assert memories.get_memory(conn, uid)["content"] == (
        "GOAL: Light the lantern\n[ ] i1 flash the board\n[~] i2 seal the case\n[ ] i3 test the beam")


def test_deleting_the_last_item_renumbers_nothing(conn):
    uid = _four(conn)
    assert tasks.delete_item(conn, uid, "i4")["renumbered"] == {}
    assert _keys(conn, uid) == ["i1", "i2", "i3"]


def test_a_deletion_enters_the_edit_history_with_the_renumbering(conn):
    uid = _four(conn)
    tasks.delete_item(conn, uid, "i2")
    history = memories.get_edit_history(conn, uid)
    assert len(history) == 1
    assert history[0]["note"] == "item i2 deleted: flash the board; i3..i4 renumbered to i2..i3"
    tasks.delete_item(conn, uid, "i2")
    assert memories.get_edit_history(conn, uid)[-1]["note"] == (
        "item i2 deleted: seal the case; i3 renumbered to i2")


def test_an_add_after_a_delete_takes_the_next_position(conn):
    uid = _four(conn)
    tasks.delete_item(conn, uid, "i4")
    assert tasks.add_items(conn, uid, ["polish the lens"])["keys"] == ["i4"]


def test_a_brief_whose_last_item_goes_stays_a_brief(conn):
    uid = _four(conn)
    body = brief("aim the beam")
    nid = tasks.add_note(conn, uid, title="Beam", body=body, items=["i4"])
    tasks.delete_item(conn, uid, "i4")
    assert tasks.note(conn, uid, nid) | {"updated_at": ""} == {
        "id": nid, "title": "Beam", "body": body, "items": [], "updated_at": ""}


def test_compact_renames_through_temporary_keys_so_no_unique_key_collides(conn):
    uid = _four(conn)
    for old, new in (("i1", "x"), ("i2", "i1"), ("x", "i2")):
        conn.execute("UPDATE task_items SET item_key = ? WHERE memory_uid = ? AND item_key = ?",
                     (new, uid, old))
    tasks.add_comment(conn, uid, "on the header", item="i2")
    assert task_items.compact(conn, uid) == {"i2": "i1", "i1": "i2"}
    assert tasks.get_task(conn, uid)["comments"][0]["item"] == "i1"
    assert _keys(conn, uid) == ["i1", "i2", "i3", "i4"]


def test_a_store_with_gaps_opens_compacted(tmp_path):
    path = tmp_path / "gaps.db"
    with connection.connect(path) as c:
        uid = _four(c)
        c.execute("UPDATE task_items SET item_key = 'i7', seq = 7 WHERE memory_uid = ? AND item_key = 'i4'",
                  (uid,))
        c.execute("UPDATE memories SET content = 'stale' WHERE uid = ?", (uid,))
    with connection.connect(path) as c:
        assert _keys(c, uid) == ["i1", "i2", "i3", "i4"]
        assert memories.get_memory(c, uid)["content"].endswith("[ ] i4 test the beam")
        assert memories.get_edit_history(c, uid) == []


def test_a_clean_store_opens_untouched(tmp_path):
    path = tmp_path / "clean.db"
    with connection.connect(path) as c:
        uid = _four(c)
        stamp = memories.get_memory(c, uid)["updated_at"]
    with connection.connect(path) as c:
        assert memories.get_memory(c, uid)["updated_at"] == stamp


def test_an_export_with_gaps_restores_contiguous(tmp_path):
    with connection.connect(tmp_path / "a.db") as a:
        uid = _four(a)
        records = list(portable.export_records(a, include_archived=True))
    for r in records:
        if r["record"] == "task":
            r["items"][3] |= {"key": "i9", "seq": 9}
            r["comments"] = [{"item_key": "i9", "body": "on the beam", "author": "agent"}]
    with connection.connect(tmp_path / "b.db") as b:
        assert portable.import_records(b, records)["errors"] == []
        task = tasks.get_task(b, uid)
        assert [i["key"] for i in task["items"]] == ["i1", "i2", "i3", "i4"]
        assert task["comments"][0]["item"] == "i4"
        assert "[ ] i4 test the beam" in memories.get_memory(b, uid)["content"]


def test_the_task_item_docstring_says_a_key_is_a_position():
    from memai import server

    assert "an item's key is its position in the checklist" in server.task_item.__doc__
