import pytest

from conftest import item_at
from memai import pending, tasks
from memai.store import connection, dedup, memories, optimizer, search, task_items


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


def _make(conn):
    return tasks.create_task(
        conn,
        title="Ship the parser",
        goal="Parse every config file",
        items=["read the spec", "write the lexer"],
        domain="acme/parser",
    )


def test_a_new_store_has_the_task_tables(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"tasks", "task_items", "task_item_links", "task_comments"} <= names


def test_creating_a_task_writes_the_memory_and_its_items(conn):
    uid = _make(conn)
    assert memories.get_memory(conn, uid)["type"] == "task"
    task = tasks.get_task(conn, uid)
    assert task["state"] == "open"
    assert task["goal"] == "Parse every config file"
    assert [i["id"] for i in task["items"]] == tasks.item_ids(conn, uid)
    assert [i["n"] for i in task["items"]] == [1, 2]
    assert [i["state"] for i in task["items"]] == ["todo", "todo"]
    assert [i["seq"] for i in task["items"]] == [1, 2]
    assert task["comments"] == []


def test_the_content_is_rendered_from_goal_and_items(conn):
    uid = _make(conn)
    a, b = tasks.item_ids(conn, uid)
    assert memories.get_memory(conn, uid)["content"] == (
        f"GOAL: Parse every config file\n[ ] {a} read the spec\n[ ] {b} write the lexer"
    )


def test_render_uses_one_mark_per_state():
    items = [
        {"id": 11, "text": "a", "state": "todo"},
        {"id": 12, "text": "b", "state": "doing"},
        {"id": 13, "text": "c", "state": "done"},
        {"id": 14, "text": "d", "state": "dropped"},
    ]
    assert task_items.render("g", items) == "GOAL: g\n[ ] 11 a\n[~] 12 b\n[x] 13 c\n[-] 14 d"


def test_the_content_is_searchable(conn):
    uid = _make(conn)
    assert uid in [r["uid"] for r in search.search_ranked(conn, "lexer")]


def test_an_item_is_named_by_its_id_as_a_number_or_its_digits(conn):
    uid = _make(conn)
    first = tasks.item_ids(conn, uid)[0]
    assert tasks.require_item(conn, uid, first) == tasks.require_item(conn, uid, str(first)) == first
    for bad in ("x", "i1", "", True, None):
        with pytest.raises(ValueError, match="not an item id"):
            tasks.require_item(conn, uid, bad)


def test_split_items_trims_and_skips_blank_lines():
    assert tasks.split_items("a\n\n  b \n") == ["a", "b"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"goal": ""},
        {"items": []},
        {"items": [f"step {n}" for n in range(51)]},
        {"items": ["x" * 301]},
        {"goal": "g" * 2001},
        {"title": ""},
    ],
    ids=["empty-goal", "no-items", "51-items", "long-item", "long-goal", "empty-title"],
)
def test_creation_refuses_out_of_limit_input_and_writes_nothing(conn, kwargs):
    args = {"title": "Ship the parser", "goal": "Parse it", "items": ["one"]}
    args.update(kwargs)
    before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    with pytest.raises(ValueError):
        tasks.create_task(conn, **args)
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM task_items").fetchone()[0] == 0


def test_creation_accepts_input_exactly_at_each_limit(conn):
    uid = tasks.create_task(
        conn, title="At the limits", goal="g" * 2000,
        items=["x" * tasks.ITEM_MAX] + [f"step {n}" for n in range(49)],
    )
    task = tasks.get_task(conn, uid)
    assert len(task["goal"]) == 2000
    assert len(task["items"]) == 50 and len(task["items"][0]["text"]) == tasks.ITEM_MAX


def test_a_created_task_records_its_session_tags_and_cross_listing(conn):
    uid = tasks.create_task(
        conn, title="Ship the parser", goal="Parse it", items=["read the spec", "write the lexer"],
        domain="acme/parser", also="acme/lexer", tags="parser,release", session="session-7",
    )
    memory = memories.get_memory(conn, uid)
    assert memory["session"] == "session-7"
    assert memory["tags"] == "parser,release"
    assert memory["also_domains"] == "acme/lexer"
    items = tasks.get_task(conn, uid)["items"]
    assert [i["updated_session"] for i in items] == ["session-7", "session-7"]


