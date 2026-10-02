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
