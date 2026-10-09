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


def test_a_stray_item_row_does_not_stop_a_store_opening(tmp_path):
    path = tmp_path / "stray.db"
    with connection.connect(path) as c:
        uid = _four(c)
        other = memories.insert_memory(c, type="note", content="beam spec", domain="acme/lantern")
        c.execute("INSERT INTO task_items (memory_uid, item_key, seq, text, state, updated_at) "
                  "VALUES (?, 'i4', 4, 'stray', 'todo', '2026-01-01T00:00:00')", (other,))
        c.execute("UPDATE task_items SET item_key = 'i7', seq = 7 WHERE memory_uid = ? AND item_key = 'i4'",
                  (uid,))
    with connection.connect(path) as c:
        assert _keys(c, uid) == ["i1", "i2", "i3", "i4"]


def test_a_store_with_gaps_keeps_each_task_updated_stamp_when_it_opens(tmp_path):
    path = tmp_path / "stamps.db"
    with connection.connect(path) as c:
        uid = _four(c)
        c.execute("UPDATE task_items SET item_key = 'i7', seq = 7 WHERE memory_uid = ? AND item_key = 'i4'",
                  (uid,))
        c.execute("UPDATE memories SET updated_at = '2026-01-02T03:04:05' WHERE uid = ?", (uid,))
    with connection.connect(path) as c:
        assert _keys(c, uid) == ["i1", "i2", "i3", "i4"]
        assert memories.get_memory(c, uid)["updated_at"] == "2026-01-02T03:04:05"


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


def test_an_export_with_gaps_keeps_the_task_updated_stamp(tmp_path):
    with connection.connect(tmp_path / "a.db") as a:
        uid = _four(a)
        a.execute("UPDATE memories SET updated_at = '2026-01-02T03:04:05' WHERE uid = ?", (uid,))
        records = list(portable.export_records(a, include_archived=True))
    for r in records:
        if r["record"] == "task":
            r["items"][3] |= {"key": "i9", "seq": 9}
    with connection.connect(tmp_path / "b.db") as b:
        assert portable.import_records(b, records)["errors"] == []
        assert memories.get_memory(b, uid)["updated_at"] == "2026-01-02T03:04:05"


def test_a_deletion_stamps_the_task_updated_now(conn):
    uid = _four(conn)
    conn.execute("UPDATE memories SET updated_at = '2026-01-02T03:04:05' WHERE uid = ?", (uid,))
    tasks.delete_item(conn, uid, "i1")
    assert memories.get_memory(conn, uid)["updated_at"] > "2026-01-02T03:04:05"


def test_a_delete_is_not_blocked_by_a_flagged_tag_in_another_item(conn):
    uid = _four(conn)
    tasks.add_items(conn, uid, ["quote the </goal> tag"])
    result = tasks.delete_item(conn, uid, "i1")
    assert result["renumbered"] == {"i2": "i1", "i3": "i2", "i4": "i3", "i5": "i4"}
    assert tasks.get_task(conn, uid)["items"][-1]["text"] == "quote the </goal> tag"


def test_a_delete_with_a_stale_expectation_is_refused_before_anything_is_written(conn):
    uid = _four(conn)
    with pytest.raises(ValueError, match=r"i2.*reload"):
        tasks.delete_item(conn, uid, "i2", expect="seal the case")
    assert _keys(conn, uid) == ["i1", "i2", "i3", "i4"]
    assert memories.get_edit_history(conn, uid) == []


def test_a_delete_whose_expectation_matches_goes_through(conn):
    uid = _four(conn)
    assert tasks.delete_item(conn, uid, "i2", expect="flash the board")["item"] == "i2"
    assert tasks.delete_item(conn, uid, "i1", expect=None)["item"] == "i1"
    assert _keys(conn, uid) == ["i1", "i2"]


def test_the_task_item_docstring_says_a_key_is_a_position():
    from memai import server

    assert "an item's key is its position in the checklist" in server.task_item.__doc__


def _five(conn):
    return tasks.create_task(conn, title="Ship the lantern", goal="Light the lantern",
                             items=["solder the header", "flash the board", "seal the case",
                                    "test the beam", "pack the box"], domain="acme/lantern")


def _depends(conn, uid, nid):
    return tasks.brief_fields(tasks.note(conn, uid, nid)["body"])["depends_on"]


def test_deleting_an_item_rewrites_the_references_to_it_and_to_the_items_that_move(conn):
    uid = _five(conn)
    nid = tasks.add_note(conn, uid, title="Pack", items=["i5"],
                         body=brief("pack it", depends_on="i2 (setup), i4"))
    tasks.delete_item(conn, uid, "i2")
    assert _depends(conn, uid, nid) == 'deleted "flash the board" (setup), i3'
    assert tasks.note(conn, uid, nid)["items"] == ["i4"]


