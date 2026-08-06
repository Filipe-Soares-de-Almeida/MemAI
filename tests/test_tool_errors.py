"""What a tool says when the call cannot be honoured.

An MCP tool's error is read by an agent, not by a person, so the shape is
part of the contract: {"ok": False, "errors": [...]} for a refusal, and
never a value that reads as a successful empty answer. Tools are exercised
against a store under tmp_path (MEMAI_HOME), never the real ~/.memai.
"""

from __future__ import annotations

import pytest

from memai import db, server


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------ unknown record

def test_get_memory_on_an_unknown_uid_is_an_error(store):
    """Not {}: an empty dict is indistinguishable from an empty record."""
    res = server.get_memory("no-such-uid")
    assert res["ok"] is False
    assert "no-such-uid" in res["errors"][0]


def test_get_memory_still_returns_the_record_it_has(store):
    uid = server.note(content="a fact worth keeping", domain="acme/x100")["uid"]
    assert server.get_memory(uid)["content"] == "a fact worth keeping"


# ------------------------------------------------------------------ relations

def test_relation_to_an_unknown_uid_is_refused(conn):
    uid = db.insert_memory(conn, type="note", content="a")
    with pytest.raises(ValueError, match="unknown memory"):
        db.add_relation(conn, uid, "no-such-uid", "relates_to")


def test_relation_to_itself_is_refused(conn):
    uid = db.insert_memory(conn, type="note", content="a")
    with pytest.raises(ValueError, match="cannot relate to itself"):
        db.add_relation(conn, uid, uid, "relates_to")


def test_an_identical_relation_is_refused(conn):
    a = db.insert_memory(conn, type="note", content="a")
    b = db.insert_memory(conn, type="note", content="b")
    db.add_relation(conn, a, b, "supersedes")
    with pytest.raises(ValueError, match="already exists"):
        db.add_relation(conn, a, b, "supersedes")


def test_a_second_relation_of_another_type_is_fine(conn):
    a = db.insert_memory(conn, type="note", content="a")
    b = db.insert_memory(conn, type="note", content="b")
    db.add_relation(conn, a, b, "supersedes")
    assert db.add_relation(conn, a, b, "relates_to")


def test_link_memories_reports_a_bad_uid_instead_of_raising(store):
    """Before, the foreign key turned a typo into a raw IntegrityError."""
    uid = server.note(content="a")["uid"]
    res = server.link_memories(uid, "no-such-uid", "relates_to")
    assert res["ok"] is False
    assert "unknown memory" in res["errors"][0]


def test_link_memories_still_links(store):
    a = server.note(content="a")["uid"]
    b = server.note(content="b")["uid"]
    assert server.link_memories(a, b, "relates_to")["relation_id"]


# ------------------------------------------------------------ editing content

def test_append_adds_a_line_without_restating_the_body(store):
    uid = server.note(content="cache warmup runs nightly")["uid"]
    assert server.edit_memory(uid, "it is skipped on holidays", mode="append")["ok"]
    assert server.get_memory(uid)["content"] == (
        "cache warmup runs nightly\nit is skipped on holidays")


def test_replace_is_still_the_default(store):
    uid = server.note(content="cache warmup runs nightly")["uid"]
    server.edit_memory(uid, "cache warmup runs hourly")
    assert server.get_memory(uid)["content"] == "cache warmup runs hourly"


def test_an_append_keeps_the_previous_version(store):
    uid = server.note(content="cache warmup runs nightly")["uid"]
    server.edit_memory(uid, "and skips holidays", mode="append", note="learned today")
    history = server.get_memory(uid)["edit_history"]
    assert history[-1]["prev_content"] == "cache warmup runs nightly"
    assert history[-1]["note"] == "learned today"


def test_appending_to_an_empty_body_does_not_lead_with_a_blank_line(conn):
    uid = db.insert_memory(conn, type="note", content="")
    db.update_memory_content(conn, uid, "the first thing known", append=True)
    assert db.get_memory(conn, uid)["content"] == "the first thing known"


def test_an_unknown_mode_is_refused(store):
    uid = server.note(content="x")["uid"]
    assert server.edit_memory(uid, "y", mode="prepend")["ok"] is False


def test_editing_a_memory_that_is_not_there_says_so(store):
    res = server.edit_memory("no-such-uid", "y")
    assert res["ok"] is False and "no-such-uid" in res["errors"][0]


def test_a_diagram_still_refuses_both_modes(store):
    uid = server.diagram(title="Cache warmup", nodes=[
        {"key": "start", "shape": "start", "label": "begin"},
        {"key": "done", "shape": "end", "label": "done"}],
        edges=[{"from": "start", "to": "done"}])["uid"]
    for mode in ("replace", "append"):
        assert server.edit_memory(uid, "hand-written", mode=mode)["ok"] is False
