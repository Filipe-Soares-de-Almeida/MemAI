"""Tasks written the way they read back: short item labels, items cited by id, never by position."""

from __future__ import annotations

import asyncio

import pytest

from conftest import brief, item_at
from memai import contract, portable, sections, server, tasks
from memai.store import connection, memories

ITEMS = ["Draft the grammar", "Write the lexer", "Wire the CLI"]


@pytest.fixture
def conn(tmp_path):
    with connection.connect(tmp_path / "test.db") as c:
        yield c


def _task(conn, **given):
    args = {"title": "Ship the config parser", "goal": "A parser that reads the config file.",
            "items": ITEMS, "domain": "acme/parser", **given}
    return tasks.create_task(conn, **args)


def _fields(**given) -> dict[str, str]:
    values = {"goal": "wire the flags", "context": "the CLI module", "steps": "add each flag",
              "pitfalls": "nothing known", "done_when": "the flags parse", "depends_on": "none"}
    return {**values, **given}


def _published(name: str) -> str:
    [t] = [t for t in asyncio.run(server.mcp.list_tools()) if t.name == name]
    text = [t.description or ""] + [p.get("description", "") for p in t.input_schema["properties"].values()]
    return " ".join(" ".join(text).split())


# ------------------------------------------------------------------ item length

def test_an_item_holds_eighty_characters():
    assert contract.ITEM_MAX == 80


def test_a_long_item_is_refused_with_where_its_detail_goes(conn):
    with pytest.raises(ValueError, match="short label.*brief"):
        _task(conn, items=["x" * 81])


def test_an_added_long_item_is_refused_the_same_way(conn):
    uid = _task(conn)
    with pytest.raises(ValueError, match="short label.*brief"):
        tasks.add_items(conn, uid, ["x" * 81])


# ------------------------------------------------------- item keys in free text

@pytest.mark.parametrize("field, given", [
    ("goal", {"goal": "Finish i2 before the release."}),
    ("title", {"title": "Config parser after i3"}),
    ("item 2", {"items": ["Draft the grammar", "Lex once i1 lands", "Wire the CLI"]}),
])
def test_a_new_task_refuses_an_item_key_in_its_free_text(conn, field, given):
    with pytest.raises(ValueError, match=rf"{field} cites i\d.*cited in free text as \[\[#id\]\]"):
        _task(conn, **given)


def test_a_word_or_a_number_past_the_last_item_is_not_a_key(conn):
    uid = _task(conn, goal="Add i18n to the parser and leave i9 as it is.")
    assert tasks.get_task(conn, uid)["goal"].startswith("Add i18n")


def test_an_added_item_may_not_cite_a_key(conn):
    uid = _task(conn)
    with pytest.raises(ValueError, match="item 4 cites i1"):
        tasks.add_items(conn, uid, ["Ship once i1 lands"])


def test_a_comment_may_not_cite_a_key(conn):
    uid = _task(conn)
    with pytest.raises(ValueError, match="comment cites i2"):
        tasks.add_comment(conn, uid, "blocked on i2", item=item_at(conn, uid, 3))


def test_a_brief_cites_items_by_id_in_depends_on_and_never_by_key(conn):
    uid = _task(conn)
    assert tasks.add_note(conn, uid, title="Lexer", items=[item_at(conn, uid, 2)],
                          body=brief("tokenize the config", depends_on=f"{item_at(conn, uid, 1)} (the grammar)"))
    with pytest.raises(ValueError, match="STEPS cites i1"):
        tasks.add_note(conn, uid, title="CLI", items=[item_at(conn, uid, 3)],
                       body=brief("wire the flags", steps="reuse what i1 built"))


def test_brief_fields_given_one_by_one_are_held_to_the_same_rule(conn):
    uid = _task(conn)
    with pytest.raises(ValueError, match="CONTEXT cites i2"):
        tasks.add_note(conn, uid, title="CLI", items=[item_at(conn, uid, 3)], brief=_fields(context="after i2"))


def test_a_note_on_the_whole_task_may_not_cite_a_key(conn):
    uid = _task(conn)
    with pytest.raises(ValueError, match="body cites i2"):
        tasks.add_note(conn, uid, title="Order of work", body="leave i2 for last", items=[])


def test_a_note_title_may_not_cite_a_key(conn):
    uid = _task(conn)
    with pytest.raises(ValueError, match="title cites i1"):
        tasks.add_note(conn, uid, title="Before i1", body="keep the grammar small", items=[])


def test_an_edit_holds_only_the_fields_it_gives_to_the_rule(conn):
    uid = _task(conn)
    nid = tasks.add_note(conn, uid, title="Lexer", body=brief("tokenize"), items=[item_at(conn, uid, 2)])
    older = sections.without_depends(brief("tokenize", steps="reuse what i1 built"))
    conn.execute("UPDATE task_notes SET body = ? WHERE id = ?", (older, nid))
    tasks.edit_note(conn, uid, nid, brief={"pitfalls": "greedy regexes"})
    with pytest.raises(ValueError, match="PITFALLS cites i1"):
        tasks.edit_note(conn, uid, nid, brief={"pitfalls": "the same as i1"})


def test_a_new_goal_may_not_cite_a_key(conn):
    uid = _task(conn)
    with pytest.raises(ValueError, match="goal cites i3"):
        tasks.set_goal(conn, uid, "Everything but i3.")


def test_the_task_tool_answers_a_cited_key_with_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    out = server.task(title="Ship the config parser", goal="Do i2 first.",
                      items="\n".join(ITEMS), domain="acme/parser")
    assert out["ok"] is False and "goal cites i2" in out["errors"][0]


# ------------------------------------------------- what was written before the rules

def test_a_task_written_before_the_rules_still_imports(tmp_path, conn):
    uid = _task(conn)
    conn.execute("UPDATE task_items SET text = ? WHERE id = ?", ("x" * 120, item_at(conn, uid, 1)))
    conn.execute("UPDATE tasks SET goal = 'Finish i2 first.' WHERE memory_uid = ?", (uid,))
    conn.execute("INSERT INTO task_comments (memory_uid, item_id, body, author, session, created_at) "
                 "VALUES (?, ?, 'waits on i2', 'agent', '', '2026-01-01T00:00:00+00:00')",
                 (uid, item_at(conn, uid, 3)))
    records = portable.export_records(conn, uids=[uid])
    with connection.connect(tmp_path / "copy.db") as target:
        assert portable.import_records(target, records)["errors"] == []
        copied = tasks.get_task(target, uid)
        assert copied["items"][0]["text"] == "x" * 120 and copied["goal"] == "Finish i2 first."


def test_task_adopt_refuses_a_memory_that_cites_a_key_outside_depends_on(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with connection.connect() as c:
        uid = _task(c)
        older = memories.insert_memory(c, type="note", title="Lexer", domain="acme/parser",
                                       content=brief("tokenize", steps="after i1"))
        tasks.link_item(c, uid, item_at(c, uid, 2), [older])
    out = portable.adopt(uid, [older])
    assert out["plan"] == [] and "cites i1" in out["refused"][0]["reason"]


# ------------------------------------------------------------ what the tools say

def test_task_says_the_title_and_the_items_are_short():
    text = _published("task")
    assert "few words" in text and "short label" in text and "brief" in text
    assert f"at most {contract.ITEM_MAX} characters" in text


@pytest.mark.parametrize("name", ["task", "task_add", "task_note", "task_item", "task_comment"])
def test_a_task_tool_says_items_are_cited_by_key_only_in_depends_on(name):
    text = _published(name)
    assert "DEPENDS ON" in text and "what it does" in text