def test_the_deleted_key_and_the_key_that_takes_its_place_do_not_mix(conn):
    uid = _five(conn)
    nid = tasks.add_note(conn, uid, title="Pack", items=["i5"],
                         body=brief("pack it", depends_on="i1, i2, i3"))
    tasks.delete_item(conn, uid, "i1")
    assert _depends(conn, uid, nid) == 'deleted "solder the header", i1, i2'


def test_deleting_the_last_item_marks_its_references_without_renumbering(conn):
    uid = _five(conn)
    nid = tasks.add_note(conn, uid, title="Seal", items=["i3"],
                         body=brief("seal it", depends_on="i5 (the box), i1"))
    tasks.delete_item(conn, uid, "i5")
    assert _depends(conn, uid, nid) == 'deleted "pack the box" (the box), i1'


def test_a_deleted_text_with_a_quote_is_written_with_an_apostrophe(conn):
    uid = tasks.create_task(conn, title="Ship it", goal="Ship it",
                            items=['fix the "lens" mount', "pack the box"], domain="acme/lantern")
    nid = tasks.add_note(conn, uid, title="Pack", items=["i2"], body=brief("pack", depends_on="i1"))
    tasks.delete_item(conn, uid, "i1")
    assert _depends(conn, uid, nid) == "deleted \"fix the 'lens' mount\""


def test_a_free_text_depends_on_is_left_as_written_by_a_deletion(conn):
    uid = _five(conn)
    nid = tasks.add_note(conn, uid, title="Pack", items=["i5"], body=brief("pack it"))
    conn.execute("UPDATE task_notes SET body = REPLACE(body, 'DEPENDS ON: none', "
                 "'DEPENDS ON: Item 2 and the flash') WHERE id = ?", (nid,))
    before = tasks.note(conn, uid, nid)["body"]
    tasks.delete_item(conn, uid, "i2")
    assert tasks.note(conn, uid, nid)["body"] == before


def test_a_note_that_is_not_a_brief_is_left_alone_by_a_deletion(conn):
    uid = _five(conn)
    nid = tasks.add_note(conn, uid, title="Rule", body="DEPENDS ON: i2", items=[])
    tasks.delete_item(conn, uid, "i2")
    assert tasks.note(conn, uid, nid)["body"] == "DEPENDS ON: i2"


def test_the_rewrite_leaves_the_note_updated_stamp(conn):
    uid = _five(conn)
    nid = tasks.add_note(conn, uid, title="Pack", items=["i5"], body=brief("pack", depends_on="i3"))
    conn.execute("UPDATE task_notes SET updated_at = '2026-01-02T03:04:05' WHERE id = ?", (nid,))
    tasks.delete_item(conn, uid, "i1")
    assert _depends(conn, uid, nid) == "i2"
    assert tasks.note(conn, uid, nid)["updated_at"] == "2026-01-02T03:04:05"


def test_a_brief_with_none_is_not_rewritten(conn):
    uid = _five(conn)
    nid = tasks.add_note(conn, uid, title="Pack", items=["i5"], body=brief("pack"))
    before = tasks.note(conn, uid, nid)["body"]
    tasks.delete_item(conn, uid, "i1")
    assert tasks.note(conn, uid, nid)["body"] == before


def test_the_references_in_a_store_with_gaps_follow_the_compaction_when_it_opens(tmp_path):
    path = tmp_path / "gaps.db"
    with connection.connect(path) as c:
        uid = _five(c)
        nid = tasks.add_note(c, uid, title="Pack", items=["i5"],
                             body=brief("pack", depends_on="i4 (the beam), i3"))
        c.execute("UPDATE task_items SET item_key = 'i7', seq = 7 WHERE memory_uid = ? AND item_key = 'i4'",
                  (uid,))
        c.execute("UPDATE task_items SET item_key = 'i8', seq = 8 WHERE memory_uid = ? AND item_key = 'i5'",
                  (uid,))
        c.execute("UPDATE task_note_items SET item_key = 'i8' WHERE note_id = ?", (nid,))
        c.execute("UPDATE task_notes SET body = REPLACE(body, 'i4 (the beam)', 'i7 (the beam)') "
                  "WHERE id = ?", (nid,))
    with connection.connect(path) as c:
        assert _keys(c, uid) == ["i1", "i2", "i3", "i4", "i5"]
        assert _depends(c, uid, nid) == "i4 (the beam), i3"