def test_add_items_accepts_up_to_fifty_items_and_an_item_at_the_length_limit(conn):
    uid = _make(conn)
    ids = tasks.add_items(conn, uid, ["x" * tasks.ITEM_MAX] + [f"step {n}" for n in range(47)])["ids"]
    assert len(ids) == 48 and len(tasks.get_task(conn, uid)["items"]) == 50
    with pytest.raises(ValueError):
        tasks.add_items(conn, uid, ["one too many"])
    with pytest.raises(ValueError):
        tasks.add_items(conn, uid, ["x" * (tasks.ITEM_MAX + 1)])
    assert len(tasks.get_task(conn, uid)["items"]) == 50


def test_a_comment_of_exactly_two_thousand_characters_is_stored_and_one_more_is_refused(conn):
    uid = _make(conn)
    assert isinstance(tasks.add_comment(conn, uid, "c" * 2000), int)
    with pytest.raises(ValueError, match="2001"):
        tasks.add_comment(conn, uid, "c" * 2001)
    assert len(tasks.get_task(conn, uid)["comments"]) == 1


def test_get_task_of_another_type_is_none(conn):
    uid = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    assert tasks.get_task(conn, uid) is None
    assert not tasks.is_task(conn, uid)


def test_is_task_is_true_for_a_task(conn):
    assert tasks.is_task(conn, _make(conn))


def _notes(conn, uid):
    return [r["note"] for r in conn.execute("SELECT note FROM edits WHERE memory_uid = ? ORDER BY id", (uid,))]


def _edit_count(conn, uid):
    return conn.execute("SELECT COUNT(*) FROM edits WHERE memory_uid = ?", (uid,)).fetchone()[0]


def _close_both(conn, uid):
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "done")
    return tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "done")


def test_marking_an_item_regenerates_the_content_without_an_edit(conn):
    uid = _make(conn)
    first = item_at(conn, uid, 1)
    result = tasks.set_item_state(conn, uid, first, "doing")
    assert result["item"] == first and result["state"] == "doing" and result["changed"] is True
    assert result["progress"] == {"done": 0, "dropped": 0, "total": 2}
    assert result["task_state"] == "open" and result["archived"] is False
    assert memories.get_memory(conn, uid)["content"].splitlines()[1] == f"[~] {first} read the spec"
    assert _notes(conn, uid) == []


def test_the_last_item_done_completes_and_archives_the_task(conn):
    uid = _make(conn)
    result = _close_both(conn, uid)
    assert result["task_state"] == "completed" and result["archived"] is True
    assert memories.get_memory(conn, uid)["status"] == "archived"
    assert tasks.get_task(conn, uid)["completed_at"] != ""
    assert "completed" in _notes(conn, uid)


def test_done_and_dropped_mix_still_completes(conn):
    uid = _make(conn)
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "done")
    result = tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "dropped")
    assert result["task_state"] == "completed"
    assert result["progress"] == {"done": 1, "dropped": 1, "total": 2}


def test_all_dropped_cancels(conn):
    uid = _make(conn)
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "dropped")
    result = tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "dropped")
    assert result["task_state"] == "cancelled" and result["archived"] is True
    assert memories.get_memory(conn, uid)["status"] == "archived"
    assert _notes(conn, uid)[-1] == "cancelled"


def test_reopening_an_item_reopens_the_task(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    result = tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "todo")
    assert result["task_state"] == "open" and result["archived"] is False
    assert memories.get_memory(conn, uid)["status"] == "active"
    assert tasks.get_task(conn, uid)["completed_at"] == ""
    assert _notes(conn, uid)[-1] == "reopened"


