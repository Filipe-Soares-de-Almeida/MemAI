"""The one line a memory is named by.

Every writing tool requires a title, the column is indexed above the body,
and a store written before the column gains it on the next connect. What a
reader falls back to when a row has none -- the opening line of the body --
lives in the dashboard, so it is not covered here.
"""

from __future__ import annotations

import sqlite3

import pytest
from starlette.testclient import TestClient

from memai import admin, db, guard, server


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(store):
    with TestClient(admin.app) as c:
        yield c


# ------------------------------------------------------- what the tools take

WRITERS = ("note", "checkpoint", "anti_pattern", "reasoning", "handoff")


@pytest.mark.parametrize("tool", WRITERS)
def test_every_writer_requires_a_title(tool):
    assert "title" in guard.GUARDED[tool]


@pytest.mark.parametrize("tool", WRITERS)
def test_a_write_without_one_is_refused_before_it_reaches_the_store(tool):
    missing, _, _ = guard.check(tool, {"title": "  "})
    assert "title" in missing


def test_the_title_a_writer_gave_is_what_comes_back(store):
    uid = server.note(title="How the drain step retries",
                      content="it retries twice, then gives up")["uid"]
    assert server.get_memory(uid)["title"] == "How the drain step retries"


def test_surrounding_space_is_not_part_of_the_name(conn):
    uid = db.insert_memory(conn, type="note", title="  padded  ", content="a fact")
    assert db.get_memory(conn, uid)["title"] == "padded"


# ---------------------------------------------------------- what it is worth

def test_a_word_only_in_the_title_finds_the_memory(conn):
    uid = db.insert_memory(conn, type="note", title="Nightly export window",
                           content="it runs while the queue is idle")
    assert [r["uid"] for r in db.search_memories(conn, "export")] == [uid]


def test_a_title_match_outranks_the_same_word_in_a_body(conn):
    mentioned = db.insert_memory(
        conn, type="note", title="How the loader reads a part",
        content="a part is rejected when the checksum of the export disagrees")
    named = db.insert_memory(
        conn, type="note", title="The export window",
        content="both ends are inclusive")
    assert [r["uid"] for r in db.search_memories(conn, "export")][0] == named
    assert mentioned in [r["uid"] for r in db.search_memories(conn, "export")]


# --------------------------------------------------------- diagrams and both

def test_a_diagrams_title_is_its_memorys_title(conn):
    uid, errors = db.insert_diagram(
        conn, title="Nightly export routine",
        nodes=[{"key": "a", "label": "Start", "shape": "start"},
               {"key": "b", "label": "Done", "shape": "end"}],
        edges=[{"from": "a", "to": "b"}])
    assert errors == []
    assert db.get_memory(conn, uid)["title"] == "Nightly export routine"


def test_renaming_a_diagram_renames_the_memory(conn):
    uid, _ = db.insert_diagram(
        conn, title="Nightly export routine",
        nodes=[{"key": "a", "label": "Start", "shape": "start"},
               {"key": "b", "label": "Done", "shape": "end"}],
        edges=[{"from": "a", "to": "b"}])
    ok, errors = db.set_diagram_meta(conn, uid, title="Hourly export routine")
    assert (ok, errors) == (True, [])
    assert db.get_memory(conn, uid)["title"] == "Hourly export routine"


# ----------------------------------------------------------- the dashboard

def test_the_dashboard_refuses_a_memory_with_no_title(client):
    res = client.post("/api/memories", json={"type": "note", "content": "a fact"})
    assert res.status_code == 400
    assert "title is required" in res.text


def test_the_dashboard_renames_through_the_meta_endpoint(client):
    uid = client.post("/api/memories", json={
        "title": "The export window", "type": "note",
        "content": "both ends are inclusive"}).json()["uid"]
    res = client.post(f"/api/memories/{uid}/meta", json={"title": "Export window bounds"})
    assert res.status_code == 200 and "title" in res.json()["changed"]
    assert client.get(f"/api/memories/{uid}").json()["title"] == "Export window bounds"


