import pytest

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
    assert [i["key"] for i in task["items"]] == ["i1", "i2"]
    assert [i["state"] for i in task["items"]] == ["todo", "todo"]
    assert [i["seq"] for i in task["items"]] == [1, 2]
    assert task["comments"] == []


def test_the_content_is_rendered_from_goal_and_items(conn):
    uid = _make(conn)
    assert memories.get_memory(conn, uid)["content"] == (
        "GOAL: Parse every config file\n[ ] i1 read the spec\n[ ] i2 write the lexer"
    )


def test_render_uses_one_mark_per_state():
    items = [
        {"key": "i1", "text": "a", "state": "todo"},
        {"key": "i2", "text": "b", "state": "doing"},
        {"key": "i3", "text": "c", "state": "done"},
        {"key": "i4", "text": "d", "state": "dropped"},
    ]
    assert task_items.render("g", items) == "GOAL: g\n[ ] i1 a\n[~] i2 b\n[x] i3 c\n[-] i4 d"


def test_the_content_is_searchable(conn):
    uid = _make(conn)
    assert uid in [r["uid"] for r in search.search_ranked(conn, "lexer")]


def test_item_keys_accept_numbers_and_prefixes():
    assert tasks.item_key("3") == tasks.item_key("i3") == tasks.item_key(" I3 ") == "i3"
    for bad in ("x", "0", ""):
        with pytest.raises(ValueError):
            tasks.item_key(bad)


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
        items=["x" * 300] + [f"step {n}" for n in range(49)],
    )
    task = tasks.get_task(conn, uid)
    assert len(task["goal"]) == 2000
    assert len(task["items"]) == 50 and len(task["items"][0]["text"]) == 300


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


def test_add_items_accepts_up_to_fifty_items_and_an_item_of_exactly_three_hundred(conn):
    uid = _make(conn)
    keys = tasks.add_items(conn, uid, ["x" * 300] + [f"step {n}" for n in range(47)])["keys"]
    assert len(keys) == 48 and len(tasks.get_task(conn, uid)["items"]) == 50
    with pytest.raises(ValueError):
        tasks.add_items(conn, uid, ["one too many"])
    with pytest.raises(ValueError):
        tasks.add_items(conn, uid, ["x" * 301])
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
    tasks.set_item_state(conn, uid, "i1", "done")
    return tasks.set_item_state(conn, uid, "i2", "done")


def test_marking_an_item_regenerates_the_content_without_an_edit(conn):
    uid = _make(conn)
    result = tasks.set_item_state(conn, uid, "1", "doing")
    assert result["item"] == "i1" and result["state"] == "doing" and result["changed"] is True
    assert result["progress"] == {"done": 0, "dropped": 0, "total": 2}
    assert result["task_state"] == "open" and result["archived"] is False
    assert memories.get_memory(conn, uid)["content"].splitlines()[1] == "[~] i1 read the spec"
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
    tasks.set_item_state(conn, uid, "i1", "done")
    result = tasks.set_item_state(conn, uid, "i2", "dropped")
    assert result["task_state"] == "completed"
    assert result["progress"] == {"done": 1, "dropped": 1, "total": 2}


def test_all_dropped_cancels(conn):
    uid = _make(conn)
    tasks.set_item_state(conn, uid, "i1", "dropped")
    result = tasks.set_item_state(conn, uid, "i2", "dropped")
    assert result["task_state"] == "cancelled" and result["archived"] is True
    assert memories.get_memory(conn, uid)["status"] == "archived"
    assert _notes(conn, uid)[-1] == "cancelled"


