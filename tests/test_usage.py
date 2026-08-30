"""Which memories are actually read back.

Curation without this judges text alone: it can see that a memory is old,
duplicated or vague, and cannot see whether the store has ever needed it.
The counter is fed by the MCP tools alone -- a person scrolling the
dashboard is not an agent being answered.

And it stops there. A low count means UNPROVEN, not useless: a memory
about a rare subject is indistinguishable from one nobody wants, and the
rare subject is often the reason a store exists. Nothing in retrieval may
read this table; the tests at the bottom of this file are what say so.
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


# ------------------------------------------------- usage must not rank

def test_a_much_read_memory_does_not_outrank_a_better_match(conn):
    """The line this whole table lives behind.

    Boosting what gets read is the obvious next idea and it is wrong: a
    memory read twice a year is about a rarer subject, not a worse one, and
    a popularity term buries the rare thing further every time it loses.
    Some of what a store is FOR is the thing nobody remembers to look up.
    """
    popular = db.insert_memory(conn, type="note",
                               content="cache warmup notes, general")
    exact = db.insert_memory(conn, type="note",
                             content="cache warmup runs nightly at midnight sharp")
    for _ in range(500):
        db.record_recall(conn, [popular])
    order = [r["uid"] for r in db.search_ranked(conn, "nightly midnight sharp")]
    assert order[0] == exact


def test_recording_a_read_does_not_change_what_a_search_returns(conn):
    for i in range(6):
        db.insert_memory(conn, type="note", content=f"queue drain finding {i}")
    before = [r["uid"] for r in db.search_ranked(conn, "queue drain")]
    for uid in before[3:]:
        db.record_recall(conn, [uid])
        db.record_recall(conn, [uid])
    assert [r["uid"] for r in db.search_ranked(conn, "queue drain")] == before


def test_no_ranking_query_reads_the_usage_table():
    """Cheaper than trusting the two tests above to catch every future
    wiring: the SQL that orders results must not name the table at all."""
    import inspect
    for fn in (db.search_memories, db.search_ranked,
               db.list_by_domain, db.list_recent, db.domain_census):
        src = inspect.getsource(fn)
        assert "memory_usage" not in src and "recall_count" not in src, fn.__name__


# ------------------------------------------------ what a search actually found

def test_a_search_credits_the_index_that_surfaced_the_row(store):
    server.note(content="cache warmup runs nightly", tags="warmup")
    server.search("cache warmup")
    with db.connect() as conn:
        share = db.search_share(conn)
    assert share["from_search"] == 1 and share["fts"] == 1


def test_a_read_with_no_search_behind_it_credits_nobody(store):
    uid = server.note(content="row merge keeps the older id", domain="acme/x100")["uid"]
    server.get_memory(uid)
    server.pulse("acme/x100")
    with db.connect() as conn:
        share = db.search_share(conn)
    assert share["reads"] == 2 and share["from_search"] == 0


def test_the_tally_survives_a_store_that_predates_it(conn):
    """The column arrives by migration; a store written before it must
    still count reads rather than fail on the INSERT."""
    conn.execute("DROP TABLE memory_usage")
    conn.execute("CREATE TABLE memory_usage (memory_uid TEXT PRIMARY KEY "
                 "REFERENCES memories(uid), recall_count INTEGER NOT NULL DEFAULT 0, "
                 "last_recalled_at TEXT NOT NULL)")
    db._ensure_columns(conn)
    uid = db.insert_memory(conn, type="note", content="x")
    assert db.record_recall(conn, [uid], sources={uid: "fts"}) == 1
    assert db.search_share(conn)["fts"] == 1
