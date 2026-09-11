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


# Two paragraphs on unrelated engineering subjects, same language, similar
# length: the shape a character-multiset measure cannot tell from a copy.
_UNRELATED_A = (
    "The queue drain worker retries a failed batch three times before it moves "
    "the batch to the dead letter table. Each retry waits twice as long as the "
    "one before, starting at two seconds, and the third failure writes the "
    "batch id and the last error into the audit row."
)
_UNRELATED_B = (
    "The report export runs on a schedule and rebuilds the search index while "
    "it holds no lock, so a reader never waits for it. When a column is added "
    "the whole index is dropped and written again, because the engine has no "
    "statement that alters one in place."
)


def test_dedup_candidates_ignore_unrelated_prose(conn):
    """Same language and length is not similarity -- only a shared sequence is."""
    db.insert_memory(conn, type="note", content=_UNRELATED_A)
    db.insert_memory(conn, type="note", content=_UNRELATED_B)
    assert db.dedup_candidates(conn, threshold=0.6) == []


def test_dedup_candidates_score_is_the_word_sequence_overlap(conn):
    """The reported ratio is what the two texts actually share, not a bound."""
    db.insert_memory(conn, type="note", content=_UNRELATED_A)
    db.insert_memory(conn, type="note", content=_UNRELATED_A + " A fourth attempt never happens.")
    pairs = db.dedup_candidates(conn, threshold=0.6)
    assert len(pairs) == 1
    assert pairs[0][2] > 0.9
    assert pairs[0][2] == pytest.approx(db.text_ratio(pairs[0][0]["content"], pairs[0][1]["content"]))


def test_ratio_bound_never_undercuts_the_ratio_it_gates():
    """_pair_ratio drops no pair that would have reached the threshold.

    The gate is only sound if the bound it applies cannot fall below the
    ratio. Evenly spread edits are the hard shape: replacing every Nth
    word leaves no window of N consecutive tokens intact while (N-1)/N of
    the sequence still matches.
    """
    words = [f"palavra{i}" for i in range(300)]
    base = " ".join(words)
    for step in range(2, 13):
        other = " ".join(w if i % step else "xxx" for i, w in enumerate(words))
        a, b = db._Prepared(base), db._Prepared(other)
        ratio = db.text_ratio(base, other)
        assert db._ratio_bound(a, b) >= ratio - 1e-12, step
        assert db._pair_ratio(a, b, 0.6) == (ratio if ratio >= 0.6 else 0.0), step


def test_similar_memories_stay_quiet_on_unrelated_neighbours(conn):
    """A write in a busy domain is only warned about a real collision."""
    for i in range(6):
        db.insert_memory(conn, type="note", domain="acme/x100",
                         content=f"{_UNRELATED_B} Run {i} finished.")
    uid = db.insert_memory(conn, type="note", domain="acme/x100", content=_UNRELATED_A)
    assert db.similar_memories(conn, uid) == []
    twin = db.insert_memory(conn, type="note", domain="acme/x100", content=_UNRELATED_A)
    assert [h["uid"] for h in db.similar_memories(conn, twin)] == [uid]


def test_search_collapse_keeps_unrelated_results(conn):
    """Collapsing hides a copy of one fact, never a second fact."""
    db.insert_memory(conn, type="note", domain="acme", content=_UNRELATED_A)
    db.insert_memory(conn, type="note", domain="acme", content=_UNRELATED_B)
    results = db.search_ranked(conn, "batch index table", limit=10, collapse=True)
    assert len(results) == 2
    assert all(not r.get("collapsed") for r in results)


def test_bound_of_one_is_not_a_score_of_one(conn):
    """A pair the bound cannot rule out is still scored, not accepted.

    Two texts holding the same words in a different order: every measure
    over an unordered bag of characters or tokens reads them as identical,
    so the bound is 1.0 and only the sequence separates them.
    """
    shuffled = " ".join(sorted(_UNRELATED_B.split()))
    assert db._ratio_bound(db._Prepared(_UNRELATED_B), db._Prepared(shuffled)) == 1.0
    assert db.text_ratio(_UNRELATED_B, shuffled) < 0.5

    db.insert_memory(conn, type="note", domain="acme", content=_UNRELATED_B)
    db.insert_memory(conn, type="note", domain="acme", content=shuffled)
    assert db.dedup_candidates(conn, threshold=0.6) == []
    results = db.search_ranked(conn, "index lock reader", limit=10, collapse=True)
    assert all(not r.get("collapsed") for r in results)