def test_a_rename_to_nothing_is_refused(client):
    uid = client.post("/api/memories", json={
        "title": "The export window", "type": "note",
        "content": "both ends are inclusive"}).json()["uid"]
    res = client.post(f"/api/memories/{uid}/meta", json={"title": "   "})
    assert res.status_code == 400
    assert "title cannot be empty" in res.text


# ------------------------------------------------------- a store without one

def _store_without_the_column(path) -> None:
    """A store whose schema has no `title`: no column, and an index of the
    four fields such a store has to be rebuilt from."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE memories (
            rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
            domain TEXT NOT NULL DEFAULT '', also_domains TEXT NOT NULL DEFAULT '',
            session TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
            confidence TEXT NOT NULL DEFAULT 'unverified', superseded_by TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE VIRTUAL TABLE memories_fts USING fts5(
            content, tags, domain, also_domains,
            content='memories', content_rowid='rowid_pk',
            tokenize='porter unicode61');
        INSERT INTO memories (uid, type, content, created_at, updated_at)
        VALUES ('0123456789abcdef', 'note', 'the queue drain retries twice',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        INSERT INTO memories_fts(rowid, content, tags, domain, also_domains)
        SELECT rowid_pk, content, tags, domain, also_domains FROM memories;
    """)
    conn.commit()
    conn.close()


def test_a_store_written_before_the_column_gains_it(tmp_path):
    path = tmp_path / "old.db"
    _store_without_the_column(path)
    with db.connect(path) as conn:
        row = db.get_memory(conn, "0123456789abcdef")
        assert row["title"] == ""
        # the index was rebuilt around the new column, so the old row is
        # still findable and a new one is findable by its title alone
        assert [r["uid"] for r in db.search_memories(conn, "drain")] == ["0123456789abcdef"]
        fresh = db.insert_memory(conn, type="note", title="Export window bounds",
                                 content="both ends are inclusive")
        assert [r["uid"] for r in db.search_memories(conn, "export")] == [fresh]


def test_an_edit_keeps_the_index_in_step_with_the_title(conn):
    uid = db.insert_memory(conn, type="note", title="The export window",
                           content="both ends are inclusive")
    conn.execute("UPDATE memories SET title = ? WHERE uid = ?", ("The drain step", uid))
    assert db.search_memories(conn, "export") == []
    assert [r["uid"] for r in db.search_memories(conn, "drain")] == [uid]


# --------------------------------------------------------------- renaming it

def test_a_rename_is_audited_and_reindexed(conn):
    uid = db.insert_memory(conn, type="note", title="The export window",
                           content="both ends are inclusive")
    assert db.set_title(conn, uid, "Export window bounds", note="clearer name")
    row = db.get_memory(conn, uid)
    assert row["title"] == "Export window bounds"
    assert [r["uid"] for r in db.search_memories(conn, "bounds")] == [uid]
    entry = db.get_edit_history(conn, uid)[0]
    assert "title 'The export window' -> 'Export window bounds'" in entry["note"]
    assert "clearer name" in entry["note"]
    # a rename is not a rewrite: the body is what it was
    assert entry["prev_content"] == entry["new_content"] == "both ends are inclusive"


def test_a_rename_to_nothing_leaves_the_name_alone(conn):
    uid = db.insert_memory(conn, type="note", title="The export window",
                           content="both ends are inclusive")
    assert db.set_title(conn, uid, "   ") is False
    assert db.get_memory(conn, uid)["title"] == "The export window"


def test_renaming_something_that_is_not_there(conn):
    assert db.set_title(conn, "0000000000000000", "a name") is False


def test_the_tool_renames_a_memory(store):
    uid = server.note(title="The export window",
                      content="both ends are inclusive")["uid"]
    assert server.edit_memory(uid, title="Export window bounds") == {
        "ok": True, "changed": ["title"]}
    assert server.get_memory(uid)["title"] == "Export window bounds"


