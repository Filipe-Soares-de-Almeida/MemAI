"""Vector/hybrid retrieval tests, using the deterministic fake embedder
from conftest (real model never loads in tests).
"""

import pytest

from conftest import shaped
from memai import db, embed
from tests.conftest import make_fake_embed


def _vec_count(conn) -> int:
    return conn.execute("SELECT count(*) AS c FROM memories_vec").fetchone()["c"]


def test_hybrid_finds_semantic_match_without_shared_keywords(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        target = db.insert_memory(conn, type="note", content="vehicle maintenance schedule")
        db.insert_memory(conn, type="note", content="database tuning note")
        # "automobile" shares no token with "vehicle ..." -- FTS misses,
        # the (fake-synonym) embedding space catches it.
        results = db.search_hybrid(conn, "automobile")
        assert results[0]["uid"] == target
        assert results[0]["match_source"] == "vec"
        assert "vec_distance" in results[0]


def test_hybrid_annotates_both_when_fts_and_vec_agree(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        uid = db.insert_memory(conn, type="note", content="car maintenance schedule")
        results = db.search_hybrid(conn, "car")
        assert results[0]["uid"] == uid
        assert results[0]["match_source"] == "both"
        assert "fts_rank" in results[0] and "vec_distance" in results[0]


def test_hybrid_falls_back_to_fts_only_without_embedder(tmp_path):
    # autouse fixture leaves embedder unavailable -> no vec table at all
    with db.connect(tmp_path / "t.db") as conn:
        uid = db.insert_memory(conn, type="note", content="alpha fact")
        results = db.search_hybrid(conn, "alpha")
        assert results[0]["uid"] == uid
        assert results[0]["match_source"] == "fts"


def test_insert_writes_vector_in_same_transaction(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        db.insert_memory(conn, type="note", content="car note")
        assert _vec_count(conn) == 1


def test_purge_removes_vector_row(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        uid = db.insert_memory(conn, type="note", content="car note")
        assert _vec_count(conn) == 1
        assert db.purge_memory(conn, uid) is True
        assert _vec_count(conn) == 0


def test_edit_reembeds_content(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        uid = db.insert_memory(conn, type="note", content="database tuning note")
        db.update_memory_content(conn, uid, "car maintenance schedule")
        results = db.search_semantic(conn, "automobile", limit=1)
        assert results[0]["uid"] == uid


def test_backfill_after_offline_inserts(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    # embedder unavailable (autouse default): rows written without vectors
    with db.connect(path) as conn:
        db.insert_memory(conn, type="note", content="car note")
        db.insert_memory(conn, type="note", content="database note")
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'memories_vec'"
        ).fetchone() is None
    # model becomes available -> next connect backfills inside one txn
    monkeypatch.setattr(embed, "embedding_dim", lambda: 8)
    monkeypatch.setattr(embed, "embed_texts", make_fake_embed(8))
    monkeypatch.setattr(embed, "model_name", lambda: "fake-model-8d")
    with db.connect(path) as conn:
        assert _vec_count(conn) == 2


def test_model_swap_drops_and_rebuilds_vectors(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(embed, "embedding_dim", lambda: 8)
    monkeypatch.setattr(embed, "embed_texts", make_fake_embed(8))
    monkeypatch.setattr(embed, "model_name", lambda: "fake-model-8d")
    with db.connect(path) as conn:
        db.insert_memory(conn, type="note", content="car note")
        db.insert_memory(conn, type="note", content="database note")
        assert _vec_count(conn) == 2
    # different model, different dim -> stored vectors meaningless
    monkeypatch.setattr(embed, "embedding_dim", lambda: 4)
    monkeypatch.setattr(embed, "embed_texts", make_fake_embed(4))
    monkeypatch.setattr(embed, "model_name", lambda: "fake-model-4d")
    with db.connect(path) as conn:
        assert _vec_count(conn) == 2  # rebuilt, not lost
        assert db._get_meta(conn, "embed_model") == "fake-model-4d"
        assert db._get_meta(conn, "embed_dim") == "4"
        # rebuilt vectors are queryable in the new space
        results = db.search_semantic(conn, "automobile", limit=1)
        assert "note" in results[0]["content"]


def test_semantic_search_respects_status_filter(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        uid = db.insert_memory(conn, type="note", content="car maintenance")
        db.set_status(conn, uid, "archived")
        assert db.search_semantic(conn, "car") == []
        assert db.search_hybrid(conn, "car") == []


# ------------------------------------------------------------------ semantic dedup

def test_dedup_uses_vectors_when_available(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        a = db.insert_memory(conn, type="note", content="car maintenance schedule")
        b = db.insert_memory(conn, type="note", content="automobile maintenance schedule")
        db.insert_memory(conn, type="note", content="database tuning")
        pairs = db.dedup_candidates(conn, threshold=0.9)
    assert len(pairs) == 1
    pa, pb, score, method = pairs[0]
    assert method == "vector" and score > 0.9
    assert {pa["uid"], pb["uid"]} == {a, b}


def test_dedup_drops_same_effort_checkpoint_pairs(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        db.insert_memory(conn, type="checkpoint", domain="proj-1",
                         content=shaped("checkpoint", "car maintenance schedule"))
        db.insert_memory(conn, type="checkpoint", domain="proj-1",
                         content=shaped("checkpoint", "vehicle maintenance schedule"))
        db.insert_memory(conn, type="checkpoint", domain="proj-2",
                         content=shaped("checkpoint", "automobile maintenance schedule"))
        pairs = db.dedup_candidates(conn, threshold=0.5)
    # the two proj-1 checkpoints are a timeline, never a candidate pair
    for a, b, _score, _method in pairs:
        assert {a["domain"], b["domain"]} != {"proj-1"}
    # cross-domain checkpoint pairs remain legitimate candidates
    assert pairs


def test_dedup_since_probes_new_against_whole_store(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        old = db.insert_memory(conn, type="note", content="car maintenance schedule",
                               created_at="2026-01-05T10:00:00+00:00")
        db.insert_memory(conn, type="note", content="database tuning",
                         created_at="2026-01-05T10:00:00+00:00")
        new = db.insert_memory(conn, type="note", content="automobile maintenance schedule",
                               created_at="2026-03-20T10:00:00+00:00")
        pairs = db.dedup_candidates(conn, threshold=0.9, since="2026-02-01")
    assert len(pairs) == 1
    a, b, _score, method = pairs[0]
    assert method == "vector"
    assert {a["uid"], b["uid"]} == {new, old}     # new probes, old still matchable


@pytest.mark.parametrize("query, opaque", [
    ("ead8bb288c070256", True),                 # a uid, exactly what gets pasted
    ("EAD8BB288C070256", True),                 # same, uppercase
    ("  ead8bb288c070256  ", True),             # trimmed before matching
    ("deadbeefcafe", True),                     # 12 chars, the floor
    ("a1b2c3", False),                          # too short to be opaque
    ("facade", False),                          # a hex-looking word stays a word
    ("decade", False),
    ("ead8bb288c070256 e o embedder", False),   # a uid among words leaves words
    ("lancamento contabil", False),
    ("", False),
])
def test_is_opaque_query(query, opaque):
    assert db.is_opaque_query(query) is opaque


def test_opaque_query_skips_the_vector_arm(tmp_path, fake_embedder):
    """A pasted uid has no semantic neighborhood; the arm must not invent one."""
    with db.connect(tmp_path / "t.db") as conn:
        db.insert_memory(conn, type="note", content="vehicle maintenance schedule")
        db.insert_memory(conn, type="note", content="database tuning note")
        assert db.search_semantic(conn, "ead8bb288c070256", max_distance=2.0) == []
        # the same store still answers a real question through that arm
        assert db.search_semantic(conn, "automobile", max_distance=2.0)


def test_opaque_query_leaves_the_keyword_arm_alone(tmp_path, fake_embedder):
    """Only the vector arm is skipped: a uid written in a body is findable."""
    with db.connect(tmp_path / "t.db") as conn:
        uid = db.insert_memory(
            conn, type="note", content="supersedes [[ead8bb288c070256]] on the cache warmup")
        hits = db.search_hybrid(conn, "ead8bb288c070256")
        assert [h["uid"] for h in hits] == [uid]
        assert hits[0]["match_source"] == "fts"


def test_pasted_uid_pins_the_row_it_names(tmp_path, fake_embedder):
    """The uid names one row; the rows that LINK to it come after, not instead."""
    with db.connect(tmp_path / "t.db") as conn:
        named = db.insert_memory(conn, type="note", content="the cache warmup runs at start")
        referrer = db.insert_memory(
            conn, type="note", content=f"supersedes [[{named}]] on the cache warmup")
        hits = db.search_hybrid(conn, named)
        assert hits[0]["uid"] == named
        assert hits[0]["match_source"] == "uid"
        assert referrer in [h["uid"] for h in hits[1:]]


def test_unknown_uid_returns_nothing_rather_than_noise(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        db.insert_memory(conn, type="note", content="vehicle maintenance schedule")
        db.insert_memory(conn, type="note", content="database tuning note")
        assert db.search_hybrid(conn, "ead8bb288c070256") == []


def test_pinned_uid_respects_the_status_filter(tmp_path, fake_embedder):
    with db.connect(tmp_path / "t.db") as conn:
        uid = db.insert_memory(conn, type="note", content="the cache warmup runs at start")
        db.set_status(conn, uid, "archived")
        assert db.search_hybrid(conn, uid, status="active") == []
        assert [h["uid"] for h in db.search_hybrid(conn, uid, status="archived")] == [uid]
