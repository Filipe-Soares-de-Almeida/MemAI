"""A task's free text mentions items as [[#id]], checked on write and resolved on read."""

import pytest

from memai import portable, tasks
from memai.store import connection


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


def _task(conn):
    uid = tasks.create_task(conn, title="Ship the lantern", goal="Light the bench",
                            items=["solder the header", "flash the board", "test the beam"])
    return uid, tasks.item_ids(conn, uid)


def _brief(**fields) -> dict:
    return {"goal": "g", "context": "c", "steps": "s", "pitfalls": "p", "done_when": "d",
            "depends_on": "none", **fields}


def test_a_comment_mentions_a_live_item(conn):
    uid, ids = _task(conn)
    tasks.add_comment(conn, uid, f"wait for [[#{ids[0]}]]")
    page = tasks.read_part(conn, uid, "comments")
    assert page["refs"] == {str(ids[0]): {"n": 1, "text": "solder the header", "state": "todo"}}


def test_a_page_without_mentions_carries_no_refs(conn):
    uid, _ = _task(conn)
    tasks.add_comment(conn, uid, "plain words")
    assert "refs" not in tasks.read_part(conn, uid, "comments")


def test_a_mention_of_another_tasks_item_is_refused(conn):
    uid, _ = _task(conn)
    _, others = _task(conn)
    with pytest.raises(ValueError, match=rf"mentions \[\[#{others[0]}\]\], which is not an item of this task"):
        tasks.add_comment(conn, uid, f"see [[#{others[0]}]]")


def test_a_label_never_carries_a_mention(conn):
    uid, ids = _task(conn)
    with pytest.raises(ValueError, match="a label never carries a mention"):
        tasks.rename_item(conn, uid, ids[1], f"after [[#{ids[0]}]]")
    with pytest.raises(ValueError, match="a label never carries a mention"):
        tasks.add_items(conn, uid, [f"after [[#{ids[0]}]]"])
    with pytest.raises(ValueError, match="a label never carries a mention"):
        tasks.add_note(conn, uid, title=f"on [[#{ids[0]}]]", body="free text", items=[])


def test_a_new_tasks_goal_cannot_mention_its_items_yet(conn):
    with pytest.raises(ValueError, match="cannot mention its items"):
        tasks.create_task(conn, title="T", goal="after [[#1]]", items=["a"])


def test_a_goal_mentions_items_and_reads_them_back(conn):
    uid, ids = _task(conn)
    tasks.set_goal(conn, uid, f"Light the bench once [[#{ids[2]}]] passes")
    assert tasks.head(conn, uid)["refs"] == {str(ids[2]): {"n": 3, "text": "test the beam", "state": "todo"}}


def test_a_brief_field_mentions_an_item(conn):
    uid, ids = _task(conn)
    nid = tasks.add_note(conn, uid, title="Beam", items=[ids[2]],
                         brief=_brief(context=f"bench from [[#{ids[0]}]]"))
    assert f"[[#{ids[0]}]]" in tasks.note(conn, uid, nid)["body"]
    assert tasks.read_part(conn, uid, "notes", ids[2])["refs"][str(ids[0])]["n"] == 1


def test_a_brief_field_mentioning_an_unknown_item_is_refused(conn):
    uid, ids = _task(conn)
    with pytest.raises(ValueError, match="not an item of this task"):
        tasks.add_note(conn, uid, title="Beam", items=[ids[2]], brief=_brief(steps="after [[#999999]]"))


def test_a_mention_of_a_deleted_item_reads_deleted(conn):
    uid, ids = _task(conn)
    tasks.add_comment(conn, uid, f"wait for [[#{ids[0]}]]")
    tasks.delete_item(conn, uid, ids[0])
    assert tasks.read_part(conn, uid, "comments")["refs"] == {str(ids[0]): {"deleted": True}}


def test_an_edit_keeps_a_mention_of_a_deleted_item(conn):
    uid, ids = _task(conn)
    tasks.set_goal(conn, uid, f"Light the bench after [[#{ids[0]}]]")
    tasks.delete_item(conn, uid, ids[0])
    tasks.set_goal(conn, uid, f"Light the bench, after [[#{ids[0]}]]")
    assert f"[[#{ids[0]}]]" in tasks.get_task(conn, uid)["goal"]


def test_a_new_mention_of_a_deleted_item_is_refused(conn):
    uid, ids = _task(conn)
    tasks.delete_item(conn, uid, ids[0])
    with pytest.raises(ValueError, match="not an item of this task"):
        tasks.add_comment(conn, uid, f"see [[#{ids[0]}]]")


def test_a_position_key_in_free_text_names_its_token(conn):
    uid, ids = _task(conn)
    with pytest.raises(ValueError, match=rf"comment cites i2 \(\[\[#{ids[1]}\]\]\): an item is cited in free text as \[\[#id\]\]"):
        tasks.add_comment(conn, uid, "after i2")


def test_an_imported_mention_of_a_deleted_item_reads_deleted(tmp_path):
    with connection.connect(tmp_path / "a.db") as src:
        uid, ids = _task(src)
        tasks.add_comment(src, uid, f"wait for [[#{ids[0]}]] and [[#{ids[1]}]]")
        tasks.set_goal(src, uid, f"Light the bench after [[#{ids[2]}]]")
        tasks.delete_item(src, uid, ids[0])
        records = list(portable.export_records(src, uids=[uid], include_archived=True))
    with connection.connect(tmp_path / "b.db") as dst:
        tasks.create_task(dst, title="Other", goal="g", items=[f"x{n}" for n in range(1, 30)])
        assert portable.import_records(dst, records)["errors"] == []
        new_ids = tasks.item_ids(dst, uid)
        assert tasks.get_task(dst, uid)["comments"][0]["body"] == f"wait for [[#0]] and [[#{new_ids[0]}]]"
        assert tasks.get_task(dst, uid)["goal"] == f"Light the bench after [[#{new_ids[1]}]]"
        assert tasks.read_part(dst, uid, "comments")["refs"]["0"] == {"deleted": True}