def test_adding_an_item_reopens_a_completed_task(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    result = tasks.add_items(conn, uid, ["ship it"])
    added = result["ids"][0]
    assert result["ids"] == [item_at(conn, uid, 3)] and result["task_state"] == "open"
    assert result["archived"] is False and result["progress"]["total"] == 3
    assert memories.get_memory(conn, uid)["status"] == "active"
    assert memories.get_memory(conn, uid)["content"].splitlines()[-1] == f"[ ] {added} ship it"
    notes = _notes(conn, uid)
    assert f"item {added} added" not in notes and notes[-1] == "reopened"


def test_consecutive_adds_go_to_the_end_with_rising_ids(conn):
    uid = _make(conn)
    added = [tasks.add_items(conn, uid, texts)["ids"] for texts in (["third"], ["fourth"], ["fifth", "sixth"])]
    ids = [i for chunk in added for i in chunk]
    assert ids == sorted(ids) and tasks.item_ids(conn, uid)[2:] == ids
    assert [i["n"] for i in tasks.get_task(conn, uid)["items"]] == [1, 2, 3, 4, 5, 6]
    assert _notes(conn, uid) == []


def test_add_items_refuses_bad_input_and_writes_nothing(conn):
    uid = _make(conn)
    before = _edit_count(conn, uid)
    for bad in ([], [""], ["x" * 301], [f"step {n}" for n in range(49)]):
        with pytest.raises(ValueError):
            tasks.add_items(conn, uid, bad)
    assert _edit_count(conn, uid) == before
    assert len(tasks.get_task(conn, uid)["items"]) == 2


def test_the_same_state_twice_writes_nothing(conn):
    uid = _make(conn)
    assert tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "done")["changed"] is True
    before = _edit_count(conn, uid)
    again = tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "done")
    assert again["changed"] is False and again["state"] == "done"
    assert _edit_count(conn, uid) == before


def test_two_connections_close_the_last_items_once(tmp_path):
    path = tmp_path / "shared.db"
    with connection.connect(path) as first:
        uid = _make(first)
    with connection.connect(path) as a:
        tasks.set_item_state(a, uid, item_at(a, uid, 1), "done")
    with connection.connect(path) as b:
        result = tasks.set_item_state(b, uid, item_at(b, uid, 2), "done")
    assert result["task_state"] == "completed"
    with connection.connect(path) as c:
        assert _notes(c, uid).count("completed") == 1


def test_a_write_reads_the_other_connections_commit(tmp_path):
    path = tmp_path / "shared.db"
    with connection.connect(path) as a:
        uid = _make(a)
        a.commit()
        with connection.connect(path) as b:
            b.commit()
            tasks.set_item_state(a, uid, item_at(a, uid, 1), "done")
            a.commit()
            result = tasks.set_item_state(b, uid, item_at(b, uid, 2), "done")
            b.commit()
            assert result["task_state"] == "completed"
            assert _notes(a, uid).count("completed") == 1


def test_archiving_an_open_task_by_hand_cancels_it(conn):
    uid = _make(conn)
    memories.set_status(conn, uid, "archived")
    assert tasks.get_task(conn, uid)["state"] == "cancelled"


def test_archiving_a_completed_task_keeps_it_completed(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    memories.set_status(conn, uid, "archived")
    assert tasks.get_task(conn, uid)["state"] == "completed"


def test_restoring_a_task_reopens_it(conn):
    uid = _make(conn)
    memories.set_status(conn, uid, "archived")
    memories.set_status(conn, uid, "active")
    task = tasks.get_task(conn, uid)
    assert task["state"] == "open" and task["completed_at"] == ""
    assert [i["state"] for i in task["items"]] == ["todo", "todo"]


def test_restoring_a_completed_task_keeps_it_completed(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    completed_at = tasks.get_task(conn, uid)["completed_at"]
    memories.set_status(conn, uid, "active")
    task = tasks.get_task(conn, uid)
    assert task["state"] == "completed" and task["completed_at"] == completed_at
    assert pending.open_task_uids(conn, ["acme/parser"]) == []


def test_a_restored_completed_task_reopens_when_an_item_does(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    memories.set_status(conn, uid, "active")
    result = tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "todo")
    assert result["task_state"] == "open" and result["archived"] is False
    assert tasks.get_task(conn, uid)["completed_at"] == ""


def test_restoring_a_cancelled_task_with_every_item_dropped_keeps_it_cancelled(conn):
    uid = _make(conn)
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "dropped")
    tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "dropped")
    memories.set_status(conn, uid, "active")
    assert tasks.get_task(conn, uid)["state"] == "cancelled"


def test_set_status_leaves_other_types_alone(conn):
    uid = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    memories.set_status(conn, uid, "archived")
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_goal_edit_regenerates_content(conn):
    uid = _make(conn)
    tasks.set_goal(conn, uid, "Parse every file")
    assert memories.get_memory(conn, uid)["content"].splitlines()[0] == "GOAL: Parse every file"
    assert tasks.get_task(conn, uid)["goal"] == "Parse every file"
    assert _notes(conn, uid)[-1] == "goal edited"
    before = _edit_count(conn, uid)
    for bad in ("g" * 2001, "  "):
        with pytest.raises(ValueError):
            tasks.set_goal(conn, uid, bad)
    tasks.set_goal(conn, uid, "Parse every file")
    assert _edit_count(conn, uid) == before