def test_the_tool_asks_for_something_to_change(store):
    uid = server.note(title="The export window", content="both ends are inclusive")["uid"]
    res = server.edit_memory(uid)
    assert res["ok"] is False and "title" in res["errors"][0]


def test_the_tool_refuses_to_rename_a_diagram(store):
    uid = server.diagram(title="Nightly export routine",
                         nodes=[{"key": "a", "label": "Start", "shape": "start"},
                                {"key": "b", "label": "Done", "shape": "end"}],
                         edges=[{"from": "a", "to": "b"}])["uid"]
    res = server.edit_memory(uid, title="Hourly export routine")
    assert res["ok"] is False and "dashboard" in res["errors"][0]
    assert server.get_memory(uid)["title"] == "Nightly export routine"


# ------------------------------------------------------- how long it may be

AT_LIMIT = "N" + "a" * (db.TITLE_MAX - 1)
OVER_LIMIT = "N" + "a" * db.TITLE_MAX


def test_a_title_at_the_limit_is_stored(conn):
    uid = db.insert_memory(conn, type="note", title=AT_LIMIT, content="a fact")
    assert db.get_memory(conn, uid)["title"] == AT_LIMIT


def test_a_writer_is_refused_a_longer_one(conn):
    with pytest.raises(ValueError, match=str(db.TITLE_MAX)):
        db.insert_memory(conn, type="note", title=OVER_LIMIT, content="a fact")


def test_the_length_is_measured_after_stripping(conn):
    """Padding does not spend the budget: the stored name is what counts."""
    uid = db.insert_memory(conn, type="note", title=f"   {AT_LIMIT}   ", content="a fact")
    assert db.get_memory(conn, uid)["title"] == AT_LIMIT


def test_renaming_over_the_limit_leaves_the_old_name(conn):
    uid = db.insert_memory(conn, type="note", title="The export window", content="a fact")
    with pytest.raises(ValueError, match=str(db.TITLE_MAX)):
        db.set_title(conn, uid, OVER_LIMIT)
    assert db.get_memory(conn, uid)["title"] == "The export window"


def test_the_tool_refuses_a_rename_over_the_limit(store):
    uid = server.note(title="The export window", content="both ends are inclusive")["uid"]
    res = server.edit_memory(uid, title=OVER_LIMIT)
    assert res["ok"] is False and str(db.TITLE_MAX) in res["errors"][0]
    assert server.get_memory(uid)["title"] == "The export window"


def test_a_diagram_cannot_be_named_over_the_limit(store):
    res = server.diagram(title=OVER_LIMIT,
                         nodes=[{"key": "a", "label": "Start", "shape": "start"},
                                {"key": "b", "label": "Done", "shape": "end"}],
                         edges=[{"from": "a", "to": "b"}])
    assert res["ok"] is False and str(db.TITLE_MAX) in res["errors"][0]


def test_the_dashboard_refuses_a_title_over_the_limit(client):
    uid = client.post("/api/memories", json={
        "title": "The export window", "type": "note", "content": "a fact"}).json()["uid"]
    res = client.post(f"/api/memories/{uid}/meta", json={"title": OVER_LIMIT})
    assert res.status_code == 400
    assert client.get(f"/api/memories/{uid}").json()["title"] == "The export window"


def test_staging_a_retitle_over_the_limit_is_reported_not_staged(conn):
    uid = db.insert_memory(conn, type="note", title="The export window", content="a fact")
    res = db.stage_optimization(conn, "a run", [
        {"kind": "retitle", "target_uid": uid, "payload": {"title": OVER_LIMIT}},
        {"kind": "retitle", "target_uid": uid, "payload": {"title": "Export window bounds"}},
    ])
    assert res["staged"] == 1
    assert str(db.TITLE_MAX) in res["errors"][0]["error"]