def test_reopening_an_item_reopens_the_task(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    result = tasks.set_item_state(conn, uid, "i1", "todo")
    assert result["task_state"] == "open" and result["archived"] is False
    assert memories.get_memory(conn, uid)["status"] == "active"
    assert tasks.get_task(conn, uid)["completed_at"] == ""
    assert _notes(conn, uid)[-1] == "reopened"


def test_adding_an_item_reopens_a_completed_task(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    result = tasks.add_items(conn, uid, ["ship it"])
    assert result["keys"] == ["i3"] and result["task_state"] == "open"
    assert result["archived"] is False and result["progress"]["total"] == 3
    assert memories.get_memory(conn, uid)["status"] == "active"
    assert memories.get_memory(conn, uid)["content"].splitlines()[-1] == "[ ] i3 ship it"
    notes = _notes(conn, uid)
    assert "item i3 added" not in notes and notes[-1] == "reopened"


def test_consecutive_adds_take_consecutive_positions(conn):
    uid = _make(conn)
    assert tasks.add_items(conn, uid, ["third"])["keys"] == ["i3"]
    assert tasks.add_items(conn, uid, ["fourth"])["keys"] == ["i4"]
    assert tasks.add_items(conn, uid, ["fifth", "sixth"])["keys"] == ["i5", "i6"]
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
    assert tasks.set_item_state(conn, uid, "i1", "done")["changed"] is True
    before = _edit_count(conn, uid)
    again = tasks.set_item_state(conn, uid, "i1", "done")
    assert again["changed"] is False and again["state"] == "done"
    assert _edit_count(conn, uid) == before


def test_two_connections_close_the_last_items_once(tmp_path):
    path = tmp_path / "shared.db"
    with connection.connect(path) as first:
        uid = _make(first)
    with connection.connect(path) as a:
        tasks.set_item_state(a, uid, "i1", "done")
    with connection.connect(path) as b:
        result = tasks.set_item_state(b, uid, "i2", "done")
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
            tasks.set_item_state(a, uid, "i1", "done")
            a.commit()
            result = tasks.set_item_state(b, uid, "i2", "done")
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
    result = tasks.set_item_state(conn, uid, "i2", "todo")
    assert result["task_state"] == "open" and result["archived"] is False
    assert tasks.get_task(conn, uid)["completed_at"] == ""


def test_restoring_a_cancelled_task_with_every_item_dropped_keeps_it_cancelled(conn):
    uid = _make(conn)
    tasks.set_item_state(conn, uid, "i1", "dropped")
    tasks.set_item_state(conn, uid, "i2", "dropped")
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
    first = tasks.add_comment(conn, uid, "starting here", item="1")
    second = tasks.add_comment(conn, uid, "overall note")
    assert isinstance(first, int) and second > first
    assert memories.get_memory(conn, uid)["content"] == content
    comments = tasks.get_task(conn, uid)["comments"]
    assert [c["item"] for c in comments] == ["i1", ""]
    assert [c["author"] for c in comments] == ["agent", "agent"]
    assert tasks.add_comment(conn, uid, "seen it", author="person") > second
    for bad in ("x" * 2001, "", "   "):
        with pytest.raises(ValueError):
            tasks.add_comment(conn, uid, bad)
    with pytest.raises(ValueError):
        tasks.add_comment(conn, uid, "ok", author="robot")
    with pytest.raises(ValueError):
        tasks.add_comment(conn, uid, "ok", item="i9")
    assert len(tasks.get_task(conn, uid)["comments"]) == 3


def test_links_to_unknown_memories_are_refused_whole(conn):
    uid = _make(conn)
    note = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    with pytest.raises(ValueError):
        tasks.link_item(conn, uid, "i1", [note, "ffffffffffffffff"])
    assert conn.execute("SELECT COUNT(*) FROM task_item_links").fetchone()[0] == 0


def test_link_and_unlink(conn):
    uid = _make(conn)
    note = memories.insert_memory(conn, type="note", content="a plain note", title="Lexer notes", domain="acme/parser")
    assert tasks.link_item(conn, uid, "i1", [note]) == [note]
    links = tasks.get_task(conn, uid)["items"][0]["links"]
    assert links == [{"uid": note, "title": "Lexer notes", "type": "note"}]
    assert tasks.link_item(conn, uid, "i1", [note]) == []
    assert tasks.unlink_item(conn, uid, "i1", note) is True
    assert tasks.get_task(conn, uid)["items"][0]["links"] == []
    assert tasks.unlink_item(conn, uid, "i1", note) is False


def test_unknown_item_and_state_are_refused(conn):
    uid = _make(conn)
    before = _edit_count(conn, uid)
    with pytest.raises(ValueError):
        tasks.set_item_state(conn, uid, "i9", "done")
    with pytest.raises(ValueError):
        tasks.set_item_state(conn, uid, "i1", "finished")
    with pytest.raises(ValueError):
        tasks.link_item(conn, uid, "i9", [])
    with pytest.raises(ValueError):
        tasks.unlink_item(conn, uid, "i9", "x")
    assert _edit_count(conn, uid) == before


def test_a_uid_that_is_not_a_task_is_refused(conn):
    note = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    calls = [
        lambda u: tasks.set_item_state(conn, u, "i1", "done"),
        lambda u: tasks.add_items(conn, u, ["x"]),
        lambda u: tasks.set_goal(conn, u, "g"),
        lambda u: tasks.add_comment(conn, u, "c"),
        lambda u: tasks.link_item(conn, u, "i1", [note]),
        lambda u: tasks.unlink_item(conn, u, "i1", note),
        lambda u: tasks.progress(conn, u),
    ]
    for call in calls:
        for uid in (note, "ffffffffffffffff"):
            with pytest.raises(ValueError, match="no task"):
                call(uid)


def test_progress_counts_done_and_dropped(conn):
    uid = _make(conn)
    assert tasks.progress(conn, uid) == {"done": 0, "dropped": 0, "total": 2}
    tasks.set_item_state(conn, uid, "i1", "done")
    assert tasks.progress(conn, uid) == {"done": 1, "dropped": 0, "total": 2}


# ------------------------------------------------- purge, portability, exclusions

def _full_task(conn, title="Ship the parser"):
    """A task with a link, a task-level comment and an item comment."""
    note = memories.insert_memory(conn, type="note", content="the lexer reads one token", domain="acme/parser")
    uid = tasks.create_task(
        conn, title=title, goal="Parse every config file",
        items=["read the spec", "write the lexer"], domain="acme/parser", tags="parser, lexer",
    )
    tasks.link_item(conn, uid, "i2", [note])
    tasks.add_comment(conn, uid, "start with the spec", author="person")
    tasks.add_comment(conn, uid, "lexer drafted", item="i2", author="agent", session="s1")
    tasks.set_item_state(conn, uid, "i1", "done")
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
        "items": [(i["key"], i["text"], i["state"], [link["uid"] for link in i["links"]])
                  for i in task["items"]],
        "comments": [(c["body"], c["author"], c["item"]) for c in task["comments"]],
    }


def test_a_task_round_trips_through_export_and_import(tmp_path):
    from memai import portable

    with connection.connect(tmp_path / "a.db") as a:
        uid, note = _full_task(a)
        tasks.set_item_state(a, uid, "i2", "done")
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


def test_an_export_after_a_delete_restores_the_positions(tmp_path):
    from memai import portable

    with connection.connect(tmp_path / "a.db") as a:
        uid = _three(a)
        tasks.delete_item(a, uid, "i3")
        records = list(portable.export_records(a, include_archived=True, include_edits=True))
    with connection.connect(tmp_path / "b.db") as b:
        assert portable.import_records(b, records)["errors"] == []
        assert tasks.add_items(b, uid, ["a new step"])["keys"] == ["i3"]


def _bare_task_record(**extra):
    return {
        "record": "task", "uid": "aaaaaaaaaaaaaaaa", "goal": "g", "state": "open",
        "completed_at": "",
        "items": [{"key": f"i{n}", "seq": n, "text": f"step {n}", "state": "todo",
                   "updated_at": "2026-01-01T00:00:00+00:00", "updated_session": ""}
                  for n in (1, 2, 5)],
        "links": [], "comments": [], **extra,
    }


def _restore_bare(conn, **extra):
    memories.restore_memory(conn, {"record": "memory", "uid": "aaaaaaaaaaaaaaaa", "type": "task",
                             "content": "GOAL: g", "domain": "acme/parser"})
    tasks.restore_task(conn, _bare_task_record(**extra))


def test_a_record_with_gaps_restores_its_items_by_position(conn):
    _restore_bare(conn)
    uid = "aaaaaaaaaaaaaaaa"
    assert [i["key"] for i in tasks.get_task(conn, uid)["items"]] == ["i1", "i2", "i3"]
    assert tasks.add_items(conn, uid, ["next"])["keys"] == ["i4"]


def test_a_record_item_seq_numbers_nothing(conn):
    _restore_bare(conn, item_seq=9)
    assert tasks.add_items(conn, "aaaaaaaaaaaaaaaa", ["next"])["keys"] == ["i4"]


@pytest.mark.parametrize("bad", [-1, "3", 2.5, True, None], ids=repr)
def test_a_bad_item_seq_is_refused_and_leaves_no_rows(conn, bad):
    with pytest.raises(ValueError, match="item_seq"):
        _restore_bare(conn, item_seq=bad)
    for table in ("tasks", "task_items", "task_item_links", "task_comments"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_an_import_reports_a_bad_item_seq_and_keeps_the_memory(tmp_path):
    from memai import portable

    with connection.connect(tmp_path / "a.db") as a:
        uid = _three(a)
        records = list(portable.export_records(a, include_archived=True))
    for r in records:
        if r["record"] == "task":
            r["item_seq"] = -4
    with connection.connect(tmp_path / "b.db") as b:
        result = portable.import_records(b, records)
        assert [e["uid"] for e in result["errors"]] == [uid]
        assert "item_seq" in result["errors"][0]["error"]
        assert memories.get_memory(b, uid) is not None
        assert b.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert b.execute("SELECT COUNT(*) FROM task_items").fetchone()[0] == 0


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
        "items": [{"key": "i1", "seq": 1, "text": "step", "state": "todo",
                   "updated_at": "2026-01-01T00:00:00+00:00", "updated_session": ""}],
        "links": [{"item_key": "i1", "target_uid": note, "created_at": "2026-01-01T00:00:00+00:00"},
                  {"item_key": "i1", "target_uid": "bbbbbbbbbbbbbbbb",
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
    tasks.set_item_state(conn, two, "i1", "done")
    assert memories.get_memory(conn, one)["content"] == memories.get_memory(conn, two)["content"]
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
        tasks.add_comment(conn, uid, LEAK, item="i1")
    assert tasks.get_task(conn, uid)["comments"] == []


# ------------------------------------------------------- import validates states

def _record(**over) -> dict:
    record = {
        "record": "task", "uid": "aaaaaaaaaaaaaaaa", "goal": "g", "state": "open",
        "completed_at": "",
        "items": [{"key": "i1", "seq": 1, "text": "step", "state": "todo",
                   "updated_at": "2026-01-01T00:00:00+00:00", "updated_session": ""}],
        "links": [], "comments": [],
    }
    return {**record, **over}


def _memory_record() -> dict:
    return {"record": "memory", "uid": "aaaaaaaaaaaaaaaa", "type": "task",
            "content": "GOAL: g\n[ ] i1 step", "domain": "acme/parser", "title": "A task"}


def _bad_item(state: str) -> dict:
    item = _record()["items"][0]
    return _record(items=[{**item, "key": "i2", "seq": 2, "state": state}, item])


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
        conn, [_memory_record(), _record(items=[item, {**item, "seq": 2}])])
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
    tasks.link_item(conn, uid, "i2", [note])
    tasks.link_item(conn, uid, "i3", [note])
    tasks.add_comment(conn, uid, "about the lexer", item="i2")
    tasks.add_comment(conn, uid, "about the parser", item="i3")
    tasks.add_comment(conn, uid, "about the whole task")
    result = tasks.delete_item(conn, uid, "2")
    assert result["uid"] == uid and result["item"] == "i2"
    assert result["progress"] == {"done": 0, "dropped": 0, "total": 2}
    assert result["task_state"] == "open" and result["archived"] is False
    task = tasks.get_task(conn, uid)
    assert result["renumbered"] == {"i3": "i2"}
    assert [i["key"] for i in task["items"]] == ["i1", "i2"]
    assert [link["uid"] for i in task["items"] for link in i["links"]] == [note]
    assert [c["body"] for c in task["comments"]] == ["about the parser", "about the whole task"]
    assert memories.get_memory(conn, uid)["content"] == (
        "GOAL: Parse every config file\n[ ] i1 read the spec\n[ ] i2 write the parser")
    assert _notes(conn, uid) == ["item i2 deleted: write the lexer; i3 renumbered to i2"]


def test_the_only_item_cannot_be_deleted(conn):
    uid = tasks.create_task(conn, title="One step", goal="Do it", items=["the only step"])
    before = _edit_count(conn, uid)
    with pytest.raises(ValueError, match="a task keeps at least one item"):
        tasks.delete_item(conn, uid, "i1")
    assert _edit_count(conn, uid) == before
    assert len(tasks.get_task(conn, uid)["items"]) == 1


def test_delete_item_refuses_an_unknown_item_and_a_non_task(conn):
    uid = _three(conn)
    before = _edit_count(conn, uid)
    with pytest.raises(ValueError, match="i9"):
        tasks.delete_item(conn, uid, "i9")
    with pytest.raises(ValueError, match="not an item key"):
        tasks.delete_item(conn, uid, "x")
    note = memories.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    with pytest.raises(ValueError, match="no task"):
        tasks.delete_item(conn, note, "i1")
    assert _edit_count(conn, uid) == before
    assert len(tasks.get_task(conn, uid)["items"]) == 3


def test_the_next_add_takes_the_position_a_deleted_last_item_left(conn):
    uid = _three(conn)
    tasks.delete_item(conn, uid, "i3")
    assert tasks.add_items(conn, uid, ["a new step"])["keys"] == ["i3"]
    tasks.delete_item(conn, uid, "i3")
    tasks.delete_item(conn, uid, "i2")
    assert tasks.add_items(conn, uid, ["one", "two"])["keys"] == ["i2", "i3"]
    assert [i["key"] for i in tasks.get_task(conn, uid)["items"]] == ["i1", "i2", "i3"]


def test_the_item_limit_counts_the_items_a_task_holds_after_adds_and_deletes(conn):
    uid = _three(conn)
    for _ in range(3):
        key = tasks.add_items(conn, uid, ["extra"])["keys"][0]
        tasks.delete_item(conn, uid, key)
    assert tasks.add_items(conn, uid, [f"step {n}" for n in range(47)])["progress"]["total"] == 50
    with pytest.raises(ValueError):
        tasks.add_items(conn, uid, ["one too many"])


def test_a_store_without_the_mark_column_gets_it_and_keeps_its_keys(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    with connection.connect(path) as c:
        uid = _three(c)
        c.commit()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE tasks DROP COLUMN item_seq")
    raw.commit()
    raw.close()
    with connection.connect(path) as c:
        assert "item_seq" in {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}
        assert tasks.add_items(c, uid, ["next"])["keys"] == ["i4"]
    with connection.connect(path) as c:
        assert "item_seq" in {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}


def test_deleting_the_last_open_item_completes_the_task(conn):
    uid = _three(conn)
    tasks.set_item_state(conn, uid, "i1", "done")
    tasks.set_item_state(conn, uid, "i2", "dropped")
    result = tasks.delete_item(conn, uid, "i3")
    assert result["task_state"] == "completed" and result["archived"] is True
    assert memories.get_memory(conn, uid)["status"] == "archived"
    assert tasks.get_task(conn, uid)["completed_at"] != ""
    assert _notes(conn, uid) == ["item i3 deleted: write the parser", "completed"]


def test_deleting_the_last_open_item_of_an_all_dropped_task_cancels_it(conn):
    uid = _three(conn)
    tasks.set_item_state(conn, uid, "i1", "dropped")
    tasks.set_item_state(conn, uid, "i2", "dropped")
    assert tasks.delete_item(conn, uid, "i3")["task_state"] == "cancelled"


def test_deleting_from_a_completed_task_keeps_it_completed(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    stamp = tasks.get_task(conn, uid)["completed_at"]
    result = tasks.delete_item(conn, uid, "i1")
    assert result["task_state"] == "completed" and result["archived"] is True
    assert result["progress"] == {"done": 1, "dropped": 0, "total": 1}
    assert tasks.get_task(conn, uid)["completed_at"] == stamp
    assert _notes(conn, uid).count("completed") == 1


def test_deleting_the_only_done_item_of_a_completed_task_does_not_cancel_it(conn):
    uid = _make(conn)
    tasks.set_item_state(conn, uid, "i1", "done")
    tasks.set_item_state(conn, uid, "i2", "dropped")
    stamp = tasks.get_task(conn, uid)["completed_at"]
    result = tasks.delete_item(conn, uid, "i1")
    assert result["task_state"] == "completed" and result["archived"] is True
    assert result["progress"] == {"done": 0, "dropped": 1, "total": 1}
    assert tasks.get_task(conn, uid)["completed_at"] == stamp
    assert "cancelled" not in _notes(conn, uid)


def test_deleting_from_a_cancelled_task_leaves_it_cancelled(conn):
    uid = _three(conn)
    memories.set_status(conn, uid, "archived")
    result = tasks.delete_item(conn, uid, "i3")
    assert result["task_state"] == "cancelled" and result["archived"] is True


def test_a_refusal_after_the_rows_changed_rolls_them_back(tmp_path, monkeypatch):
    path = tmp_path / "rollback.db"
    with connection.connect(path) as c:
        uid = _three(c)
        note = memories.insert_memory(c, type="note", content="a plain note", domain="acme/parser")
        tasks.link_item(c, uid, "i3", [note])
        tasks.add_comment(c, uid, "about the parser", item="i3")

    def refuse(conn, uid, note, **_):
        raise ValueError("refused after the rows changed")
    monkeypatch.setattr(tasks, "_regenerate", refuse)
    with pytest.raises(ValueError), connection.connect(path) as c:
        tasks.delete_item(c, uid, "i3")
    with connection.connect(path) as c:
        task = tasks.get_task(c, uid)
        assert [i["key"] for i in task["items"]] == ["i1", "i2", "i3"]
        assert len(task["items"][2]["links"]) == 1 and len(task["comments"]) == 1
        assert c.execute("SELECT item_seq FROM tasks WHERE memory_uid = ?", (uid,)).fetchone()[0] == 0