def test_comments_are_kept_apart_from_the_content(conn):
    uid = _make(conn)
    content = memories.get_memory(conn, uid)["content"]
    first_item = item_at(conn, uid, 1)
    first = tasks.add_comment(conn, uid, "starting here", item=first_item)
    second = tasks.add_comment(conn, uid, "overall note")
    assert isinstance(first, int) and second > first
    assert memories.get_memory(conn, uid)["content"] == content
    comments = tasks.get_task(conn, uid)["comments"]
    assert [c["item"] for c in comments] == [first_item, None]
    assert [c["author"] for c in comments] == ["agent", "agent"]
    assert tasks.add_comment(conn, uid, "seen it", author="person") > second
    for bad in ("x" * 2001, "", "   "):
        with pytest.raises(ValueError):
            tasks.add_comment(conn, uid, bad)
    with pytest.raises(ValueError):
        tasks.add_comment(conn, uid, "ok", author="robot")
    with pytest.raises(ValueError):
        tasks.add_comment(conn, uid, "ok", item=999_999)
    assert len(tasks.get_task(conn, uid)["comments"]) == 3


def test_links_to_unknown_memories_are_refused_whole(conn):
    uid = _make(conn)
    note = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    with pytest.raises(ValueError):
        tasks.link_item(conn, uid, item_at(conn, uid, 1), [note, "ffffffffffffffff"])
    assert conn.execute("SELECT COUNT(*) FROM task_item_links").fetchone()[0] == 0


def test_link_and_unlink(conn):
    uid = _make(conn)
    note = memories.insert_memory(conn, type="note", content="a plain note", title="Lexer notes", domain="acme/parser")
    assert tasks.link_item(conn, uid, item_at(conn, uid, 1), [note]) == [note]
    links = tasks.get_task(conn, uid)["items"][0]["links"]
    assert links == [{"uid": note, "title": "Lexer notes", "type": "note"}]
    assert tasks.link_item(conn, uid, item_at(conn, uid, 1), [note]) == []
    assert tasks.unlink_item(conn, uid, item_at(conn, uid, 1), note) is True
    assert tasks.get_task(conn, uid)["items"][0]["links"] == []
    assert tasks.unlink_item(conn, uid, item_at(conn, uid, 1), note) is False


def test_unknown_item_and_state_are_refused(conn):
    uid = _make(conn)
    before = _edit_count(conn, uid)
    with pytest.raises(ValueError):
        tasks.set_item_state(conn, uid, 999_999, "done")
    with pytest.raises(ValueError):
        tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "finished")
    with pytest.raises(ValueError):
        tasks.link_item(conn, uid, 999_999, [])
    with pytest.raises(ValueError):
        tasks.unlink_item(conn, uid, 999_999, "x")
    assert _edit_count(conn, uid) == before


def test_a_uid_that_is_not_a_task_is_refused(conn):
    note = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    calls = [
        lambda u: tasks.set_item_state(conn, u, 1, "done"),
        lambda u: tasks.add_items(conn, u, ["x"]),
        lambda u: tasks.set_goal(conn, u, "g"),
        lambda u: tasks.add_comment(conn, u, "c"),
        lambda u: tasks.link_item(conn, u, 1, [note]),
        lambda u: tasks.unlink_item(conn, u, 1, note),
        lambda u: tasks.progress(conn, u),
    ]
    for call in calls:
        for uid in (note, "ffffffffffffffff"):
            with pytest.raises(ValueError, match="no task"):
                call(uid)


def test_progress_counts_done_and_dropped(conn):
    uid = _make(conn)
    assert tasks.progress(conn, uid) == {"done": 0, "dropped": 0, "total": 2}
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "done")
    assert tasks.progress(conn, uid) == {"done": 1, "dropped": 0, "total": 2}


# ------------------------------------------------- purge, portability, exclusions

