"""Tests for the warm-up / recall / discovery additions and the
forget-audit and KNN-starvation fixes.

Kept at the db layer, like the rest of the suite -- the MCP tools in
server.py are thin wrappers that open db.connect() on the real user
store, so exercising them directly would touch ~/.memai.
"""

import pytest

from conftest import shaped
from memai import db


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


def test_list_domains_counts_and_excludes_empty(conn):
    db.insert_memory(conn, type="note", content="a", domain="d1")
    db.insert_memory(conn, type="note", content="b", domain="d1")
    db.insert_memory(conn, type="note", content="c", domain="d2")
    db.insert_memory(conn, type="note", content="no domain here")  # domain=""
    counts = {r["domain"]: r["count"] for r in db.list_domains(conn)}
    assert counts == {"d1": 2, "d2": 1}


def test_list_domains_excludes_archived(conn):
    a = db.insert_memory(conn, type="note", content="x", domain="d1")
    db.insert_memory(conn, type="note", content="y", domain="d1")
    db.set_status(conn, a, "archived")
    rows = db.list_domains(conn)
    assert len(rows) == 1
    assert rows[0]["domain"] == "d1"
    assert rows[0]["count"] == 1


def test_set_status_with_reason_records_single_audit_edit(conn):
    uid = db.insert_memory(conn, type="note", content="keep me")
    assert db.set_status(conn, uid, "archived", note="archived: stale") is True
    hist = db.get_edit_history(conn, uid)
    assert len(hist) == 1
    # content is untouched -- the audit entry is a status change, not an edit
    assert hist[0]["prev_content"] == hist[0]["new_content"] == "keep me"
    assert "stale" in hist[0]["note"]
    row = db.get_memory(conn, uid)
    assert row["status"] == "archived"
    assert row["content"] == "keep me"


def test_set_status_without_reason_leaves_no_edit(conn):
    uid = db.insert_memory(conn, type="note", content="keep me")
    assert db.set_status(conn, uid, "archived") is True
    assert db.get_edit_history(conn, uid) == []


def test_search_hybrid_type_filter_scopes_to_notes(conn):
    # recall() is search(type='note'); the filter must exclude other types
    note = db.insert_memory(conn, type="note", content="x100 rule detail")
    db.insert_memory(conn, type="checkpoint", content=shaped("checkpoint", "x100 checkpoint state"))
    results = db.search_hybrid(conn, "x100", type="note")
    assert [r["uid"] for r in results] == [note]


def test_semantic_search_not_starved_by_other_domains(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        # 55 near-identical vectors in one domain fill the naive top-k
        # window (k = max(limit*4, 50) = 50)...
        for _ in range(55):
            db.insert_memory(conn, type="note", content="car maintenance schedule", domain="big")
        # ...and one relevant-but-farther vector sits just outside it.
        small = db.insert_memory(conn, type="note", content="car", domain="small")
        # With the domain filter, KNN must widen k to see the small domain.
        results = db.search_semantic(conn, "car maintenance schedule", domain="small", limit=5)
        assert [r["uid"] for r in results] == [small]
