import pytest

from memai import db, tasks


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
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
    assert db.get_memory(conn, uid)["type"] == "task"
    task = tasks.get_task(conn, uid)
    assert task["state"] == "open"
    assert task["goal"] == "Parse every config file"
    assert [i["key"] for i in task["items"]] == ["i1", "i2"]
    assert [i["state"] for i in task["items"]] == ["todo", "todo"]
    assert [i["seq"] for i in task["items"]] == [1, 2]
    assert task["comments"] == []


def test_the_content_is_rendered_from_goal_and_items(conn):
    uid = _make(conn)
    assert db.get_memory(conn, uid)["content"] == (
        "GOAL: Parse every config file\n[ ] i1 read the spec\n[ ] i2 write the lexer"
    )


def test_render_uses_one_mark_per_state():
    items = [
        {"key": "i1", "text": "a", "state": "todo"},
        {"key": "i2", "text": "b", "state": "doing"},
        {"key": "i3", "text": "c", "state": "done"},
        {"key": "i4", "text": "d", "state": "dropped"},
    ]
    assert tasks.render("g", items) == "GOAL: g\n[ ] i1 a\n[~] i2 b\n[x] i3 c\n[-] i4 d"


def test_the_content_is_searchable(conn):
    uid = _make(conn)
    assert uid in [r["uid"] for r in db.search_ranked(conn, "lexer")]


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


def test_get_task_of_another_type_is_none(conn):
    uid = db.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
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


def test_marking_an_item_regenerates_the_content_with_an_audited_note(conn):
    uid = _make(conn)
    result = tasks.set_item_state(conn, uid, "1", "doing")
    assert result["item"] == "i1" and result["state"] == "doing" and result["changed"] is True
    assert result["progress"] == {"done": 0, "dropped": 0, "total": 2}
    assert result["task_state"] == "open" and result["archived"] is False
    assert db.get_memory(conn, uid)["content"].splitlines()[1] == "[~] i1 read the spec"
    assert _notes(conn, uid)[-1] == "item i1: todo -> doing"


def test_the_last_item_done_completes_and_archives_the_task(conn):
    uid = _make(conn)
    result = _close_both(conn, uid)
    assert result["task_state"] == "completed" and result["archived"] is True
    assert db.get_memory(conn, uid)["status"] == "archived"
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
    assert db.get_memory(conn, uid)["status"] == "archived"
    assert _notes(conn, uid)[-1] == "cancelled"


def test_reopening_an_item_reopens_the_task(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    result = tasks.set_item_state(conn, uid, "i1", "todo")
    assert result["task_state"] == "open" and result["archived"] is False
    assert db.get_memory(conn, uid)["status"] == "active"
    assert tasks.get_task(conn, uid)["completed_at"] == ""
    assert _notes(conn, uid)[-1] == "reopened"


def test_adding_an_item_reopens_a_completed_task(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    result = tasks.add_items(conn, uid, ["ship it"])
    assert result["keys"] == ["i3"] and result["task_state"] == "open"
    assert result["archived"] is False and result["progress"]["total"] == 3
    assert db.get_memory(conn, uid)["status"] == "active"
    assert db.get_memory(conn, uid)["content"].splitlines()[-1] == "[ ] i3 ship it"
    notes = _notes(conn, uid)
    assert "item i3 added" in notes and notes[-1] == "reopened"


def test_keys_are_never_reused(conn):
    uid = _make(conn)
    assert tasks.add_items(conn, uid, ["third"])["keys"] == ["i3"]
    assert tasks.add_items(conn, uid, ["fourth"])["keys"] == ["i4"]
    assert tasks.add_items(conn, uid, ["fifth", "sixth"])["keys"] == ["i5", "i6"]
    assert "items i5, i6 added" in _notes(conn, uid)


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
    with db.connect(path) as first:
        uid = _make(first)
    with db.connect(path) as a:
        tasks.set_item_state(a, uid, "i1", "done")
    with db.connect(path) as b:
        result = tasks.set_item_state(b, uid, "i2", "done")
    assert result["task_state"] == "completed"
    with db.connect(path) as c:
        assert _notes(c, uid).count("completed") == 1


def test_a_write_reads_the_other_connections_commit(tmp_path):
    path = tmp_path / "shared.db"
    with db.connect(path) as a:
        uid = _make(a)
        a.commit()
        with db.connect(path) as b:
            b.commit()
            tasks.set_item_state(a, uid, "i1", "done")
            a.commit()
            result = tasks.set_item_state(b, uid, "i2", "done")
            b.commit()
            assert result["task_state"] == "completed"
            assert _notes(a, uid).count("completed") == 1


def test_archiving_an_open_task_by_hand_cancels_it(conn):
    uid = _make(conn)
    db.set_status(conn, uid, "archived")
    assert tasks.get_task(conn, uid)["state"] == "cancelled"


def test_archiving_a_completed_task_keeps_it_completed(conn):
    uid = _make(conn)
    _close_both(conn, uid)
    db.set_status(conn, uid, "archived")
    assert tasks.get_task(conn, uid)["state"] == "completed"


def test_restoring_a_task_reopens_it(conn):
    uid = _make(conn)
    db.set_status(conn, uid, "archived")
    db.set_status(conn, uid, "active")
    task = tasks.get_task(conn, uid)
    assert task["state"] == "open" and task["completed_at"] == ""
    assert [i["state"] for i in task["items"]] == ["todo", "todo"]


def test_set_status_leaves_other_types_alone(conn):
    uid = db.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    db.set_status(conn, uid, "archived")
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_goal_edit_regenerates_content(conn):
    uid = _make(conn)
    tasks.set_goal(conn, uid, "Parse every file")
    assert db.get_memory(conn, uid)["content"].splitlines()[0] == "GOAL: Parse every file"
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
    content = db.get_memory(conn, uid)["content"]
    first = tasks.add_comment(conn, uid, "starting here", item="1")
    second = tasks.add_comment(conn, uid, "overall note")
    assert isinstance(first, int) and second > first
    assert db.get_memory(conn, uid)["content"] == content
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
    note = db.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
    with pytest.raises(ValueError):
        tasks.link_item(conn, uid, "i1", [note, "ffffffffffffffff"])
    assert conn.execute("SELECT COUNT(*) FROM task_item_links").fetchone()[0] == 0


def test_link_and_unlink(conn):
    uid = _make(conn)
    note = db.insert_memory(conn, type="note", content="a plain note", title="Lexer notes", domain="acme/parser")
    assert tasks.link_item(conn, uid, "i1", [note]) == [note]
    links = tasks.get_task(conn, uid)["items"][0]["links"]
    assert links == [{"uid": note, "title": "Lexer notes"}]
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
    note = db.insert_memory(conn, type="note", content="a plain note", domain="acme/parser")
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