def _full_task(conn, title="Ship the parser"):
    """A task with a link, a task-level comment and an item comment."""
    note = memories.insert_memory(conn, type="note", content="the lexer reads one token", domain="acme/parser")
    uid = tasks.create_task(
        conn, title=title, goal="Parse every config file",
        items=["read the spec", "write the lexer"], domain="acme/parser", tags="parser, lexer",
    )
    tasks.link_item(conn, uid, item_at(conn, uid, 2), [note])
    tasks.add_comment(conn, uid, "start with the spec", author="person")
    tasks.add_comment(conn, uid, "lexer drafted", item=item_at(conn, uid, 2), author="agent", session="s1")
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "done")
    return uid, note


def _task_rows(conn, uid):
    return [
        conn.execute(f"SELECT COUNT(*) FROM {table} WHERE memory_uid = ?", (uid,)).fetchone()[0]
        for table in ("tasks", "task_items", "task_item_links", "task_comments")
    ]


def test_purging_a_task_removes_every_child_row(conn):
    uid, _ = _full_task(conn)
    assert all(_task_rows(conn, uid))
    assert memories.purge_memory(conn, uid) is True
    assert _task_rows(conn, uid) == [0, 0, 0, 0]
    assert memories.get_memory(conn, uid) is None


def test_purging_a_linked_memory_removes_the_item_link(conn):
    uid, note = _full_task(conn)
    assert memories.purge_memory(conn, note) is True
    assert conn.execute("SELECT COUNT(*) FROM task_item_links").fetchone()[0] == 0
    assert memories.get_memory(conn, uid) is not None
    assert tasks.get_task(conn, uid)["items"][1]["links"] == []


def _shape(task):
    return {
        "goal": task["goal"], "state": task["state"],
        "items": [(i["n"], i["text"], i["state"], [link["uid"] for link in i["links"]])
                  for i in task["items"]],
        "comments": [(c["body"], c["author"], c["item"] is None) for c in task["comments"]],
    }


def test_a_task_round_trips_through_export_and_import(tmp_path):
    from memai import portable

    with connection.connect(tmp_path / "a.db") as a:
        uid, note = _full_task(a)
        tasks.set_item_state(a, uid, item_at(a, uid, 2), "done")
        records = list(portable.export_records(a, include_archived=True, include_edits=True))
        expected = tasks.get_task(a, uid)
        edits = a.execute("SELECT COUNT(*) FROM edits WHERE memory_uid = ?", (uid,)).fetchone()[0]
    assert [r for r in records if r["record"] == "task"][0]["uid"] == uid
    with connection.connect(tmp_path / "b.db") as b:
        result = portable.import_records(b, records)
        assert result["errors"] == []
        got = tasks.get_task(b, uid)
        assert _shape(got) == _shape(expected)
        assert got["completed_at"] == expected["completed_at"] != ""
        assert got["items"][1]["links"][0]["uid"] == note
        assert [i["updated_at"] for i in got["items"]] == [i["updated_at"] for i in expected["items"]]
        assert b.execute("SELECT COUNT(*) FROM edits WHERE memory_uid = ?", (uid,)).fetchone()[0] == edits
        # a second import adds nothing
        portable.import_records(b, records)
        assert _task_rows(b, uid) == [1, 2, 1, 2]


def test_an_export_after_a_delete_restores_the_items_in_order(tmp_path):
    from memai import portable

    with connection.connect(tmp_path / "a.db") as a:
        uid = _three(a)
        tasks.delete_item(a, uid, item_at(a, uid, 2))
        records = list(portable.export_records(a, include_archived=True, include_edits=True))
    with connection.connect(tmp_path / "b.db") as b:
        assert portable.import_records(b, records)["errors"] == []
        items = tasks.get_task(b, uid)["items"]
        assert [(i["n"], i["text"]) for i in items] == [(1, "read the spec"), (2, "write the parser")]


def test_importing_into_a_store_that_holds_the_task_keeps_its_rows(tmp_path):
    from memai import portable

    with connection.connect(tmp_path / "a.db") as a:
        uid, _ = _full_task(a)
        records = list(portable.export_records(a, include_archived=True))
        tasks.add_comment(a, uid, "later remark")
        portable.import_records(a, records)
        assert _task_rows(a, uid) == [1, 2, 1, 3]


