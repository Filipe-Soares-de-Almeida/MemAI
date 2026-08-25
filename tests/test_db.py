import sqlite3

import pytest

from conftest import shaped
from memai import db


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


def test_insert_and_get(conn):
    uid = db.insert_memory(conn, type="note", content="hello world", domain="d1", tags="greeting")
    row = db.get_memory(conn, uid)
    assert row["content"] == "hello world"
    assert row["status"] == "active"
    assert row["confidence"] == "unverified"


def test_search_bm25(conn):
    db.insert_memory(conn, type="note", content="best of n dpo critic reranking dismissed", domain="d1")
    db.insert_memory(conn, type="note", content="unrelated audio pipeline note", domain="d1")
    results = db.search_memories(conn, "reranking critic")
    assert len(results) == 1
    assert "critic" in results[0]["content"]


def test_search_multi_term_or_widens_matches(conn):
    db.insert_memory(conn, type="note", content="axis b hypothesis dismissed", domain="d1")
    results = db.search_memories(conn, "reranking axis")
    assert len(results) == 1


def test_edit_history_preserves_previous_content(conn):
    uid = db.insert_memory(conn, type="note", content="v1 content")
    db.update_memory_content(conn, uid, "v2 content", note="fixed typo")
    row = db.get_memory(conn, uid)
    assert row["content"] == "v2 content"
    history = db.get_edit_history(conn, uid)
    assert len(history) == 1
    assert history[0]["prev_content"] == "v1 content"
    assert history[0]["new_content"] == "v2 content"


def test_relations_graph(conn):
    a = db.insert_memory(conn, type="note", content="decision A")
    b = db.insert_memory(conn, type="note", content="decision B supersedes A")
    db.add_relation(conn, b, a, "supersedes")
    rels = db.get_relations(conn, a)
    assert len(rels) == 1
    assert rels[0]["relation_type"] == "supersedes"


def test_forget_is_soft_delete(conn):
    uid = db.insert_memory(conn, type="note", content="stale fact")
    db.set_status(conn, uid, "archived")
    row = db.get_memory(conn, uid)
    assert row["status"] == "archived"
    # excluded from default active-only search/list
    assert db.search_memories(conn, "stale") == []
    assert db.list_recent(conn) == []


def test_pulse_picks_latest_checkpoint_by_recency_not_similarity(conn):
    old_uid = db.insert_memory(conn, type="checkpoint",
                               content=shaped("checkpoint", "old checkpoint about widgets"),
                               domain="dom")
    new_uid = db.insert_memory(conn, type="checkpoint",
                               content=shaped("checkpoint", "totally different topic"),
                               domain="dom")
    # force distinguishable created_at ordering
    conn.execute("UPDATE memories SET created_at = '2020-01-01' WHERE uid = ?", (old_uid,))
    conn.execute("UPDATE memories SET created_at = '2030-01-01' WHERE uid = ?", (new_uid,))
    latest = db.latest_by_type(conn, "checkpoint", domain="dom")
    assert latest["uid"] == new_uid


def test_purge_memory_removes_row_edits_relations_and_fts(conn):
    a = db.insert_memory(conn, type="note", content="alpha content")
    b = db.insert_memory(conn, type="note", content="beta content")
    db.update_memory_content(conn, a, "alpha content v2", note="edit")
    db.add_relation(conn, a, b, "relates_to")

    assert db.purge_memory(conn, a) is True
    assert db.get_memory(conn, a) is None
    assert db.get_edit_history(conn, a) == []
    assert db.get_relations(conn, a) == []
    # fts row for a must be gone too -- searching its old content finds nothing
    assert db.search_memories(conn, "alpha", status="") == []
    # unrelated row untouched
    assert db.get_memory(conn, b) is not None


def test_purge_memory_missing_uid_returns_false(conn):
    assert db.purge_memory(conn, "does-not-exist") is False


def test_dedup_candidates_finds_near_duplicates(conn):
    db.insert_memory(conn, type="note", content="the sky is blue today and sunny")
    db.insert_memory(conn, type="note", content="the sky is blue today and cloudy")
    db.insert_memory(conn, type="note", content="completely unrelated fact about databases")
    pairs = db.dedup_candidates(conn, threshold=0.85)
    assert len(pairs) == 1


def test_tag_filter_matches_whole_tags_only(conn):
    """`tags` is one comma string, so the filter has to respect boundaries."""
    hit = db.insert_memory(conn, type="note", content="alpha rollout note",
                           domain="d1", tags="rollout,flag")
    db.insert_memory(conn, type="note", content="beta rollout note",
                     domain="d1", tags="rollout-plan,flagged")
    db.insert_memory(conn, type="note", content="gamma rollout note",
                     domain="d1", tags="batch")

    rows = db.search_memories(conn, "rollout", tag="rollout")
    assert [r["uid"] for r in rows] == [hit]

    # a tag in the middle of the list, and one written with spaces around it
    spaced = db.insert_memory(conn, type="note", content="delta rollout note",
                              domain="d1", tags="batch, flag , loader")
    rows = db.search_memories(conn, "rollout", tag="flag")
    assert set(r["uid"] for r in rows) == {hit, spaced}


def test_tag_filter_escapes_like_wildcards(conn):
    """'anti_pattern' is a valid tag and '_' is a LIKE wildcard."""
    exact = db.insert_memory(conn, type="note", content="one", tags="anti_pattern")
    db.insert_memory(conn, type="note", content="two", tags="anti-pattern")
    rows = db.list_recent(conn, tag="anti_pattern")
    assert [r["uid"] for r in rows] == [exact]

    pct = db.insert_memory(conn, type="note", content="three", tags="100%done")
    rows = db.list_recent(conn, tag="100%done")
    assert [r["uid"] for r in rows] == [pct]


def test_tag_filter_default_is_inert(conn):
    """Every existing caller passes no tag; the results must not move."""
    db.insert_memory(conn, type="note", content="alpha note", tags="one")
    db.insert_memory(conn, type="note", content="beta note", tags="two")
    assert len(db.list_recent(conn)) == 2
    assert len(db.list_recent(conn, tag="")) == 2
    assert len(db.search_memories(conn, "note")) == 2
    assert len(db.search_memories(conn, "note", tag="")) == 2


def test_list_recent_tag_filter(conn):
    a = db.insert_memory(conn, type="note", content="one", tags="loader,schema")
    db.insert_memory(conn, type="note", content="two", tags="loader")
    rows = db.list_recent(conn, tag="schema")
    assert [r["uid"] for r in rows] == [a]
