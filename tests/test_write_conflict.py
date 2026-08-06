"""What a writer says when the store already held something like this.

The moment of writing is the only moment the answer is free to act on:
the agent still has the context that produced the text, so it can tell a
correction from a duplicate from a second unrelated fact. dedup_scan asks
the same question later, over the whole store, for a human.

Vector coverage is the interesting path, so most of these use the fake
embedder; the lexical fallback gets its own case.
"""

from __future__ import annotations

import pytest

from memai import db, server


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def vec_conn(tmp_path, fake_embedder):
    """A store whose vector table exists: _ensure_vec runs at connect time,
    so the embedder has to be in place before the connection is opened."""
    with db.connect(tmp_path / "vec.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


_FACT = "the nightly database tuning schedule runs at midnight"


# ------------------------------------------------------------------- lexical

def test_a_restatement_comes_back_on_the_write(store):
    first = server.note(content=_FACT, domain="acme/x100")["uid"]
    res = server.note(content=_FACT + " sharp", domain="acme/x100")
    assert [s["uid"] for s in res["similar"]] == [first]
    assert res["similar_hint"]


def test_the_write_still_happened(store):
    server.note(content=_FACT, domain="acme/x100")
    res = server.note(content=_FACT + " sharp", domain="acme/x100")
    assert server.get_memory(res["uid"])["content"].endswith("sharp")


def test_an_unrelated_fact_says_nothing(store):
    server.note(content=_FACT, domain="acme/x100")
    res = server.note(content="an empty part is rejected on upload", domain="acme/x100")
    assert "similar" not in res and "similar_hint" not in res


def test_the_first_memory_of_a_store_says_nothing(store):
    assert "similar" not in server.note(content=_FACT)


def test_an_archived_twin_is_not_a_collision(store):
    old = server.note(content=_FACT, domain="acme/x100")["uid"]
    server.forget(old, reason="superseded")
    assert "similar" not in server.note(content=_FACT + " sharp", domain="acme/x100")


def test_consecutive_checkpoints_are_a_timeline_not_a_copy(store):
    """They share a skeleton by design; firing on every one would train the
    field to be ignored."""
    server.checkpoint(intent="ship the retry path", established="a", pursuing="b",
                      open_questions="c", domain="acme/x100")
    res = server.checkpoint(intent="ship the retry path", established="a2", pursuing="b",
                            open_questions="c", domain="acme/x100")
    assert "similar" not in res


def test_the_lexical_fallback_stays_inside_the_scope(conn):
    """No vectors: the probe is a scan, so it must not widen with the store."""
    near = db.insert_memory(conn, type="note", content=_FACT, domain="acme/x100")
    db.insert_memory(conn, type="note", content=_FACT, domain="zeta/x200")
    uid = db.insert_memory(conn, type="note", content=_FACT + " sharp", domain="acme/x100")
    hits = db.similar_memories(conn, uid)
    assert [h["uid"] for h in hits] == [near]
    assert hits[0]["method"] == "lexical"


def test_a_diagram_is_never_a_collision(store):
    """Its content is a projection of a graph, so the resemblance names no
    merge anybody could carry out."""
    server.diagram(title="Cache warmup", domain="acme/x100", nodes=[
        {"key": "start", "shape": "start", "label": _FACT},
        {"key": "done", "shape": "end", "label": "done"}],
        edges=[{"from": "start", "to": "done"}])
    assert "similar" not in server.note(content=_FACT, domain="acme/x100")


def test_writing_a_diagram_never_probes(store):
    server.note(content=_FACT, domain="acme/x100")
    res = server.diagram(title="Nightly tuning", domain="acme/x100", nodes=[
        {"key": "start", "shape": "start", "label": _FACT},
        {"key": "done", "shape": "end", "label": "done"}],
        edges=[{"from": "start", "to": "done"}])
    assert "similar" not in res


# -------------------------------------------------------------------- vector

def test_a_paraphrase_is_caught_by_the_vector_side(vec_conn):
    first = db.insert_memory(vec_conn, type="note", content="car maintenance schedule",
                             domain="acme/x100")
    uid = db.insert_memory(vec_conn, type="note", content="automobile maintenance schedule",
                           domain="acme/x100")
    hits = db.similar_memories(vec_conn, uid)
    assert [h["uid"] for h in hits] == [first]
    assert hits[0]["method"] == "vector" and hits[0]["ratio"] >= db.SIMILAR_ON_WRITE


def test_the_vector_probe_reaches_outside_the_filed_scope(vec_conn):
    """Unlike the scan, a KNN costs the same store-wide -- and a duplicate
    filed somewhere else is exactly the one nobody would find."""
    elsewhere = db.insert_memory(vec_conn, type="note", content="car maintenance schedule",
                                 domain="zeta/x200")
    uid = db.insert_memory(vec_conn, type="note", content="automobile maintenance schedule",
                           domain="acme/x100")
    assert [h["uid"] for h in db.similar_memories(vec_conn, uid)] == [elsewhere]


def test_it_stops_at_the_cap(vec_conn):
    for _ in range(6):
        db.insert_memory(vec_conn, type="note", content="car maintenance schedule")
    uid = db.insert_memory(vec_conn, type="note", content="automobile maintenance schedule")
    assert len(db.similar_memories(vec_conn, uid)) == db.SIMILAR_ON_WRITE_MAX