def test_restore_task_skips_a_link_whose_target_is_missing(conn):
    note = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    memories.restore_memory(conn, {"record": "memory", "uid": "aaaaaaaaaaaaaaaa", "type": "task",
                             "content": "GOAL: g\n[ ] i1 step", "domain": "acme/parser"})
    tasks.restore_task(conn, {
        "record": "task", "uid": "aaaaaaaaaaaaaaaa", "goal": "g", "state": "open",
        "completed_at": "",
        "items": [{"id": 7, "seq": 1, "text": "step", "state": "todo",
                   "updated_at": "2026-01-01T00:00:00+00:00", "updated_session": ""}],
        "links": [{"item_id": 7, "target_uid": note, "created_at": "2026-01-01T00:00:00+00:00"},
                  {"item_id": 7, "target_uid": "bbbbbbbbbbbbbbbb",
                   "created_at": "2026-01-01T00:00:00+00:00"}],
        "comments": [],
    })
    assert [link["uid"] for link in tasks.get_task(conn, "aaaaaaaaaaaaaaaa")["items"][0]["links"]] == [note]


def test_tasks_are_not_dedup_or_similar_candidates(conn):
    one, _ = _full_task(conn, title="Ship the parser")
    two = tasks.create_task(
        conn, title="Ship the parser again", goal="Parse every config file",
        items=["read the spec", "write the lexer"], domain="acme/parser",
    )
    tasks.set_item_state(conn, two, item_at(conn, two, 1), "done")
    assert dedup.dedup_candidates(conn, threshold=0.1) == []
    assert dedup.dedup_candidates(conn, type="task", threshold=0.1) == []
    assert dedup.similar_memories(conn, one, threshold=0.1) == []
    twin = memories.insert_memory(conn, type="note", content=memories.get_memory(conn, one)["content"],
                            domain="acme/parser")
    assert {s["type"] for s in dedup.similar_memories(conn, twin, threshold=0.1)} <= {"note"}


def test_a_task_is_not_a_distill_source(conn):
    uid, _ = _full_task(conn)
    other = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    res = optimizer.stage_optimization(conn, "distill a task", [{
        "kind": "distill", "verified": "checked",
        "payload": {"source_uids": [uid, other], "new_type": "note",
                    "new_content": "the durable fact", "title": "What the parser reads"},
    }])
    assert res["staged"] == 0
    assert "task" in res["errors"][0]["error"]


def test_edit_memory_refuses_a_task(tmp_path, monkeypatch):
    from memai import server

    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))

    with connection.connect() as conn:
        uid = tasks.create_task(conn, title="Ship the parser", goal="Parse every config file",
                                items=["read the spec"], domain="acme/parser")
        before = memories.get_memory(conn, uid)["content"]
    res = server.edit_memory(uid, new_content="x")
    assert res["ok"] is False and "task" in res["errors"][0]
    with connection.connect() as conn:
        assert memories.get_memory(conn, uid)["content"] == before


def test_opening_a_store_cancels_an_open_task_whose_memory_is_archived(tmp_path):
    path = tmp_path / "test.db"
    with connection.connect(path) as c:
        archived, kept = _make(c), _make(c)
        done = _make(c)
        _close_both(c, done)
        for uid in (archived, done):
            c.execute("UPDATE memories SET status = 'archived' WHERE uid = ?", (uid,))
    with connection.connect(path) as c:
        assert tasks.get_task(c, archived)["state"] == "cancelled"
        assert tasks.get_task(c, kept)["state"] == "open"
        assert tasks.get_task(c, done)["state"] == "completed"


# ------------------------------------------------------- comments are refused a leak

LEAK = "see the call </parameter> that ended early"


def test_add_comment_refuses_a_tool_calls_closing_tag(conn):
    uid = _make(conn)
    with pytest.raises(ValueError, match="tool call"):
        tasks.add_comment(conn, uid, LEAK)
    with pytest.raises(ValueError, match="tool call"):
        tasks.add_comment(conn, uid, LEAK, item=item_at(conn, uid, 1))
    assert tasks.get_task(conn, uid)["comments"] == []


# ------------------------------------------------------- import validates states

def _record(**over) -> dict:
    record = {
        "record": "task", "uid": "aaaaaaaaaaaaaaaa", "goal": "g", "state": "open",
        "completed_at": "",
        "items": [{"id": 7, "seq": 1, "text": "step", "state": "todo",
                   "updated_at": "2026-01-01T00:00:00+00:00", "updated_session": ""}],
        "links": [], "comments": [],
    }
    return {**record, **over}


