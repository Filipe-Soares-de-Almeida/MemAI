"""Which memories are actually read back.

Curation without this judges text: it can see that a memory is old,
duplicated or vague, and cannot see that the one nobody has needed since
it was written is the store's dead weight. The counter is deliberately
fed by the MCP tools alone -- a person scrolling the dashboard is not an
agent being answered.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from memai import admin, db, server


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


def _usage(uid: str) -> dict:
    with db.connect() as conn:
        return db.usage_for(conn, [uid]).get(uid) or {}


# ------------------------------------------------------------- the counting

def test_writing_a_memory_is_not_reading_it(store):
    uid = server.note(content="cache warmup runs nightly")["uid"]
    assert _usage(uid) == {}


def test_search_counts_what_it_returned(store):
    uid = server.note(content="cache warmup runs nightly")["uid"]
    server.search("cache warmup")
    assert _usage(uid)["recalls"] == 1


def test_counts_accumulate(store):
    uid = server.note(content="the queue drain is single threaded")["uid"]
    for _ in range(3):
        server.recall("queue drain")
    assert _usage(uid)["recalls"] == 3


def test_get_memory_counts(store):
    uid = server.note(content="row merge keeps the older identifier")["uid"]
    server.get_memory(uid)
    assert _usage(uid)["recalls"] == 1


def test_listing_counts(store):
    uid = server.note(content="an empty part is rejected", domain="acme/x100")["uid"]
    server.list_by_domain("acme/x100")
    server.list_recent()
    assert _usage(uid)["recalls"] == 2


def test_a_warm_up_counts_everything_it_hands_over(store):
    note = server.note(content="the export window is inclusive", domain="acme/x100")["uid"]
    hand = server.handoff(content="pick up at the retry path", domain="acme/x100")["uid"]
    cp = server.checkpoint(intent="i", established="e", pursuing="p",
                           open_questions="q", domain="acme/x100")["uid"]
    server.pulse("acme/x100")
    assert all(_usage(u)["recalls"] == 1 for u in (note, hand, cp))


def test_a_search_that_missed_counts_nothing(store):
    uid = server.note(content="cache warmup runs nightly")["uid"]
    server.search("something else entirely")
    assert _usage(uid) == {}


def test_an_unknown_uid_does_not_break_the_read(conn):
    assert db.record_recall(conn, ["no-such-uid"]) == 0


def test_the_dashboard_does_not_inflate_the_count(client):
    uid = client.post("/api/memories",
                      json={"type": "note", "content": "cache warmup runs nightly"}).json()["uid"]
    client.get("/api/memories")
    client.get(f"/api/memories/{uid}")
    client.get("/api/memories?q=cache")
    assert client.get(f"/api/memories/{uid}").json()["recalls"] == 0


# --------------------------------------------------------------- what it buys

def test_the_curation_corpus_reports_usage(store):
    used = server.note(content="cache warmup runs nightly")["uid"]
    unused = server.note(content="the digest mailout follows the audit sweep")["uid"]
    server.search("cache warmup")
    corpus = server.optimize_scan()
    by_uid = {m["uid"]: m for m in corpus["memories"]}
    assert by_uid[used]["recalls"] == 1
    assert "recalls" not in by_uid[unused]
    assert corpus["stats"]["never_recalled"] == 1


def test_never_recalled_counts_the_whole_corpus_not_the_page(store):
    for i in range(5):
        server.note(content=f"finding {i}: the export window is inclusive on both ends")
    assert server.optimize_scan(limit=2)["stats"]["never_recalled"] == 5


# ---------------------------------------------------------------- lifecycle

def test_purging_a_memory_takes_its_usage_with_it(store):
    uid = server.note(content="cache warmup runs nightly")["uid"]
    server.search("cache warmup")
    server.purge_memory(uid, f"DELETE {uid}")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_usage").fetchone()[0] == 0


# ------------------------------------------------------------- the dashboard

def test_the_list_exposes_and_orders_by_recalls(client, monkeypatch, tmp_path):
    quiet = client.post("/api/memories",
                        json={"type": "note", "content": "the digest mailout"}).json()["uid"]
    busy = client.post("/api/memories",
                       json={"type": "note", "content": "cache warmup runs nightly"}).json()["uid"]
    with db.connect() as conn:
        db.record_recall(conn, [busy])
        db.record_recall(conn, [busy])

    items = client.get("/api/memories?sort=recalls&dir=desc").json()["items"]
    assert [i["uid"] for i in items] == [busy, quiet]
    assert items[0]["recalls"] == 2 and items[1]["recalls"] == 0

    least = client.get("/api/memories?sort=recalls&dir=asc").json()["items"]
    assert [i["uid"] for i in least] == [quiet, busy]


def test_an_unknown_sort_falls_back_instead_of_reaching_sql(client):
    client.post("/api/memories", json={"type": "note", "content": "x"})
    assert client.get("/api/memories?sort=recall_count;DROP").status_code == 200