def _memory_record() -> dict:
    return {"record": "memory", "uid": "aaaaaaaaaaaaaaaa", "type": "task",
            "content": "GOAL: g\n[ ] i1 step", "domain": "acme/parser", "title": "A task"}


def _bad_item(state: str) -> dict:
    item = _record()["items"][0]
    return _record(items=[{**item, "id": 8, "seq": 2, "state": state}, item])


@pytest.mark.parametrize("record", [
    _record(state="bogus"),
    _bad_item('"><img src=x onerror=alert(1)>'),
])
def test_restore_task_refuses_a_state_outside_its_vocabulary(conn, record):
    memories.restore_memory(conn, _memory_record())
    with pytest.raises(ValueError, match="state"):
        tasks.restore_task(conn, record)


@pytest.mark.parametrize("record", [
    _record(state="bogus"),
    _bad_item('"><img src=x onerror=alert(1)>'),
])
def test_importing_a_bad_state_is_an_error_and_leaves_no_task_rows(conn, record):
    from memai import portable

    result = portable.import_records(conn, [_memory_record(), record])
    assert [e["uid"] for e in result["errors"]] == ["aaaaaaaaaaaaaaaa"]
    assert "state" in result["errors"][0]["error"]
    for table in ("tasks", "task_items", "task_item_links", "task_comments"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_a_task_record_that_fails_midway_leaves_no_rows(conn):
    from memai import portable

    item = _record()["items"][0]
    result = portable.import_records(
        conn, [_memory_record(), _record(items=[item, {**item, "id": 8, "seq": 2, "text": None}])])
    assert [e["uid"] for e in result["errors"]] == ["aaaaaaaaaaaaaaaa"]
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM task_items").fetchone()[0] == 0


def _three(conn):
    return tasks.create_task(
        conn, title="Ship the parser", goal="Parse every config file",
        items=["read the spec", "write the lexer", "write the parser"], domain="acme/parser",
    )


def test_delete_item_removes_the_item_its_comments_and_its_links(conn):
    uid = _three(conn)
    note = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    tasks.link_item(conn, uid, item_at(conn, uid, 2), [note])
    tasks.link_item(conn, uid, item_at(conn, uid, 3), [note])
    tasks.add_comment(conn, uid, "about the lexer", item=item_at(conn, uid, 2))
    tasks.add_comment(conn, uid, "about the parser", item=item_at(conn, uid, 3))
    tasks.add_comment(conn, uid, "about the whole task")
    first, second, third = tasks.item_ids(conn, uid)
    result = tasks.delete_item(conn, uid, second)
    assert result["uid"] == uid and result["item"] == second
    assert result["progress"] == {"done": 0, "dropped": 0, "total": 2}
    assert result["task_state"] == "open" and result["archived"] is False
    assert "renumbered" not in result
    task = tasks.get_task(conn, uid)
    assert [i["id"] for i in task["items"]] == [first, third]
    assert [link["uid"] for i in task["items"] for link in i["links"]] == [note]
    assert [c["body"] for c in task["comments"]] == ["about the parser", "about the whole task"]
    assert memories.get_memory(conn, uid)["content"] == (
        f"GOAL: Parse every config file\n[ ] {first} read the spec\n[ ] {third} write the parser")
    assert _notes(conn, uid) == [f"item {second} deleted: write the lexer"]


def test_the_only_item_cannot_be_deleted(conn):
    uid = tasks.create_task(conn, title="One step", goal="Do it", items=["the only step"])
    before = _edit_count(conn, uid)
    with pytest.raises(ValueError, match="a task keeps at least one item"):
        tasks.delete_item(conn, uid, item_at(conn, uid, 1))
    assert _edit_count(conn, uid) == before
    assert len(tasks.get_task(conn, uid)["items"]) == 1


def test_delete_item_refuses_an_unknown_item_and_a_non_task(conn):
    uid = _three(conn)
    before = _edit_count(conn, uid)
    with pytest.raises(ValueError, match="no item 999999"):
        tasks.delete_item(conn, uid, 999_999)
    with pytest.raises(ValueError, match="not an item id"):
        tasks.delete_item(conn, uid, "x")
    note = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    with pytest.raises(ValueError, match="no task"):
        tasks.delete_item(conn, note, 1)
    assert _edit_count(conn, uid) == before
    assert len(tasks.get_task(conn, uid)["items"]) == 3


def test_an_add_after_deletes_never_reuses_an_id(conn):
    uid = _three(conn)
    gone = tasks.item_ids(conn, uid)[1:]
    for item in gone:
        tasks.delete_item(conn, uid, item)
    added = tasks.add_items(conn, uid, ["one", "two"])["ids"]
    assert min(added) > max(gone)
    assert [i["n"] for i in tasks.get_task(conn, uid)["items"]] == [1, 2, 3]


def test_the_item_limit_counts_the_items_a_task_holds_after_adds_and_deletes(conn):
    uid = _three(conn)
    for _ in range(3):
        item = tasks.add_items(conn, uid, ["extra"])["ids"][0]
        tasks.delete_item(conn, uid, item)
    assert tasks.add_items(conn, uid, [f"step {n}" for n in range(47)])["progress"]["total"] == 50
    with pytest.raises(ValueError):
        tasks.add_items(conn, uid, ["one too many"])


def test_deleting_the_last_open_item_completes_the_task(conn):
    uid = _three(conn)
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "done")
    tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "dropped")
    third = item_at(conn, uid, 3)
    result = tasks.delete_item(conn, uid, third)
    assert result["task_state"] == "completed" and result["archived"] is True
    assert memories.get_memory(conn, uid)["status"] == "archived"
    assert tasks.get_task(conn, uid)["completed_at"] != ""
    assert _notes(conn, uid) == [f"item {third} deleted: write the parser", "completed"]


def test_deleting_the_last_open_item_of_an_all_dropped_task_cancels_it(conn):
    uid = _three(conn)
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "dropped")
    tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "dropped")
    assert tasks.delete_item(conn, uid, item_at(conn, uid, 3))["task_state"] == "cancelled"


def test_deleting_from_a_completed_task_keeps_it_completed(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    stamp = tasks.get_task(conn, uid)["completed_at"]
    result = tasks.delete_item(conn, uid, item_at(conn, uid, 1))
    assert result["task_state"] == "completed" and result["archived"] is True
    assert result["progress"] == {"done": 1, "dropped": 0, "total": 1}
    assert tasks.get_task(conn, uid)["completed_at"] == stamp
    assert _notes(conn, uid).count("completed") == 1


def test_deleting_the_only_done_item_of_a_completed_task_does_not_cancel_it(conn):
    uid = _make(conn)
    tasks.set_item_state(conn, uid, item_at(conn, uid, 1), "done")
    tasks.set_item_state(conn, uid, item_at(conn, uid, 2), "dropped")
    stamp = tasks.get_task(conn, uid)["completed_at"]
    result = tasks.delete_item(conn, uid, item_at(conn, uid, 1))
    assert result["task_state"] == "completed" and result["archived"] is True
    assert result["progress"] == {"done": 0, "dropped": 1, "total": 1}
    assert tasks.get_task(conn, uid)["completed_at"] == stamp
    assert "cancelled" not in _notes(conn, uid)


def test_deleting_from_a_cancelled_task_leaves_it_cancelled(conn):
    uid = _three(conn)
    memories.set_status(conn, uid, "archived")
    result = tasks.delete_item(conn, uid, item_at(conn, uid, 3))
    assert result["task_state"] == "cancelled" and result["archived"] is True


def test_a_refusal_after_the_rows_changed_rolls_them_back(tmp_path, monkeypatch):
    path = tmp_path / "rollback.db"
    with connection.connect(path) as c:
        uid = _three(c)
        note = memories.insert_memory(c, type="note", content="a plain note", domain="acme/parser")
        tasks.link_item(c, uid, item_at(c, uid, 3), [note])
        tasks.add_comment(c, uid, "about the parser", item=item_at(c, uid, 3))

    def refuse(conn, uid, note, **_):
        raise ValueError("refused after the rows changed")
    monkeypatch.setattr(tasks, "_regenerate", refuse)
    with pytest.raises(ValueError), connection.connect(path) as c:
        tasks.delete_item(c, uid, item_at(c, uid, 3))
    with connection.connect(path) as c:
        task = tasks.get_task(c, uid)
        assert [i["n"] for i in task["items"]] == [1, 2, 3]
        assert len(task["items"][2]["links"]) == 1 and len(task["comments"]) == 1
